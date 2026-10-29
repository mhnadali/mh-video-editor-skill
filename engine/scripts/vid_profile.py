"""Preferences that carry over: what the user likes, learned from what they decide about a storyboard.

When the user swaps a shot, bans a clip or says "more people, less food", that is information about their taste. It is kept in
a small `profile.json` (per project in the work folder, and optionally one global file) and applied the next time a story is
selected, so the engine does not make the same mistake twice. Nothing is learned silently: every change comes from an explicit
decision in a feedback file (the review page exports one), and the profile is plain JSON the user can read and edit.

profile.json:
    {"version": 1,
     "weights": {"people": 0.25},             # overrides of vid_story.WEIGHTS
     "topics": {"food": -0.08, "tyres": 0.04},   # added to a clip's score when its topic matches (-0.3 .. +0.3)
     "roles": {"action": 0.05},
     "banned": ["E:/footage/a.mov"],          # never used
     "favoured": ["E:/footage/b.mov"],        # a little more likely
     "pins": {"E:/footage/c.mov": 12},        # use this clip, in this slot of the storyboard it was pinned in
     "starts": {"E:/footage/c.mov": 4.2},     # the start the user chose for this clip on the review page
     "history": [{"when": "...", "action": "replace", "slot": 7, "from": 3, "to": 9}]}
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Optional

import vidlib as vl

VERSION = 1
BIAS_STEP = 0.04
BIAS_LIMIT = 0.30
FAVOUR = 0.15
ACTIONS = {"keep", "replace", "ban", "prefer-topic", "avoid-topic", "prefer-role", "avoid-role", "weight", "unban", "unpin", "start"}


def empty() -> dict:
    return {"version": VERSION, "weights": {}, "topics": {}, "roles": {}, "banned": [], "favoured": [], "pins": {}, "starts": {},
            "history": []}


def global_path() -> Path:
    env = os.environ.get("MH_PROFILE")
    return Path(env) if env else Path.home() / ".mh-video-skill" / "profile.json"


def load(path: str | os.PathLike | None) -> dict:
    if not path or not Path(path).is_file():
        return empty()
    data = vl.load_json(path)
    out = empty()
    for k in ("weights", "topics", "roles", "pins", "starts"):
        if isinstance(data.get(k), dict):
            out[k] = dict(data[k])
    for k in ("banned", "favoured", "history"):
        if isinstance(data.get(k), list):
            out[k] = list(data[k])
    try:
        validate(out)
    except vl.EngineError as e:
        raise vl.EngineError(f"{path}: {e}")
    return out


def merge(base: dict, over: dict) -> dict:
    """`over` wins over `base` (project over global)."""
    out = empty()
    for k in ("weights", "topics", "roles", "pins", "starts"):
        out[k] = {**base.get(k, {}), **over.get(k, {})}
    for k in ("banned", "favoured"):
        out[k] = sorted(set(base.get(k, [])) | set(over.get(k, [])))
    out["history"] = list(base.get("history", [])) + list(over.get("history", []))
    return out


def promote(project: dict, global_: dict, source: str = "") -> dict:
    """The global profile after taking in what a project taught: topic and role biases (averaged with what the global profile
    already holds, so one project cannot swing it) and weights (the project's win). Clip decisions (banned, favoured, pinned)
    belong to that project's footage and are never promoted."""
    out = load_dict(global_)
    for group in ("topics", "roles"):
        for k, v in project.get(group, {}).items():
            out[group][k] = round(_clamp((out[group][k] + v) / 2.0 if k in out[group] else v), 3)
    out["weights"].update(project.get("weights", {}))
    out["history"].append({"when": dt.datetime.now().isoformat(timespec="seconds"), "action": "promote", "from": source})
    validate(out)
    return out


def load_for(work: str | os.PathLike, path: str | os.PathLike | None = None, use_global: bool = True) -> dict:
    """The profile that applies to a work folder: the global one, then the project's (`work/profile.json` or `path`)."""
    prof = load(global_path()) if use_global else empty()
    return merge(prof, load(path or Path(work) / "profile.json"))


def save(profile: dict, path: str | os.PathLike) -> None:
    vl.save_json(path, profile)


def validate(profile: dict) -> dict:
    import vid_story
    for k in profile.get("weights", {}):
        if k not in vid_story.WEIGHTS:
            raise vl.EngineError(f"profile: unknown weight '{k}'")
    for group in ("topics", "roles"):
        for k, v in profile.get(group, {}).items():
            if not isinstance(v, (int, float)) or isinstance(v, bool) or abs(v) > BIAS_LIMIT + 1e-9:
                raise vl.EngineError(f"profile.{group}.{k}: a number between -{BIAS_LIMIT} and {BIAS_LIMIT}")
    for k, v in profile.get("weights", {}).items():
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise vl.EngineError(f"profile.weights.{k}: a number")
    for p, slot in profile.get("pins", {}).items():
        if not isinstance(slot, int) or isinstance(slot, bool) or slot < 0:
            raise vl.EngineError(f"profile.pins: the slot of '{p}' must be a whole number >= 0")
    for p, s in profile.get("starts", {}).items():
        if not isinstance(s, (int, float)) or isinstance(s, bool) or s < 0:
            raise vl.EngineError(f"profile.starts: the start of '{p}' must be a number of seconds >= 0")
    for k in ("banned", "favoured"):
        if not all(isinstance(p, str) for p in profile.get(k, [])):
            raise vl.EngineError(f"profile.{k}: a list of file paths")
    return profile


# --------------------------------------------------------------------------- applying a profile

def norm(path: str) -> str:
    """One spelling of a path (slashes, case on Windows) so a hand-edited profile still matches the inventory."""
    return os.path.normcase(os.path.abspath(path))


def clip_bias(profile: dict, view: dict) -> float:
    """What the profile adds to (or takes from) a clip's score."""
    v = 0.0
    if view.get("topic"):
        v += profile.get("topics", {}).get(view["topic"], 0.0)
    if view.get("role"):
        v += profile.get("roles", {}).get(view["role"], 0.0)
    if view.get("path") and norm(view["path"]) in {norm(p) for p in profile.get("favoured", [])}:
        v += FAVOUR
    return v


def banned_indexes(profile: dict, inventory: list) -> set:
    paths = {norm(p) for p in profile.get("banned", [])}
    return {r["index"] for r in inventory if norm(r["path"]) in paths}


def pinned(profile: dict, inventory: list) -> dict:
    """{clip index: slot} for clips the user pinned."""
    by_path = {norm(r["path"]): r["index"] for r in inventory}
    out = {}
    for p, slot in profile.get("pins", {}).items():
        i = by_path.get(norm(p))
        if i is not None:
            out[i] = int(slot)
    return out


# --------------------------------------------------------------------------- learning from feedback

def _clamp(x: float) -> float:
    return max(-BIAS_LIMIT, min(BIAS_LIMIT, x))


def _bump(d: dict, key: Optional[str], delta: float) -> None:
    if key:
        d[key] = round(_clamp(d.get(key, 0.0) + delta), 3)


def validate_feedback(fb: dict) -> list:
    if not isinstance(fb, dict) or not isinstance(fb.get("decisions"), list):
        raise vl.EngineError('feedback file: expected {"decisions": [{"action": ..., ...}, ...]}')
    for d in fb["decisions"]:
        if not isinstance(d, dict) or d.get("action") not in ACTIONS:
            raise vl.EngineError(f"feedback: action must be one of {', '.join(sorted(ACTIONS))}, got {d!r}")
        if d["action"] == "replace" and not (isinstance(d.get("slot"), int) and isinstance(d.get("with"), int)):
            raise vl.EngineError("feedback: 'replace' needs an integer 'slot' and an integer 'with' (the new clip's index)")
        if d["action"] in ("ban", "unban") and not isinstance(d.get("clip"), int):
            raise vl.EngineError(f"feedback: '{d['action']}' needs an integer 'clip'")
        if d["action"] in ("prefer-topic", "avoid-topic") and not isinstance(d.get("topic"), str):
            raise vl.EngineError(f"feedback: '{d['action']}' needs a 'topic'")
        if d["action"] in ("prefer-role", "avoid-role") and not isinstance(d.get("role"), str):
            raise vl.EngineError(f"feedback: '{d['action']}' needs a 'role'")
        if d["action"] == "start":
            if not (isinstance(d.get("slot"), int) and isinstance(d.get("start"), (int, float)) and not isinstance(d["start"], bool)
                    and d["start"] >= 0):
                raise vl.EngineError("feedback: 'start' needs an integer 'slot' and a 'start' in seconds (0 or more)")
            if d.get("clip") is not None and not isinstance(d["clip"], int):
                raise vl.EngineError("feedback: the 'clip' of a 'start' must be an integer")
        if d["action"] == "weight" and not (isinstance(d.get("name"), str) and isinstance(d.get("value"), (int, float))):
            raise vl.EngineError("feedback: 'weight' needs a 'name' and a numeric 'value'")
    return fb["decisions"]


def learn(profile: dict, storyboard: dict, feedback: dict, inventory: list, views: dict, min_duration: float = 5.0) -> tuple:
    """Apply a feedback file: returns (new profile, new storyboard rows). Nothing is changed in place.

    `views` = {clip index: vid_story.clip_view(...)} for every clip that may be named in the feedback.
    replace: the row takes the new clip (never one used in another slot, a banned one or one under `min_duration`); the topic
    and role of the old clip lose a little, those of the new one gain, and the new clip becomes favoured. keep: the clip is
    pinned to that slot. ban / unban: a clip is never / may again be used; a slot that uses a banned clip takes its first
    usable alternate (else it is marked `banned` and the next `story select` replaces it). prefer-topic / avoid-topic /
    prefer-role / avoid-role: a step of the bias. weight: sets a weight. start: the shot of a slot starts at this second of its
    clip (it must be inside the clip; a start meant for a clip that is no longer in the slot is ignored).
    Replacements are applied first (so a `start` meant for the clip they replace is recognised and ignored), then keeps, starts and
    biases, and bans last, so a clip the user replaced and banned does not teach
    the engine to dislike its replacement."""
    prof = load_dict(profile)
    rows = [dict(r) for r in storyboard["rows"]]
    by_idx = {r["index"]: r for r in inventory}
    now = dt.datetime.now().isoformat(timespec="seconds")
    decisions = validate_feedback(feedback)
    order = sorted(decisions, key=lambda d: 0 if d["action"] == "replace" else 2 if d["action"] in ("ban", "unban") else 1)

    def slot_row(n):
        row = next((r for r in rows if r["slot"] == n), None)
        if row is None:
            raise vl.EngineError(f"feedback: the storyboard has no slot {n}")
        return row

    def path_of(i):
        if i not in by_idx:
            raise vl.EngineError(f"feedback: clip {i} is not in the inventory")
        return by_idx[i]["path"]

    changed: set = set()                                          # slots whose clip changed

    def take(row, nv, kind, why):
        row.pop("moment", None)                                   # it described the old clip
        row.pop("banned", None)
        changed.add(row["slot"])
        row.update(clip=nv["index"], name=nv["name"], taken=nv["taken"], device=nv["device"], role=nv["role"], size=nv["size"],
                   people=nv["people"], topic=nv["topic"], energy=round(nv["act"], 2), path=nv["path"],
                   start=round(nv["best_start"], 2), start_kind=kind, why=why)

    for d in order:
        act = d["action"]
        entry = {"when": now, "action": act}
        if act == "replace":
            slot, new = d["slot"], d["with"]
            row = slot_row(slot)
            if new not in views:
                raise vl.EngineError(f"feedback: clip {new} is unknown or was not scored")
            nv = views[new]
            if any(r["clip"] == new and r["slot"] != slot for r in rows):
                raise vl.EngineError(f"feedback: clip {new} is already used in another slot; a clip appears once")
            if norm(nv["path"]) in {norm(p) for p in prof["banned"]}:
                raise vl.EngineError(f"feedback: clip {new} is banned")
            if (nv.get("duration") or 0.0) < min_duration - 0.1:
                raise vl.EngineError(f"feedback: clip {new} lasts {nv.get('duration', 0):.1f} s, under the {min_duration:g} s minimum")
            old = views.get(row["clip"])
            if old:
                _bump(prof["topics"], old.get("topic"), -BIAS_STEP)
                _bump(prof["roles"], old.get("role"), -BIAS_STEP / 2)
            _bump(prof["topics"], nv.get("topic"), BIAS_STEP)
            _bump(prof["roles"], nv.get("role"), BIAS_STEP / 2)
            if norm(nv["path"]) not in {norm(p) for p in prof["favoured"]}:
                prof["favoured"].append(nv["path"])
            prof["pins"] = {p: s for p, s in prof["pins"].items() if norm(p) != norm(row["path"])}      # the replaced clip is released
            prof["pins"][nv["path"]] = slot                                                              # the chosen one is kept
            take(row, nv, "chosen by the user", f"chosen by the user (replaced clip {old['index'] if old else '?'})")
            row["alternates"] = []
            entry.update(slot=slot, **{"from": old["index"] if old else None, "to": new})
        elif act == "ban":
            path = path_of(d["clip"])
            if norm(path) not in {norm(p) for p in prof["banned"]}:
                prof["banned"].append(path)
            prof["pins"] = {p: s for p, s in prof["pins"].items() if norm(p) != norm(path)}
            prof["favoured"] = [p for p in prof["favoured"] if norm(p) != norm(path)]
            entry["clip"] = d["clip"]
        elif act == "unban":
            path = path_of(d["clip"])
            prof["banned"] = [p for p in prof["banned"] if norm(p) != norm(path)]
            entry["clip"] = d["clip"]
        elif act == "start":
            row = slot_row(d["slot"])
            if d.get("clip") is not None and d["clip"] != row["clip"]:
                entry["action"] = "start-ignored"                 # it was set for a clip that has since been replaced
                entry["slot"] = row["slot"]
            else:
                dur = (views.get(row["clip"]) or {}).get("duration") or 0.0
                if dur and d["start"] > dur - 0.3:
                    raise vl.EngineError(f"feedback: slot {row['slot']}: a start of {d['start']:g} s leaves nothing of a {dur:.1f} s clip")
                row.pop("moment", None)
                row["start"] = round(float(d["start"]), 2)
                row["start_kind"] = "set by the user"
                prof["starts"] = {p: s for p, s in prof["starts"].items() if norm(p) != norm(row["path"])}
                prof["starts"][row["path"]] = row["start"]                  # remembered: the next `story select` uses it
                prof["pins"][row["path"]] = row["slot"]                     # choosing its start means keeping the clip
                entry.update(slot=row["slot"], start=row["start"])
        elif act == "keep":
            row = slot_row(d.get("slot"))
            prof["pins"][row["path"]] = row["slot"]
            entry["slot"] = row["slot"]
        elif act == "unpin":
            row = slot_row(d.get("slot"))
            prof["pins"] = {p: s for p, s in prof["pins"].items() if norm(p) != norm(row["path"])}
            entry["slot"] = row["slot"]
        elif act in ("prefer-topic", "avoid-topic"):
            _bump(prof["topics"], d["topic"], BIAS_STEP if act == "prefer-topic" else -BIAS_STEP)
            entry["topic"] = d["topic"]
        elif act in ("prefer-role", "avoid-role"):
            _bump(prof["roles"], d["role"], BIAS_STEP if act == "prefer-role" else -BIAS_STEP)
            entry["role"] = d["role"]
        elif act == "weight":
            prof["weights"][d["name"]] = float(d["value"])
            entry["name"] = d["name"]
        prof["history"].append(entry)
    # every slot that still uses a banned clip (banned now or earlier) takes its first usable alternate, or is marked
    banned_now = {norm(p) for p in prof["banned"]}
    for row in rows:
        if norm(row["path"]) not in banned_now:
            if row.pop("banned", None):
                row["why"] = "the ban was lifted"
            continue
        used = {r["clip"] for r in rows}
        alt = next((a for a in row.get("alternates") or []
                    if a in views and a not in used and norm(views[a]["path"]) not in banned_now
                    and (views[a].get("duration") or 0.0) >= min_duration - 0.1), None)
        if alt is None:
            row["banned"] = True
            row["why"] = "BANNED by the user: run `story select` again to fill this slot"
        else:
            was = row["clip"]
            take(row, views[alt], "alternate after a ban", f"alternate: clip {was} was banned")
            row["alternates"] = [a for a in row["alternates"] if a != alt]
    # "looks like slot N" notes no longer hold for a slot whose clip changed (the other side is not known any more)
    for row in rows:
        if row["slot"] in changed:
            row["lookalike"] = []
        elif row.get("lookalike"):
            row["lookalike"] = [s for s in row["lookalike"] if s not in changed]
    validate(prof)
    return prof, rows


def start_of(profile: dict, path: str) -> Optional[float]:
    """The start the user chose for this clip, or None."""
    starts = {norm(p): s for p, s in (profile or {}).get("starts", {}).items()}
    return starts.get(norm(path))


def load_dict(profile: dict) -> dict:
    out = empty()
    for k in ("weights", "topics", "roles", "pins", "starts"):
        out[k] = dict(profile.get(k, {}))
    for k in ("banned", "favoured", "history"):
        out[k] = list(profile.get(k, []))
    return out


def describe(profile: dict) -> str:
    lines = []
    if profile["topics"]:
        lines.append("topics: " + ", ".join(f"{k} {v:+.2f}" for k, v in sorted(profile["topics"].items())))
    if profile["roles"]:
        lines.append("roles: " + ", ".join(f"{k} {v:+.2f}" for k, v in sorted(profile["roles"].items())))
    if profile["weights"]:
        lines.append("weights: " + ", ".join(f"{k}={v}" for k, v in sorted(profile["weights"].items())))
    lines.append(f"{len(profile['banned'])} banned, {len(profile['favoured'])} favoured, {len(profile['pins'])} pinned, "
                 f"{len(profile.get('starts', {}))} start(s) chosen, "
                 f"{len(profile['history'])} decisions remembered")
    return "\n".join(lines)
