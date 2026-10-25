"""Phase 2: score footage so shots are chosen on measured quality as well as on what the contact sheet shows.

For each clip, a few 1-second windows are measured in ONE FFmpeg pass (signalstats for exposure, contrast and
saturation, tblend+signalstats for motion, blurdetect for sharpness). The best window becomes `best_start`.
A tiny perceptual hash flags clips that show the same scene, so a shot list does not repeat itself.

Measurements are relative, not absolute: blurdetect reads low-texture skies as soft, so `flags` are hints for
the agent (who still LOOKS at the sheets), never automatic rejections of a clip the user asked for.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import vid_sheets
import vidlib as vl

WINDOW = 1.0
POSITIONS = (0.25, 0.5, 0.75)
ANALYSIS_WIDTH = 480

_GRAPH = ("fps=10,scale={w}:-2,{tm}format=yuv420p,split=3[a][b][c];"
          "[a]signalstats,metadata=print:file=sig.txt,nullsink;"
          "[b]tblend=all_mode=difference,signalstats,metadata=print:file=mot.txt,nullsink;"
          "[c]format=gray,blurdetect,metadata=print:file=blur.txt[o]")


def _read_metadata(path: Path, keys: set[str]) -> dict[str, float]:
    vals: dict[str, list[float]] = {}
    if path.exists():
        for line in path.read_text(errors="replace").splitlines():
            m = re.match(r"lavfi\.(?:signalstats\.)?(\w+)=(\S+)", line.strip())
            if m and m.group(1) in keys:
                v = vl.parse_float(m.group(2))
                if v is not None:
                    vals.setdefault(m.group(1), []).append(v)
    return {k: sum(v) / len(v) for k, v in vals.items()}


def measure_window(path: str, start: float, dur: float = WINDOW, hdr: bool = False, hw: bool = False) -> dict | None:
    """Raw metrics of one window, or None if FFmpeg produced nothing (unreadable window)."""
    graph = _GRAPH.format(w=ANALYSIS_WIDTH, tm=vid_sheets.TONEMAP_HLG if hdr else "")
    with tempfile.TemporaryDirectory(prefix="mhvid_score_") as d:
        cmd = [vl.find_ffmpeg()["ffmpeg"], "-v", "error"] + (["-hwaccel", "cuda"] if hw else []) + \
              ["-ss", f"{max(0.0, start):.2f}", "-t", str(dur), "-i", vl.ffp(path), "-an", "-filter_complex", graph,
               "-map", "[o]", "-f", "null", "-"]
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=d)
        sig = _read_metadata(Path(d) / "sig.txt", {"YAVG", "SATAVG", "YLOW", "YHIGH"})
        mot = _read_metadata(Path(d) / "mot.txt", {"YAVG"})
        blur = _read_metadata(Path(d) / "blur.txt", {"blur"})
    if r.returncode != 0 and not sig:
        return None
    if not sig:
        return None
    # blurdetect reports nothing when it finds no edges at all (black or featureless frames): treat as very soft
    return {"brightness": sig.get("YAVG", 0.0), "saturation": sig.get("SATAVG", 0.0),
            "contrast": sig.get("YHIGH", 0.0) - sig.get("YLOW", 0.0),
            "motion": mot.get("YAVG", 0.0), "blur": blur.get("blur", 10.0)}


def windows_for(duration: float, positions=POSITIONS, dur: float = WINDOW) -> list[float]:
    """Window start times; one window at 0 for clips shorter than two windows."""
    if duration <= 2 * dur:
        return [0.0]
    starts = []
    for p in positions:
        s = max(0.0, min(duration * p - dur / 2, duration - dur - 0.05))
        if not starts or abs(s - starts[-1]) >= dur:
            starts.append(round(s, 2))
    return starts


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def quality(m: dict) -> float:
    """0-100: sharper, well exposed and contrasty windows score higher. Motion is NOT part of quality."""
    sharp = _clamp01((8.0 - m["blur"]) / (8.0 - 2.5))
    b = m["brightness"]
    exposure = 1.0 if 80 <= b <= 170 else _clamp01(1.0 - (80 - b) / 60.0) if b < 80 else _clamp01(1.0 - (b - 170) / 60.0)
    contrast = _clamp01((m["contrast"] - 20.0) / 70.0)
    return round(100 * (0.45 * sharp + 0.35 * exposure + 0.20 * contrast), 1)


def flags(m: dict) -> list[str]:
    out = []
    if m["brightness"] < 45:
        out.append("dark")
    if m["brightness"] > 205:
        out.append("overexposed")
    if m["contrast"] < 35:
        out.append("flat")
    if m["blur"] > 9.0:
        out.append("blurry")
    if m["motion"] < 1.2:
        out.append("static")
    return out


def dhash(path: str, t: float, hdr: bool = False) -> int | None:
    """64-bit difference hash of one frame (9x8 grayscale)."""
    vf = (vid_sheets.TONEMAP_HLG if hdr else "") + "scale=9:8:flags=area,format=gray"
    r = subprocess.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-ss", f"{max(0.0, t):.2f}", "-i", vl.ffp(path),
                        "-frames:v", "1", "-vf", vf, "-f", "rawvideo", "-"], capture_output=True)
    px = r.stdout
    if r.returncode != 0 or len(px) < 72:
        return None
    h = 0
    for y in range(8):
        for x in range(8):
            h = (h << 1) | (1 if px[y * 9 + x] > px[y * 9 + x + 1] else 0)
    return h


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def score_clip(rec: dict, hw: bool = False) -> dict:
    """Score one inventory record. Returns the new fields for that clip."""
    if rec.get("error") or not rec.get("duration"):
        return {"index": rec["index"], "error": rec.get("error") or "no duration"}
    wins = []
    for s in windows_for(rec["duration"]):
        m = measure_window(rec["path"], s, WINDOW, bool(rec.get("hdr")), hw)
        if m is None and hw:
            m = measure_window(rec["path"], s, WINDOW, bool(rec.get("hdr")), False)     # GPU decode failed: software
        if m:
            m["start"] = s
            m["quality"] = quality(m)
            wins.append(m)
    if not wins:
        return {"index": rec["index"], "error": "could not measure any window"}
    best = max(wins, key=lambda w: (w["quality"], w["motion"]))
    mid = best["start"] + WINDOW / 2
    return {"index": rec["index"], "path": rec["path"], "name": rec["name"], "size": rec.get("size"),
            "mtime": rec.get("mtime"), "quality": best["quality"],
            "best_start": best["start"],
            "brightness": round(best["brightness"], 1), "contrast": round(best["contrast"], 1),
            "saturation": round(best["saturation"], 1), "blur": round(best["blur"], 2),
            "motion": round(best["motion"], 2), "flags": flags(best), "windows": len(wins),
            "hash": dhash(rec["path"], mid, bool(rec.get("hdr")))}


def percentile_rank(values: list[float]) -> list[float]:
    """Energy 0-100 = share of clips with less motion (ties share their lowest rank)."""
    n = len(values)
    if n <= 1:
        return [50.0] * n
    order = sorted(values)
    import bisect
    return [round(100.0 * bisect.bisect_left(order, v) / (n - 1), 1) for v in values]


def mark_similar(scores: list[dict], max_distance: int = 8) -> int:
    """`similar_to` = index of an earlier clip whose frame hash is within `max_distance` bits (same scene)."""
    seen: list[tuple[int, int]] = []
    n = 0
    for s in scores:
        s["similar_to"] = None
        h = s.get("hash")
        if h is None:
            continue
        for idx, hh in seen:
            if hamming(h, hh) <= max_distance:
                s["similar_to"] = idx
                n += 1
                break
        seen.append((s["index"], h))
    return n


def score_inventory(records: list[dict], workers: int | None = None, limit: int | None = None,
                    hw: bool | None = None, progress=None, previous: dict[str, dict] | None = None) -> list[dict]:
    """Score every non-duplicate clip. `previous` = earlier score entries keyed by path: entries whose file is
    unchanged (same size and mtime) are reused, so re-running after adding clips only measures the new ones."""
    recs = [r for r in records if r.get("dup_of") is None]       # exact duplicates are never candidates
    if limit:
        recs = recs[:limit]
    if hw is None:
        import vid_render
        hw = vid_render.nvenc_works()
    previous = previous or {}
    done = 0
    reused = 0

    def one(r):
        nonlocal done, reused
        old = previous.get(r["path"])
        if old and old.get("size") == r.get("size") and old.get("mtime") == r.get("mtime") and "quality" in old:
            out = dict(old)
            out["index"] = r["index"]
            reused += 1
        else:
            out = score_clip(r, hw)
        done += 1
        if progress and done % 25 == 0:
            progress(f"scored {done}/{len(recs)} ({reused} reused)")
        return out

    with ThreadPoolExecutor(workers or vl.cpu_workers(6)) as ex:
        scores = list(ex.map(one, recs))
    ok = [s for s in scores if "quality" in s]
    for s, e in zip(ok, percentile_rank([s["motion"] for s in ok])):
        s["energy"] = e
    mark_similar(ok)
    return scores


def save_scores(scores: list[dict], workdir: str | os.PathLike) -> str:
    p = Path(workdir) / "scores.json"
    vl.save_json(p, {"summary": summarize(scores), "scores": scores})
    return str(p)


def load_previous(workdir: str | os.PathLike) -> dict[str, dict]:
    """Earlier score entries keyed by clip path (empty if none)."""
    p = Path(workdir) / "scores.json"
    if not p.is_file():
        return {}
    try:
        return {s["path"]: s for s in vl.load_json(p)["scores"] if s.get("path")}
    except (vl.EngineError, KeyError):
        return {}


def load_scores(workdir: str | os.PathLike) -> dict[int, dict]:
    p = Path(workdir) / "scores.json"
    if not p.is_file():
        raise vl.EngineError(f"no scores in {workdir}; run `score` first")
    return {s["index"]: s for s in vl.load_json(p)["scores"] if "quality" in s}


def summarize(scores: list[dict]) -> dict:
    ok = [s for s in scores if "quality" in s]
    fl: dict[str, int] = {}
    for s in ok:
        for f in s["flags"]:
            fl[f] = fl.get(f, 0) + 1
    return {"clips_scored": len(ok), "unmeasurable": len(scores) - len(ok),
            "mean_quality": round(sum(s["quality"] for s in ok) / max(len(ok), 1), 1), "flags": fl,
            "similar": sum(1 for s in ok if s.get("similar_to") is not None)}


# --------------------------------------------------------------------------- ranking and picking

def parse_index_spec(spec: str | None) -> set[int] | None:
    """'0-5,9,12-14' -> {0,1,2,3,4,5,9,12,13,14}. None/empty -> None (no filter). Bad text raises EngineError."""
    if not spec or not spec.strip():
        return None
    out: set[int] = set()
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        try:
            if "-" in part:
                a, b = part.split("-", 1)
                lo, hi = int(a), int(b)
                if hi < lo:
                    raise ValueError
                out.update(range(lo, hi + 1))
            else:
                out.add(int(part))
        except ValueError:
            raise vl.EngineError(f"bad index list '{spec}': use numbers and ranges like 0-40,52,100-120")
    return out


def candidates(records: list[dict], scores: dict[int, dict], skip_flagged: bool = True, skip_similar: bool = True,
               min_quality: float = 0.0, only: set[int] | None = None, exclude: set[int] | None = None) -> list[dict]:
    """Clips that may be used, each merged with its score. Never includes exact duplicates.

    `only` restricts the pool to those inventory indexes (the agent chooses them from the contact sheets: measured
    energy cannot tell an off-road jump from a hand-held burger close-up); `exclude` removes indexes."""
    out = []
    for r in records:
        s = scores.get(r["index"])
        if not s or r.get("dup_of") is not None or r.get("error"):
            continue
        if only is not None and r["index"] not in only:
            continue
        if exclude and r["index"] in exclude:
            continue
        if skip_flagged and any(f in s["flags"] for f in ("dark", "overexposed", "blurry")):
            continue
        if skip_similar and s.get("similar_to") is not None:
            continue
        if s["quality"] < min_quality:
            continue
        out.append({**r, **s})
    return out


def pick(cands: list[dict], intro: int, build: int, payoff: int, outro: int = 1) -> list[dict]:
    """Order candidates as a story: calm opening, rising build, high-energy payoff, calm closing.

    Intro/outro take the best-quality calm clips; the build takes clips by rising energy; the payoff takes the highest
    energy of the rest, best quality first within the same energy band. Raises if there are too few candidates.
    """
    need = intro + build + payoff + outro
    if len(cands) < need:
        raise vl.EngineError(f"only {len(cands)} usable candidates for {need} shots; relax the filters (--allow-flagged/--allow-similar)")
    pool = sorted(cands, key=lambda c: -c["quality"])
    calm = sorted(pool, key=lambda c: (c["energy"] > 60, -c["quality"]))      # calm first, then by quality
    chosen_intro = calm[:intro]
    used = {c["index"] for c in chosen_intro}
    chosen_outro = [c for c in calm if c["index"] not in used][:outro]
    used |= {c["index"] for c in chosen_outro}
    rest = [c for c in pool if c["index"] not in used]
    # payoff: top energy, then build: the next best quality ordered by rising energy
    by_energy = sorted(rest, key=lambda c: (-c["energy"], -c["quality"]))
    chosen_payoff = by_energy[:payoff]
    used |= {c["index"] for c in chosen_payoff}
    chosen_build = [c for c in rest if c["index"] not in used][:build]
    chosen_build.sort(key=lambda c: c["energy"])
    chosen_payoff.sort(key=lambda c: c["energy"])
    return chosen_intro + chosen_build + chosen_payoff + chosen_outro
