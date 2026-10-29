"""Match the colour of the cameras of one trip to each other, from clips they filmed at the same time and place.

Different cameras render the same light differently (on the first real trip an iPhone 17 Pro Max was about 5 % redder and 8 %
less blue than a DJI Osmo Pocket 3 on 95 pairs of clips filmed within a minute of each other). Cutting between them makes the
picture jump. A correction that pulls every shot towards the AVERAGE balance of the edit (`color plan --wb median`) also flattens
real differences of light (noon and sunset); this one only removes what belongs to the camera:

1. pairs: clips of two different cameras that started within `window` seconds of each other (the same place, the same light);
2. for each pair, the difference of colour balance (R/G and B/G of the mean picture, as log ratios);
3. for each pair of cameras, the median of those differences (robust to pairs that point at different things), kept only when
   there are enough pairs and the offset is clearly larger than its own uncertainty;
4. one offset per camera from all camera pairs together (least squares), measured against the camera that filmed most of the
   trip: that camera is left as it is and the others are matched to it (a camera without evidence is left as it is too, so it
   stays as close to the main camera as it was);
5. a white-balance gain per camera that removes its offset, applied in linear light like every other correction (`correct.gain`).

Measured out of sample on that trip (estimated on day 1, applied through FFmpeg to day 2 and measured again): see
`docs/CALIBRATION.md`.
"""
from __future__ import annotations

import json
import math
import os
import statistics
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import vid_color as vc
import vidlib as vl

WINDOW = 60.0             # seconds between the starts of two clips that count as the same moment
MIN_PAIRS = 8             # pairs of two cameras needed before their offset is believed
MIN_OFFSET = 0.02         # log-ratio below which an offset is ignored (2 %)
MAX_GAIN = 0.12           # a camera gain stays within +-12 % (linear light)
STATS_FILE = "camera_stats.json"


def _linear_exponent(x: float = vc.TARGET_MEAN, d: float = 0.01) -> float:
    """How much a log ratio of encoded values grows in linear light (about 2.2 near mid grey)."""
    return math.log(vc.to_linear(x * math.exp(d)) / vc.to_linear(x)) / d


def cross_camera_pairs(inventory: list, scores: dict, window: float = WINDOW, min_duration: float = 4.9) -> list:
    """[(a, b, gap)] inventory indexes of clips of two different cameras that started within `window` seconds."""
    recs = sorted((r for r in inventory if r["index"] in scores and r.get("dup_of") is None and not r.get("error")
                   and (r.get("duration") or 0.0) >= min_duration and r.get("taken_epoch")),
                  key=lambda r: (r["taken_epoch"], r["index"]))
    out = []
    for i, a in enumerate(recs):
        for b in recs[i + 1:]:
            gap = b["taken_epoch"] - a["taken_epoch"]
            if gap > window:
                break
            if (a.get("device") or "?") != (b.get("device") or "?"):
                out.append((a["index"], b["index"], round(gap, 1)))
    return out


def measure(inventory: list, scores: dict, indexes: list, work: Optional[str | os.PathLike] = None, workers: int = 6,
            measure_fn=None) -> dict:
    """{index: signalstats means} of each clip's best window, cached in `work/camera_stats.json` (by path and start)."""
    measure_fn = measure_fn or vc.measure_window
    by = {r["index"]: r for r in inventory}
    cache_path = Path(work) / STATS_FILE if work else None
    cache = {}
    if cache_path and cache_path.is_file():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except ValueError:
            cache = {}

    def key(i):
        return f"{by[i]['path']}|{scores[i].get('best_start', 0.0):.2f}"

    todo = [i for i in indexes if key(i) not in cache]

    def one(i):
        r = by[i]
        return i, measure_fn(r["path"], scores[i].get("best_start", 0.0), 1.0, bool(r.get("hdr")))

    with ThreadPoolExecutor(max(1, workers)) as ex:
        for i, s in ex.map(one, todo):
            if s:
                cache[key(i)] = s
    if cache_path and todo:
        vl.save_json(cache_path, cache)
    return {i: cache[key(i)] for i in indexes if key(i) in cache}


def balance(stats: dict) -> Optional[tuple]:
    """(log R/G, log B/G) of a clip's mean picture, or None for a picture too dark to read."""
    r, g, b = vc.yuv_means_to_rgb(stats["YAVG"], stats["UAVG"], stats["VAVG"])
    if min(r, g, b) < 0.03:
        return None
    return math.log(r / g), math.log(b / g)


def pair_offsets(pairs: list, stats: dict, device_of: dict) -> dict:
    """{(camera A, camera B): [(dR, dB), ...]} with A < B alphabetically: how much redder / bluer B is than A in each pair."""
    out: dict = {}
    for a, b, _gap in pairs:
        if a not in stats or b not in stats:
            continue
        ba, bb = balance(stats[a]), balance(stats[b])
        if ba is None or bb is None:
            continue
        da, db = device_of[a], device_of[b]
        if da > db:
            da, db, ba, bb = db, da, bb, ba
        out.setdefault((da, db), []).append((bb[0] - ba[0], bb[1] - ba[1]))
    return out


def _robust(values: list) -> tuple:
    """(median, standard error of the median) from the spread of the middle half."""
    med = statistics.median(values)
    if len(values) < 4:
        return med, float("inf")
    q = statistics.quantiles(values, n=4)
    sigma = (q[2] - q[0]) / 1.349
    return med, 1.2533 * sigma / math.sqrt(len(values))


def _solve(devices: list, edges: list, weight_of: dict) -> dict:
    """Least squares offsets o[d] with o[b] - o[a] ~ m for every edge (a, b, m, n), and sum(weight * o) = 0."""
    k = len(devices)
    idx = {d: i for i, d in enumerate(devices)}
    n = k + 1
    A = [[0.0] * n for _ in range(n)]
    y = [0.0] * n
    for a, b, m, w in edges:
        i, j = idx[a], idx[b]
        A[i][i] += w
        A[j][j] += w
        A[i][j] -= w
        A[j][i] -= w
        y[i] -= w * m
        y[j] += w * m
    total = sum(weight_of.get(d, 0.0) for d in devices) or 1.0
    for d in devices:                                     # the constraint row / column (a Lagrange multiplier)
        A[k][idx[d]] = A[idx[d]][k] = weight_of.get(d, 0.0) / total
    # Gaussian elimination with partial pivoting
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(A[r][c]))
        if abs(A[p][c]) < 1e-12:
            continue
        A[c], A[p], y[c], y[p] = A[p], A[c], y[p], y[c]
        for r in range(n):
            if r != c and A[r][c]:
                f = A[r][c] / A[c][c]
                A[r] = [x - f * z for x, z in zip(A[r], A[c])]
                y[r] -= f * y[c]
    return {d: (y[idx[d]] / A[idx[d]][idx[d]] if abs(A[idx[d]][idx[d]]) > 1e-12 else 0.0) for d in devices}


def _reachable(start: Optional[str], edges: list) -> set:
    """The cameras linked to `start` through trusted camera pairs (only those can be matched to it)."""
    if start is None:
        return set()
    seen, todo = {start}, [start]
    while todo:
        d = todo.pop()
        for a, b, _m, _n in edges:
            for x, y in ((a, b), (b, a)):
                if x == d and y not in seen:
                    seen.add(y)
                    todo.append(y)
    return seen


def estimate(inventory: list, scores: dict, work: Optional[str | os.PathLike] = None, window: float = WINDOW,
             workers: int = 6, measure_fn=None, reference: Optional[str] = None) -> dict:
    """Per-camera colour offsets and the gains that remove them, with the evidence behind them. The other cameras are matched
    to `reference` (default: the camera that filmed most of the trip), which is left as it is."""
    device_of = {r["index"]: (r.get("device") or "?") for r in inventory}
    pairs = cross_camera_pairs(inventory, scores, window)
    stats = measure(inventory, scores, sorted({x for p in pairs for x in p[:2]}), work, workers, measure_fn)
    offsets = pair_offsets(pairs, stats, device_of)
    usage: dict = {}
    for r in inventory:
        if r["index"] in scores and r.get("dup_of") is None:
            usage[device_of[r["index"]]] = usage.get(device_of[r["index"]], 0) + 1
    evidence = []
    edges_r, edges_b = [], []
    for (da, db), vals in sorted(offsets.items()):
        mr, sr = _robust([v[0] for v in vals])
        mb, sb = _robust([v[1] for v in vals])
        trusted = len(vals) >= MIN_PAIRS
        use_r = trusted and abs(mr) > max(MIN_OFFSET, 2 * sr)
        use_b = trusted and abs(mb) > max(MIN_OFFSET, 2 * sb)
        evidence.append({"cameras": [da, db], "pairs": len(vals), "red": round(mr, 4), "red_error": round(sr, 4),
                         "blue": round(mb, 4), "blue_error": round(sb, 4), "used": {"red": use_r, "blue": use_b}})
        if use_r:                                       # an offset within the noise is unknown, not zero: it is left out
            edges_r.append((da, db, mr, len(vals)))
        if use_b:
            edges_b.append((da, db, mb, len(vals)))
    devices = sorted(usage)
    if reference is not None and reference not in devices:
        raise vl.EngineError(f"camera '{reference}' is not in this library (cameras: {', '.join(devices)})")
    main = reference or (max(devices, key=lambda d: (usage[d], d)) if devices else None)
    anchor = {d: (1.0 if d == main else 0.0) for d in devices}
    off_r = _solve(devices, edges_r, anchor) if edges_r else {d: 0.0 for d in devices}
    off_b = _solve(devices, edges_b, anchor) if edges_b else {d: 0.0 for d in devices}
    linked_r = _reachable(main, edges_r)
    linked_b = _reachable(main, edges_b)
    connected = linked_r | linked_b
    k = _linear_exponent()
    gains = {}
    for d in devices:
        if d not in connected:
            gains[d] = [1.0, 1.0, 1.0]
            continue
        gr = math.exp(-k * off_r.get(d, 0.0)) if d in linked_r else 1.0
        gb = math.exp(-k * off_b.get(d, 0.0)) if d in linked_b else 1.0
        gains[d] = [round(vc.clamp(gr, 1 - MAX_GAIN, 1 + MAX_GAIN), 4), 1.0, round(vc.clamp(gb, 1 - MAX_GAIN, 1 + MAX_GAIN), 4)]
    return {"window": window, "pairs": len(pairs), "measured": len(stats), "usage": usage, "reference": main, "evidence": evidence,
            "offsets": {d: [round(off_r.get(d, 0.0), 4), round(off_b.get(d, 0.0), 4)] for d in devices}, "gains": gains,
            "not_matched": sorted(set(devices) - connected)}


def format_report(rep: dict) -> str:
    lines = [f"{rep['pairs']} pairs of clips from two cameras filmed within {rep['window']:g} s of each other; {rep['measured']} clips measured; "
             f"matched to {rep.get('reference')}"]
    for e in rep["evidence"]:
        a, b = e["cameras"]
        lines.append(f"  {b} vs {a}: {e['pairs']} pairs; redder by {e['red']:+.3f} (+-{e['red_error']:.3f}), bluer by "
                     f"{e['blue']:+.3f} (+-{e['blue_error']:.3f}) log ratio"
                     + ("" if e["used"]["red"] or e["used"]["blue"] else "  -> not used (too few pairs or within the noise)"))
    for d, g in rep["gains"].items():
        lines.append(f"  {d:22} clips {rep['usage'].get(d, 0):>4}  gain R {g[0]:.3f}  B {g[2]:.3f}"
                     + ("  (no evidence: unchanged)" if d in rep["not_matched"] else ""))
    return "\n".join(lines)


def apply_to_edl(edl: dict, rep: dict, inventory: list) -> int:
    """Multiply each shot's `correct.gain` by its camera's gain. Shots already matched (listed in `edl['camera_match']['shots']`
    with their camera) are skipped, so running it twice changes nothing; `color plan --overwrite` keeps the camera gain of a
    matched shot. Returns how many shots changed."""
    by_path = {os.path.normcase(os.path.abspath(r["path"])): (r.get("device") or "?") for r in inventory}
    cm = edl.setdefault("camera_match", {"gains": rep["gains"], "shots": {}})
    cm["gains"] = rep["gains"]
    cm["reference"] = rep.get("reference")
    done = dict(cm.get("shots") or {})
    n = 0
    for j, shot in enumerate(edl.get("shots") or []):
        if str(j) in done:
            continue
        dev = by_path.get(os.path.normcase(os.path.abspath(shot.get("clip", ""))))
        g = rep["gains"].get(dev) if dev else None
        if not g or all(abs(x - 1.0) < 1e-6 for x in g):
            continue
        c = vc.normalize_correction(shot.get("correct")) or {k: (list(v) if isinstance(v, list) else v)
                                                              for k, v in vc.IDENTITY_CORRECTION.items()}
        c["gain"] = [round(vc.clamp(a * b, 0.5, 2.0), 4) for a, b in zip(c["gain"], g)]
        shot["correct"] = vc.normalize_correction(c)
        done[str(j)] = dev
        n += 1
    cm["shots"] = dict(sorted(done.items(), key=lambda kv: int(kv[0])))
    return n


def camera_gain_of(edl: dict, j: int) -> Optional[list]:
    """The camera gain already multiplied into shot j's correction, or None."""
    cm = edl.get("camera_match") or {}
    dev = (cm.get("shots") or {}).get(str(j))
    return (cm.get("gains") or {}).get(dev) if dev else None
