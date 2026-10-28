"""Tracks A + E: editorial measurement (the 'why' of a cut) and auto-reframe saliency.

Pure Python over small grayscale frames that FFmpeg decodes (scale + format=gray + fps; no extra filters, no ML).
The engine only MEASURES here - shot size, camera motion direction, a saliency centroid, and continuity signals
between shots. The agent (and the editorial lint / QA gate) turns those numbers into editorial judgement.

Everything is deterministic and cheap: a 48x27 gray frame is 1296 bytes; a full-frame block match over +-4 px is a
few hundred thousand integer operations. Results are stored as plain fields on the score entries, so they are cached
and reviewable, never a hidden decision.
"""
from __future__ import annotations

import subprocess

import vid_sheets
import vidlib as vl

GW, GH = 48, 27                       # analysis frame size (16:9-ish)
SHOT_SIZES = ("wide", "medium", "close")


def gray_frames(clip: str, start: float, dur: float, n: int = 6, w: int = GW, h: int = GH,
                hdr: bool = False) -> list[bytes]:
    """`n` evenly spaced grayscale frames of a window, each `w*h` bytes. [] if nothing decodes."""
    tm = vid_sheets.TONEMAP_HLG if hdr else ""
    fps = max(1.0, n / max(dur, 1e-3))
    cmd = [vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-ss", f"{max(0.0, start):.2f}", "-t", f"{max(0.05, dur):.2f}",
           "-i", vl.ffp(clip), "-an", "-vf", f"{tm}scale={w}:{h},format=gray,fps={fps:.3f}",
           "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    r = subprocess.run(cmd, capture_output=True)
    data = r.stdout or b""
    fs = w * h
    return [data[i * fs:(i + 1) * fs] for i in range(len(data) // fs) if len(data[i * fs:(i + 1) * fs]) == fs]


def _grad(frame: bytes, w: int, h: int) -> list[int]:
    """Per-pixel gradient magnitude |dx| + |dy| (Sobel-lite)."""
    g = [0] * (w * h)
    for y in range(h):
        row = y * w
        for x in range(w):
            i = row + x
            gx = frame[i + 1] - frame[i] if x < w - 1 else 0
            gy = frame[i + w] - frame[i] if y < h - 1 else 0
            g[i] = (gx if gx >= 0 else -gx) + (gy if gy >= 0 else -gy)
    return g


def shot_size(frame: bytes, w: int = GW, h: int = GH) -> str:
    """Classify framing from where the detail sits: detail concentrated in the centre reads as a close-up, detail
    spread evenly (or very little detail) reads as a wide shot."""
    g = _grad(frame, w, h)
    area = w * h
    full = sum(g) / area
    cx0, cx1, cy0, cy1 = w // 3, 2 * w // 3, h // 3, 2 * h // 3
    c = ca = 0
    for y in range(cy0, cy1):
        for x in range(cx0, cx1):
            c += g[y * w + x]
            ca += 1
    center = c / max(ca, 1)
    ratio = center / max(full, 1e-6)
    if full < 4.0:
        return "wide"
    if ratio > 1.35:
        return "close"
    if ratio < 0.9:
        return "wide"
    return "medium"


def _sad(a: bytes, b: bytes, w: int, h: int, dx: int, dy: int) -> float:
    s = cnt = 0
    for y in range(h):
        yy = y + dy
        if yy < 0 or yy >= h:
            continue
        ra, rb = y * w, yy * w
        for x in range(w):
            xx = x + dx
            if xx < 0 or xx >= w:
                continue
            d = a[ra + x] - b[rb + xx]
            s += d if d >= 0 else -d
            cnt += 1
    return s / max(cnt, 1)


def motion_between(a: bytes, b: bytes, w: int, h: int, rng: int = 4) -> tuple[int, int]:
    """Global shift (dx, dy) in pixels that best aligns frame b onto frame a (camera motion direction).

    A tiny penalty on the shift size breaks ties toward no motion, so a flat/low-texture frame (every shift scores the
    same) reads as still rather than as a spurious pan."""
    best = (1e18, 0, 0)
    for dy in range(-rng, rng + 1):
        for dx in range(-rng, rng + 1):
            v = _sad(a, b, w, h, dx, dy) + 0.01 * (abs(dx) + abs(dy))
            if v < best[0]:
                best = (v, dx, dy)
    return best[1], best[2]


def _texture(frame: bytes, w: int, h: int) -> float:
    g = _grad(frame, w, h)
    return sum(g) / (w * h)


def motion_vector(frames: list[bytes], w: int = GW, h: int = GH) -> tuple[float, float, float]:
    """Average camera motion over the window: (dx, dy, magnitude) in analysis pixels. A textureless window (nothing to
    track) reports no motion."""
    if len(frames) < 2 or _texture(frames[0], w, h) < 2.0:
        return 0.0, 0.0, 0.0
    sx = sy = 0.0
    pairs = 0
    for k in range(1, len(frames)):
        dx, dy = motion_between(frames[k - 1], frames[k], w, h)
        sx += dx
        sy += dy
        pairs += 1
    dx, dy = sx / pairs, sy / pairs
    return round(dx, 3), round(dy, 3), round((dx * dx + dy * dy) ** 0.5, 3)


def saliency_centroid(frames: list[bytes], w: int = GW, h: int = GH) -> tuple[float, float]:
    """Where the action/detail sits, (x, y) in 0..1: temporal change plus edge energy, weighted by position.
    Used for subject-aware auto-reframe (keep this point in frame when changing aspect). A near-empty frame centres."""
    if not frames:
        return 0.5, 0.5
    acc = [0.0] * (w * h)
    for k in range(1, len(frames)):
        a, b = frames[k - 1], frames[k]
        for i in range(w * h):
            d = a[i] - b[i]
            acc[i] += d if d >= 0 else -d
    for i, gv in enumerate(_grad(frames[-1], w, h)):
        acc[i] += 0.5 * gv
    tot = sum(acc)
    if tot < 1.0 * w * h:              # almost no detail or motion: nothing to frame on, keep centred
        return 0.5, 0.5
    sx = sum((i % w) * acc[i] for i in range(w * h)) / tot
    sy = sum((i // w) * acc[i] for i in range(w * h)) / tot
    return round(sx / max(w - 1, 1), 3), round(sy / max(h - 1, 1), 3)


def measure_shot(clip: str, start: float, dur: float, hdr: bool = False) -> dict:
    """All editorial measurements for one window of a clip."""
    frames = gray_frames(clip, start, min(max(dur, 0.3), 2.0), hdr=hdr)
    if not frames:
        return {}
    mid = frames[len(frames) // 2]
    dx, dy, mag = motion_vector(frames)
    cx, cy = saliency_centroid(frames)
    return {"shot_size": shot_size(mid), "motion_dx": dx, "motion_dy": dy, "motion_mag": mag,
            "focus_x": cx, "focus_y": cy}


# --------------------------------------------------------------------------- editorial lint (Track A)

PACING_FLAT_CV = 0.08                 # coefficient of variation below which pacing is "monotone"
SCREEN_DIR_MIN = 1.2                  # analysis-pixels of horizontal motion to count as a clear direction


def _issue(level: str, code: str, msg: str) -> dict:
    return {"level": level, "code": code, "msg": msg}


def editorial_issues(edl: dict, feats: list[dict], bounds: list[int], trans: dict[int, str]) -> list[dict]:
    """Editorial warnings from the measured per-shot features (Walter Murch's priorities in miniature).

    `feats[j]` holds the measurements for shot j (shot_size, motion_dx, hash, ...) or {} if unmeasured. `trans` maps a
    boundary index to its transition name (a hard cut is a boundary not in `trans`).
    """
    out: list[dict] = []
    n = len(edl.get("shots") or [])
    sizes = [(feats[j].get("shot_size") if j < len(feats) and feats[j] else None) for j in range(n)]
    # three or more shots of the same size in a row: coverage lacks variety
    run = 1
    for j in range(1, n):
        if sizes[j] is not None and sizes[j] == sizes[j - 1]:
            run += 1
            if run >= 3:
                out.append(_issue("warn", "shot-size-repeat",
                                   f"shots {j - 2}-{j} are all '{sizes[j]}' shots; vary shot size for coverage"))
        else:
            run = 1
    # jump cut: a hard cut between two look-alike shots of the same size
    for j in range(1, n):
        if j in trans:
            continue
        a, b = (feats[j - 1] if j - 1 < len(feats) else {}), (feats[j] if j < len(feats) else {})
        if not a or not b:
            continue
        if a.get("shot_size") and a.get("shot_size") == b.get("shot_size") and a.get("hash") is not None and b.get("hash") is not None:
            if bin(a["hash"] ^ b["hash"]).count("1") <= 10:
                out.append(_issue("warn", "jump-cut",
                                  f"shot {j - 1}->{j}: a hard cut between two similar '{a['shot_size']}' shots looks like a jump cut"))
    # screen-direction flip across a hard cut (crossing the line, approximated by camera motion)
    for j in range(1, n):
        if j in trans:
            continue
        a, b = (feats[j - 1] if j - 1 < len(feats) else {}), (feats[j] if j < len(feats) else {})
        da, db = a.get("motion_dx"), b.get("motion_dx")
        if da is None or db is None:
            continue
        if abs(da) >= SCREEN_DIR_MIN and abs(db) >= SCREEN_DIR_MIN and (da > 0) != (db > 0):
            out.append(_issue("info", "screen-direction",
                              f"shot {j - 1}->{j}: camera motion reverses direction across the cut (check the 180-degree line)"))
    # a directional slide/wipe should continue the content's movement, not fight it
    import vid_cutlogic as _cl
    for j, name in sorted(trans.items()):
        want = _cl.TRANSITION_DIRECTION.get(name)
        a = feats[j - 1] if 0 <= j - 1 < len(feats) else {}
        if want in ("left", "right") and a and a.get("motion_dx") is not None and abs(a["motion_dx"]) >= SCREEN_DIR_MIN:
            if (a["motion_dx"] < 0) != (want == "left"):
                out.append(_issue("warn", "direction-mismatch",
                                  f"cut {j}: '{name}' moves {want} but the outgoing shot's content moves "
                                  f"{'left' if a['motion_dx'] < 0 else 'right'}; use the mirror transition or a hard cut"))
    # pacing: flat shot-length rhythm carries no emotion
    lens = [bounds[j + 1] - bounds[j] for j in range(n)]
    if n >= 5:
        mean = sum(lens) / n
        if mean > 0:
            var = sum((x - mean) ** 2 for x in lens) / n
            cv = (var ** 0.5) / mean
            if cv < PACING_FLAT_CV:
                out.append(_issue("info", "pacing-flat",
                                  f"shot lengths barely vary (variation {cv:.0%}); vary the pace to carry the story's energy"))
    return out


def summarize_editorial(edl: dict, feats: list[dict], bounds: list[int], trans: dict[int, str]) -> dict:
    """Editorial numbers for the report: shot-size mix, cut-on-motion share, variety."""
    n = len(edl.get("shots") or [])
    sizes = [f.get("shot_size") for f in feats[:n] if f]
    mix: dict[str, int] = {}
    for s in sizes:
        if s:
            mix[s] = mix.get(s, 0) + 1
    hard_cuts = [j for j in range(1, len(bounds) - 1) if j not in trans]
    on_motion = 0
    for j in hard_cuts:
        f = feats[j] if j < len(feats) else {}
        if f and f.get("motion_mag", 0) >= 1.0:
            on_motion += 1
    return {"shot_size_mix": mix, "distinct_shot_sizes": len(mix),
            "cut_on_motion_share": round(on_motion / max(len(hard_cuts), 1), 2)}
