#!/usr/bin/env python3
"""Phase 7: generate a tiny, zero-footage sample project so a newcomer can run the whole pipeline with no media.

It synthesises a handful of clips and a click track with FFmpeg (the same trick the test suite uses), then runs the
real pipeline (probe -> beats -> plan -> lint) to leave a ready-to-render EDL. The point is onboarding: after this,
`python engine/scripts/mh_video.py render <dir>/sample.edl.json --preview` produces a video from nothing.

    python engine/tools/make_sample.py OUTDIR [--clips N] [--seconds S]
"""
from __future__ import annotations

import argparse
import math
import struct
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import vid_beats          # noqa: E402
import vid_edl as ve      # noqa: E402
import vid_plan as vp     # noqa: E402
import vid_probe          # noqa: E402
import vidlib as vl       # noqa: E402

SOURCES = ["testsrc2", "smptebars", "rgbtestsrc", "testsrc"]


def _click_track(path: Path, bpm: float = 120.0, first: float = 0.5, seconds: float = 8.0, sr: int = 22050) -> None:
    """A WAV with a kick on every beat and a loudness step halfway (a synthetic 'drop') so `beats` has something to find."""
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


def make_sample(out_dir: str | Path, clips: int = 6, seconds: float = 4.0) -> dict:
    out = Path(out_dir)
    footage = out / "footage"
    footage.mkdir(parents=True, exist_ok=True)
    ff = vl.find_ffmpeg()["ffmpeg"]
    paths = []
    for i in range(clips):
        p = footage / f"clip{i:02d}.mp4"
        vl.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", f"{SOURCES[i % len(SOURCES)]}=s=360x640:r=30:d={seconds}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", vl.ffp(p)])
        paths.append(p)
    music = footage / "music.wav"
    _click_track(music, seconds=max(8.0, clips * 1.2))
    work = out / "work"
    recs = vid_probe.scan(footage)
    vid_probe.write_inventory(recs, work)
    beats = vid_beats.analyse(music)
    vl.save_json(work / "beats.json", beats)
    body = vp.body_start_time(beats, beats["duration"], beats["period"], 2)
    need = vp.layout(beats["duration"], body, beats["period"], 2)["shots"]
    pool = [vl.ffp(paths[i % len(paths)]) for i in range(need)]
    (work / "clips.txt").write_text("\n".join(pool) + "\n", encoding="utf-8")
    edl = vp.plan([{"clip": c} for c in pool], beats, name="sample", music=vl.ffp(music), duration=beats["duration"])
    edl_path = out / "sample.edl.json"
    vl.save_json(edl_path, edl)
    issues = [i for i in ve.lint(edl) if i["code"] != "clip-reused"]
    return {"edl": str(edl_path), "footage": str(footage), "work": str(work), "music": str(music),
            "shots": len(edl["shots"]), "lint_clean": not issues,
            "render_cmd": f'python engine/scripts/mh_video.py render "{edl_path}" --preview'}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("outdir")
    ap.add_argument("--clips", type=int, default=6)
    ap.add_argument("--seconds", type=float, default=4.0)
    a = ap.parse_args(argv)
    import json
    res = make_sample(a.outdir, a.clips, a.seconds)
    print(json.dumps(res, indent=2))
    print("\nnext: " + res["render_cmd"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
