#!/usr/bin/env python3
"""Local vision plug-in for the M.H. Video Editor Skill. Runs inside its own virtual environment, offline.

It looks at a few frames of every clip with the classic OpenCV Haar cascades (they ship inside opencv-python-headless; no
model is downloaded) and writes `vision.json` next to the inventory:

    {"engine": "opencv-haar", "version": "...", "clips": {"12": {"faces": 2, "face_area": 0.031, "bodies": 1, "frames": 5,
                                                            "people": true, "scene_label": "..."}}}

An optional ONNX image classifier (`--onnx model.onnx --labels labels.txt`, a file the USER supplies) adds `scene_label`.
Nothing here touches the network. Cascades are crude: they find frontal and side faces and upper bodies in daylight well,
and miss people who are small, turned away or in strong shadow; the agent's own reading of the footage always wins.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

MODEL_DIR = Path(__file__).resolve().parent / "models"
YUNET = MODEL_DIR / "face_detection_yunet_2023mar.onnx"      # fetched once by `setup.py --model`; used offline afterwards
YUNET_SCORE = 0.7
MIN_FACE_AREA = 0.004        # share of the frame: smaller faces are passers-by
STRONG_FACE_AREA = 0.025     # a face this large in one frame is believed without a second sighting
FRAME_WIDTH = 640


def load_cascades() -> dict:
    base = cv2.data.haarcascades
    names = {"face": "haarcascade_frontalface_default.xml", "profile": "haarcascade_profileface.xml",
             "upper": "haarcascade_upperbody.xml", "full": "haarcascade_fullbody.xml"}
    out = {}
    for key, fn in names.items():
        c = cv2.CascadeClassifier(os.path.join(base, fn))
        if not c.empty():
            out[key] = c
    return out


def sample_times(rec: dict, score: dict | None, n: int) -> list:
    """Where to look: the starts of the clip's measured windows when `score` has them, else spread across the clip."""
    dur = float(rec.get("duration") or 0.0)
    if dur <= 0:
        return []
    times = []
    for w in (score or {}).get("wins") or []:
        times.append(min(max(0.0, w[0] + 0.5), max(0.0, dur - 0.2)))
    for f in (0.15, 0.35, 0.5, 0.65, 0.85):
        times.append(f * dur)
    out, seen = [], []
    for t in sorted(times):
        if all(abs(t - s) > 0.6 for s in seen):
            seen.append(t)
            out.append(round(t, 2))
    step = max(1, len(out) // max(n, 1))
    return out[::step][:n] if len(out) > n else out


def grab(ffmpeg: str, path: str, t: float, dest: str) -> bool:
    """One small frame at `t`. Key frames only first (fast on long-GOP 4K); if the clip has none near `t`, decode normally."""
    for skip in (["-skip_frame", "nokey"], []):
        r = subprocess.run([ffmpeg, "-v", "error", "-y", *skip, "-ss", f"{max(0.0, t):.2f}", "-i", path, "-frames:v", "1",
                            "-vf", f"scale={FRAME_WIDTH}:-2", "-q:v", "3", dest], capture_output=True)
        if r.returncode == 0 and os.path.isfile(dest) and os.path.getsize(dest) > 200:
            return True
    return False


def detect(img, cascades: dict) -> dict:
    gray = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    h, w = gray.shape[:2]
    min_face = max(24, int(0.045 * w))
    faces = []
    for key in ("face", "profile"):
        c = cascades.get(key)
        if c is None:
            continue
        found = c.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(min_face, min_face))
        faces += [tuple(map(int, f)) for f in found]
    faces = _merge(faces)
    bodies = []
    for key in ("upper", "full"):
        c = cascades.get(key)
        if c is None:
            continue
        found = c.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(int(0.15 * w), int(0.15 * w)))
        bodies += [tuple(map(int, f)) for f in found]
    bodies = _merge(bodies)
    area = max((f[2] * f[3] for f in faces), default=0) / float(w * h)
    return {"faces": len(faces), "face_area": round(area, 4), "bodies": len(bodies)}


def _merge(boxes: list) -> list:
    """Drop boxes that overlap a larger one by more than half (the same face found by two cascades)."""
    out = []
    for b in sorted(boxes, key=lambda x: -x[2] * x[3]):
        ok = True
        for o in out:
            ix = max(0, min(b[0] + b[2], o[0] + o[2]) - max(b[0], o[0]))
            iy = max(0, min(b[1] + b[3], o[1] + o[3]) - max(b[1], o[1]))
            if ix * iy > 0.5 * b[2] * b[3]:
                ok = False
                break
        if ok:
            out.append(b)
    return out


def aggregate(per_frame: list) -> dict:
    """Combine the frames of one clip: the most faces in a frame, the largest face, bodies seen in at least two frames."""
    if not per_frame:
        return {"faces": 0, "face_area": 0.0, "face_frames": 0, "bodies": 0, "frames": 0, "people": False, "people_sure": False}
    faces = max(f["faces"] for f in per_frame)
    area = max(f["face_area"] for f in per_frame)
    face_frames = sum(1 for f in per_frame if f["faces"] > 0 and f["face_area"] >= MIN_FACE_AREA)
    body_frames = sum(1 for f in per_frame if f["bodies"] > 0)
    bodies = max(f["bodies"] for f in per_frame) if body_frames >= 2 else 0
    # measured against hand-labelled clips (9 frames each): a face in any frame catches 92 % of the clips with people and
    # wrongly flags 2 of 10 landscapes; asking for two frames raises precision to 95 % but loses a quarter of the people.
    # The agent's own notes override this anyway, so recall wins; `people_sure` marks the safer reading.
    people = bool(face_frames >= 1 or bodies > 0)
    sure = bool(face_frames >= 2 or (face_frames >= 1 and area >= STRONG_FACE_AREA) or bodies > 0)
    return {"faces": faces, "face_area": area, "face_frames": face_frames, "bodies": bodies, "frames": len(per_frame),
            "people": people, "people_sure": sure}


def classify(net, labels: list, img) -> tuple:
    blob = cv2.dnn.blobFromImage(cv2.resize(img, (224, 224)), 1.0 / 255.0, (224, 224), (0.485 * 255, 0.456 * 255, 0.406 * 255), swapRB=True)
    net.setInput(blob)
    out = net.forward().flatten()
    k = int(np.argmax(out))
    e = np.exp(out - out.max())
    p = float(e[k] / e.sum())
    return (labels[k] if k < len(labels) else str(k)), p


def majority_label(votes: list) -> str | None:
    """The label most frames agree on (needs at least half of the frames), or None."""
    if not votes:
        return None
    counts: dict = {}
    for v in votes:
        counts[v] = counts.get(v, 0) + 1
    label, n = max(counts.items(), key=lambda kv: kv[1])
    return label if n * 2 >= len(votes) else None


_LOCAL = threading.local()


def thread_yunet(model: str | None, score: float = YUNET_SCORE):
    """One YuNet face detector per thread (they keep an input size), or None when no model file is available."""
    if not model or not os.path.isfile(model) or not hasattr(cv2, "FaceDetectorYN"):
        return None
    d = getattr(_LOCAL, "yunet", None)
    if d is None:
        d = _LOCAL.yunet = cv2.FaceDetectorYN.create(model, "", (320, 320), score, 0.3, 50)
    return d


def detect_yunet(img, det) -> dict:
    h, w = img.shape[:2]
    det.setInputSize((w, h))
    _ok, found = det.detect(img)
    faces = [] if found is None else [tuple(int(v) for v in f[:4]) for f in found]
    area = max((f[2] * f[3] for f in faces), default=0) / float(w * h)
    return {"faces": len(faces), "face_area": round(area, 4), "bodies": 0}


def thread_cascades() -> dict:
    """OpenCV cascade classifiers are not safe to share between threads (results change from run to run), so each worker
    thread loads its own."""
    c = getattr(_LOCAL, "cascades", None)
    if c is None:
        c = _LOCAL.cascades = load_cascades()
    return c


def analyse_clip(rec: dict, score: dict | None, args, cascades: dict, net, labels: list) -> dict:
    per_frame, votes = [], []
    yunet = thread_yunet(getattr(args, "face_model", None), getattr(args, "face_score", YUNET_SCORE))
    if yunet is None:
        cascades = thread_cascades()
    with tempfile.TemporaryDirectory(prefix="mhvid_vision_") as d:
        for k, t in enumerate(sample_times(rec, score, args.frames)):
            dest = os.path.join(d, f"{k}.jpg")
            if not grab(args.ffmpeg, rec["path"], t, dest):
                continue
            img = cv2.imread(dest)
            if img is None:
                continue
            per_frame.append(detect_yunet(img, yunet) if yunet is not None else detect(img, cascades))
            if net is not None:
                label, p = classify(net, labels, img)
                if p >= 0.3:
                    votes.append(label)
    res = aggregate(per_frame)
    res["detector"] = "yunet" if yunet is not None else "cascades"
    lab = majority_label(votes)
    if lab:
        res["scene_label"] = lab
    return res


def parse_only(spec: str | None) -> set | None:
    if not spec:
        return None
    out = set()
    for part in spec.replace(" ", "").split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", required=True)
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--frames", type=int, default=9)
    ap.add_argument("--only")
    ap.add_argument("--min-duration", type=float, default=5.0)
    ap.add_argument("--onnx")
    ap.add_argument("--labels")
    ap.add_argument("--workers", type=int, default=max(2, (os.cpu_count() or 4) // 2))
    ap.add_argument("--face-model", default=str(YUNET) if YUNET.is_file() else None,
                    help="YuNet face detector (ONNX). Default: the one `setup.py --model` fetched, else the classic cascades")
    ap.add_argument("--face-score", type=float, default=YUNET_SCORE, help="minimum YuNet confidence (0-1)")
    ap.add_argument("--force", action="store_true", help="look at every clip again, even if its earlier result is still valid")
    args = ap.parse_args(argv)
    work = Path(args.work)
    inv = json.loads((work / "inventory.json").read_text(encoding="utf-8"))["clips"]
    scores = {}
    if (work / "scores.json").is_file():
        scores = {s["index"]: s for s in json.loads((work / "scores.json").read_text(encoding="utf-8"))["scores"]}
    only = parse_only(args.only)
    todo = [r for r in inv if r.get("dup_of") is None and not r.get("error")
            and (r.get("duration") or 0) >= args.min_duration - 0.1 and (only is None or r["index"] in only)]
    cascades = load_cascades() if not args.face_model else {}
    if not cascades and not args.face_model:
        print("no OpenCV cascades found in this environment", file=sys.stderr)
        return 3
    net, labels = None, []
    if args.onnx:
        net = cv2.dnn.readNetFromONNX(args.onnx)
        if args.labels:
            labels = Path(args.labels).read_text(encoding="utf-8").splitlines()
    out_path = work / "vision.json"
    prev = {}
    if out_path.is_file():
        try:
            prev = json.loads(out_path.read_text(encoding="utf-8")).get("clips", {})
        except ValueError:
            prev = {}
    # indexes change when the inventory is made again (clips added or removed): an earlier result only counts while the same
    # file still has that index, and is reused only if the file is unchanged and the same number of frames was looked at
    by_index = {str(r["index"]): r for r in inv}
    clips = {}
    for k, v in prev.items():
        r = by_index.get(k)
        if r is not None and v.get("path") == r["path"]:
            clips[k] = v
    def fresh(r):
        old = clips.get(str(r["index"]))
        return (old is not None and not args.force and old.get("size") == r.get("size") and old.get("mtime") == r.get("mtime")
                and old.get("asked") == args.frames and old.get("engine") == cv2.__version__ and bool(args.onnx) == old.get("onnx", False)
                and old.get("detector") == ("yunet" if args.face_model else "cascades"))
    work_list = [r for r in todo if not fresh(r)]
    began = time.monotonic()
    print(f"vision: {len(work_list)} clip(s) to look at, {len(todo) - len(work_list)} unchanged", flush=True)
    with ThreadPoolExecutor(args.workers) as ex:
        for k, (rec, res) in enumerate(zip(work_list, ex.map(lambda r: analyse_clip(r, scores.get(r["index"]), args, cascades, net, labels), work_list)), 1):
            if k % 25 == 0 or k == len(work_list):
                rest = (time.monotonic() - began) / k * (len(work_list) - k)
                print(f"vision: {k}/{len(work_list)}" + (f", about {rest / 60:.0f} min left" if rest >= 60 else f", about {rest:.0f} s left"),
                      flush=True)
            res.update({"path": rec["path"], "size": rec.get("size"), "mtime": rec.get("mtime"), "asked": args.frames,
                        "engine": cv2.__version__, "onnx": bool(args.onnx)})
            clips[str(rec["index"])] = res
    data = {"engine": "opencv-haar", "version": cv2.__version__, "clips": clips}
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, out_path)
    seen = [clips[str(r["index"])] for r in todo]
    print(f"vision: {len(work_list)} clips looked at, {len(todo) - len(work_list)} reused; people in {sum(1 for c in seen if c['people'])}, "
          f"faces in {sum(1 for c in seen if c['faces'] > 0)} -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
