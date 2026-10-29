"""End the video on the song's own ending instead of fading the music out in the middle of a phrase.

A song is usually longer than the edit. `fit` cuts the music at a downbeat `cut` (seconds into the song) and resumes it at a later
downbeat `resume`, so that the song's last strong moment (the final hit) lands just before the end of the video and its own decay
closes it. What must not change is where the beats fall against the cuts the edit was planned on: for every pair the speed of the
ending (`tempo`, pitch kept, within 3 %) and a small shift of `resume` (within 0.1 s) are chosen so that the music's onsets after
the join sit against the planned beat grid exactly as the original music sat there (the median and the spread of their distance).
This is computed on the song's onset envelope, without rendering. Estimating the tempo and the phase separately was not good
enough on the Kavir track (a played folk song): single-beat onsets catch off-beat accents and the tempo map claimed a drift the
onsets do not show; joins came out 0.05-0.09 s off. Measured on the rendered joins after this, they match the original to 0.01 s.

Among the possible pairs the join is chosen by measurement: the loudness of the bar before the cut against the bar after the
resume, and the balance of low, middle and high frequencies there (a join between a sparse verse and a full chorus is audible
even at equal loudness). The result is written into the EDL as `project.music_edit = {"cut": ..., "resume": ...}` and the
renderer joins the two parts with 15 ms fades on the downbeat. The engine cannot hear the join: listen to it before delivering.
"""
from __future__ import annotations

import math
import subprocess
import tempfile
from pathlib import Path

import vidlib as vl

STEP = 0.25                     # seconds per loudness value
BANDS = {"low": "lowpass=f=250", "mid": "highpass=f=250,lowpass=f=2500", "high": "highpass=f=2500"}
HIT_BEFORE_END = (0.4, 3.0)     # the final hit lands this many seconds before the end of the video
HIT_TARGET = 1.2
SAME_TEMPO = 0.005              # a measured tempo difference under 0.5 % is jitter, not drift: no stretch


def loudness(path: str, rate: int = 16000) -> dict:
    """{'all'|'low'|'mid'|'high': [dB per STEP seconds]} for the whole file, in one FFmpeg pass."""
    n = int(rate * STEP)
    graph = f"[0:a]aresample={rate},aformat=channel_layouts=mono,asplit=4[a][l][m][h];"
    for name, src, f in (("all", "a", "anull"), ("low", "l", BANDS["low"]), ("mid", "m", BANDS["mid"]), ("high", "h", BANDS["high"])):
        graph += (f"[{src}]{f},asetnsamples=n={n},astats=metadata=1:reset=1:measure_perchannel=RMS_level:measure_overall=none,"
                  f"ametadata=print:file={name}.txt" + (",anullsink;" if name != "high" else ""))
    out = {}
    with tempfile.TemporaryDirectory(prefix="mhvid_music_") as d:
        r = subprocess.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-i", vl.ffp(Path(path).resolve()), "-vn", "-filter_complex",
                            graph, "-f", "null", "-"], capture_output=True, text=True, cwd=d)
        for name in BANDS.keys() | {"all"}:
            vals = []
            p = Path(d) / f"{name}.txt"
            if p.is_file():
                for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                    if "RMS_level" in line:
                        v = vl.parse_float(line.split("=", 1)[1])
                        vals.append(v if v is not None and math.isfinite(v) else -120.0)
            out[name] = vals
    if not out.get("all"):
        raise vl.EngineError(f"could not measure the music's loudness: {(r.stderr or '')[-300:]}")
    return out


def _mean(vals: list) -> float:
    if not vals:
        return -120.0
    return 10.0 * math.log10(max(sum(10 ** (v / 10.0) for v in vals) / len(vals), 1e-12))


def onset_envelope(path: str) -> list:
    """The song's onset strength every 10 ms (vid_beats' own measure)."""
    import vid_beats
    pcm = vid_beats.decode_pcm(path)
    low, high, _full = vid_beats._bands(pcm)                                       # noqa: SLF001
    return vid_beats.onset_strength(low, high)


def _offsets(onset: list, times: list, w: float, shift=None) -> list:
    """Distance from each time to the strongest onset within +-w (seconds). `shift(t)` maps a time to the song's time first."""
    import vid_beats
    fr, out = vid_beats.FRAME, []
    for g in times:
        t0 = shift(g) if shift else g
        i0, i1 = max(0, int((t0 - w) / fr)), min(len(onset) - 1, int((t0 + w) / fr))
        if i1 > i0:
            out.append(max(range(i0, i1 + 1), key=lambda i: onset[i]) * fr - t0)
    return out


def _spread(v: list) -> tuple:
    s = sorted(v)
    return (s[len(s) // 2], s[3 * len(s) // 4] - s[len(s) // 4]) if s else (0.0, 0.0)


def align(onset: list, grid: list, period: float, cut: float, resume: float, video_seconds: float) -> dict:
    """The ending's speed and resume shift that keep the beats after the join where the original music had them."""
    planned = [g for g in grid if cut + 0.3 < g < video_seconds - 0.3]
    w = period / 4.0
    if len(planned) < 4:
        return {"tempo": 1.0, "shift": 0.0, "offset": None, "original": None}
    m0, _iqr0 = _spread(_offsets(onset, planned, w))
    best = None
    for si in range(-12, 13):
        sp = 1.0 + si * 0.0025
        for fi in range(-10, 11):
            sh = fi * 0.01
            offs = [o / sp for o in _offsets(onset, planned, w, lambda g: resume + sh + (g - cut) * sp)]
            if len(offs) < 4:
                continue
            m, iqr = _spread(offs)
            score = abs(m - m0) + 0.5 * iqr + 0.02 * abs(si) + 0.01 * abs(fi)      # prefer no stretch and no shift on a tie
            if best is None or score < best[0]:
                best = (score, sp, sh, m)
    return {"tempo": round(best[1], 4), "shift": round(best[2], 3), "offset": round(best[3], 3), "original": round(m0, 3)}


def window(series: list, a: float, b: float) -> list:
    return series[max(0, int(a / STEP)):max(0, int(b / STEP))]


def final_hit(series: list) -> float:
    """The last moment the song is still loud: within 8 dB of the median level of its loud part."""
    loud = sorted(v for v in series if v > -60)
    if not loud:
        return len(series) * STEP
    ref = loud[len(loud) // 2] - 8.0
    last = max((k for k, v in enumerate(series) if v >= ref), default=len(series) - 1)
    return round((last + 1) * STEP, 2)


def join_cost(lev: dict, cut: float, resume: float, bar: float) -> dict:
    """How different the bar before `cut` and the bar after `resume` sound: loudness (dB) and band balance (dB, summed)."""
    before = {k: _mean(window(v, cut - bar, cut)) for k, v in lev.items()}
    after = {k: _mean(window(v, resume, resume + bar)) for k, v in lev.items()}
    bal = sum(abs((before[k] - before["all"]) - (after[k] - after["all"])) for k in BANDS)
    return {"loudness_db": round(abs(before["all"] - after["all"]), 1), "balance_db": round(bal, 1)}


def fit(beats: dict, video_seconds: float, lev: dict | None = None, min_keep: float | None = None,
        onset: list | None = None) -> dict | None:
    """The best (cut, resume) for a video of `video_seconds`, or None when the song ends in time on its own or cannot be fitted."""
    cands = candidates(beats, video_seconds, lev, min_keep, onset)
    return cands[0] if cands else None


def candidates(beats: dict, video_seconds: float, lev: dict | None = None, min_keep: float | None = None,
               onset: list | None = None) -> list:
    """Every (cut, resume) pair of downbeats that lands the song's final hit inside the video, cheapest join first. The cost
    adds the loudness step, half the band-balance step, how far the hit is from 1.2 s before the end, and a price for every
    second of the planned music that the ending replaces (the storyboard was chosen on it)."""
    downs = sorted(beats.get("downbeats") or [])
    if len(downs) < 4:
        raise vl.EngineError("beats.json has too few downbeats to cut the music on (run `beats` again)")
    song = float(beats.get("duration") or 0.0)
    if song <= video_seconds + HIT_BEFORE_END[1]:
        return None
    lev = lev or loudness(beats["file"])
    bar = (downs[-1] - downs[0]) / (len(downs) - 1)
    hit = min(final_hit(lev["all"]), song)
    min_keep = video_seconds * 0.5 if min_keep is None else min_keep
    out = []
    for resume in downs:
        if resume > hit - 2 * bar or resume < song - 90.0:
            continue
        for cut in downs:
            if cut < min_keep or cut > resume - bar:
                continue
            before_end = video_seconds - (cut + (hit - resume))
            if not HIT_BEFORE_END[0] <= before_end <= HIT_BEFORE_END[1]:
                continue
            jc = join_cost(lev, cut, resume, bar)
            cost = jc["loudness_db"] + 0.5 * jc["balance_db"] + 0.5 * abs(before_end - HIT_TARGET) + 3.0 * (video_seconds - cut) / video_seconds
            out.append({"cut": round(cut, 3), "resume": round(resume, 3), "song_hit": hit, "join": jc, "cost": round(cost, 2),
                        "bar": round(bar, 3), "replaces": round(video_seconds - cut, 2)})
    out.sort(key=lambda c: c["cost"])
    if out:
        onset = onset if onset is not None else onset_envelope(beats["file"])
        grid = sorted(beats.get("beats") or [])
        period = float(beats.get("period") or bar / 4)
        for c in out[:12]:                                    # the beat alignment only for the ones anyone will look at
            al = align(onset, grid, period, c["cut"], c["resume"], video_seconds)
            c["resume"] = round(c["resume"] + al["shift"], 3)
            c["tempo"], c["beats"] = al["tempo"], {"after": al["offset"], "original": al["original"]}
            c["hit_at"] = round(c["cut"] + (hit - c["resume"]) / al["tempo"], 2)
        out = out[:12]
    return out


def format_table(cands: list) -> str:
    lines = [f"{'#':>2} {'cut':>7} {'resume':>7} {'tempo':>6} {'ending':>7} {'loud dB':>8} {'balance dB':>10} {'beats s':>14} {'cost':>5}"]
    for k, c in enumerate(cands, 1):
        bt = c.get("beats") or {}
        beat = f"{bt.get('after'):+.3f}/{bt.get('original'):+.3f}" if bt.get("after") is not None else "-"
        lines.append(f"{k:>2} {c['cut']:>7.2f} {c['resume']:>7.2f} {c.get('tempo', 1.0):>6.3f} {c['replaces']:>6.1f}s "
                     f"{c['join']['loudness_db']:>8} {c['join']['balance_db']:>10} {beat:>14} {c['cost']:>5}")
    lines.append("beats s: where the music's beats sit against the planned cuts after the join / in the original music there (median)")
    return "\n".join(lines)


def preview(music: str, cand: dict, video_seconds: float, out: str | Path, lead: float = 5.0) -> Path:
    """A short AAC file to listen to one join: from `lead` seconds before the cut to the end of the video, the song's own fade."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    start = max(0.0, cand["cut"] - lead)
    g = edit_filter(cand) + f";[music]atrim=start={start:.3f}:end={video_seconds:.3f},asetpts=PTS-STARTPTS[o]"
    vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-i", vl.ffp(music), "-filter_complex", g, "-map", "[o]",
            "-c:a", "aac", "-b:a", "160k", vl.ffp(out)])
    return out


def describe(plan: dict | None, video_seconds: float) -> str:
    if not plan:
        return "no music edit: the song ends in time on its own, or no pair of downbeats lands its ending inside the video"
    j = plan["join"]
    tempo = plan.get("tempo", 1.0)
    return (f"cut the music at {plan['cut']:.2f} s and go on from {plan['resume']:.2f} s (both downbeats"
            + (f"; the ending played at {tempo:.3f} times its speed to keep the tempo of the cut" if abs(tempo - 1.0) >= 0.002 else "")
            + f"): the song's final hit "
            f"({plan['song_hit']:.1f} s) lands at {plan['hit_at']:.2f} s of {video_seconds:g} s, and its last "
            f"{plan['replaces']:.1f} s are the song's own ending. Join: loudness {j['loudness_db']} dB apart, band balance "
            f"{j['balance_db']} dB apart (under 3 and 6 is usually smooth). Listen to the join before delivering.")


def edit_filter(edit: dict, fade_ms: int = 15) -> str:
    """An audio filter chain that joins [0, cut] and [resume, end] of input 0 with short fades on the downbeat; ends in [music]."""
    c, r, f = float(edit["cut"]), float(edit["resume"]), fade_ms / 1000.0
    sp = float(edit.get("tempo") or 1.0)
    stretch = f"atempo={sp:.4f}," if abs(sp - 1.0) >= 0.002 else ""
    return (f"[0:a]asplit=2[m1][m2];[m1]atrim=0:{c:.3f},asetpts=PTS-STARTPTS,afade=t=out:st={max(0.0, c - f):.3f}:d={f:.3f}[ma];"
            f"[m2]atrim=start={r:.3f},asetpts=PTS-STARTPTS,{stretch}afade=t=in:st=0:d={f:.3f}[mb];[ma][mb]concat=n=2:v=0:a=1[music]")


def validate(edit, song_seconds: float | None = None) -> list:
    """Problems with a `project.music_edit` value (strings)."""
    if edit is None:
        return []
    if not isinstance(edit, dict) or not all(isinstance(edit.get(k), (int, float)) and not isinstance(edit.get(k), bool)
                                             for k in ("cut", "resume")):
        return ["project.music_edit must be {\"cut\": seconds, \"resume\": seconds} (from `music fit`)"]
    out = []
    if not 0 < edit["cut"] < edit["resume"]:
        out.append("project.music_edit: cut must be above 0 and before resume")
    sp = edit.get("tempo", 1.0)
    if not (isinstance(sp, (int, float)) and not isinstance(sp, bool) and 0.9 <= sp <= 1.1):
        out.append("project.music_edit: tempo must be 0.9-1.1 (the ending's speed against the song's)")
    if song_seconds and edit["resume"] >= song_seconds:
        out.append(f"project.music_edit: resume ({edit['resume']}) is past the end of the music ({song_seconds:.1f} s)")
    return out
