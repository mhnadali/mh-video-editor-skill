"""Tracks A + E: editorial measurement (the 'why' of a cut) and auto-reframe saliency.

Pure Python over small grayscale frames that FFmpeg decodes (scale + format=gray + fps; no extra filters, no ML).
The engine only MEASURES here - shot size, camera motion direction, a saliency centroid, and continuity signals
between shots. The agent (and the editorial lint / QA gate) turns those numbers into editorial judgement.

Everything is deterministic and cheap: a 48x27 gray frame is 1296 bytes; a full-frame block match over +-4 px is a
few hundred thousand integer operations. Results are stored as plain fields on the score entries, so they are cached
and reviewable, never a hidden decision.
"""
from __future__ import annotations

import math
import subprocess
from typing import Optional

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


def rgb_frames(clip: str, start: float, dur: float, n: int = 3, w: int = 16, h: int = 9, hdr: bool = False) -> list[bytes]:
    """`n` tiny RGB frames (w*h*3 bytes each) of a window, for colour measurements. [] if nothing decodes."""
    tm = vid_sheets.TONEMAP_HLG if hdr else ""
    fps = max(1.0, n / max(dur, 1e-3))
    cmd = [vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-ss", f"{max(0.0, start):.2f}", "-t", f"{max(0.05, dur):.2f}",
           "-i", vl.ffp(clip), "-an", "-vf", f"{tm}scale={w}:{h},format=rgb24,fps={fps:.3f}",
           "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    r = subprocess.run(cmd, capture_output=True)
    data = r.stdout or b""
    fs = w * h * 3
    return [data[i * fs:(i + 1) * fs] for i in range(len(data) // fs) if len(data[i * fs:(i + 1) * fs]) == fs]


def _hue_angle(r: float, g: float, b: float, v: float, c: float) -> float:
    """Hue as an angle in radians (0 = red) from RGB in 0..1; grey pixels (no chroma) have weight 0 anyway."""
    if c <= 0:
        return 0.0
    if v == r:
        h = ((g - b) / c) % 6
    elif v == g:
        h = (b - r) / c + 2
    else:
        h = (r - g) / c + 4
    return math.pi * h / 3.0


def dominant_hue(frames: list[bytes]) -> tuple[Optional[float], float]:
    """(hue in 0..1 of the most colourful pixels, mean saturation 0..1). Hue is None for a grey picture.

    Pixels are weighted by their saturation, so a grey road under a blue sky reads as blue, not as nothing."""
    sx = sy = wsum = sat_total = 0.0
    count = 0
    for fr in frames:
        for i in range(0, len(fr) - 2, 3):
            r, g, b = fr[i] / 255.0, fr[i + 1] / 255.0, fr[i + 2] / 255.0
            v = max(r, g, b)
            c = v - min(r, g, b)
            s = c / v if v > 0 else 0.0
            wgt = s * v
            ang = _hue_angle(r, g, b, v, c)
            sx += wgt * math.cos(ang)
            sy += wgt * math.sin(ang)
            wsum += wgt
            sat_total += s
            count += 1
    if not count:
        return None, 0.0
    mean_sat = sat_total / count
    if wsum < 1e-6 or (sx * sx + sy * sy) ** 0.5 / wsum < 0.2 or mean_sat < 0.08:
        return None, round(mean_sat, 3)
    return round((math.atan2(sy, sx) / (2 * math.pi)) % 1.0, 3), round(mean_sat, 3)


def hue_distance(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """Circular distance between two hues, 0 (same) to 0.5 (opposite); None if either picture is grey."""
    if a is None or b is None:
        return None
    d = abs(a - b) % 1.0
    return min(d, 1.0 - d)


def flux(frames: list[bytes]) -> float:
    """Largest mean absolute change between consecutive analysis frames (0-255); informational. Use `internal_cut` to
    decide whether the clip cuts or flashes."""
    worst = 0.0
    for a, b in zip(frames, frames[1:]):
        if len(a) != len(b) or not a:
            continue
        worst = max(worst, sum(abs(x - y) for x, y in zip(a, b)) / len(a))
    return round(worst, 2)


def jitter(frames: list[bytes], w: int = GW, h: int = GH, rng: int = 2) -> float:
    """Camera shake: how much the global shift between consecutive frames wobbles around its own average, in analysis
    pixels (48 wide) per frame step. A steady pan or a drive has a constant shift and scores about 0; a hand-held walk or a
    bumpy ride scores high. A textureless window (nothing to track) reads 0."""
    if len(frames) < 3 or any(len(f) != w * h for f in frames) or _texture(frames[0], w, h) < 2.0:
        return 0.0
    dxs, dys = [], []
    for a, b in zip(frames, frames[1:]):
        dx, dy = motion_between(a, b, w, h, rng=rng)
        dxs.append(dx)
        dys.append(dy)
    mx, my = sum(dxs) / len(dxs), sum(dys) / len(dys)
    return round(sum(abs(x - mx) + abs(y - my) for x, y in zip(dxs, dys)) / len(dxs), 3)


def texture_mean(frames: list[bytes], w: int = GW, h: int = GH) -> float:
    """Average gradient strength of the frames (0 = flat). A covered lens or a wall reads near 0."""
    vals = [_texture(f, w, h) for f in frames if len(f) == w * h]
    return round(sum(vals) / len(vals), 3) if vals else 0.0


def colour_signature(rgb: list[bytes], w: int = 16, h: int = 9) -> list[int]:
    """Mean RGB of the four quarters of the picture, averaged over the frames: 12 numbers that say where the colours are."""
    cells = [[0, 0, 0, 0] for _ in range(4)]
    for fr in rgb:
        if len(fr) != w * h * 3:
            continue
        for y in range(h):
            for x in range(w):
                q = (0 if y < h // 2 else 2) + (0 if x < w // 2 else 1)
                i = (y * w + x) * 3
                c = cells[q]
                c[0] += fr[i]
                c[1] += fr[i + 1]
                c[2] += fr[i + 2]
                c[3] += 1
    out: list[int] = []
    for c in cells:
        n = max(c[3], 1)
        out += [int(c[0] / n), int(c[1] / n), int(c[2] / n)]
    return out


def colour_distance(a: list[int], b: list[int]) -> float:
    """Mean absolute difference of two colour signatures (0-255)."""
    if not a or not b or len(a) != len(b):
        return 255.0
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def scene_shares(rgb: list[bytes], w: int = 16, h: int = 9) -> dict:
    """Rough content of the picture without any model: the share of sky, sand, vegetation and dark pixels."""
    sky = sand = green = dark = total = 0
    for fr in rgb:
        if len(fr) != w * h * 3:
            continue
        for y in range(h):
            for x in range(w):
                i = (y * w + x) * 3
                r, g, b = fr[i], fr[i + 1], fr[i + 2]
                total += 1
                lum = (r + g + b) / 3.0
                if lum < 45:
                    dark += 1
                elif b > r + 15 and b >= g and lum > 110 and y < h * 0.7:
                    sky += 1
                elif r > g > b and r - b > 25 and 110 < r < 235:
                    sand += 1
                elif g > r + 10 and g > b + 10:
                    green += 1
    t = max(total, 1)
    return {"sky": round(sky / t, 2), "sand": round(sand / t, 2), "green": round(green / t, 2), "dark": round(dark / t, 2)}


def scene_label(shares: dict) -> str:
    if shares.get("dark", 0) > 0.6:
        return "dark"
    if shares.get("sky", 0) > 0.40:
        return "sky"
    if shares.get("sand", 0) > 0.35:
        return "desert"
    if shares.get("green", 0) > 0.30:
        return "green"
    return "other"


def internal_cut(frames: list[bytes]) -> bool:
    """True if one step between consecutive analysis frames is far larger than the others: the clip itself cuts or
    flashes there. A fast pan changes every step by a similar amount, so it does not trigger this."""
    diffs = []
    for a, b in zip(frames, frames[1:]):
        if len(a) == len(b) and a:
            diffs.append(sum(abs(x - y) for x, y in zip(a, b)) / len(a))
    if len(diffs) < 3:
        return bool(diffs) and max(diffs) >= 90.0              # too few steps to compare: only an extreme jump counts
    worst = max(diffs)
    rest = sorted(diffs)
    rest.remove(worst)
    median = rest[len(rest) // 2]
    return worst >= 40.0 and worst >= 2.5 * max(median, 6.0)


def local_motion(frames: list[bytes], w: int = GW, h: int = GH) -> float:
    """Share (0-1) of the picture that still changes after the camera's own motion is cancelled: a moving subject
    (rider, wave, person) over a steady or panning background. Near 0 = only the camera moves (or nothing does)."""
    if len(frames) < 2 or _texture(frames[0], w, h) < 2.0:
        return 0.0
    shares = []
    for k in range(1, len(frames)):
        dx, dy = motion_between(frames[k - 1], frames[k], w, h)
        a, b = frames[k - 1], frames[k]
        changed = total = 0
        for y in range(h):
            yy = y + dy
            if yy < 0 or yy >= h:
                continue
            for x in range(w):
                xx = x + dx
                if xx < 0 or xx >= w:
                    continue
                total += 1
                if abs(a[y * w + x] - b[yy * w + xx]) > 28:
                    changed += 1
        shares.append(changed / max(total, 1))
    return round(sum(shares) / len(shares), 3)


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


WIDE_TEXTURE = 25.0            # mean gradient of the 48x27 frame below which a shot reads as wide (sky, haze, far landscape)


def shot_size(frame: bytes, w: int = GW, h: int = GH) -> str:
    """Wide or medium from how much fine detail the picture has: a landscape of sky and distant sand has little, a nearer
    subject (a car, a person, ground, food) has a lot. Measured against 174 shots labelled by eye on a real trip, this agrees
    63 % of the time (the majority label alone: 42 %). The earlier rule (detail concentrated in the centre means close-up)
    agreed 21 % of the time: on desert footage the centre of a landscape is as busy as its edges and a sand close-up is not.
    A close-up cannot be told from the pixels with confidence; `close` comes from a large face (vid_story.clip_view) or
    from the agent's notes."""
    g = _grad(frame, w, h)
    return "wide" if sum(g) / (w * h) < WIDE_TEXTURE else "medium"


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
    """All editorial measurements for one window of a clip (decodes the window itself)."""
    frames = gray_frames(clip, start, min(max(dur, 0.3), 2.0), hdr=hdr)
    if not frames:
        return {}
    try:
        rgb = rgb_frames(clip, start, min(max(dur, 0.3), 2.0), hdr=hdr)
    except OSError:
        rgb = []
    return measure_from_frames(frames, rgb)


def measure_from_frames(frames: list[bytes], rgb: list[bytes] | None = None) -> dict:
    """The editorial measurements from tiny frames already decoded elsewhere (`score` gets them for free from the same
    pass that measures exposure and motion, so a clip is decoded once per window instead of four times)."""
    if not frames:
        return {}
    mid = frames[len(frames) // 2]
    dx, dy, mag = motion_vector(frames)
    cx, cy = saliency_centroid(frames)
    out = {"shot_size": shot_size(mid), "motion_dx": dx, "motion_dy": dy, "motion_mag": mag,
           "focus_x": cx, "focus_y": cy, "flux": flux(frames), "internal_cut": internal_cut(frames),
           "local_motion": local_motion(frames)}
    if rgb:
        hue, sat = dominant_hue(rgb)
        out["hue"], out["colourfulness"] = hue, sat
        out["csig"] = colour_signature(rgb)
        shares = scene_shares(rgb)
        out["scene_shares"], out["scene"] = shares, scene_label(shares)
    return out


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
    # a clip that contains its own cut or flash is a poor single shot
    for j in range(n):
        f = feats[j] if j < len(feats) else {}
        if f and f.get("internal_cut"):
            out.append(_issue("warn", "internal-cut",
                              f"shot {j}: the clip changes abruptly inside the chosen window (a cut or flash in the source); "
                              "pick another start (`src_start`) or another clip"))
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
