#!/usr/bin/env python3
"""Content plug-in of the M.H. Video Editor Skill: image and text embeddings with SigLIP (Google, Apache 2.0). Runs inside its own
environment (`plugins/vision/.venv-content`: onnxruntime and tokenizers), offline; the model files come once from `setup.py
--content` (a pinned revision, every file checked against its SHA-256).

    python content_run.py --job job.json --out content.json --ffmpeg /path/to/ffmpeg

The engine writes the job (vid_content.build_job): for every clip its path, size, time stamps, the moments to look at and the
filter that brings a frame to Rec.709 (HLG clips are tone mapped first), plus the texts to embed. For every clip this writes the
normalised embedding of each frame and of their mean (float16, base64), and one embedding per text. A clip whose file, moments
and model are unchanged is not looked at again. Nothing here touches the network.
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

MODEL_DIR = Path(__file__).resolve().parent / "models" / "siglip-base-patch16-224"
SIZE = 224
MAX_TOKENS = 64
PAD_ID = 1                         # "</s>": SigLIP was trained on texts padded to 64 tokens with it


def b64(a: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(a, dtype="<f2").tobytes()).decode("ascii")


def unit(a: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(a, axis=-1, keepdims=True)
    return a / np.maximum(n, 1e-12)


def frame(ffmpeg: str, path: str, t: float, vf: str) -> np.ndarray | None:
    r = subprocess.run([ffmpeg, "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path, "-frames:v", "1", "-an", "-vf",
                        f"{vf}scale={SIZE}:{SIZE}:flags=bicubic,format=rgb24", "-f", "rawvideo", "-"], capture_output=True)
    if r.returncode != 0 or len(r.stdout) < SIZE * SIZE * 3:
        return None
    img = np.frombuffer(r.stdout[: SIZE * SIZE * 3], dtype=np.uint8).reshape(SIZE, SIZE, 3).astype(np.float32)
    return ((img / 255.0 - 0.5) / 0.5).transpose(2, 0, 1)          # the SigLIP processor: rescale, mean 0.5, std 0.5, CHW


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--job", required=True); ap.add_argument("--out", required=True); ap.add_argument("--ffmpeg", required=True)
    ap.add_argument("--workers", type=int, default=4); ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    job = json.loads(Path(a.job).read_text(encoding="utf-8"))
    out_path = Path(a.out)
    old = json.loads(out_path.read_text(encoding="utf-8")) if out_path.is_file() and not a.force else {}
    same_model = old.get("revision") == job["revision"]
    vision = ort.InferenceSession(str(MODEL_DIR / "onnx" / "vision_model_quantized.onnx"), providers=["CPUExecutionProvider"])
    result = {"model": job["model"], "revision": job["revision"], "clips": {}, "texts": {}}
    todo = []
    for c in job["clips"]:
        prev = (old.get("clips") or {}).get(str(c["index"])) if same_model else None
        key = [c["path"], c["size"], c["mtime"], c["times"]]
        if prev and prev.get("key") == key:
            result["clips"][str(c["index"])] = prev
        else:
            todo.append(c)
    start = time.time()
    with ThreadPoolExecutor(max(1, a.workers)) as ex:
        for k, c in enumerate(todo, 1):
            frames = [f for f in ex.map(lambda t: frame(a.ffmpeg, c["path"], t, c.get("vf", "")), c["times"]) if f is not None]
            if frames:
                emb = unit(vision.run(["pooler_output"], {"pixel_values": np.stack(frames).astype(np.float32)})[0])
                result["clips"][str(c["index"])] = {"key": [c["path"], c["size"], c["mtime"], c["times"]], "frames": b64(emb),
                                                    "n": len(frames), "mean": b64(unit(emb.mean(axis=0)))}
            if k % 25 == 0 or k == len(todo):
                left = (time.time() - start) / k * (len(todo) - k)
                print(f"  {k}/{len(todo)} clips, about {left:.0f} s left", flush=True)
    texts = job.get("texts") or []
    if texts:
        tok = Tokenizer.from_file(str(MODEL_DIR / "tokenizer.json"))
        tok.enable_truncation(MAX_TOKENS)
        tok.enable_padding(length=MAX_TOKENS, pad_id=PAD_ID, pad_token="</s>")
        prev_texts = old.get("texts") or {} if same_model else {}
        need = [t for t in texts if t not in prev_texts]
        result["texts"] = {t: prev_texts[t] for t in texts if t in prev_texts}
        if need:
            text = ort.InferenceSession(str(MODEL_DIR / "onnx" / "text_model_quantized.onnx"), providers=["CPUExecutionProvider"])
            ids = np.array([e.ids for e in tok.encode_batch([t.lower() for t in need])], dtype=np.int64)
            emb = unit(text.run(["pooler_output"], {"input_ids": ids})[0])
            for t, e in zip(need, emb):
                result["texts"][t] = b64(e)
    out_path.write_text(json.dumps(result), encoding="utf-8")
    print(f"content: {len(result['clips'])} clips ({len(todo)} new), {len(result['texts'])} texts -> {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
