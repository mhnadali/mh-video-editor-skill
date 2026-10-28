#!/usr/bin/env python3
"""Create the vision plug-in's own virtual environment and install its two libraries into it.

    python plugins/vision/setup.py

It downloads `opencv-python-headless` and `numpy` from PyPI (about 60 MB) ONCE, into `plugins/vision/.venv`; nothing is
installed into your main Python. After that the plug-in runs offline. To remove it, delete the `.venv` folder.
"""
from __future__ import annotations

import subprocess
import sys
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
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
