#!/usr/bin/env python3
"""Measured throughput on a large synthetic library, so a change to scoring can be judged on numbers, not a hunch.

The selection scales with the library; the expensive stage is `score` (one FFmpeg decode per clip), and scores are
cached per file (same size and mtime), so a second run only measures what changed. This tool synthesises N clips with
FFmpeg (no footage needed, the same trick as make_sample.py), then times:

  synth     making the clips (not part of the engine; shown so the rest is in context)
  probe     building the inventory (duration, rotation, fps, duplicates)
  score     the cold run (every clip measured)
  rescore   the warm run (nothing changed, so every clip is reused from the cache)

and reports clips/second for each and the warm-run speed-up. This is where the ROADMAP's "measured throughput on
500+ clips" and "resumable scoring for very large libraries" are checked:

    python engine/tools/bench_throughput.py --clips 500 --seconds 1.5 --work /tmp/bench
    python engine/tools/bench_throughput.py --clips 50 --json bench.json

The work folder is disposable; nothing is written into the repository.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import vid_probe        # noqa: E402
import vid_score        # noqa: E402
import vidlib as vl     # noqa: E402

SOURCES = ["testsrc2", "smptebars", "rgbtestsrc", "testsrc"]


def _synth_clips(footage: Path, clips: int, seconds: float, size: str) -> list[Path]:
    """`clips` short clips, each visually distinct (a per-clip hue rotation) so none is detected as a duplicate."""
    footage.mkdir(parents=True, exist_ok=True)
    ff = vl.find_ffmpeg()["ffmpeg"]
    paths = []
    for i in range(clips):
        p = footage / f"clip{i:04d}.mp4"
        if not p.is_file():
            vl.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", f"{SOURCES[i % len(SOURCES)]}=s={size}:r=30:d={seconds}",
                    "-vf", f"hue=h={(i * 23) % 360}", "-c:v", "libx264", "-pix_fmt", "yuv420p", vl.ffp(p)])
        paths.append(p)
    return paths


def _rate(label: str, n: int, seconds: float) -> dict:
    return {"stage": label, "clips": n, "seconds": round(seconds, 3),
            "clips_per_sec": round(n / seconds, 1) if seconds > 0 else None}


def bench(work: str | Path, clips: int = 200, seconds: float = 1.5, size: str = "160x90",
          workers: int | None = None) -> dict:
    work = Path(work)
    footage = work / "footage"
    t0 = time.monotonic()
    _synth_clips(footage, clips, seconds, size)
    synth = _rate("synth", clips, time.monotonic() - t0)

    t0 = time.monotonic()
    recs = vid_probe.scan(footage)
    probe = _rate("probe", len(recs), time.monotonic() - t0)

    t0 = time.monotonic()
    cold = vid_score.score_inventory(recs, workers=workers, hw=False, previous={})
    score = _rate("score", len(recs), time.monotonic() - t0)

    previous = {s["path"]: s for s in cold if "path" in s}
    reused_before = len(previous)
    t0 = time.monotonic()
    warm = vid_score.score_inventory(recs, workers=workers, hw=False, previous=previous)
    rescore = _rate("rescore", len(recs), time.monotonic() - t0)
    # a reused entry keeps its measured quality unchanged; count how many came back identical
    cold_by_i = {s["index"]: s for s in cold}
    unchanged = sum(1 for s in warm if s.get("quality") == (cold_by_i.get(s["index"]) or {}).get("quality"))

    speedup = round(score["seconds"] / rescore["seconds"], 1) if rescore["seconds"] > 0 else None
    return {"clips": clips, "seconds_each": seconds, "size": size, "workers": workers,
            "stages": [synth, probe, score, rescore],
            "resumable": {"reused_pool": reused_before, "unchanged_after_rescore": unchanged, "warm_speedup": speedup},
            "ffmpeg": ".".join(str(x) for x in vl.ffmpeg_version())}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--clips", type=int, default=200)
    ap.add_argument("--seconds", type=float, default=1.5, help="length of each synthetic clip")
    ap.add_argument("--size", default="160x90", help="frame size of the synthetic clips (small keeps the decode cheap)")
    ap.add_argument("--work", default=None, help="work folder for the clips and inventory (default: a temp folder)")
    ap.add_argument("--workers", type=int)
    ap.add_argument("--json", help="also write the result here")
    a = ap.parse_args(argv)
    work = a.work
    if not work:
        import tempfile
        work = tempfile.mkdtemp(prefix="mhvid_bench_")
    res = bench(work, a.clips, a.seconds, a.size, a.workers)
    print(json.dumps(res, indent=2))
    if a.json:
        vl.save_json(a.json, res)
    for s in res["stages"]:
        print(f"  {s['stage']:8} {s['clips']:5} clips in {s['seconds']:7.2f}s = {s['clips_per_sec']}/s")
    r = res["resumable"]
    print(f"  warm rescore reused all {r['unchanged_after_rescore']}/{res['clips']} "
          f"(speed-up x{r['warm_speedup']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
