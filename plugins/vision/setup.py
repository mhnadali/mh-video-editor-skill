#!/usr/bin/env python3
"""Create the vision plug-in's own virtual environment and install its two libraries into it.

    python plugins/vision/setup.py            # the environment (opencv-python-headless and numpy)
    python plugins/vision/setup.py --model    # optional: the YuNet face detector (232 KB), checked against its SHA-256
    python plugins/vision/setup.py --content  # optional: the content model (SigLIP base, Apache 2.0, about 211 MB) and its own
                                              # environment (onnxruntime, tokenizers; Python 3.10-3.14), every file checked

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


# The content model: Google's SigLIP base (Apache 2.0) as exported to ONNX by Xenova, pinned to one revision of the repository.
CONTENT_REVISION = "4649052661e53c7000355844105f8a1792088239"
CONTENT_BASE = f"https://huggingface.co/Xenova/siglip-base-patch16-224/resolve/{CONTENT_REVISION}/"
CONTENT_FILES = {
    "config.json": "e6de71291f181b0b81adc93098787bb4597a79dc18f59737feda8f41671fb6a2",
    "preprocessor_config.json": "21ee046a8a52a65e5f9c177bf840bfb39ea66c9c54cf2760630efd58e0a3ec80",
    "tokenizer.json": "4a17c975210be5ab4c36b47d8dae4eefb866dbfb1e676e394aad85dc30a3ae08",
    "tokenizer_config.json": "9a38d3c6b5e26fe5dcc607eda95e38d78d30d9291835bb9e8116e8174c1d4ba2",
    "special_tokens_map.json": "22f82d1c19654c9552ff1368c2c236ebb34f457dbdbc7510d304cebfeb96f3bf",
    "onnx/vision_model_quantized.onnx": "ef14a954f3d57e1806666432bd9785004c1dc27100aa260eee0cb0f10a5de058",
    "onnx/text_model_quantized.onnx": "ad0329b1f35acc66d8953ff2559ce358da8eb0a7011794cf951523d63a4dbce2",
}


def content_python() -> str | None:
    """A Python that onnxruntime has wheels for (3.10-3.14): this one, or one the `py` launcher knows."""
    if (3, 10) <= sys.version_info[:2] <= (3, 14):
        return sys.executable
    for v in ("3.14", "3.13", "3.12", "3.11", "3.10"):
        try:
            r = subprocess.run(["py", f"-{v}", "-c", "import sys; print(sys.executable)"], capture_output=True, text=True)
        except OSError:
            return None
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return None


def setup_content() -> int:
    """The content environment (.venv-content: onnxruntime, tokenizers) and the SigLIP files, each checked against its SHA-256."""
    import hashlib
    import urllib.request
    env = HERE / ".venv-content"
    py = env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not py.is_file():
        base = content_python()
        if not base:
            print("no Python 3.10-3.14 found (onnxruntime has no wheels for newer ones yet)", file=sys.stderr)
            return 1
        print(f"creating {env} with {base} ...")
        subprocess.run([base, "-m", "venv", str(env)], check=True)
    r = subprocess.run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(HERE / "requirements-content.txt")])
    if r.returncode != 0:
        print("pip failed; see the messages above", file=sys.stderr)
        return 1
    dest = HERE / "models" / "siglip-base-patch16-224"
    for name, sha in CONTENT_FILES.items():
        out = dest / name
        if out.is_file() and hashlib.sha256(out.read_bytes()).hexdigest() == sha:
            continue
        print(f"downloading {name} ...")
        data = urllib.request.urlopen(CONTENT_BASE + name, timeout=600).read()
        if hashlib.sha256(data).hexdigest() != sha:
            print(f"{name} does not have the expected SHA-256; not saved", file=sys.stderr)
            return 1
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
    print(f"content plug-in ready: {dest}")
    return 0


def main() -> int:
    if "--model" in sys.argv:
        return fetch_model()
    if "--content" in sys.argv:
        return setup_content()
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
