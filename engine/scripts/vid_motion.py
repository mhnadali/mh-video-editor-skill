"""Phase 4: motion tools per shot - stabilisation (vidstab, two passes) and speed changes (slow motion, speed-up, ramps).

EDL fields on a shot:
  "stabilize": false | true | "light" | "strong" | {"shakiness": 1-10, "smoothing": frames, "zoom": percent, "optzoom": 0|1|2}
  "speed":     1 (default) | 0.5 (slow motion) | 2 (speed-up) | {"from": 1.0, "to": 0.4} (a linear speed ramp)
  "speed_mode": "auto" | "native" | "blend" | "mci"       how missing frames are made when slowing down

Slow motion needs frames that were never recorded. `native` uses the real frames (best: 60/120 fps footage slowed to 50%),
`mci` interpolates with motion compensation (smooth, can warp fast edges), `blend` cross-fades neighbours (cheap, ghosts).
`auto` picks native when the source has enough frames, else mci for shots up to 1.5 s and blend for longer ones.
"""
from __future__ import annotations

import math

import vidlib as vl

STAB_PRESETS = {
    "light": {"shakiness": 4, "accuracy": 9, "stepsize": 6, "smoothing": 8, "zoom": 0, "optzoom": 1, "interpol": "bicubic"},
    "strong": {"shakiness": 8, "accuracy": 15, "stepsize": 4, "smoothing": 20, "zoom": 0, "optzoom": 1, "interpol": "bicubic"},
}
SPEED_MIN, SPEED_MAX = 0.1, 4.0
SPEED_MODES = ("auto", "native", "blend", "mci")
RAMP_FLAT = 1e-3             # speed ramps flatter than this are treated as constant


def normalize_stabilize(value) -> dict | None:
    """Resolve the EDL value to a parameter dict (None = off). Raises EngineError for nonsense."""
    if value in (None, False, 0, "off", "none"):
        return None
    if value is True:
        value = "light"
    if isinstance(value, str):
        if value not in STAB_PRESETS:
            raise vl.EngineError(f"stabilize '{value}' is unknown (use light, strong, true/false or a parameter object)")
        return dict(STAB_PRESETS[value])
    if isinstance(value, dict):
        out = dict(STAB_PRESETS["light"])
        out.update(value)
        if not 1 <= out["shakiness"] <= 10:
            raise vl.EngineError("stabilize.shakiness must be 1-10")
        if not 0 <= out["smoothing"] <= 100:
            raise vl.EngineError("stabilize.smoothing must be 0-100 frames")
        if not 0 <= out["zoom"] <= 20:
            raise vl.EngineError("stabilize.zoom must be 0-20 percent")
        return out
    raise vl.EngineError(f"stabilize: unsupported value {value!r}")


def normalize_speed(value) -> dict | None:
    """-> {'from': s0, 'to': s1} or None for normal speed."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        s0 = s1 = float(value)
    elif isinstance(value, dict) and "from" in value and "to" in value:
        s0, s1 = float(value["from"]), float(value["to"])
    else:
        raise vl.EngineError("speed must be a number or {\"from\": a, \"to\": b}")
    for s in (s0, s1):
        if not SPEED_MIN <= s <= SPEED_MAX:
            raise vl.EngineError(f"speed {s} is outside {SPEED_MIN}-{SPEED_MAX}")
    if abs(s0 - 1.0) < 1e-9 and abs(s1 - 1.0) < 1e-9:
        return None
    return {"from": s0, "to": s1}


def average_speed(speed: dict | None) -> float:
    return 1.0 if not speed else (speed["from"] + speed["to"]) / 2.0


def source_seconds(speed: dict | None, out_seconds: float) -> float:
    """How much source time a retimed shot of `out_seconds` consumes (exact for a linear ramp)."""
    return out_seconds * average_speed(speed)


def choose_mode(speed: dict | None, mode: str, out_seconds: float, src_fps: float, fps: float) -> str:
    """Resolve 'auto' to native / blend / mci. Speed-ups always use native (frames are dropped)."""
    if not speed:
        return "native"
    if mode not in SPEED_MODES:
        raise vl.EngineError(f"speed_mode '{mode}' is unknown (use {', '.join(SPEED_MODES)})")
    if mode != "auto":
        return mode
    slowest = min(speed["from"], speed["to"])
    if slowest >= 1.0:
        return "native"
    if (src_fps or 0) * slowest >= fps * 0.95:          # e.g. 120 fps footage at 50% still has 60 real frames per second
        return "native"
    return "mci" if out_seconds <= 1.5 else "blend"


def retime_chain(speed: dict | None, out_seconds: float, fps: float, src_fps: float, mode: str = "auto") -> list[str]:
    """Filters that retime a decoded clip (timestamps start at 0) and resample it to `fps`.

    The output timeline `u` and the source timeline `t` are related by t = s0*u + (s1-s0)*u^2/(2L) for a linear ramp,
    so setpts receives the inverse: u = (-s0 + sqrt(s0^2 + 2*a*t)) / a with a = (s1-s0)/L.
    """
    if not speed:
        return [f"fps={fps}"]
    chain = ["setpts=PTS-STARTPTS"]
    used = choose_mode(speed, mode, out_seconds, src_fps, fps)
    if speed:
        s0, s1 = speed["from"], speed["to"]
        if abs(s1 - s0) < RAMP_FLAT:
            chain.append(f"setpts=PTS/{(s0 + s1) / 2:.5f}")
        else:
            a = (s1 - s0) / max(out_seconds, 1e-3)
            chain.append(f"setpts='(-{s0:.5f}+sqrt({s0:.5f}*{s0:.5f}+2*({a:.6f})*T))/({a:.6f})/TB'")
    if used == "mci" and speed:
        chain.append(f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1")
    elif used == "blend" and speed:
        chain.append(f"minterpolate=fps={fps}:mi_mode=blend")
    else:
        chain.append(f"fps={fps}")
    return chain


def stab_detect_filter(params: dict, trf: str) -> str:
    return (f"vidstabdetect=shakiness={params['shakiness']}:accuracy={params['accuracy']}:stepsize={params['stepsize']}:"
            f"result='{vl.filter_escape_path(trf)}'")


def stab_transform_filter(params: dict, trf: str) -> str:
    return (f"vidstabtransform=input='{vl.filter_escape_path(trf)}':smoothing={int(params['smoothing'])}:zoom={params['zoom']}:"
            f"optzoom={params['optzoom']}:interpol={params['interpol']},unsharp=3:3:0.4:3:3:0.0")


def validate_shot(shot: dict, j: int) -> list[dict]:
    """Lint issues for the motion fields of one shot."""
    out = []
    try:
        normalize_stabilize(shot.get("stabilize"))
    except vl.EngineError as e:
        out.append({"level": "error", "code": "stabilize", "msg": f"shot {j}: {e}"})
    try:
        sp = normalize_speed(shot.get("speed"))
    except vl.EngineError as e:
        out.append({"level": "error", "code": "speed", "msg": f"shot {j}: {e}"})
        sp = None
    mode = shot.get("speed_mode", "auto")
    if mode not in SPEED_MODES:
        out.append({"level": "error", "code": "speed-mode", "msg": f"shot {j}: speed_mode '{mode}' is unknown"})
    if sp and shot.get("stabilize") and min(sp["from"], sp["to"]) < 0.5:
        out.append({"level": "warn", "code": "stabilize-slow", "msg": f"shot {j}: stabilising an already slowed shot exaggerates residual wobble"})
    return out


def smoothness(frame_diffs: list[float]) -> float:
    """Coefficient of variation of frame-to-frame change: lower = steadier. Used by tests and the QA gate."""
    if len(frame_diffs) < 3:
        return 0.0
    mean = sum(frame_diffs) / len(frame_diffs)
    if mean <= 1e-9:
        return 0.0
    var = sum((d - mean) ** 2 for d in frame_diffs) / len(frame_diffs)
    return math.sqrt(var) / mean
