#!/usr/bin/env python3
"""Check the selection's thresholds against examples YOU labelled, and suggest better ones.

    python engine/tools/calibrate_selection.py --work WORK --labels labels.json [--json out.json]

`labels.json`:
    {"pairs":  [{"a": 264, "b": 265, "same": true}, {"a": 204, "b": 206, "same": false}, ...],
     "people": [{"clip": 5, "people": true}, {"clip": 9, "people": false}, ...]}

`same` = the two clips show the same moment (nothing differs: keep only the best). `people` = a person is clearly in the clip.
Indexes are inventory indexes of the work folder. The tool needs `inventory.json` and `scores.json` (and `vision.json` for people).

What it reports:
  * the strict and the look-alike reading of "same moment" at the default thresholds: precision (of the pairs it calls the same,
    how many are) and recall (of the pairs that are the same, how many it finds);
  * a sweep of the strict thresholds (hash bits, frames matching, colour, brightness): every combination that never calls a
    different pair "same" (precision 1.0), best recall first. The engine's rule is to prefer keeping a clip over wrongly
    dropping one, so precision comes first;
  * people detection: the confusion matrix of `person` and `person_sure` against the labels.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import vid_filter as vf  # noqa: E402
import vid_vision as vv  # noqa: E402
import vidlib as vl  # noqa: E402


def load_world(work: str) -> tuple:
    inv = {r["index"]: r for r in vl.load_json(Path(work) / "inventory.json")["clips"]}
    scores = {s["index"]: s for s in vl.load_json(Path(work) / "scores.json")["scores"] if "quality" in s}
    vv.merge_into_scores(scores, vv.load(work))
    return inv, scores


def confusion(pred: list, truth: list) -> dict:
    tp = sum(1 for p, t in zip(pred, truth) if p and t)
    fp = sum(1 for p, t in zip(pred, truth) if p and not t)
    fn = sum(1 for p, t in zip(pred, truth) if not p and t)
    tn = sum(1 for p, t in zip(pred, truth) if not p and not t)
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": None if tp + fp == 0 else round(tp / (tp + fp), 3),
            "recall": None if tp + fn == 0 else round(tp / (tp + fn), 3)}


def judge_pairs(pairs: list, inv: dict, scores: dict, cfg: dict | None = None) -> dict:
    strict, loose = [], []
    truth = [bool(p["same"]) for p in pairs]
    for p in pairs:
        level = vf.similarity_level(inv[p["a"]], inv[p["b"]], scores[p["a"]], scores[p["b"]], cfg)
        strict.append(level == "same")
        loose.append(level in ("same", "lookalike"))
    return {"strict": confusion(strict, truth), "lookalike_or_same": confusion(loose, truth)}


def sweep(pairs: list, inv: dict, scores: dict) -> list:
    """Strict-reading settings that never call a different pair 'same', best recall first."""
    out = []
    for bits, need, col, br in itertools.product((8, 10, 12, 14, 16), (1, 2, 3), (5.0, 8.0, 12.0, 20.0), (4.0, 8.0, 15.0, 30.0)):
        cfg = {"hash_bits": bits, "frames_matching": need, "colour": col, "brightness": br,
               "window_s": 600.0, "cross_camera_window_s": 600.0}
        pred, truth = [], []
        for p in pairs:
            pred.append(vf.similarity_level(inv[p["a"]], inv[p["b"]], scores[p["a"]], scores[p["b"]],
                                            dict(cfg, lookalike_hash_bits=-1)) == "same")
            truth.append(bool(p["same"]))
        c = confusion(pred, truth)
        if c["fp"] == 0:
            out.append({"hash_bits": bits, "frames_matching": need, "colour": col, "brightness": br, **c})
    out.sort(key=lambda r: (-r["tp"], r["frames_matching"] * -1, r["hash_bits"], r["colour"], r["brightness"]))
    return out


def judge_people(rows: list, scores: dict) -> dict:
    out = {}
    truth = [bool(r["people"]) for r in rows]
    for key in ("person", "person_sure"):
        have = [(r, scores[r["clip"]].get(key)) for r in rows if r["clip"] in scores and scores[r["clip"]].get(key) is not None]
        out[key] = confusion([bool(v) for _, v in have], [bool(r["people"]) for r, _ in have])
        out[key]["judged"] = len(have)
    out["labelled"] = len(truth)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--json")
    ap.add_argument("--top", type=int, default=8, help="how many sweep results to print")
    a = ap.parse_args(argv)
    inv, scores = load_world(a.work)
    labels = vl.load_json(a.labels)
    pairs = [p for p in labels.get("pairs", []) if p["a"] in scores and p["b"] in scores]
    skipped = len(labels.get("pairs", [])) - len(pairs)
    report: dict = {"pairs": len(pairs), "pairs_skipped": skipped}
    if pairs:
        report["defaults"] = judge_pairs(pairs, inv, scores)
        report["sweep"] = sweep(pairs, inv, scores)
        print(f"{len(pairs)} labelled pairs ({sum(1 for p in pairs if p['same'])} same, {sum(1 for p in pairs if not p['same'])} different)"
              + (f"; {skipped} skipped (not scored)" if skipped else ""))
        for name, c in report["defaults"].items():
            print(f"  default {name:18} precision {c['precision']}  recall {c['recall']}   (tp {c['tp']} fp {c['fp']} fn {c['fn']} tn {c['tn']})")
        cur = vf.DEFAULT_DEDUPE
        print(f"  current strict settings: hash_bits {cur['hash_bits']}, frames {cur['frames_matching']}, colour {cur['colour']}, "
              f"brightness {cur['brightness']}")
        print("  settings with NO wrong 'same' (best recall first):")
        for r in report["sweep"][:a.top]:
            print(f"    hash_bits {r['hash_bits']:>2} frames {r['frames_matching']} colour {r['colour']:>4} brightness {r['brightness']:>4}"
                  f"   recall {r['recall']}  (tp {r['tp']})")
        print("  Few labels cannot prove a setting: a different trip may need different numbers. Prefer the current (strict) "
              "settings unless many labelled pairs agree.")
    if labels.get("people"):
        report["people"] = judge_people(labels["people"], scores)
        for key in ("person", "person_sure"):
            c = report["people"][key]
            print(f"  people {key:12} precision {c['precision']}  recall {c['recall']}   (tp {c['tp']} fp {c['fp']} fn {c['fn']} tn {c['tn']}, "
                  f"{c['judged']} judged)")
    if a.json:
        Path(a.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
