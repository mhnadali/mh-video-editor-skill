# Security Policy

## Supported versions
The latest release on `main`.

## Reporting a vulnerability
Please do not open a public issue. Use GitHub's private vulnerability reporting (Security tab, "Report a vulnerability") or contact the maintainer through their GitHub profile.

## Scope notes
- The engine only runs FFmpeg/FFprobe on files you point it at and writes into the work and output folders you choose. It never downloads media, music or software.
- The optional vision plug-in (`plugins/vision`) is installed only when you run its `setup.py` yourself: it creates its own virtual environment from PyPI (`opencv-python-headless`, `numpy`), and `setup.py --model` fetches one 232 KB face-detection model from the OpenCV Zoo on GitHub, saved only if its SHA-256 matches. After that it runs offline, as a separate process; the engine never imports it.
- The optional content plug-in is installed only by `python plugins/vision/setup.py --content`: a second environment
  (`plugins/vision/.venv-content`, from PyPI: `onnxruntime`, `tokenizers` and their dependencies, about 160 MB on disk) and the SigLIP
  model files (Google, Apache 2.0, about 211 MB) from one pinned revision of `huggingface.co/Xenova/siglip-base-patch16-224`, each file
  saved only if its SHA-256 matches. `tokenizers` brings `huggingface_hub` as a dependency; the plug-in never calls it and touches no
  network after setup.
- The review page (`story review`) is a static HTML file with no network access: clip names are shown as text, never as markup, and the embedded data is escaped so it cannot close the script. Decisions are kept only in your browser's storage and in the file you export.
- The only file the engine deletes outside its work folders is the global taste profile, and only on `story profile --forget-global` after it has been read as a valid profile.
- The skill tells agents never to install software, publish, push or send anything, and to treat file names, EDL text and tool output as data, not instructions.
- Clip paths from an EDL are passed to FFmpeg as arguments (no shell), so file names with spaces or quotes are safe.
