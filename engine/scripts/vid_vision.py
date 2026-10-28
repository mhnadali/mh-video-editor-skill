"""Optional local vision: people, faces and scene labels for the selection, from an OFFLINE plug-in.

The engine itself stays standard-library only and never reaches the network. Recognising people needs an image library, so
that part lives in `plugins/vision/` and runs in its OWN Python environment (a virtual environment next to it, created by
`plugins/vision/setup.py`). The engine never imports the plug-in: it starts it as a separate process with the work folder,
the plug-in writes `vision.json`, and the engine reads that file. Nothing is downloaded at run time; the face and body
detectors are the classic OpenCV cascades that ship inside the `opencv-python-headless` package, and a scene classifier is
used only if the user supplies a model file of their own.

`vision.json` (per clip index): faces (most faces seen in one sampled frame), face_area (largest face as a share of the
frame), bodies (people seen from the waist up or full length), frames (how many frames were looked at), people (true if a
person is clearly in it), scene_label (if a classifier was supplied).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import vidlib as vl

PLUGIN_DIR = Path(__file__).resolve().parent.parent.parent / "plugins" / "vision"
VISION_FILE = "vision.json"
MIN_FACE_AREA = 0.004          # a face this small (share of the frame) is a passer-by, not a subject
SURE_FACE_FRAMES = 3           # classic cascades: a face in this many sampled frames. Measured on 60 labelled clips of a real trip,
                               # a face in any frame is right 40 % of the time, in 3 or more frames 60 % (clouds, mirrors and food
                               # counters fool the cascades); the notes of the agent are the only reliable source
# YuNet (the small face-detection network the plug-in uses when its model file is present), same 60 clips: a face in any frame is
# right 83 % of the time and finds 71 % of the people; a face in 2+ frames that is not tiny is right 91 %+
YUNET_SURE_FRAMES = 2
YUNET_SURE_AREA = 0.005
CERTAINTY = {"cascades": (0.25, 0.6), "yunet": (0.6, 0.9)}       # (a sighting, a sure sighting): how much of a person it counts for


def plugin_python(plugin_dir: Optional[Path] = None) -> Optional[Path]:
    """The Python of the plug-in's own environment (MH_VISION_PYTHON overrides it), or None if it is not set up."""
    env = os.environ.get("MH_VISION_PYTHON")
    if env:
        return Path(env) if Path(env).is_file() else None
    base = Path(plugin_dir) if plugin_dir else PLUGIN_DIR
    for rel in (".venv/Scripts/python.exe", ".venv/bin/python"):
        p = base / rel
        if p.is_file():
            return p
    return None


def available(plugin_dir: Optional[Path] = None) -> bool:
    return plugin_python(plugin_dir) is not None


def command(work: str | os.PathLike, only: Optional[str] = None, onnx: Optional[str] = None, labels: Optional[str] = None,
            frames: int = 9, plugin_dir: Optional[Path] = None, force: bool = False) -> list:
    """The command line that runs the plug-in (separated from `run` so it can be checked without starting anything)."""
    py = plugin_python(plugin_dir)
    if py is None:
        raise vl.EngineError("the vision plug-in is not set up: run `python plugins/vision/setup.py` (it creates its own virtual "
                             "environment and installs opencv-python-headless there), or set MH_VISION_PYTHON")
    base = Path(plugin_dir) if plugin_dir else PLUGIN_DIR
    cmd = [str(py), str(base / "vision_run.py"), "--work", str(work), "--ffmpeg", vl.find_ffmpeg()["ffmpeg"], "--frames", str(frames)]
    if force:
        cmd += ["--force"]
    if only:
        cmd += ["--only", only]
    if onnx:
        cmd += ["--onnx", str(onnx)]
        if labels:
            cmd += ["--labels", str(labels)]
    return cmd


def run(work: str | os.PathLike, only: Optional[str] = None, onnx: Optional[str] = None, labels: Optional[str] = None,
        frames: int = 9, plugin_dir: Optional[Path] = None, log=print, force: bool = False) -> dict:
    """Run the plug-in over the inventory of `work` and return what it found."""
    cmd = command(work, only, onnx, labels, frames, plugin_dir, force)
    log("running the local vision plug-in (offline) ...")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.stdout.strip():
        log(r.stdout.strip())
    if r.returncode != 0:
        raise vl.EngineError(f"the vision plug-in failed (exit {r.returncode}): {(r.stderr or r.stdout or '')[-400:]}")
    return load(work)


def load(work: str | os.PathLike) -> dict:
    """{index: {...}} from vision.json, or {} if the plug-in was never run."""
    p = Path(work) / VISION_FILE
    if not p.is_file():
        return {}
    data = vl.load_json(p)
    return {int(k): v for k, v in (data.get("clips") or {}).items()}


def merge_into_scores(scores: dict, vision: dict) -> int:
    """Copy what the plug-in saw onto the score entries (faces, face_area, bodies, people, scene_label). Returns how many
    clips were enriched. Scores of clips the plug-in did not see are left alone, and so is a clip whose index now belongs to
    a different file (the inventory was made again after clips were added or removed)."""
    n = 0
    for i, v in vision.items():
        s = scores.get(i)
        if s is None:
            continue
        if v.get("path") is None or os.path.normcase(os.path.abspath(v["path"])) != os.path.normcase(os.path.abspath(s.get("path") or "")):
            continue
        s["faces"] = v.get("faces")
        s["face_area"] = v.get("face_area")
        s["bodies"] = v.get("bodies")
        detector = v.get("detector") or "cascades"
        if v.get("face_frames") is not None:                                     # the raw count: apply the current rule
            s["person"] = bool(v["face_frames"] >= 1 or (v.get("bodies") or 0) > 0)
            if detector == "yunet":
                s["person_sure"] = bool(v["face_frames"] >= YUNET_SURE_FRAMES and (v.get("face_area") or 0.0) >= YUNET_SURE_AREA)
            else:
                s["person_sure"] = bool(v["face_frames"] >= SURE_FACE_FRAMES)
        else:
            s["person"] = bool(v.get("people"))
            s["person_sure"] = bool(v.get("people_sure"))
        weak, sure = CERTAINTY.get(detector, CERTAINTY["cascades"])
        s["person_certainty"] = (sure if s["person_sure"] else weak) if s["person"] else 0.0
        s["person_detector"] = detector
        if v.get("scene_label"):
            s["scene_label"] = v["scene_label"]
        n += 1
    return n


def is_person_clip(score: dict) -> Optional[bool]:
    """True/False if the vision plug-in judged the clip, None if it never saw it."""
    if "person" not in score:
        return None
    return bool(score["person"])


def summarize(vision: dict) -> dict:
    n = len(vision)
    people = sum(1 for v in vision.values() if v.get("people"))
    faces = sum(1 for v in vision.values() if (v.get("faces") or 0) > 0)
    sure = sum(1 for v in vision.values() if v.get("people_sure"))
    return {"clips": n, "with_people": people, "sure": sure, "with_faces": faces}
