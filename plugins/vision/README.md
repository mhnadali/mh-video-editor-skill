# Local vision plug-in (offline)

Finds people and faces in the footage so the story-first selection can count them without a human describing every clip.

- **Separate environment.** The engine is standard-library Python and never reaches the network. This plug-in has its own virtual
  environment (`plugins/vision/.venv`, created by `python plugins/vision/setup.py`, which installs `opencv-python-headless` and
  `numpy` from PyPI once, about 60 MB). Nothing is installed into your main Python. Delete `.venv` to remove it.
- **No model download.** It uses the classic OpenCV Haar cascades that ship inside the package (frontal face, profile face, upper
  body, full body). A scene classifier is used only if you give it an ONNX file of your own (`--onnx`, `--labels`).
- **Run it:** `python engine/scripts/mh_video.py vision run --work <work-dir>` writes `<work-dir>/vision.json`. `story select`,
  `story exclusions` and the review page read it automatically.
- **How good is it?** Measured against 34 hand-labelled clips of a real trip, 9 frames per clip: a face or body in any frame finds 92 %
  of the clips with people and wrongly flags 2 of 10 landscapes (precision 92 %). `people_sure` (a face in two frames, or a large one)
  has precision 95 % and recall 75 %. Cascades miss small, turned-away or shadowed people and close-ups of hands: the agent's own
  `people` / `role` notes always win over this plug-in.
- **Requirements:** OpenCV 4.x (OpenCV 5 removed the cascade classifier, so `requirements.txt` pins `<5`).
