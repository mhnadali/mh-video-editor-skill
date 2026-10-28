"""Story-first selection: choose the shots from a plan for the story, not from whatever looked nice on a contact sheet.

The reasoning, in order:
  1. The trip is cut into CHAPTERS (time blocks of the capture-ordered inventory). Each chapter has a weight (how much the
     story needs it, not how much footage there is) and an energy (calm / building / peak).
  2. The edit has SLOTS (one per shot, from the beat layout). The music has an energy at every slot. Chapters keep their
     chronological order, and the number of slots each one gets is chosen so that the high-energy chapters sit on the
     loud parts of the music (a small dynamic programme over the chapter boundaries).
  3. Inside a chapter, the clips are picked one by one by a score (technical quality, how well the clip's energy fits
     the chapter's slots, its own sound, people, wanted role, under-used camera, spread across the chapter's time) with a
     diversity penalty (same scene, same topic, same shot size). Then they are ordered (chronologically by default).
  4. The result is a STORYBOARD with a reason for every slot and two alternates, for the user to approve before any
     render. `audit` checks an EDL against the same principles afterwards.

The engine measures and ranks; the agent's `shot-notes.json` (role, mood, people, topic, shot size, skip) and the user's
decisions win. Nothing here is learned.
"""
from __future__ import annotations

import json
import math
import os
from typing import Optional

import vid_cutlogic as cl
import vid_filter as vf
import vid_plan as vp
import vidlib as vl

MOODS = {"calm": 0.25, "building": 0.55, "intense": 0.85}
CHAPTER_ENERGY = {"calm": 0.2, "building": 0.5, "peak": 0.85, "intense": 0.85}
SAME_SCENE_BITS = 10
DEVICE_SHORT = 12


# --------------------------------------------------------------------------- chapters

def device_key(rec: dict) -> str:
    return (rec.get("device") or "?").strip()[:DEVICE_SHORT]


def propose_chapters(inventory: list, scores: dict, gap_min: float = 35.0) -> dict:
    """Draft chapters from the capture timeline: a new chapter after every pause longer than `gap_min` minutes.

    The draft is only a starting point: the agent names the chapters after looking at the footage, merges or splits them,
    and sets the weights. The weight starts at the square root of the minutes of footage (a big block is not 20 times more
    important than a small one), and the energy at the typical energy of the block's livelier clips."""
    recs = sorted((r for r in inventory if r.get("dup_of") is None and not r.get("error")),
                  key=lambda r: (r.get("taken_epoch") or 0.0, r["index"]))
    if not recs:
        raise vl.EngineError("the inventory has no readable clips")
    blocks: list = [[recs[0]]]
    for a, b in zip(recs, recs[1:]):
        gap = ((b.get("taken_epoch") or 0) - ((a.get("taken_epoch") or 0) + (a.get("duration") or 0))) / 60.0
        if gap > gap_min:
            blocks.append([])
        blocks[-1].append(b)
    out = []
    for k, blk in enumerate(blocks):
        minutes = sum(r.get("duration") or 0 for r in blk) / 60.0
        en = sorted((scores.get(r["index"], {}).get("energy") or 0.0) for r in blk)
        top = en[len(en) * 2 // 3:] or [0.0]
        out.append({"name": f"Block {k + 1} ({blk[0]['taken'][5:16]} to {blk[-1]['taken'][11:16]})",
                    "from": min(r["index"] for r in blk), "to": max(r["index"] for r in blk),
                    "weight": round(math.sqrt(max(minutes, 0.1)), 2),
                    "energy": round(min(1.0, sum(top) / len(top) / 100.0), 2),
                    "clips": len(blk), "minutes": round(minutes, 1),
                    "devices": sorted({device_key(r) for r in blk})})
    return {"message": "", "hook": "chronological", "min_people_share": 0.25, "max_per_topic": 3, "chapters": out}


def validate_chapters(cfg: dict) -> dict:
    if not isinstance(cfg, dict) or not isinstance(cfg.get("chapters"), list) or not cfg["chapters"]:
        raise vl.EngineError("chapters file: expected {\"chapters\": [{name, from, to, weight, energy}, ...]}")
    last_to = -1
    for i, c in enumerate(cfg["chapters"]):
        for k in ("name", "from", "to"):
            if k not in c:
                raise vl.EngineError(f"chapter {i}: '{k}' is missing")
        if not (isinstance(c["from"], int) and isinstance(c["to"], int) and 0 <= c["from"] <= c["to"]):
            raise vl.EngineError(f"chapter '{c['name']}': from/to must be inventory indexes with from <= to")
        if c["from"] <= last_to:
            raise vl.EngineError(f"chapter '{c['name']}' overlaps or precedes the previous one (chapters follow the trip in order)")
        last_to = c["to"]
        w = c.get("weight", 1)
        if not isinstance(w, (int, float)) or isinstance(w, bool) or w < 0:
            raise vl.EngineError(f"chapter '{c['name']}': weight must be a number >= 0")
        e = c.get("energy", "building")
        if isinstance(e, str):
            if e not in CHAPTER_ENERGY:
                raise vl.EngineError(f"chapter '{c['name']}': energy must be calm, building, peak or a number 0-1")
        elif not isinstance(e, (int, float)) or isinstance(e, bool) or not 0 <= e <= 1:
            raise vl.EngineError(f"chapter '{c['name']}': energy must be calm, building, peak or a number 0-1")
        if c.get("order", "chronological") not in ("chronological", "energy"):
            raise vl.EngineError(f"chapter '{c['name']}': order must be chronological or energy")
    if cfg.get("hook", "chronological") not in ("chronological", "teaser"):
        raise vl.EngineError("hook must be 'chronological' or 'teaser'")
    for key, known in (("filters", vf.DEFAULT_FILTERS), ("dedupe", vf.DEFAULT_DEDUPE)):
        given = cfg.get(key)
        if given is None:
            continue
        if not isinstance(given, dict):
            raise vl.EngineError(f"'{key}' must be an object")
        for k in given:
            if k not in known:
                raise vl.EngineError(f"{key}: unknown setting '{k}' (known: {', '.join(known)})")
    md = (cfg.get("filters") or {}).get("min_duration")
    if md is not None and (not isinstance(md, (int, float)) or isinstance(md, bool) or md < 0):
        raise vl.EngineError("filters.min_duration must be a number of seconds >= 0")
    return cfg


def chapter_energy(c: dict) -> float:
    e = c.get("energy", "building")
    return CHAPTER_ENERGY[e] if isinstance(e, str) else float(e)


# --------------------------------------------------------------------------- slots and the music

def music_energy_at(beats: dict, t: float) -> float:
    """Music energy 0..1 at time t, relative to the quietest and loudest sections of the track."""
    secs = beats.get("sections") or []
    if not secs:
        return 0.5
    lo = min(s["energy"] for s in secs)
    hi = max(s["energy"] for s in secs)
    cur = secs[0]
    for s in secs:
        if s["start"] <= t:
            cur = s
    return 0.5 if hi - lo < 1e-9 else round((cur["energy"] - lo) / (hi - lo), 3)


def make_slots(beats: dict, duration: float, body_start: Optional[float], pacing: Optional[str], intro_shots: int = 2,
               period: Optional[float] = None) -> list:
    period = float(period or beats["period"])
    t0 = vp.body_start_time(beats, duration, period, intro_shots, body_start)
    if pacing == "energy":
        lay = vp.layout_paced(beats, duration, t0, period, intro_shots)
    else:
        lay = vp.layout(duration, t0, period, intro_shots)
    edges = [0.0] + lay["cuts"] + [duration]
    slots = []
    drop = beats.get("drop")
    for i in range(len(edges) - 1):
        t = edges[i]
        e = music_energy_at(beats, t + 0.5 * (edges[i + 1] - t))
        is_drop = drop is not None and abs(t - float(drop)) <= 0.08 and i > 0
        slots.append({"i": i, "t": round(t, 3), "len": round(edges[i + 1] - t, 3),
                      "music": 1.0 if is_drop else e, "drop": bool(is_drop)})     # the flash lands here: the strongest picture
    return slots


def allocate(chapters: list, slots: list, reserve: int = 0, flex: float = 0.3) -> list:
    """How many consecutive slots each chapter gets, in trip order.

    Each chapter's wish is its weight's share of the slots. The count may move within +-`flex` (30 % by default) of the wish (never
    below 1 for a chapter with weight > 0) so that high-energy chapters land on loud music and calm ones on quiet music:
    the cost of a count is how far the music energy of the slots is from the chapter's energy."""
    n = len(slots) - reserve
    active = [c for c in chapters if c.get("weight", 1) > 0]
    if not active:
        raise vl.EngineError("every chapter has weight 0")
    total_w = sum(c.get("weight", 1) for c in active)
    target = [n * c.get("weight", 1) / total_w for c in active]
    flex = min(max(float(flex), 0.0), 0.9)
    lo = [max(1, int(math.floor((1.0 - flex) * t))) for t in target]
    hi = [max(l, int(math.ceil((1.0 + flex) * t))) for t, l in zip(target, lo)]
    if sum(lo) > n:
        raise vl.EngineError(f"{len(active)} chapters need at least {sum(lo)} slots but the edit has {n}: raise the duration, "
                             "merge chapters or give a chapter weight 0")
    if sum(hi) < n:
        hi = [h + math.ceil((n - sum(hi)) / len(hi)) for h in hi]
    sl = slots[reserve:]
    K = len(active)
    INF = float("inf")
    dp = [[INF] * (n + 1) for _ in range(K + 1)]
    back = [[0] * (n + 1) for _ in range(K + 1)]
    dp[0][0] = 0.0
    for k in range(K):
        e = chapter_energy(active[k])
        for u in range(n + 1):
            if dp[k][u] == INF:
                continue
            for c in range(lo[k], hi[k] + 1):
                if u + c > n:
                    break
                cost = sum(abs(sl[s]["music"] - e) for s in range(u, u + c)) / max(n, 1) + \
                    0.15 * abs(c - target[k]) / max(target[k], 1.0)
                if dp[k][u] + cost < dp[k + 1][u + c]:
                    dp[k + 1][u + c] = dp[k][u] + cost
                    back[k + 1][u + c] = c
    if dp[K][n] == INF:
        raise vl.EngineError("no way to share the slots between the chapters; relax the weights")
    counts, u = [0] * K, n
    for k in range(K, 0, -1):
        counts[k - 1] = back[k][u]
        u -= counts[k - 1]
    by_name = {c["name"]: counts[i] for i, c in enumerate(active)}
    return [by_name.get(c["name"], 0) for c in chapters]


# --------------------------------------------------------------------------- candidates

def _note(notes: dict, rec: dict) -> dict:
    return notes.get(os.path.normcase(os.path.abspath(rec["path"]))) or {}


def clip_view(rec: dict, score: dict, note: dict) -> dict:
    """The few numbers the selection reasons about, merged from the measurement and the agent's notes."""
    mood = note.get("mood")
    act = MOODS[mood] if mood in MOODS else (score.get("energy") or 0.0) / 100.0
    size = note.get("shot_size") or score.get("shot_size")
    size = "close" if size == "detail" else size
    wins = score.get("wins") or []
    action_start = max(wins, key=lambda w: w[2])[0] if wins else score.get("best_start", 0.0)
    return {"index": rec["index"], "path": rec["path"], "name": rec.get("name"), "taken": rec.get("taken"),
            "epoch": rec.get("taken_epoch") or 0.0, "device": device_key(rec), "duration": rec.get("duration") or 0.0,
            "quality": (score.get("quality") or 0.0) / 100.0, "act": act,
            "sound": None if score.get("sound") is None else score["sound"] / 100.0,
            "hash": score.get("hash"), "size": size, "role": note.get("role"), "topic": note.get("topic"),
            "people": bool(note.get("people")) or note.get("role") == "portrait",
            "best_start": score.get("best_start", 0.0), "action_start": action_start,
            "flags": score.get("flags") or [], "internal_cut": bool(score.get("internal_cut"))}


SOFT_REASONS = {"dark", "blurry", "shaky", "covered", "internal-cut", "low-quality"}


def pool(chapter: dict, inventory: list, scores: dict, notes: dict, avoid: set, excluded: Optional[dict] = None,
         relaxed: bool = False) -> list:
    """The clips a chapter may use. `excluded` ({index: [reasons]}, from `vid_filter.exclusions`) removes unusable clips and
    the weaker takes of a moment; `relaxed` still allows the soft reasons (dark, blurry, shaky ...) when a chapter has too
    few clips, but never a short clip, a copy, a weaker take of the same moment or a clip marked `skip`."""
    out = []
    must = set(chapter.get("must") or [])
    excluded = excluded or {}
    for rec in inventory:
        i = rec["index"]
        if not (chapter["from"] <= i <= chapter["to"]) or rec.get("dup_of") is not None or i in avoid:
            continue
        sc = scores.get(i)
        if not sc or "quality" not in sc:
            continue
        note = _note(notes, rec)
        if i in set(chapter.get("avoid") or []):
            continue
        why = excluded.get(i, [])
        if why and i not in must:
            hard = [w for w in why if w.split(":")[0] not in SOFT_REASONS]
            if hard or not relaxed:
                continue
        elif i in must and note.get("skip"):
            continue
        v = clip_view(rec, sc, note)
        v["must"] = i in must
        out.append(v)
    return out


def _hamming(a, b) -> Optional[int]:
    if a is None or b is None:
        return None
    return bin(int(a) ^ int(b)).count("1")


def _components(c: dict, target: float, chosen: list, ctx: dict) -> dict:
    """Score parts of one candidate against what is already chosen (all 0..1 unless a penalty)."""
    parts = {"quality": c["quality"], "fit": 1.0 - abs(c["act"] - target)}
    parts["sound"] = c["sound"] if c["sound"] is not None else 0.0
    bonus, why = 0.0, []
    if c["people"] and ctx["people_share"] < ctx["min_people_share"]:
        bonus += 0.20
        why.append("brings people into the story")
    if len(ctx["device_library_share"]) > 1 and ctx["picked_so_far"] >= 4 and             ctx["device_share"].get(c["device"], 0.0) < ctx["device_library_share"].get(c["device"], 0.0) * 0.8:
        bonus += 0.12
        why.append(f"{c['device']} is under-used")
    if ctx["roles"] and c["role"] in ctx["roles"]:
        bonus += 0.15
        why.append(f"the wanted role ({c['role']})")
    parts["bonus"] = bonus
    parts["bonus_why"] = why
    pen = 0.0
    for o in chosen:
        h = _hamming(c["hash"], o["hash"])
        if h is not None and h <= SAME_SCENE_BITS:
            pen += 0.6
        if c["topic"] and c["topic"] == o["topic"] and ctx["topic_count"].get(c["topic"], 0) >= ctx["max_per_topic"] \
                and o in ctx.get("chapter_picks", []):
            pen += 0.5                       # `topic_count` is reset for every chapter: a trip about dunes may show dunes twice
    if chosen and c["size"]:
        same = sum(1 for o in chosen if o["size"] == c["size"])
        if same / len(chosen) > 0.45 and len(chosen) >= 3:
            pen += 0.12
    parts["penalty"] = pen
    if chosen:
        span = max(abs(c["epoch"] - o["epoch"]) for o in chosen)
        nearest = min(abs(c["epoch"] - o["epoch"]) for o in chosen)
        parts["spread"] = min(1.0, nearest / max(ctx["chapter_span"], 1.0)) if span else 0.0
    else:
        parts["spread"] = 0.5
    return parts


def total(parts: dict) -> float:
    return (0.30 * parts["quality"] + 0.28 * parts["fit"] + 0.08 * parts["sound"] + parts["bonus"]
            + 0.10 * parts["spread"] - parts["penalty"])


def pick_chapter(cands: list, n: int, target: float, ctx: dict, chosen_global: list) -> tuple:
    """Greedy choice of n clips of one chapter. Returns (picked views with their parts, alternates by clip index)."""
    picked: list = []
    ctx["chapter_picks"] = []
    local_ctx = dict(ctx)
    remaining = list(cands)
    forced = [c for c in remaining if c.get("must")]
    for c in forced[:n]:
        picked.append((c, _components(c, target, chosen_global + [p[0] for p in picked], local_ctx)))
        ctx["chapter_picks"].append(c)
        remaining.remove(c)
    while len(picked) < n and remaining:
        best, best_parts, best_total = None, None, -1e9
        already = chosen_global + [p[0] for p in picked]
        for c in remaining:
            parts = _components(c, target, already, local_ctx)
            t = total(parts)
            if t > best_total:
                best, best_parts, best_total = c, parts, t
        picked.append((best, best_parts))
        ctx["chapter_picks"].append(best)
        remaining.remove(best)
        if best["topic"]:
            ctx["topic_count"][best["topic"]] = ctx["topic_count"].get(best["topic"], 0) + 1
    # alternates: the best unchosen clips of the chapter, scored on their own
    alt = sorted(remaining, key=lambda c: -total(_components(c, target, [], local_ctx)))
    return picked, [c["index"] for c in alt[:6]]


def pick_by_slot(cands: list, slots: list, chapter: dict, ctx: dict, chosen_global: list) -> tuple:
    """For a chapter whose inner order does not matter ('order': 'energy'): fill the slots from the loudest music down, each
    slot taking the clip that fits ITS energy best, so the most striking picture lands on the biggest musical moment.
    Returns ([(view, parts)] aligned with `slots`, alternates)."""
    ctx["chapter_picks"] = []
    remaining = list(cands)
    out: list = [None] * len(slots)
    base = chapter_energy(chapter)
    for k in sorted(range(len(slots)), key=lambda i: -slots[i]["music"]):
        if not remaining:
            break
        target = 0.5 * slots[k]["music"] + 0.5 * base
        already = chosen_global + [o[0] for o in out if o]
        scored = [(total(_components(c, target, already, ctx)), c) for c in remaining]
        scored.sort(key=lambda x: -x[0])
        best = scored[0][1]
        out[k] = (best, _components(best, target, already, ctx))
        ctx["chapter_picks"].append(best)
        if best["topic"]:
            ctx["topic_count"][best["topic"]] = ctx["topic_count"].get(best["topic"], 0) + 1
        remaining.remove(best)
    alt = sorted(remaining, key=lambda c: -total(_components(c, base, [], ctx)))
    return out, [c["index"] for c in alt[:6]]


def select(inventory: list, scores: dict, beats: dict, cfg: dict, notes: Optional[dict] = None, duration: float = 45.0,
           body_start: Optional[float] = None, pacing: Optional[str] = "energy", intro_shots: int = 2,
           period: Optional[float] = None) -> dict:
    cfg = validate_chapters(cfg)
    nt = cl.notes_by_path(notes)
    chapters = cfg["chapters"]
    must_all = {i for c in chapters for i in (c.get("must") or [])}
    ex = vf.exclusions(inventory, scores, notes, cfg.get("filters"), cfg.get("dedupe"), must=must_all)
    excluded = ex["reasons"]
    slots = make_slots(beats, duration, body_start, pacing, intro_shots, period)
    teaser = cfg.get("hook") == "teaser"
    counts = allocate(chapters, slots, reserve=1 if teaser else 0, flex=cfg.get("flex", 0.3))
    lib_dev: dict = {}
    for r in inventory:
        if r.get("dup_of") is None and not r.get("error"):
            lib_dev[device_key(r)] = lib_dev.get(device_key(r), 0) + 1
    lib_total = sum(lib_dev.values()) or 1
    ctx = {"people_share": 0.0, "min_people_share": float(cfg.get("min_people_share", 0.0)),
           "device_share": {}, "device_library_share": {d: n / lib_total for d, n in lib_dev.items()},
           "topic_count": {}, "max_per_topic": int(cfg.get("max_per_topic", 3)), "roles": set(), "chapter_span": 1.0,
           "picked_so_far": 0}
    warnings: list = []
    chosen_global: list = []
    entries: list = []                         # [(chapter name, view, parts, alternates, target)]
    shortlist: dict = {}                       # chapter -> the chosen clips and the best alternates, to look at in detail
    cursor = 1 if teaser else 0
    teaser_entry = None
    if teaser:
        tc = next((c for c in chapters if c["name"] == cfg.get("teaser_chapter")), None) or max(chapters, key=chapter_energy)
        cands = pool(tc, inventory, scores, nt, set(), excluded)
        if not cands:
            raise vl.EngineError("no clip available for the teaser")
        ctx["roles"] = {"action"}
        ctx["chapter_span"] = max(1.0, tc["to"] - tc["from"])
        picked, _alt = pick_chapter(cands, 1, 0.9, ctx, [])
        teaser_entry = (f"{tc['name']} (teaser)", picked[0][0], picked[0][1], _alt, 0.9)
        chosen_global.append(picked[0][0])
    used = {teaser_entry[1]["index"]} if teaser_entry else set()
    for c, cnt in zip(chapters, counts):
        if cnt <= 0:
            continue
        sl = slots[cursor:cursor + cnt]
        target = sum(s["music"] for s in sl) / len(sl) if sl else chapter_energy(c)
        target = 0.5 * target + 0.5 * chapter_energy(c)      # the music asks, the chapter's nature answers
        cands = pool(c, inventory, scores, nt, used, excluded)
        if len(cands) < cnt:
            warnings.append(f"chapter '{c['name']}' has {len(cands)} usable clips for {cnt} slots; clips with soft problems "
                            "(dark, blurry, shaky ...) were allowed")
            cands = pool(c, inventory, scores, nt, used, excluded, relaxed=True)
        if len(cands) < cnt:
            warnings.append(f"chapter '{c['name']}' only has {len(cands)} clips for {cnt} slots: the chapter is given fewer shots")
            cnt = len(cands)
            sl = sl[:cnt]
            if cnt == 0:
                cursor += counts[chapters.index(c)]
                continue
        ctx["roles"] = set(c.get("roles") or [])
        ctx["topic_count"] = {}
        ctx["max_per_topic"] = int(c.get("max_per_topic", cfg.get("max_per_topic", 3)))
        ctx["chapter_span"] = max(1.0, (max(x["epoch"] for x in cands) - min(x["epoch"] for x in cands)))
        if c.get("order", "chronological") == "energy":
            slot_picks, alt = pick_by_slot(cands, sl, c, ctx, chosen_global)
            order = [p[0] for p in slot_picks if p]
            parts_by = {p[0]["index"]: p[1] for p in slot_picks if p}
            slot_target = {p[0]["index"]: 0.5 * sl[k]["music"] + 0.5 * chapter_energy(c) for k, p in enumerate(slot_picks) if p}
        else:
            picked, alt = pick_chapter(cands, cnt, target, ctx, chosen_global)
            order = sorted((p[0] for p in picked), key=lambda v: v["epoch"])
            parts_by = {p[0]["index"]: p[1] for p in picked}
            slot_target = {}
        shortlist[c["name"]] = sorted({v["index"] for v in order} | set(alt[:6]))
        for v in order:
            entries.append((c["name"], v, parts_by[v["index"]], alt, slot_target.get(v["index"], target)))
            chosen_global.append(v)
            ctx["picked_so_far"] = len(chosen_global)
            used.add(v["index"])
            ctx["device_share"][v["device"]] = ctx["device_share"].get(v["device"], 0.0) + 1.0 / n_total(counts, teaser)
            if v["people"]:
                ctx["people_share"] += 1.0 / n_total(counts, teaser)
        cursor += counts[chapters.index(c)]
    if teaser_entry:
        entries.insert(0, teaser_entry)
    rows = []
    for k, (cname, v, parts, alt, target) in enumerate(entries[:len(slots)]):
        s = slots[k]
        action = v["act"] >= 0.6 or v["role"] == "action"
        start = v["action_start"] if action else v["best_start"]
        rows.append({"slot": k, "t": s["t"], "len": s["len"], "music": s["music"], "chapter": cname, "clip": v["index"],
                     "name": v["name"], "taken": v["taken"], "device": v["device"], "role": v["role"], "size": v["size"],
                     "people": v["people"], "topic": v["topic"], "energy": round(v["act"], 2), "target": round(target, 2),
                     "start": round(start, 2), "start_kind": "action moment" if action else "best frame window",
                     "score": round(total(parts), 3), "path": v["path"],
                     "why": explain(v, parts, target, s), "alternates": [a for a in alt if a != v["index"]][:2]})
    if len(rows) < len(slots):
        warnings.append(f"only {len(rows)} of {len(slots)} slots could be filled")
    return {"duration": duration, "slots": len(slots), "body_start": body_start, "pacing": pacing,
            "hook": cfg.get("hook", "chronological"), "chapters": chapter_summary(chapters, counts, rows),
            "rows": rows, "warnings": warnings, "coverage": coverage(rows, inventory), "shortlist": shortlist,
            "exclusions": vf.summarize(ex, sum(1 for r in inventory if r.get("dup_of") is None)),
            "same_moment": [{"keep": c["keep"], "drop": c["drop"]} for c in ex["clusters"]]}


def n_total(counts: list, teaser: bool) -> int:
    return max(1, sum(counts) + (1 if teaser else 0))


def explain(v: dict, parts: dict, target: float, slot: dict) -> str:
    bits = [f"quality {v['quality'] * 100:.0f}", f"energy {v['act']:.2f} for a slot that wants {target:.2f} (music {slot['music']:.2f})"]
    if v["role"]:
        bits.append(v["role"])
    if v["people"]:
        bits.append("people")
    if parts.get("sound", 0) >= 0.7:
        bits.append("lively sound")
    bits += parts.get("bonus_why", [])
    return "; ".join(bits)


def chapter_summary(chapters: list, counts: list, rows: list) -> list:
    out = []
    for c, n in zip(chapters, counts):
        got = sum(1 for r in rows if r["chapter"] == c["name"])
        out.append({"name": c["name"], "weight": c.get("weight", 1), "energy": c.get("energy", "building"),
                    "slots": n, "filled": got, "from": c["from"], "to": c["to"]})
    return out


def coverage(rows: list, inventory: list) -> dict:
    n = max(len(rows), 1)
    dev: dict = {}
    for r in rows:
        dev[r["device"]] = dev.get(r["device"], 0) + 1
    lib: dict = {}
    for r in inventory:
        if r.get("dup_of") is None and not r.get("error"):
            lib[device_key(r)] = lib.get(device_key(r), 0) + 1
    lt = sum(lib.values()) or 1
    return {"devices_in_edit": {d: round(c / n, 2) for d, c in dev.items()},
            "devices_in_library": {d: round(c / lt, 2) for d, c in lib.items()},
            "people_share": round(sum(1 for r in rows if r["people"]) / n, 2),
            "roles": {k: sum(1 for r in rows if r["role"] == k) for k in sorted({r["role"] for r in rows if r["role"]})}}


# --------------------------------------------------------------------------- output

def clip_list_text(sb: dict) -> str:
    lines = ["# story-first selection (chronological unless a chapter says otherwise); idx = inventory index"]
    for r in sb["rows"]:
        lines.append(f'{r["path"]} @ {r["start"]:.2f}    # slot {r["slot"]} idx {r["clip"]} {r["taken"][5:16]} {r["chapter"]}')
    return "\n".join(lines) + "\n"


def storyboard_markdown(sb: dict) -> str:
    out = [f"# Storyboard ({sb['slots']} shots, {sb['duration']:.0f} s)", "", "## Chapters",
           "| chapter | weight | energy | slots | filled | clips (index range) |", "|---|---|---|---|---|---|"]
    for c in sb["chapters"]:
        out.append(f"| {c['name']} | {c['weight']} | {c['energy']} | {c['slots']} | {c['filled']} | {c['from']}-{c['to']} |")
    out += ["", "## Shots", "| # | time | len | music | chapter | clip | taken | device | role | why | alternates |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in sb["rows"]:
        out.append(f"| {r['slot']} | {r['t']:.1f} | {r['len']:.2f} | {r['music']:.2f} | {r['chapter']} | {r['clip']} @ {r['start']:.1f}s "
                   f"({r['start_kind']}) | {r['taken'][5:16]} | {r['device']} | {r['role'] or ''} | {r['why']} | "
                   f"{', '.join(str(a) for a in r['alternates'])} |")
    cv = sb["coverage"]
    out += ["", "## Coverage", f"- cameras in the edit: {cv['devices_in_edit']} (library: {cv['devices_in_library']})",
            f"- shots with people: {cv['people_share']:.0%}", f"- roles: {cv['roles']}"]
    ex = sb.get("exclusions")
    if ex:
        out += ["", "## Left out before choosing",
                f"- {ex['usable']} of {ex['clips']} clips were usable; excluded: " +
                ", ".join(f"{k} {v}" for k, v in ex["by_reason"].items()),
                f"- {ex['same_moment_clusters']} moments were filmed more than once: only the best take of each is used"]
    if sb["warnings"]:
        out += ["", "## Warnings"] + [f"- {w}" for w in sb["warnings"]]
    return "\n".join(out) + "\n"


def format_storyboard(sb: dict) -> str:
    lines = [f"{'#':>2} {'time':>5} {'len':>4} {'mus':>4}  {'chapter':22} {'clip':>5} {'taken':11} {'cam':9} {'role':12} why"]
    for r in sb["rows"]:
        lines.append(f"{r['slot']:>2} {r['t']:5.1f} {r['len']:4.2f} {r['music']:4.2f}  {r['chapter'][:22]:22} {r['clip']:>5} "
                     f"{r['taken'][5:16]:11} {r['device'][:9]:9} {(r['role'] or '-')[:12]:12} {r['why']}")
    cv = sb["coverage"]
    lines.append(f"cameras {cv['devices_in_edit']} vs library {cv['devices_in_library']}; people {cv['people_share']:.0%}; roles {cv['roles']}")
    ex = sb.get("exclusions")
    if ex:
        lines.append(f"left out: {ex['excluded']} of {ex['clips']} clips ({', '.join(f'{k} {v}' for k, v in ex['by_reason'].items())}); "
                     f"{ex['same_moment_clusters']} repeated moments reduced to their best take")
    lines += [f"WARNING: {w}" for w in sb["warnings"]]
    return "\n".join(lines)


# --------------------------------------------------------------------------- audit of an EDL

def _pearson(a: list, b: list) -> Optional[float]:
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va < 1e-9 or vb < 1e-9:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


def audit_edl(edl: dict, inventory: list, scores: dict, beats: Optional[dict] = None, notes: Optional[dict] = None,
              cfg: Optional[dict] = None, chronological: bool = True) -> dict:
    """Check an EDL against the story principles: coverage of the chapters, cameras, people, topics, energy against the
    music, reading time and the order of the trip. Returns {'findings': [...], 'stats': {...}}."""
    import vid_edl as ve
    by_path = {os.path.normcase(os.path.abspath(r["path"])): r for r in inventory}
    nt = cl.notes_by_path(notes)
    bounds = ve.timeline(edl)
    fps = ve.project(edl)["fps"]
    views, times = [], []
    for j, sh in enumerate(edl["shots"]):
        key = os.path.normcase(os.path.abspath(sh["clip"]))
        rec = by_path.get(key)
        if not rec:
            views.append(None)
            times.append((bounds[j] / fps, (bounds[j + 1] - bounds[j]) / fps))
            continue
        views.append(clip_view(rec, scores.get(rec["index"], {}), nt.get(key) or {}))
        times.append((bounds[j] / fps, (bounds[j + 1] - bounds[j]) / fps))
    known = [(v, t) for v, t in zip(views, times) if v]
    findings: list = []
    stats: dict = {"shots": len(views), "unmatched_clips": len(views) - len(known)}
    if not known:
        return {"findings": [{"level": "warn", "code": "no-inventory-match", "msg": "no shot of the EDL is in the inventory"}], "stats": stats}
    n = len(known)
    # order of the trip
    if chronological:
        inv = sum(1 for (a, _), (b, _) in zip(known, known[1:]) if b["epoch"] < a["epoch"] - 1)
        stats["order_inversions"] = inv
        if inv:
            findings.append({"level": "warn", "code": "trip-order", "msg": f"{inv} shot(s) are earlier in the trip than the shot before them"})
    # cameras
    dev: dict = {}
    for v, _ in known:
        dev[v["device"]] = dev.get(v["device"], 0) + 1
    lib: dict = {}
    for r in inventory:
        if r.get("dup_of") is None and not r.get("error"):
            lib[device_key(r)] = lib.get(device_key(r), 0) + 1
    lt = sum(lib.values()) or 1
    stats["devices"] = {d: round(c / n, 2) for d, c in dev.items()}
    for d, c in lib.items():
        if c / lt >= 0.2 and dev.get(d, 0) / n < 0.5 * c / lt:
            findings.append({"level": "info", "code": "camera-underused",
                             "msg": f"{d} is {c / lt:.0%} of the library but {dev.get(d, 0) / n:.0%} of the edit"})
    # people and topics
    people = sum(1 for v, _ in known if v["people"]) / n
    stats["people_share"] = round(people, 2)
    want = float((cfg or {}).get("min_people_share", 0.0))
    if want and people < want:
        findings.append({"level": "warn", "code": "few-people", "msg": f"people are in {people:.0%} of the shots; the story asks for {want:.0%}"})
    run, best_run, best_topic = 1, 1, None
    for (a, _), (b, _) in zip(known, known[1:]):
        if a["topic"] and a["topic"] == b["topic"]:
            run += 1
            if run > best_run:
                best_run, best_topic = run, a["topic"]
        else:
            run = 1
    if best_run >= 3:
        findings.append({"level": "warn", "code": "topic-run", "msg": f"{best_run} shots in a row about '{best_topic}'"})
    # chapters
    if cfg:
        cfg = validate_chapters(cfg)
        total_w = sum(c.get("weight", 1) for c in cfg["chapters"]) or 1
        rows = []
        for c in cfg["chapters"]:
            got = sum(1 for v, _ in known if c["from"] <= v["index"] <= c["to"])
            share, wish = got / n, c.get("weight", 1) / total_w
            rows.append({"chapter": c["name"], "shots": got, "share": round(share, 2), "wish": round(wish, 2)})
            if c.get("weight", 1) > 0 and got == 0:
                findings.append({"level": "warn", "code": "chapter-missing", "msg": f"chapter '{c['name']}' has no shot"})
            elif wish and (share > 1.8 * wish or share < 0.5 * wish):
                findings.append({"level": "info", "code": "chapter-balance",
                                 "msg": f"chapter '{c['name']}': {share:.0%} of the shots, the story asked for {wish:.0%}"})
        stats["chapters"] = rows
    # energy against the music
    if beats and beats.get("sections"):
        me = [music_energy_at(beats, t + 0.5 * ln) for _, (t, ln) in known]
        ce = [v["act"] for v, _ in known]
        r = _pearson(me, ce)
        stats["energy_correlation"] = None if r is None else round(r, 2)
        for (v, (t, ln)), m in zip(known, me):
            if m >= 0.65 and v["act"] < 0.3:
                findings.append({"level": "warn", "code": "energy-mismatch",
                                 "msg": f"{t:.1f}s: loud music ({m:.2f}) under a calm shot (clip {v['index']}, energy {v['act']:.2f})"})
        if r is not None and r < 0.1:
            findings.append({"level": "warn", "code": "energy-curve",
                             "msg": f"the energy of the pictures does not follow the music (correlation {r:.2f})"})
    # reading time
    for v, (t, ln) in known:
        if ln < 0.9 and (v["size"] == "wide" or v["people"] or v["role"] in ("portrait", "establishing")):
            findings.append({"level": "info", "code": "short-reading-time",
                             "msg": f"{t:.1f}s: a {v['role'] or v['size']} shot (clip {v['index']}) lasts only {ln:.2f}s; the viewer cannot read it"})
    return {"findings": findings, "stats": stats}


def format_audit(rep: dict) -> str:
    st = rep["stats"]
    lines = [f"story audit: {st.get('shots', 0)} shots; cameras {st.get('devices')}; people {st.get('people_share')}; "
             f"energy vs music correlation {st.get('energy_correlation')}; order inversions {st.get('order_inversions')}"]
    for r in st.get("chapters", []):
        lines.append(f"  chapter {r['chapter'][:30]:30} shots {r['shots']:2d}  share {r['share']:.0%}  asked {r['wish']:.0%}")
    for f in rep["findings"]:
        lines.append(f"  [{f['level'].upper():4}] {f['code']}: {f['msg']}")
    if not rep["findings"]:
        lines.append("  no findings")
    return "\n".join(lines)
