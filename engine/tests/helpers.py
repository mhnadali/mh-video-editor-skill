"""Synthetic fixtures: clips from FFmpeg test sources and a click-track WAV. No real footage needed."""
from __future__ import annotations

import atexit
import math
import os
import random
import shutil
import struct
import sys
import tempfile
import wave
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import vidlib as vl  # noqa: E402

_TMP: Path | None = None


def tmpdir() -> Path:
    global _TMP
    if _TMP is None:
        _TMP = Path(tempfile.mkdtemp(prefix="mhvid_test_"))
        atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
    return _TMP


def ffmpeg_available() -> bool:
    try:
        vl.find_ffmpeg()
        return True
    except vl.EngineError:
        return False


SLOW_ATTR = "_mh_slow"
FAST = bool(os.environ.get("MH_TEST_FAST"))


def slow(obj):
    """Mark a test or test class as slow (many seconds of rendering). `run_tests.py --fast` (MH_TEST_FAST=1) skips them."""
    import unittest
    setattr(obj, SLOW_ATTR, True)
    return unittest.skipIf(FAST, "slow test (MH_TEST_FAST=1)")(obj)


def filters_available(*names: str) -> bool:
    """True when the installed FFmpeg has every named filter (stock distro builds lack libass, libvidstab ...)."""
    try:
        return set(names) <= vl.available_filters()
    except vl.EngineError:
        return False


def requires_filters(*names: str):
    import unittest
    return unittest.skipUnless(filters_available(*names), f"this FFmpeg build lacks: {', '.join(names)}")


def encoder_available(name: str) -> bool:
    try:
        return name in vl.run([vl.find_ffmpeg()["ffmpeg"], "-hide_banner", "-encoders"]).stdout
    except vl.EngineError:
        return False


# everything the optional features of the engine need (see vidlib.FEATURE_FILTERS)
ALL_FEATURE_FILTERS = tuple(sorted({f for names in vl.FEATURE_FILTERS.values() for f in names}))


def click_track(path: Path, bpm: float = 92.0, first: float = 1.0, seconds: float = 20.0, step_at: float | None = 10.0,
                sr: int = 22050, accent_every: int = 0, accent_phase: int = 0, accent_gain: float = 2.0,
                bpm_end: float | None = None) -> float:
    """WAV with a kick on every beat, hats on the off-beats, and a loudness step at `step_at`. Returns the first period.

    accent_every=4 makes every 4th kick louder (phase = which beat index carries the accent); bpm_end ramps the tempo
    linearly from `bpm` to `bpm_end` over the track.
    """
    n = int(seconds * sr)
    buf = [0.0] * n
    rnd = random.Random(7)
    k = 0
    t = first
    while True:
        frac = min(1.0, max(0.0, (t - first) / max(seconds - first, 1e-6)))
        cur_bpm = bpm if bpm_end is None else bpm + (bpm_end - bpm) * frac
        period = 60.0 / cur_bpm
        if t >= seconds - 0.15:
            break
        gain = 1.0 if (step_at is None or t < step_at) else 1.8
        if accent_every and k % accent_every == accent_phase:
            gain *= accent_gain
        i0 = int(t * sr)
        for i in range(int(0.12 * sr)):
            if i0 + i < n:
                tt = i / sr
                buf[i0 + i] += gain * 0.5 * math.sin(2 * math.pi * 55 * tt) * math.exp(-tt * 28)
        h0 = int((t + period / 2) * sr)
        for i in range(int(0.02 * sr)):
            if h0 + i < n:
                buf[h0 + i] += 0.12 * gain * (rnd.random() * 2 - 1) * math.exp(-i / (0.006 * sr))
        k += 1
        t += period
    if step_at is not None:
        for i in range(int(step_at * sr), n):
            buf[i] += 0.18 * math.sin(2 * math.pi * 220 * i / sr)
    peak = max(abs(x) for x in buf) or 1.0
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, x / peak * 0.9)) * 32767)) for x in buf))
    return 60.0 / bpm


SOURCES = ["testsrc2", "smptebars", "rgbtestsrc", "testsrc"]


def make_clip(path: Path, source: str = "testsrc2", size: str = "640x360", seconds: float = 4.0, fps: int = 30,
              extra: list[str] | None = None, audio: str | None = None) -> Path:
    """Synthetic clip. `audio` is an FFmpeg audio source such as "sine=f=220" or "anoisesrc=c=brown:a=0.3" (adds a track)."""
    cmd = [vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-f", "lavfi", "-i", f"{source}=s={size}:r={fps}:d={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"{audio}:d={seconds}" if ":" in audio or "=" in audio else audio]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "128k", "-shortest"]
    cmd += (extra or []) + [str(path)]
    vl.run(cmd)
    return path


def make_clips(directory: Path, count: int = 4, seconds: float = 4.0) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(count):
        out.append(make_clip(directory / f"clip{i}.mp4", SOURCES[i % len(SOURCES)], seconds=seconds))
    return out


def make_hlg_clip(path: Path, seconds: float = 3.0) -> Path:
    """A clip flagged as HLG (BT.2020). 10-bit HEVC like an iPhone when libx265 exists, else 8-bit H.264 with the same tags."""
    src = ["-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=640x360:r=30:d={seconds}"]
    if encoder_available("libx265"):
        cmd = [vl.find_ffmpeg()["ffmpeg"], *src, "-c:v", "libx265", "-pix_fmt", "yuv420p10le",
               "-x265-params", "log-level=error:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc", str(path)]
    else:
        cmd = [vl.find_ffmpeg()["ffmpeg"], *src, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_primaries", "bt2020",
               "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc", str(path)]
    vl.run(cmd)
    return path


def make_rotated_clip(src: Path, dst: Path, rotation: int = 90) -> Path:
    vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-display_rotation", str(rotation), "-i", str(src),
            "-c", "copy", str(dst)])
    return dst
