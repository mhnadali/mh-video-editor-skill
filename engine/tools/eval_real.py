"""A regression check on a real project: the numbers that were measured on real footage, measured again, against a baseline.

    python engine/tools/eval_real.py --work WORK --chapters chapters.json --notes shot-notes.json --save baseline.json
    python engine/tools/eval_real.py --work WORK --chapters chapters.json --notes shot-notes.json --compare baseline.json

The synthetic tests prove the rules; they cannot show that a change made the engine worse on real clips. This runs on a real work
folder (which never goes into the repository) in under a minute and measures:

  beats       where the music's onsets sit against beats.json's grid (median and spread, first 60 s) and its timebase
  same        the same-moment rules against the labelled pairs (labels.json: precision first)
  people      `person` / `person_sure` against the labelled clips
  hook        the share of action or intense shots among `story hook`'s five candidates
  sound       "voices" among the clips noted with people against the action clips (audio.json from `audio scan`)
  select      the storyboard selected twice with the same input is identical (no hidden randomness)
  qa          with --video and --edl: the beat sync measured on that render's sound

With --compare every number is set against the baseline and a change in the wrong direction beyond its tolerance is a
regression (exit 1). Missing inputs (no labels, no audio.json) skip their part.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path[:0] = [str(HERE.parents[1] / "scripts"), str(HERE.parent)]

import vidlib as vl  # noqa: E402

# (key path, better: "higher" | "lower" | "closer0", tolerance)
CHECKS = [(("beats", "median_abs"), "lower", 0.015), (("beats", "iqr"), "lower", 0.03),
          (("same", "precision"), "higher", 0.0), (("same", "recall"), "higher", 0.05),
          (("people", "precision"), "higher", 0.05), (("people", "recall"), "higher", 0.05),
          (("hook", "action_share"), "higher", 0.2), (("sound", "voices_gap"), "higher", 0.05),
          (("select", "deterministic"), "higher", 0.0), (("qa", "median_abs"), "lower", 0.015)]

# The 1.0 gate: a calibrated number must hold on a SECOND real project, not only on the one it was tuned on. These are
# absolute floors (not a per-project regression); both projects must clear them for the calibration to be trusted.
# (key path, direction, floor): "min" means value >= floor, "max" means value <= floor, "eq" means value == floor.
FLOORS = [(("same", "precision"), "min", 0.95), (("people", "precision"), "min", 0.80),
          (("beats", "median_abs"), "max", 0.02), (("qa", "median_abs"), "max", 0.045),
          (("select", "deterministic"), "eq", 1)]



def beats_part(beats: dict) -> dict:
    import vid_beats
    pcm = vid_beats.decode_pcm(beats["file"], max_seconds=60)
    low, high, _ = vid_beats._bands(pcm)                                                   # noqa: SLF001
    on = vid_beats.onset_strength(low, high)
    w, fr, offs = beats["period"] / 4, vid_beats.FRAME, []
    for g in beats["beats"]:
        if 1.0 < g < 59.0:
            i0, i1 = int((g - w) / fr), min(len(on) - 1, int((g + w) / fr))
            offs.append(max(range(i0, i1 + 1), key=lambda i: on[i]) * fr - g)
    s = sorted(offs)
    med = s[len(s) // 2]
    return {"timebase": beats.get("timebase", 1), "median": round(med, 3), "median_abs": round(abs(med), 3),
            "iqr": round(s[3 * len(s) // 4] - s[len(s) // 4], 3), "beats": len(s)}


def run(work: str, chapters: str | None, notes_path: str | None, video: str | None = None, edl: str | None = None) -> dict:
    import calibrate_selection as cs
    import vid_hook
    import vid_story
    w = Path(work)
    inv, scores = cs.load_world(work)
    inventory = list(inv.values())
    notes = vl.load_json(notes_path) if notes_path else None
    out: dict = {}
    beats = vl.load_json(w / "beats.json") if (w / "beats.json").is_file() else None
    if beats:
        out["beats"] = beats_part(beats)
    labels = vl.load_json(w / "labels.json") if (w / "labels.json").is_file() else {}
    pairs = [p for p in labels.get("pairs") or [] if p["a"] in scores and p["b"] in scores]
    if pairs:
        out["same"] = {k: v for k, v in cs.judge_pairs(pairs, inv, scores)["strict"].items() if k in ("precision", "recall", "tp", "fp")}
    if labels.get("people"):
        p = cs.judge_people(labels["people"], scores)["person"]
        out["people"] = {k: p[k] for k in ("precision", "recall", "judged")}
    cfg = vl.load_json(chapters) if chapters else None
    if cfg and beats:
        kw = dict(duration=45.0, body_start=beats.get("drop"), pacing="energy")
        a = vid_story.select(inventory, scores, beats, cfg, notes, **kw)
        b = vid_story.select(inventory, scores, beats, cfg, notes, **kw)
        out["select"] = {"deterministic": int([r["clip"] for r in a["rows"]] == [r["clip"] for r in b["rows"]]),
                         "shots": len(a["rows"])}
        if notes:
            rank = vid_hook.hook_rank(inventory, scores, a, cfg, notes, top=5)
            by = {vl.ffp(k).lower(): v for k, v in notes.items()}
            acts = [c for c in rank["candidates"]
                    if (by.get(vl.ffp(inv[c["clip"]]["path"]).lower()) or {}).get("role") == "action"
                    or (by.get(vl.ffp(inv[c["clip"]]["path"]).lower()) or {}).get("mood") == "intense"]
            out["hook"] = {"action_share": round(len(acts) / max(1, len(rank["candidates"])), 2), "unread": rank["unread"]}
    sounds = vl.load_json(w / "audio.json") if (w / "audio.json").is_file() else None
    if sounds and notes:
        by = {vl.ffp(k).lower(): v for k, v in notes.items()}
        grp = {"people": [0, 0], "action": [0, 0]}
        for k, s in sounds.items():
            n = by.get(vl.ffp(s["path"]).lower())
            if not n:
                continue
            g = "action" if n.get("role") == "action" else ("people" if n.get("people") or n.get("role") == "portrait" else None)
            if g:
                grp[g][0] += s["label"] == "voices"
                grp[g][1] += 1
        share = {g: round(v[0] / v[1], 3) if v[1] else None for g, v in grp.items()}
        if None not in share.values():
            out["sound"] = {"voices_people": share["people"], "voices_action": share["action"],
                            "voices_gap": round(share["people"] - share["action"], 3)}
    if video and edl:
        import vid_edl as ve
        import vid_qa
        e = ve.load(edl)
        info = vl.probe(video)
        sync = vid_qa.beat_sync(video, e, vid_qa.expected_events(e, ve.project(e)["fps"], info["duration"]))
        if sync:
            out["qa"] = dict(sync, median_abs=round(abs(sync["median"]), 3))
    return out


def compare(now: dict, base: dict) -> list:
    """[(name, before, after, verdict)] for every number present in both."""
    rows = []
    for path, better, tol in CHECKS:
        a, b = base, now
        for k in path:
            a = (a or {}).get(k)
            b = (b or {}).get(k)
        if a is None or b is None:
            continue
        worse = (b < a - tol) if better == "higher" else (b > a + tol)
        rows.append((".".join(path), a, b, "REGRESSION" if worse else "ok"))
    return rows


def _at(d: dict, path) -> object:
    for k in path:
        d = (d or {}).get(k)
    return d


def _meets(value, direction: str, floor) -> bool:
    if value is None:
        return False
    if direction == "min":
        return value >= floor
    if direction == "max":
        return value <= floor
    return value == floor


def cross_project(a: dict, b: dict) -> dict:
    """Two project baselines against the absolute floors. Every floor present in both must hold on both; the spread is
    reported so a number that merely passes on one project and barely passes on the other is visible. This is the
    calibration-stability check the 1.0 milestone asks for: one trip cannot prove a threshold."""
    rows = []
    ok = True
    for path, direction, floor in FLOORS:
        va, vb = _at(a, path), _at(b, path)
        if va is None or vb is None:
            rows.append({"metric": ".".join(path), "floor": f"{direction} {floor}", "a": va, "b": vb,
                         "verdict": "missing"})
            continue
        pa, pb = _meets(va, direction, floor), _meets(vb, direction, floor)
        spread = round(abs(va - vb), 3) if isinstance(va, (int, float)) and isinstance(vb, (int, float)) else None
        verdict = "ok" if (pa and pb) else "BELOW FLOOR"
        ok = ok and pa and pb
        rows.append({"metric": ".".join(path), "floor": f"{direction} {floor}", "a": va, "b": vb,
                     "spread": spread, "verdict": verdict})
    compared = [r for r in rows if r["verdict"] != "missing"]
    return {"rows": rows, "ok": ok, "compared": len(compared), "missing": len(rows) - len(compared)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--work"); ap.add_argument("--chapters"); ap.add_argument("--notes")
    ap.add_argument("--video", help="a render of this project, for the beat sync measured on its sound"); ap.add_argument("--edl")
    ap.add_argument("--save", help="write the numbers as a baseline"); ap.add_argument("--compare", help="a baseline to compare with")
    ap.add_argument("--cross", nargs=2, metavar=("A", "B"),
                    help="two saved baselines from different projects: check both clear the absolute floors (the 1.0 gate)")
    a = ap.parse_args(argv)
    if a.cross:
        base_a, base_b = vl.load_json(a.cross[0]), vl.load_json(a.cross[1])
        res = cross_project(base_a, base_b)
        for r in res["rows"]:
            spread = "" if r.get("spread") is None else f"  (spread {r['spread']})"
            print(f"  {r['metric']:22} {r['floor']:>8}   {r['a']!s:>8} / {r['b']!s:<8} {r['verdict']}{spread}")
        if not res["ok"]:
            print(f"CALIBRATION DOES NOT HOLD across the two projects ({res['compared']} metrics compared)")
            return 1
        print(f"calibration holds on both projects ({res['compared']} metrics compared, {res['missing']} missing)")
        return 0
    if not a.work:
        ap.error("--work is required (or use --cross A B to compare two saved baselines)")
    now = run(a.work, a.chapters, a.notes, a.video, a.edl)
    print(json.dumps(now, indent=2))
    if a.save:
        vl.save_json(a.save, now)
        print(f"baseline written to {a.save}")
    if a.compare:
        rows = compare(now, vl.load_json(a.compare))
        for name, x, y, v in rows:
            print(f"  {name:24} {x!s:>8} -> {y!s:<8} {v}")
        if any(v == "REGRESSION" for *_r, v in rows):
            print("REGRESSION against the baseline")
            return 1
        print(f"no regression ({len(rows)} numbers compared)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
