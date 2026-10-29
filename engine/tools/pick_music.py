#!/usr/bin/env python3
"""Track C: choose the best-fitting music from the user's OWN library for a brief. It never downloads anything; it
ranks the tracks the user already has by how well they fit a target tempo and length.

    python engine/tools/pick_music.py MUSIC_DIR [--target-bpm 120] [--target-seconds 28] [--max-seconds 60] [--chapters chapters.json]

Ranks each track by tempo closeness (half/double counts as a match), duration closeness, grid confidence, and whether
it has a clear drop, then prints the ranking as JSON with the best first. With `--chapters` it also compares the energy of
the first `--target-seconds` of each track with the story's energy arc (each chapter as long as its weight, as loud as its
energy): music that is calm where the story is calm and loud where the story peaks ranks higher (`arc_fit`, -1 to 1).
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


ARC_POINTS = 24
ARC_WEIGHT = 15.0                 # points of `fit` for a perfect match of the story arc


def story_arc(cfg: dict, n: int = ARC_POINTS) -> list[float]:
    """The story's energy over time: each chapter takes a share of the time equal to its weight's share, at its energy."""
    import vid_story
    chapters = [c for c in vid_story.validate_chapters(cfg)["chapters"] if c.get("weight", 1) > 0]
    total = sum(c.get("weight", 1) for c in chapters) or 1.0
    edges, t = [], 0.0
    for c in chapters:
        t += c.get("weight", 1) / total
        edges.append((t, vid_story.chapter_energy(c)))
    out = []
    for k in range(n):
        x = (k + 0.5) / n
        out.append(next(e for end, e in edges if x <= end + 1e-9) if edges else 0.5)
    return out


def music_profile(res: dict, seconds: float, n: int = ARC_POINTS) -> list[float] | None:
    """The track's section energy over its first `seconds`, sampled at n points (None without sections)."""
    secs = res.get("sections") or []
    if not secs or seconds <= 0:
        return None
    out = []
    for k in range(n):
        t = (k + 0.5) / n * seconds
        cur = secs[0]["energy"]
        for s in secs:
            if s["start"] <= t:
                cur = s["energy"]
        out.append(cur)
    return out


def arc_correlation(a: list, b: list) -> float | None:
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    sa = sum((x - ma) ** 2 for x in a) ** 0.5
    sb = sum((y - mb) ** 2 for y in b) ** 0.5
    if sa < 1e-9 or sb < 1e-9:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb)


def rank(folder: str | Path, target_bpm: float | None = None, target_seconds: float | None = None,
         max_seconds: float | None = 60.0, chapters: dict | None = None) -> list[dict]:
    arc = story_arc(chapters) if chapters else None
    out = []
    for p in list_audio(folder):
        try:
            res = vid_beats.analyse(p, max_seconds=max_seconds)
        except vl.EngineError as e:
            out.append({"file": vl.ffp(p), "error": str(e).splitlines()[0]})
            continue
        item = {"file": vl.ffp(p), "bpm": res["bpm"], "duration": res["duration"],
                "confidence": res["confidence"], "drop": res["drop"], "sections": len(res.get("sections") or []),
                "fit": fit_score(res, target_bpm, target_seconds)}
        if arc:
            prof = music_profile(res, target_seconds or res.get("duration") or 0.0)
            r = arc_correlation(arc, prof) if prof else None
            item["arc_fit"] = None if r is None else round(r, 2)
            item["fit"] = round(item["fit"] + ARC_WEIGHT * (r or 0.0), 1)
        out.append(item)
    out.sort(key=lambda r: r.get("fit", -1), reverse=True)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("music_dir")
    ap.add_argument("--target-bpm", type=float)
    ap.add_argument("--target-seconds", type=float)
    ap.add_argument("--max-seconds", type=float, default=60.0)
    ap.add_argument("--chapters", help="chapters.json: also rank by how well the music follows the story's energy arc")
    a = ap.parse_args(argv)
    ranked = rank(a.music_dir, a.target_bpm, a.target_seconds, a.max_seconds, vl.load_json(a.chapters) if a.chapters else None)
    print(json.dumps(ranked, indent=2))
    best = next((r for r in ranked if "fit" in r), None)
    if best:
        print("\nbest fit: " + best["file"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
