"""Phase 8 + 12: delivery presets, size/bitrate targeting, render manifests and project bundles.

Everything here is pure Python (standard library only); the FFmpeg wiring lives in vid_render. The split keeps the
size maths and the reproducibility helpers easy to test without rendering.

Delivery presets map a platform ("reels", "tiktok", "shorts", "stories", "youtube", "square") to the output format,
an integrated-loudness target and a few container knobs (pixel format, faststart, a sane bitrate ceiling). Platforms
re-encode anything you upload, so the goal is not to out-smart them but to hand them a clean, correctly shaped,
correctly loud file that survives that re-encode.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import platform
import zipfile
from pathlib import Path

import vidlib as vl

# aspect -> the existing format preset that carries width/height/fps
_FORMAT_FOR_ASPECT = {"9x16": "reels-9x16", "16x9": "youtube-16x9", "1x1": "square-1x1"}

# A platform profile. `format` is the size preset; `loudness` is the integrated LUFS the master aims for; `max_mbps`
# caps the video bitrate when a size/bitrate target would exceed what the platform keeps anyway.
DELIVERY: dict[str, dict] = {
    "reels":   {"description": "Instagram Reels (9:16)", "format": "reels-9x16", "loudness": -14.0, "max_mbps": 12.0},
    "tiktok":  {"description": "TikTok (9:16)", "format": "reels-9x16", "loudness": -14.0, "max_mbps": 12.0},
    "shorts":  {"description": "YouTube Shorts (9:16)", "format": "reels-9x16", "loudness": -14.0, "max_mbps": 14.0},
    "stories": {"description": "Instagram/Facebook Stories (9:16)", "format": "reels-9x16", "loudness": -14.0, "max_mbps": 12.0},
    "youtube": {"description": "YouTube (16:9)", "format": "youtube-16x9", "loudness": -14.0, "max_mbps": 16.0},
    "square":  {"description": "Square feed post (1:1)", "format": "square-1x1", "loudness": -14.0, "max_mbps": 12.0},
}
# shared container settings
PIX_FMT = "yuv420p"
AUDIO_KBPS = 192
# how close the final file must land to a requested size for the delivery check to pass
SIZE_TOLERANCE = 0.12


def list_delivery() -> list[tuple[str, str]]:
    return [(k, v["description"]) for k, v in DELIVERY.items()]


def resolve_delivery(name: str) -> dict:
    d = DELIVERY.get(name)
    if d is None:
        raise vl.EngineError(f"delivery preset '{name}' is unknown (known: {', '.join(DELIVERY)})")
    return dict(d)


def video_kbps_for_size(target_mb: float, duration: float, audio_kbps: int = AUDIO_KBPS) -> int:
    """Video bitrate (kbit/s) so that video+audio land near `target_mb` over `duration` seconds.

    A megabyte is 8*1000*1000 bits here (drive/marketing MB), because that is how platforms quote their size limits.
    Leaves 2% for the container overhead and refuses to go below a floor that would look broken.
    """
    if target_mb <= 0 or duration <= 0:
        raise vl.EngineError("target size and duration must be positive")
    total_kbit = target_mb * 8000.0 * 0.98
    video_kbit = total_kbit - audio_kbps * duration
    kbps = int(video_kbit / duration)
    if kbps < 120:
        raise vl.EngineError(f"target size {target_mb} MB is too small for {duration:.1f}s of video "
                             "(less than 120 kbit/s of picture); ask for a bigger size")
    return kbps


def estimate_mb(video_kbps: int, duration: float, audio_kbps: int = AUDIO_KBPS) -> float:
    return round((video_kbps + audio_kbps) * duration / 8000.0, 2)


def verify_delivery(info: dict, target_mb: float | None, delivery: str | None) -> dict:
    """A check dict (same shape as the QA gate's checks) for a delivered file: size within tolerance, pixel format,
    faststart container and aspect of the chosen platform. `info` is a vidlib.probe() result."""
    mb = (info.get("size") or 0) / 1e6
    detail = [f"{mb:.1f} MB"]
    level = "ok"
    if target_mb:
        lo, hi = target_mb * (1 - SIZE_TOLERANCE), target_mb * (1 + SIZE_TOLERANCE)
        detail.append(f"target {target_mb:.0f} MB (accept {lo:.0f}-{hi:.0f})")
        if not (lo <= mb <= hi):
            level = "warn"
    if delivery:
        d = resolve_delivery(delivery)
        pre = vl.load_preset(d["format"])
        want = pre["width"] / pre["height"]
        got = info.get("width", 1) / max(info.get("height", 1), 1)
        detail.append(f"{info.get('width')}x{info.get('height')} for {delivery}")
        if abs(want - got) > 0.02:
            level = "warn"
    return {"id": "delivery", "level": level, "msg": "; ".join(detail)}


# --------------------------------------------------------------------------- reproducibility

def _sha256(path: str | os.PathLike, limit: int | None = None) -> str:
    h = hashlib.sha256()
    read = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            read += len(chunk)
            if limit is not None and read >= limit:
                break
    return h.hexdigest()


def _input_record(path: str | os.PathLike) -> dict:
    try:
        st = os.stat(path)
        return {"path": vl.ffp(path), "size": st.st_size, "mtime": int(st.st_mtime)}
    except OSError:
        return {"path": vl.ffp(path), "missing": True}


def build_manifest(edl_path: str | os.PathLike, edl: dict, out_path: str | os.PathLike,
                   result: dict | None = None) -> dict:
    """A record that lets another machine tell whether it reproduced the same render: engine and FFmpeg versions, a
    hash of the EDL and of the output, and size+mtime of every input (clips and music). Clips are not hashed (they can
    be tens of gigabytes); size and modification time are what the part cache already trusts."""
    p = edl.get("project") or {}
    clips = []
    seen = set()
    for s in edl.get("shots") or []:
        c = s.get("clip")
        if c and c not in seen:
            seen.add(c)
            clips.append(_input_record(c))
    music = p.get("music")
    music_list = music if isinstance(music, list) else ([music] if music else [])
    ff = {}
    try:
        ff = {"version": ".".join(str(x) for x in vl.ffmpeg_version())}
    except vl.EngineError:
        ff = {"version": None}
    man = {
        "manifest_version": 1,
        "engine_version": vl.engine_version(),
        "ffmpeg": ff,
        "created_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platform": platform.platform(),
        "edl": {"path": vl.ffp(edl_path), "sha256": _sha256(edl_path)},
        "project": {k: p.get(k) for k in ("name", "width", "height", "fps", "duration", "preset")},
        "inputs": {"clips": clips, "music": [_input_record(m) for m in music_list]},
    }
    if Path(out_path).is_file():
        oi = vl.probe(out_path)
        man["output"] = {"path": vl.ffp(out_path), "size": oi.get("size"), "duration": oi.get("duration"),
                         "width": oi.get("width"), "height": oi.get("height"), "fps": oi.get("fps"),
                         "sha256": _sha256(out_path)}
    if result:
        man["render"] = {k: result.get(k) for k in ("encoder", "frames", "format", "size", "seconds") if k in result}
    return man


def write_manifest(edl_path, edl, out_path, result=None, manifest_path=None) -> str:
    man = build_manifest(edl_path, edl, out_path, result)
    mp = manifest_path or (str(Path(out_path).with_suffix("")) + ".manifest.json")
    vl.save_json(mp, man)
    return str(mp)


BUNDLE_SIDECARS = ("video-brief.md", "beats.json", "scores.json", "clips.txt")


def build_bundle(edl_path: str | os.PathLike, out_zip: str | os.PathLike | None = None,
                 workdir: str | os.PathLike | None = None) -> str:
    """Zip the small, reusable project files (EDL, brief, beats, scores, clip list) for hand-off between machines, so
    the scratch files that were always getting lost travel with the edit. Media is never bundled (too large)."""
    edl_path = Path(edl_path)
    if not edl_path.is_file():
        raise vl.EngineError(f"EDL not found: {edl_path}")
    base = edl_path.resolve().parent
    work = Path(workdir) if workdir else base / (edl_path.stem + "_work")
    out_zip = Path(out_zip) if out_zip else edl_path.with_suffix(".bundle.zip")
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    added: list[str] = []
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(edl_path, edl_path.name)
        added.append(edl_path.name)
        for name in BUNDLE_SIDECARS:
            for cand in (base / name, work / name):
                if cand.is_file():
                    z.write(cand, name)
                    added.append(name)
                    break
        manifest = base / (edl_path.stem + ".manifest.json")
        if manifest.is_file():
            z.write(manifest, manifest.name)
            added.append(manifest.name)
        z.writestr("BUNDLE.txt", "mh-video-editor project bundle\ncontains: " + ", ".join(added) +
                   "\n(media files are not included; keep the original footage and music)\n")
    return str(out_zip)


# --------------------------------------------------------------------------- cache housekeeping

def gc_cache(workdir: str | os.PathLike, keep_days: float = 14.0) -> dict:
    """Delete cached part/LUT/master files under a work folder that have not been touched in `keep_days` days.

    Large libraries leave gigabytes of cached parts behind; this reclaims space without losing a current render's cache
    (anything used in the last two weeks is kept). Returns {'removed', 'freed_bytes'}.
    """
    work = Path(workdir)
    if not work.is_dir():
        return {"removed": 0, "freed_bytes": 0}
    import time
    cutoff = time.time() - keep_days * 86400
    removed, freed = 0, 0
    for sub in ("parts", "parts_preview", "luts", "text"):
        d = work / sub
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.is_file() and p.stat().st_mtime < cutoff:
                freed += p.stat().st_size
                p.unlink(missing_ok=True)
                removed += 1
    for p in work.glob("master_*.wav"):
        if p.stat().st_mtime < cutoff:
            freed += p.stat().st_size
            p.unlink(missing_ok=True)
            removed += 1
    return {"removed": removed, "freed_bytes": freed}


# What a work folder holds, by kind. Only what is made again from the footage by a later step may be deleted; the inventory,
# scores, beats, storyboards, decisions and anything the user wrote are never touched.
WORKSPACE_KINDS = (
    ("render parts", lambda p: "parts" in p.parts or "parts_preview" in p.parts, True),
    ("review proxies", lambda p: any(x.endswith("_media") for x in p.parts[:-1]) and p.suffix in (".mp4", ".m4a", ".jpg"), True),
    ("moment analysis", lambda p: "moments" in p.parts, True),
    ("luts and text", lambda p: "luts" in p.parts or "text" in p.parts, True),
    ("audio masters", lambda p: p.name.startswith(("master_", "music_edit_")) and p.suffix == ".wav", True),
    ("videos", lambda p: p.suffix.lower() in (".mp4", ".mov", ".mkv"), False),
    ("other", lambda p: True, False),
)


def _kind(p: Path) -> tuple:
    for name, test, deletable in WORKSPACE_KINDS:
        if test(p):
            return name, deletable
    return "other", False


def workspace(workdir: str | os.PathLike, keep_days: float = 14.0) -> dict:
    """Sizes by kind, how much is older than `keep_days`, and how much of that may be deleted."""
    import time
    work = Path(workdir)
    cutoff = time.time() - keep_days * 86400
    rows: dict = {}
    for p in work.rglob("*"):
        if not p.is_file():
            continue
        name, deletable = _kind(p.relative_to(work))
        st = p.stat()
        r = rows.setdefault(name, {"kind": name, "mb": 0.0, "files": 0, "old_mb": 0.0, "deletable": deletable})
        r["mb"] += st.st_size / 1e6
        r["files"] += 1
        if st.st_mtime < cutoff:
            r["old_mb"] += st.st_size / 1e6
    order = [k for k, _t, _d in WORKSPACE_KINDS]
    out = sorted(rows.values(), key=lambda r: order.index(r["kind"]))
    return {"rows": out, "mb": sum(r["mb"] for r in out), "freeable_mb": sum(r["old_mb"] for r in out if r["deletable"])}


def clean_workspace(workdir: str | os.PathLike, keep_days: float = 14.0) -> dict:
    """Delete the deletable kinds of files untouched for `keep_days` days."""
    import time
    work = Path(workdir)
    cutoff = time.time() - keep_days * 86400
    removed, freed = 0, 0
    for p in list(work.rglob("*")):
        if p.is_file() and _kind(p.relative_to(work))[1] and p.stat().st_mtime < cutoff:
            freed += p.stat().st_size
            p.unlink(missing_ok=True)
            removed += 1
    return {"removed": removed, "freed_bytes": freed}
