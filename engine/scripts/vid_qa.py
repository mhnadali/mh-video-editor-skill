"""Phase 3: automated quality gate for a rendered video.

One pass over the picture (signalstats, scene score, frame difference at low resolution) and one over the sound
(EBU R128 loudness, true peak, silence) feed a list of checks. Every check knows what the EDL says SHOULD happen
(fades at the ends, a white flash on the drop, a deliberate dip to black) so intended effects are never reported as
defects. The three bugs that reached the user during the Maranjab project (white first frames, purple cast, 400 MB
file) and the ones found while building the engine (a black frame from `rectcrop`, loudness above target) are all
caught here.

Verdict: FAIL if any error, PASS WITH WARNINGS if any warning, else PASS. Measurements are advice for a human: a
warning says "look here", with the time stamps.
"""
from __future__ import annotations

import json
import os
import re
import statistics
import subprocess
import tempfile
from pathlib import Path

import vid_edl as ve
import vid_editorial
import vid_sheets
import vidlib as vl

ANALYSIS_WIDTH = 270

# thresholds (calibrated on the approved Maranjab video: YAVG 16-229, mean chroma U 121 / V 132, no frame with both
# chroma means above 134, 2 black frames only inside the fades)
BLACK_YAVG = 20.0
BLACK_YMAX = 70.0
WHITE_YAVG = 222.0             # natural frames of the approved video never exceed 215; limited-range white is 235
WHITE_YLOW = 200.0             # a washed-out frame is bright even in its darkest 10%
CAST_BOTH_ABOVE = 134.0        # U and V both this high for a whole second = magenta/purple
CAST_BOTH_BELOW = 120.0 - 14   # both this low = green
FREEZE_SECONDS = 0.5
FREEZE_DIFF = 0.02
LOUDNESS_TOLERANCE = (-3.0, 2.0)          # LU around the target
TRUE_PEAK_WARN = -1.0
TRUE_PEAK_ERROR = -0.1
MAX_MB_PER_SECOND = 6.0
MAX_FILE_MB = 400.0
EXPOSURE_JUMP = 45.0            # luma levels between the 4 frames before and after a hard cut
EXPOSURE_JUMP_SHARE = 0.30      # warn when more than this share of the cuts jump that much (v4 had 14%, median jump 12)
CUT_SCENE_MIN = 0.12            # scene score a hard cut normally exceeds
CUT_OFFSET_FRAMES = 2


def level_rank(level: str) -> int:
    return {"ok": 0, "info": 1, "warn": 2, "error": 3}[level]


# --------------------------------------------------------------------------- measurement passes

def _read_series(path: Path, prefix: str, keys: tuple[str, ...]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {k: [] for k in keys}
    if path.exists():
        for line in path.read_text(errors="replace").splitlines():
            m = re.match(rf"{re.escape(prefix)}(\w+)=(\S+)", line.strip())
            if m and m.group(1) in out:
                v = vl.parse_float(m.group(2))
                if v is not None:
                    out[m.group(1)].append(v)
    return out


def measure_picture(video: str | os.PathLike, fps: float = 30.0) -> dict:
    """Per-frame series at ANALYSIS_WIDTH: luma/chroma averages, scene score, frame difference."""
    graph = (f"fps={fps},scale={ANALYSIS_WIDTH}:-2,format=yuv420p,split=3[a][b][c];"
             "[a]signalstats,metadata=print:file=sig.txt,nullsink;"
             "[b]select='gte(scene,0)',metadata=print:key=lavfi.scene_score:file=scene.txt,nullsink;"
             "[c]tblend=all_mode=difference,signalstats,metadata=print:file=diff.txt[o]")
    with tempfile.TemporaryDirectory(prefix="mhvid_qa_") as d:
        r = subprocess.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-i", vl.ffp(video), "-an", "-filter_complex", graph,
                            "-map", "[o]", "-f", "null", "-"], capture_output=True, text=True, cwd=d)
        sig = _read_series(Path(d) / "sig.txt", "lavfi.signalstats.", ("YAVG", "YLOW", "YMAX", "UAVG", "VAVG", "SATAVG"))
        scene = _read_series(Path(d) / "scene.txt", "lavfi.", ("scene_score",))["scene_score"]
        diff = _read_series(Path(d) / "diff.txt", "lavfi.signalstats.", ("YAVG",))["YAVG"]
    if r.returncode != 0 or not sig["YAVG"]:
        raise vl.EngineError(f"picture analysis failed for {video}: {(r.stderr or '')[-300:]}")
    return {"frames": len(sig["YAVG"]), "yavg": sig["YAVG"], "ylow": sig["YLOW"], "ymax": sig["YMAX"], "u": sig["UAVG"], "v": sig["VAVG"],
            "sat": sig["SATAVG"], "scene": scene, "diff": diff}


def measure_audio(video: str | os.PathLike) -> dict | None:
    """Integrated loudness, loudness range, true peak, max sample level and silences; None if there is no audio."""
    info = vl.probe(video)
    if not info.get("has_audio"):
        return None
    r = subprocess.run([vl.find_ffmpeg()["ffmpeg"], "-hide_banner", "-nostats", "-v", "info", "-i", vl.ffp(video), "-vn",
                        "-af", "ebur128=peak=true:framelog=quiet,volumedetect,silencedetect=n=-50dB:d=0.5",
                        "-f", "null", "-"], capture_output=True, text=True)
    text = r.stderr or ""

    def num(pattern):
        m = re.search(pattern, text)
        return float(m.group(1)) if m else None

    silences = []
    start = None
    for line in text.splitlines():
        a = re.search(r"silence_start: ([\d.]+)", line)
        b = re.search(r"silence_end: ([\d.]+)", line)
        if a:
            start = float(a.group(1))
        if b and start is not None:
            silences.append((start, float(b.group(1))))
            start = None
    if start is not None:
        silences.append((start, info["duration"]))
    res = {"lufs": num(r"I:\s+(-?[\d.]+) LUFS"), "lra": num(r"LRA:\s+([\d.]+) LU"),
           "true_peak": num(r"Peak:\s+(-?[\d.]+) dBFS"), "max_volume": num(r"max_volume: (-?[\d.]+) dB"),
           "mean_volume": num(r"mean_volume: (-?[\d.]+) dB"), "silences": silences}
    if res["lufs"] is None:
        raise vl.EngineError(f"loudness analysis failed: {text[-300:]}")
    return res


# --------------------------------------------------------------------------- expectations from the EDL

def expected_events(edl: dict | None, fps: float, duration: float) -> dict:
    """Times (s) where the EDL says something unusual should happen: allowed black, allowed white, cuts, transitions."""
    ev = {"black": [(0.0, 0.15), (max(0.0, duration - 0.3), duration)], "white": [], "cuts": [], "transitions": [],
          "fade_in": 0.4, "fade_out": 0.8, "planned": edl is not None}
    if not edl:
        return ev
    p = ve.project(edl)
    ev["fade_in"], ev["fade_out"] = p["fade_in"], p["fade_out"]
    ev["black"] = [(0.0, p["fade_in"] + 0.15), (max(0.0, duration - p["fade_out"] - 0.15), duration)]
    bounds = ve.timeline(edl)
    trans = ve.effective_transitions(edl, bounds)
    for j in range(1, len(bounds) - 1):
        t = bounds[j] / fps
        if j in trans:
            ev["transitions"].append((j, t, trans[j]))
            if trans[j] == "fadewhite":
                ev["white"].append((t - 0.15, t + 0.25))
            if trans[j] == "fadeblack":
                ev["black"].append((t - 0.15, t + 0.15))
        else:
            ev["cuts"].append((j, t))
    dj = ve.drop_boundary(edl, bounds)
    if dj is not None and ve.merged_style(edl)["drop_flash"]:
        t = bounds[dj] / fps
        ev["white"].append((t - 0.15, t + 0.35))
    return ev


def _inside(t: float, windows) -> bool:
    return any(a <= t <= b for a, b in windows)


def _runs(flags: list[bool]) -> list[tuple[int, int]]:
    """[(start_index, length)] of consecutive True values."""
    out, i = [], 0
    while i < len(flags):
        if flags[i]:
            j = i
            while j < len(flags) and flags[j]:
                j += 1
            out.append((i, j - i))
            i = j
        else:
            i += 1
    return out


def _t(i: int, fps: float) -> float:
    return round(i / fps, 2)


# --------------------------------------------------------------------------- the checks

def check_picture(pic: dict, ev: dict, fps: float) -> list[dict]:
    out: list[dict] = []
    n = pic["frames"]
    ya, ym = pic["yavg"], pic["ymax"]
    # black frames outside the intended fades
    black = [ya[i] < BLACK_YAVG and ym[i] < BLACK_YMAX and not _inside(i / fps, ev["black"]) for i in range(n)]
    runs = _runs(black)
    if runs:
        where = [f"{_t(i, fps)}s ({ln} frame{'s' if ln > 1 else ''})" for i, ln in runs[:6]]
        out.append({"id": "black-frames", "level": "error", "msg": "black frame(s) in the middle of the video: "
                    + ", ".join(where), "where": [_t(i, fps) for i, _ in runs]})
    else:
        out.append({"id": "black-frames", "level": "ok", "msg": "no unintended black frames"})
    # white frames outside the flash
    yl = pic["ylow"]
    white = [ya[i] > WHITE_YAVG and yl[i] > WHITE_YLOW and not _inside(i / fps, ev["white"]) for i in range(n)]
    runs = _runs(white)
    if runs:
        longest = max(ln for _, ln in runs)
        where = [f"{_t(i, fps)}s ({ln} frames)" for i, ln in runs[:6]]
        level = "error" if longest >= 2 else "warn"
        note = ""
        if not ev.get("planned"):                              # no EDL: an intended flash cannot be told from a defect
            level, note = "warn", " (no EDL given, so a planned flash cannot be excluded)"
        out.append({"id": "white-frames", "level": level,
                    "msg": "blown-out/white frames outside the planned flash: " + ", ".join(where) + note,
                    "where": [_t(i, fps) for i, _ in runs]})
    else:
        out.append({"id": "white-frames", "level": "ok", "msg": "no unintended white frames"})
    # the planned flash should exist
    if ev["white"]:
        peak = max((ya[i] for i in range(n) if _inside(i / fps, ev["white"])), default=0)
        if peak < 200:
            out.append({"id": "flash-missing", "level": "warn", "msg": f"a flash was planned but the brightest frame near it is only {peak:.0f}"})
    # colour cast per second
    per = int(round(fps))
    magenta, green = [], []
    for s in range(0, n - per + 1, per):
        u = statistics.mean(pic["u"][s:s + per])
        v = statistics.mean(pic["v"][s:s + per])
        if u > CAST_BOTH_ABOVE and v > CAST_BOTH_ABOVE:
            magenta.append(round(s / fps, 1))
        if u < CAST_BOTH_BELOW and v < CAST_BOTH_BELOW:
            green.append(round(s / fps, 1))
    for name, times, what in (("magenta-cast", magenta, "purple/magenta"), ("green-cast", green, "green")):
        if times:
            out.append({"id": name, "level": "error" if len(times) >= 3 else "warn",
                        "msg": f"{what} colour cast in {len(times)} second(s): " + ", ".join(f"{t}s" for t in times[:8]), "where": times})
        else:
            out.append({"id": name, "level": "ok", "msg": f"no {what} cast"})
    mu, mv = statistics.mean(pic["u"]), statistics.mean(pic["v"])
    out.append({"id": "chroma-balance", "level": "info", "msg": f"mean chroma U {mu:.1f}, V {mv:.1f} (neutral is 128; warm scenes read U<128, V>128)"})
    # frozen picture
    frozen = [pic["diff"][i] < FREEZE_DIFF for i in range(1, len(pic["diff"]))]
    fr = [(i + 1, ln) for i, ln in _runs(frozen) if ln / fps >= FREEZE_SECONDS]
    fr = [(i, ln) for i, ln in fr if not _inside(i / fps, ev["black"])]
    if fr:
        out.append({"id": "frozen", "level": "warn", "msg": "picture frozen for 0.5 s or more: "
                    + ", ".join(f"{_t(i, fps)}s for {ln / fps:.1f}s" for i, ln in fr[:6]) +
                    " (a clip shorter than its shot? a static clip?)", "where": [_t(i, fps) for i, _ in fr]})
    else:
        out.append({"id": "frozen", "level": "ok", "msg": "no frozen stretches"})
    # brightness range
    out.append({"id": "luma-range", "level": "info", "msg": f"luma average {statistics.mean(ya):.0f} (min {min(ya):.0f}, max {max(ya):.0f})"})
    return out


def check_cuts(pic: dict, ev: dict, fps: float) -> tuple[list[dict], dict]:
    """Hard cuts in the render must sit where the EDL put them (this catches transition/offset timing bugs)."""
    sc = pic["scene"]
    out: list[dict] = []
    offsets: list[int] = []
    missing = []
    for j, t in ev["cuts"]:
        f = int(round(t * fps))
        lo, hi = max(0, f - 6), min(len(sc), f + 7)
        if hi <= lo:
            continue
        window = sc[lo:hi]
        peak = max(window)
        if peak < CUT_SCENE_MIN:
            missing.append(j)
            continue
        offsets.append(lo + window.index(peak) - f)
    stats = {"hard_cuts": len(ev["cuts"]), "detected": len(offsets), "undetected": len(missing)}
    if offsets:
        med = statistics.median(offsets)
        worst = max(abs(o) for o in offsets)
        stats.update(median_offset_frames=med, max_offset_frames=worst)
        if abs(med) >= 1 or worst > CUT_OFFSET_FRAMES:
            lvl = "error" if abs(med) >= CUT_OFFSET_FRAMES else "warn"
            out.append({"id": "cut-timing", "level": lvl,
                        "msg": f"cuts are not where the EDL put them: median offset {med:+.0f} frame(s), worst {worst}; "
                               "the beat sync will drift"})
        else:
            out.append({"id": "cut-timing", "level": "ok",
                        "msg": f"{len(offsets)} hard cuts land on their planned frames (median offset {med:+.0f}, worst {worst})"})
    elif ev["cuts"]:
        out.append({"id": "cut-timing", "level": "info", "msg": "no hard cut produced a strong scene change (similar shots?), timing not verified"})
    if ev["cuts"] and len(missing) > 0.5 * len(ev["cuts"]):
        out.append({"id": "cut-detection", "level": "warn",
                    "msg": f"{len(missing)} of {len(ev['cuts'])} planned hard cuts show no scene change: neighbouring shots look alike"})
    return out, stats


def check_exposure_jumps(pic: dict, ev: dict, fps: float) -> list[dict]:
    """How much brightness and colour change across hard cuts. Big jumps are fine in a few places (dark cabin -> bright sky)
    but a video made of them flickers: `color plan --match exposure` evens shots out."""
    if len(ev["cuts"]) < 4:
        return []
    ya, u, v = pic["yavg"], pic["u"], pic["v"]
    jumps = []
    for j, t in ev["cuts"]:
        f = int(round(t * fps))
        if f < 6 or f + 5 > len(ya):
            continue
        a, b = statistics.mean(ya[f - 5:f - 1]), statistics.mean(ya[f + 1:f + 5])
        dc = ((statistics.mean(u[f + 1:f + 5]) - statistics.mean(u[f - 5:f - 1])) ** 2 +
              (statistics.mean(v[f + 1:f + 5]) - statistics.mean(v[f - 5:f - 1])) ** 2) ** 0.5
        jumps.append((abs(b - a), dc, t))
    if not jumps:
        return []
    lumas = sorted(x[0] for x in jumps)
    big = [x for x in jumps if x[0] > EXPOSURE_JUMP]
    share = len(big) / len(jumps)
    worst = sorted(jumps, reverse=True)[:3]
    msg = (f"median brightness change across hard cuts {lumas[len(lumas) // 2]:.0f} levels; {len(big)} of {len(jumps)} cuts jump more than "
           f"{EXPOSURE_JUMP:.0f} (worst at " + ", ".join(f"{x[2]:.1f}s" for x in worst) + ")")
    return [{"id": "exposure-jumps", "level": "warn" if share > EXPOSURE_JUMP_SHARE else "info",
             "msg": msg + ("; even the shots out with `color plan`" if share > EXPOSURE_JUMP_SHARE else ""),
             "where": [round(x[2], 2) for x in worst]}]


def check_audio(aud: dict | None, expect_audio: bool, target: float, duration: float) -> list[dict]:
    out: list[dict] = []
    if aud is None:
        out.append({"id": "audio", "level": "error" if expect_audio else "info",
                    "msg": "no audio track" + (" but the EDL has music" if expect_audio else "")})
        return out
    lufs, tp, mx = aud["lufs"], aud["true_peak"], aud["max_volume"]
    lo, hi = target + LOUDNESS_TOLERANCE[0], target + LOUDNESS_TOLERANCE[1]
    if lufs is not None and not (lo <= lufs <= hi):
        out.append({"id": "loudness", "level": "warn",
                    "msg": f"integrated loudness {lufs:.1f} LUFS is outside {lo:.0f}..{hi:.0f} (target {target:.0f}); sound effects may be too hot"})
    else:
        out.append({"id": "loudness", "level": "ok", "msg": f"integrated loudness {lufs:.1f} LUFS (target {target:.0f}), range {aud['lra']} LU"})
    if tp is not None and tp > TRUE_PEAK_ERROR:
        out.append({"id": "true-peak", "level": "error", "msg": f"true peak {tp:.1f} dBFS: clipping"})
    elif tp is not None and tp > TRUE_PEAK_WARN:
        out.append({"id": "true-peak", "level": "warn", "msg": f"true peak {tp:.1f} dBFS is above {TRUE_PEAK_WARN} (platforms may distort or re-encode louder)"})
    else:
        out.append({"id": "true-peak", "level": "ok", "msg": f"true peak {tp:.1f} dBFS"})
    mid = [(a, b) for a, b in aud["silences"] if a > 0.6 and b < duration - 0.6]
    if mid:
        out.append({"id": "silence", "level": "warn", "msg": "silence in the middle of the video: "
                    + ", ".join(f"{a:.1f}-{b:.1f}s" for a, b in mid[:5]), "where": [a for a, _ in mid]})
    else:
        out.append({"id": "silence", "level": "ok", "msg": "no silent gaps"})
    return out


def check_structure(info: dict, p: dict | None, size_bytes: int) -> list[dict]:
    out: list[dict] = []
    mb = size_bytes / 1e6
    per_sec = mb / max(info["duration"], 0.1)
    if mb > MAX_FILE_MB:
        out.append({"id": "file-size", "level": "error", "msg": f"{mb:.0f} MB is above {MAX_FILE_MB:.0f} MB (grain/noise or a wrong quality setting)"})
    elif per_sec > MAX_MB_PER_SECOND:
        out.append({"id": "file-size", "level": "warn", "msg": f"{per_sec:.1f} MB per second ({mb:.0f} MB): unusually heavy for a reel"})
    else:
        out.append({"id": "file-size", "level": "ok", "msg": f"{mb:.1f} MB ({per_sec:.2f} MB/s)"})
    if p:
        if abs(info["duration"] - p["duration"]) > 0.06:
            out.append({"id": "duration", "level": "error", "msg": f"{info['duration']:.3f}s but the EDL says {p['duration']:.3f}s"})
        else:
            out.append({"id": "duration", "level": "ok", "msg": f"{info['duration']:.3f}s matches the EDL"})
        want = p["width"] / p["height"]
        got = info.get("width", 1) / max(info.get("height", 1), 1)
        if abs(want - got) > 0.01:
            out.append({"id": "aspect", "level": "error", "msg": f"{info.get('width')}x{info.get('height')} does not match {p['width']}x{p['height']}"})
        else:
            out.append({"id": "aspect", "level": "ok", "msg": f"{info.get('width')}x{info.get('height')}"})
        if abs((info.get("fps") or 0) - p["fps"]) > 0.01:
            out.append({"id": "fps", "level": "error", "msg": f"{info.get('fps')} fps, expected {p['fps']}"})
    return out


# --------------------------------------------------------------------------- EDL audits

def audit_edit(edl: dict, scores: dict | None = None) -> tuple[list[dict], dict]:
    """Effects, transitions, pacing and beat alignment of the plan itself (from the EDL, not the pixels)."""
    out: list[dict] = []
    p = ve.project(edl)
    fps = p["fps"]
    style = ve.merged_style(edl)
    bounds = ve.timeline(edl)
    trans = ve.effective_transitions(edl, bounds)
    cuts = max(len(edl["shots"]) - 1, 1)
    lens = [(bounds[j + 1] - bounds[j]) / fps for j in range(len(edl["shots"]))]
    kinds = [k for k in ("zoom", "drop_flash", "end_flare") if (style.get(k) if k != "zoom" else style.get("zoom"))]
    families: dict[str, int] = {}
    for v in trans.values():
        fam = vl.family_of(v)
        if fam:
            families[fam] = families.get(fam, 0) + 1
    audit = {"shots": len(edl["shots"]), "avg_shot_s": round(statistics.mean(lens), 2), "min_shot_s": round(min(lens), 2),
             "max_shot_s": round(max(lens), 2), "visible_effects": kinds, "transitions": len(trans),
             "transition_share": round(len(trans) / cuts, 2), "hard_cut_share": round(1 - len(trans) / cuts, 2),
             "transition_types": sorted(set(trans.values())), "families": families}
    for issue in ve.lint(edl, check_files=False):
        out.append({"id": f"lint-{issue['code']}", "level": issue["level"], "msg": issue["msg"]})
    if not any(i["id"].startswith("lint-") for i in out):
        out.append({"id": "restraint", "level": "ok",
                    "msg": f"{len(kinds)} visible effect(s) ({', '.join(kinds) or 'none'}), {len(trans)} transitions of {len(set(trans.values()))} kinds, "
                           f"{audit['hard_cut_share']:.0%} hard cuts"})
    # beat alignment of cuts
    b = edl.get("beats") or {}
    if "t0" in b and "period" in b:
        on, tot = 0, 0
        for j in range(1, len(bounds) - 1):
            t = bounds[j] / fps
            k = round((t - b["t0"]) / b["period"])
            if abs(t - (b["t0"] + k * b["period"])) <= 1.5 / fps:
                on += 1
            tot += 1
        share = on / max(tot, 1)
        audit["cuts_on_beat_share"] = round(share, 2)
        if share < 0.9:
            out.append({"id": "beat-alignment", "level": "warn", "msg": f"only {share:.0%} of the cuts sit on a beat of the grid (cut on main beats unless the brief asks otherwise)"})
        else:
            out.append({"id": "beat-alignment", "level": "ok", "msg": f"{share:.0%} of the cuts sit on a beat"})
    # phase 4 content: looks, corrections, motion, text, sound design
    looks = set()
    graded = 0
    for sh in edl["shots"]:
        g = sh.get("grade", style.get("grade"))
        looks.add(json.dumps(g, sort_keys=True) if isinstance(g, dict) else str(g))
        graded += 1 if sh.get("correct") or style.get("correct") else 0
    slow = [j for j, sh in enumerate(edl["shots"]) if sh.get("speed") not in (None, 1, 1.0)]
    stab = [j for j, sh in enumerate(edl["shots"]) if sh.get("stabilize") not in (None, False, 0)]
    audit.update(distinct_looks=len(looks), corrected_shots=graded, speed_shots=len(slow), stabilised_shots=len(stab),
                 texts=len(edl.get("texts") or []))
    if len(looks) > 2:
        out.append({"id": "look-consistency", "level": "warn", "msg": f"{len(looks)} different grades in one video: one look per video unless the story needs a change"})
    parts = [f"look {', '.join(sorted(looks))[:60]}"]
    if graded:
        parts.append(f"{graded} corrected shot(s)")
    if slow:
        parts.append(f"{len(slow)} speed-changed shot(s)")
    if stab:
        parts.append(f"{len(stab)} stabilised shot(s)")
    if edl.get("texts"):
        parts.append(f"{len(edl['texts'])} text(s)")
    if (edl.get("style") or {}).get("sound_design"):
        parts.append("sound design")
    if (edl.get("style") or {}).get("ambient"):
        parts.append("ambient sound")
    out.append({"id": "content", "level": "info", "msg": "; ".join(parts)})
    if scores:
        used = []
        for sh in edl["shots"]:
            key = os.path.normcase(os.path.abspath(sh["clip"]))
            for s in scores.values():
                if s.get("path") and os.path.normcase(os.path.abspath(s["path"])) == key:
                    used.append(s)
                    break
        if used:
            allq = [s["quality"] for s in scores.values()]
            audit["mean_quality_used"] = round(statistics.mean(s["quality"] for s in used), 1)
            audit["mean_quality_library"] = round(statistics.mean(allq), 1)
            bad = [s for s in used if any(f in s["flags"] for f in ("dark", "overexposed", "blurry"))]
            if bad:
                out.append({"id": "weak-clips", "level": "warn", "msg": "clips flagged by scoring are in the edit: "
                            + ", ".join(f"{s['name']} ({','.join(s['flags'])})" for s in bad[:5])})
            seq = [s.get("similar_to") for s in used]
            ids = [s["index"] for s in used]
            adj = [i for i in range(1, len(used)) if used[i].get("similar_to") == ids[i - 1] or used[i - 1].get("similar_to") == ids[i]]
            if adj:
                out.append({"id": "similar-neighbours", "level": "warn", "msg": f"{len(adj)} neighbouring shots show the same scene (shots {', '.join(str(i - 1) + '-' + str(i) for i in adj[:6])})"})
            out.append({"id": "clip-quality", "level": "info", "msg": f"mean quality of used clips {audit['mean_quality_used']} (library {audit['mean_quality_library']})"})
        # editorial audit (Track A/F): shot-size variety, jump cuts, screen direction, pacing
        feats = []
        for sh in edl["shots"]:
            key = os.path.normcase(os.path.abspath(sh["clip"]))
            feats.append(next((s for s in scores.values() if s.get("path") and os.path.normcase(os.path.abspath(s["path"])) == key), {}))
        if any(f.get("shot_size") for f in feats):
            out += vid_editorial.editorial_issues(edl, feats, bounds, trans)
            ed = vid_editorial.summarize_editorial(edl, feats, bounds, trans)
            audit.update(ed)
            out.append({"id": "editorial", "level": "info",
                        "msg": f"shot sizes {ed['shot_size_mix']}, {ed['distinct_shot_sizes']} distinct; "
                               f"{ed['cut_on_motion_share']:.0%} of hard cuts land on motion"})
    return out, audit


# --------------------------------------------------------------------------- orchestration

def run_qa(video: str | os.PathLike, edl_path: str | os.PathLike | None = None, scores: dict | None = None,
           sheet: str | os.PathLike | None = None, log=None, fmt: str | None = None) -> dict:
    edl = ve.load(edl_path) if edl_path else None
    info = vl.probe(video)
    if not info.get("has_video"):
        raise vl.EngineError(f"no video stream in {video}")
    p = ve.project(edl) if edl else None
    if p and fmt:                                            # rendered in another format: expect that preset's size
        pre = vl.load_preset(fmt)
        p = dict(p, width=pre["width"], height=pre["height"])
    fps = (p["fps"] if p else info.get("fps")) or 30.0
    ev = expected_events(edl, fps, info["duration"])
    checks: list[dict] = []
    checks += check_structure(info, p, info["size"])
    pic = measure_picture(video, fps)
    exp_frames = vl.frames(p["duration"], fps) if p else None
    if exp_frames is not None:
        if abs(pic["frames"] - exp_frames) > 1:
            checks.append({"id": "frame-count", "level": "error", "msg": f"{pic['frames']} frames, expected {exp_frames}"})
        else:
            checks.append({"id": "frame-count", "level": "ok", "msg": f"{pic['frames']} frames (expected {exp_frames})"})
    checks += check_picture(pic, ev, fps)
    cut_checks, cut_stats = check_cuts(pic, ev, fps)
    checks += cut_checks
    checks += check_exposure_jumps(pic, ev, fps)
    aud = measure_audio(video)
    target = ve.merged_style(edl)["loudness"] if edl else -14.0
    checks += check_audio(aud, bool(p and p.get("music")), target, info["duration"])
    audit, audit_data = ({}, {})
    if edl:
        audit_checks, audit_data = audit_edit(edl, scores)
        checks += audit_checks
    worst = max((level_rank(c["level"]) for c in checks), default=0)
    verdict = {0: "pass", 1: "pass", 2: "warn", 3: "fail"}[worst]
    report = {"verdict": verdict, "video": vl.ffp(video), "edl": vl.ffp(edl_path) if edl_path else None,
              "duration": info["duration"], "frames": pic["frames"], "size_mb": round(info["size"] / 1e6, 1),
              "counts": {lv: sum(1 for c in checks if c["level"] == lv) for lv in ("ok", "info", "warn", "error")},
              "checks": checks, "audio": aud, "cuts": cut_stats, "edit_audit": audit_data}
    if sheet:
        report["sheet"] = vid_sheets.video_sheet(video, sheet)
    return report


ICON = {"ok": "ok  ", "info": "info", "warn": "WARN", "error": "FAIL"}


def format_report(rep: dict) -> str:
    head = {"pass": "PASS", "warn": "PASS WITH WARNINGS", "fail": "FAIL"}[rep["verdict"]]
    lines = [f"QA {head}: {Path(rep['video']).name}  ({rep['duration']:.2f}s, {rep['frames']} frames, {rep['size_mb']} MB)"]
    for lvl in ("error", "warn", "info", "ok"):
        for c in rep["checks"]:
            if c["level"] == lvl:
                lines.append(f"  [{ICON[lvl]}] {c['id']}: {c['msg']}")
    c = rep["counts"]
    lines.append(f"  {c['error']} error(s), {c['warn']} warning(s), {c['info']} note(s), {c['ok']} ok")
    if rep.get("sheet"):
        lines.append(f"  contact sheet: {rep['sheet']}  (OPEN IT and look: the gate cannot judge composition or taste)")
    return "\n".join(lines)


def save_report(rep: dict, path: str | os.PathLike) -> str:
    vl.save_json(path, rep)
    return str(path)
