"""Phase 4: sound design and the natural sound of the clips.

Everything is synthesised with FFmpeg's own generators (no sound files are downloaded or bundled).

EDL:
  "style": {"sound_design": {"whoosh": true, "level_db": -24, "max_events": 12, "duck": true}}
      A short whoosh on every moving transition (slides, wipes, covers, circles ...), panned the way the picture moves
      (a slide to the left sweeps right -> left). Soft transitions (fade, dissolve) stay silent. While a whoosh plays the
      music is ducked by a sidechain compressor, so effects never fight the beat.
  "style": {"ambient": {"level_db": -14, "fade_ms": 40, "shots": "all" | [0, 3, 7], "highpass_hz": 90}}
      The recorded sound of each shot (engine, wind, voices) under the music, level set RELATIVE to the music: every
      shot's mean level is normalised first, so a loud shot does not jump out. Slowed or sped-up shots are muted unless
      the shot says  "audio": {"speed_follow": true}.  A shot can opt out with  "audio": {"use": false}  or add gain with
      "audio": {"gain_db": 3}.
The riser and boom around the drop (style.riser / style.boom) are separate and unchanged.
"""
from __future__ import annotations

import os
import re
import subprocess

import vidlib as vl

# transition name -> whoosh kind
_RL = {"slideleft", "smoothleft", "wipeleft", "coverleft", "revealleft", "hlslice", "hlwind", "horzopen"}
_LR = {"slideright", "smoothright", "wiperight", "coverright", "revealright", "hrslice", "hrwind", "horzclose"}
_UP = {"slideup", "smoothup", "wipeup", "coverup", "revealup", "vuslice", "vuwind", "vertopen"}
_DOWN = {"slidedown", "smoothdown", "wipedown", "coverdown", "revealdown", "vdslice", "vdwind", "vertclose"}
_SWELL = {"circleopen", "circleclose", "radial", "fadeblack"}
_SWISH = {"pixelize", "hblur", "distance", "squeezeh", "diagtl", "diagtr", "diagbl", "diagbr", "wipetl", "wipetr", "wipebl", "wipebr"}
SILENT = {"fade", "dissolve", "fadegrays", "fadewhite", "fadefast", "fadeslow"}

DEFAULT_SOUND_DESIGN = {"whoosh": True, "level_db": -24.0, "max_events": 12, "duck": True}
DEFAULT_AMBIENT = {"level_db": -14.0, "fade_ms": 40, "shots": "all", "highpass_hz": 90, "skip": ["wind", "clipped"]}
SKIPPABLE = ("wind", "clipped", "rumble", "voices", "other")


def whoosh_kind(transition: str) -> str | None:
    if transition in _RL:
        return "rl"
    if transition in _LR:
        return "lr"
    if transition in _UP:
        return "up"
    if transition in _DOWN:
        return "down"
    if transition in _SWELL:
        return "swell"
    if transition in _SWISH:
        return "swish"
    return None


def sound_design(edl: dict) -> dict | None:
    sd = (edl.get("style") or {}).get("sound_design")
    if not sd:
        return None
    out = dict(DEFAULT_SOUND_DESIGN)
    out.update(sd if isinstance(sd, dict) else {})
    return out


MAX_LEAD_LAG = 1.0            # seconds a clip's sound may start before (J-cut) or outlast (L-cut) its picture


def ambient_config(edl: dict) -> dict | None:
    a = (edl.get("style") or {}).get("ambient")
    if not a:
        return None
    out = dict(DEFAULT_AMBIENT)
    out.update(a if isinstance(a, dict) else {})
    return out


def validate(edl: dict) -> list[dict]:
    out: list[dict] = []

    def add(level, code, msg):
        out.append({"level": level, "code": code, "msg": msg})

    style = edl.get("style") or {}
    sd = style.get("sound_design")
    if sd not in (None, False):
        if sd is not True and not isinstance(sd, dict):
            add("error", "sound-design", "style.sound_design must be true or an object")
        else:
            cfg = dict(DEFAULT_SOUND_DESIGN, **(sd if isinstance(sd, dict) else {}))
            if not -40 <= cfg["level_db"] <= -6:
                add("error", "sound-design", "sound_design.level_db must be between -40 and -6")
            if not 1 <= cfg["max_events"] <= 60:
                add("error", "sound-design", "sound_design.max_events must be 1-60")
    amb = style.get("ambient")
    if amb not in (None, False):
        if amb is not True and not isinstance(amb, dict):
            add("error", "ambient", "style.ambient must be true or an object")
        else:
            cfg = dict(DEFAULT_AMBIENT, **(amb if isinstance(amb, dict) else {}))
            if not -40 <= cfg["level_db"] <= 0:
                add("error", "ambient", "ambient.level_db (relative to the music) must be between -40 and 0")
            if cfg["shots"] != "all" and not (isinstance(cfg["shots"], list) and all(isinstance(i, int) for i in cfg["shots"])):
                add("error", "ambient", "ambient.shots must be \"all\" or a list of shot numbers")
            if not 0 <= cfg["fade_ms"] <= 500:
                add("error", "ambient", "ambient.fade_ms must be 0-500")
            th = cfg.get("thresholds") or {}
            import vid_audio
            if not (isinstance(th, dict) and all(k in vid_audio.THRESHOLDS and isinstance(v, (int, float)) for k, v in th.items())):
                add("error", "ambient", f"ambient.thresholds may set only {', '.join(vid_audio.THRESHOLDS)} (numbers; from calibrate_audio.py)")
            if not (isinstance(cfg["skip"], list) and all(s in SKIPPABLE for s in cfg["skip"])):
                add("error", "ambient", f"ambient.skip must be a list of {', '.join(SKIPPABLE)} (default wind and clipped; [] keeps all)")
    for j, shot in enumerate(edl.get("shots") or []):
        a = shot.get("audio")
        if a is not None and not isinstance(a, dict):
            add("error", "shot-audio", f"shot {j}: audio must be an object")
        elif isinstance(a, dict):
            if not -30 <= float(a.get("gain_db", 0)) <= 18:
                add("error", "shot-audio", f"shot {j}: audio.gain_db must be between -30 and 18")
            for key in ("lead", "lag"):
                v = a.get(key)
                if v is not None and not (isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= MAX_LEAD_LAG):
                    add("error", "shot-audio", f"shot {j}: audio.{key} must be 0 to {MAX_LEAD_LAG} seconds")
    return out


# --------------------------------------------------------------------------- whooshes

def whoosh_events(trans: dict[int, str], bounds: list[int], fps: float, cfg: dict) -> list[dict]:
    """[{'t': centre seconds, 'kind': ..., 'name': transition}] for the moving transitions, spread evenly if capped."""
    ev = []
    for j in sorted(trans):
        k = whoosh_kind(trans[j])
        if k:
            ev.append({"t": bounds[j] / fps, "kind": k, "name": trans[j], "j": j})
    cap = int(cfg["max_events"])
    if len(ev) > cap:                                             # keep an even spread instead of the first N
        step = len(ev) / cap
        ev = [ev[int(i * step)] for i in range(cap)]
    return ev


def whoosh_source(kind: str, level_db: float) -> tuple[list[str], float]:
    """Filter lines (ending in a stereo label placeholder `{out}`) and the length of one whoosh in seconds."""
    d = {"swell": 0.55, "swish": 0.30}.get(kind, 0.38)
    base = f"anoisesrc=d={d}:c=pink:r=44100:a=0.6"
    shape = {"up": "highpass=f=700,lowpass=f=9000", "down": "highpass=f=120,lowpass=f=2600",
             "swell": "highpass=f=150,lowpass=f=3800", "swish": "highpass=f=500,lowpass=f=8000"}.get(kind, "highpass=f=260,lowpass=f=7500")
    peak = 0.5 if kind in ("swell",) else 0.45
    env = (f"afade=t=in:st=0:d={d * peak:.3f}:curve=qua,afade=t=out:st={d * peak:.3f}:d={d * (1 - peak):.3f}:curve=qua,"
           f"volume={level_db:.1f}dB")
    if kind in ("rl", "lr"):
        first, second = ("min(1,t/{d})", "max(0,1-t/{d})") if kind == "rl" else ("max(0,1-t/{d})", "min(1,t/{d})")
        lines = [f"{base},{shape},{env},asplit=2[wa{{id}}][wb{{id}}]",
                 f"[wa{{id}}]volume='{first.format(d=d)}':eval=frame[wl{{id}}]",
                 f"[wb{{id}}]volume='{second.format(d=d)}':eval=frame[wr{{id}}]",
                 "[wl{id}][wr{id}]amerge=inputs=2,aformat=channel_layouts=stereo[{out}]"]
    else:
        lines = [f"{base},{shape},{env},pan=stereo|c0=c0|c1=c0,aformat=channel_layouts=stereo[{{out}}]"]
    return lines, d


def whoosh_graph(events: list[dict], level_db: float) -> tuple[list[str], str | None]:
    """Lines producing one stereo bus [sfxbus] with every whoosh placed at its time. (lines, label or None)."""
    if not events:
        return [], None
    lines, labels = [], []
    for i, e in enumerate(events):
        src, d = whoosh_source(e["kind"], level_db)
        delay = max(0, int(round((e["t"] - d / 2) * 1000)))
        for ln in src[:-1]:
            lines.append(ln.replace("{id}", str(i)))
        last = src[-1].replace("{id}", str(i)).replace("[{out}]", f"[wh{i}raw]")
        lines.append(last)
        lines.append(f"[wh{i}raw]adelay={delay}|{delay}[wh{i}]")
        labels.append(f"[wh{i}]")
    if len(labels) == 1:
        lines.append(f"{labels[0]}anull[sfxbus]")
    else:
        lines.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest[sfxbus]")
    return lines, "sfxbus"


# --------------------------------------------------------------------------- ambient (natural sound)

def volume_stats(path: str, start: float, seconds: float) -> tuple:
    """(mean_volume, max_volume) in dB of a window of a file's audio; (None, None) if there is no audio."""
    r = subprocess.run([vl.find_ffmpeg()["ffmpeg"], "-hide_banner", "-nostats", "-v", "info", "-ss", f"{max(0.0, start):.3f}",
                        "-t", f"{seconds:.3f}", "-i", vl.ffp(path), "-vn", "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    mean = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr or "")
    peak = re.search(r"max_volume:\s*(-?[\d.]+) dB", r.stderr or "")
    return (float(mean.group(1)) if mean else None, float(peak.group(1)) if peak else None)


def mean_volume(path: str, start: float, seconds: float) -> float | None:
    """Mean level (dB) of a window of a file's audio, via volumedetect. None if there is no audio."""
    r = subprocess.run([vl.find_ffmpeg()["ffmpeg"], "-hide_banner", "-nostats", "-v", "info", "-ss", f"{max(0.0, start):.3f}",
                        "-t", f"{seconds:.3f}", "-i", vl.ffp(path), "-vn", "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr or "")
    return float(m.group(1)) if m else None


def sound_of(clip: str, start: float, seconds: float) -> dict | None:
    """What the window's sound is (vid_audio), or None when it cannot be measured (then nothing is skipped for it)."""
    try:
        import vid_audio
        return vid_audio.measure(clip, start, seconds)
    except Exception:                                           # noqa: BLE001 - a measurement never stops a render
        return None


def ambient_segments(edl: dict, windows: list[dict], music_mean_db: float | None, skipped: list | None = None) -> list[dict]:
    """Which shots contribute natural sound, with the gain (dB) that puts each at music + level_db.

    `windows`: [{'j', 'clip', 'start0', 'seconds', 'out_start', 'out_seconds', 'speed', 'has_audio'}] per shot. A window whose
    sound is measured as one of `ambient.skip` (default wind and clipping) is left out, unless the shot says `"audio": {"use": true}`;
    `skipped` (if given) receives {'j', 'why'} for each.
    """
    cfg = ambient_config(edl)
    if not cfg:
        return []
    chosen = None if cfg["shots"] == "all" else set(cfg["shots"])
    ref = (music_mean_db if music_mean_db is not None else -20.0) + cfg["level_db"]
    segs = []
    from concurrent.futures import ThreadPoolExecutor

    todo = []
    for w in windows:
        shot = edl["shots"][w["j"]]
        opts = shot.get("audio") if isinstance(shot.get("audio"), dict) else {}
        if chosen is not None and w["j"] not in chosen:
            continue
        if opts.get("use") is False or not w["has_audio"]:
            continue
        if w["speed"] and not opts.get("speed_follow"):
            continue                                              # retimed picture: sound would be wrong
        todo.append((w, opts))

    skip = set(cfg.get("skip") or [])

    def measure(item):
        w, opts = item
        snd = sound_of(w["clip"], w["start0"], w["seconds"]) if skip and opts.get("use") is not True else None
        return mean_volume(w["clip"], w["start0"], w["seconds"]), snd

    with ThreadPoolExecutor(vl.cpu_workers(4)) as ex:
        means = list(ex.map(measure, todo))
    import vid_audio
    for (w, opts), (mv, snd) in zip(todo, means):
        if mv is None or mv < -75:
            continue                                              # silent recording
        if snd is not None:
            th = dict(vid_audio.THRESHOLDS, **(cfg.get("thresholds") or {}))
            lab = vid_audio.label(snd, th)
            why = "clipped" if ("clipped" in skip and snd.get("clipped", 0.0) > th["clipped"]) else (lab if lab in skip else None)
            if why:
                if skipped is not None:
                    skipped.append({"j": w["j"], "why": why})
                continue
        gain = max(-12.0, min(18.0, ref - mv)) + float(opts.get("gain_db", 0.0))
        tempo = None
        if w["speed"] and opts.get("speed_follow"):
            tempo = (w["speed"]["from"] + w["speed"]["to"]) / 2.0
        seg = {"j": w["j"], "clip": vl.ffp(w["clip"]), "start": round(w["start0"], 3), "seconds": round(w["seconds"], 3),
               "delay": round(w["out_start"], 3), "out_seconds": round(w["out_seconds"], 3), "gain_db": round(gain, 2),
               "tempo": tempo}
        if tempo is None:
            seg = apply_lead_lag(seg, opts)
        segs.append(seg)
    return segs


def apply_lead_lag(seg: dict, opts: dict) -> dict:
    """J-cut / L-cut: the shot's own sound enters `lead` seconds before its picture and/or lingers `lag` seconds after it.

    The lead cannot start before the beginning of the video or of the source clip, so it is cut down to what fits.
    """
    try:
        lead = max(0.0, min(float(opts.get("lead") or 0.0), MAX_LEAD_LAG))
        lag = max(0.0, min(float(opts.get("lag") or 0.0), MAX_LEAD_LAG))
    except (TypeError, ValueError):
        return seg
    lead = min(lead, seg["delay"], seg["start"])
    if lead <= 0 and lag <= 0:
        return seg
    out = dict(seg)
    out["delay"] = round(seg["delay"] - lead, 3)
    out["start"] = round(seg["start"] - lead, 3)
    out["out_seconds"] = round(seg["out_seconds"] + lead + lag, 3)
    out["seconds"] = round(seg["seconds"] + lead + lag, 3)
    out["lead"], out["lag"] = round(lead, 3), round(lag, 3)
    return out


def ambient_lines(segs: list[dict], first_input: int, cfg: dict) -> tuple[list[str], str | None]:
    """Filter lines for the ambient bed: each segment trimmed, levelled, faded, delayed; one stereo bus [ambbus]."""
    if not segs:
        return [], None
    lines, labels = [], []
    fade = max(0.0, cfg["fade_ms"] / 1000.0)
    for k, s in enumerate(segs):
        idx = first_input + k
        chain = [f"aformat=sample_rates=44100:channel_layouts=stereo", f"highpass=f={cfg['highpass_hz']}"]
        if s["tempo"] and abs(s["tempo"] - 1.0) > 1e-3:
            t = max(0.5, min(2.0, s["tempo"]))
            chain.append(f"atempo={t:.4f}")
        chain.append(f"atrim=duration={s['out_seconds']:.3f}")
        chain.append("asetpts=PTS-STARTPTS")
        if fade > 0:
            chain.append(f"afade=t=in:st=0:d={fade:.3f}")
            chain.append(f"afade=t=out:st={max(0.0, s['out_seconds'] - fade):.3f}:d={fade:.3f}")
        chain.append(f"volume={s['gain_db']:.2f}dB")
        delay = max(0, int(round(s["delay"] * 1000)))
        chain.append(f"adelay={delay}|{delay}")
        lines.append(f"[{idx}:a]{','.join(chain)}[am{k}]")
        labels.append(f"[am{k}]")
    if len(labels) == 1:
        lines.append(f"{labels[0]}anull[ambbus]")
    else:
        lines.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest[ambbus]")
    return lines, "ambbus"
