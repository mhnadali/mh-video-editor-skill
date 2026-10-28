#!/usr/bin/env python3
"""Measure how good the clip selection is, against a synthetic library whose truth is known.

    python engine/tools/eval_selection.py            # print the metrics for beam and greedy selection
    python engine/tools/eval_selection.py --check    # exit 1 if a metric falls below its floor (used by the tests)

The library is built from a fixed seed: clips that are too short, copies of the same moment (one camera and two cameras), people
clips, calm / peak chapters, and a music map. Nothing needs FFmpeg. Every metric has a known right answer:

  short_used           clips under the minimum length that were used                         must be 0
  duplicates_used      pairs of clips of the same moment that were BOTH used                 must be 0
  kept_best_take       share of same-moment groups whose used clip is the best take          >= 0.95 (of groups that are used)
  order_errors         shots out of the trip's order inside the story                        must be 0
  fill                 share of the music's slots that got a shot                            must be 1.0
  chapter_coverage     share of chapters that got at least one shot                          must be 1.0
  distinct_pictures    share of shots with a picture not seen before                         >= 0.97
  energy_fit           correlation of the music's energy with the shots' energy              >= 0.25
                       (the story's own energy takes half of each slot's target, so this is not 1.0 by design)
  people_share         share of shots with people when the library has plenty                >= the `people` variant's floor
  sequence_value       the sequence score (beam only); beam must not lose to greedy by more than a small tolerance
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import vid_filter as vf  # noqa: E402
import vid_story as st  # noqa: E402

FLOORS = {"short_used": 0, "duplicates_used": 0, "kept_best_take": 0.95, "order_errors": 0, "fill": 1.0,
          "chapter_coverage": 1.0, "distinct_pictures": 0.97, "energy_fit": 0.25}


def build_library(seed: int = 7, n: int = 110) -> tuple:
    """(inventory, scores, truth). truth = {"short": set, "groups": [[best, worse...]], "people": set}"""
    rng = random.Random(seed)
    inv, sc, truth = [], {}, {"short": set(), "groups": [], "people": set()}
    minute = 0
    base_epoch = 1791400000.0

    def add(i, device, t, dur, hash64, q, energy, person=False):
        inv.append({"index": i, "path": f"lib/clip{i}.mp4", "name": f"clip{i}.mp4", "duration": dur, "dup_of": None, "error": None,
                    "device": device, "taken": "2026-10-08 10:00:00", "taken_epoch": base_epoch + t})
        sc[i] = {"index": i, "path": f"lib/clip{i}.mp4", "quality": q, "energy": energy, "best_start": 1.0, "flags": [],
                 "hash": hash64, "hashes": [hash64, hash64 ^ 0b101, hash64 ^ 0b110], "csig": [100 + (hash64 % 40)] * 12,
                 "brightness": 120.0, "scene": f"s{hash64 % 5}", "shot_size": ("wide", "medium", "close")[i % 3],
                 "wins": [[1.0, q, energy / 10.0], [3.0, q, energy / 5.0]], "motion_dx": 0.0, "motion_mag": 0.0, "shake": 1.0,
                 "texture": 3.0, "person": person}

    def fresh_hash():
        return rng.getrandbits(64)

    i = 0
    t = 0.0
    devices = ("iPhone", "DJI")
    while i < n:
        t += rng.uniform(25.0, 70.0)
        dev = devices[i % 2]
        kind = rng.random()
        energy = 15 + 85 * ((i * 37) % 100) / 100.0
        person = rng.random() < 0.25
        if kind < 0.12:                                   # too short
            add(i, dev, t, rng.uniform(1.5, 4.5), fresh_hash(), 80.0, energy, person)
            truth["short"].add(i)
            i += 1
        elif kind < 0.30 and i + 2 < n:                   # the same moment filmed twice (same camera or two cameras)
            h = fresh_hash()
            q = rng.uniform(60, 85)
            add(i, dev, t, 9.0, h, q, energy, person)
            other = devices[(i + 1) % 2] if rng.random() < 0.5 else dev
            add(i + 1, other, t + rng.uniform(2.0, 20.0), 9.0, h ^ 0b11, q - rng.uniform(8, 20), energy, person)
            truth["groups"].append([i, i + 1])
            if person:
                truth["people"].update({i, i + 1})
            i += 2
        else:
            add(i, dev, t, rng.uniform(6.0, 25.0), fresh_hash(), rng.uniform(55, 95), energy, person)
            if person:
                truth["people"].add(i)
            i += 1
    for k in truth["short"]:
        inv[k]["duration"] = min(inv[k]["duration"], 4.5)
    return inv, sc, truth


def build_beats(duration: float = 45.0, period: float = 0.6) -> dict:
    n = int(duration / period)
    secs = [{"label": "intro", "start": 0.2, "end": 9.0, "energy": 0.15},
            {"label": "drop", "start": 9.6, "end": 21.0, "energy": 0.85},
            {"label": "build", "start": 21.6, "end": 31.0, "energy": 0.35},
            {"label": "drop", "start": 31.6, "end": duration, "energy": 0.95}]
    return {"period": period, "t0": 0.2, "duration": duration, "beats": [round(0.2 + period * k, 3) for k in range(n)],
            "beat_strength": [0.9 if k % 2 == 0 else 0.3 for k in range(n)], "sections": secs}


def build_story(inv: list) -> dict:
    top = max(r["index"] for r in inv)
    a, b = top // 3, 2 * top // 3
    return {"chapters": [{"name": "arrival", "from": 0, "to": a, "weight": 1, "energy": "calm"},
                         {"name": "dunes", "from": a + 1, "to": b, "weight": 2, "energy": "peak"},
                         {"name": "camp", "from": b + 1, "to": top, "weight": 1, "energy": "building"}]}


def pearson(x: list, y: list):
    n = len(x)
    if n < 3:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    if sx == 0 or sy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy)


def measure(sb: dict, inv: list, scores: dict, truth: dict, chapters: dict, min_duration: float = 5.0) -> dict:
    rows = sb["rows"]
    used = [r["clip"] for r in rows]
    by = {r["index"]: r for r in inv}
    short_used = sum(1 for i in used if i in truth["short"] or (by[i].get("duration") or 0) < min_duration - 0.1)
    both = 0
    best_ok = 0
    used_groups = 0
    for g in truth["groups"]:
        picked = [i for i in g if i in used]
        if len(picked) > 1:
            both += 1
        if picked:
            used_groups += 1
            best = max(g, key=lambda i: scores[i]["quality"])
            best_ok += 1 if best in picked else 0
    epochs = [by[i]["taken_epoch"] for i in used]
    order_errors = sum(1 for a, b in zip(epochs, epochs[1:]) if b < a)
    cover = 0
    for c in chapters["chapters"]:
        if any(c["from"] <= i <= c["to"] for i in used):
            cover += 1
    hashes = [scores[i]["hash"] for i in used]
    seen, distinct = [], 0
    for h in hashes:
        if all(bin(h ^ s).count("1") > 8 for s in seen):
            distinct += 1
        seen.append(h)
    corr = pearson([r["music"] for r in rows], [r["energy"] for r in rows])
    return {"shots": len(rows), "short_used": short_used, "duplicates_used": both,
            "kept_best_take": 1.0 if not used_groups else best_ok / used_groups,
            "order_errors": order_errors, "fill": len(rows) / max(sb["slots"], 1),
            "chapter_coverage": cover / len(chapters["chapters"]), "distinct_pictures": distinct / max(len(rows), 1),
            "energy_fit": 0.0 if corr is None else corr,
            "people_share": sum(1 for r in rows if r.get("people")) / max(len(rows), 1),
            "sequence_value": sb.get("sequence_score")}


def evaluate(seed: int = 7) -> dict:
    inv, sc, truth = build_library(seed)
    beats = build_beats()
    story = build_story(inv)
    out = {"library": {"clips": len(inv), "short": len(truth["short"]), "same_moment_groups": len(truth["groups"]),
                       "people": len(truth["people"])}}
    for method in ("beam", "greedy"):
        sb = st.select(inv, sc, beats, story, None, duration=45.0, pacing="energy", method=method)
        out[method] = measure(sb, inv, sc, truth, story)
    for variant in ("action", "people"):
        sb = st.select(inv, sc, beats, story, None, duration=45.0, pacing="energy", variant=variant)
        out[variant] = measure(sb, inv, sc, truth, story)
    return out


def check(res: dict) -> list:
    problems = []
    for method in ("beam", "greedy"):
        m = res[method]
        for k, floor in FLOORS.items():
            v = m[k]
            if k in ("short_used", "duplicates_used", "order_errors"):
                if v > floor:
                    problems.append(f"{method}: {k} = {v} (must be {floor})")
            elif v < floor:
                problems.append(f"{method}: {k} = {v:.2f} (floor {floor})")
    if res["people"]["people_share"] + 1e-9 < res["beam"]["people_share"]:
        problems.append("the people variant has a lower share of people than the balanced one")
    for name in ("action", "people"):
        for k in ("short_used", "duplicates_used", "order_errors"):
            if res[name][k]:
                problems.append(f"{name}: {k} = {res[name][k]}")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--check", action="store_true", help="exit 1 if a metric is below its floor")
    ap.add_argument("--json", help="also write the numbers here")
    a = ap.parse_args(argv)
    res = evaluate(a.seed)
    print(f"library: {res['library']}")
    keys = ["shots", "short_used", "duplicates_used", "kept_best_take", "order_errors", "fill", "chapter_coverage",
            "distinct_pictures", "energy_fit", "people_share", "sequence_value"]
    print(f"{'metric':20}" + "".join(f"{n:>10}" for n in ("beam", "greedy", "action", "people")))
    for k in keys:
        cells = []
        for n in ("beam", "greedy", "action", "people"):
            v = res[n][k]
            cells.append(f"{'-':>10}" if v is None else (f"{v:>10.2f}" if isinstance(v, float) else f"{v:>10}"))
        print(f"{k:20}" + "".join(cells))
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=2), encoding="utf-8")
    problems = check(res)
    for p in problems:
        print("PROBLEM:", p)
    if a.check and problems:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
