"""What a clip's own sound is: voices, wind, steady rumble (an engine), quiet, or clipped.

One FFmpeg pass per window splits the sound into bands (all, under 150 Hz, the speech band 300-3400 Hz) and prints the level of
every 20 ms, and hands over the 30-300 Hz band at 1 kHz for the periodicity. From those:

  level_db   the window's mean level (dBFS)
  low_db     the share of the sound under 150 Hz (dB against all of it): wind and engines sit there, voices do not
  voice_db   the share in the speech band
  syllables  how much the speech band's loudness moves at 2.5-7 Hz (the rate of syllables) against 0.5-15 Hz: talk is high,
             a steady noise or an engine is low
  depth      how deep that movement is (the 2.5-7 Hz swing against the mean loudness): talk swings, an engine does not
  periodic   0-1, the strongest repetition of the 30-300 Hz band at 5-40 ms: an engine or a motor repeats, wind does not
  clipped    the share of 20 ms frames whose peak reaches -0.3 dBFS

The labels are a reading of these numbers with provisional thresholds (set on synthetic sounds, see docs/CALIBRATION.md); they
become trustworthy only against the user's own ears: `audio review` makes a listening page and `calibrate_audio.py` checks the
thresholds against the labels it exports. The engine itself cannot hear.
"""
from __future__ import annotations

import math
import subprocess
import tempfile
from pathlib import Path

import vidlib as vl

RATE = 16000
FRAME = 320                    # 20 ms at 16 kHz
LOW_HZ = 150
VOICE_BAND = (300, 3400)
CLIP_DB = -0.3
# provisional thresholds (synthetic sounds); calibrate_audio.py checks them against labels from the listening page
THRESHOLDS = {"quiet_db": -55.0, "wind_low_db": -4.0, "wind_periodic": 0.5, "rumble_low_db": -6.0,
              "voice_db": -8.0, "voice_syllables": 0.35, "voice_depth": 0.15, "clipped": 0.01}
LABELS = ("voices", "wind", "rumble", "quiet", "other")


def _series(path: Path, key: str) -> list:
    out = []
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if key in line:
            v = vl.parse_float(line.split("=", 1)[1])
            out.append(v if v is not None and math.isfinite(v) else -120.0)
    return out


def _mean_db(series: list) -> float | None:
    if not series:
        return None
    lin = [10 ** (max(v, -120.0) / 10.0) for v in series]
    return 10.0 * math.log10(max(sum(lin) / len(lin), 1e-12))


def syllables(series_db: list, fps: float = RATE / FRAME) -> tuple:
    """(share of the loudness envelope's movement at 2.5-7 Hz within 0.5-15 Hz, depth of that movement against the mean)."""
    env = [10 ** (max(v, -100.0) / 20.0) for v in series_db]
    n = len(env)
    if n < int(fps):                                   # under a second: no rhythm to measure
        return 0.0, 0.0
    mean = sum(env) / n
    x = [e - mean for e in env]
    total = band = peak = 0.0
    f = 0.5
    while f <= 15.0:
        re_ = sum(v * math.cos(2 * math.pi * f * k / fps) for k, v in enumerate(x))
        im = sum(v * math.sin(2 * math.pi * f * k / fps) for k, v in enumerate(x))
        p = re_ * re_ + im * im
        total += p
        if 2.5 <= f <= 7.0:
            band += p
            peak = max(peak, math.sqrt(p) * 2.0 / n)    # amplitude of the strongest syllable-rate swing
        f += 0.5
    if total <= 0 or mean <= 0:
        return 0.0, 0.0
    return round(band / total, 3), round(peak / mean, 3)


def periodicity(samples: list, rate: int = 1000) -> float:
    """Strongest normalised autocorrelation of a 30-300 Hz signal at 5-40 ms lags (a 25-200 Hz fundamental)."""
    n = len(samples)
    if n < rate // 2:
        return 0.0
    mean = sum(samples) / n
    x = [s - mean for s in samples]
    best = 0.0
    for lag in range(int(0.005 * rate), int(0.040 * rate) + 1):
        a, b = x[:-lag], x[lag:]
        num = sum(p * q for p, q in zip(a, b))
        den = math.sqrt(sum(p * p for p in a) * sum(q * q for q in b))
        if den > 0:
            best = max(best, num / den)
    return round(best, 3)


def measure(path: str, start: float, seconds: float) -> dict | None:
    """The numbers above for one window of a file's sound; None when the file has no sound there."""
    graph = (f"aresample={RATE},aformat=channel_layouts=mono,asplit=4[a][b][c][d];"
             f"[a]asetnsamples=n={FRAME},astats=metadata=1:reset=1:measure_perchannel=RMS_level+Peak_level:measure_overall=none,"
             "ametadata=print:file=all.txt,anullsink;"
             f"[b]lowpass=f={LOW_HZ},lowpass=f={LOW_HZ},asetnsamples=n={FRAME},astats=metadata=1:reset=1:measure_perchannel=RMS_level:"
             "measure_overall=none,ametadata=print:file=low.txt,anullsink;"
             f"[c]highpass=f={VOICE_BAND[0]},lowpass=f={VOICE_BAND[1]},asetnsamples=n={FRAME},astats=metadata=1:reset=1:"
             "measure_perchannel=RMS_level:measure_overall=none,ametadata=print:file=voice.txt,anullsink;"
             "[d]highpass=f=30,lowpass=f=300,lowpass=f=300,aresample=1000[p]")
    with tempfile.TemporaryDirectory(prefix="mhvid_audio_") as d:
        r = subprocess.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-ss", f"{max(0.0, start):.3f}", "-t", f"{seconds:.3f}",
                            "-i", vl.ffp(Path(path).resolve()), "-vn", "-filter_complex", graph, "-map", "[p]", "-f", "s16le", "-"],
                           capture_output=True, cwd=d)
        allr = _series(Path(d) / "all.txt", "RMS_level")
        peaks = _series(Path(d) / "all.txt", "Peak_level")
        low = _series(Path(d) / "low.txt", "RMS_level")
        voice = _series(Path(d) / "voice.txt", "RMS_level")
        raw = r.stdout or b""
        low_wave = list(memoryview(raw[: len(raw) // 2 * 2]).cast("h")) if raw else []
    if r.returncode != 0 and not allr:
        return None
    level = _mean_db(allr)
    if level is None:
        return None
    lo, vo = _mean_db(low), _mean_db(voice)
    share, depth = syllables(voice) if voice else (0.0, 0.0)
    return {"level_db": round(level, 1),
            "low_db": round(lo - level, 1) if lo is not None else None,
            "voice_db": round(vo - level, 1) if vo is not None else None,
            "syllables": share, "depth": depth, "periodic": periodicity(low_wave),
            "clipped": round(sum(1 for p in peaks if p >= CLIP_DB) / len(peaks), 3) if peaks else 0.0}


def label(m: dict | None, th: dict | None = None) -> str:
    """One word for a measured window (provisional; see the module text)."""
    t = dict(THRESHOLDS, **(th or {}))
    if not m:
        return "quiet"
    if m["level_db"] < t["quiet_db"]:
        return "quiet"
    if ((m.get("voice_db") or -99) >= t["voice_db"] and m.get("syllables", 0.0) >= t["voice_syllables"]
            and m.get("depth", 0.0) >= t["voice_depth"]):
        return "voices"
    low = m.get("low_db") if m.get("low_db") is not None else -99
    if low >= t["wind_low_db"] and (m.get("periodic") or 0.0) < t["wind_periodic"]:
        return "wind"
    if low >= t["rumble_low_db"]:
        return "rumble"
    return "other"


def good_ambience(m: dict | None, th: dict | None = None) -> tuple:
    """(usable under the music?, why not). Wind and clipping are never good ambience; silence adds nothing."""
    t = dict(THRESHOLDS, **(th or {}))
    lab = label(m, t)
    if lab == "quiet":
        return False, "quiet"
    if lab == "wind":
        return False, "wind"
    if m and m.get("clipped", 0.0) > t["clipped"]:
        return False, "clipped"
    return True, lab
