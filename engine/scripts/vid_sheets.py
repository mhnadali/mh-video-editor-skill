"""Step 2b: contact sheets so the agent can SEE the footage (and the rendered result).

`clip_sheets` makes numbered thumbnail sheets of an inventory (index burned in, one frame per clip).
`video_sheet` makes one sheet of evenly spaced frames from a rendered video for visual QA.
"""
from __future__ import annotations

import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import vidlib as vl

def _tm(npl: int, curve: str, param: str = "") -> str:
    return (f"zscale=t=linear:npl={npl},format=gbrpf32le,zscale=p=bt709,tonemap=tonemap={curve}{param}:desat=0,"
            "zscale=t=bt709:m=bt709:r=tv,format=yuv420p,")


# HLG/PQ -> SDR Rec.709. `hable-203` maps HLG reference white (203 nits, ITU-R BT.2408) to SDR white: interiors stay readable and
# skies keep detail. `legacy` (npl 1000) is what the first four Maranjab versions used: darker, flatter HDR shots.
TONEMAPS = {
    "hable-203": _tm(203, "hable"),
    "mobius-203": _tm(203, "mobius", ":param=0.3"),
    "reinhard-203": _tm(203, "reinhard", ":param=0.4"),
    "legacy": _tm(1000, "hable"),
}
DEFAULT_TONEMAP = "hable-203"
TONEMAP_HLG = TONEMAPS[DEFAULT_TONEMAP]


def tonemap_chain(name: str | None = None) -> str:
    """Filter chain (ending with a comma) that turns an HDR frame into SDR Rec.709."""
    name = name or DEFAULT_TONEMAP
    if name not in TONEMAPS:
        raise vl.EngineError(f"hdr_tonemap '{name}' is unknown (known: {', '.join(TONEMAPS)})")
    return TONEMAPS[name]


def _thumb(rec: dict, out: Path, size: int, pos: float, font: str | None) -> bool:
    dur = rec.get("duration") or 0
    t = max(0.0, dur * pos)
    vf = (TONEMAP_HLG if rec.get("hdr") else "") + \
        f"scale={size}:{size}:force_original_aspect_ratio=decrease,pad={size}:{size}:(ow-iw)/2:(oh-ih)/2"
    if font:
        vf += (f",drawtext=text='{rec['index']}':fontfile='{vl.filter_escape_path(font)}':fontsize={max(14, size // 8)}:"
               "fontcolor=yellow:borderw=2:bordercolor=black:x=4:y=4")
    # nokey: decode only key frames after the seek (the nearest one is as good as the exact frame for a thumbnail, and
    # 4 times faster on long-GOP 4K files); if that yields nothing (a clip without usable key frames) decode normally
    base = [vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y"]
    tail = ["-ss", f"{t:.2f}", "-i", rec["path"], "-frames:v", "1", "-vf", vf, "-q:v", "4", out]
    r = vl.run(base + ["-skip_frame", "nokey", "-threads", "2"] + tail, check=False)
    if r.returncode != 0 or not out.is_file() or out.stat().st_size < 200:
        r = vl.run(base + tail, check=False)
    return r.returncode == 0 and out.is_file()


def _blank(out: Path, size: int) -> None:
    vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x202020:s={size}x{size}",
            "-frames:v", "1", out])


def clip_sheets(records: list[dict], outdir: str | os.PathLike, size: int = 200, cols: int = 8, rows: int = 5,
                pos: float = 0.4, workers: int | None = None) -> list[str]:
    """Write sheetNN.jpg files into outdir; returns their paths. Unreadable clips show a dark tile."""
    out = Path(outdir)
    thumbs = out / "thumbs"
    thumbs.mkdir(parents=True, exist_ok=True)
    font = vl.find_font()
    idx_files = []

    def work(rec):
        p = thumbs / f"{rec['index']:04d}.jpg"
        ok = (not rec.get("error")) and _thumb(rec, p, size, pos, font)
        if not ok:
            _blank(p, size)
        return p

    with ThreadPoolExecutor(workers or vl.cpu_workers(8)) as ex:
        idx_files = list(ex.map(work, records))
    per = cols * rows
    sheets = []
    ff = vl.find_ffmpeg()["ffmpeg"]
    # thumbnails are numbered by inventory index; build each sheet from its contiguous index range
    for s in range(0, len(records), per):
        first = records[s]["index"]
        sheet = out / f"sheet{s // per:02d}.jpg"
        vl.run([ff, "-v", "error", "-y", "-framerate", "1", "-start_number", str(first), "-i", str(thumbs / "%04d.jpg"),
                "-frames:v", "1", "-vf", f"tile={cols}x{rows}:padding=2:color=black", "-q:v", "3",
                "-update", "1", sheet])
        sheets.append(str(sheet))
    return sheets


def video_sheet(video: str | os.PathLike, out: str | os.PathLike, count: int = 48, cols: int = 8,
                tile_w: int = 150) -> str:
    info = vl.probe(video)
    if not info.get("has_video"):
        raise vl.EngineError(f"no video stream in {video}")
    fps = info.get("fps") or 30
    total = max(1, int(info["duration"] * fps))
    step = max(1, total // count)
    rows = -(-count // cols)
    tile_h = vl.even(tile_w * info["height"] / max(info["width"], 1))
    vf = f"select='not(mod(n,{step}))',scale={tile_w}:{tile_h},tile={cols}x{rows}:padding=2:color=black"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-i", vl.ffp(video), "-vf", vf, "-frames:v", "1",
            "-update", "1", "-q:v", "3", out])
    return str(out)
