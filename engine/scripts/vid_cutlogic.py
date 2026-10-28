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
HARD_TYPES = {"cut-on-action", "graphic-match", "contrast-cut", "size-step", "montage-cut", "direction-clash",
              "jump-risk"}
ALL_TYPES = set(PRIORITY) | HARD_TYPES


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
         "mood": n.get("mood")}
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
        ra, rb = SIZE_RANK.get(a.get("size")), SIZE_RANK.get(b.get("size"))
        if dist <= FOCUS_NEAR and (ra is None or rb is None or abs(ra - rb) <= 1):
            return {"type": "graphic-match", "transition": None, "confidence": 0.6,
                    "why": "the point of interest sits in the same place in both shots: the eye does not have to move"}
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
    if both_calm and ba is not None and bb is not None and (abs(ba - bb) >= 25 or abs((a.get("saturation") or 0) - (b.get("saturation") or 0)) >= 25):
        return {"type": "calm-dissolve", "transition": "dissolve", "confidence": 0.5,
                "why": "calm shots with a change of light or colour: a dissolve eases the change"}
    ra, rb = SIZE_RANK.get(a.get("size")), SIZE_RANK.get(b.get("size"))
    if ra is not None and rb is not None and ra != rb:
        return {"type": "size-step", "transition": None, "confidence": 0.6,
                "why": f"{a['size']} to {b['size']}: a change of shot size is a clean cut that moves the story"}
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


def choose(decisions: list, n_cuts: int, bud: dict) -> list:
    """Apply the transition budget: the highest-priority wishes win, never more than the share cap, never two
    neighbouring transitions unless one is the drop or the closing, every name within the repeat and family caps."""
    cap = min(int(n_cuts * bud["max_transition_share"] + 1e-9), n_cuts)
    cap = min(cap, max(1, int(n_cuts * 0.45))) if n_cuts else 0
    order = sorted((i for i, d in enumerate(decisions) if d.get("transition")),
                   key=lambda i: (-PRIORITY.get(decisions[i]["type"], 0), -decisions[i]["confidence"], i))
    used: dict = {}
    fam: dict = {}
    taken: set = set()
    for i in order:
        d = decisions[i]
        forced = d["type"] in ("impact", "closing")
        if len(taken) >= cap and not forced:
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


def analyse(edl: dict, scores: dict, beats: Optional[dict] = None, notes: Optional[dict] = None) -> list:
    """Decisions for boundaries 1..n-1 of `edl`: a list of {j, type, why, confidence, transition}."""
    shots = edl["shots"]
    n = len(shots)
    bounds = ve.timeline(edl)
    fps = ve.project(edl)["fps"]
    sc, nt = scores_by_path(scores), notes_by_path(notes)
    feats = []
    for sh in shots:
        key = os.path.normcase(os.path.abspath(sh["clip"]))
        feats.append(shot_features(sc.get(key), nt.get(key)))
    drop_j = ve.drop_boundary(edl, bounds) if ve.merged_style(edl)["drop_flash"] else None
    period = float((edl.get("beats") or {}).get("period") or (beats or {}).get("period") or 0.5)
    out = []
    for j in range(1, n):
        ctx = {"is_drop": j == drop_j, "is_last": j == n - 1 and n >= 4,
               "section": section_context(beats, bounds[j] / fps, period)}
        d = classify(feats[j - 1], feats[j], ctx)
        d["j"] = j
        out.append(d)
    return choose(out, n - 1, ve.budget(edl))


def apply(edl: dict, decisions: list, overwrite: bool = False) -> dict:
    """Write transitions and rationale for the decisions into `edl` (existing entries kept unless `overwrite`)."""
    trans = dict(edl.get("transitions") or {})
    rat = dict(edl.get("rationale") or {})
    if overwrite:
        trans, rat = {}, {}
    for d in decisions:
        k = str(d["j"])
        if k not in rat:
            entry = {"type": d["type"], "why": d["why"]}
            if d.get("kept_hard"):
                entry["why"] += " (a transition was wanted but the budget keeps it a hard cut)"
            rat[k] = entry
        if d.get("transition") and k not in trans:
            trans[k] = d["transition"]
    edl["transitions"] = dict(sorted(trans.items(), key=lambda kv: int(kv[0])))
    edl["rationale"] = dict(sorted(rat.items(), key=lambda kv: int(kv[0])))
    return edl


def format_table(decisions: list, edl: dict) -> str:
    lines = [f"{'cut':>3}  {'type':14} {'transition':11} {'conf':>4}  why"]
    for d in decisions:
        lines.append(f"{d['j']:>3}  {d['type']:14} {(d.get('transition') or '-'):11} {d['confidence']:>4.1f}  {d['why']}")
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
