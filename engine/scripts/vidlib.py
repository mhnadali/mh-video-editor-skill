"""Shared helpers for the M.H. Video Editor engine (standard library only).

Finds FFmpeg (also when it is not on PATH, e.g. a fresh winget install), probes media,
and holds the vocabulary used by the planner, the linter and the renderer.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
PRESET_DIR = ENGINE_DIR / "presets"

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".mts", ".m2ts", ".mxf"}

# xfade transitions grouped by how they LOOK. Two names in one family read as one style.
TRANSITION_FAMILIES = {
    "horizontal": {"slideleft", "slideright", "smoothleft", "smoothright", "wipeleft", "wiperight",
                   "horzopen", "horzclose", "coverleft", "coverright", "revealleft", "revealright"},
    "vertical": {"slideup", "slidedown", "smoothup", "smoothdown", "wipeup", "wipedown",
                 "vertopen", "vertclose", "coverup", "coverdown", "revealup", "revealdown"},
    "diagonal": {"diagtl", "diagtr", "diagbl", "diagbr", "wipetl", "wipetr", "wipebl", "wipebr"},
    "circular": {"circleopen", "circleclose", "circlecrop", "radial"},
    "fade": {"fade", "fadeblack", "fadewhite", "fadegrays", "fadefast", "fadeslow", "dissolve"},
    "blur": {"hblur"},
    "pixel": {"pixelize"},
    "squeeze": {"squeezeh", "squeezev"},
    "morph": {"distance"},
    "slice": {"hlslice", "hrslice", "vuslice", "vdslice", "hlwind", "hrwind", "vuwind", "vdwind"},
}
ALL_TRANSITIONS = set().union(*TRANSITION_FAMILIES.values())
# Found by rendering every xfade transition between two test clips (tests/test_transitions.py):
BANNED_REASONS = {
    "zoomin": "renders a flat single-colour frame",
    "rectcrop": "shows a pure black frame mid-cut",
    "circlecrop": "shows a pure black frame mid-cut",
    "squeezev": "crashes FFmpeg (access violation)",
}
BANNED_TRANSITIONS = set(BANNED_REASONS)
# Deliberate dips: allowed, and the QA gate expects the black/white frames they make.
DIP_TRANSITIONS = {"fadeblack": "black", "fadewhite": "white"}
# Order used by the auto-planner: visually different looks first.
PLANNER_TRANSITIONS = ["fade", "slideleft", "circleopen", "hblur", "pixelize", "radial", "coverup", "wipetl",
                       "hlslice", "squeezeh", "vertopen", "diagbl", "slideup", "distance", "circleclose",
                       "fadegrays", "dissolve"]


def family_of(name: str) -> str | None:
    for fam, names in TRANSITION_FAMILIES.items():
        if name in names:
            return fam
    return None


class EngineError(RuntimeError):
    """A problem the user (or agent) can act on; the CLI prints it without a traceback."""


# --------------------------------------------------------------------------- FFmpeg discovery

_FF_CACHE: dict | None = None


def _candidate_dirs() -> list[str]:
    dirs: list[str] = []
    env = os.environ.get("MH_FFMPEG_DIR")
    if env:
        dirs.append(env)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        pat = os.path.join(local, "Microsoft", "WinGet", "Packages", "*FFmpeg*", "ffmpeg-*", "bin")
        dirs += sorted(glob.glob(pat), reverse=True)
        dirs.append(os.path.join(local, "Microsoft", "WinGet", "Links"))
    dirs += ["C:/ffmpeg/bin", "C:/Program Files/ffmpeg/bin", "/usr/local/bin", "/opt/homebrew/bin", "/usr/bin"]
    return dirs


def find_ffmpeg(refresh: bool = False) -> dict:
    """Return {'ffmpeg': path, 'ffprobe': path}. Raises EngineError if FFmpeg cannot be found."""
    global _FF_CACHE
    if _FF_CACHE and not refresh:
        return _FF_CACHE
    exe = ".exe" if os.name == "nt" else ""
    found: dict[str, str] = {}
    for tool in ("ffmpeg", "ffprobe"):
        p = shutil.which(tool)
        if not p:
            for d in _candidate_dirs():
                cand = os.path.join(d, tool + exe)
                if os.path.isfile(cand):
                    p = cand
                    break
        if p:
            found[tool] = p
    missing = [t for t in ("ffmpeg", "ffprobe") if t not in found]
    if missing:
        raise EngineError(
            "FFmpeg not found (" + ", ".join(missing) + "). Install it (Windows: `winget install Gyan.FFmpeg`), "
            "then restart the app, or set MH_FFMPEG_DIR to the folder that contains ffmpeg and ffprobe.")
    _FF_CACHE = found
    return found


def ffmpeg_version(refresh: bool = False) -> tuple[int, ...]:
    out = subprocess.run([find_ffmpeg(refresh)["ffmpeg"], "-version"], capture_output=True, text=True).stdout
    m = re.search(r"ffmpeg version\D*?(\d+)(?:\.(\d+))?(?:\.(\d+))?", out)
    if not m:
        return (0,)
    return tuple(int(x) for x in m.groups() if x is not None)


# Optional capabilities and the FFmpeg filters they need. Stock distro builds lack some of them (libass, libvidstab,
# libzimg are external libraries), so doctor and lint report this instead of letting FFmpeg fail in the middle of a render.
FEATURE_FILTERS = {
    "colour looks and correction": ("lut3d", "curves"),
    "stabilisation": ("vidstabdetect", "vidstabtransform"),
    "slow motion by interpolation": ("minterpolate",),
    "on-screen text": ("ass",),
    "blur-fill reframing": ("gblur", "overlay"),
    "sound design (whooshes, ducking)": ("sidechaincompress", "anoisesrc", "pan", "highpass", "lowpass", "adelay"),
    "ambient sound": ("highpass", "atempo", "amix", "afade"),
    "HDR tone mapping": ("zscale", "tonemap"),
}
_FILTER_CACHE: set[str] | None = None


def parse_filter_list(text: str) -> set[str]:
    """Names from the output of `ffmpeg -filters` (flags column such as `T.C` or `..C`, then the name)."""
    names = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and len(parts[0]) <= 3 and set(parts[0]) <= set("TSC.|") and re.fullmatch(r"[a-z][a-z0-9_]*", parts[1]):
            names.add(parts[1])
    return names


def available_filters(refresh: bool = False) -> set[str]:
    global _FILTER_CACHE
    if _FILTER_CACHE is None or refresh:
        _FILTER_CACHE = parse_filter_list(run([find_ffmpeg()["ffmpeg"], "-hide_banner", "-filters"]).stdout)
    return _FILTER_CACHE


def missing_for(feature: str, have: set[str]) -> list[str]:
    return [f for f in FEATURE_FILTERS[feature] if f not in have]


def filter_script_flag() -> str:
    """FFmpeg 7 replaced -filter_complex_script with the generic '-/filter_complex file'."""
    return "-/filter_complex" if ffmpeg_version()[0] >= 7 else "-filter_complex_script"


def run(cmd: list[str], check: bool = True, timeout: float | None = None) -> subprocess.CompletedProcess:
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        tail = (r.stderr or "")[-800:]
        raise EngineError(f"command failed (exit {r.returncode}): {Path(str(cmd[0])).name} ...\n{tail}")
    return r


def ffp(path: str | os.PathLike) -> str:
    """Path form FFmpeg accepts on every OS (forward slashes)."""
    return str(path).replace("\\", "/")


def filter_escape_path(path: str | os.PathLike) -> str:
    """Escape a path for use inside a filter argument (fontfile=..., subtitles=...)."""
    p = ffp(path)
    return p.replace(":", "\\:").replace("'", "\\'")


# --------------------------------------------------------------------------- probing

def _rate(txt: str | None) -> float:
    if not txt or txt in ("0/0", "N/A"):
        return 0.0
    if "/" in txt:
        a, b = txt.split("/", 1)
        try:
            return float(a) / float(b) if float(b) else 0.0
        except ValueError:
            return 0.0
    try:
        return float(txt)
    except ValueError:
        return 0.0


def parse_probe(data: dict) -> dict:
    """Turn raw ffprobe JSON into the small dict the engine needs."""
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})
    info: dict = {"duration": 0.0, "has_video": v is not None, "has_audio": a is not None}
    try:
        info["duration"] = float(fmt.get("duration") or (v or {}).get("duration") or (a or {}).get("duration") or 0)
    except ValueError:
        info["duration"] = 0.0
    try:
        info["size"] = int(fmt.get("size") or 0)
    except ValueError:
        info["size"] = 0
    if v:
        rot = 0
        for sd in v.get("side_data_list") or []:
            if "rotation" in sd:
                rot = int(round(float(sd["rotation"])))
        if not rot and (v.get("tags") or {}).get("rotate"):
            rot = int(float(v["tags"]["rotate"]))
        w, h = int(v.get("width") or 0), int(v.get("height") or 0)
        if rot % 180 != 0:
            w, h = h, w
        trc = v.get("color_transfer") or ""
        info.update(width=w, height=h, rotation=rot, fps=_rate(v.get("avg_frame_rate")) or _rate(v.get("r_frame_rate")),
                    codec=v.get("codec_name"), pix_fmt=v.get("pix_fmt"), color_transfer=trc,
                    hdr=trc in ("arib-std-b67", "smpte2084"), nb_frames=int(v.get("nb_frames") or 0))
    if a:
        info.update(audio_codec=a.get("codec_name"), sample_rate=int(a.get("sample_rate") or 0),
                    channels=int(a.get("channels") or 0))
    return info


_PROBE_CACHE: dict[tuple, dict] = {}


def probe(path: str | os.PathLike) -> dict:
    """ffprobe summary, cached per (path, size, mtime) so lint, render and analysis never probe a file twice."""
    try:
        st = os.stat(path)
    except OSError:
        raise EngineError(f"file not found: {path}")
    key = (os.path.abspath(path), st.st_size, int(st.st_mtime))
    hit = _PROBE_CACHE.get(key)
    if hit is not None:
        return dict(hit)
    r = run([find_ffmpeg()["ffprobe"], "-v", "error", "-print_format", "json", "-show_format", "-show_streams", ffp(path)])
    info = parse_probe(json.loads(r.stdout or "{}"))
    _PROBE_CACHE[key] = info
    return dict(info)


def probe_many(paths, workers: int | None = None) -> dict:
    """Probe many files in parallel. Returns {path: info or EngineError}; fills the cache."""
    from concurrent.futures import ThreadPoolExecutor
    uniq = list(dict.fromkeys(str(p) for p in paths))

    def one(p):
        try:
            return p, probe(p)
        except EngineError as e:
            return p, e

    with ThreadPoolExecutor(workers or cpu_workers(8)) as ex:
        return dict(ex.map(one, uniq))


# --------------------------------------------------------------------------- misc helpers

def frames(t: float, fps: float) -> int:
    return int(round(t * fps))


def even(n: float) -> int:
    """Nearest even integer (video dimensions must be even), never below 2."""
    return max(2, int(round(n / 2.0)) * 2)


def load_json(path: str | os.PathLike) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise EngineError(f"file not found: {path}")
    except json.JSONDecodeError as e:
        raise EngineError(f"invalid JSON in {path}: {e}")
    except UnicodeDecodeError:
        raise EngineError(f"{path} is not a UTF-8 text file")
    except (OSError, ValueError) as e:
        raise EngineError(f"cannot read {path}: {e}")


def replace_file(tmp: str | os.PathLike, dst: str | os.PathLike, retries: int = 40) -> None:
    """os.replace that survives Windows' "Access is denied" when another thread or process has `dst` open or is replacing
    it at the same moment (parallel part renders share cache files). The file is only ever swapped, never half-written."""
    for attempt in range(retries):
        try:
            os.replace(tmp, dst)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            time.sleep(0.02 * (attempt + 1) ** 0.5)


def save_json(path: str | os.PathLike, data) -> None:
    """Write JSON atomically: an interrupted write (Ctrl-C, full disk) must not leave a half-written EDL or scores file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        replace_file(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def find_font(bold: bool = True) -> str | None:
    cands = []
    if os.name == "nt":
        win = os.environ.get("WINDIR", "C:/Windows")
        cands += [f"{win}/Fonts/arialbd.ttf" if bold else f"{win}/Fonts/arial.ttf", f"{win}/Fonts/segoeuib.ttf"]
    cands += ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
              "/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial Bold.ttf"]
    return next((c for c in cands if os.path.isfile(c)), None)


def cpu_workers(cap: int = 4) -> int:
    return max(1, min(cap, (os.cpu_count() or 2)))


def load_preset(name: str) -> dict:
    if not isinstance(name, str) or not name.strip():
        raise EngineError(f"a preset is named by a string, found {name!r}")
    p = Path(name)
    if not p.suffix:
        p = PRESET_DIR / f"{name}.json"
    return load_json(p)


def say(msg: str) -> None:
    print(msg, flush=True)


def die(msg: str, code: int = 2) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)
