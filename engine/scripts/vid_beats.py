"""Step 2a: beat grid, tempo candidates and song sections from an audio file.

Pure Python (standard library): decode through FFmpeg to mono 16-bit PCM, build a two-band onset
envelope, estimate tempo by autocorrelation, then fit an exact grid  t = t0 + period * k.
The result is a *measurement*, not a guarantee: it reports confidence and the half/double-tempo
alternatives so a human (or the agent) can confirm before cutting.
"""
from __future__ import annotations

import array
import math
import os
import subprocess
import sys
from pathlib import Path

import vidlib as vl

SR = 11025          # analysis sample rate
HOP = SR // 100     # 10 ms frames
FRAME = 0.01


def decode_pcm(path: str | os.PathLike, max_seconds: float | None = None) -> array.array:
    cmd = [vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-i", vl.ffp(path), "-vn", "-ac", "1", "-ar", str(SR)]
    if max_seconds:
        cmd += ["-t", str(max_seconds)]
    cmd += ["-f", "s16le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or not r.stdout:
        raise vl.EngineError(f"could not decode audio: {path}\n{r.stderr.decode(errors='replace')[-400:]}")
    pcm = array.array("h")
    pcm.frombytes(r.stdout[: len(r.stdout) // 2 * 2])
    if sys.byteorder == "big":
        pcm.byteswap()
    return pcm


def _bands(pcm: array.array) -> tuple[list[float], list[float], list[float]]:
    """Per-10ms RMS of: low band (<150 Hz), the rest, and the full signal."""
    a = math.exp(-2 * math.pi * 150 / SR)
    lp = 0.0
    low, high, full = [], [], []
    sl = sh = sf = 0.0
    n = 0
    for x in pcm:
        lp = (1 - a) * x + a * lp
        h = x - lp
        sl += lp * lp
        sh += h * h
        sf += x * x
        n += 1
        if n == HOP:
            low.append(math.sqrt(sl / HOP))
            high.append(math.sqrt(sh / HOP))
            full.append(math.sqrt(sf / HOP))
            sl = sh = sf = 0.0
            n = 0
    return low, high, full


def _flux(e: list[float]) -> list[float]:
    f = [0.0] * len(e)
    for i in range(1, len(e)):
        d = e[i] - e[i - 1]
        f[i] = d if d > 0 else 0.0
    return f


def onset_strength(low: list[float], high: list[float]) -> list[float]:
    fl, fh = _flux(low), _flux(high)
    ml, mh = max(fl, default=0) or 1.0, max(fh, default=0) or 1.0
    return [0.6 * l / ml + 0.4 * h / mh for l, h in zip(fl, fh)]


def _autocorr_scores(on: list[float], bpm_lo: float, bpm_hi: float) -> list[tuple[float, int]]:
    lag_lo, lag_hi = int(60 / bpm_hi / FRAME), int(60 / bpm_lo / FRAME)
    n = len(on)
    out = []
    for lag in range(max(lag_lo, 5), min(lag_hi, n // 2) + 1):
        c = 0.0
        for i in range(n - lag):
            c += on[i] * on[i + lag]
        c /= (n - lag)
        bpm = 60 / (lag * FRAME)
        # gentle prior towards 100-140 BPM so that half/double tempo ties break sensibly
        prior = math.exp(-0.5 * (math.log2(bpm / 120.0) / 0.6) ** 2)
        out.append((c * (0.6 + 0.4 * prior), lag))
    out.sort(reverse=True)
    return out


def _grid_score(on: list[float], period: float, offset: float) -> float:
    n = len(on)
    s = 0.0
    k = 0
    cnt = 0
    while True:
        t = offset + k * period
        i = int(round(t / FRAME))
        if i >= n - 1:
            break
        if i >= 1:
            s += max(on[i - 1], on[i], on[i + 1])
            cnt += 1
        k += 1
    return s / max(cnt, 1)


def fit_grid(on: list[float], period0: float) -> tuple[float, float, float]:
    """Refine period and phase around an initial estimate. Returns (period, phase, score)."""
    best = (-1.0, period0, 0.0)
    steps = 41
    for si in range(steps):
        period = period0 * (1 + (si - steps // 2) * 0.0008)
        nph = max(1, int(period / 0.005))
        for pi in range(nph):
            ph = pi * 0.005
            sc = _grid_score(on, period, ph)
            if sc > best[0]:
                best = (sc, period, ph)
    return best[1], best[2], best[0]


def _smooth(x: list[float], w: int) -> list[float]:
    out, acc = [], 0.0
    for i, v in enumerate(x):
        acc += v
        if i >= w:
            acc -= x[i - w]
        out.append(acc / min(i + 1, w))
    return out


def find_sections(full: list[float], period: float, t0: float, duration: float) -> dict:
    """Find the biggest sustained loudness step (the 'drop') and snap it to the beat grid.

    For every 0.1 s position the mean energy of the next 2 s is compared with the previous 2 s;
    the position with the largest ratio is the step. Needs a ratio >= 1.25 to count.
    """
    blk = 10                                    # 10 frames of 10 ms = 0.1 s
    e = [sum(full[i:i + blk]) / blk for i in range(0, len(full) - blk + 1, blk)]
    w = 20                                      # 2 s
    best, best_i = 1.0, None
    peak = max(e, default=0.0)
    start = next((i for i, v in enumerate(e) if v > 0.1 * peak), 0)      # where the music actually begins
    for i in range(start + w, len(e) - w):                               # never mistake the song start for a drop
        before = sum(e[i - w:i]) / w
        after = sum(e[i:i + w]) / w
        if before > 0 and after / before > best:
            best, best_i = after / before, i
    result = {"drop": None, "drop_beat": None, "drop_ratio": round(best, 2)}
    if best_i is not None and best >= 1.25:
        t = best_i * 0.1
        # the step starts rising at t; the 'drop' is the first beat where the next second is (almost) at full level
        def level(a: float) -> float:
            i = int(a * 10)
            return sum(e[i:i + 10]) / max(1, len(e[i:i + 10]))
        ceil = max(level(t + d * 0.1) for d in range(0, 30))
        k = max(0, math.ceil((t - t0) / period))
        while t0 + k * period < t + 3 and level(t0 + k * period) < 0.9 * ceil:
            k += 1
        result.update(drop=round(t0 + k * period, 3), drop_beat=int(k))
    return result



# --------------------------------------------------------------------------- phase 2: validation

def peak_times(on: list[float], min_gap: float = 0.1, rel: float = 0.3) -> list[float]:
    """Times (s) of local maxima of the onset curve above rel * max, at least min_gap apart."""
    top = max(on, default=0.0)
    if top <= 0:
        return []
    gap = int(min_gap / FRAME)
    peaks: list[int] = []
    for i in range(2, len(on) - 2):
        if on[i] >= rel * top and on[i] == max(on[max(0, i - 5):i + 6]):
            if peaks and i - peaks[-1] < gap:
                if on[i] > on[peaks[-1]]:
                    peaks[-1] = i
                continue
            peaks.append(i)
    return [i * FRAME for i in peaks]


def ioi_tempo(peaks: list[float]) -> float | None:
    """Independent tempo estimate (BPM): histogram of inter-onset intervals folded into one octave (0.35-0.9 s)."""
    if len(peaks) < 6:
        return None
    hist: dict[int, float] = {}
    for gap_n in (1, 2, 3):
        for a, b in zip(peaks, peaks[gap_n:]):
            d = (b - a) / gap_n
            while d < 0.35:
                d *= 2
            while d > 0.9:
                d /= 2
            if 0.35 <= d <= 0.9:
                k = int(round(d * 100))
                for dk in (-1, 0, 1):                       # smooth: neighbours share weight
                    hist[k + dk] = hist.get(k + dk, 0.0) + (1.0 if dk == 0 else 0.5)
    if not hist:
        return None
    best = max(hist, key=lambda k: hist[k])
    return 60.0 / (best / 100.0)


def relation(bpm_a: float, bpm_b: float, tol: float = 0.04) -> str:
    """How estimate B relates to estimate A: same, double (B = 2A), half (B = A/2) or different."""
    for name, mult in (("same", 1.0), ("double", 2.0), ("half", 0.5)):
        target = bpm_a * mult
        if abs(bpm_b - target) / target <= tol:
            return name
    return "different"


def stability(on: list[float], period: float, phase: float) -> dict:
    """Fit the grid on each half of the track around the global period: tempo drift and median onset error."""
    mid = len(on) // 2
    half = {}
    for name, seg, off in (("first", on[:mid], 0.0), ("second", on[mid:], mid * FRAME)):
        best = (-1.0, period)
        for si in range(-32, 33):                          # +-8% around the global period, 0.25% steps
            p = period * (1 + si * 0.0025)
            for pi in range(max(1, int(p / 0.01))):
                sc = _grid_score(seg, p, pi * 0.01)
                if sc > best[0]:
                    best = (sc, p)
        half[name] = best[1]
    drift = abs(half["first"] - half["second"]) / period
    pk = peak_times(on, rel=0.5)
    sub = period / 4                                       # kicks, snares and hats all land on the 16th-note grid
    res = sorted(abs(t - (phase + round((t - phase) / sub) * sub)) for t in pk)
    resid = res[len(res) // 2] if res else 0.0             # median: a few fills do not matter
    return {"period_first_half": round(half["first"], 4), "period_second_half": round(half["second"], 4),
            "drift_pct": round(100 * drift, 2), "median_onset_error_ms": round(resid * 1000, 1)}


def bar_phase(low: list[float], beats: list[float], beats_per_bar: int = 4) -> tuple[int, float]:
    """Which beat of the bar is the 'one': the phase whose beats carry the most low-band (kick) energy.
    Returns (phase 0..beats_per_bar-1 as an index into `beats`, confidence 0-1)."""
    flux = _flux(low)
    sums = [0.0] * beats_per_bar
    counts = [0] * beats_per_bar
    for k, t in enumerate(beats):
        i = int(round(t / FRAME))
        if 1 <= i < len(flux) - 2:
            sums[k % beats_per_bar] += max(flux[i - 1:i + 3])
            counts[k % beats_per_bar] += 1
    means = [s / c if c else 0.0 for s, c in zip(sums, counts)]
    total = sum(means)
    if total <= 0:
        return 0, 0.0
    best = max(range(beats_per_bar), key=lambda p: means[p])
    others = [m for p, m in enumerate(means) if p != best]
    conf = (means[best] - (sum(others) / len(others))) / means[best] if (others and means[best] > 0) else 0.0
    return best, round(max(0.0, min(1.0, conf * 2)), 2)


def validate(on: list[float], low: list[float], period: float, phase: float, beats: list[float],
             beats_per_bar: int = 4) -> dict:
    """Second opinion on the grid. Everything here is advice for a human: warnings, never hard failures."""
    notes: list[str] = []
    pk = peak_times(on)
    bpm = 60.0 / period
    ioi = ioi_tempo(pk)
    rel = relation(bpm, ioi) if ioi else None
    if ioi is None:
        notes.append("too few onsets for a second tempo estimate")
    elif rel == "different":
        notes.append(f"second estimate says {ioi:.1f} BPM, grid says {bpm:.1f}: confirm by ear before cutting")
    elif rel in ("double", "half"):
        notes.append(f"second estimate ({ioi:.1f} BPM) is the {rel} of the grid tempo ({bpm:.1f}): check which pulse you want to cut on")
    st = stability(on, period, phase)
    if st["drift_pct"] > 0.8:
        notes.append(f"tempo drifts {st['drift_pct']}% between the first and second half: late cuts may slip off the beat")
    if st["median_onset_error_ms"] > 35:
        notes.append(f"strong onsets sit {st['median_onset_error_ms']} ms from the 16th-note grid (median): the track may be swung or live-played")
    bp, bc = bar_phase(low, beats, beats_per_bar)
    return {"second_tempo_bpm": round(ioi, 1) if ioi else None, "second_relation": rel, **st,
            "bar_phase": bp, "bar_confidence": bc, "notes": notes,
            "ok": not notes}


def beat_strengths(onset: list[float], beats: list[float]) -> list[float]:
    """Onset strength at each beat (0-1), so the planner can hold calm beats and cut fast on strong ones (pacing)."""
    out = []
    for t in beats:
        i = int(round(t / FRAME))
        window = onset[max(0, i - 1):i + 2] or [0.0]
        out.append(max(window))
    mx = max(out) or 1.0
    return [round(v / mx, 3) for v in out]


def detect_sections(full: list[float], beats: list[float], period: float) -> list[dict]:
    """Segment the track into musical sections (intro / build / drop / outro) by sustained loudness level.

    For each beat, take the mean full-band energy over the following beat; smooth it; start a new section when the
    level departs from the current section's running mean by a clear margin for at least a few beats. Sections are then
    labelled by energy relative to the loudest and by position (first calm = intro, last calm = outro, loudest = drop).
    Editors put the biggest picture change on the biggest musical boundary, so story beats align to these.
    """
    if len(beats) < 4:
        return []
    step = int(round(period / FRAME)) or 1
    e = []
    for t in beats:
        i = int(round(t / FRAME))
        seg = full[i:i + step] or [0.0]
        e.append(sum(seg) / len(seg))
    mx = max(e) or 1.0
    e = [v / mx for v in e]
    sm = _smooth(e, 3)
    bounds = [0]
    mean, cnt = sm[0], 1
    for k in range(1, len(sm)):
        if abs(sm[k] - mean) > 0.18 and (k - bounds[-1]) >= 3:
            bounds.append(k)
            mean, cnt = sm[k], 1
        else:
            mean = (mean * cnt + sm[k]) / (cnt + 1)
            cnt += 1
    bounds.append(len(sm))
    secs = []
    for a, b in zip(bounds, bounds[1:]):
        lvl = sum(sm[a:b]) / max(b - a, 1)
        secs.append({"start_beat": a, "end_beat": b, "start": round(beats[a], 3),
                     "end": round(beats[min(b, len(beats)) - 1], 3), "energy": round(lvl, 3), "label": "build"})
    hi = max((s["energy"] for s in secs), default=0.0) or 1.0
    for idx, s in enumerate(secs):
        if idx == 0 and s["energy"] <= 0.6 * hi:
            s["label"] = "intro"
        elif idx == len(secs) - 1 and s["energy"] <= 0.7 * hi:
            s["label"] = "outro"
        elif s["energy"] >= 0.85 * hi:
            s["label"] = "drop"
    return secs


def refine_onset(pcm: array.array, t: float, window: float = 0.12) -> float:
    """Refine an onset time to the steepest short-term energy rise within +-`window` seconds (near-sample-accurate).

    The 10 ms onset grid is fine for choosing beats, but a hit (the drop, a flash) sits better on the exact transient.
    This scans ~5 ms energy frames at a ~1 ms hop around `t` and returns the time of the largest positive energy step.
    """
    i0, i1 = max(0, int((t - window) * SR)), min(len(pcm), int((t + window) * SR))
    if i1 - i0 < 64:
        return round(t, 4)
    hop = max(1, SR // 1000)          # ~1 ms
    fl = max(hop, SR // 200)          # ~5 ms frame
    energies, idxs = [], []
    k = i0
    while k + fl <= i1:
        seg = pcm[k:k + fl]
        energies.append(sum(x * x for x in seg))
        idxs.append(k)
        k += hop
    best = (-1.0, t)
    for j in range(1, len(energies)):
        d = energies[j] - energies[j - 1]
        if d > best[0]:
            best = (d, idxs[j] / SR)
    return round(best[1], 4)


def tempo_map(onset: list[float], period0: float, segments: int = 8) -> list[dict]:
    """Tempo over time: fit the local period in each of `segments` windows around the global period (+-10%).

    For a steady track every entry equals the global tempo; for a track that speeds up or slows down the curve follows
    it, so `beats_following_tempo` can place a grid that does not drift at the end."""
    n = len(onset)
    if n < segments * 4:
        return []
    seglen = max(1, n // segments)
    out = []
    for s in range(0, n, seglen):
        seg = onset[s:s + seglen]
        if len(seg) < seglen // 2:
            break
        best = (-1.0, period0)
        for si in range(-20, 21):
            p = period0 * (1 + si * 0.005)
            for pi in range(max(1, int(p / 0.02))):
                sc = _grid_score(seg, p, pi * 0.02)
                if sc > best[0]:
                    best = (sc, p)
        out.append({"t": round(s * FRAME, 2), "bpm": round(60.0 / best[1], 1)})
    return out


def beats_following_tempo(tmap: list[dict], t0: float, duration: float) -> list[float]:
    """Beat times from `t0` that follow the tempo map (variable spacing), linearly interpolating the instantaneous BPM."""
    if not tmap:
        return []

    def bpm_at(t: float) -> float:
        if t <= tmap[0]["t"]:
            return tmap[0]["bpm"]
        if t >= tmap[-1]["t"]:
            return tmap[-1]["bpm"]
        for i in range(1, len(tmap)):
            if t < tmap[i]["t"]:
                a, b = tmap[i - 1], tmap[i]
                f = (t - a["t"]) / max(b["t"] - a["t"], 1e-6)
                return a["bpm"] + f * (b["bpm"] - a["bpm"])
        return tmap[-1]["bpm"]

    times = [round(t0, 3)]
    t = t0
    guard = 0
    while t < duration - 0.05 and guard < 100000:
        t += 60.0 / max(bpm_at(t), 1e-6)
        if t < duration:
            times.append(round(t, 3))
        guard += 1
    return times


def analyse(path: str | os.PathLike, max_seconds: float | None = None, bpm_range=(70.0, 180.0),
            bpm: float | None = None, beats_per_bar: int = 4, first_beat: float | None = None) -> dict:
    pcm = decode_pcm(path, max_seconds)
    duration = len(pcm) / SR
    if duration < 3:
        raise vl.EngineError("audio shorter than 3 seconds: cannot estimate a beat grid")
    if beats_per_bar < 1:
        raise vl.EngineError("beats-per-bar must be >= 1")
    low, high, full = _bands(pcm)
    on = onset_strength(low, high)
    forced_note = None
    if bpm is not None:
        if not 20.0 <= float(bpm) <= 400.0:
            raise vl.EngineError("forced bpm must be between 20 and 400")
        period = 60.0 / float(bpm)
        best = (-1.0, 0.0)                                   # best phase for this fixed tempo
        for pi in range(max(1, int(period / 0.005))):
            ph = pi * 0.005
            sc = _grid_score(on, period, ph)
            if sc > best[0]:
                best = (sc, ph)
        grid, phase = best[0], best[1]
        forced_note = f"tempo forced to {float(bpm):.1f} BPM"
    else:
        scores = _autocorr_scores(on, *bpm_range)
        if not scores:
            raise vl.EngineError("no tempo candidates found (audio too short or silent)")
        top_lag = scores[0][1]
        period, phase, grid = fit_grid(on, top_lag * FRAME)
    # alternatives: half and double tempo, scored on the same onset curve
    alts = []
    for mult in (0.5, 2.0):
        p2 = period * mult
        bpm2 = 60 / p2
        if bpm_range[0] <= bpm2 <= bpm_range[1] + 40:
            best = max(_grid_score(on, p2, (phase + i * period / 4) % p2) for i in range(4))
            alts.append({"bpm": round(bpm2, 1), "period": round(p2, 4), "score": round(best, 3)})
    mean_on = sum(on) / len(on) or 1e-9
    confidence = min(1.0, max(0.0, (grid / mean_on - 1.0) / 4.0))
    if bpm is not None:
        confidence = max(confidence, 0.6)                    # the user gave the tempo
    if first_beat is not None:
        if not 0.0 <= float(first_beat) < duration:
            raise vl.EngineError("first-beat must be between 0 and the track length")
        t0 = round(float(first_beat), 3)
        phase = t0 % period
    else:
        # first strong beat: first grid beat after the onset curve first exceeds 25% of its robust max
        rob = sorted(on)[int(len(on) * 0.97)] or 1.0
        start_i = next((i for i, v in enumerate(on) if v > 0.25 * rob), 0)
        k0 = max(0, math.ceil((start_i * FRAME - phase - 0.5 * period) / period))
        t0 = phase + k0 * period
    sections = find_sections(full, period, t0, duration)
    nbeats = int((duration - t0) / period) + 1
    beat_times = [round(t0 + k * period, 3) for k in range(nbeats)]
    check = validate(on, low, period, phase, beat_times, beats_per_bar)
    bp = check["bar_phase"]
    if forced_note:
        check["notes"].insert(0, forced_note)
    strengths = beat_strengths(on, beat_times)
    secs = detect_sections(full, beat_times, period)
    tmap = tempo_map(on, period)
    beats_ref = beats_following_tempo(tmap, t0, duration)
    drop_refined = refine_onset(pcm, sections["drop"]) if sections["drop"] is not None else None
    return {
        "file": vl.ffp(path), "duration": round(duration, 3), "bpm": round(60 / period, 2),
        "period": round(period, 4), "phase": round(phase, 3), "t0": round(t0, 3),
        "confidence": round(confidence, 2), "alternatives": alts, "beats_per_bar": beats_per_bar,
        "drop": sections["drop"], "drop_beat": sections["drop_beat"], "drop_ratio": sections["drop_ratio"],
        "drop_refined": drop_refined, "tempo_map": tmap,
        "beats": beat_times, "beats_refined": beats_ref, "beat_strength": strengths, "sections": secs,
        "bar_phase": bp, "downbeats": [t for k, t in enumerate(beat_times) if k % beats_per_bar == bp],
        "validation": check,
        "note": "Grid fitted to onsets; verify with `plan` output or by ear. If BPM looks doubled/halved, "
                "pass the alternative period to `plan --period` (or force it with `beats --bpm`).",
    }
