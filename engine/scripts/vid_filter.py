"""Hard filters and "same moment" clusters: what the selection must never choose, and why.

Two kinds of clips are taken out before any choice is made:

1. **Unusable clips** (`usable`): shorter than `min_duration` (5 s by default: a clip that short is almost always an
   accident or an unfinished take), unreadable, copies, too dark, too blurry, covered lens or a pocket shot, too shaky,
   containing a cut or a flash of its own, or marked `skip` by the agent.
2. **Redundant clips** (`same_moment_clusters`): clips of the same scene filmed at the same time in which nothing differs.
   The best of each cluster stays (`best_of`), the rest are dropped. Two cameras filming the same moment count as one
   moment; only the better clip is kept.

Every exclusion has a reason code, so the agent can show "why not" for any clip (`exclusions` / `story exclusions`).
Nothing here deletes or touches a file.
"""
from __future__ import annotations

import os
from typing import Optional

import vid_cutlogic as cl
import vid_editorial as ed
import vidlib as vl

DEFAULT_FILTERS = {
    "min_duration": 5.0,          # seconds; the clip must last about this long ("about": see duration_tolerance)
    "duration_tolerance": 0.1,    # a 4.95 s clip counts as 5 s
    "max_shake": 3.0,             # camera jitter (editorial.jitter) above which a clip is "shaky"
    "min_texture": 0.8,           # mean gradient below which the picture is flat (covered lens, a wall, the ground)
    "block_flags": ["dark", "blurry"],
    "block_internal_cut": True,
    "block_covered": True,
    "block_shaky": True,
    "min_quality": 35.0,
}

DEFAULT_DEDUPE = {
    "enabled": True,
    "window_s": 150.0,            # clips started within this many seconds of each other can be the same moment
    "hash_bits": 12,              # Hamming distance (of 64) between picture hashes that still means "the same picture"
    "frames_matching": 2,         # how many of the 3 frames of a window must match
    "colour": 20.0,               # colour signature distance (0-255) below which the colours agree
    "brightness": 10.0,           # brightness difference below which the light agrees (30 wrongly joined two cameras of one drive)
    "cross_camera_window_s": 90.0,
    # a second, looser reading that never drops a clip: two clips that match this way are only FLAGGED as look-alikes (the
    # storyboard warns when both are chosen). Measured on a real trip, desert clips from one drive look alike without being
    # the same shot, so the strict reading above is the only one that removes anything.
    "lookalike_hash_bits": 16,
    "lookalike_colour": 8.0,
    "lookalike_brightness": 6.0,
}


def merged(defaults: dict, given: Optional[dict]) -> dict:
    out = dict(defaults)
    out.update({k: v for k, v in (given or {}).items() if k in defaults})
    return out


# --------------------------------------------------------------------------- unusable clips

def usable(rec: dict, score: Optional[dict], note: Optional[dict] = None, cfg: Optional[dict] = None,
           must: bool = False) -> list:
    """Reasons a clip cannot be used (empty list = usable). `must` (a clip the user insisted on) skips the soft reasons."""
    f = merged(DEFAULT_FILTERS, cfg)
    why: list = []
    if rec.get("error"):
        return ["unreadable"]
    if rec.get("dup_of") is not None:
        return ["copy"]
    dur = rec.get("duration") or 0.0
    if dur < f["min_duration"] - f["duration_tolerance"]:
        why.append("short")
    if (note or {}).get("skip"):
        why.append("skipped")
    if must:                                           # the user's insistence overrides the soft reasons, never the length rule
        return [w for w in why if w in ("skipped", "short")]
    if not score or "quality" not in score:
        why.append("not-scored")
        return why
    flags = score.get("flags") or []
    for fl in f["block_flags"]:
        if fl in flags:
            why.append(fl)
    if f["block_internal_cut"] and score.get("internal_cut"):
        why.append("internal-cut")
    shake = score.get("shake")
    if f["block_shaky"] and shake is not None and shake > f["max_shake"]:
        why.append("shaky")
    tex = score.get("texture")
    if f["block_covered"] and tex is not None and tex < f["min_texture"] and \
            ((score.get("brightness") or 128) < 70 or "static" in flags):
        why.append("covered")
    if (score.get("quality") or 0) < f["min_quality"]:
        why.append("low-quality")
    return why


# --------------------------------------------------------------------------- the same moment

def quality_score(rec: dict, score: dict, note: Optional[dict] = None) -> float:
    """One number (0-1) for "which of these clips is the best take": quality, steadiness, sharpness, exposure, length and
    the clip's own sound, and the agent's judgement when it noted a mood or a role."""
    q = (score.get("quality") or 0.0) / 100.0
    shake = score.get("shake")
    steady = 1.0 if shake is None else max(0.0, 1.0 - shake / 6.0)
    blur = score.get("blur")
    sharp = 0.7 if blur is None else max(0.0, min(1.0, 1.0 - (blur - 2.0) / 8.0))
    bright = score.get("brightness")
    expo = 0.7 if bright is None else max(0.0, 1.0 - abs(bright - 120.0) / 120.0)
    length = min((rec.get("duration") or 0.0), 20.0) / 20.0
    snd = (score.get("sound") or 0.0) / 100.0
    s = 0.34 * q + 0.20 * steady + 0.14 * sharp + 0.12 * expo + 0.12 * length + 0.08 * snd
    if (note or {}).get("role") or (note or {}).get("mood"):
        s += 0.02                                        # the agent looked at this clip
    return round(s, 4)


def _hash_distance(a: Optional[list], b: Optional[list]) -> Optional[list]:
    if not a or not b:
        return None
    return sorted(bin(int(x) ^ int(y)).count("1") for x, y in zip(a, b))


def pair_similarity(a: dict, b: dict, sa: dict, sb: dict) -> dict:
    """How alike two clips look: the Hamming distances of the three window frames (sorted), the colour signature distance,
    the brightness difference and whether the rough scene content agrees."""
    hd = _hash_distance(sa.get("hashes"), sb.get("hashes"))
    if hd is None and sa.get("hash") is not None and sb.get("hash") is not None:
        hd = [bin(int(sa["hash"]) ^ int(sb["hash"])).count("1")]
    col = ed.colour_distance(sa.get("csig") or [], sb.get("csig") or [])
    br = abs((sa.get("brightness") or 0.0) - (sb.get("brightness") or 0.0))
    return {"hash": hd, "colour": col, "brightness": br,
            "scene": sa.get("scene") is not None and sa.get("scene") == sb.get("scene")}


def same_moment(a: dict, b: dict, sa: dict, sb: dict, cfg: Optional[dict] = None) -> bool:
    return similarity_level(a, b, sa, sb, cfg) == "same"


def similarity_level(a: dict, b: dict, sa: dict, sb: dict, cfg: Optional[dict] = None) -> Optional[str]:
    """'same' (the strict reading: one of the two is dropped), 'lookalike' (the looser reading: flagged only) or None."""
    d = merged(DEFAULT_DEDUPE, cfg)
    ta, tb = a.get("taken_epoch") or 0.0, b.get("taken_epoch") or 0.0
    cross = (a.get("device") or "") != (b.get("device") or "")
    gap = abs(ta - tb)
    if gap > (d["cross_camera_window_s"] if cross else d["window_s"]):
        return None
    sim = pair_similarity(a, b, sa, sb)
    hd = sim["hash"]
    if not hd:
        return None
    need = min(d["frames_matching"], len(hd))
    if sum(1 for x in hd if x <= d["hash_bits"]) >= need and sim["colour"] <= d["colour"] and sim["brightness"] <= d["brightness"]:
        return "same"
    if sum(1 for x in hd if x <= d["lookalike_hash_bits"]) >= need and sim["colour"] <= d["lookalike_colour"] \
            and sim["brightness"] <= d["lookalike_brightness"]:
        return "lookalike"
    return None


def lookalikes(chosen: list, inventory: list, scores: dict, cfg: Optional[dict] = None) -> list:
    """Pairs [i, j] among the chosen clips that look alike (the strict or the looser reading). The selection warns about them:
    nothing is dropped automatically, the user decides on the review page."""
    by = {r["index"]: r for r in inventory}
    out = []
    ids = [i for i in chosen if i in by and i in scores]
    for x, i in enumerate(ids):
        for j in ids[x + 1:]:
            if similarity_level(by[i], by[j], scores[i], scores[j], cfg):
                out.append([i, j])
    return out


def same_moment_clusters(inventory: list, scores: dict, cfg: Optional[dict] = None, only: Optional[set] = None) -> list:
    """Groups (lists of inventory indexes, 2 or more) of clips that show the same moment. Union-find over clips sorted by
    capture time; only clips within the time window are compared, so a thousand clips stay cheap."""
    d = merged(DEFAULT_DEDUPE, cfg)
    if not d["enabled"]:
        return []
    recs = sorted((r for r in inventory if r.get("dup_of") is None and not r.get("error") and r["index"] in scores
                   and (only is None or r["index"] in only)),
                  key=lambda r: (r.get("taken_epoch") or 0.0, r["index"]))
    parent = {r["index"]: r["index"] for r in recs}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    horizon = max(d["window_s"], d["cross_camera_window_s"])
    for i, a in enumerate(recs):
        ta = a.get("taken_epoch") or 0.0
        for b in recs[i + 1:]:
            if (b.get("taken_epoch") or 0.0) - ta > horizon:
                break
            if same_moment(a, b, scores[a["index"]], scores[b["index"]], d):
                parent[find(b["index"])] = find(a["index"])
    groups: dict = {}
    for r in recs:
        groups.setdefault(find(r["index"]), []).append(r["index"])
    return sorted((sorted(g) for g in groups.values() if len(g) > 1), key=lambda g: g[0])


def best_of(cluster: list, inventory: list, scores: dict, notes: Optional[dict] = None, prefer: Optional[set] = None) -> int:
    """The index of the best take of a cluster. A take the user chose (`prefer`) beats a better-measured one."""
    prefer = prefer or set()
    by = {r["index"]: r for r in inventory}
    nt = cl.notes_by_path(notes)

    def key(i: int):
        rec = by[i]
        note = nt.get(os.path.normcase(os.path.abspath(rec["path"]))) or {}
        skipped = 1 if note.get("skip") else 0
        return (-skipped, 1 if i in prefer else 0, quality_score(rec, scores[i], note), rec.get("duration") or 0.0, -i)

    return max(cluster, key=key)


# --------------------------------------------------------------------------- all exclusions

def exclusions(inventory: list, scores: dict, notes: Optional[dict] = None, filters: Optional[dict] = None,
               dedupe: Optional[dict] = None, must: Optional[set] = None, only: Optional[set] = None,
               prefer: Optional[set] = None) -> dict:
    """{index: [reasons]} for every clip that must not be chosen, plus the clusters.

    Returns {"reasons": {index: [codes]}, "clusters": [{"keep": i, "drop": [...], "members": [...]}]}. A reason is a code
    (short, copy, dark, blurry, shaky, covered, internal-cut, low-quality, not-scored, skipped, unreadable) or
    `same-moment:N` (a better take, clip N, exists)."""
    nt = cl.notes_by_path(notes)
    must = must or set()
    reasons: dict = {}
    for r in inventory:
        i = r["index"]
        if only is not None and i not in only:
            continue
        if r.get("dup_of") is not None:
            continue                                      # an exact copy is not a candidate at all (the inventory already folds it)
        note = nt.get(os.path.normcase(os.path.abspath(r["path"]))) or {}
        why = usable(r, scores.get(i), note, filters, must=i in must)
        if why:
            reasons[i] = why
    live = {i: s for i, s in scores.items() if i not in reasons}
    clusters = []
    for group in same_moment_clusters(inventory, live, dedupe, only):
        keep = best_of(group, inventory, live, notes, prefer)
        drop = [i for i in group if i != keep and i not in must]
        for i in drop:
            reasons.setdefault(i, []).append(f"same-moment:{keep}")
        clusters.append({"keep": keep, "drop": drop, "members": group})
    return {"reasons": reasons, "clusters": clusters}


def summarize(result: dict, total: int) -> dict:
    counts: dict = {}
    for why in result["reasons"].values():
        for w in why:
            code = w.split(":")[0]
            counts[code] = counts.get(code, 0) + 1
    return {"clips": total, "excluded": len(result["reasons"]), "usable": total - len(result["reasons"]),
            "by_reason": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
            "same_moment_clusters": len(result["clusters"])}


def format_report(result: dict, inventory: list, limit: int = 40) -> str:
    by = {r["index"]: r for r in inventory}
    s = summarize(result, sum(1 for r in inventory if r.get("dup_of") is None))
    lines = [f"{s['usable']} usable of {s['clips']} clips; {s['excluded']} excluded: " +
             ", ".join(f"{k} {v}" for k, v in s["by_reason"].items()),
             f"{s['same_moment_clusters']} same-moment groups (only the best take of each is used)"]
    for c in result["clusters"][:limit]:
        keep = by[c["keep"]]
        lines.append(f"  moment {keep['taken'][5:16] if keep.get('taken') else '?'}: keep {c['keep']}, drop "
                     f"{', '.join(str(i) for i in c['drop']) or '-'}  (members {c['members']})")
    return "\n".join(lines)
