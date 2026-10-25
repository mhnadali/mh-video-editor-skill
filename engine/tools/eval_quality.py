#!/usr/bin/env python3
"""Phase 9: an evaluation harness for editing quality, so a change to the beat, scoring, pick or planner code is
judged by measured deltas on a fixed synthetic corpus rather than by eye.

It builds a click track with a known tempo and a set of clips, then reports:
  * beats: tempo error and first-beat error against the ground truth, and how tightly the grid lands on the clicks;
  * pick: how diverse the chosen shots are (distinct clips, look-alike neighbours);
  * plan: the restraint numbers of the resulting EDL (visible effects, transition share, transition variety).

    python engine/tools/eval_quality.py [--bpm 120] [--clips 10] [--json out.json]

Run it before and after a change and compare. Lower beat error and higher pick diversity, with the restraint numbers
inside budget, mean "better" — not a subjective judgement.
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import vid_beats          # noqa: E402
import vid_edl as ve      # noqa: E402
import vid_plan as vp     # noqa: E402
import vid_probe          # noqa: E402
import vid_score          # noqa: E402
import vidlib as vl       # noqa: E402

SOURCES = ["testsrc2", "smptebars", "rgbtestsrc", "testsrc", "yuvtestsrc", "pal75bars"]


def _click_track(path: Path, bpm: float, first: float, seconds: float, sr: int = 22050) -> None:
    n = int(seconds * sr)
    buf = [0.0] * n
    period = 60.0 / bpm
    t = first
    while t < seconds - 0.15:
        gain = 1.0 if t < seconds / 2 else 1.8
        i0 = int(t * sr)
        for i in range(int(0.12 * sr)):
            if i0 + i < n:
                tt = i / sr
                buf[i0 + i] += gain * 0.5 * math.sin(2 * math.pi * 55 * tt) * math.exp(-tt * 28)
        t += period
    for i in range(int(seconds / 2 * sr), n):
        buf[i] += 0.18 * math.sin(2 * math.pi * 220 * i / sr)
    peak = max(abs(x) for x in buf) or 1.0
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, x / peak * 0.9)) * 32767)) for x in buf))


def evaluate(bpm: float = 120.0, first: float = 0.5, clips: int = 10, seconds: float = 12.0,
             workdir: str | Path | None = None) -> dict:
    tmp = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="mhvid_eval_"))
    footage = tmp / "footage"
    footage.mkdir(parents=True, exist_ok=True)
    ff = vl.find_ffmpeg()["ffmpeg"]
    for i in range(clips):
        vl.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", f"{SOURCES[i % len(SOURCES)]}=s=320x568:r=30:d={seconds / 3:.2f}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", vl.ffp(footage / f"c{i:02d}.mp4")])
    music = footage / "music.wav"
    _click_track(music, bpm, first, seconds)
    beats = vid_beats.analyse(music)
    bpm_err = abs(beats["bpm"] - bpm)
    # tightness: median distance of each grid beat to the nearest true click
    true_clicks = [first + k * 60.0 / bpm for k in range(int((seconds - first) / (60.0 / bpm)))]
    def nearest(t):
        return min(abs(t - c) for c in true_clicks) if true_clicks else 0.0
    errs = sorted(nearest(t) for t in beats["beats"])
    grid_ms = round(1000 * (errs[len(errs) // 2] if errs else 0.0), 1)
    beat_metrics = {"bpm_truth": bpm, "bpm_measured": beats["bpm"], "bpm_error": round(bpm_err, 2),
                    "first_beat_truth": first, "first_beat_measured": beats["t0"],
                    "first_beat_error_ms": round(1000 * abs(beats["t0"] - first), 1),
                    "median_grid_error_ms": grid_ms, "confidence": beats["confidence"]}
    recs = vid_probe.scan(footage)
    scores = vid_score.score_inventory(recs, progress=None)
    vid_score.save_scores(scores, tmp)
    sc = {s["index"]: s for s in scores if "quality" in s}
    body = vp.body_start_time(beats, beats["duration"], beats["period"], 2)
    need = vp.layout(beats["duration"], body, beats["period"], 2)["shots"]
    cands = vid_score.candidates(recs, sc, skip_flagged=False, skip_similar=False)
    pick_metrics = {"library": len(sc), "needed": need}
    edl_metrics = {}
    if cands and need >= 2:
        if len(cands) >= need and need >= 4:
            intro, outro = 2, 1
            payoff = max(1, (need - intro - outro) // 2)
            build = need - intro - outro - payoff
            chosen = vid_score.pick(cands, intro, build, payoff, outro)
        else:
            chosen = [cands[i % len(cands)] for i in range(need)]   # fewer clips than beats: cycle (diversity reflects it)
        idx = [c["index"] for c in chosen]
        pick_metrics.update(distinct_clips=len(set(idx)), diversity=round(len(set(idx)) / len(idx), 2))
        edl = vp.plan([{"clip": c["path"], "src_start": c.get("best_start")} for c in chosen], beats,
                      name="eval", music=vl.ffp(music), duration=beats["duration"])
        _au, audit = ve_audit(edl)
        edl_metrics = audit
    return {"beats": beat_metrics, "pick": pick_metrics, "plan": edl_metrics, "workdir": str(tmp)}


def ve_audit(edl: dict) -> tuple[list, dict]:
    import vid_qa
    return vid_qa.audit_edit(edl)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bpm", type=float, default=120.0)
    ap.add_argument("--first", type=float, default=0.5)
    ap.add_argument("--clips", type=int, default=10)
    ap.add_argument("--seconds", type=float, default=12.0)
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    res = evaluate(a.bpm, a.first, a.clips, a.seconds)
    if a.json:
        vl.save_json(a.json, res)
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
