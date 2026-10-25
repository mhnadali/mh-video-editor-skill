"""Step 1: inventory a footage folder (duration, size, rotation, fps, HDR, duplicates)."""
from __future__ import annotations

import csv
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import vidlib as vl


def list_media(folder: str | os.PathLike, recursive: bool = False) -> list[Path]:
    root = Path(folder)
    if not root.is_dir():
        raise vl.EngineError(f"folder not found: {folder}")
    it = root.rglob("*") if recursive else root.glob("*")
    return sorted(p for p in it if p.is_file() and p.suffix.lower() in vl.VIDEO_EXTS)


def _record(idx: int, path: Path) -> dict:
    st = path.stat()
    rec = {"index": idx, "path": vl.ffp(path), "name": path.name, "size": st.st_size,
           "mtime": int(st.st_mtime), "error": None}
    try:
        info = vl.probe(path)
        if not info.get("has_video"):
            rec["error"] = "no video stream"
        rec.update({k: info.get(k) for k in ("duration", "width", "height", "rotation", "fps", "codec", "pix_fmt",
                                             "color_transfer", "hdr", "has_audio")})
    except vl.EngineError as e:
        rec["error"] = str(e).splitlines()[0]
    return rec


def mark_duplicates(records: list[dict]) -> int:
    """Files with identical size and modification second are almost certainly copies (e.g. two exports)."""
    seen: dict[tuple, int] = {}
    n = 0
    for r in records:
        key = (r["size"], r["mtime"])
        if key in seen:
            r["dup_of"] = seen[key]
            n += 1
        else:
            seen[key] = r["index"]
            r["dup_of"] = None
    return n


def scan(folder: str | os.PathLike, recursive: bool = False, workers: int | None = None) -> list[dict]:
    files = list_media(folder, recursive)
    with ThreadPoolExecutor(workers or vl.cpu_workers(6)) as ex:
        records = list(ex.map(lambda ip: _record(*ip), enumerate(files)))
    mark_duplicates(records)
    return records


def summarize(records: list[dict]) -> dict:
    ok = [r for r in records if not r["error"]]
    by: dict[str, int] = {}
    for r in ok:
        key = f"{r['width']}x{r['height']}"
        by[key] = by.get(key, 0) + 1
    return {
        "files": len(records), "readable": len(ok), "errors": len(records) - len(ok),
        "total_minutes": round(sum(r["duration"] or 0 for r in ok) / 60, 1),
        "total_gb": round(sum(r["size"] for r in records) / 1e9, 2),
        "hdr_clips": sum(1 for r in ok if r.get("hdr")),
        "duplicates": sum(1 for r in records if r.get("dup_of") is not None),
        "vertical": sum(1 for r in ok if r["height"] > r["width"]),
        "horizontal": sum(1 for r in ok if r["width"] >= r["height"]),
        "resolutions": dict(sorted(by.items(), key=lambda kv: -kv[1])),
    }


def write_inventory(records: list[dict], outdir: str | os.PathLike) -> dict:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    vl.save_json(out / "inventory.json", {"summary": summarize(records), "clips": records})
    cols = ["index", "name", "duration", "width", "height", "rotation", "fps", "codec", "hdr", "has_audio",
            "size", "dup_of", "error", "path"]
    with open(out / "inventory.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(records)
    return {"json": str(out / "inventory.json"), "csv": str(out / "inventory.csv")}


def load_inventory(workdir: str | os.PathLike) -> list[dict]:
    p = Path(workdir) / "inventory.json"
    if not p.is_file():
        raise vl.EngineError(f"no inventory in {workdir}; run `probe` first")
    return vl.load_json(p)["clips"]
