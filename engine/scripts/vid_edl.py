"""EDL (edit decision list): the single source of truth for one video.

An EDL is plain JSON. `load` + `timeline` turn it into frame-exact shot boundaries, `lint` checks it
against the restraint budget learned from the Maranjab project (few effects, varied transitions),
and the renderer builds the video from it. Changing a video = editing the EDL and re-rendering.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

import vidlib as vl

PAD = 2                      # frames of overlap kept on each side of a transition (xfade lasts 2*PAD frames)
MIN_SHOT_FRAMES = 8
MIN_OVERLAP, MAX_OVERLAP = 2, 24     # frames a transition may overlap (EDL `transition_frames`); even numbers only

DEFAULT_STYLE = {
    "grade": "warm-desert",   # preset name or path (engine/presets/*.json)
    "zoom": 0.05,             # gentle push-in per shot (0 = off)
    "drop_flash": True,       # one white flash + RGB split on the drop (needs beats.drop_beat)
    "end_flare": True,        # one warm sun flare at the ending
    "flare_time": None,       # seconds; default = last cut + 0.3
    "riser": True,            # synthesised riser before the drop
    "boom": True,             # synthesised 50 Hz boom on the drop
    "loudness": -14.0,        # integrated LUFS target of the final mix (YouTube, Reels and Spotify normalise to about -14)
    "correct": None,          # colour correction for every shot: {"ev","black","white","gamma","gain":[r,g,b]} (see vid_color)
    "reframe": "cover",       # how footage of another aspect ratio fills the frame: cover | blur-fill | fit
    "sound_design": None,     # synthesised whooshes with ducking: {"whoosh": true, "level_db": -24, "max_events": 12, "duck": true}
    "ambient": None,          # natural sound of the clips under the music: {"level_db": -14, "fade_ms": 40, "shots": "all", "highpass_hz": 90}
    "hdr_tonemap": "hable-203",   # HLG/PQ -> SDR: hable-203 | mobius-203 | reinhard-203 | legacy (what Maranjab v1-v4 used)
}
VISIBLE_EFFECTS = ("zoom", "drop_flash", "end_flare")   # what counts against the effect budget (+ "slow-motion" when used)
REFRAME_MODES = ("cover", "blur-fill", "fit")
KNOWN_STYLE_KEYS = set(DEFAULT_STYLE) | {"extras", "budget"}
DEFAULT_BUDGET = {
    "max_effects": 3,               # visible effect kinds in the whole video
    "max_transition_share": 0.5,    # transitions / cut points
    "max_same_transition": 1,       # each type once ('fade' may appear twice)
    "max_family": 3,                # looks-alike family cap
    "allow_per_beat": False,        # effects that fire on every beat were judged "too much"
    "max_zoom": 0.08,
}


def merged_style(edl: dict) -> dict:
    s = dict(DEFAULT_STYLE)
    s.update(edl.get("style") or {})
    return s


def budget(edl: dict) -> dict:
    b = dict(DEFAULT_BUDGET)
    b.update((edl.get("style") or {}).get("budget") or {})
    return b


def project(edl: dict) -> dict:
    p = dict(edl.get("project") or {})
    if p.get("preset"):
        pre = vl.load_preset(p["preset"])
        for k in ("width", "height", "fps"):
            p.setdefault(k, pre.get(k))
    p.setdefault("fps", 30)
    p.setdefault("fade_in", 0.4)
    p.setdefault("fade_out", 0.8)
    p.setdefault("music_fade_in", 0.3)
    p.setdefault("music_fade_out", 0.9)
    return p


def load(path: str | os.PathLike) -> dict:
    edl = vl.load_json(path)
    if not isinstance(edl, dict):
        raise vl.EngineError(f"{path}: an EDL is a JSON object, found {type(edl).__name__}")
    if edl.get("version") != 1:
        raise vl.EngineError(f"{path}: unsupported or missing 'version' (expected 1)")
    for key in ("project", "shots"):
        if key not in edl:
            raise vl.EngineError(f"{path}: missing '{key}'")
    return edl


def grid_time(edl: dict, beat: float) -> float:
    b = edl.get("beats") or {}
    if "t0" not in b or "period" not in b:
        raise vl.EngineError("EDL uses 'beats' spans but has no beats.t0 / beats.period")
    return b["t0"] + b["period"] * beat


def shot_span(edl: dict, shot: dict) -> tuple[float, float]:
    if "span" in shot:
        a, b = shot["span"]
    elif "beats" in shot:
        a, b = (grid_time(edl, x) for x in shot["beats"])
    else:
        raise vl.EngineError(f"shot for {shot.get('clip')}: needs 'span' (seconds) or 'beats'")
    return float(a), float(b)


def timeline(edl: dict) -> list[int]:
    """Frame boundaries [0, b1, ..., end]; shot j covers bounds[j]..bounds[j+1]."""
    p = project(edl)
    fps = p["fps"]
    bounds = [0]
    prev_end = 0.0
    for i, shot in enumerate(edl["shots"]):
        a, b = shot_span(edl, shot)
        if abs(a - prev_end) > 0.5 / fps + 1e-6:
            raise vl.EngineError(f"shot {i}: starts at {a:.3f}s but the previous shot ends at {prev_end:.3f}s (gap or overlap)")
        bounds.append(vl.frames(b, fps))
        prev_end = b
    return bounds


def overlap_frames(edl: dict, trans: dict[int, str]) -> dict[int, int]:
    """Frames each transition overlaps (split evenly over both shots). Default 2*PAD; `transition_frames` overrides it."""
    raw = edl.get("transition_frames") or {}
    out = {}
    for j in trans:
        v = raw.get(str(j), raw.get(j))
        out[j] = int(v) if isinstance(v, int) and not isinstance(v, bool) and v % 2 == 0 and MIN_OVERLAP <= v <= MAX_OVERLAP else 2 * PAD
    return out


def drop_boundary(edl: dict, bounds: list[int]) -> int | None:
    """Index j of the cut that carries the drop (None if the EDL has no drop or it is not on a cut)."""
    b = edl.get("beats") or {}
    if b.get("drop_beat") is None:
        return None
    fps = project(edl)["fps"]
    f = vl.frames(grid_time(edl, b["drop_beat"]), fps)
    for j in range(1, len(bounds) - 1):
        if abs(bounds[j] - f) <= 1:
            return j
    return None


def effective_transitions(edl: dict, bounds: list[int] | None = None) -> dict[int, str]:
    """User transitions + the forced white fade on the drop cut when drop_flash is on."""
    bounds = bounds or timeline(edl)
    out = {int(k): v for k, v in (edl.get("transitions") or {}).items()}
    if merged_style(edl)["drop_flash"]:
        j = drop_boundary(edl, bounds)
        if j is not None:
            out[j] = "fadewhite"
    return out


# --------------------------------------------------------------------------- lint

def _issue(level: str, code: str, msg: str) -> dict:
    return {"level": level, "code": code, "msg": msg}


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and abs(v) <= 1e7


def structure_errors(edl) -> list[dict]:
    """Shape and type problems that would make the rest of the code fail with a raw exception.

    Everything else in lint (and the renderer) may assume what is checked here: the types and finiteness of the core
    fields. Anything not covered is caught by the safety net in lint() and reported as `edl-malformed`.
    """
    out: list[dict] = []

    def bad(code, msg):
        out.append(_issue("error", code, msg))

    if not isinstance(edl, dict):
        return [_issue("error", "structure", f"an EDL is a JSON object, found {type(edl).__name__}")]
    for key, kind in (("project", dict), ("beats", dict), ("style", dict), ("transitions", dict), ("transition_frames", dict), ("rationale", dict), ("shots", list), ("texts", list)):
        v = edl.get(key)
        if v is not None and not isinstance(v, kind):
            bad("structure", f"'{key}' must be {'an object' if kind is dict else 'a list'}, found {type(v).__name__}")
    if out:
        return out
    p = edl.get("project") or {}
    for k in ("width", "height"):
        if k in p and not (_num(p[k]) and 16 <= p[k] <= 16384):
            bad("project", f"project.{k} must be a number between 16 and 16384, found {p[k]!r}")
    if "fps" in p and not (_num(p["fps"]) and 1 <= p["fps"] <= 240):
        bad("project", f"project.fps must be a number between 1 and 240, found {p['fps']!r}")
    if "duration" in p and not (_num(p["duration"]) and 0 < p["duration"] <= 36000):
        bad("project", f"project.duration must be a positive number of seconds, found {p['duration']!r}")
    for k in ("fade_in", "fade_out", "music_fade_in", "music_fade_out"):
        if k in p and not (_num(p[k]) and 0 <= p[k] <= 30):
            bad("project", f"project.{k} must be 0-30 seconds, found {p[k]!r}")
    mu = p.get("music")
    if mu is not None and not (isinstance(mu, str) or (isinstance(mu, list) and all(isinstance(x, str) for x in mu))):
        bad("project", "project.music must be a file path, a list of file paths (music sections), or null")
    if p.get("preset") is not None and not isinstance(p["preset"], str):
        bad("project", "project.preset must be a preset name")
    st = edl.get("style") or {}
    if "zoom" in st and not (_num(st["zoom"]) and -1 <= st["zoom"] <= 1):
        bad("style", f"style.zoom must be a number between 0 and 1, found {st['zoom']!r}")
    if "loudness" in st and not (_num(st["loudness"]) and -70 <= st["loudness"] <= 0):
        bad("style", f"style.loudness must be LUFS between -70 and 0, found {st['loudness']!r}")
    if st.get("flare_time") is not None and not _num(st["flare_time"]):
        bad("style", f"style.flare_time must be a number of seconds, found {st['flare_time']!r}")
    b = edl.get("beats") or {}
    for k in ("t0", "period"):
        if k in b and not _num(b[k]):
            bad("beats", f"beats.{k} must be a number, found {b[k]!r}")
    if _num(b.get("period")) and b["period"] <= 0:
        bad("beats", "beats.period must be positive")
    if b.get("drop_beat") is not None and not _num(b["drop_beat"]):
        bad("beats", f"beats.drop_beat must be a number or null, found {b['drop_beat']!r}")
    for i, shot in enumerate(edl.get("shots") or []):
        if not isinstance(shot, dict):
            bad("shot", f"shot {i} must be an object, found {type(shot).__name__}")
            continue
        if not isinstance(shot.get("clip"), str) or not shot["clip"]:
            bad("shot", f"shot {i}: 'clip' must be a file path")
        for k in ("span", "beats"):
            if k in shot and not (isinstance(shot[k], list) and len(shot[k]) == 2 and all(_num(x) for x in shot[k])):
                bad("shot", f"shot {i}: '{k}' must be [start, end] with two numbers, found {shot[k]!r}")
        if "span" in shot and "beats" in shot:
            bad("shot", f"shot {i}: give either 'span' or 'beats', not both")
        if "src_start" in shot and not (_num(shot["src_start"]) and shot["src_start"] >= 0):
            bad("shot", f"shot {i}: src_start must be a number of seconds >= 0, found {shot['src_start']!r}")
    def numbers(where, obj, names):
        if obj is None or obj is True or obj is False:
            return
        if not isinstance(obj, dict):
            bad("structure", f"{where} must be an object, found {obj!r}")
            return
        for n in names:
            if n in obj and not _num(obj[n]):
                bad("structure", f"{where}.{n} must be a number, found {obj[n]!r}")

    def correction(where, c):
        numbers(where, c, ("ev", "black", "white", "gamma"))
        g = c.get("gain") if isinstance(c, dict) else None
        if g is not None and not (isinstance(g, list) and len(g) == 3 and all(_num(x) for x in g)):
            bad("structure", f"{where}.gain must be [red, green, blue] numbers, found {g!r}")

    correction("style.correct", st.get("correct"))
    numbers("style.sound_design", st.get("sound_design"), ("level_db", "max_events"))
    numbers("style.ambient", st.get("ambient"), ("level_db", "fade_ms", "highpass_hz"))
    if isinstance(st.get("grade"), dict):
        numbers("style.grade", st["grade"], ("strength", "lut_strength"))
    for i, shot in enumerate(edl.get("shots") or []):
        if not isinstance(shot, dict):
            continue
        sp = shot.get("speed")
        if isinstance(sp, dict):
            numbers(f"shot {i} speed", sp, ("from", "to"))
        elif sp is not None and not _num(sp):
            bad("structure", f"shot {i}: speed must be a number or {{\"from\": a, \"to\": b}}, found {sp!r}")
        if shot.get("correct") is not None:
            correction(f"shot {i} correct", shot["correct"])
        numbers(f"shot {i} audio", shot.get("audio"), ("gain_db",))
        if isinstance(shot.get("grade"), dict):
            numbers(f"shot {i} grade", shot["grade"], ("strength", "lut_strength"))
    if not edl.get("shots"):
        bad("no-shots", "EDL has no shots")
    for k, v in (edl.get("transitions") or {}).items():
        if not isinstance(v, str):
            bad("transition-unknown", f"transition {k!r} must be a transition name, found {v!r}")
    for i, t in enumerate(edl.get("texts") or []):
        if not isinstance(t, dict):
            bad("text", f"texts[{i}] must be an object, found {type(t).__name__}")
    return out


def lint(edl: dict, check_files: bool = True) -> list[dict]:
    """Return a list of {'level': 'error'|'warn', 'code', 'msg'}. Errors block rendering. Never raises on bad input."""
    errs = structure_errors(edl)
    if errs:
        return errs
    try:
        return _lint(edl, check_files)
    except vl.EngineError as e:
        return [_issue("error", "edl-malformed", str(e))]
    except (TypeError, ValueError, KeyError, AttributeError, IndexError, OverflowError, OSError) as e:
        return [_issue("error", "edl-malformed", f"the EDL has a value of the wrong kind or shape ({type(e).__name__}: {e}); "
                       "check the fields named in the other messages and the format in references/edl-format.md")]


def _lint(edl: dict, check_files: bool = True) -> list[dict]:
    out: list[dict] = []
    p = project(edl)
    fps = p["fps"]
    style = merged_style(edl)
    bud = budget(edl)
    shots = edl["shots"]
    if not shots:
        return [_issue("error", "no-shots", "EDL has no shots")]
    for k in ("width", "height", "duration"):
        if not p.get(k):
            out.append(_issue("error", "project", f"project.{k} is missing"))
    if out:
        return out
    try:
        bounds = timeline(edl)
    except vl.EngineError as e:
        return [_issue("error", "timeline", str(e))]
    total = vl.frames(p["duration"], fps)
    if bounds[-1] != total:
        out.append(_issue("error", "duration", f"shots end at frame {bounds[-1]} but project.duration is {total} frames"))
    n = len(shots)
    for j in range(n):
        ln = bounds[j + 1] - bounds[j]
        if ln < MIN_SHOT_FRAMES:
            out.append(_issue("error", "short-shot", f"shot {j} is only {ln} frames (minimum {MIN_SHOT_FRAMES})"))
    # transitions
    trans = {}
    for k, v in (edl.get("transitions") or {}).items():
        try:
            j = int(k)
        except ValueError:
            out.append(_issue("error", "transition-key", f"transition key '{k}' is not a boundary number"))
            continue
        if not 1 <= j <= n - 1:
            out.append(_issue("error", "transition-key", f"transition {j} is outside 1..{n - 1}"))
            continue
        if v in vl.BANNED_TRANSITIONS:
            out.append(_issue("error", "transition-banned", f"transition '{v}' at {j} is banned ({vl.BANNED_REASONS[v]})"))
        elif v not in vl.ALL_TRANSITIONS:
            out.append(_issue("error", "transition-unknown", f"unknown transition '{v}' at {j}"))
        trans[j] = v
    tf = edl.get("transition_frames")
    if isinstance(tf, dict):
        for k, v in tf.items():
            try:
                tj = int(k)
            except ValueError:
                out.append(_issue("error", "transition-frames", f"transition_frames key '{k}' is not a boundary number"))
                continue
            if not 1 <= tj <= n - 1:
                out.append(_issue("error", "transition-frames", f"transition_frames {tj} is outside 1..{n - 1}"))
            if not (isinstance(v, int) and not isinstance(v, bool) and v % 2 == 0 and MIN_OVERLAP <= v <= MAX_OVERLAP):
                out.append(_issue("error", "transition-frames",
                                  f"transition_frames {k} must be an even whole number of frames from {MIN_OVERLAP} to {MAX_OVERLAP}, found {v!r}"))
    rat = edl.get("rationale")
    if isinstance(rat, dict):
        for k, v in rat.items():
            try:
                rj = int(k)
            except ValueError:
                out.append(_issue("error", "rationale-key", f"rationale key '{k}' is not a boundary number"))
                continue
            if not 1 <= rj <= n - 1:
                out.append(_issue("error", "rationale-key", f"rationale {rj} is outside 1..{n - 1}"))
            if not (isinstance(v, dict) and isinstance(v.get("type"), str) and isinstance(v.get("why"), str) and v["why"].strip()):
                out.append(_issue("error", "rationale-shape", f"rationale {k} must be {{\"type\": text, \"why\": text}} with a reason"))
    eff = effective_transitions(edl, bounds) if not any(i["code"].startswith("transition-") for i in out) else trans
    cuts = max(n - 1, 1)
    share = len(eff) / cuts
    if share > bud["max_transition_share"] + 1e-9:
        out.append(_issue("warn", "transition-share", f"{len(eff)}/{cuts} cut points use a transition ({share:.0%}); "
                          f"keep at most {bud['max_transition_share']:.0%} and use hard cuts on the beat for the rest"))
    counts: dict[str, int] = {}
    fam: dict[str, int] = {}
    for v in eff.values():
        counts[v] = counts.get(v, 0) + 1
        f = vl.family_of(v)
        if f:
            fam[f] = fam.get(f, 0) + 1
    for v, c in sorted(counts.items()):
        cap = bud["max_same_transition"] + (1 if v == "fade" else 0)
        if c > cap:
            out.append(_issue("warn", "transition-repeat", f"transition '{v}' is used {c} times (max {cap}); vary the look"))
    for f, c in sorted(fam.items()):
        if c > bud["max_family"] and f != "fade":
            out.append(_issue("warn", "transition-family", f"{c} transitions look alike ({f} family, max {bud['max_family']})"))
    if isinstance(tf, dict) and tf and not any(i["code"] == "transition-frames" for i in out):
        ovs = overlap_frames(edl, eff)
        for k in sorted(tf, key=lambda x: int(x)):
            if int(k) not in eff:
                out.append(_issue("warn", "transition-frames-unused", f"transition_frames {k} has no transition at that cut"))
        for j in range(n):
            ln = bounds[j + 1] - bounds[j]
            head = ovs.get(j, 0) // 2
            tail = ovs.get(j + 1, 0) // 2
            if head + tail > ln - 2:
                out.append(_issue("error", "transition-frames",
                                  f"shot {j} is {ln} frames but its transitions overlap {head + tail}; shorten them or lengthen the shot"))
    if isinstance(rat, dict) and rat:                                      # opt-in: once reasons are used, every transition needs one
        for j in sorted(eff):
            if str(j) not in rat:
                out.append(_issue("warn", "transition-unmotivated",
                                  f"transition '{eff[j]}' at cut {j} has no reason in `rationale`; give it one or make it a hard cut"))
    if sum(1 for v in eff.values() if v == "fadewhite") > 1:
        out.append(_issue("warn", "flash-overuse", "more than one white flash: it only works once, on the drop"))
    # effects
    for k in (edl.get("style") or {}):
        if k not in KNOWN_STYLE_KEYS:
            out.append(_issue("error", "style-key", f"unknown style key '{k}' (known: {', '.join(sorted(KNOWN_STYLE_KEYS))})"))
    extras = style.get("extras") or []
    kinds = [e for e in VISIBLE_EFFECTS if (style.get(e) if e != "zoom" else style.get("zoom"))]
    if any(_has_speed(s) for s in shots):
        kinds.append("slow-motion")                           # visible and noticeable: it counts against the budget
    if extras:
        kinds += list(extras)
        if not bud["allow_per_beat"]:
            out.append(_issue("error", "per-beat", f"extra effects {extras} are not engine-supported in this version; "
                              "effects that fire on every beat made v3 'too much'"))
    if len(kinds) > bud["max_effects"]:
        out.append(_issue("warn", "effect-budget", f"{len(kinds)} visible effects ({', '.join(kinds)}); budget is {bud['max_effects']}"))
    if style.get("zoom", 0) > bud["max_zoom"]:
        out.append(_issue("warn", "zoom", f"zoom {style['zoom']} is stronger than {bud['max_zoom']}; keep it subtle"))
    if style.get("zoom", 0) < 0:
        out.append(_issue("error", "zoom", "zoom must be >= 0 (zoom-out crashes some FFmpeg builds)"))
    if style["drop_flash"] and (edl.get("beats") or {}).get("drop_beat") is not None and drop_boundary(edl, bounds) is None:
        out.append(_issue("warn", "drop-not-on-cut", "beats.drop_beat does not fall on a cut; the flash will be skipped"))
    out += _phase4_issues(edl, style, shots)
    # files
    if check_files:
        try:
            out += ffmpeg_gaps(edl, vl.available_filters())
        except vl.EngineError:
            pass                                      # no FFmpeg at all: the render reports that
        mu = p.get("music")
        warm_music = [m for m in (mu if isinstance(mu, list) else ([mu] if mu else [])) if m and os.path.isfile(m)]
        vl.probe_many([s.get("clip") for s in shots if s.get("clip") and os.path.isfile(s["clip"])]
                      + warm_music)   # warm the probe cache in parallel
        seen: dict[tuple, int] = {}
        for j, shot in enumerate(shots):
            clip = shot.get("clip")
            if not clip or not os.path.isfile(clip):
                out.append(_issue("error", "clip-missing", f"shot {j}: clip not found: {clip}"))
                continue
            key = (clip, shot.get("src_start"))
            if key in seen:
                out.append(_issue("warn", "clip-reused", f"shot {j} reuses the clip of shot {seen[key]}"))
            seen[key] = j
            try:
                info = vl.probe(clip)
            except vl.EngineError as e:
                out.append(_issue("error", "clip-unreadable", f"shot {j}: {str(e).splitlines()[0]}"))
                continue
            need = (bounds[j + 1] - bounds[j]) / fps
            if info["duration"] and info["duration"] < need + 0.2:
                out.append(_issue("warn", "clip-short", f"shot {j}: clip is {info['duration']:.2f}s but the shot needs {need:.2f}s (last frame will freeze)"))
        music = p.get("music")
        music_files = music if isinstance(music, list) else ([music] if music else [])
        for mi, mf in enumerate(music_files):
            if not os.path.isfile(mf):
                out.append(_issue("error", "music-missing", f"music not found: {mf}"))
                continue
            try:
                md = vl.probe(mf)["duration"]
                if len(music_files) == 1 and abs(md - p["duration"]) > 0.15:
                    out.append(_issue("warn", "music-length", f"music is {md:.2f}s but project.duration is {p['duration']:.2f}s"))
            except vl.EngineError as e:
                out.append(_issue("error", "music-unreadable", str(e).splitlines()[0]))
    return out


def features_in_use(edl: dict) -> list[str]:
    """Names (keys of vidlib.FEATURE_FILTERS) of the optional capabilities this EDL needs from FFmpeg."""
    import vid_color
    import vid_motion
    style = merged_style(edl)
    shots = edl.get("shots") or []
    used: list[str] = []

    def add(name):
        if name not in used:
            used.append(name)

    def uses_grade(grade, correct):
        if correct:
            return True
        return grade is not None and vid_color.is_look_spec(grade)

    if uses_grade(style.get("grade"), style.get("correct")) or any(uses_grade(s.get("grade"), s.get("correct")) for s in shots):
        add("colour looks and correction")
    for s in shots:
        try:
            if vid_motion.normalize_stabilize(s.get("stabilize")):
                add("stabilisation")
            if vid_motion.normalize_speed(s.get("speed")) is not None and s.get("speed_mode", "auto") != "native":
                add("slow motion by interpolation")
        except vl.EngineError:
            continue                                    # reported by the validators
    if edl.get("texts"):
        add("on-screen text")
    if style.get("reframe") == "blur-fill" or any(s.get("reframe") == "blur-fill" for s in shots):
        add("blur-fill reframing")
    sd = style.get("sound_design")
    if sd is True or (isinstance(sd, dict) and sd.get("whoosh", True)):
        add("sound design (whooshes, ducking)")
    if style.get("ambient"):
        add("ambient sound")
    return used


def ffmpeg_gaps(edl: dict, have: set[str]) -> list[dict]:
    """Errors for every optional capability the EDL uses but the installed FFmpeg lacks."""
    out = []
    for feat in features_in_use(edl):
        miss = vl.missing_for(feat, have)
        if miss:
            out.append(_issue("error", "ffmpeg-missing", f"this EDL uses {feat}, but your FFmpeg build lacks the filter(s) "
                              f"{', '.join(miss)}. Install a full FFmpeg build (Windows: winget install Gyan.FFmpeg) "
                              "or remove the feature from the EDL; `doctor` shows what your build supports"))
    return out


def _has_speed(shot: dict) -> bool:
    try:
        import vid_motion
        return vid_motion.normalize_speed(shot.get("speed")) is not None
    except vl.EngineError:
        return False


def _phase4_issues(edl: dict, style: dict, shots: list[dict]) -> list[dict]:
    """Validate the phase-4 fields: motion, colour, reframing, focus, texts, sound design, ambient audio."""
    import vid_color
    import vid_motion
    out: list[dict] = []

    def add(level, code, msg):
        out.append(_issue(level, code, msg))

    def color_checks(where, grade, correct):
        if grade is not None and vid_color.is_look_spec(grade):
            try:
                vid_color.resolve_look(grade)
            except vl.EngineError as e:
                add("error", "grade", f"{where}: {e}")
        elif isinstance(grade, str) and grade:
            if not grade.strip().startswith(("eq=", "colorbalance", "curves", "lut3d")):
                try:
                    vl.load_preset(grade)
                except vl.EngineError:
                    add("error", "grade", f"{where}: grade '{grade}' is not a look ({', '.join(vid_color.LOOKS)}), a preset or a raw filter")
        if correct is not None:
            try:
                vid_color.normalize_correction(correct)
            except vl.EngineError as e:
                add("error", "correct", f"{where}: {e}")

    color_checks("style", style.get("grade"), style.get("correct"))
    import vid_sheets
    if style.get("hdr_tonemap") not in vid_sheets.TONEMAPS:
        add("error", "hdr-tonemap", f"style.hdr_tonemap '{style.get('hdr_tonemap')}' is unknown ({', '.join(vid_sheets.TONEMAPS)})")
    if style.get("reframe") not in REFRAME_MODES:
        add("error", "reframe", f"style.reframe '{style.get('reframe')}' is unknown ({', '.join(REFRAME_MODES)})")
    for j, shot in enumerate(shots):
        for issue in vid_motion.validate_shot(shot, j):
            add(issue["level"], issue["code"], issue["msg"])
        if "grade" in shot or "correct" in shot:
            color_checks(f"shot {j}", shot.get("grade"), shot.get("correct"))
        if shot.get("reframe") is not None and shot["reframe"] not in REFRAME_MODES:
            add("error", "reframe", f"shot {j}: reframe '{shot['reframe']}' is unknown ({', '.join(REFRAME_MODES)})")
        f = shot.get("focus")
        if f is not None and f != "auto" and not (isinstance(f, list) and len(f) == 2 and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in f)):
            add("error", "focus", f"shot {j}: focus must be [x, y] with both between 0 and 1, or \"auto\"")
    try:
        import vid_text
        out += vid_text.validate(edl)
    except ImportError:
        pass
    try:
        import vid_sfx
        out += vid_sfx.validate(edl)
    except ImportError:
        pass
    return out


def has_errors(issues: list[dict]) -> bool:
    return any(i["level"] == "error" for i in issues)


def format_issues(issues: list[dict]) -> str:
    if not issues:
        return "lint: OK (no issues)"
    return "\n".join(f"[{i['level'].upper():5}] {i['code']}: {i['msg']}" for i in issues)
