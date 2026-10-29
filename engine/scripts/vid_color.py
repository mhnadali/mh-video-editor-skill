"""Phase 4: colour correction and colour grading, done the way a colourist works.

Pipeline per shot, in this order (never skip the first two to "fix it in the grade"):
  1. INPUT TRANSFORM   HLG/PQ clips -> SDR Rec.709 (tone-mapped in the part filter, see vid_render).
  2. CORRECTION        make the shot technically right and match its neighbours: exposure (in linear light), black and
                       white points, mid-tone gamma, white balance (per-channel gains in linear light). One `curves` filter.
                       Measured from the shot's own pixels (`plan_corrections`) and written into the EDL as plain numbers,
                       so a human can read, review with scopes and change them. Rendering never guesses.
  3. LOOK (GRADE)      a creative transform expressed with the vocabulary of grading: ASC CDL (slope / offset / power /
                       saturation), a filmic S-curve around a pivot, split-toning of shadows / mids / highlights, vibrance,
                       hue-selective shifts, highlight shoulder, lifted blacks. The whole look is baked into ONE 3D LUT
                       (.cube, 33^3) that `lut3d` applies with tetrahedral interpolation; your own .cube files plug in at the
                       same point, with a strength control.
  4. FINISH            optional vignette, limited-range legaliser (done by the final conversion).
All colour maths runs in 16-bit planar RGB (`gbrp16le`) and is dithered once, at the end of the part, to avoid banding.

Look strength blends the baked LUT towards identity at build time, so every strength is still one filter.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

import vid_sheets
import vidlib as vl

GAMMA = 2.4                    # display gamma used to move between encoded values and linear light
LUT_SIZE = 33
LUMA709 = (0.2126, 0.7152, 0.0722)


# --------------------------------------------------------------------------- small colour maths

def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def to_linear(x: float) -> float:
    return max(x, 0.0) ** GAMMA


def to_encoded(x: float) -> float:
    return max(x, 0.0) ** (1.0 / GAMMA)


def luma(r: float, g: float, b: float) -> float:
    return LUMA709[0] * r + LUMA709[1] * g + LUMA709[2] * b


def rgb_to_hsv(r: float, g: float, b: float) -> tuple[float, float, float]:
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    if d == 0:
        h = 0.0
    elif mx == r:
        h = ((g - b) / d) % 6
    elif mx == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return (h * 60.0) % 360.0, (0.0 if mx == 0 else d / mx), mx


def hsv_to_rgb(h: float, s: float, v: float) -> tuple[float, float, float]:
    h = (h % 360.0) / 60.0
    c = v * s
    x = c * (1 - abs(h % 2 - 1))
    m = v - c
    r, g, b = [(c, x, 0), (x, c, 0), (0, c, x), (0, x, c), (x, 0, c), (c, 0, x)][int(h) % 6]
    return r + m, g + m, b + m


def yuv_means_to_rgb(y: float, u: float, v: float) -> tuple[float, float, float]:
    """Mean limited-range Y/U/V (as signalstats reports them) -> mean encoded RGB in 0..1 (BT.709)."""
    yn, cb, cr = (y - 16.0) / 219.0, (u - 128.0) / 224.0, (v - 128.0) / 224.0
    return (yn + 1.5748 * cr, yn - 0.1873 * cb - 0.4681 * cr, yn + 1.8556 * cb)


# --------------------------------------------------------------------------- correction (curves filter)

IDENTITY_CORRECTION = {"ev": 0.0, "black": 0.0, "white": 1.0, "gamma": 1.0, "gain": [1.0, 1.0, 1.0]}


SECONDARY_KEYS = {"center", "width", "sat", "shift", "gain"}


def validate_secondary(adjustments) -> list:
    """Validate a hue-qualified secondary list: [{center, width, sat, shift, gain}, ...]."""
    if not isinstance(adjustments, list):
        raise vl.EngineError("correct.secondary must be a list of hue adjustments")
    for adj in adjustments:
        if not isinstance(adj, dict):
            raise vl.EngineError("each correct.secondary entry must be an object")
        for k in adj:
            if k not in SECONDARY_KEYS:
                raise vl.EngineError(f"correct.secondary.{k} is unknown (center, width, sat, shift, gain)")
        if not 0 <= adj.get("center", 0) <= 360:
            raise vl.EngineError("correct.secondary.center must be a hue 0..360")
        if not 5 <= adj.get("width", 40) <= 180:
            raise vl.EngineError("correct.secondary.width must be 5..180 degrees")
        if not 0.0 <= adj.get("sat", 1.0) <= 2.0:
            raise vl.EngineError("correct.secondary.sat must be 0..2")
        if not -60 <= adj.get("shift", 0.0) <= 60:
            raise vl.EngineError("correct.secondary.shift must be -60..60 degrees")
        if not 0.5 <= adj.get("gain", 1.0) <= 2.0:
            raise vl.EngineError("correct.secondary.gain must be 0.5..2.0")
    return adjustments


def normalize_correction(c: dict | None) -> dict | None:
    """Validate a correction dict (missing keys default to identity). None/empty -> None."""
    if not c:
        return None
    if not isinstance(c, dict):
        raise vl.EngineError("correct must be an object with ev, black, white, gamma, gain")
    secondary = c.get("secondary")
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in IDENTITY_CORRECTION.items()}
    for k, v in c.items():
        if k == "secondary":
            continue
        if k not in out:
            raise vl.EngineError(f"correct.{k} is unknown (known: ev, black, white, gamma, gain, secondary)")
        out[k] = v
    if not -3.0 <= out["ev"] <= 3.0:
        raise vl.EngineError("correct.ev must be between -3 and 3 stops")
    if not 0.0 <= out["black"] <= 0.3:
        raise vl.EngineError("correct.black must be 0..0.3")
    if not 0.6 <= out["white"] <= 1.0:
        raise vl.EngineError("correct.white must be 0.6..1.0")
    if out["white"] - out["black"] < 0.4:
        raise vl.EngineError("correct: white - black must leave at least 0.4 of range")
    if not 0.5 <= out["gamma"] <= 2.0:
        raise vl.EngineError("correct.gamma must be 0.5..2.0")
    g = out["gain"]
    if not (isinstance(g, list) and len(g) == 3 and all(0.5 <= x <= 2.0 for x in g)):
        raise vl.EngineError("correct.gain must be three numbers between 0.5 and 2.0")
    if secondary is not None:
        out["secondary"] = validate_secondary(secondary)
    if is_identity_correction(out) and not secondary:
        return None
    return out


def is_identity_correction(c: dict) -> bool:
    return (abs(c["ev"]) < 1e-6 and abs(c["black"]) < 1e-6 and abs(c["white"] - 1.0) < 1e-6 and abs(c["gamma"] - 1.0) < 1e-6
            and all(abs(x - 1.0) < 1e-6 for x in c["gain"]))


def correct_channel(x: float, c: dict, ch: int) -> float:
    lin = to_linear(x) * (2.0 ** c["ev"]) * c["gain"][ch]
    x = to_encoded(lin)
    x = (x - c["black"]) / max(c["white"] - c["black"], 1e-6)
    x = clamp(x)
    return x ** (1.0 / c["gamma"])


def curves_filter(c: dict | None, points: int = 17) -> str:
    """`curves` filter (monotonic pchip) implementing the correction, '' for identity."""
    c = normalize_correction(c)
    if not c:
        return ""
    parts = []
    for ch, name in enumerate("rgb"):
        pts = []
        for i in range(points):
            x = i / (points - 1)
            pts.append(f"{x:.4f}/{correct_channel(x, c, ch):.4f}")
        parts.append(f"{name}='{' '.join(pts)}'")
    return "curves=" + ":".join(parts) + ":interp=pchip"


# --------------------------------------------------------------------------- looks (parametric, baked into a 3D LUT)

LOOKS: dict[str, dict] = {
    "neutral": {"description": "No creative change."},
    "clean-bright": {
        "description": "Fresh travel/vlog look: lifted mids, gentle contrast, a little vibrance, neutral colour.",
        "exposure": 0.10, "contrast": 0.10, "pivot": 0.45, "vibrance": 0.25, "saturation": 1.05,
        "cdl": {"power": [0.97, 0.97, 0.97]}, "shoulder": 0.88},
    "desert-gold": {
        "description": "Warm sand and deep blue sky: golden highlights, cool-teal shadows, rich but controlled saturation.",
        "contrast": 0.18, "pivot": 0.42, "saturation": 1.08, "vibrance": 0.15,
        "cdl": {"slope": [1.03, 1.00, 0.95], "power": [0.98, 0.99, 1.01]},
        "shadows": [-0.008, 0.005, 0.016], "highlights": [0.022, 0.010, -0.020], "shoulder": 0.86,
        "hue_shifts": [{"center": 30, "width": 35, "shift": 3, "sat": 1.06}, {"center": 215, "width": 45, "shift": 3, "sat": 1.10}],
        "vignette": 0.18},
    "teal-orange": {
        "description": "Classic blockbuster split: warm skin/sand pushed to orange, shadows and sky to teal.",
        "contrast": 0.22, "pivot": 0.44, "saturation": 1.10,
        "shadows": [-0.025, 0.010, 0.035], "highlights": [0.040, 0.015, -0.040], "mids": [0.010, 0.000, -0.012],
        "hue_shifts": [{"center": 25, "width": 40, "shift": 6, "sat": 1.15}, {"center": 200, "width": 60, "shift": -8, "sat": 1.18}],
        "shoulder": 0.87, "vignette": 0.15},
    "golden-hour": {
        "description": "Low warm sun: amber cast, soft contrast, glowing highlights, slightly lifted blacks.",
        "exposure": 0.05, "contrast": 0.12, "pivot": 0.46, "saturation": 1.05, "black": 0.008,
        "cdl": {"slope": [1.06, 1.00, 0.91], "power": [0.97, 0.99, 1.02]},
        "highlights": [0.025, 0.012, -0.015], "shoulder": 0.84, "vignette": 0.18},
    "cinematic-soft": {
        "description": "Filmic and calm: lifted blacks, rolled-off highlights, muted colour, cool shadows.",
        "contrast": 0.14, "pivot": 0.47, "saturation": 0.88, "black": 0.035, "shoulder": 0.80,
        "shadows": [-0.008, 0.004, 0.016], "highlights": [0.012, 0.006, -0.006], "vignette": 0.22},
    "film-faded": {
        "description": "Faded print: milky blacks, softened highlights, slightly green-yellow mids.",
        "contrast": 0.08, "pivot": 0.5, "saturation": 0.92, "black": 0.06, "white": 0.96, "shoulder": 0.78,
        "mids": [0.004, 0.010, -0.006]},
    "bleach-bypass": {
        "description": "Gritty and desaturated with heavy contrast.",
        "contrast": 0.34, "pivot": 0.45, "saturation": 0.62, "shoulder": 0.90},
    "moody-dark": {
        "description": "Low-key: dense shadows, protected highlights, cool tint.",
        "exposure": -0.30, "contrast": 0.24, "pivot": 0.38, "saturation": 0.95, "shoulder": 0.84,
        "shadows": [-0.012, 0.004, 0.026], "vignette": 0.30},
    "vivid-travel": {
        "description": "Punchy and saturated for postcards: strong colour, crisp contrast.",
        "contrast": 0.26, "pivot": 0.45, "saturation": 1.28, "vibrance": 0.30, "shoulder": 0.90},
    "bw-contrast": {
        "description": "Black and white with strong contrast and a slight red filter.",
        "mono": {"weights": [0.45, 0.45, 0.10]}, "contrast": 0.30, "pivot": 0.45, "shoulder": 0.90, "vignette": 0.30},
    "muted-matte": {
        "description": "Understated editorial matte: softened colour, lifted blacks, rolled highlights, gentle contrast.",
        "contrast": 0.10, "pivot": 0.46, "saturation": 0.82, "black": 0.03, "white": 0.97, "shoulder": 0.82,
        "shadows": [-0.004, 0.002, 0.008]},
    "cool-blue": {
        "description": "Cool blue cast for night, water and urban scenes: teal-leaning shadows, crisp contrast.",
        "contrast": 0.14, "pivot": 0.45, "saturation": 0.98, "shoulder": 0.88,
        "cdl": {"slope": [0.96, 1.00, 1.06]}, "shadows": [-0.010, 0.000, 0.020]},
    "warm-film": {
        "description": "Warm film emulation: amber highlights, protected blacks, soft shoulder, a hint of vignette.",
        "contrast": 0.14, "pivot": 0.45, "saturation": 1.04, "black": 0.02, "shoulder": 0.84,
        "cdl": {"slope": [1.05, 1.00, 0.93]}, "highlights": [0.020, 0.008, -0.012], "vignette": 0.12},
}
LOOK_KEYS = {"description", "exposure", "contrast", "pivot", "black", "white", "shoulder", "cdl", "shadows", "mids",
             "highlights", "saturation", "vibrance", "hue_shifts", "mono", "vignette"}


def list_looks() -> list[tuple[str, str]]:
    return [(k, v.get("description", "")) for k, v in LOOKS.items()]


def validate_look(look: dict, name: str = "look") -> None:
    for k in look:
        if k not in LOOK_KEYS:
            raise vl.EngineError(f"{name}: unknown key '{k}' (known: {', '.join(sorted(LOOK_KEYS))})")
    for k in ("shadows", "mids", "highlights"):
        v = look.get(k)
        if v is not None and not (isinstance(v, list) and len(v) == 3 and all(-0.2 <= x <= 0.2 for x in v)):
            raise vl.EngineError(f"{name}.{k} must be three numbers between -0.2 and 0.2")
    cdl = look.get("cdl") or {}
    for k in cdl:
        if k not in ("slope", "offset", "power"):
            raise vl.EngineError(f"{name}.cdl.{k} is unknown (slope, offset, power)")
        if not (isinstance(cdl[k], list) and len(cdl[k]) == 3):
            raise vl.EngineError(f"{name}.cdl.{k} must be three numbers")
    if not 0.0 <= look.get("saturation", 1.0) <= 2.0:
        raise vl.EngineError(f"{name}.saturation must be 0..2")
    if not 0.0 <= look.get("contrast", 0.0) <= 0.6:
        raise vl.EngineError(f"{name}.contrast must be 0..0.6")
    if not 0.2 <= look.get("pivot", 0.45) <= 0.8:
        raise vl.EngineError(f"{name}.pivot must be 0.2..0.8")
    if not 0.0 <= look.get("vignette", 0.0) <= 1.0:
        raise vl.EngineError(f"{name}.vignette must be 0..1")


def s_curve(x: float, contrast: float, pivot: float) -> float:
    """Filmic contrast: a power curve on each side of the pivot, slope (1 + contrast) at the pivot, ends fixed at 0 and 1."""
    if contrast <= 0:
        return x
    k = 1.0 + contrast
    if x <= pivot:
        return pivot * (x / pivot) ** k if pivot > 0 else x
    return 1.0 - (1.0 - pivot) * ((1.0 - x) / (1.0 - pivot)) ** k


def shoulder_rolloff(x: float, knee: float) -> float:
    """Soft highlight compression: identity below `knee`, smooth approach to 1.0 above it."""
    if x <= knee or knee >= 1.0:
        return x
    r = 1.0 - knee
    return knee + r * (1.0 - math.exp(-(x - knee) / r))


def _hue_weight(h: float, center: float, width: float) -> float:
    d = abs((h - center + 180.0) % 360.0 - 180.0)
    return 0.0 if d >= width else 0.5 * (1.0 + math.cos(math.pi * d / width))


def look_function(look: dict):
    """-> f(r, g, b) mapping encoded RGB 0..1 to graded RGB 0..1."""
    ex = 2.0 ** look.get("exposure", 0.0)
    contrast, pivot = look.get("contrast", 0.0), look.get("pivot", 0.45)
    black, white = look.get("black", 0.0), look.get("white", 1.0)
    knee = look.get("shoulder")
    cdl = look.get("cdl") or {}
    slope, offset, power = cdl.get("slope", [1, 1, 1]), cdl.get("offset", [0, 0, 0]), cdl.get("power", [1, 1, 1])
    sh, mi, hi = look.get("shadows"), look.get("mids"), look.get("highlights")
    sat, vib = look.get("saturation", 1.0), look.get("vibrance", 0.0)
    hues = look.get("hue_shifts") or []
    mono = look.get("mono")

    def f(r: float, g: float, b: float):
        c = [r, g, b]
        if ex != 1.0:                                              # exposure in linear light
            c = [to_encoded(to_linear(v) * ex) for v in c]
        c = [clamp(v, 0.0, 4.0) for v in c]
        for i in range(3):                                         # ASC CDL: (in * slope + offset) ^ power
            v = max(c[i] * slope[i] + offset[i], 0.0)
            c[i] = v ** power[i]
        if contrast > 0:
            c = [s_curve(clamp(v), contrast, pivot) for v in c]
        l = clamp(luma(*c))
        if sh or mi or hi:                                         # split toning weighted by luminance
            ws, wm, wh = (1 - l) ** 2, 4 * l * (1 - l), l ** 2
            for i in range(3):
                c[i] += (sh[i] * ws if sh else 0.0) + (mi[i] * wm if mi else 0.0) + (hi[i] * wh if hi else 0.0)
        c = [clamp(v) for v in c]
        if sat != 1.0 or vib:
            l = luma(*c)
            mx, mn = max(c), min(c)
            s_in = 0.0 if mx <= 1e-6 else (mx - mn) / mx
            k = sat * (1.0 + vib * (1.0 - s_in))
            c = [clamp(l + (v - l) * k) for v in c]
        for hs in hues:                                            # hue-selective shift/saturation
            h, s, v = rgb_to_hsv(*c)
            w = _hue_weight(h, hs["center"], hs["width"]) * min(1.0, s * 3.0)
            if w > 0:
                nh = h + hs.get("shift", 0.0) * w
                ns = clamp(s * (1.0 + (hs.get("sat", 1.0) - 1.0) * w))
                c = list(hsv_to_rgb(nh, ns, v))
        if mono:
            w = mono.get("weights", [0.3, 0.59, 0.11])
            m = clamp(w[0] * c[0] + w[1] * c[1] + w[2] * c[2])
            c = [m, m, m]
        if black or white != 1.0:                                  # lifted blacks / lowered whites (film fade)
            c = [black + (white - black) * v for v in c]
        if knee:
            c = [shoulder_rolloff(v, knee) for v in c]
        return tuple(clamp(v) for v in c)

    return f


def _blend_towards_identity(f, strength: float):
    if strength >= 0.999:
        return f

    def g(r, gg, b):
        o = f(r, gg, b)
        return (r + strength * (o[0] - r), gg + strength * (o[1] - gg), b + strength * (o[2] - b))

    return g


def build_lut(fn, size: int = LUT_SIZE) -> list[tuple[float, float, float]]:
    """Sample fn over the RGB cube; red varies fastest (the .cube convention FFmpeg expects)."""
    data = []
    step = 1.0 / (size - 1)
    for bi in range(size):
        for gi in range(size):
            for ri in range(size):
                data.append(fn(ri * step, gi * step, bi * step))
    return data


def write_cube(path: str | os.PathLike, data: list, size: int, title: str = "mh-video-editor") -> str:
    lines = [f'TITLE "{title}"', f"LUT_3D_SIZE {size}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    lines += [f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in data]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic: parallel part renders (and parallel test processes) share one LUT folder, and a reader must never see a
    # half-written file ("3D LUT is empty" killed a render when two workers raced on the same look)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="ascii")
    vl.replace_file(tmp, path)
    return str(path)


def read_cube(path: str | os.PathLike) -> dict:
    """Parse a 3D .cube LUT: {'size', 'data', 'domain_min', 'domain_max', 'title'}. Raises EngineError on anything odd."""
    p = Path(path)
    if not p.is_file():
        raise vl.EngineError(f"LUT not found: {path}")
    size, title, dmin, dmax, data = None, "", [0.0] * 3, [1.0] * 3, []
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        up = line.upper()
        if up.startswith("TITLE"):
            title = line[5:].strip().strip('"')
        elif up.startswith("LUT_1D_SIZE"):
            raise vl.EngineError(f"{path}: 1D LUTs are not supported, use a 3D .cube")
        elif up.startswith("LUT_3D_SIZE"):
            size = int(line.split()[1])
        elif up.startswith("DOMAIN_MIN"):
            dmin = [float(x) for x in line.split()[1:4]]
        elif up.startswith("DOMAIN_MAX"):
            dmax = [float(x) for x in line.split()[1:4]]
        elif re.match(r"^[-+0-9.eE]", line):
            vals = line.split()
            if len(vals) != 3:
                raise vl.EngineError(f"{path}: bad data line '{line[:40]}'")
            data.append(tuple(float(v) for v in vals))
    if not size or size < 2:
        raise vl.EngineError(f"{path}: no LUT_3D_SIZE")
    if len(data) != size ** 3:
        raise vl.EngineError(f"{path}: expected {size ** 3} entries for size {size}, found {len(data)}")
    return {"size": size, "data": data, "domain_min": dmin, "domain_max": dmax, "title": title}


def apply_cube(cube: dict, r: float, g: float, b: float) -> tuple[float, float, float]:
    """Trilinear lookup (tests and previews); inputs and outputs in 0..1 of the LUT domain."""
    n = cube["size"]
    pos = []
    for v, lo, hi in zip((r, g, b), cube["domain_min"], cube["domain_max"]):
        pos.append(clamp((v - lo) / max(hi - lo, 1e-9)) * (n - 1))
    i0 = [min(int(p), n - 2) for p in pos]
    fr = [p - i for p, i in zip(pos, i0)]
    out = [0.0, 0.0, 0.0]
    for dr in (0, 1):
        for dg in (0, 1):
            for db in (0, 1):
                w = (fr[0] if dr else 1 - fr[0]) * (fr[1] if dg else 1 - fr[1]) * (fr[2] if db else 1 - fr[2])
                idx = (i0[0] + dr) + (i0[1] + dg) * n + (i0[2] + db) * n * n
                e = cube["data"][idx]
                for k in range(3):
                    out[k] += w * e[k]
    return tuple(out)


# --------------------------------------------------------------------------- grade specs -> filter chain

def resolve_look(spec) -> tuple[dict, float, str | None, float]:
    """spec (name | dict) -> (look dict, strength, user_lut_path, lut_strength)."""
    strength, lut, lut_strength = 1.0, None, 1.0
    if isinstance(spec, str):
        name = spec
        look = LOOKS.get(name)
        if look is None:
            raise vl.EngineError(f"look '{name}' is unknown (known: {', '.join(LOOKS)})")
        return dict(look), 1.0, None, 1.0
    if not isinstance(spec, dict):
        raise vl.EngineError("grade must be a look name, a raw filter string or an object")
    look = {}
    if "look" in spec:
        base = spec["look"]
        if isinstance(base, str):
            if base not in LOOKS:
                raise vl.EngineError(f"look '{base}' is unknown (known: {', '.join(LOOKS)})")
            look = dict(LOOKS[base])
        elif isinstance(base, dict):
            look = dict(base)
        else:
            raise vl.EngineError("grade.look must be a name or an object")
    extra = {k: v for k, v in spec.items() if k in LOOK_KEYS}
    look.update(extra)
    strength = float(spec.get("strength", 1.0))
    lut = spec.get("lut")
    lut_strength = float(spec.get("lut_strength", 1.0))
    if not 0.0 <= strength <= 1.0 or not 0.0 <= lut_strength <= 1.0:
        raise vl.EngineError("grade.strength and grade.lut_strength must be 0..1")
    validate_look(look, "grade")
    return look, strength, lut, lut_strength


def is_look_spec(grade) -> bool:
    if isinstance(grade, dict):
        return True
    return isinstance(grade, str) and grade in LOOKS


def look_hash(look: dict, strength: float) -> str:
    return hashlib.sha1(json.dumps([look, round(strength, 4), LUT_SIZE, "v1"], sort_keys=True).encode()).hexdigest()[:12]


_LUT_LOCKS: dict[str, threading.Lock] = {}
_LUT_LOCKS_GUARD = threading.Lock()


def ensure_look_lut(look: dict, strength: float, lut_dir: str | os.PathLike) -> str | None:
    """Bake the look into <lut_dir>/look_<hash>.cube (cached) and return its path; None for an identity look."""
    keys = set(look) - {"description", "vignette"}
    if not keys or strength <= 0:
        return None
    path = Path(lut_dir) / f"look_{look_hash(look, strength)}.cube"
    with _LUT_LOCKS_GUARD:
        lock = _LUT_LOCKS.setdefault(str(path), threading.Lock())
    with lock:                                                       # part renders run in threads: build each LUT once
        if not path.is_file():
            fn = _blend_towards_identity(look_function(look), strength)
            write_cube(path, build_lut(fn), LUT_SIZE, title="mh-look-" + look_hash(look, strength))
    return str(path)


def secondary_function(adjustments: list):
    """-> f(r, g, b): hue-qualified secondary correction. Each adjustment only touches pixels near its hue (sky,
    foliage, skin), changing saturation, hue and brightness there. Baked into a small LUT like a look."""
    def f(r: float, g: float, b: float):
        c = [r, g, b]
        for adj in adjustments:
            h, s, v = rgb_to_hsv(*c)
            w = _hue_weight(h, adj.get("center", 0.0), adj.get("width", 40.0)) * min(1.0, s * 3.0)
            if w > 0:
                nh = h + adj.get("shift", 0.0) * w
                ns = clamp(s * (1.0 + (adj.get("sat", 1.0) - 1.0) * w))
                nv = clamp(v * (1.0 + (adj.get("gain", 1.0) - 1.0) * w))
                c = list(hsv_to_rgb(nh, ns, nv))
        return tuple(clamp(x) for x in c)
    return f


def ensure_secondary_lut(adjustments: list, lut_dir: str | os.PathLike) -> str | None:
    """Bake a hue-qualified secondary correction into <lut_dir>/sec_<hash>.cube (cached); None if empty."""
    if not adjustments:
        return None
    key = hashlib.sha1(json.dumps(adjustments, sort_keys=True).encode()).hexdigest()[:12]
    path = Path(lut_dir) / f"sec_{key}.cube"
    with _LUT_LOCKS_GUARD:
        lock = _LUT_LOCKS.setdefault(str(path), threading.Lock())
    with lock:
        if not path.is_file():
            write_cube(path, build_lut(secondary_function(adjustments)), LUT_SIZE, title="mh-secondary")
    return str(path)


def vignette_filter(amount: float) -> str:
    """Soft corner darkening. FFmpeg's `angle` is the lens angle: a LARGER angle darkens MORE (measured on 9:16: pi/7 leaves
    the corners at 62% of the centre, pi/3 at 0%). amount 0.2 = pi/7 (the gentle vignette of the approved Maranjab v4)."""
    if amount <= 0:
        return ""
    angle = min(math.pi / 2 * 0.8, (math.pi / 7) * amount / 0.2)
    return f"vignette=angle={angle:.4f}"


def grade_chain(grade, correct, lut_dir: str | os.PathLike) -> list[str]:
    """Filters (in order) for one shot: correction curves, look LUT, user LUT, vignette. [] when nothing is requested.

    `grade` may be a legacy raw filter string / preset (returned untouched by the caller), so this handles only looks:
    a look name, or an object {look, strength, lut, lut_strength, vignette, ...overrides}.
    """
    chain: list[str] = []
    corr = curves_filter(correct)
    nc = normalize_correction(correct)
    secondary = nc.get("secondary") if nc else None
    look = None
    strength, user_lut, user_strength = 1.0, None, 1.0
    if grade:
        look, strength, user_lut, user_strength = resolve_look(grade)
    luts: list[str] = []
    if secondary:                                                     # hue-qualified secondary correction, before the look
        sp = ensure_secondary_lut(secondary, lut_dir)
        if sp:
            luts.append(f"lut3d=file='{vl.filter_escape_path(sp)}':interp=tetrahedral")
    if look:
        p = ensure_look_lut(look, strength, lut_dir)
        if p:
            luts.append(f"lut3d=file='{vl.filter_escape_path(p)}':interp=tetrahedral")
    if user_lut:
        cube = read_cube(user_lut)                                    # validates the file early with a clear message
        if user_strength >= 0.999:
            luts.append(f"lut3d=file='{vl.filter_escape_path(user_lut)}':interp=tetrahedral")
        else:
            # strength < 1: bake a blended copy so it is still one filter
            blended = [tuple(i / (cube["size"] - 1) for i in (n % cube["size"], (n // cube["size"]) % cube["size"], n // cube["size"] ** 2))
                       for n in range(cube["size"] ** 3)]
            mixed = [tuple(a + user_strength * (b - a) for a, b in zip(src, dst)) for src, dst in zip(blended, cube["data"])]
            mp = Path(lut_dir) / f"user_{hashlib.sha1((user_lut + str(user_strength)).encode()).hexdigest()[:10]}.cube"
            if not mp.is_file():
                write_cube(mp, mixed, cube["size"], title="mh-user-blend")
            luts.append(f"lut3d=file='{vl.filter_escape_path(mp)}':interp=tetrahedral")
    if corr or luts:
        chain.append("format=gbrp16le")
        if corr:
            chain.append(corr)
        chain += luts
    vig = vignette_filter((look or {}).get("vignette", 0.0))
    if vig:
        chain.append(vig)
    return chain


# --------------------------------------------------------------------------- measurement and automatic correction

_STAT_KEYS = ("YAVG", "YLOW", "YHIGH", "YMIN", "YMAX", "UAVG", "VAVG", "SATAVG")


def measure_window(path: str, start: float, dur: float = 1.0, hdr: bool = False, hw: bool = False) -> dict | None:
    """Mean signalstats of a window of a clip (HDR clips are tone-mapped first). None if nothing could be measured."""
    graph = f"fps=5,scale=480:-2,{vid_sheets.TONEMAP_HLG if hdr else ''}format=yuv420p,signalstats,metadata=print:file=s.txt[o]"
    with tempfile.TemporaryDirectory(prefix="mhvid_color_") as d:
        cmd = [vl.find_ffmpeg()["ffmpeg"], "-v", "error"] + (["-hwaccel", "cuda"] if hw else []) + \
              ["-ss", f"{max(0.0, start):.2f}", "-t", str(dur), "-i", vl.ffp(path), "-an", "-filter_complex", graph,
               "-map", "[o]", "-f", "null", "-"]
        subprocess.run(cmd, capture_output=True, text=True, cwd=d)
        s = Path(d) / "s.txt"
        if not s.exists():
            return None
        vals: dict[str, list[float]] = {k: [] for k in _STAT_KEYS}
        for line in s.read_text(errors="replace").splitlines():
            m = re.match(r"lavfi\.signalstats\.(\w+)=(\S+)", line.strip())
            if m and m.group(1) in vals:
                v = vl.parse_float(m.group(2))
                if v is not None:
                    vals[m.group(1)].append(v)
    if not vals["YAVG"]:
        return None
    return {k: sum(v) / len(v) for k, v in vals.items() if v}


TARGET_MEAN = 0.42           # encoded luma mean a "well exposed" daylight shot sits near


def propose_correction(stats: dict, strength: float = 0.8, target_mean: float | None = None,
                       reference: dict | None = None, wb: str = "median", median_chroma: tuple[float, float] | None = None
                       ) -> tuple[dict | None, list[str]]:
    """Measured stats -> (correction dict or None, plain-language reasons).

    Conservative by design, because automatic correction on real footage is easy to get wrong:
      * exposure: a dead band of 0.07 in mean luma, at most +-0.45 EV, and at most +0.25 EV for low-key shots (a dark cabin
        is meant to be dark);
      * black / white points: only when the ABSOLUTE darkest/brightest pixel (YMIN/YMAX) shows the picture does not use the
        range (haze, flat light). A bright desert has a high 10th percentile and is not hazy;
      * white balance (`wb`): "median" moves the shot's colour balance a little towards the median balance of all shots
        (consistency, at most +-10%); "gray-world" assumes the average colour is neutral (wrong for a sky or a desert, so
        it is capped at +-15% and opt-in); "off" does nothing. A `reference` shot replaces the targets for exposure and balance.
    """
    reasons: list[str] = []
    y = stats["YAVG"]
    mean = clamp((y - 16.0) / 219.0, 0.02, 0.98)
    target = clamp((reference["YAVG"] - 16.0) / 219.0, 0.05, 0.95) if reference else (target_mean if target_mean is not None else TARGET_MEAN)
    y_min = clamp((stats["YMIN"] - 16.0) / 219.0)
    y_max = clamp((stats["YMAX"] - 16.0) / 219.0)
    # 1. levels first: they move the mean, so the exposure step must aim from where the mean ends up
    black = 0.0
    if y_min > 0.12:                                           # nothing near black: lifted/hazy picture
        black = clamp((y_min - 0.02) * strength, 0.0, 0.10)
        reasons.append(f"black point {black:.2f} (darkest pixel sits at {y_min:.2f}: hazy or lifted)")
    white = 1.0
    if y_max < 0.80:                                           # nothing near white: flat light
        white = clamp(y_max / 0.95, 0.85, 1.0)
        white = 1.0 - (1.0 - white) * strength
        reasons.append(f"white point {white:.2f} (brightest pixel only reaches {y_max:.2f})")
    leveled = clamp((mean - black) / max(white - black, 0.4))
    # 2. exposure
    ev = 0.0
    if abs(leveled - target) > 0.07:
        ev = clamp(2.4 * math.log2(target / max(leveled, 0.02)) * strength, -0.45, 0.45)
        if leveled < 0.30 and ev > 0.25:
            ev = 0.25
        reasons.insert(0, f"exposure {ev:+.2f} EV (mean luma {leveled:.2f}, target {target:.2f})")
    gain = [1.0, 1.0, 1.0]
    r, g, b = yuv_means_to_rgb(y, stats["UAVG"], stats["VAVG"])
    if r > 0.02 and g > 0.02 and b > 0.02 and wb != "off":
        full, limit, strength_wb, why = None, 0.10, 0.6, ""
        if reference:
            rr, rg, rb = yuv_means_to_rgb(reference["YAVG"], reference["UAVG"], reference["VAVG"])
            full, why = [(g / r) * (rr / rg), 1.0, (g / b) * (rb / rg)], "matching the reference shot"
        elif wb == "median" and median_chroma:
            mr, mg, mb = yuv_means_to_rgb(y, median_chroma[0], median_chroma[1])      # median colour at this shot's brightness
            full, why = [(g / r) * (mr / mg), 1.0, (g / b) * (mb / mg)], "towards the median colour balance of all shots"
        elif wb == "gray-world":
            full, limit, strength_wb, why = [g / r, 1.0, g / b], 0.15, 0.5, "gray-world (assumes the average colour is neutral)"
        elif wb == "skin":
            # skin-tone aware: neutralise a cast but never cool the image below neutral, so warm skin stays warm
            full, limit, strength_wb, why = [g / r, 1.0, g / b], 0.12, 0.5, "skin-tone protected (warms only, never cools)"
        if full:
            cast = max(abs(full[0] - 1.0), abs(full[2] - 1.0))
            if cast > 0.03:
                gain = [clamp(1.0 + strength_wb * strength * (x - 1.0), 1.0 - limit, 1.0 + limit) for x in full]
                if wb == "skin":
                    gain = [max(gain[0], 1.0), gain[1], min(gain[2], 1.0)]   # protect warmth: allow warming, block cooling
                reasons.append(f"white balance gains R {gain[0]:.2f} / B {gain[2]:.2f} ({why}, difference {cast:.0%})")
    corr = {"ev": round(ev, 3), "black": round(black, 3), "white": round(white, 3), "gamma": 1.0,
            "gain": [round(x, 3) for x in gain]}
    try:
        corr = normalize_correction(corr)
    except vl.EngineError:
        corr = None
    if not reasons:
        reasons.append("already well exposed and balanced: no correction")
    return corr, reasons


def plan_corrections(edl: dict, work: str | os.PathLike, strength: float = 0.8, match: str = "exposure",
                     reference: int | None = None, only: set[int] | None = None, log=vl.say, wb: str | None = None) -> dict:
    """Analyse every shot of an EDL and return {shot index: {"correct": dict|None, "reasons": [...], "stats": {...}}}.

    match='exposure' pulls each shot's brightness towards the median of all shots (limited to +-0.6 EV), 'reference' matches
    shot `reference` (exposure and colour balance), 'none' only repairs technical problems per shot.
    """
    import vid_edl as ve
    import vid_render as vr
    from concurrent.futures import ThreadPoolExecutor
    bounds = ve.timeline(edl)
    trans = ve.effective_transitions(edl, bounds)
    shots = edl["shots"]

    def one(j):
        pl = vr.part_plan(edl, j, bounds, trans, 1.0)
        info = vl.probe(pl["clip"])
        win = vr.shot_window(pl, info)
        st = measure_window(pl["clip"], win["start"], max(0.6, min(2.0, win["src_seconds"])), bool(info.get("hdr")),
                            vr.nvenc_works())
        return j, st

    with ThreadPoolExecutor(vl.cpu_workers(4)) as ex:
        stats = dict(ex.map(one, range(len(shots))))
    ok = {j: s for j, s in stats.items() if s}
    if not ok:
        raise vl.EngineError("could not measure any shot")
    ref_stats = ok.get(reference) if reference is not None else None
    if match == "reference" and not ref_stats:
        raise vl.EngineError("reference shot could not be measured")
    ys = sorted((s["YAVG"] - 16.0) / 219.0 for s in ok.values())
    median_mean = ys[len(ys) // 2]
    us = sorted(s["UAVG"] for s in ok.values())
    vs = sorted(s["VAVG"] for s in ok.values())
    median_chroma = (us[len(us) // 2], vs[len(vs) // 2])
    if wb is None:
        # after `color cameras` the cameras are already matched to each other: pulling every shot towards the median balance
        # on top of that would also flatten real differences of light, so the default becomes 'off'
        wb = "off" if edl.get("camera_match") else ("median" if match == "exposure" else "off")
        if edl.get("camera_match"):
            log("the cameras of this EDL are matched (color cameras): white balance is left alone (--wb to override)")
    out = {}
    for j, s in ok.items():
        if only is not None and j not in only:
            continue
        if match == "reference":
            corr, why = propose_correction(s, strength, reference=ref_stats, wb=wb)
        elif match == "exposure":
            corr, why = propose_correction(s, strength, target_mean=median_mean, wb=wb, median_chroma=median_chroma)
        else:
            corr, why = propose_correction(s, strength, target_mean=clamp((s["YAVG"] - 16.0) / 219.0), wb=wb,
                                           median_chroma=median_chroma)
        out[j] = {"correct": corr, "reasons": why, "stats": {k: round(v, 1) for k, v in s.items()}}
    return out


def _is_only_gain(correct: dict | None, gain: list) -> bool:
    """True when a shot's correction is the identity apart from exactly this gain."""
    c = normalize_correction(correct) if correct else None
    if not c or c.get("secondary"):
        return False
    rest = dict(c, gain=[1.0, 1.0, 1.0])
    return is_identity_correction(rest) and all(abs(a - b) < 1e-4 for a, b in zip(c["gain"], gain))


def apply_plan(edl: dict, plan: dict, overwrite: bool = False) -> int:
    """Write planned corrections into the EDL shots (skips shots that already have one unless overwrite). A shot whose camera
    was matched (`color cameras`) keeps its camera gain on top of the new correction."""
    import vid_camera
    n = 0
    for j, item in plan.items():
        shot = edl["shots"][j]
        cam = vid_camera.camera_gain_of(edl, j)
        # a correction that is nothing but the camera gain of `color cameras` does not count as one: the shot still gets its
        # exposure / levels correction (otherwise matching the cameras first would block every other correction of those shots)
        only_camera = bool(cam) and _is_only_gain(shot.get("correct"), cam)
        if shot.get("correct") is not None and not overwrite and not only_camera:
            continue
        new = item["correct"]
        if cam:
            base = dict(new) if new else {k: (list(v) if isinstance(v, list) else v) for k, v in IDENTITY_CORRECTION.items()}
            base["gain"] = [round(clamp(a * b, 0.5, 2.0), 4) for a, b in zip(base["gain"], cam)]
            new = normalize_correction(base)
        if new:
            if new != shot.get("correct"):
                n += 1
            shot["correct"] = new
        elif "correct" in shot and overwrite:
            del shot["correct"]
    return n


# --------------------------------------------------------------------------- scopes

def scope_sheet(source: str | os.PathLike, out: str | os.PathLike, t: float = 0.0, width: int = 420, hdr: bool = False) -> str:
    """One image with the frame, a luma waveform, a vectorscope and an RGB histogram: what a colourist reads."""
    width = width // 2 * 2
    th = int(width * 0.75) // 2 * 2                       # even sizes: yuv420p cannot hold odd heights
    tm = vid_sheets.TONEMAP_HLG if hdr else ""
    g = (f"[0:v]{tm}format=yuv420p,scale={width}:{th}:force_original_aspect_ratio=decrease,pad={width}:{th}:(ow-iw)/2:(oh-ih)/2,"
         "split=4[a][b][c][d];"
         f"[b]waveform=filter=lowpass:scale=digital:graticule=green:intensity=0.25:mode=column,scale={width}:{th}[wf];"
         f"[c]vectorscope=mode=color3:graticule=green:colorspace=709,scale={th}:{th},pad={width}:{th}:(ow-iw)/2:0[vs];"
         f"[d]histogram=display_mode=overlay:levels_mode=logarithmic:components=7,scale={width}:{th}[hg];"
         "[a][wf]hstack[top];[vs][hg]hstack[bot];[top][bot]vstack[o]")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-ss", f"{max(0.0, t):.2f}", "-i", vl.ffp(source),
            "-filter_complex", g, "-map", "[o]", "-frames:v", "1", "-update", "1", "-q:v", "3", vl.ffp(out)])
    return str(out)
