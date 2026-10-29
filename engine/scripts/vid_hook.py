"""The first second and the last one of a storyboard.

A feed decides in the first second whether to keep watching. `hook_rank` scores clips as an opening shot (a teaser placed before
the story starts) and the storyboard's own opening on the same scale, with the reasons in words; `loop_fit` says how well the last
shot leads back into the first (feeds replay a short video, and an ending that looks like the opening hides the seam). Both only
propose: the user decides, and the storyboard is selected again with `"hook": "teaser", "teaser_clip": N` in chapters.json. A
chronological story may keep its calm opening on purpose.

What makes an opening strong is a judgement about the content, so it comes from the agent's reading of the footage
(shot-notes.json: role and mood), and the measurements only break ties (picture quality, visual punch, freshness). Measured on the
Kavir trip (174 clips the agent had read, 42 of them action or intense): the measured energy (motion and sound) does not tell an
action shot from people talking (AUC 0.48; handheld wobble and loud talk score high), and the features that did separate them
there (quality 0.74; low texture and contrast on desert sand) belong to that trip, not to footage in general. Clips without a
note are therefore not ranked but counted, so the agent knows what to read first (`sheets --only`).
"""
from __future__ import annotations

import vid_filter as vf
import vid_story as st

# The agent's reading: how much of an opening a shot of this role and mood is (0-1).
MOOD_HOOK = {"intense": 1.0, "building": 0.6, "calm": 0.2}
ROLE_HOOK = {"action": 0.9, "establishing": 0.45, "portrait": 0.2, "detail": 0.25}
HOOK_WEIGHTS = {"reading": 0.55, "quality": 0.25, "punch": 0.10, "fresh": 0.10}
GUESS_DISCOUNT = 0.9           # a role / mood guessed by the content plug-in (74 % / 67 % right on Kavir) counts a little less


def _percentiles(values: dict) -> dict:
    """{key: value} -> {key: share of the others below it}, 0-1."""
    order = sorted(values, key=lambda k: values[k])
    n = max(1, len(order) - 1)
    return {k: i / n for i, k in enumerate(order)}


def _punch(scores: dict, ids: list) -> dict:
    col = _percentiles({i: scores[i].get("colourfulness") or 0.0 for i in ids})
    sat = _percentiles({i: scores[i].get("saturation") or 0.0 for i in ids})
    return {i: 0.5 * col[i] + 0.5 * sat[i] for i in ids}


SCENE_SECONDS = 120.0


def same_scene(a: dict, b: dict) -> bool:
    """The same camera within two minutes: another take of one scene (different framings of one dune climb)."""
    return ((a.get("device") or "") == (b.get("device") or "")
            and abs((a.get("taken_epoch") or 0.0) - (b.get("taken_epoch") or 0.0)) <= SCENE_SECONDS)


def reading(note: dict) -> float | None:
    """0-1 from the note's role and mood (the stronger of the two), or None when the clip has not been read."""
    vals = [MOOD_HOOK[note["mood"]]] if note.get("mood") in MOOD_HOOK else []
    vals += [ROLE_HOOK[note["role"]]] if note.get("role") in ROLE_HOOK else []
    if not vals:
        return None
    if note.get("role") == "action" and note.get("mood") == "intense":
        return 1.0
    return max(vals)


def hook_score(note: dict, quality: float, punch: float, fresh: float) -> tuple:
    """(0-1 score or None when the clip has no note, the reasons in words) of one clip as the opening shot."""
    r = reading(note)
    if r is None:
        return None, ["not read yet (no role or mood in shot-notes.json)"]
    total = (HOOK_WEIGHTS["reading"] * r + HOOK_WEIGHTS["quality"] * quality + HOOK_WEIGHTS["punch"] * punch
             + HOOK_WEIGHTS["fresh"] * fresh)
    what = " ".join(x for x in (note.get("mood"), note.get("role")) if x)
    why = [what + (": " + note["subject"] if note.get("subject") else ""), f"quality {round(quality * 100)}"]
    if punch >= 0.75:
        why.append("vivid (colour in the top quarter)")
    if fresh < 1.0:
        why.append("already in the storyboard: the teaser would repeat it" if fresh == 0.0 else "looks like a storyboard shot")
    return round(total, 3), why


def hook_rank(inventory: list, scores: dict, sb: dict, cfg: dict | None = None, notes: dict | None = None, top: int = 5) -> dict:
    """The storyboard's opening and the best `top` teaser candidates on one 0-1 scale, and how many usable clips are unread."""
    cfg = cfg or {}
    res = vf.exclusions(inventory, scores, notes, cfg.get("filters"), cfg.get("dedupe"))
    by_path = st.cl.notes_by_path(notes)
    by = {r["index"]: r for r in inventory}
    usable = [r["index"] for r in inventory if r["index"] in scores and "quality" in scores[r["index"]]
              and r.get("dup_of") is None and not res["reasons"].get(r["index"])]
    rows = sb.get("rows") or []
    if not rows:
        raise st.vl.EngineError("the storyboard has no shots")
    first = rows[0]["clip"]
    pool = sorted(set(usable) | ({first} if first in scores and first in by else set()))
    punch = _punch(scores, pool)
    in_board = {r["clip"] for r in rows}
    cands, unread = [], 0
    for i in usable:
        if i == first:
            continue
        note = st._note(by_path, by[i])                                                          # noqa: SLF001
        guessed = False
        if reading(note) is None and (scores[i].get("role_guess") or scores[i].get("mood_guess")):
            note = {"role": scores[i].get("role_guess"), "mood": scores[i].get("mood_guess")}      # the content plug-in's guess
            guessed = True
        if reading(note) is None:
            unread += 1
            continue
        fresh = 0.0 if i in in_board else 1.0
        if fresh and any(vf.similarity_level(by[i], by[j], scores[i], scores[j], cfg.get("dedupe"))
                         for j in in_board if j in scores and j in by):
            fresh = 0.5
        s, why = hook_score(note, (scores[i].get("quality") or 0.0) / 100.0, punch.get(i, 0.0), fresh)
        if guessed:
            s = round(s * GUESS_DISCOUNT, 3)
            why[0] = "guessed " + why[0] + " (from the clips you noted)"
        wins = scores[i].get("wins") or []
        start = max(wins, key=lambda w: w[2])[0] if wins else scores[i].get("best_start", 0.0)
        cands.append({"clip": i, "name": by[i].get("name"), "taken": by[i].get("taken"), "score": s, "why": why, "start": start})
    cands.sort(key=lambda c: -c["score"])
    distinct: list = []                     # one candidate per scene: a real choice, not five takes of one dune
    for c in cands:
        if len(distinct) >= top:
            break
        if not any(same_scene(by[c["clip"]], by[d["clip"]]) or loops(scores[c["clip"]], scores[d["clip"]]) for d in distinct):
            distinct.append(c)
    current = None
    if first in scores and first in by:
        s, why = hook_score(st._note(by_path, by[first]), (scores[first].get("quality") or 0.0) / 100.0,      # noqa: SLF001
                            punch.get(first, 0.0), 1.0)
        current = {"clip": first, "name": by[first].get("name"), "score": s, "why": why,
                   "percentile": None if s is None else round(sum(1 for c in cands if c["score"] < s) / max(1, len(cands)), 2)}
    return {"current": current, "candidates": distinct, "considered": len(cands), "unread": unread}


# A loop reads as one when the last frame could be the same place as the first. Calibrated on the Kavir trip: 20 hand-labelled
# pairs of one place and time were at most 19 bits apart in the 64-bit picture hash and 20 apart in the colour signature (median 7);
# 3000 random pairs had a median of 32 bits (5th percentile 24) and 37 in colour.
LOOP_HASH_BITS = 20
LOOP_COLOUR = 15.0


def look_parts(a: dict, b: dict) -> dict:
    """Picture-hash bits apart (0-64), mean colour-signature difference (0-255) and brightness difference of two clips."""
    h = bin(int(a["hash"]) ^ int(b["hash"])).count("1") if a.get("hash") is not None and b.get("hash") is not None else None
    ca, cb = a.get("csig") or [], b.get("csig") or []
    c = round(sum(abs(x - y) for x, y in zip(ca, cb)) / len(ca), 1) if ca and len(ca) == len(cb) else None
    br = round(abs(a["brightness"] - b["brightness"]), 1) if a.get("brightness") is not None and b.get("brightness") is not None else None
    return {"hash_bits": h, "colour": c, "brightness": br}


def look_distance(a: dict, b: dict) -> float:
    """0 = the two shots look alike, 1 = no more alike than two random clips (32 bits and colour 37 apart)."""
    lp = look_parts(a, b)
    parts = [(0.6, min(1.0, lp["hash_bits"] / 32.0))] if lp["hash_bits"] is not None else []
    parts += [(0.4, min(1.0, lp["colour"] / 37.0))] if lp["colour"] is not None else []
    return round(sum(w * v for w, v in parts) / sum(w for w, _ in parts), 3) if parts else 1.0


def loops(a: dict, b: dict) -> bool:
    lp = look_parts(a, b)
    return (lp["hash_bits"] is not None and lp["hash_bits"] <= LOOP_HASH_BITS
            and (lp["colour"] is None or lp["colour"] <= LOOP_COLOUR))


def loop_fit(sb: dict, scores: dict) -> dict:
    """How well the last shot leads back into the first, and the last slot's alternates ranked by that."""
    rows = sb.get("rows") or []
    if len(rows) < 2:
        return {"distance": None, "fits": False, "alternates": []}
    first, last = rows[0]["clip"], rows[-1]["clip"]
    if first not in scores or last not in scores:
        return {"distance": None, "fits": False, "alternates": []}
    alts = sorted(({"clip": i, "distance": look_distance(scores[i], scores[first]), "fits": loops(scores[i], scores[first])}
                   for i in (rows[-1].get("alternates") or []) if i in scores), key=lambda x: x["distance"])
    return {"distance": look_distance(scores[last], scores[first]), "fits": loops(scores[last], scores[first]),
            "parts": look_parts(scores[last], scores[first]), "alternates": alts}


def format_report(rank: dict, loop: dict) -> str:
    lines = []
    cur = rank.get("current")
    if cur and cur["score"] is not None:
        lines.append(f"current opening: clip {cur['clip']} ({cur['name']}), hook {cur['score']:.2f}, stronger than "
                     f"{round(cur['percentile'] * 100)} % of the {rank['considered']} read clips ({'; '.join(cur['why'])})")
    elif cur:
        lines.append(f"current opening: clip {cur['clip']} ({cur['name']}) has no note yet, so its strength is not judged")
    if rank.get("unread"):
        lines.append(f"{rank['unread']} usable clips have no role or mood in shot-notes.json and are not ranked: read the strongest "
                     "chapters first (`sheets --only RANGE --frames 3`) and note them")
    lines.append(f"{'#':>2} {'clip':>5} {'hook':>5}  {'taken':16} why")
    for k, c in enumerate(rank["candidates"], 1):
        lines.append(f"{k:>2} {c['clip']:>5} {c['score']:>5.2f}  {str(c.get('taken') or '')[:16]:16} {'; '.join(c['why'])}")
    if loop.get("distance") is not None:
        pt = loop.get("parts") or {}
        alt = next((a for a in loop.get("alternates") or [] if a["fits"]), None)
        lines.append(f"loop: the last shot {'looks like the same place as' if loop['fits'] else 'does not lead back into'} the first "
                     f"(picture {pt.get('hash_bits')} bits apart, colour {pt.get('colour')}; a loop needs at most "
                     f"{LOOP_HASH_BITS} bits and {LOOP_COLOUR:g})" +
                     (f"; alternate clip {alt['clip']} of the last slot would loop" if alt and not loop["fits"] else ""))
    return "\n".join(lines)
