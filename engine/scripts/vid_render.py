"""Step 4: render an EDL. Each shot becomes a cached part; one filter graph joins them.

Hard-won rules baked in (see references/pitfalls.md):
  * zoom is push-in only (zoom-out crashes some FFmpeg builds)
  * blends run in gbrp (blending yuv420p shifts chroma -> purple cast)
  * the drop flash is `xfade=fadewhite` + a short rgbashift, never `fade=in:start_frame`
  * no temporal grain (it exploded the file to 400+ MB)
  * HLG/PQ clips are tone-mapped to SDR bt709 before grading
  * parts are padded by 2 frames ONLY on sides that touch a transition so every cut stays on its beat

Per shot (phase 4): retime (slow motion / ramps) -> reframe to the output size (cover / blur-fill / fit, with a focus
point) -> stabilise (vidstab, two passes) -> push-in zoom -> correction + look (one `curves`, one 3D LUT) -> finish.
The same EDL renders to other formats (`render(..., fmt="youtube-16x9")`): every part is cached per output size.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import vid_color
import vid_deliver
import vid_edl as ve
import vid_motion
import vid_sfx
import vid_sheets
import vid_text
import vidlib as vl

OV = 2 * ve.PAD
MIN_PART_BYTES = 200          # a valid MP4 of a flat 15-frame shot can be under 1 KB: size is only a sanity check
_ENC_CACHE: dict = {}                       # encoder probes: {kind: bool, 'master': name or None}


# --------------------------------------------------------------------------- encoder choice

def nvenc_works() -> bool:
    return encoder_available("nvenc")


# h264 hardware encoders by short name -> the FFmpeg encoder. `auto` tries these in order, then libx264.
HW_H264 = {"nvenc": "h264_nvenc", "videotoolbox": "h264_videotoolbox", "qsv": "h264_qsv", "vaapi": "h264_vaapi", "amf": "h264_amf"}
HW_PREFERENCE = ("nvenc", "videotoolbox", "qsv")        # encoders the assembly can feed system frames to directly


def encoder_available(kind: str) -> bool:
    """Does this machine have a working `kind` H.264 encoder? A 0.2 s null encode is the only reliable probe (a
    present encoder can still fail without the GPU/driver). Cached per process."""
    if kind in _ENC_CACHE:
        return _ENC_CACHE[kind]
    enc = HW_H264.get(kind)
    ok = False
    if enc:
        src = ["-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2"]
        try:
            if kind == "vaapi":
                dev = os.environ.get("MH_VAAPI_DEVICE", "/dev/dri/renderD128")
                cmd = ([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-vaapi_device", dev] + src +
                       ["-vf", "format=nv12,hwupload", "-c:v", enc, "-f", "null", "-"])
            else:
                cmd = [vl.find_ffmpeg()["ffmpeg"], "-v", "error"] + src + ["-c:v", enc, "-f", "null", "-"]
            ok = vl.run(cmd, check=False, timeout=30).returncode == 0
        except Exception:
            ok = False
    _ENC_CACHE[kind] = ok
    return ok


# The optional master: the same edit as 10-bit HEVC (Main 10), for archiving and for platforms that take HEVC uploads. The
# delivery file stays 8-bit H.264. Software x265 first (best quality per bit), then the GPU encoders that can do Main 10.
MASTER_PIX_FMT = "yuv420p10le"
MASTER_ENCODERS = {
    "libx265": ["-c:v", "libx265", "-preset", "slow", "-x265-params", "log-level=error"],
    "hevc_nvenc": ["-c:v", "hevc_nvenc", "-preset", "p6", "-profile:v", "main10", "-rc", "vbr", "-b:v", "0"],
    "hevc_videotoolbox": ["-c:v", "hevc_videotoolbox", "-profile:v", "main10"],
}
MASTER_QUALITY = {"standard": {"crf": 18, "cq": 22, "q": 55}, "high": {"crf": 15, "cq": 18, "q": 65},
                  "max": {"crf": 12, "cq": 15, "q": 75}}


def master_encoder() -> str | None:
    """The first HEVC encoder that really encodes 10-bit here (a 0.2 s null encode, cached), or None."""
    if "master" in _ENC_CACHE:
        return _ENC_CACHE["master"]
    found = None
    for name, args in MASTER_ENCODERS.items():
        cmd = [vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=256x256:d=0.2"] + args + \
              ["-pix_fmt", "p010le" if name == "hevc_videotoolbox" else MASTER_PIX_FMT, "-f", "null", "-"]
        try:
            if vl.run(cmd, check=False, timeout=30).returncode == 0:
                found = name
                break
        except Exception:
            continue
    _ENC_CACHE["master"] = found
    return found


def master_encoder_args(quality: str | None) -> tuple[list[str], str]:
    name = master_encoder()
    if not name:
        raise vl.EngineError("no 10-bit HEVC encoder here (libx265, hevc_nvenc or hevc_videotoolbox) for --master; "
                             "the FFmpeg builds from gyan.dev, BtbN, Homebrew and most Linux distributions include libx265")
    mq = MASTER_QUALITY[quality if quality in MASTER_QUALITY else "high"]
    args = list(MASTER_ENCODERS[name])
    if name == "libx265":
        args = args[:-2] + ["-crf", str(mq["crf"]), "-x265-params",
                            "log-level=error:colorprim=bt709:transfer=bt709:colormatrix=bt709:range=limited"]
    elif name == "hevc_nvenc":
        args += ["-cq", str(mq["cq"])]
    else:
        args += ["-q:v", str(mq["q"])]
    pix = "p010le" if name == "hevc_videotoolbox" else MASTER_PIX_FMT
    return args + ["-tag:v", "hvc1", "-pix_fmt", pix], name


def hw_encoders() -> dict:
    """{short name: available?} for every hardware H.264 encoder the engine knows about (for `doctor`)."""
    return {k: encoder_available(k) for k in HW_H264}


def hwdecode_args(mode: str) -> list[str]:
    """'auto' = CUDA decode when an NVIDIA GPU encodes fine (about 20% faster on 4K HEVC), 'none' = software."""
    mode = (mode or "none").lower()
    if os.environ.get("MH_HWACCEL"):
        return ["-hwaccel", os.environ["MH_HWACCEL"]]
    if mode in ("auto", "cuda") and nvenc_works():
        return ["-hwaccel", "cuda"]
    return []


def _resolve_encoder(pref: str) -> str:
    """short name ('auto'|'libx264'|'nvenc'|...) -> the chosen short name, honouring availability."""
    pref = (pref or "auto").lower()
    if pref == "libx264":
        return "libx264"
    if pref == "auto":
        for k in HW_PREFERENCE:
            if encoder_available(k):
                return k
        return "libx264"
    if pref in HW_H264:
        if encoder_available(pref):
            return pref
        raise vl.EngineError(f"{pref} encoder requested but not available on this machine "
                             "(run `doctor` to see which hardware encoders work here)")
    raise vl.EngineError(f"unknown encoder '{pref}'")


# Constant-quality settings. Every shot is encoded once as a cached part and the parts are encoded again when they are
# assembled, so the parts must be close to lossless or the second encode compounds the first. `high` (the default for a
# final render) keeps the picture visually as good as the source; `standard` is the pre-0.14 behaviour.
QUALITY = {
    "standard": {"part_crf": 16, "final_crf": 18, "preset": "slow", "nvenc_cq": 21, "hw_q": 22, "vt_q": 40, "audio_kbps": 192},
    "high": {"part_crf": 12, "final_crf": 16, "preset": "medium", "nvenc_cq": 17, "hw_q": 17, "vt_q": 55, "audio_kbps": 256},
    "max": {"part_crf": 8, "final_crf": 11, "preset": "slower", "nvenc_cq": 14, "hw_q": 13, "vt_q": 70, "audio_kbps": 320},
}


def quality_settings(name: str | None) -> dict:
    q = name or "standard"
    if q not in QUALITY:
        raise vl.EngineError(f"unknown quality '{q}' (known: {', '.join(QUALITY)})")
    return QUALITY[q]


# How a shot is stored between its own filters and the assembly. Final renders at high and max keep 10 bits (and HLG
# sources their 10 bits); the assembly works in 16-bit YUV and dithers once to 8 bits. Measured on 7 real Kavir shots against
# the same filters without any lossy step (docs/CALIBRATION.md, phase H): lossless FFV1 parts gave chroma PSNR +0.26 dB and
# SSIM +0.12 dB over the old 8-bit parts but 9.7 times the disk; x264 10-bit at CRF 6 gave +0.17 / +0.10 at 1.9 times. So
# `high` uses x264 10-bit (FFV1 if this libx264 has no 10-bit), `max` is lossless. Previews and `standard` stay light 8-bit.
PRECISE_PARTS = {
    "high": {"pix_fmt": "yuv420p10le", "ext": ".mp4", "args": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "6"]},
    "max": {"pix_fmt": "yuv420p10le", "ext": ".mkv", "args": ["-c:v", "ffv1", "-level", "3", "-slices", "16", "-g", "1"]},
}
LIGHT_PART = {"pix_fmt": "yuv420p", "ext": ".mp4"}
PART_EXTS = (".mp4", ".mkv")
# The precise assembly converts only with zscale and the Rec.709 matrix stated: FFmpeg's automatic conversions guess the matrix
# from tags (an untagged or RGB frame becomes BT.601) and `fade` has no 16-bit RGB path, so it inserts one of those guesses
# (measured: skin 227/187/140 came out 210/175/134). The picture stays in 16-bit YUV; RGB only for the screen-blended flare,
# in 12 bits: FFmpeg's `blend` mixes up the green and blue planes of gbrp16le (screen over black turned 191/47/21 into 191/201/174).
Z709 = "tin=709:pin=709:t=709:p=709"
FLARE_RGB = "gbrp12le"
YUV_TO_RGB = f"zscale=min=709:rin=tv:{Z709}:r=full,format={FLARE_RGB}"
RGB_TO_YUV16 = f"zscale=rin=full:{Z709}:m=709:r=tv,format=yuv444p16le"
TO_YUV16 = f"zscale=min=709:rin=tv:{Z709}:m=709:r=tv,format=yuv444p16le"
FINAL_DITHER = f"zscale=min=709:rin=tv:{Z709}:m=709:r=tv:d=error_diffusion,"       # once, to 8 bits, by error diffusion


def precise_parts(quality: str | None, preview: bool = False) -> bool:
    return not preview and (quality or "standard") in ("high", "max")


def x264_10bit() -> bool:
    """Does this libx264 encode 10-bit (every build since 2019 does both depths; older ones were 8-bit only)? Cached."""
    if "x264_10bit" not in _ENC_CACHE:
        try:                                  # no FFmpeg at all counts as no (the render will say so where it matters)
            cmd = [vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=64x64:d=0.1", "-c:v", "libx264",
                   "-pix_fmt", "yuv420p10le", "-f", "null", "-"]
            _ENC_CACHE["x264_10bit"] = vl.run(cmd, check=False, timeout=30).returncode == 0
        except Exception:
            _ENC_CACHE["x264_10bit"] = False
    return _ENC_CACHE["x264_10bit"]


def part_encoding(quality: str | None, preview: bool = False) -> dict:
    """pix_fmt, file extension and encoder arguments of the cached parts."""
    if precise_parts(quality, preview):
        q = quality if quality == "max" or x264_10bit() else "max"
        return dict(PRECISE_PARTS[q], args=list(PRECISE_PARTS[q]["args"]))
    return dict(LIGHT_PART, args=["-c:v", "libx264", "-preset", "veryfast", "-crf", str(quality_settings(quality)["part_crf"])])


def encoder_args(pref: str, preview: bool, bitrate_kbps: int | None = None, maxrate_kbps: int | None = None,
                 two_pass: int | None = None, passlog: str | None = None, quality: str | None = None) -> tuple[list[str], str]:
    """FFmpeg video-encoder arguments. With `bitrate_kbps` the encoder targets a bitrate (for a size budget); otherwise
    it uses constant quality. `two_pass` is 1 or 2 for the two libx264 passes of a strict size target."""
    kind = _resolve_encoder(pref)
    enc = "libx264" if kind == "libx264" else HW_H264[kind]
    qs = quality_settings(quality)
    if bitrate_kbps:
        mr = maxrate_kbps or int(bitrate_kbps * 1.45)
        buf = int(mr * 2)
        if kind == "libx264":
            args = ["-c:v", "libx264", "-preset", "veryfast" if preview else "slow",
                    "-b:v", f"{bitrate_kbps}k", "-maxrate", f"{mr}k", "-bufsize", f"{buf}k"]
            if two_pass:
                args += ["-pass", str(two_pass), "-passlogfile", passlog or "mh_pass"]
            return args, "libx264"
        if kind == "nvenc":
            return (["-c:v", enc, "-preset", "p5", "-rc", "vbr", "-b:v", f"{bitrate_kbps}k",
                     "-maxrate", f"{mr}k", "-bufsize", f"{buf}k"], enc)
        return ["-c:v", enc, "-b:v", f"{bitrate_kbps}k", "-maxrate", f"{mr}k", "-bufsize", f"{buf}k"], enc
    if kind == "nvenc":
        q = "28" if preview else str(qs["nvenc_cq"])
        return ["-c:v", enc, "-preset", "p5", "-rc", "vbr", "-cq", q, "-b:v", "0"], enc
    if kind in ("videotoolbox", "qsv", "vaapi", "amf"):
        return ["-c:v", enc, "-q:v", "55" if preview else str(qs["vt_q"])] if kind == "videotoolbox" else \
               ["-c:v", enc, "-global_quality", "28" if preview else str(qs["hw_q"])], enc
    crf = "24" if preview else str(qs["final_crf"])
    return ["-c:v", "libx264", "-preset", "veryfast" if preview else qs["preset"], "-crf", crf], "libx264"


# --------------------------------------------------------------------------- shots

def _legacy_grade(g) -> str:
    """Raw FFmpeg filter string or a grade preset file (engine/presets/*.json). Looks are handled by vid_color."""
    if not g:
        return ""
    if g.strip().startswith(("eq=", "colorbalance", "curves", "lut3d")):      # raw filter string
        return g
    return vl.load_preset(g)["grade"]


def _grade(style: dict) -> str:
    g = style.get("grade")
    return "" if vid_color.is_look_spec(g) else _legacy_grade(g)


def output_size(edl: dict, fmt: str | None = None, scale: float = 1.0) -> tuple[int, int]:
    """(width, height) for the project, or for another format preset (e.g. 'youtube-16x9'), times `scale`."""
    p = ve.project(edl)
    w, h = p["width"], p["height"]
    if fmt:
        pre = vl.load_preset(fmt)
        w, h = pre["width"], pre["height"]
    return vl.even(w * scale), vl.even(h * scale)


def format_tag(fmt: str | None) -> str:
    """'youtube-16x9' -> '16x9' (used in output file names)."""
    if not fmt:
        return ""
    m = re.search(r"(\d+x\d+)$", fmt)
    return m.group(1) if m else re.sub(r"[^A-Za-z0-9]+", "", fmt)


REFRAME_MODES = ("cover", "blur-fill", "fit")


def part_plan(edl: dict, j: int, bounds: list[int], trans: dict[int, str], scale: float, fmt: str | None = None,
              lut_dir: str | os.PathLike | None = None) -> dict:
    p = ve.project(edl)
    style = ve.merged_style(edl)
    fps = p["fps"]
    shot = edl["shots"][j]
    ovs = ve.overlap_frames(edl, trans)
    head = ovs[j] // 2 if j in trans else 0
    tail = ovs[j + 1] // 2 if (j + 1) in trans else 0
    n = (bounds[j + 1] - bounds[j]) + head + tail
    W, H = output_size(edl, fmt, scale)
    split = None
    if style["drop_flash"] and ve.drop_boundary(edl, bounds) == j:
        split = (head, head + 5)          # part frames that sit on timeline frames df..df+5
    grade = shot.get("grade", style.get("grade"))
    look = grade if vid_color.is_look_spec(grade) else None
    correct = shot.get("correct", style.get("correct"))
    reframe = shot.get("reframe") or style.get("reframe")
    if fmt and not (edl.get("style") or {}).get("reframe") and not shot.get("reframe"):
        reframe = "blur-fill"             # another aspect ratio: never silently crop away most of the picture
    reframe = reframe or "cover"
    return {"j": j, "n": n, "head": head, "tail": tail, "W": W, "H": H, "fps": fps, "clip": shot["clip"],
            "src_start": shot.get("src_start"), "zoom": float(style["zoom"]),
            "grade": "" if look else _legacy_grade(grade), "look": look, "correct": correct, "split": split,
            "stab": vid_motion.normalize_stabilize(shot.get("stabilize")), "speed": vid_motion.normalize_speed(shot.get("speed")),
            "speed_mode": shot.get("speed_mode", "auto"), "reframe": reframe, "focus": shot.get("focus"),
            "lut_dir": str(lut_dir) if lut_dir else None, "tonemap": style.get("hdr_tonemap")}


def _reframe(pl: dict) -> list[str]:
    """Filters that bring the decoded frame to W x H. Returned as chain pieces joined with commas / semicolons."""
    W, H = pl["W"], pl["H"]
    mode = pl.get("reframe") or "cover"
    focus = pl.get("focus")
    if not (isinstance(focus, (list, tuple)) and len(focus) == 2):
        focus = None                                  # "auto" is resolved before this in render_part; ignore anything else
    if mode == "fit":
        return [f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black"]
    if mode == "blur-fill":
        bg = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma={max(8, W // 36)},"
              "eq=brightness=-0.10:saturation=1.1")
        return [f"split=2[bgi][fgi];[bgi]{bg}[bgb];[fgi]scale={W}:{H}:force_original_aspect_ratio=decrease[fgs];"
                f"[bgb][fgs]overlay=x=(W-w)/2:y=(H-h)/2:format=auto"]
    if focus:
        fx, fy = float(focus[0]), float(focus[1])
        return [f"scale={W}:{H}:force_original_aspect_ratio=increase",
                f"crop={W}:{H}:x='(iw-ow)*{fx:.4f}':y='(ih-oh)*{fy:.4f}'"]
    return [f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos", f"crop={W}:{H}"]


def part_front(pl: dict, hdr: bool, src_fps: float = 30.0) -> str:
    """Everything up to and including the reframe: the geometry the stabiliser must see in both passes."""
    parts: list[str] = []
    if hdr:
        tm = vid_sheets.tonemap_chain(pl.get("tonemap")).rstrip(",")
        if (pl.get("pix_fmt") or "yuv420p") != "yuv420p":
            tm = tm.replace("format=yuv420p", "format=yuv420p10le")      # a precise part keeps the HLG source's 10 bits
        parts.append(tm)
    out_seconds = pl["n"] / pl["fps"]
    if pl.get("speed"):
        parts += vid_motion.retime_chain(pl["speed"], out_seconds, pl["fps"], src_fps, pl.get("speed_mode", "auto"))
    else:
        parts.append(f"fps={pl['fps']}")
    parts += _reframe(pl)
    parts.append("setsar=1")
    return ",".join(parts)


def part_vf(pl: dict, hdr: bool, src_fps: float = 30.0) -> str:
    W, H, n = pl["W"], pl["H"], pl["n"]
    parts = [part_front(pl, hdr, src_fps)]
    if pl.get("stab") and pl.get("trf"):
        parts.append(vid_motion.stab_transform_filter(pl["stab"], pl["trf"]))
    if pl["zoom"] > 0:
        rate = pl["zoom"] / max(n, 1)
        z = f"1.0+{rate:.6f}*n"
        parts.append(f"scale=w='trunc({W}*({z})/2)*2':h='trunc({H}*({z})/2)*2':eval=frame:flags=lanczos")
        parts.append(f"crop={W}:{H}")
        parts.append("setsar=1")                       # the zoom scale rounds to even sizes and leaves SAR 481:480; concat needs 1:1
    corr = pl.get("correct")
    if pl.get("look") or (corr and not pl["grade"]):
        parts += vid_color.grade_chain(pl.get("look"), corr, pl.get("lut_dir") or tempfile_dir())
    elif pl["grade"]:
        if corr:
            parts += ["format=gbrp16le", vid_color.curves_filter(corr)]
        parts.append(pl["grade"])
    if pl.get("split"):
        a, b = pl["split"]
        rgb = "gbrp" if (pl.get("pix_fmt") or "yuv420p") == "yuv420p" else "gbrp16le"
        parts.append(f"format={rgb},rgbashift=rh=-12:bh=12:rv=4:bv=-4:enable='between(n,{a},{b})'")
    parts.append(f"format={pl.get('pix_fmt') or 'yuv420p'}")
    parts.append("tpad=stop_mode=clone:stop=-1")
    return ",".join(x for x in parts if x)


def tempfile_dir() -> str:
    import tempfile
    d = Path(tempfile.gettempdir()) / "mhvid_luts"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def shot_window(pl: dict, info: dict) -> dict:
    """Where a shot reads its source. Default start = 40% into the clip (the frame the contact sheet showed), minus a margin,
    pulled back so the shot fits; an explicit src_start wins. `start` includes the transition head pad (what the video part
    decodes), `start0` is the source time of the cut itself (what the sound must use)."""
    dur = info["duration"]
    fps = pl["fps"]
    speed = pl.get("speed")
    out_s = pl["n"] / fps
    src_s = vid_motion.source_seconds(speed, out_s)
    st0 = float(pl["src_start"]) if pl.get("src_start") is not None else dur * 0.4 - 0.15
    st0 = min(st0, dur - src_s - 0.2)
    first_speed = speed["from"] if speed else 1.0
    start = st0 - pl["head"] / fps * first_speed
    return {"start": max(0.0, start), "start0": max(0.0, st0 + 0.0), "src_seconds": src_s, "out_seconds": out_s}


def source_start(pl: dict, dur: float) -> float:
    return shot_window(pl, {"duration": dur})["start"]


def wav_complete(path: Path) -> bool:
    """True when the data chunk of a RIFF/WAVE file is entirely present (the header of a truncated WAV still claims the full length)."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            head = fh.read(12)
            if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
                return False
            pos = 12
            while pos + 8 <= size:
                fh.seek(pos)
                chunk_id, chunk_len = struct.unpack("<4sI", fh.read(8))
                if chunk_id == b"data":
                    return pos + 8 + chunk_len <= size
                pos += 8 + chunk_len + (chunk_len & 1)
    except (OSError, struct.error):
        return False
    return False


def cached_media_ok(path: Path, seconds: float, tolerance: float, video: bool = True) -> bool:
    """A cached part or audio master is reused only if it opens and has the expected length: a file cut short by a crash
    or a full disk can still be larger than MIN_PART_BYTES."""
    try:
        if path.stat().st_size <= MIN_PART_BYTES:
            return False
        if path.suffix.lower() == ".wav" and not wav_complete(path):
            return False
        info = vl.probe(path)
    except (OSError, vl.EngineError, ValueError):
        return False
    if video and not info.get("has_video"):
        return False
    if abs((info.get("duration") or 0.0) - seconds) > tolerance:
        return False
    if path.suffix.lower() == ".mkv":          # Matroska writes its duration up front: a cut-off file still claims all of it
        return mkv_frames(path) / (info.get("fps") or 30.0) >= seconds - tolerance
    return True


def mkv_frames(path: Path) -> int:
    """Video packets really present in a Matroska file (read, not decoded; FFV1 parts are one frame per packet)."""
    r = vl.run([vl.find_ffmpeg()["ffprobe"], "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                "stream=nb_read_packets", "-of", "csv=p=0", vl.ffp(path)], check=False)
    try:
        return int((r.stdout or "").strip().rstrip(",") or 0)
    except ValueError:
        return 0


def _cache_key(cmd: list[str], clip: str) -> str:
    stt = os.stat(clip)
    blob = json.dumps([cmd, stt.st_size, int(stt.st_mtime)], sort_keys=True).encode()
    return hashlib.sha1(blob).hexdigest()[:16]


def render_part(edl: dict, j: int, bounds: list[int], trans: dict[int, str], scale: float, workdir: Path,
                use_cache: bool = True, hwdecode: str = "none", fmt: str | None = None, quality: str | None = None) -> dict:
    lut_dir = workdir.parent / "luts"
    lut_dir.mkdir(parents=True, exist_ok=True)
    pl = part_plan(edl, j, bounds, trans, scale, fmt, lut_dir)
    info = vl.probe(pl["clip"])
    if not info.get("has_video"):
        raise vl.EngineError(f"shot {j}: no video stream in {pl['clip']}")
    win = shot_window(pl, info)
    st = win["start"]
    hdr = bool(info.get("hdr"))
    if pl.get("focus") == "auto":                      # Track E: keep the subject in frame when reframing
        import vid_editorial
        frames = vid_editorial.gray_frames(pl["clip"], win["start0"], min(max(win["out_seconds"], 0.3), 2.0), hdr=hdr)
        pl["focus"] = list(vid_editorial.saliency_centroid(frames)) if frames else None
    src_fps = info.get("fps") or 30.0
    hw = hwdecode_args(hwdecode)
    ff = vl.find_ffmpeg()["ffmpeg"]
    if pl["stab"]:
        stat = os.stat(pl["clip"])
        front = part_front(pl, hdr, src_fps)
        tag = hashlib.sha1(json.dumps([front, pl["stab"], round(st, 3), pl["n"], stat.st_size, int(stat.st_mtime)],
                                      sort_keys=True).encode()).hexdigest()[:12]
        pl["trf"] = str(workdir / f"{j:03d}_{tag}.trf")
    enc = part_encoding(quality)
    pl["pix_fmt"] = enc["pix_fmt"]
    vf = part_vf(pl, hdr, src_fps)
    cmd = [ff, "-v", "error", "-y"] + hw + ["-ss", f"{st:.3f}", "-i", pl["clip"], "-an",
           "-frames:v", str(pl["n"]), "-vf", vf] + enc["args"] + ["-r", str(pl["fps"]), "-pix_fmt", enc["pix_fmt"]]
    key = _cache_key([c for c in cmd[1:] if c not in hw], pl["clip"])      # same cache entry with or without GPU decode
    out = workdir / f"{j:03d}_{key}{enc['ext']}"
    if use_cache and out.is_file() and cached_media_ok(out, pl["n"] / pl["fps"], 3.0 / pl["fps"] + 0.05):
        try:
            os.utime(out)                      # "used recently" for vid_deliver.gc_cache, which goes by modification time
        except OSError:
            pass
        return {"j": j, "path": out, "n": pl["n"], "cached": True}
    for stale in workdir.glob(f"{j:03d}_*"):
        if stale.suffix.lower() in PART_EXTS:
            stale.unlink(missing_ok=True)
    if pl["stab"] and not Path(pl["trf"]).is_file():
        det = [ff, "-v", "error", "-y"] + hw + ["-ss", f"{st:.3f}", "-i", pl["clip"], "-an", "-frames:v", str(pl["n"]),
                                                "-vf", f"{part_front(pl, hdr, src_fps)},{vid_motion.stab_detect_filter(pl['stab'], pl['trf'])}",
                                                "-f", "null", "-"]
        r0 = vl.run(det, check=False)
        if (r0.returncode != 0 or not Path(pl["trf"]).is_file()) and hw:
            r0 = vl.run([c for c in det if c not in hw], check=False)
        if r0.returncode != 0 or not Path(pl["trf"]).is_file():
            raise vl.EngineError(f"shot {j}: stabilisation analysis failed: {(r0.stderr or '')[-300:]}")
    tmp = out.with_suffix(".tmp" + enc["ext"])
    r = vl.run(cmd + [tmp], check=False)
    if (r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size < MIN_PART_BYTES) and hw:
        sw = [c for c in cmd if c not in hw]                                 # GPU decode failed: retry in software
        r = vl.run(sw + [tmp], check=False)
    if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size < MIN_PART_BYTES:
        raise vl.EngineError(f"shot {j} ({Path(pl['clip']).name}) failed to render (exit {r.returncode}): "
                             f"{(r.stderr or '')[-400:]}")
    tmp.replace(out)
    return {"j": j, "path": out, "n": pl["n"], "cached": False}


# --------------------------------------------------------------------------- assembly

def audio_lines(edl: dict, music_idx: int, plan: dict | None = None) -> list[str]:
    """Filter lines that end in [mix]: music with fades + synthesised riser/boom (+ whooshes, ducking, ambient when `plan`
    carries them). No loudness work here: the master pass does that on the finished mix."""
    p = ve.project(edl)
    style = ve.merged_style(edl)
    dur = p["duration"]
    mfi, mfo = p["music_fade_in"], p["music_fade_out"]
    # afade, like fade, reads d=0 as its default length (one second of samples): a 0 s music fade is left out
    mf = "".join(f + "," for f in ((f"afade=t=in:st=0:d={mfi}" if mfi > 0 else ""),
                                  (f"afade=t=out:st={max(0, dur - mfo - 0.1):.3f}:d={mfo}" if mfo > 0 else "")) if f)
    lines = [f"[{music_idx}:a]{mf}aformat=sample_rates=44100:channel_layouts=stereo[mus]"]
    mus = "[mus]"
    plan = plan or {}
    if plan.get("whoosh_lines"):
        lines += plan["whoosh_lines"]
        if plan.get("duck"):
            # the music dips under every whoosh (sidechain), so effects never mask the beat
            # the key must last as long as the music: sidechaincompress stops at the end of its SHORTEST input
            lines.append(f"[{plan['whoosh_label']}]asplit=2[sfxk0][sfxmix]")
            lines.append(f"[sfxk0]apad=whole_dur={dur:.3f}[sfxkey]")
            lines.append(f"[mus][sfxkey]sidechaincompress=threshold={plan.get('duck_threshold', 0.002):.5f}:ratio=4:attack=5:release=200:makeup=1:detection=peak[musd]")
            mus = "[musd]"
            sfx_out = "[sfxmix]"
        else:
            sfx_out = f"[{plan['whoosh_label']}]"
    else:
        sfx_out = None
    mix = [mus]
    if sfx_out:
        mix.append(sfx_out)
    if plan.get("ambient_lines"):
        lines += plan["ambient_lines"]
        mix.append(f"[{plan['ambient_label']}]")
    drop_t = None
    if (edl.get("beats") or {}).get("drop_beat") is not None:
        drop_t = ve.grid_time(edl, edl["beats"]["drop_beat"])
    if drop_t is not None and drop_t > 1.0:
        if style["riser"]:
            rl = min(3.4, drop_t)
            delay = int(max(0.0, drop_t - rl) * 1000)
            lines.append(f"anoisesrc=d={rl:.2f}:c=white:r=44100:a=0.4,highpass=f=1500,afade=t=in:st=0:d={rl - 0.2:.2f}:curve=exp,"
                         f"afade=t=out:st={rl - 0.2:.2f}:d=0.2,volume=0.45,aformat=channel_layouts=stereo,adelay={delay}|{delay}[riser]")
            mix.append("[riser]")
        if style["boom"]:
            delay = int(drop_t * 1000)
            lines.append(f"sine=f=50:d=0.9:r=44100,afade=t=out:st=0:d=0.9:curve=qua,volume=1.4,aformat=channel_layouts=stereo,"
                         f"adelay={delay}|{delay}[boom]")
            mix.append("[boom]")
    lines.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0[mix]")
    return lines


def audio_plan(edl: dict, bounds: list[int], trans: dict[int, str], work: Path, log=vl.say) -> dict:
    """Everything the audio graph needs besides the music: whoosh events, ambient segments (with measured gains),
    the extra input arguments and a JSON-able description used in the cache key."""
    p = ve.project(edl)
    fps = p["fps"]
    plan: dict = {"inputs": [], "key": {}}
    sd = vid_sfx.sound_design(edl)
    if sd and sd["whoosh"]:
        events = vid_sfx.whoosh_events(trans, bounds, fps, sd)
        lines, label = vid_sfx.whoosh_graph(events, sd["level_db"])
        if lines:
            # peak detection, threshold following the whoosh level (a -24 dB whoosh never crosses a fixed one): about 4 dB of ducking at any level
            plan.update(whoosh_lines=lines, whoosh_label=label, duck=bool(sd["duck"]),
                        duck_threshold=max(0.001, 0.025 * 10 ** (sd["level_db"] / 20.0)))
        plan["key"]["whoosh"] = [(round(e["t"], 3), e["kind"]) for e in events] + [sd["level_db"], sd["duck"]]
    amb = vid_sfx.ambient_config(edl)
    if amb:
        windows = []
        for j, shot in enumerate(edl["shots"]):
            pl = part_plan(edl, j, bounds, trans, 1.0)
            info = vl.probe(pl["clip"])
            win = shot_window(pl, info)
            nominal = (bounds[j + 1] - bounds[j]) / fps
            windows.append({"j": j, "clip": pl["clip"], "start0": win["start0"],
                            "seconds": vid_motion.source_seconds(pl["speed"], nominal), "out_start": bounds[j] / fps,
                            "out_seconds": nominal, "speed": pl["speed"], "has_audio": bool(info.get("has_audio"))})
        music = p.get("music")
        music_mean = vid_sfx.mean_volume(music, 0.0, p["duration"]) if music else None
        segs = vid_sfx.ambient_segments(edl, windows, music_mean)
        if segs:
            lines, label = vid_sfx.ambient_lines(segs, 1, amb)
            plan.update(ambient_lines=lines, ambient_label=label)
            for sg in segs:
                plan["inputs"] += ["-ss", f"{sg['start']:.3f}", "-t", f"{sg['seconds'] + 0.1:.3f}", "-i", sg["clip"]]
            plan["key"]["ambient"] = [(sg["j"], sg["start"], sg["seconds"], sg["gain_db"], sg["tempo"]) for sg in segs] + [amb["fade_ms"], amb["highpass_hz"]]
            if any("lead" in sg for sg in segs):                      # only then: older projects keep their cache key
                plan["key"]["ambient_jl"] = [(sg["j"], sg["delay"], sg["out_seconds"]) for sg in segs]
            log(f"ambient sound from {len(segs)} of {len(edl['shots'])} shots (music {music_mean if music_mean is not None else '?'} dB, "
                f"bed {amb['level_db']} dB below)")
    return plan


TRUE_PEAK_TARGET = -2.0        # leaves headroom for the AAC encoder, which overshoots by a few tenths of a dB


def _loudnorm_json(text: str) -> dict:
    blocks = re.findall(r"\{[^{}]*\}", text, re.S)
    if not blocks:
        raise vl.EngineError("loudness measurement produced no result:\n" + text[-300:])
    return json.loads(blocks[-1])


def master_audio(edl: dict, work: Path, log=vl.say, bounds: list[int] | None = None,
                 trans: dict[int, str] | None = None) -> Path | None:
    """Mix music + synthesised sounds (+ whooshes, ambient) and set the loudness of the FINAL mix in two passes
    (measure, then a linear gain). A one-pass loudnorm missed the target by 3 LU and let the true peak reach +0.1 dBFS."""
    p = ve.project(edl)
    style = ve.merged_style(edl)
    music = p.get("music")
    if not music:
        return None
    bounds = bounds or ve.timeline(edl)
    trans = trans if trans is not None else ve.effective_transitions(edl, bounds)
    plan = audio_plan(edl, bounds, trans, work, log)
    st = os.stat(music)
    key = hashlib.sha1(json.dumps([vl.ffp(music), st.st_size, int(st.st_mtime), p["duration"], p["music_fade_in"],
                                   p["music_fade_out"], style["riser"], style["boom"], style["loudness"],
                                   (edl.get("beats") or {}).get("drop_beat"), (edl.get("beats") or {}).get("t0"),
                                   (edl.get("beats") or {}).get("period"), TRUE_PEAK_TARGET, plan["key"]],
                                  sort_keys=True, default=str).encode()).hexdigest()[:16]
    out = work / f"master_{key}.wav"
    expected = min(p["duration"], vl.probe(music)["duration"] or p["duration"])          # a short music file gives a short mix
    if out.is_file() and cached_media_ok(out, expected, 0.3, video=False):
        return out
    ff = vl.find_ffmpeg()["ffmpeg"]
    mix = work / f"mix_{key}.wav"
    graph_file = work / f"audio_{key}.txt"
    graph_file.write_text(";\n".join(audio_lines(edl, 0, plan)), encoding="utf-8")
    vl.run([ff, "-v", "error", "-y", "-i", vl.ffp(music)] + plan["inputs"] +
           [vl.filter_script_flag(), vl.ffp(graph_file), "-map", "[mix]", "-t", f"{p['duration']:.3f}", "-c:a", "pcm_f32le", vl.ffp(mix)])
    target = f"I={style['loudness']}:TP={TRUE_PEAK_TARGET}:LRA=11"
    r = vl.run([ff, "-hide_banner", "-nostats", "-v", "info", "-i", vl.ffp(mix), "-af", f"loudnorm={target}:print_format=json",
                "-f", "null", "-"])
    m = _loudnorm_json(r.stderr)
    measured = float(m["input_i"])
    if measured < -60:                                       # silent mix: nothing to normalise
        mix.replace(out)
        return out
    flt = (f"loudnorm={target}:measured_I={m['input_i']}:measured_LRA={m['input_lra']}:measured_TP={m['input_tp']}:"
           f"measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true,"
           f"alimiter=limit=0.84:attack=3:release=40:level=disabled,aformat=sample_rates=44100:channel_layouts=stereo")
    vl.run([ff, "-v", "error", "-y", "-i", vl.ffp(mix), "-af", flt, "-c:a", "pcm_f32le", vl.ffp(out)])
    mix.unlink(missing_ok=True)
    log(f"audio mastered: {measured:.1f} LUFS -> {style['loudness']} LUFS")
    return out


def build_graph(edl: dict, parts: list[dict], bounds: list[int], trans: dict[int, str], scale: float,
                audio_master: str | None = None, size: tuple[int, int] | None = None,
                text_filter: str | None = None, precise: bool = False, master: bool = False) -> tuple[list[str], list[str]]:
    """Return (filter lines, extra input args). Inputs: parts 0..N-1, then music (or the mastered wav), then optional flare source.
    `precise`: the parts are 10-bit, the picture stays in 16-bit RGB and is dithered once to 8 bits. `master`: [vout] is the
    same picture in 10-bit 4:2:0, for the HEVC master."""
    p = ve.project(edl)
    style = ve.merged_style(edl)
    fps = p["fps"]
    N = len(parts)
    lens = [x["n"] for x in parts]
    lines = [f"[{j}:v]settb=AVTB,setpts=PTS-STARTPTS,fps={fps}[p{j}]" for j in range(N)]
    cur, curlen = "p0", lens[0]
    ovs = ve.overlap_frames(edl, trans)
    for j in range(1, N):
        out = f"x{j}"
        if j in trans:
            ov = ovs[j]
            off = (curlen - ov) / fps
            lines.append(f"[{cur}][p{j}]xfade=transition={trans[j]}:duration={ov / fps:.4f}:offset={off:.4f}[{out}]")
            curlen += lens[j] - ov
        else:
            lines.append(f"[{cur}][p{j}]concat=n=2:v=1:a=0,settb=AVTB,fps={fps}[{out}]")
            curlen += lens[j]
        cur = out
    total = vl.frames(p["duration"], fps)
    if curlen != total:
        raise vl.EngineError(f"internal timing error: assembled {curlen} frames, expected {total}")
    music = p.get("music")
    nin = N
    music_idx = None
    if music:
        music_idx = nin
        nin += 1
    flare_idx = None
    flare = style["end_flare"] and len(parts) >= 3
    if precise:
        lines.append(f"[{cur}]{YUV_TO_RGB if flare else TO_YUV16}[v0]")
    else:
        lines.append(f"[{cur}]format=gbrp[v0]")
    cur = "v0"
    if flare:
        flare_idx = nin
        nin += 1
        ft = style.get("flare_time")
        if ft is None:
            ft = bounds[-2] / fps + 0.3
        # the source fades up from black to a peak at `ft` and back to black: screen-blending black changes nothing,
        # so the flare only shows inside its window, using FFmpeg's fast built-in blend (an expression blend was 10x slower)
        lines.append(f"[{flare_idx}:v]format=gbrp,fade=t=in:st={max(0.0, ft - 0.6):.3f}:d=0.6,fade=t=out:st={ft:.3f}:d=0.6"
                     f"{',format=' + FLARE_RGB if precise else ''}[leak]")
        lines.append(f"[{cur}][leak]blend=all_mode=screen:all_opacity=0.6{',' + RGB_TO_YUV16 if precise else ''}[v1]")
        cur = "v1"
    fi, fo = p["fade_in"], p["fade_out"]
    dur = p["duration"]
    deep = precise
    if text_filter:                                  # text sits under the final fades, so it fades with the picture
        lines.append(f"[{cur}]{FINAL_DITHER if precise else ''}format=yuv420p,{text_filter}[vtxt]")
        cur = "vtxt"
        deep = False                                 # the subtitle renderer draws on 8-bit frames: dithered before it, once
    # FFmpeg's fade reads d=0 as "the default 25 frames", so a fade of 0 seconds is left out rather than written as d=0
    fades = ",".join(f for f in ((f"fade=t=in:st=0:d={fi}" if fi > 0 else ""),
                                 (f"fade=t=out:st={dur - fo:.3f}:d={fo}" if fo > 0 else "")) if f) or "null"
    if master and deep:
        lines.append(f"[{cur}]{fades},{FINAL_DITHER}format={MASTER_PIX_FMT}[vout]")
    elif master:
        lines.append(f"[{cur}]{fades},format={MASTER_PIX_FMT}[vout]")
    elif deep:
        lines.append(f"[{cur}]{fades},{FINAL_DITHER}format=yuv420p[vout]")
    else:
        lines.append(f"[{cur}]{fades},format=yuv420p[vout]")
    if music:
        if audio_master:
            lines.append(f"[{music_idx}:a]anull[aout]")                       # already mixed and mastered (master_audio)
        else:
            lines += audio_lines(edl, music_idx)
            lines.append("[mix]alimiter=limit=0.84[aout]")
    extra = []
    if music:
        extra += ["-i", audio_master or music]
    if flare_idx is not None:
        sw, sh = size or (vl.even(p["width"] * scale), vl.even(p["height"] * scale))
        extra += ["-f", "lavfi", "-i", f"gradients=s={sw}x{sh}:c0=0xff9a3c:c1=0x1a0800:x0={sw}:y0=0:x1={int(sw * 0.23)}:"
                  f"y1={int(sh * 0.68)}:r={fps}:d={dur + 0.5:.2f}"]
    return lines, extra


def resolve_music(edl: dict, work: Path) -> str | None:
    """Return a single music file path for the render. `project.music` may be a list of user tracks (phase 10 music
    sections): they are concatenated, equal-loudness-formatted, into one cached wav so the rest of the audio pipeline
    (fades, ducking, two-pass master) is unchanged."""
    p = ve.project(edl)
    music = p.get("music")
    if not music or isinstance(music, str):
        return music if music else None
    files = [m for m in music if m]
    if not files:
        return None
    if len(files) == 1:
        return files[0]
    for f in files:
        if not os.path.isfile(f):
            raise vl.EngineError(f"music track not found: {f}")
    stats = [(vl.ffp(f), os.stat(f).st_size, int(os.stat(f).st_mtime)) for f in files]
    key = hashlib.sha1(json.dumps(stats, sort_keys=True).encode()).hexdigest()[:16]
    work.mkdir(parents=True, exist_ok=True)
    out = work / f"music_{key}.wav"
    if out.is_file() and cached_media_ok(out, 0.0, 1e9, video=False):
        return str(out)
    ff = vl.find_ffmpeg()["ffmpeg"]
    cmd = [ff, "-v", "error", "-y"]
    for f in files:
        cmd += ["-i", vl.ffp(f)]
    fmt = "".join(f"[{i}:a]aformat=sample_rates=44100:channel_layouts=stereo[a{i}];" for i in range(len(files)))
    fmt += "".join(f"[a{i}]" for i in range(len(files))) + f"concat=n={len(files)}:v=0:a=1[a]"
    cmd += ["-filter_complex", fmt, "-map", "[a]", "-c:a", "pcm_s16le", vl.ffp(out)]
    vl.run(cmd)
    return str(out)


def render(edl_path: str | os.PathLike, out: str | os.PathLike | None = None, preview: bool = False,
           encoder: str = "auto", workdir: str | os.PathLike | None = None, use_cache: bool = True,
           force: bool = False, workers: int | None = None, log=vl.say, hwdecode: str = "auto",
           qa: bool = False, fmt: str | None = None, delivery: str | None = None, target_size_mb: float | None = None,
           bitrate: int | None = None, manifest: bool = False, bundle: bool = False,
           json_out: str | os.PathLike | None = None, quality: str | None = None, master: bool = False) -> dict:
    t_start = time.time()
    edl = ve.load(edl_path)
    issues = ve.lint(edl)
    broken = ve.structure_errors(edl)
    if broken:                                                    # --force cannot render what cannot even be read
        raise vl.EngineError("EDL is malformed (--force does not apply):\n" + ve.format_issues(broken))
    if ve.has_errors(issues) and not force:
        raise vl.EngineError("EDL has errors; fix them first:\n" + ve.format_issues(issues))
    if issues:
        log(ve.format_issues(issues))
    deliv = None
    if delivery:
        deliv = vid_deliver.resolve_delivery(delivery)
        edl.setdefault("style", {})["loudness"] = deliv["loudness"]        # platform loudness target for the master
        if fmt is None:
            native = vl.load_preset(deliv["format"])
            pj = ve.project(edl)
            if (native["width"], native["height"]) != (pj["width"], pj["height"]):
                fmt = deliv["format"]
    p = ve.project(edl)
    scale = 0.5 if preview else 1.0
    quality = "standard" if preview else (quality or p.get("quality") or "high")       # previews stay light; finals are high
    qs = quality_settings(quality)
    precise = precise_parts(quality, preview)
    if master and preview:
        raise vl.EngineError("--master is a final render (it keeps the full picture); leave out --preview")
    master_args = master_encoder_args(quality) if master else None          # fail before the parts are rendered
    base = Path(edl_path).resolve().parent
    work = Path(workdir) if workdir else base / (Path(edl_path).stem + "_work")
    music_path = resolve_music(edl, work)
    if isinstance((edl.get("project") or {}).get("music"), list):
        edl["project"]["music"] = music_path                              # the rest of the pipeline wants one file
        p = ve.project(edl)
    tag = format_tag(fmt)
    pdir = work / ("parts_preview" if preview else "parts") / (tag or "main")
    pdir.mkdir(parents=True, exist_ok=True)
    bounds = ve.timeline(edl)
    trans = ve.effective_transitions(edl, bounds)
    n = len(edl["shots"])
    size = output_size(edl, fmt, scale)
    log(f"rendering {n} shots ({'preview' if preview else 'final'}{', ' + fmt if fmt else ''}, {size[0]}x{size[1]}) ...")
    t_parts = time.time()
    done = 0
    with ThreadPoolExecutor(workers or vl.cpu_workers(4)) as ex:
        futs = {ex.submit(render_part, edl, j, bounds, trans, scale, pdir, use_cache, hwdecode, fmt, quality): j for j in range(n)}
        parts_by_j: dict[int, dict] = {}
        for fut in as_completed(futs):
            parts_by_j[futs[fut]] = fut.result()
            done += 1
            if n >= 8 and done % max(1, n // 4) == 0:
                log(f"  parts {done}/{n}")
        parts = [parts_by_j[j] for j in range(n)]
    cached = sum(1 for x in parts if x["cached"])
    parts_seconds = round(time.time() - t_parts, 1)
    mastered = master_audio(edl, work, log, bounds, trans)
    text_filter = None
    if edl.get("texts"):
        ass, fonts = vid_text.build_ass(edl, size[0], size[1], work / "text" / (tag or "main"))
        text_filter = vid_text.text_filter(ass, fonts)
    audio_wav = vl.ffp(mastered) if mastered else None
    lines, extra = build_graph(edl, parts, bounds, trans, scale, audio_master=audio_wav, size=size,
                               text_filter=text_filter, precise=precise)
    graph_file = work / (("graph_preview" if preview else "graph") + (f"_{tag}" if tag else "") + ".txt")
    graph_file.write_text(";\n".join(lines), encoding="utf-8")
    video_kbps = None
    if bitrate:
        video_kbps = int(bitrate)
    elif target_size_mb:
        video_kbps = vid_deliver.video_kbps_for_size(float(target_size_mb), p["duration"], vid_deliver.AUDIO_KBPS)
    maxrate = int(deliv["max_mbps"] * 1000) if (deliv and video_kbps) else None
    out_path = Path(out) if out else base / f"{p.get('name', 'video')}{'_' + tag if tag else ''}{'_preview' if preview else ''}.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def assemble(enc_args: list[str], target: str | os.PathLike, map_audio: bool, to_null: bool = False,
                 graph: Path | None = None, inputs: list | None = None) -> "object":
        cmd = [vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y"]
        for x in parts:
            cmd += ["-i", vl.ffp(x["path"])]
        cmd += (extra if inputs is None else inputs) + [vl.filter_script_flag(), vl.ffp(graph or graph_file), "-map", "[vout]"]
        if map_audio and p.get("music"):
            cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", f"{vid_deliver.AUDIO_KBPS if video_kbps else qs['audio_kbps']}k"]
        elif not map_audio:
            cmd += ["-an"]
        pix = [] if "-pix_fmt" in enc_args else ["-pix_fmt", vid_deliver.PIX_FMT]
        cmd += enc_args + pix + ["-r", str(p["fps"]), "-t", f"{p['duration']:.3f}"]
        if to_null:
            cmd += ["-f", "null", "-"]
        else:
            cmd += ["-movflags", "+faststart", vl.ffp(target)]
        return vl.run(cmd, check=False)

    log("assembling ...")
    t_asm = time.time()
    two_pass = bool(video_kbps) and _resolve_encoder(encoder) == "libx264" and not preview
    if two_pass:
        passlog = str(work / f"pass_{tag or 'main'}")
        enc1, _ = encoder_args(encoder, preview, video_kbps, maxrate, two_pass=1, passlog=passlog, quality=quality)
        r = assemble(enc1, out_path, map_audio=True, to_null=True)
        if r.returncode != 0:
            raise vl.EngineError(f"assembly (pass 1) failed (exit {r.returncode}):\n{(r.stderr or '')[-1200:]}")
        enc, enc_name = encoder_args(encoder, preview, video_kbps, maxrate, two_pass=2, passlog=passlog, quality=quality)
        r = assemble(enc, out_path, map_audio=True)
    else:
        enc, enc_name = encoder_args(encoder, preview, video_kbps, maxrate, quality=quality)
        r = assemble(enc, out_path, map_audio=True)
    assemble_seconds = round(time.time() - t_asm, 1)
    if r.returncode != 0:
        raise vl.EngineError(f"assembly failed (exit {r.returncode}):\n{(r.stderr or '')[-1200:]}")
    master_out = None
    if master_args:
        log(f"writing the 10-bit HEVC master ({master_args[1]}) ...")
        mlines, mextra = build_graph(edl, parts, bounds, trans, scale, audio_master=audio_wav, size=size,
                                     text_filter=text_filter, precise=precise, master=True)
        mgraph = work / (f"graph_master{'_' + tag if tag else ''}.txt")
        mgraph.write_text(";\n".join(mlines), encoding="utf-8")
        master_out = out_path.with_name(out_path.stem + "_master.mp4")
        rm = assemble(master_args[0], master_out, map_audio=True, graph=mgraph, inputs=mextra)
        if rm.returncode != 0:
            raise vl.EngineError(f"the HEVC master failed (exit {rm.returncode}):\n{(rm.stderr or '')[-1200:]}")
    qa_report = None
    if qa:
        import vid_qa
        qa_report = vid_qa.run_qa(out_path, edl_path, sheet=str(Path(out_path).with_suffix("")) + "_check.jpg", fmt=fmt)
        vid_qa.save_report(qa_report, str(Path(out_path).with_suffix("")) + ".qa.json")
        log(vid_qa.format_report(qa_report))
    info = vl.probe(out_path)
    result = {"out": str(out_path), "shots": n, "qa": qa_report["verdict"] if qa_report else None, "parts_cached": cached, "encoder": enc_name,
              "frames": vl.frames(p["duration"], p["fps"]), "seconds": round(time.time() - t_start, 1),
              "parts_seconds": parts_seconds, "assemble_seconds": assemble_seconds,
              "preview": preview, "quality": quality, "workdir": str(work), "format": fmt, "size": list(size), "size_mb": round((info.get("size") or 0) / 1e6, 2),
              "parts": (("10-bit lossless" if "ffv1" in part_encoding(quality)["args"] else "10-bit") if precise else "8-bit"), "parts_mb": round(sum(Path(x["path"]).stat().st_size for x in parts) / 1e6, 1)}
    if master_out:
        result["master"] = str(master_out)
        result["master_mb"] = round(master_out.stat().st_size / 1e6, 2)
    if delivery or target_size_mb:
        result["delivery_check"] = vid_deliver.verify_delivery(info, float(target_size_mb) if target_size_mb else None, delivery)
    if manifest:
        result["manifest"] = vid_deliver.write_manifest(edl_path, edl, out_path, result)
    if bundle:
        result["bundle"] = vid_deliver.build_bundle(edl_path, workdir=work)
    if json_out:
        vl.save_json(json_out, result)
        result["json"] = str(json_out)
    return result


STANDARD_FORMATS = ("reels-9x16", "youtube-16x9", "square-1x1")


def render_formats(edl_path: str | os.PathLike, formats: list[str] | None = None, **kw) -> list[dict]:
    """Render the same EDL in several output formats (default: the three standard presets). The project's own size is
    rendered as it is; other sizes reframe with blur-fill unless the EDL says otherwise. Parts are cached per size."""
    edl = ve.load(edl_path)
    p = ve.project(edl)
    out = []
    for f in (formats or list(STANDARD_FORMATS)):
        pre = vl.load_preset(f)
        native = (pre["width"], pre["height"]) == (p["width"], p["height"])
        res = render(edl_path, fmt=None if native else f, **kw)
        res["format_name"] = f
        out.append(res)
    return out


# --------------------------------------------------------------------------- basic verification

def verify(edl_path: str | os.PathLike, video: str | os.PathLike, sheet: str | os.PathLike | None = None) -> dict:
    """Phase-1 verification: stream layout, duration, frame count, size, plus a contact sheet to LOOK at.
    (Automated visual QA - black/white frames, colour cast, loudness, effect audit - arrives in phase 3.)"""
    edl = ve.load(edl_path)
    p = ve.project(edl)
    info = vl.probe(video)
    checks = []

    def chk(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    full = p["width"] * p["height"]
    got = info.get("width", 0) * info.get("height", 0)
    chk("has video", info.get("has_video"), "")
    chk("duration", abs(info["duration"] - p["duration"]) <= 0.06, f"{info['duration']:.3f}s vs expected {p['duration']:.3f}s")
    ratio_ok = abs((info.get("width", 1) / max(info.get("height", 1), 1)) - (p["width"] / p["height"])) < 0.01
    chk("aspect ratio", ratio_ok, f"{info.get('width')}x{info.get('height')} vs {p['width']}x{p['height']}")
    chk("audio", bool(info.get("has_audio")) == bool(p.get("music")), f"has_audio={info.get('has_audio')} music={bool(p.get('music'))}")
    chk("fps", abs((info.get("fps") or 0) - p["fps"]) < 0.01, f"{info.get('fps')}")
    chk("file size sane", info["size"] < 400 * 1024 * 1024, f"{info['size'] / 1e6:.1f} MB")
    res = {"ok": all(c["ok"] for c in checks), "checks": checks, "info": info}
    if sheet:
        res["sheet"] = vid_sheets.video_sheet(video, sheet)
    return res
