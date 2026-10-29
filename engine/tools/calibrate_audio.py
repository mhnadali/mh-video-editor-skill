"""Check the engine's reading of the footage's sound against what a person heard.

    python engine/tools/calibrate_audio.py --work WORK --labels audio_labels.json

`audio_labels.json` is the export of the listening page (`mh_video.py audio review`). Prints how often the measured label agrees
with the heard one, the confusion table, the precision and recall of "wind" (the one label that removes sound from the ambient
bed: a false "wind" loses good sound, a missed one lets wind under the music), and a sweep of the wind thresholds that keeps
precision first. Put a better setting in the EDL as `style.ambient.thresholds` (e.g. {"wind_low_db": -3, "wind_periodic": 0.4}).
"""
from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import vid_listen  # noqa: E402
import vidlib as vl  # noqa: E402


def sweep(sounds: dict, labels: list) -> list:
    rows = []
    for low, per in itertools.product((-8.0, -6.0, -5.0, -4.0, -3.0, -2.0), (0.3, 0.4, 0.5, 0.6, 0.7)):
        r = vid_listen.agreement(sounds, labels, {"wind_low_db": low, "wind_periodic": per})
        rows.append({"wind_low_db": low, "wind_periodic": per, **{k: r[k] for k in ("agree", "wind_precision", "wind_recall")}})
    rows.sort(key=lambda r: (-(r["wind_precision"] or 0), -(r["wind_recall"] or 0), -(r["agree"] or 0)))
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--work", required=True, help="work folder with audio.json (`audio scan`)")
    ap.add_argument("--labels", required=True, help="audio_labels.json exported by the listening page")
    a = ap.parse_args(argv)
    sounds = vl.load_json(Path(a.work) / "audio.json")
    labels = (vl.load_json(a.labels) or {}).get("labels") or []
    r = vid_listen.agreement(sounds, labels)
    print(f"{r['n']} labelled excerpts; measured label agrees with the heard one in {r['agree']}")
    print(f"wind: precision {r['wind_precision']}, recall {r['wind_recall']} (precision first: a false 'wind' drops good sound)")
    print("heard -> measured:")
    for heard, row in sorted(r["table"].items()):
        print(f"  {heard:8} " + ", ".join(f"{k} {v}" for k, v in sorted(row.items())))
    print("wind thresholds, best first:")
    for row in sweep(sounds, labels)[:5]:
        print(f"  low_db >= {row['wind_low_db']:>5}, periodic < {row['wind_periodic']}: precision {row['wind_precision']}, "
              f"recall {row['wind_recall']}, agreement {row['agree']}")
    good = [lb for lb in labels if lb.get("good") is not None]
    if good:
        print(f"good under the music: {sum(1 for lb in good if lb['good'])} of {len(good)} answered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
