"""Cut decisions with reasons: for every boundary between two shots, name the kind of cut, say why, and choose a
transition only when it earns its place (references/cut-craft.md, references/transition-craft.md).

The engine measures (shot size, camera motion, point of interest, brightness, energy, music sections); the agent's
`shot-notes.json` overrides those measurements because the agent has seen the frames. Everything here is explainable
rules over numbers, no ML. Output is a proposal: `cuts` prints it, `--write` stores `transitions` and `rationale` in the
EDL, `plan --smart-transitions` uses it instead of the positional vocabulary.

Sign convention (vid_editorial.motion_between): dx > 0 means the picture content moves RIGHT on screen between frames.
`slideleft` brings the next shot in from the right (content moves left), so it continues motion with dx < 0.
"""
from __future__ import annotations

import os
from typing import Optional

import vid_edl as ve
import vidlib as vl

MOVE_MIN = 1.0                  # motion magnitude (analysis pixels) that counts as "moving"
LATERAL_MIN = 1.2               # horizontal component that counts as a clear direction (== editorial SCREEN_DIR_MIN)
FOCUS_NEAR = 0.15               # distance between points of interest for a graphic match
FOCUS_OFF_CENTRE = 0.12         # a match only says something when at least one point of interest is off the middle
COMMON_HUE_SHARE = 0.6          # when this share of the shots has the same dominant colour, a colour match means nothing
COMMON_HUE_NEAR = 0.08
SAME_SCENE_BITS = 10            # perceptual-hash Hamming distance for "the same scene"
BRIGHT_JUMP = 70                # brightness (0-255) jump for a contrast cut
ENERGY_JUMP = 55                # energy (0-100) jump for a contrast cut
CALM = 35
SIZE_RANK = {"detail": 0, "close": 1, "medium": 2, "wide": 3}
MOOD_ENERGY = {"calm": 25.0, "building": 55.0, "intense": 85.0}

# Content moving LEFT on screen is continued by these; RIGHT by the mirror names; UP / DOWN likewise.
DIRECTIONAL = {
    "left": ["slideleft", "smoothleft", "coverleft", "wipeleft", "revealleft"],
    "right": ["slideright", "smoothright", "coverright", "wiperight", "revealright"],
    "up": ["slideup", "smoothup", "coverup", "wipeup", "revealup"],
    "down": ["slidedown", "smoothdown", "coverdown", "wipedown", "revealdown"],
}
TRANSITION_DIRECTION = {name: d for d, names in DIRECTIONAL.items() for name in names}
SECTION_LIFT = ["circleopen", "radial", "diagbl", "vertopen", "horzopen"]
SOFT = ["dissolve", "fade", "fadegrays"]

PRIORITY = {"impact": 100, "closing": 90, "section-lift": 70, "motion-bridge": 60, "soften": 50, "calm-dissolve": 40}
HARD_TYPES = {"cut-on-action", "graphic-match", "colour-match", "contrast-cut", "size-step", "montage-cut", "direction-clash",
              "jump-risk"}
ALL_TYPES = set(PRIORITY) | HARD_TYPES
# Overlap in frames (even; the cut stays centred on the beat). Calm passings of time breathe longer than energetic ones.
DEFAULT_FRAMES = 2 * ve.PAD
WANT_FRAMES = {"closing": 8, "soften": 8, "calm-dissolve": 8, "section-lift": 6}


# --------------------------------------------------------------------------- features

def scores_by_path(scores: dict) -> dict:
    out = {}
    for s in (scores or {}).values():
        if s.get("path"):
            out[os.path.normcase(os.path.abspath(s["path"]))] = s
    return out


def notes_by_path(notes: Optional[dict]) -> dict:
    return {os.path.normcase(os.path.abspath(k)): v for k, v in (notes or {}).items() if isinstance(v, dict)}


def shot_features(score: Optional[dict], note: Optional[dict]) -> dict:
    """One shot's features: measurements from `score`, overridden by the agent's `note`."""
    s, n = score or {}, note or {}
    f = {"size": n.get("shot_size") or s.get("shot_size"), "hash": s.get("hash"),
         "brightness": s.get("brightness"), "saturation": s.get("saturation"), "role": n.get("role"),
         "mood": n.get("mood"), "hue": s.get("hue"), "colourfulness": s.get("colourfulness"),
         "flux": s.get("flux"), "local_motion": s.get("local_motion")}
    dx, dy, mag = s.get("motion_dx"), s.get("motion_dy"), s.get("motion_mag")
    md = n.get("motion_dir")
    if md == "left":
        dx, dy, mag = -max(abs(dx or 0), 2.0), 0.0, max(mag or 0, 2.0)
    elif md == "right":
        dx, dy, mag = max(abs(dx or 0), 2.0), 0.0, max(mag or 0, 2.0)
    elif md == "up":
        dx, dy, mag = 0.0, -max(abs(dy or 0), 2.0), max(mag or 0, 2.0)
    elif md == "down":
        dx, dy, mag = 0.0, max(abs(dy or 0), 2.0), max(mag or 0, 2.0)
    elif md in ("toward", "away"):
        dx, dy, mag = 0.0, 0.0, max(mag or 0, 2.0)
    elif md == "none":
        dx, dy, mag = 0.0, 0.0, 0.0
    f["dx"], f["dy"], f["mag"] = dx, dy, mag
    foc = n.get("focus")
    if isinstance(foc, (list, tuple)) and len(foc) == 2:
        f["focus"] = (float(foc[0]), float(foc[1]))
    elif s.get("focus_x") is not None and s.get("focus_y") is not None:
        f["focus"] = (s["focus_x"], s["focus_y"])
    else:
        f["focus"] = None
    energy = MOOD_ENERGY.get(n.get("mood"))
    f["energy"] = energy if energy is not None else s.get("energy")
    return f


def override_score(score: Optional[dict], note: Optional[dict]) -> dict:
    """A score entry with the agent's shot-notes laid over it (shot size, movement), keyed like the score, for the QA gate."""
    out = dict(score or {})
    n = note or {}
    size = n.get("shot_size")
    if size:
        out["shot_size"] = "close" if size == "detail" else size
        out["shot_size_from"] = "notes"
    elif out.get("shot_size"):
        out["shot_size_from"] = "measured"            # 63 % agreement with sizes judged by eye (docs/CALIBRATION.md)
    f = shot_features(score, note)
    if n.get("motion_dir"):
        out["motion_dx"], out["motion_dy"], out["motion_mag"] = f["dx"], f["dy"], f["mag"]
    return out


def lateral(f: dict) -> int:
    """-1 content moves left, +1 right, 0 no clear horizontal direction."""
    dx = f.get("dx")
    if dx is None or abs(dx) < LATERAL_MIN:
        return 0
    return 1 if dx > 0 else -1


def vertical(f: dict) -> int:
    dy = f.get("dy")
    if dy is None or abs(dy) < LATERAL_MIN:
        return 0
    return 1 if dy > 0 else -1


def moving(f: dict) -> bool:
    return (f.get("mag") or 0) >= MOVE_MIN


def content_change(a: dict, b: dict) -> float:
    """How different two shots are, 0 (the same scene) to 1 (a different world): picture hash, light, saturation, hue and
    shot size, each contributing what it can. Unknown parts are left out, not guessed."""
    parts = []
    ham = hamming(a.get("hash"), b.get("hash"))
    if ham is not None:
        parts.append((0.35, min(ham / 32.0, 1.0)))
    if a.get("brightness") is not None and b.get("brightness") is not None:
        parts.append((0.2, min(abs(a["brightness"] - b["brightness"]) / 128.0, 1.0)))
    if a.get("saturation") is not None and b.get("saturation") is not None:
        parts.append((0.15, min(abs(a["saturation"] - b["saturation"]) / 100.0, 1.0)))
    hd = None
    if a.get("hue") is not None and b.get("hue") is not None:
        d = abs(a["hue"] - b["hue"]) % 1.0
        hd = min(d, 1.0 - d)
        parts.append((0.2, hd / 0.5))
    ra, rb = SIZE_RANK.get(a.get("size")), SIZE_RANK.get(b.get("size"))
    if ra is not None and rb is not None:
        parts.append((0.1, abs(ra - rb) / 3.0))
    total = sum(w for w, _ in parts)
    return round(sum(w * v for w, v in parts) / total, 3) if total else 0.0


def hamming(a, b) -> Optional[int]:
    if a is None or b is None:
        return None
    return bin(int(a) ^ int(b)).count("1")


def direction_name(f: dict) -> Optional[str]:
    """The on-screen direction the outgoing shot's content moves, dominant axis first."""
    lx, ly = lateral(f), vertical(f)
    if lx and (not ly or abs(f["dx"]) >= abs(f["dy"])):
        return "right" if lx > 0 else "left"
    if ly:
        return "down" if ly > 0 else "up"
    return None


# --------------------------------------------------------------------------- classification

def classify(a: dict, b: dict, ctx: dict) -> dict:
    """Decide the cut between shot features `a` (outgoing) and `b` (incoming).

    ctx: is_drop, is_last, section ('up' | 'down' | None). Returns {type, why, transition (a want or None), confidence}.
    The transition is a *wish*; `choose` applies the budget.
    """
    if ctx.get("is_drop"):
        return {"type": "impact", "transition": "fadewhite", "confidence": 1.0,
                "why": "the drop: a white flash lands the biggest musical hit"}
    if ctx.get("is_last"):
        return {"type": "closing", "transition": "fade", "confidence": 0.9,
                "why": "the ending: a soft fade lets the last shot breathe"}
    same_size = bool(a.get("size")) and a.get("size") == b.get("size")
    ham = hamming(a.get("hash"), b.get("hash"))
    same_scene = ham is not None and ham <= SAME_SCENE_BITS
    la, lb = lateral(a), lateral(b)
    ea, eb = a.get("energy"), b.get("energy")
    both_calm = ea is not None and eb is not None and ea < CALM and eb < CALM

    if same_scene and same_size:
        if both_calm:
            return {"type": "soften", "transition": "dissolve", "confidence": 0.6,
                    "why": "two similar calm shots of the same scene: a short dissolve softens what would be a jump cut "
                           "(better: change size or add a detail shot)"}
        return {"type": "jump-risk", "transition": None, "confidence": 0.4,
                "why": "similar shots of the same scene and size: this reads as a jump cut; change the size or angle, "
                       "or insert a detail shot between them"}
    if moving(a) and moving(b) and la and lb:
        if la == lb:
            return {"type": "cut-on-action", "transition": None, "confidence": 0.9,
                    "why": f"both shots move {'right' if la > 0 else 'left'} on screen: cutting on the movement hides the cut"}
        return {"type": "direction-clash", "transition": None, "confidence": 0.5,
                "why": "the movement reverses direction across the cut (180-degree line): reorder, flip the order, or "
                       "keep it only if the clash is intended"}
    if moving(a) and moving(b) and vertical(a) and vertical(a) == vertical(b):
        return {"type": "cut-on-action", "transition": None, "confidence": 0.8,
                "why": f"both shots move {'down' if vertical(a) > 0 else 'up'} on screen: the movement carries the cut"}
    fa, fb = a.get("focus"), b.get("focus")
    if fa and fb and ham is not None and ham > SAME_SCENE_BITS:
        dist = ((fa[0] - fb[0]) ** 2 + (fa[1] - fb[1]) ** 2) ** 0.5
        off = max(((f[0] - 0.5) ** 2 + (f[1] - 0.5) ** 2) ** 0.5 for f in (fa, fb))
        ra, rb = SIZE_RANK.get(a.get("size")), SIZE_RANK.get(b.get("size"))
        if dist <= FOCUS_NEAR and off >= FOCUS_OFF_CENTRE and (ra is None or rb is None or abs(ra - rb) <= 1):
            return {"type": "graphic-match", "transition": None, "confidence": 0.6,
                    "why": "the point of interest sits in the same place in both shots: the eye does not have to move"}
    ha, hb = a.get("hue"), b.get("hue")
    if ctx.get("colour_informative", True) and ha is not None and hb is not None and ham is not None and ham > SAME_SCENE_BITS and             (a.get("colourfulness") or 0) >= 0.15 and (b.get("colourfulness") or 0) >= 0.15:
        hd = abs(ha - hb) % 1.0
        if min(hd, 1.0 - hd) <= 0.06:
            return {"type": "colour-match", "transition": None, "confidence": 0.5,
                    "why": "two different places share the same dominant colour: the colour carries the eye across the cut"}
    ba, bb = a.get("brightness"), b.get("brightness")
    jump = (ba is not None and bb is not None and abs(ba - bb) >= BRIGHT_JUMP) or \
           (ea is not None and eb is not None and abs(ea - eb) >= ENERGY_JUMP)
    if jump:
        return {"type": "contrast-cut", "transition": None, "confidence": 0.7,
                "why": "a sudden change of light or energy: a hard cut makes the contrast hit; use it sparingly"}
    sec = ctx.get("section")
    if sec == "up":
        d = direction_name(a)
        return {"type": "section-lift", "transition": ("dir:" + d) if d else "lift", "confidence": 0.7,
                "why": "the music steps up here: a transition marks the new section"}
    if sec == "down":
        return {"type": "calm-dissolve", "transition": "fade", "confidence": 0.6,
                "why": "the music settles here: a fade marks the calmer section"}
    d = direction_name(a)
    if moving(a) and d and not moving(b):
        return {"type": "motion-bridge", "transition": "dir:" + d, "confidence": 0.6,
                "why": f"shot A ends moving {d} on screen: a slide {d} carries its momentum into the next shot"}
    if both_calm and ba is not None and bb is not None and (content_change(a, b) >= 0.3 or abs(ba - bb) >= 25 or abs((a.get("saturation") or 0) - (b.get("saturation") or 0)) >= 25):
        return {"type": "calm-dissolve", "transition": "dissolve", "confidence": 0.5,
                "why": "calm shots with a change of light or colour: a dissolve eases the change"}
    ra, rb = SIZE_RANK.get(a.get("size")), SIZE_RANK.get(b.get("size"))
    if ra is not None and rb is not None and ra != rb:
        return {"type": "size-step", "transition": None, "confidence": 0.6,
                "why": f"{a['size']} to {b['size']}: a change of shot size is a clean cut that moves the story"}
    if not any(a.get(k) is not None for k in ("size", "hash", "brightness", "mag")) and             not any(b.get(k) is not None for k in ("size", "hash", "brightness", "mag")):
        return {"type": "montage-cut", "transition": None, "confidence": 0.1,
                "why": "no measurements for these shots (run `score`, or describe them in shot-notes.json): a plain rhythm cut"}
    return {"type": "montage-cut", "transition": None, "confidence": 0.4,
            "why": "different shots on the beat; no stronger relation between them (rhythm cut)"}


# --------------------------------------------------------------------------- choosing under the budget

def _pick_name(want: str, used: dict, fam: dict, bud: dict, direction_hint: Optional[str] = None) -> Optional[str]:
    """Resolve a wish ('dir:left', 'lift', or a name) to a concrete, unbanned, within-budget transition name."""
    if want.startswith("dir:"):
        cands = list(DIRECTIONAL.get(want[4:], []))
    elif want == "lift":
        cands = list(SECTION_LIFT)
    elif want in ("dissolve", "fade"):
        cands = [want] + [c for c in SOFT if c != want]
    else:
        cands = [want]
    for c in cands:
        if c in vl.BANNED_TRANSITIONS or c not in vl.ALL_TRANSITIONS:
            continue
        cap = bud["max_same_transition"] + (1 if c == "fade" else 0)
        if used.get(c, 0) >= cap:
            continue
        f = vl.family_of(c)
        if f and f != "fade" and fam.get(f, 0) >= bud["max_family"]:
            continue
        return c
    return None


def choose(decisions: list, n_cuts: int, bud: dict, existing: Optional[dict] = None) -> list:
    """Apply the transition budget: the highest-priority wishes win, never more than the share cap, never two
    neighbouring transitions unless one is the drop or the closing, every name within the repeat and family caps."""
    hard_cap = min(int(n_cuts * bud["max_transition_share"] + 1e-9), n_cuts)     # what lint allows
    cap = min(hard_cap, max(1, int(n_cuts * 0.45))) if n_cuts else 0
    used: dict = {}
    fam: dict = {}
    taken: set = set()
    kept: set = set()
    for j, name in (existing or {}).items():            # transitions already in the EDL count against the budget
        kept.add(j - 1)
        used[name] = used.get(name, 0) + 1
        f = vl.family_of(name)
        if f:
            fam[f] = fam.get(f, 0) + 1
    for i, d in enumerate(decisions):
        if i in kept:
            d["existing"] = (existing or {}).get(d["j"])
            d["transition"] = None
    taken |= kept
    order = sorted((i for i, d in enumerate(decisions) if d.get("transition")),
                   key=lambda i: (-PRIORITY.get(decisions[i]["type"], 0), -decisions[i]["confidence"], i))
    for i in order:
        d = decisions[i]
        forced = d["type"] in ("impact", "closing")
        if d["type"] == "impact":
            pass                                         # the drop flash is forced by the engine whatever the budget says
        elif len(taken) >= (hard_cap if forced else cap):
            d["kept_hard"] = True
            continue
        if not forced and ((i - 1) in taken or (i + 1) in taken):
            d["kept_hard"] = True
            continue
        name = _pick_name(d["transition"], used, fam, bud)
        if name is None:
            d["kept_hard"] = True
            continue
        d["transition"] = name
        taken.add(i)
        used[name] = used.get(name, 0) + 1
        f = vl.family_of(name)
        if f:
            fam[f] = fam.get(f, 0) + 1
    for i, d in enumerate(decisions):
        if i not in taken:
            d["transition"] = None
    return decisions


def fit_frames(decisions: list, bounds: list) -> list:
    """Give every chosen transition its overlap, shrinking it where a short shot could not hold its two overlaps."""
    ov = {d["j"]: WANT_FRAMES.get(d["type"], DEFAULT_FRAMES) for d in decisions if d.get("transition")}
    lens = [bounds[i + 1] - bounds[i] for i in range(len(bounds) - 1)]
    changed = True
    while changed:
        changed = False
        for k, ln in enumerate(lens):
            if ov.get(k, 0) // 2 + ov.get(k + 1, 0) // 2 > ln - 2:
                shrinkable = [j for j in (k, k + 1) if ov.get(j, 0) > ve.MIN_OVERLAP]
                if shrinkable:
                    ov[max(shrinkable, key=lambda j: ov[j])] -= 2
                    changed = True
    for d in decisions:
        d["frames"] = ov.get(d["j"])
    return decisions


# --------------------------------------------------------------------------- whole-EDL analysis

def section_context(beats: Optional[dict], t: float, period: float) -> Optional[str]:
    """'up' / 'down' if a music section boundary falls within half a beat of time t, else None."""
    secs = (beats or {}).get("sections") or []
    for prev, nxt in zip(secs, secs[1:]):
        if abs(nxt["start"] - t) <= max(0.5 * period, 0.1):
            ep, en = prev.get("energy"), nxt.get("energy")
            if ep is None or en is None:
                return None
            if en > ep:
                return "up"
            if en < ep:
                return "down"
    return None


def edl_features(edl: dict, scores: dict, notes: Optional[dict] = None) -> list:
    sc, nt = scores_by_path(scores), notes_by_path(notes)
    feats = []
    for sh in edl["shots"]:
        key = os.path.normcase(os.path.abspath(sh["clip"]))
        feats.append(shot_features(sc.get(key), nt.get(key)))
    return feats


def colour_is_informative(feats: list) -> bool:
    """False when most shots share one dominant colour (a desert, a snowfield): then a 'colour match' is true of almost
    every cut and says nothing."""
    hues = [f["hue"] for f in feats if f.get("hue") is not None]
    if len(hues) < 4:
        return True
    best = 0
    for h in hues:
        near = sum(1 for g in hues if min(abs(h - g) % 1.0, 1.0 - abs(h - g) % 1.0) <= COMMON_HUE_NEAR)
        best = max(best, near)
    return best / len(hues) < COMMON_HUE_SHARE


def analyse(edl: dict, scores: dict, beats: Optional[dict] = None, notes: Optional[dict] = None,
            keep_existing: bool = False) -> list:
    """Decisions for boundaries 1..n-1 of `edl`: a list of {j, type, why, confidence, transition}.

    keep_existing: transitions already in the EDL stay, count against the budget and are not proposed again."""
    n = len(edl["shots"])
    bounds = ve.timeline(edl)
    fps = ve.project(edl)["fps"]
    feats = edl_features(edl, scores, notes)
    colour_ok = colour_is_informative(feats)
    drop_j = ve.drop_boundary(edl, bounds) if ve.merged_style(edl)["drop_flash"] else None
    period = float((edl.get("beats") or {}).get("period") or (beats or {}).get("period") or 0.5)
    out = []
    for j in range(1, n):
        ctx = {"is_drop": j == drop_j, "is_last": j == n - 1 and n >= 4,
               "section": section_context(beats, bounds[j] / fps, period), "colour_informative": colour_ok}
        d = classify(feats[j - 1], feats[j], ctx)
        d["j"] = j
        out.append(d)
    existing = {}
    if keep_existing:
        for k, v in (edl.get("transitions") or {}).items():
            if str(k).isdigit() and 1 <= int(k) <= n - 1:
                existing[int(k)] = v
    return fit_frames(choose(out, n - 1, ve.budget(edl), existing), bounds)


def apply(edl: dict, decisions: list, overwrite: bool = False) -> dict:
    """Write transitions and rationale for the decisions into `edl` (existing entries kept unless `overwrite`)."""
    trans = dict(edl.get("transitions") or {})
    rat = dict(edl.get("rationale") or {})
    tf = dict(edl.get("transition_frames") or {})
    if overwrite:
        trans, rat, tf = {}, {}, {}
    for d in decisions:
        k = str(d["j"])
        if k not in rat:
            entry = {"type": d["type"], "why": d["why"]}
            if d.get("kept_hard"):
                entry["why"] += " (a transition was wanted but the budget keeps it a hard cut)"
            rat[k] = entry
        if d.get("transition") and k not in trans:
            trans[k] = d["transition"]
            if d.get("frames") and d["frames"] != DEFAULT_FRAMES:
                tf[k] = d["frames"]
    edl["transitions"] = dict(sorted(trans.items(), key=lambda kv: int(kv[0])))
    edl["rationale"] = dict(sorted(rat.items(), key=lambda kv: int(kv[0])))
    if tf:
        edl["transition_frames"] = dict(sorted(tf.items(), key=lambda kv: int(kv[0])))
    else:
        edl.pop("transition_frames", None)
    return edl


# --------------------------------------------------------------------------- motion alignment (match on action)

def measure_windows(clip: str, duration: float, need: float, step: float = 0.75, max_windows: int = 8) -> list:
    """[(start, dx, dy, mag)] camera motion of up to `max_windows` windows of `need` seconds across the clip."""
    import vid_editorial
    info = vl.probe(clip)
    hdr = bool(info.get("hdr"))
    out = []
    t = 0.0
    while t + need <= duration + 1e-6 and len(out) < max_windows:
        frames = vid_editorial.gray_frames(clip, t, min(max(need, 0.3), 2.0), hdr=hdr)
        if frames:
            dx, dy, mag = vid_editorial.motion_vector(frames)
            out.append((round(t, 3), dx, dy, mag))
        t += step
    return out


def continues(direction: str, dx: float, dy: float) -> bool:
    if direction == "left":
        return dx <= -LATERAL_MIN
    if direction == "right":
        return dx >= LATERAL_MIN
    if direction == "up":
        return dy <= -LATERAL_MIN
    if direction == "down":
        return dy >= LATERAL_MIN
    return False


def align_start(candidates: list, direction: str, current: float) -> Optional[float]:
    """The start of the window whose movement continues `direction`, nearest to `current`; None if no window does."""
    ok = [c for c in candidates if continues(direction, c[1], c[2])]
    if not ok:
        return None
    return min(ok, key=lambda c: abs(c[0] - current))[0]


def align_edl(edl: dict, scores: dict, notes: Optional[dict] = None, measure=measure_windows) -> list:
    """Proposals [{j, direction, old, new}] that set `src_start` so shot j keeps moving the way shot j-1 ends.

    Only where shot j-1 clearly moves one way and shot j does not already continue it. `measure(clip, duration, need)`
    is injectable for tests."""
    feats = edl_features(edl, scores, notes)
    bounds = ve.timeline(edl)
    fps = ve.project(edl)["fps"]
    out = []
    for j in range(1, len(edl["shots"])):
        a, b = feats[j - 1], feats[j]
        d = direction_name(a) if moving(a) else None
        if not d or (moving(b) and direction_name(b) == d):
            continue
        shot = edl["shots"][j]
        need = (bounds[j + 1] - bounds[j]) / fps
        try:
            dur = float(vl.probe(shot["clip"])["duration"])
        except (vl.EngineError, KeyError, TypeError):
            continue
        cur = shot.get("src_start")
        cur = 0.4 * dur if cur is None else float(cur)
        new = align_start(measure(shot["clip"], dur, need), d, cur)
        if new is not None and abs(new - cur) > 0.05:
            out.append({"j": j, "direction": d, "old": round(cur, 3), "new": new})
    return out


def apply_alignment(edl: dict, proposals: list) -> dict:
    for p in proposals:
        edl["shots"][p["j"]]["src_start"] = p["new"]
    return edl


def format_table(decisions: list, edl: dict) -> str:
    lines = [f"{'cut':>3}  {'type':14} {'transition':14} {'conf':>4}  why"]
    for d in decisions:
        tr = d.get("transition")
        label = f"[{d['existing']}]" if d.get("existing") else "-" if not tr else (tr if d.get("frames") in (None, DEFAULT_FRAMES) else f"{tr} {d['frames']}f")
        lines.append(f"{d['j']:>3}  {d['type']:14} {label:14} {d['confidence']:>4.1f}  {d['why']}")
    types: dict = {}
    for d in decisions:
        types[d["type"]] = types.get(d["type"], 0) + 1
    lines.append("types: " + ", ".join(f"{k} {v}" for k, v in sorted(types.items())))
    return "\n".join(lines)


def cut_report(edl: dict) -> dict:
    """Counts of cut types and transitions with their stated reasons, from the EDL's `rationale` (for QA and the report)."""
    rat = edl.get("rationale") or {}
    types: dict = {}
    for v in rat.values():
        if isinstance(v, dict) and v.get("type"):
            types[v["type"]] = types.get(v["type"], 0) + 1
    trans = {int(k): v for k, v in (edl.get("transitions") or {}).items()}
    justified = sum(1 for k in trans if str(k) in rat and isinstance(rat[str(k)], dict) and rat[str(k)].get("why"))
    return {"cut_types": types, "transitions": len(trans), "transitions_with_reason": justified}
