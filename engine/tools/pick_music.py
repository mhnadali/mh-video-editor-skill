#!/usr/bin/env python3
"""Track C: choose the best-fitting music from the user's OWN library for a brief. It never downloads anything; it
ranks the tracks the user already has by how well they fit a target tempo and length.

    python engine/tools/pick_music.py MUSIC_DIR [--target-bpm 120] [--target-seconds 28] [--max-seconds 60]

Ranks each track by tempo closeness (half/double counts as a match), duration closeness, grid confidence, and whether
it has a clear drop, then prints the ranking as JSON with the best first.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import vid_beats          # noqa: E402
import vidlib as vl       # noqa: E402

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}


def list_audio(folder: str | Path) -> list[Path]:
    root = Path(folder)
    if not root.is_dir():
        raise vl.EngineError(f"folder not found: {folder}")
    return sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS)


def tempo_distance(bpm: float, target: float) -> float:
    """BPM distance, treating half and double tempo as the same pulse."""
    return min(abs(bpm - target), abs(bpm * 2 - target), abs(bpm / 2 - target))


def fit_score(res: dict, target_bpm: float | None, target_seconds: float | None) -> float:
    score = 60.0
    if target_bpm:
        score -= min(40.0, tempo_distance(res["bpm"], target_bpm))
    if target_seconds and res.get("duration"):
        score -= min(30.0, abs(res["duration"] - target_seconds) / target_seconds * 60.0)
    score += res.get("confidence", 0.0) * 15.0
    if res.get("drop") is not None:
        score += 6.0
    return round(score, 1)


def rank(folder: str | Path, target_bpm: float | None = None, target_seconds: float | None = None,
         max_seconds: float | None = 60.0) -> list[dict]:
    out = []
    for p in list_audio(folder):
        try:
            res = vid_beats.analyse(p, max_seconds=max_seconds)
        except vl.EngineError as e:
            out.append({"file": vl.ffp(p), "error": str(e).splitlines()[0]})
            continue
        out.append({"file": vl.ffp(p), "bpm": res["bpm"], "duration": res["duration"],
                    "confidence": res["confidence"], "drop": res["drop"], "sections": len(res.get("sections") or []),
                    "fit": fit_score(res, target_bpm, target_seconds)})
    out.sort(key=lambda r: r.get("fit", -1), reverse=True)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("music_dir")
    ap.add_argument("--target-bpm", type=float)
    ap.add_argument("--target-seconds", type=float)
    ap.add_argument("--max-seconds", type=float, default=60.0)
    a = ap.parse_args(argv)
    ranked = rank(a.music_dir, a.target_bpm, a.target_seconds, a.max_seconds)
    print(json.dumps(ranked, indent=2))
    best = next((r for r in ranked if "fit" in r), None)
    if best:
        print("\nbest fit: " + best["file"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
