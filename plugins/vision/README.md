# Local vision plug-in (offline)

Finds people and faces in the footage so the story-first selection can count them without a human describing every clip.

- **Separate environment.** The engine is standard-library Python and never reaches the network. This plug-in has its own virtual
  environment (`plugins/vision/.venv`, created by `python plugins/vision/setup.py`, which installs `opencv-python-headless` and
  `numpy` from PyPI once, about 60 MB). Nothing is installed into your main Python. Delete `.venv` to remove it.
- **No model download.** It uses the classic OpenCV Haar cascades that ship inside the package (frontal face, profile face, upper
  body, full body). A scene classifier is used only if you give it an ONNX file of your own (`--onnx`, `--labels`).
- **Run it:** `python engine/scripts/mh_video.py vision run --work <work-dir>` writes `<work-dir>/vision.json`. `story select`,
  `story exclusions` and the review page read it automatically.
- **Better detector (optional): YuNet.** `python plugins/vision/setup.py --model` downloads the 232 KB MIT-licensed YuNet face
  detector once into `plugins/vision/models/` (SHA-256 checked, git-ignored); afterwards everything is offline and `vision run` uses
  it automatically. On 60 clips of a real trip labelled by eye it is right 83 % of the time and finds 71 % of the people (`person`),
  and 100 % / 71 % for `person_sure` (a face in two or more frames, larger than 0.5 % of the frame). Without the model the plug-in
  falls back to the classic Haar cascades, which were measured at 34-63 % precision on the same clips (clouds, mirrors, grills and
  food counters fool them; an earlier check on 34 easy clips had suggested 92 %). The engine counts each detector as part of a
  person according to those numbers and reports how many shots with people are confirmed by the agent's notes, which always win.
  See `docs/CALIBRATION.md` to measure it on your own footage.
- **Requirements:** OpenCV 4.x (OpenCV 5 removed the cascade classifier, so `requirements.txt` pins `<5`).
