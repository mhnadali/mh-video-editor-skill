"""Step 3: turn a beat grid + an ordered clip list into an EDL (cuts on main beats, restrained transitions).

Structure it builds (the approved Maranjab v4 layout):
  intro   : `intro_shots` shots that split the time before the body starts
  body    : one shot per beat from the body start to the last full beat
  outro   : one final shot from the last beat to the end (>= 1.5 beats long)
Transitions: a few, all different, on a ~45% share of the cuts; the drop cut gets the white fade.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

import vid_edl as ve
import vidlib as vl


def parse_clip_list(path: str | os.PathLike) -> list[dict]:
    """Text file: one clip per line, optional ' @ src_start_seconds'. '#' starts a comment.
    Or a JSON list of strings / {"clip":..., "src_start":...} objects."""
    p = Path(path)
    if p.suffix.lower() == ".json":
        raw = vl.load_json(p)
        items = raw if isinstance(raw, list) else raw.get("clips", [])
        out = []
        for it in items:
            out.append({"clip": it} if isinstance(it, str) else dict(it))
        return out
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        src = None
        if " @ " in line:
            line, s = line.rsplit(" @ ", 1)
            src = float(s)
        item = {"clip": line.strip().strip('"')}
        if src is not None:
            item["src_start"] = src
        out.append(item)
    return out


def apply_scores(clips: list[dict], scores_by_index: dict[int, dict]) -> int:
    """Fill a missing src_start with the clip's best measured window (matched by path). Returns how many were filled."""
    n = 0
    by_path = {}
    for s in scores_by_index.values():
        if s.get("path"):
            by_path[os.path.normcase(os.path.abspath(s["path"]))] = s
    for c in clips:
        if c.get("src_start") is not None:
            continue
        s = by_path.get(os.path.normcase(os.path.abspath(c["clip"])))
        if s is not None:
            c["src_start"] = s["best_start"]
            n += 1
    return n


def body_start_time(beats: dict, duration: float, period: float, intro_shots: int, body_start: float | None = None,
                    align_bars: bool = False) -> float:
    """Time (s) of the first body beat: the first grid beat at/after ~11% of the video (3.1 s of 28 s).
    With align_bars the body starts on a downbeat (the 'one' of a bar) so the build feels musical."""
    if not intro_shots:
        return 0.0
    phase_t = beats["t0"]
    bs = body_start if body_start is not None else max(0.0, min(0.11 * duration, duration / 4))
    k0 = max(0, math.ceil((bs - phase_t) / period - 1e-6))
    if align_bars and beats.get("bar_phase") is not None:
        while (k0 - beats["bar_phase"]) % 4 != 0:
            k0 += 1
    return round(phase_t + k0 * period, 4)


def layout(duration: float, t0: float, period: float, intro_shots: int = 2, min_tail_beats: float = 1.5) -> dict:
    """Pure geometry: how many shots are needed and where the cuts fall."""
    if intro_shots < 0:
        raise vl.EngineError("intro_shots must be >= 0")
    if t0 <= 0 and intro_shots:
        raise vl.EngineError("body starts at 0 s; use intro_shots=0")
    last_beat = int(math.floor((duration - t0 - min_tail_beats * period) / period))
    if last_beat < 1:
        raise vl.EngineError("music is too short for this tempo/intro")
    cuts = []                                   # seconds, strictly increasing, excluding 0 and duration
    for i in range(1, intro_shots):
        cuts.append(round(t0 * i / intro_shots, 4))
    if intro_shots:
        cuts.append(round(t0, 4))
    for k in range(1, last_beat + 1):
        cuts.append(round(t0 + k * period, 4))
    return {"shots": len(cuts) + 1, "cuts": cuts, "last_beat": last_beat}


def auto_transitions(n_shots: int, drop_cut: int | None, share: float = 0.45) -> dict[str, str]:
    """Pick which cut points get a transition and which look. Deterministic and varied."""
    cuts = n_shots - 1
    if cuts <= 0:
        return {}
    cap = cuts // 2                              # hard ceiling: the linter allows at most 50% of the cuts
    want = min(cap, max(1, int(cuts * share)))   # the drop fade and the closing fade are part of this budget
    vocab = list(vl.PLANNER_TRANSITIONS)
    chosen: dict[int, str] = {}
    if want >= 1 and drop_cut is not None and 1 <= drop_cut <= cuts:
        chosen[drop_cut] = "fadewhite"           # the drop always comes first
    if cuts >= 3 and len(chosen) < want:
        chosen[cuts] = "fade"                    # soft ending, if the budget allows
    gaps = [2, 3, 2, 2, 3]                       # alternate: transition, cut, (cut), transition ...
    j, gi, vi = 1, 0, 0
    while j <= cuts and len(chosen) < want and vi < len(vocab):
        if j not in chosen:
            chosen[j] = vocab[vi]
            vi += 1
        if j == 1 and 2 not in chosen:
            j += 1                               # both intro cuts get a transition
            continue
        j += gaps[gi % len(gaps)]
        gi += 1
    return {str(k): v for k, v in sorted(chosen.items())}


def plan(clips: list[dict], beats: dict, *, name: str = "video", music: str | None = None, preset: str = "reels-9x16",
         duration: float | None = None, intro_shots: int = 2, body_start: float | None = None,
         period: float | None = None, grade: str = "warm-desert", transitions: bool = True,
         align_bars: bool = False) -> dict:
    duration = float(duration or beats["duration"])
    period = float(period or beats["period"])
    phase_t = beats["t0"]
    t0 = body_start_time(beats, duration, period, intro_shots, body_start, align_bars)
    lay = layout(duration, t0, period, intro_shots)
    if len(clips) != lay["shots"]:
        raise vl.EngineError(
            f"this music needs {lay['shots']} clips ({intro_shots} intro + {lay['last_beat']} body + 1 outro) "
            f"but {len(clips)} were given")
    edges = [0.0] + lay["cuts"] + [duration]
    shots = []
    for i, c in enumerate(clips):
        s = {"clip": c["clip"], "span": [round(edges[i], 4), round(edges[i + 1], 4)]}
        if c.get("src_start") is not None:
            s["src_start"] = c["src_start"]
        shots.append(s)
    drop_beat = None
    drop_cut = None
    if beats.get("drop_beat") is not None:
        drop_abs_t = phase_t + beats["drop_beat"] * period
        db = round((drop_abs_t - t0) / period)
        if db >= 1:
            drop_beat = int(db)
            target = t0 + db * period
            for j, cut in enumerate(lay["cuts"], start=1):
                if abs(cut - target) < 0.02:
                    drop_cut = j
    music_val = None
    if music:
        music_val = [vl.ffp(m) for m in music] if isinstance(music, list) else vl.ffp(music)
    edl = {
        "version": 1,
        "project": {"name": name, "preset": preset, "duration": round(duration, 4), "music": music_val},
        "beats": {"t0": t0, "period": round(period, 5)},
        "style": {"grade": grade},
        "shots": shots,
        "transitions": auto_transitions(len(shots), drop_cut) if transitions else {},
    }
    if drop_beat is not None:
        edl["beats"]["drop_beat"] = drop_beat
    if beats.get("bar_phase") is not None and intro_shots:
        k_body = round((t0 - phase_t) / period)
        edl["beats"]["bar_phase"] = (beats["bar_phase"] - k_body) % 4     # body beats j with j % 4 == bar_phase are downbeats
    pre = vl.load_preset(preset)
    edl["project"].update(width=pre["width"], height=pre["height"], fps=pre["fps"])
    return edl
