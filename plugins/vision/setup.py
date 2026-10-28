#!/usr/bin/env python3
"""Create the vision plug-in's own virtual environment and install its two libraries into it.

    python plugins/vision/setup.py            # the environment (opencv-python-headless and numpy)
    python plugins/vision/setup.py --model    # optional: the YuNet face detector (232 KB), checked against its SHA-256

It downloads `opencv-python-headless` and `numpy` from PyPI (about 60 MB) ONCE, into `plugins/vision/.venv`; nothing is
installed into your main Python. After that the plug-in runs offline. To remove it, delete the `.venv` folder.
"""
from __future__ import annotations

import subprocess
import sys
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent


MODEL_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"


def fetch_model() -> int:
    """Download the YuNet face detector (232 KB, MIT licence, OpenCV Zoo) once into plugins/vision/models and check its hash."""
    import hashlib
    import urllib.request
    dest = HERE / "models" / "face_detection_yunet_2023mar.onnx"
    dest.parent.mkdir(exist_ok=True)
    print(f"downloading {MODEL_URL} ...")
    data = urllib.request.urlopen(MODEL_URL, timeout=60).read()
    if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
        print("the downloaded file does not have the expected SHA-256; not saved", file=sys.stderr)
        return 1
    dest.write_bytes(data)
    print(f"saved {dest} ({len(data)} bytes)")
    return 0


def main() -> int:
    if "--model" in sys.argv:
        return fetch_model()
    env = HERE / ".venv"
    if not env.exists():
        print(f"creating {env} ...")
        venv.EnvBuilder(with_pip=True).create(env)
    py = env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    print("installing opencv-python-headless and numpy into the plug-in environment ...")
    r = subprocess.run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(HERE / "requirements.txt")])
    if r.returncode != 0:
        print("pip failed; see the messages above", file=sys.stderr)
        return r.returncode
    check = subprocess.run([str(py), "-c", "import cv2, numpy; print('opencv', cv2.__version__, 'numpy', numpy.__version__)"])
    return check.returncode


if __name__ == "__main__":
    sys.exit(main())
