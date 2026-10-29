"""What a clip shows, read by an image-text model (SigLIP, the optional content plug-in) and checked against the user's labels.

The plug-in (`plugins/vision/content_run.py`, its own environment, offline) turns three frames of every clip into embeddings, and
the texts of the prompt sets below into embeddings of the same space. Here they become:

  similarity   how alike two clips look (cosine of their mean embeddings, or the best pair of frames)
  zero-shot    for a set of labels (shot size, role, people), how well each label's prompts describe the clip
  search       the clips that fit a description in words

Nothing is used by the selection until it has been measured against labels (docs/CALIBRATION.md, phase K): the engine stays
standard-library Python, and without the plug-in everything works as before.
"""
from __future__ import annotations

import base64
import json
import math
import os
import struct
import subprocess
from pathlib import Path
from typing import Optional

import vid_sheets
import vidlib as vl

PLUGIN_DIR = Path(__file__).resolve().parents[2] / "plugins" / "vision"
CONTENT_FILE = "content.json"
MODEL = "siglip-base-patch16-224"
REVISION = "4649052661e53c7000355844105f8a1792088239"
POSITIONS = (0.2, 0.5, 0.8)
DIM = 768

# Generic prompts (not written for one trip). Each label is the mean of its prompts.
PROMPTS = {
    "size": {"wide": ["a wide shot of a place seen from far away", "a wide landscape photo", "a distant view of a scene"],
             "medium": ["a medium shot of people from the waist up", "a photo of a person a few meters away", "a medium shot"],
             "close": ["a close-up of a face", "a close-up photo of an object", "an extreme close-up"]},
    "role": {"establishing": ["a photo that shows where we are", "a view of the scenery", "a landscape"],
             "portrait": ["a portrait of a person", "people looking at the camera", "people talking to each other"],
             "action": ["a photo of fast action", "a vehicle in motion", "people doing something energetic"],
             "detail": ["a close-up detail of an object", "a close-up of food", "a close-up of hands doing something"]},
    "people": {"yes": ["a photo of people", "a photo of a person"],
               "no": ["a photo with no people in it", "a photo of a landscape", "a photo of an object"]},
}
SCALE = 100.0                     # softmax temperature for zero-shot (the usual CLIP-style logit scale)


def content_python(plugin_dir: Optional[Path] = None) -> Optional[Path]:
    """The Python of the content environment (MH_CONTENT_PYTHON overrides it), or None when `setup.py --content` was not run."""
    env = os.environ.get("MH_CONTENT_PYTHON")
    if env:
        return Path(env) if Path(env).is_file() else None
    base = Path(plugin_dir) if plugin_dir else PLUGIN_DIR
    model = base / "models" / MODEL / "onnx" / "vision_model_quantized.onnx"
    for rel in (".venv-content/Scripts/python.exe", ".venv-content/bin/python"):
        p = base / rel
        if p.is_file() and model.is_file():
            return p
    return None


def build_job(inventory: list, texts: list) -> dict:
    clips = []
    for r in inventory:
        if r.get("dup_of") is not None or r.get("error") or not r.get("duration"):
            continue
        st = os.stat(r["path"]) if os.path.isfile(r["path"]) else None
        clips.append({"index": r["index"], "path": vl.ffp(r["path"]), "size": st.st_size if st else r.get("size"),
                      "mtime": int(st.st_mtime) if st else r.get("mtime"),
                      "times": [round(r["duration"] * p, 2) for p in POSITIONS],
                      "vf": vid_sheets.TONEMAP_HLG if r.get("hdr") else ""})
    return {"model": MODEL, "revision": REVISION, "clips": clips, "texts": texts}


def all_prompts() -> list:
    return sorted({t for labels in PROMPTS.values() for ps in labels.values() for t in ps})


def run(work: str | os.PathLike, inventory: list, extra_texts: Optional[list] = None, force: bool = False, log=print,
        plugin_dir: Optional[Path] = None) -> dict:
    py = content_python(plugin_dir)
    if py is None:
        raise vl.EngineError("the content plug-in is not set up: `python plugins/vision/setup.py --content` (with the owner's "
                             "approval: about 230 MB, SigLIP, Apache 2.0)")
    base = Path(plugin_dir) if plugin_dir else PLUGIN_DIR
    job = Path(work) / "content_job.json"
    vl.save_json(job, build_job(inventory, all_prompts() + list(extra_texts or [])))
    cmd = [str(py), str(base / "content_run.py"), "--job", str(job), "--out", str(Path(work) / CONTENT_FILE),
           "--ffmpeg", vl.find_ffmpeg()["ffmpeg"]] + (["--force"] if force else [])
    log("running the content plug-in (offline) ...")
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    lines = []
    for line in p.stdout:
        if line.strip():
            lines.append(line.rstrip())
            log(line.rstrip())
    err = p.stderr.read()
    if p.wait() != 0:
        raise vl.EngineError(f"the content plug-in failed: {(err or chr(10).join(lines))[-400:]}")
    return load(work, inventory)


def _vec(b: str) -> list:
    raw = base64.b64decode(b)
    return list(struct.unpack(f"<{len(raw) // 2}e", raw))


def load(work: str | os.PathLike, inventory: Optional[list] = None) -> dict:
    """{'clips': {index: {'mean': [...], 'frames': [[...], ...]}}, 'texts': {text: [...]}}; clips whose file changed are left out."""
    p = Path(work) / CONTENT_FILE
    if not p.is_file():
        return {"clips": {}, "texts": {}}
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("revision") != REVISION:
        return {"clips": {}, "texts": {}}
    by = {r["index"]: r for r in inventory or []}
    clips = {}
    for k, c in (data.get("clips") or {}).items():
        i = int(k)
        r = by.get(i)
        if inventory is not None and (r is None or vl.ffp(r["path"]) != c["key"][0] or (r.get("size") and r["size"] != c["key"][1])):
            continue                                       # the inventory was made again and this number is another file now
        fr = _vec(c["frames"])
        clips[i] = {"mean": _vec(c["mean"]), "frames": [fr[n * DIM:(n + 1) * DIM] for n in range(c.get("n", len(fr) // DIM))]}
    return {"clips": clips, "texts": {t: _vec(v) for t, v in (data.get("texts") or {}).items()}}


def dot(a: list, b: list) -> float:
    return sum(x * y for x, y in zip(a, b))


def unit(a: list) -> list:
    n = math.sqrt(sum(x * x for x in a)) or 1.0
    return [x / n for x in a]


def similarity(content: dict, i: int, j: int, best_frames: bool = False) -> Optional[float]:
    a, b = content["clips"].get(i), content["clips"].get(j)
    if not a or not b:
        return None
    if best_frames:
        return max(dot(x, y) for x in a["frames"] for y in b["frames"])
    return dot(a["mean"], b["mean"])


def label_vectors(content: dict, labels: dict) -> dict:
    out = {}
    for lab, prompts in labels.items():
        vs = [content["texts"][t] for t in prompts if t in content["texts"]]
        if vs:
            out[lab] = unit([sum(col) / len(vs) for col in zip(*vs)])
    return out


def zero_shot(content: dict, i: int, labels: dict, vecs: Optional[dict] = None) -> Optional[dict]:
    """{label: probability} for clip i over the labels (softmax of SCALE x cosine)."""
    c = content["clips"].get(i)
    vecs = vecs or label_vectors(content, labels)
    if not c or not vecs:
        return None
    logits = {k: SCALE * dot(c["mean"], v) for k, v in vecs.items()}
    m = max(logits.values())
    ex = {k: math.exp(v - m) for k, v in logits.items()}
    s = sum(ex.values())
    return {k: round(v / s, 4) for k, v in ex.items()}


def search(content: dict, text_vec: list, top: int = 10) -> list:
    """[(index, cosine)] of the clips best described by a text."""
    return sorted(((i, round(dot(c["mean"], text_vec), 4)) for i, c in content["clips"].items()), key=lambda x: -x[1])[:top]


# What was adopted after measuring on the Kavir labels (docs/CALIBRATION.md, phase K). People: zero-shot "yes" was right every time
# on two label sets (60 hand labels: precision 1.0, recall 0.86; 153 noted clips: 1.0 / 0.72; YuNet 0.83 / 0.71 and 0.82 / 0.63),
# so a "yes" counts like a sure face; a "no" leaves the face detector's reading alone. Shot size: zero-shot 74 % against the
# measured rule's 63 % (174 noted clips). Role and mood of clips the agent has not noted: the nearest centre of the noted ones
# (5-fold cross-validated on Kavir: role 74 % against 52 % from measurements, mood 67 % against 49 %; action-or-intense AUC 0.87
# against 0.74 for quality). The same moment is NOT decided by it (one different pair in twenty came out "same"); a very close
# pair inside the time window is only flagged as a look-alike.
PEOPLE_CERTAINTY = 0.9
LOOKALIKE_COS = 0.94             # above every different-moment pair of the Kavir labels (0.941 at most); 12 of 14 same ones
MIN_NOTED = 4                    # noted clips a role or mood needs before it is guessed for others


def centres(content: dict, labelled: dict) -> dict:
    """{label: unit mean embedding} from {index: label}, for labels with at least MIN_NOTED clips."""
    groups: dict = {}
    for i, lab in labelled.items():
        if lab and i in content["clips"]:
            groups.setdefault(lab, []).append(content["clips"][i]["mean"])
    return {lab: unit([sum(col) / len(vs) for col in zip(*vs)]) for lab, vs in groups.items() if len(vs) >= MIN_NOTED}


def nearest(vec: list, cent: dict) -> Optional[str]:
    return max(cent, key=lambda lab: dot(vec, cent[lab])) if cent else None


def merge_into_scores(scores: dict, content: dict, notes_by_index: Optional[dict] = None) -> int:
    """Copy the adopted readings onto the score entries: people, shot size, and role / mood guesses for clips without a note.
    The agent's notes always win (the selection reads them first). Returns how many clips were enriched."""
    if not content.get("clips"):
        return 0
    notes_by_index = notes_by_index or {}
    size_v = label_vectors(content, PROMPTS["size"])
    ppl_v = label_vectors(content, PROMPTS["people"])
    role_c = centres(content, {i: (n or {}).get("role") for i, n in notes_by_index.items()})
    mood_c = centres(content, {i: (n or {}).get("mood") for i, n in notes_by_index.items()})
    n = 0
    for i, cl in content["clips"].items():
        s = scores.get(i)
        if s is None:
            continue
        s["content_vec"] = cl["mean"]
        if ppl_v:
            p = zero_shot(content, i, None, ppl_v)["yes"]
            s["content_people"] = p
            if p >= 0.5:
                s["person"], s["person_sure"], s["person_certainty"], s["person_detector"] = True, True, PEOPLE_CERTAINTY, "content"
        if size_v:
            zs = zero_shot(content, i, None, size_v)
            s.setdefault("shot_size_measured", s.get("shot_size"))
            s["shot_size"], s["shot_size_from"] = max(zs, key=zs.get), "content"
        note = notes_by_index.get(i) or {}
        if role_c and not note.get("role"):
            s["role_guess"] = nearest(cl["mean"], role_c)
        if mood_c and not note.get("mood"):
            s["mood_guess"] = nearest(cl["mean"], mood_c)
        n += 1
    return n
