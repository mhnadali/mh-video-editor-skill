"""Usable-moment extraction: where inside a clip is the part worth using?

`score` measures three one-second windows of every clip, which is enough to rank clips but not to cut them: the best
second of a 40-second clip is rarely the best 1.2 seconds for the slot it will fill. This module looks at the WHOLE clip
(only for the clips a storyboard actually uses, so it stays cheap): one FFmpeg pass at a few frames per second measures
exposure, motion, sharpness and the camera's own movement, and a sliding window picks the segment that is
  * sharp, well exposed and steady (no hand-held wobble, no exposure flicker),
  * free of a cut or a flash,
  * the right kind of moment for the slot (the action peak for an action slot, a calm stretch for a calm one),
  * away from the first and last half second of the clip, where the camera is still being raised or lowered.
It also names the kind of camera movement (static, pan, tilt, push/pull, hand-held) and where the action ends.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import vid_editorial as ed
import vid_sheets
import vidlib as vl

FPS = 6                        # analysis frames per second
MAX_SPAN = 45.0                # seconds of a clip that are analysed (around the interesting part for longer clips)
GW, GH = 48, 27
MARGIN = 0.4                   # seconds at both ends of a clip that are never chosen
STEP = 0.25                    # sliding step of the window
MIN_PEAK = 0.5                 # motion (mean frame difference, 0-255) below which a clip has no action peak
DECODE_THREADS = 2

_GRAPH = ("fps={fps},scale=480:-2,{tm}format=yuv420p,split=4[a][b][c][g];"
          "[a]signalstats,metadata=print:file=sig.txt,nullsink;"
          "[b]tblend=all_mode=difference,signalstats,metadata=print:file=mot.txt,nullsink;"
          "[c]format=gray,blurdetect,metadata=print:file=blur.txt[o];"
          "[g]scale=48:27:flags=area,format=gray[og]")


# --------------------------------------------------------------------------- reading a clip

def _blocks(path: Path, keys: set) -> list:
    """The numeric values of `keys` for every frame block of an FFmpeg metadata file, in order."""
    out: list = []
    if not path.is_file():
        return out
    cur: Optional[dict] = None
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("frame:"):
            cur = {}
            out.append(cur)
            continue
        m = re.match(r"lavfi\.(?:signalstats\.)?(\w+)=(\S+)", line.strip())
        if m and cur is not None and m.group(1) in keys:
            v = vl.parse_float(m.group(2))
            if v is not None:
                cur[m.group(1)] = v
    return out


def half(frame: bytes, w: int = GW, h: int = GH) -> bytes:
    """A 2x2-averaged copy of a gray frame (24x13): the shift search runs on this to stay cheap in pure Python."""
    hw, hh = w // 2, h // 2
    out = bytearray(hw * hh)
    for y in range(hh):
        r0, r1 = (2 * y) * w, (2 * y + 1) * w
        for x in range(hw):
            out[y * hw + x] = (frame[r0 + 2 * x] + frame[r0 + 2 * x + 1] + frame[r1 + 2 * x] + frame[r1 + 2 * x + 1]) >> 2
    return bytes(out)


def shifts(frames: list, w: int = GW, h: int = GH) -> tuple:
    """Per frame step: the global shift of the picture (dx, dy in 48-px units) and the shift of its left and right halves
    (dxl, dxr), which differ when the camera pushes in or pulls back."""
    hw, hh = w // 2, h // 2
    small = [half(f, w, h) for f in frames]
    dx, dy, dxl, dxr = [0.0], [0.0], [0.0], [0.0]
    for a, b in zip(small, small[1:]):
        if ed._texture(a, hw, hh) < 1.5:                          # noqa: SLF001  nothing to track
            dx.append(0.0)
            dy.append(0.0)
            dxl.append(0.0)
            dxr.append(0.0)
            continue
        sx, sy = ed.motion_between(a, b, hw, hh, rng=2)
        dx.append(2.0 * sx)
        dy.append(2.0 * sy)
        left_a = b"".join(a[r * hw:r * hw + hw // 2] for r in range(hh))
        left_b = b"".join(b[r * hw:r * hw + hw // 2] for r in range(hh))
        right_a = b"".join(a[r * hw + hw // 2:(r + 1) * hw] for r in range(hh))
        right_b = b"".join(b[r * hw + hw // 2:(r + 1) * hw] for r in range(hh))
        lx, _ = ed.motion_between(left_a, left_b, hw // 2, hh, rng=2)
        rx, _ = ed.motion_between(right_a, right_b, hw - hw // 2, hh, rng=2)
        dxl.append(2.0 * lx)
        dxr.append(2.0 * rx)
    return dx, dy, dxl, dxr


def scan(path: str, start: float, span: float, hdr: bool = False, fps: int = FPS) -> dict:
    """Measure `span` seconds of a clip from `start`: a dict of per-frame series (exposure, motion, blur, shifts)."""
    graph = _GRAPH.format(fps=fps, tm=vid_sheets.TONEMAP_HLG if hdr else "")
    with tempfile.TemporaryDirectory(prefix="mhvid_moment_") as d:
        cmd = [vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-threads", str(DECODE_THREADS), "-ss", f"{max(0.0, start):.3f}",
               "-t", f"{span:.3f}", "-i", vl.ffp(path), "-an", "-filter_complex", graph, "-map", "[o]", "-f", "null", "-",
               "-map", "[og]", "-f", "rawvideo", "gray.raw"]
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=d)
        sig = _blocks(Path(d) / "sig.txt", {"YAVG", "SATAVG"})
        mot = _blocks(Path(d) / "mot.txt", {"YAVG"})
        blur = _blocks(Path(d) / "blur.txt", {"blur"})
        raw = (Path(d) / "gray.raw").read_bytes() if (Path(d) / "gray.raw").is_file() else b""
    n = min(len(sig), len(mot), len(raw) // (GW * GH))
    if r.returncode != 0 or n < 4:
        raise vl.EngineError(f"could not analyse {Path(path).name}: {(r.stderr or '')[-200:]}")
    frames = [raw[i * GW * GH:(i + 1) * GW * GH] for i in range(n)]
    dx, dy, dxl, dxr = shifts(frames)
    return {"start": start, "fps": fps, "n": n,
            "bright": [s.get("YAVG", 0.0) for s in sig[:n]], "motion": [m.get("YAVG", 0.0) for m in mot[:n]],
            "blur": [(blur[i].get("blur") if i < len(blur) else 4.0) or 4.0 for i in range(n)],
            "dx": dx, "dy": dy, "dxl": dxl, "dxr": dxr}


# --------------------------------------------------------------------------- choosing a segment

def _mean(v: list) -> float:
    return sum(v) / len(v) if v else 0.0


def _std(v: list) -> float:
    if len(v) < 2:
        return 0.0
    m = _mean(v)
    return (sum((x - m) ** 2 for x in v) / len(v)) ** 0.5


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def classify_motion(dx: list, dy: list, dxl: list, dxr: list) -> str:
    """static | pan | tilt | push-pull | hand-held for a stretch of per-frame shifts."""
    n = len(dx)
    if n < 3:
        return "static"
    mdx, mdy = _mean(dx), _mean(dy)
    mag = (mdx * mdx + mdy * mdy) ** 0.5
    wob = _mean([abs(x - mdx) + abs(y - mdy) for x, y in zip(dx, dy)])
    spread = _mean([abs(r - l) for l, r in zip(dxl, dxr)])
    opposite = _mean([1.0 if (l * r) < 0 else 0.0 for l, r in zip(dxl, dxr)])
    if opposite > 0.5 and spread > 0.8 and wob < 1.6:
        return "push-pull"                         # the two halves move apart or together although the whole picture hardly shifts
    if mag < 0.25 and wob < 0.6:
        return "static"
    if wob > 1.6 and wob > 1.2 * mag:
        return "hand-held"
    if abs(mdx) >= abs(mdy):
        return "pan" if mag >= 0.6 else "static"
    return "tilt" if mag >= 0.6 else "static"


def end_of_action(motion: list, fps: int, peak: int) -> Optional[float]:
    """Seconds (in the clip's own timeline, from the series start) where the action has died down after its peak."""
    if not motion or peak >= len(motion) - 1:
        return None
    top = motion[peak]
    if top <= 0:
        return None
    for i in range(peak, len(motion)):
        if motion[i] < 0.3 * top and all(m < 0.35 * top for m in motion[i:i + max(2, fps // 2)]):
            return round(i / fps, 2)
    return None


def smooth(v: list, k: int = 3) -> list:
    out = []
    for i in range(len(v)):
        seg = v[max(0, i - k // 2):i + k // 2 + 1]
        out.append(_mean(seg))
    return out


def best_segment(series: dict, length: float, want: str = "any", margin: float = MARGIN, step: float = STEP) -> dict:
    """The best `length`-second segment of an analysed clip.

    want: 'action' (the most eventful stretch), 'calm' (the quietest steady stretch) or 'any' (sharp and steady only).
    Returns {start, length, score, parts, motion_type, peak_at, end_of_action, window (frames)} with `start` in the clip's
    own time (series start included)."""
    fps, n = series["fps"], series["n"]
    t0 = series.get("start", 0.0)
    win = max(2, int(round(length * fps)))
    keep = int(math.ceil(margin * fps - 1e-9))                # whole frames, never less than the margin
    first = keep
    last = n - win - keep
    if last < first:                                         # a clip as short as the segment: take what there is
        first, last = 0, max(0, n - win)
    med = sorted(series["motion"])[len(series["motion"]) // 2] if n else 0.0
    cut_level = max(6.0 * max(med, 0.5), 25.0)               # a frame that differs from the last one by far: a cut or a flash
    # smoothing must not spread a cut into its neighbours and make them look like the action peak
    motion = smooth([m if m <= cut_level else med for m in series["motion"]])
    ref = sorted(motion)[int(0.9 * (len(motion) - 1))] if motion else 1.0
    ref = max(ref, 1e-6)
    # the action peak is the most eventful frame that is not a cut; a clip where nothing moves has no peak at all
    calm_frames = [i for i in range(n) if series["motion"][i] <= cut_level]
    peak: Optional[int] = max(calm_frames, key=lambda i: (motion[i], -i)) if calm_frames else None
    if peak is not None and motion[peak] < MIN_PEAK:
        peak = None
    best: Optional[dict] = None
    stride = max(1, int(round(step * fps)))
    for s in range(first, last + 1, stride):
        e = min(n, s + win)
        seg_m = series["motion"][s:e]
        seg_b = series["bright"][s:e]
        seg_blur = series["blur"][s:e]
        dx, dy = series["dx"][s:e], series["dy"][s:e]
        spike = max(seg_m) if seg_m else 0.0
        cut = spike > cut_level
        sharp = _clamp(1.0 - (_mean(seg_blur) - 2.0) / 8.0)
        luma = _mean(seg_b)
        expo = _clamp(1.0 - abs(luma - 120.0) / 120.0) * _clamp(1.0 - _std(seg_b) / 25.0)
        mdx, mdy = _mean(dx), _mean(dy)
        wob = _mean([abs(x - mdx) + abs(y - mdy) for x, y in zip(dx, dy)]) if dx else 0.0
        steady = _clamp(1.0 - wob / 4.0)
        mot = _mean(motion[s:e]) / ref
        fit = _clamp(mot) if want == "action" else _clamp(1.0 - mot) if want == "calm" else 0.5
        has_peak = peak is not None and s <= peak < e
        centred = 1.0 - min(1.0, abs((s + win / 2.0) - peak) / max(win, 1)) if want == "action" and peak is not None else 0.5
        score = 0.25 * sharp + 0.15 * expo + 0.25 * steady + 0.25 * fit + 0.10 * (centred if want == "action" else 0.5)
        if has_peak and want == "action":
            score += 0.05
        if cut:
            score -= 1.0
        cand = {"s": s, "score": round(score, 4), "parts": {"sharp": round(sharp, 2), "expo": round(expo, 2),
                "steady": round(steady, 2), "fit": round(fit, 2)}, "cut": cut}
        if best is None or cand["score"] > best["score"]:
            best = cand
    if best is None:
        best = {"s": 0, "score": 0.0, "parts": {}, "cut": False}
    s = best["s"]
    e = min(n, s + win)
    kind = classify_motion(series["dx"][s:e], series["dy"][s:e], series["dxl"][s:e], series["dxr"][s:e])
    eoa = end_of_action(motion, fps, peak) if peak is not None else None
    return {"start": round(t0 + s / fps, 2), "length": round((e - s) / fps, 2), "score": best["score"], "parts": best["parts"],
            "motion_type": kind, "peak_at": None if peak is None else round(t0 + peak / fps, 2),
            "end_of_action": None if eoa is None else round(t0 + eoa, 2), "had_cut": best["cut"]}


# --------------------------------------------------------------------------- caching and the storyboard

def _cache_path(work: Path, path: str, start: float, span: float, fps: int) -> Path:
    st = os.stat(path)
    key = hashlib.sha1(json.dumps([os.path.abspath(path), st.st_size, int(st.st_mtime), round(start, 2), round(span, 2), fps]).encode()
                       ).hexdigest()[:16]
    return work / "moments" / f"{key}.json"


def analyse_clip(path: str, duration: float, work: Path, hdr: bool = False, around: Optional[float] = None,
                 fps: int = FPS) -> dict:
    """The analysed series of a clip, from the cache when the file is unchanged. Long clips are analysed in a window of
    MAX_SPAN seconds around `around` (the clip's best window from `score`)."""
    start = 0.0
    span = min(duration, MAX_SPAN)
    if duration > MAX_SPAN and around is not None:
        start = max(0.0, min(around - span / 2.0, duration - span))
    cache = _cache_path(work, path, start, span, fps)
    if cache.is_file():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    series = scan(path, start, span, hdr, fps)
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    tmp.write_text(json.dumps(series), encoding="utf-8")
    vl.replace_file(tmp, cache)
    return series


def want_for(row: dict) -> str:
    if row.get("role") == "action" or (row.get("energy") or 0.0) >= 0.6:
        return "action"
    if row.get("energy") is not None and row["energy"] <= 0.3 and row.get("role") in ("establishing", "portrait", "detail"):
        return "calm"
    return "any"


KEEP_START = ("set by the user", "as approved")      # starts that `story refine` must not move


def refine_storyboard(sb: dict, inventory: list, scores: dict, work: str | os.PathLike, workers: int = 4,
                      log=lambda *_: None) -> dict:
    """Replace each row's start with the best moment of its clip (a copy of `sb` with `moment` details on every row). A start
    the user set on the review page, or one carried over from an approved storyboard (a cut-down), is kept."""
    wk = Path(work)
    by = {r["index"]: r for r in inventory}
    rows = [dict(r) for r in sb["rows"]]

    def one(row: dict) -> tuple:
        rec = by[row["clip"]]
        sc = scores.get(row["clip"], {})
        length = float(row["len"]) + 0.8                    # the slot plus the overlap padding of the transitions
        try:
            series = analyse_clip(rec["path"], float(rec.get("duration") or 0.0), wk, bool(rec.get("hdr")), sc.get("best_start"))
        except vl.EngineError as e:
            return row, None, str(e)
        seg = best_segment(series, min(length, float(rec.get("duration") or length)), want_for(row))
        return row, seg, None

    with ThreadPoolExecutor(workers) as ex:
        results = list(ex.map(one, rows))
    changed = 0
    for row, seg, err in results:
        if seg is None:
            row["moment"] = {"error": err}
            continue
        row["moment"] = seg
        if row.get("start_kind") in KEEP_START:
            continue                                  # the user chose this start (review page) or approved it: analysed, not moved
        if abs(seg["start"] - row["start"]) > 0.1:
            changed += 1
        row["start"] = seg["start"]
        row["start_kind"] = f"refined moment ({seg['motion_type']})"
    out = dict(sb)
    out["rows"] = rows
    out["refined"] = {"clips": len(rows), "start_changed": changed}
    log(f"refined {len(rows)} clips; {changed} start times changed")
    return out
