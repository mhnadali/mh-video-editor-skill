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
                                             "color_transfer", "hdr", "has_audio", "created_utc", "device")})
        loc = vl.parse_local(info.get("created_local"))
        rec["taken_local"] = loc[0] if loc else None            # the phone's own wall-clock time (trustworthy)
        rec["taken_offset_min"] = loc[1] if loc else None
    except vl.EngineError as e:
        rec["error"] = str(e).splitlines()[0]
    return rec


def assign_times(records: list[dict], utc_offset_min: int = 210, device_shift_min: dict | None = None) -> None:
    """Give every record `taken` (local wall-clock 'YYYY-MM-DD HH:MM:SS') and `taken_epoch` (comparable across devices).

    A phone that stores its own local time (Apple) is used as is. Other cameras store UTC: it is turned into local time
    with `utc_offset_min` (Iran: 210), then shifted by `device_shift_min[substring of the device name]` minutes when that
    camera's clock or time zone is known to be off. With no tag at all the file's modification time is the last resort
    (`time_source` says which)."""
    import datetime as dt
    shifts = device_shift_min or {}
    for r in records:
        shift = next((m for key, m in shifts.items() if key.lower() in (r.get("device") or r.get("name") or "").lower()), 0)
        if r.get("taken_local"):
            wall = dt.datetime.strptime(r["taken_local"], "%Y-%m-%d %H:%M:%S")
            off = r["taken_offset_min"] if r.get("taken_offset_min") is not None else utc_offset_min
            r["time_source"] = "device-local"
            epoch = wall.replace(tzinfo=dt.timezone.utc).timestamp() - off * 60
        elif r.get("created_utc"):
            r["time_source"] = "container-utc"
            epoch = r["created_utc"]
        else:
            r["time_source"] = "file-mtime"
            epoch = float(r.get("mtime") or 0)
        epoch += shift * 60
        r["taken_epoch"] = epoch
        r["taken"] = dt.datetime.fromtimestamp(epoch + utc_offset_min * 60, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        r["clock_shift_min"] = shift


def chronological(records: list[dict]) -> list[dict]:
    """Re-number the records in capture order (index 0 = the first clip of the trip), keeping `dup_of` consistent.

    Every later step (contact sheets, `pick --only 100-330`, the story order) then reads in the order things happened."""
    order = sorted(records, key=lambda r: (r.get("taken_epoch") or 0.0, r["name"]))
    new_index = {r["index"]: k for k, r in enumerate(order)}
    for r in order:
        r["index"] = new_index[r["index"]]
        if r.get("dup_of") is not None:
            r["dup_of"] = new_index[r["dup_of"]]
    for k, r in enumerate(order):
        r["index"] = k
    return order


def write_timeline(records: list[dict], outdir: str | os.PathLike) -> str:
    """timeline.csv: every readable clip in capture order with the gap to the previous one."""
    out = Path(outdir)
    rows = sorted((r for r in records if not r.get("error") and r.get("taken_epoch")), key=lambda r: r["taken_epoch"])
    prev = None
    with open(out / "timeline.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["order", "taken", "device", "time_source", "gap_s", "duration", "index", "name"])
        for k, r in enumerate(rows):
            gap = round(r["taken_epoch"] - (prev["taken_epoch"] + (prev.get("duration") or 0))) if prev else ""
            w.writerow([k, r["taken"], r.get("device") or "", r["time_source"], gap, round(r.get("duration") or 0, 1),
                        r["index"], r["name"]])
            prev = r
    return str(out / "timeline.csv")


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


def scan(folder: str | os.PathLike, recursive: bool = False, workers: int | None = None,
         utc_offset_min: int = 210, device_shift_min: dict | None = None) -> list[dict]:
    files = list_media(folder, recursive)
    with ThreadPoolExecutor(workers or vl.cpu_workers(6)) as ex:
        records = list(ex.map(lambda ip: _record(*ip), enumerate(files)))
    mark_duplicates(records)
    assign_times(records, utc_offset_min, device_shift_min)
    records = chronological(records)
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
    write_timeline(records, out)
    cols = ["index", "name", "taken", "device", "duration", "width", "height", "rotation", "fps", "codec", "hdr", "has_audio",
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
