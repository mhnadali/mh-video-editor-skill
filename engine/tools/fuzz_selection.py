#!/usr/bin/env python3
"""Check the rules of the selection on many random libraries (no FFmpeg, no footage).

    python engine/tools/fuzz_selection.py [--runs 300] [--seed 0]      # exit 1 if any rule is broken

Every run builds a random library (short clips, the same moment filmed twice, people, three cameras), random chapters (weights,
energies, orders, `must` clips, a teaser hook), a random profile (bans, pins, favourites, a topic bias), a random length, variant
and method, and checks what must ALWAYS hold:

  clip-twice        no clip is used twice                      short-used        no clip under 5 s
  banned-used       no banned clip                             same-moment-twice two takes of one moment only if the user wanted both
  chrono-order      trip order inside chronological chapters   chapter-order     chapters follow the trip
  outside-chapter   every shot belongs to its chapter          start-outside     every start lies inside its clip
  unfilled-silent   an unfilled slot is always explained       underfilled       slots stay empty only when the chapters have no usable clip left
  not-approved      a cut-down uses only approved shots         approved-start    ... each at its approved start
  user-start        a clip with a start the user chose keeps it (unless a cut-down's approved start wins)

Then it applies random feedback (replace, keep, ban, unban, start, biases, weights, nonsense) and checks that a decision is either
refused with a clear error or leaves a valid profile, no clip twice, no banned clip unmarked and a profile `story select` accepts.
The deep review of 0.16.0 found two real bugs this way (an allocation that left slots empty while clips were spare, and bans
that did not reach an older storyboard).
"""
from __future__ import annotations

import argparse
import random
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import eval_selection as ev  # noqa: E402
import vid_filter as vf  # noqa: E402
import vid_profile as vpf  # noqa: E402
import vid_story as st  # noqa: E402
import vidlib as vl  # noqa: E402

REFUSALS = ("need at least", "no way to share", "no clip available", "every chapter has weight 0")


def random_case(seed: int) -> dict:
    rng = random.Random(seed)
    n = rng.choice([30, 60, 110, 200])
    inv, sc, truth = ev.build_library(seed, n)
    top = max(r["index"] for r in inv)
    k = rng.randint(1, 6)
    cuts = sorted(rng.sample(range(1, top), k - 1)) if k > 1 else []
    edges = [0] + cuts + [top + 1]
    chapters = []
    for a, b in zip(edges, edges[1:]):
        chapters.append({"name": f"c{a}", "from": a, "to": b - 1, "weight": rng.choice([0, 1, 1, 2, 5]) if k > 1 else 1,
                         "energy": rng.choice(["calm", "building", "peak", 0.2, 0.9]),
                         "order": rng.choice(["chronological", "energy"])})
    if all(c["weight"] == 0 for c in chapters):
        chapters[0]["weight"] = 1
    for c in chapters:
        if rng.random() < 0.3:
            c["must"] = rng.sample(range(c["from"], c["to"] + 1), min(2, c["to"] - c["from"] + 1))
    cfg = {"chapters": chapters}
    if rng.random() < 0.3:
        cfg["hook"] = "teaser"
    if rng.random() < 0.3:
        cfg["flex"] = rng.choice([0.0, 0.3, 0.6, 0.9])
    profile = None
    if rng.random() < 0.5:
        idx = [r["index"] for r in inv]
        profile = dict(vpf.empty(), banned=[inv[i]["path"] for i in rng.sample(idx, min(5, len(idx)))],
                       topics={"food": rng.choice([-0.3, 0.2])}, pins={inv[i]["path"]: 0 for i in rng.sample(idx, 2)},
                       favoured=[inv[i]["path"] for i in rng.sample(idx, 3)],
                       starts={inv[i]["path"]: round(rng.uniform(0, 20), 2) for i in rng.sample(idx, 6)})
    duration = rng.choice([15.0, 30.0, 45.0, 60.0])
    approved = None
    if rng.random() < 0.3:                          # a cut-down: an approved subset of the library, at chosen starts
        pool = [r["index"] for r in inv if (r.get("duration") or 0) >= 4.9]
        approved = {i: round(rng.uniform(0, 3), 2) for i in rng.sample(pool, min(len(pool), rng.randint(8, 40)))}
    return {"inv": inv, "sc": sc, "cfg": cfg, "profile": profile, "duration": duration, "beats": ev.build_beats(duration),
            "variant": rng.choice(["balanced", "action", "people"]), "method": rng.choice(["beam", "greedy"]),
            "pacing": rng.choice(["energy", "beat"]), "approved": approved}


def check_storyboard(case: dict, sb: dict) -> list:
    inv, sc, cfg, profile = case["inv"], case["sc"], case["cfg"], case["profile"]
    chapters = cfg["chapters"]
    rows = sb["rows"]
    used = [r["clip"] for r in rows]
    by = {r["index"]: r for r in inv}
    bad = []
    if len(used) != len(set(used)):
        bad.append(("clip-twice", sorted({i for i in used if used.count(i) > 1})[:3]))
    if any((by[i]["duration"] or 0) < 4.9 for i in used):
        bad.append(("short-used", [i for i in used if (by[i]["duration"] or 0) < 4.9]))
    banned = vpf.banned_indexes(profile, inv) if profile else set()
    if banned & set(used):
        bad.append(("banned-used", sorted(banned & set(used))))
    must = {i for c in chapters for i in c.get("must", [])} | (set(vpf.pinned(profile, inv)) if profile else set())
    for cl in sb["same_moment"]:
        both = [i for i in [cl["keep"]] + cl["drop"] if i in used]
        if len(both) > 1 and not set(both) <= must | {cl["keep"]}:
            bad.append(("same-moment-twice", both))
    for c in chapters:
        if c["order"] == "chronological":
            ep = [by[r["clip"]]["taken_epoch"] for r in rows if r["chapter"] == c["name"]]
            if ep != sorted(ep):
                bad.append(("chrono-order", c["name"]))
    names = [c["name"] for c in chapters]
    body = [names.index(r["chapter"]) for r in rows if r["chapter"] in names]
    if body != sorted(body):
        bad.append(("chapter-order", [r["chapter"] for r in rows][:6]))
    for r in rows:
        c = next((c for c in chapters if c["name"] == r["chapter"]), None)
        if c and not c["from"] <= r["clip"] <= c["to"]:
            bad.append(("outside-chapter", (r["clip"], c["name"])))
        if not 0 <= r["start"] <= by[r["clip"]]["duration"]:
            bad.append(("start-outside", (r["clip"], r["start"])))
    if len(rows) < sb["slots"] and not sb["warnings"]:
        bad.append(("unfilled-silent", (len(rows), sb["slots"])))
    approved = case.get("approved")
    if approved is not None:
        if any(r["clip"] not in approved for r in rows):
            bad.append(("not-approved", [r["clip"] for r in rows if r["clip"] not in approved][:3]))
        if any(abs(r["start"] - approved[r["clip"]]) > 0.011 for r in rows if r["clip"] in approved):
            bad.append(("approved-start", [(r["clip"], r["start"]) for r in rows if r["clip"] in approved][:3]))
    elif profile:
        for r in rows:
            s = vpf.start_of(profile, by[r["clip"]]["path"])
            if s is not None and (r["start_kind"] != "set by the user" or abs(r["start"] - min(s, by[r["clip"]]["duration"] - 0.3)) > 0.011):
                bad.append(("user-start", (r["clip"], r["start"], s)))
    # capacity as the engine sees it: the same exclusions (with the profile's preferred takes), bans and approval
    prefer = set()
    if profile:
        fav = {vpf.norm(p) for p in profile.get("favoured", [])}
        prefer = {r["index"] for r in inv if vpf.norm(r["path"]) in fav} | set(vpf.pinned(profile, inv))
    excluded = {k: list(v) for k, v in vf.exclusions(inv, sc, None, None, None, must=must, prefer=prefer)["reasons"].items()}
    for i in banned:
        excluded.setdefault(i, []).append("banned")
    if approved is not None:
        for r in inv:
            if r["index"] not in approved:
                excluded.setdefault(r["index"], []).append("not-approved")
    cap = 0
    for c in chapters:
        if c["weight"] > 0:
            c2 = dict(c, must=sorted(set(c.get("must") or []) | {i for i in must if c["from"] <= i <= c["to"]}))
            cap += len(st.pool(c2, inv, sc, {}, set(), excluded, relaxed=True))
    if len(rows) < min(sb["slots"], cap) - 2 - (1 if cfg.get("hook") == "teaser" else 0):
        bad.append(("underfilled", (len(rows), sb["slots"], cap)))
    return bad


def run_selection(seed: int) -> list:
    case = random_case(seed)
    try:
        sb = st.select(case["inv"], case["sc"], case["beats"], case["cfg"], None, duration=case["duration"], pacing=case["pacing"],
                       variant=case["variant"], method=case["method"], profile=case["profile"], approved=case["approved"])
    except vl.EngineError as e:
        return [] if any(x in str(e) for x in REFUSALS) else [("refused-unclearly", str(e)[:120])]
    except Exception as e:                                          # noqa: BLE001  any crash is a finding
        return [("exception", "".join(traceback.format_exception_only(type(e), e)).strip()[:160])]
    return check_storyboard(case, sb)


def run_feedback(seed: int) -> list:
    rng = random.Random(10_000 + seed)
    inv, sc, _truth = ev.build_library(seed, 90)
    story, beats = ev.build_story(inv), ev.build_beats()
    sb = st.select(inv, sc, beats, story, None, duration=45.0, pacing="energy")
    views = {r["index"]: st.clip_view(r, sc[r["index"]], {}) for r in inv}
    prof, bad = vpf.empty(), []
    for _step in range(rng.randint(1, 4)):
        decisions = []
        for _ in range(rng.randint(1, 5)):
            a = rng.choice(["replace", "keep", "ban", "unban", "prefer-topic", "avoid-role", "weight", "unpin", "start", "bogus"])
            slot = rng.choice([r["slot"] for r in sb["rows"]] + [999])
            clip = rng.choice([r["index"] for r in inv] + [9999])
            d = {"action": a}
            if a == "replace":
                d.update(slot=slot, **{"with": clip})
            elif a in ("keep", "unpin"):
                d["slot"] = slot
            elif a in ("ban", "unban"):
                d["clip"] = clip
            elif a == "start":
                d.update(slot=slot, start=rng.uniform(0, 12))
            elif a == "prefer-topic":
                d["topic"] = "food"
            elif a == "avoid-role":
                d["role"] = "action"
            elif a == "weight":
                d.update(name=rng.choice(["people", "nonsense"]), value=rng.random())
            decisions.append(d)
        try:
            new, rows = vpf.learn(prof, sb, {"decisions": decisions}, inv, views)
        except vl.EngineError:
            continue
        except Exception as e:                                      # noqa: BLE001
            bad.append(("feedback-exception", "".join(traceback.format_exception_only(type(e), e)).strip()[:160]))
            continue
        clips = [r["clip"] for r in rows if not r.get("banned")]
        if len(clips) != len(set(clips)):
            bad.append(("feedback-clip-twice", seed))
        ban = vpf.banned_indexes(new, inv)
        if any(r["clip"] in ban and not r.get("banned") for r in rows):
            bad.append(("feedback-banned-unmarked", seed))
        try:
            vpf.validate(new)
            st.select(inv, sc, beats, story, None, duration=45.0, pacing="energy", profile=new)
        except Exception as e:                                      # noqa: BLE001
            bad.append(("feedback-profile-unusable", str(e)[:120]))
        prof = new
    return bad


def run(runs: int, seed: int = 0, feedback_runs: int | None = None, log=print) -> dict:
    found: dict = {}
    for s in range(seed, seed + runs):
        for code, detail in run_selection(s):
            found.setdefault(code, []).append((s, detail))
    for s in range(seed, seed + (runs // 3 if feedback_runs is None else feedback_runs)):
        for code, detail in run_feedback(s):
            found.setdefault(code, []).append((s, detail))
    for code, items in found.items():
        log(f"{code}: {len(items)} run(s), e.g. {items[:3]}")
    return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=300)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    found = run(a.runs, a.seed)
    if found:
        return 1
    print(f"all rules held on {a.runs} random selections and {a.runs // 3} random feedback sequences")
    return 0


if __name__ == "__main__":
    sys.exit(main())
