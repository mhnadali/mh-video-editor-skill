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
ACTIONS = {"keep", "replace", "ban", "prefer-topic", "avoid-topic", "prefer-role", "avoid-role", "weight", "unban", "unpin"}


def empty() -> dict:
    return {"version": VERSION, "weights": {}, "topics": {}, "roles": {}, "banned": [], "favoured": [], "pins": {}, "history": []}


def global_path() -> Path:
    env = os.environ.get("MH_PROFILE")
    return Path(env) if env else Path.home() / ".mh-video-skill" / "profile.json"


def load(path: str | os.PathLike | None) -> dict:
    if not path or not Path(path).is_file():
        return empty()
    data = vl.load_json(path)
    out = empty()
    for k in ("weights", "topics", "roles", "pins"):
        if isinstance(data.get(k), dict):
            out[k] = dict(data[k])
    for k in ("banned", "favoured", "history"):
        if isinstance(data.get(k), list):
            out[k] = list(data[k])
    return out


def merge(base: dict, over: dict) -> dict:
    """`over` wins over `base` (project over global)."""
    out = empty()
    for k in ("weights", "topics", "roles", "pins"):
        out[k] = {**base.get(k, {}), **over.get(k, {})}
    for k in ("banned", "favoured"):
        out[k] = sorted(set(base.get(k, [])) | set(over.get(k, [])))
    out["history"] = list(base.get("history", [])) + list(over.get("history", []))
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
    return profile


# --------------------------------------------------------------------------- applying a profile

def clip_bias(profile: dict, view: dict) -> float:
    """What the profile adds to (or takes from) a clip's score."""
    v = 0.0
    if view.get("topic"):
        v += profile.get("topics", {}).get(view["topic"], 0.0)
    if view.get("role"):
        v += profile.get("roles", {}).get(view["role"], 0.0)
    if view.get("path") in set(profile.get("favoured", [])):
        v += FAVOUR
    return v


def banned_indexes(profile: dict, inventory: list) -> set:
    paths = {os.path.normcase(os.path.abspath(p)) for p in profile.get("banned", [])}
    return {r["index"] for r in inventory if os.path.normcase(os.path.abspath(r["path"])) in paths}


def pinned(profile: dict, inventory: list) -> dict:
    """{clip index: slot} for clips the user pinned."""
    by_path = {os.path.normcase(os.path.abspath(r["path"])): r["index"] for r in inventory}
    out = {}
    for p, slot in profile.get("pins", {}).items():
        i = by_path.get(os.path.normcase(os.path.abspath(p)))
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
        if d["action"] == "weight" and not (isinstance(d.get("name"), str) and isinstance(d.get("value"), (int, float))):
            raise vl.EngineError("feedback: 'weight' needs a 'name' and a numeric 'value'")
    return fb["decisions"]


def learn(profile: dict, storyboard: dict, feedback: dict, inventory: list, views: dict) -> tuple:
    """Apply a feedback file: returns (new profile, new storyboard rows).

    `views` = {clip index: vid_story.clip_view(...)} for every clip that may be named in the feedback.
    replace: the row takes the new clip; the topic and role of the old clip lose a little, those of the new one gain, and the
    new clip becomes favoured. ban/unban: a clip is never / may again be used. keep: the clip is pinned to that slot.
    prefer-topic / avoid-topic / prefer-role / avoid-role: a step of the bias. weight: sets a weight."""
    prof = load_dict(profile)
    rows = [dict(r) for r in storyboard["rows"]]
    by_idx = {r["index"]: r for r in inventory}
    now = dt.datetime.now().isoformat(timespec="seconds")
    for d in validate_feedback(feedback):
        act = d["action"]
        entry = {"when": now, "action": act}
        if act == "replace":
            slot, new = d["slot"], d["with"]
            row = next((r for r in rows if r["slot"] == slot), None)
            if row is None:
                raise vl.EngineError(f"feedback: the storyboard has no slot {slot}")
            if new not in views:
                raise vl.EngineError(f"feedback: clip {new} is unknown")
            old = views.get(row["clip"])
            nv = views[new]
            if old:
                _bump(prof["topics"], old.get("topic"), -BIAS_STEP)
                _bump(prof["roles"], old.get("role"), -BIAS_STEP / 2)
            _bump(prof["topics"], nv.get("topic"), BIAS_STEP)
            _bump(prof["roles"], nv.get("role"), BIAS_STEP / 2)
            if nv["path"] not in prof["favoured"]:
                prof["favoured"].append(nv["path"])
            row.update(clip=new, name=nv["name"], taken=nv["taken"], device=nv["device"], role=nv["role"], size=nv["size"],
                       people=nv["people"], topic=nv["topic"], energy=round(nv["act"], 2), path=nv["path"],
                       start=round(nv["best_start"], 2), start_kind="chosen by the user", why="chosen by the user (replaced clip "
                       f"{old['index'] if old else '?'})", alternates=[])
            entry.update(slot=slot, **{"from": old["index"] if old else None, "to": new})
        elif act == "ban":
            path = by_idx[d["clip"]]["path"] if d["clip"] in by_idx else None
            if path and path not in prof["banned"]:
                prof["banned"].append(path)
            entry["clip"] = d["clip"]
        elif act == "unban":
            path = by_idx[d["clip"]]["path"] if d["clip"] in by_idx else None
            prof["banned"] = [p for p in prof["banned"] if p != path]
            entry["clip"] = d["clip"]
        elif act == "keep":
            row = next((r for r in rows if r["slot"] == d.get("slot")), None)
            if row is not None:
                prof["pins"][row["path"]] = row["slot"]
                entry["slot"] = row["slot"]
        elif act == "unpin":
            row = next((r for r in rows if r["slot"] == d.get("slot")), None)
            if row is not None:
                prof["pins"].pop(row["path"], None)
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
    validate(prof)
    return prof, rows


def load_dict(profile: dict) -> dict:
    out = empty()
    for k in ("weights", "topics", "roles", "pins"):
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
                 f"{len(profile['history'])} decisions remembered")
    return "\n".join(lines)
