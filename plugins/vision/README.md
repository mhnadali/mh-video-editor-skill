# Local vision plug-in (offline)

Finds people and faces in the footage so the story-first selection can count them without a human describing every clip.

- **Separate environment.** The engine is standard-library Python and never reaches the network. This plug-in has its own virtual
  environment (`plugins/vision/.venv`, created by `python plugins/vision/setup.py`, which installs `opencv-python-headless` and
  `numpy` from PyPI once, about 60 MB). Nothing is installed into your main Python. Delete `.venv` to remove it.
- **No model download.** It uses the classic OpenCV Haar cascades that ship inside the package (frontal face, profile face, upper
  body, full body). A scene classifier is used only if you give it an ONNX file of your own (`--onnx`, `--labels`).
- **Run it:** `python engine/scripts/mh_video.py vision run --work <work-dir>` writes `<work-dir>/vision.json`. `story select`,
  `story exclusions` and the review page read it automatically.
- **How good is it? Honestly: a weak hint.** Measured against 60 clips of a real desert trip that were labelled by looking at three
  frames of each (a sample with a share of every kind of verdict; 413 clips were looked at): a face or body in any frame (`person`)
  finds about 80 % of the clips with people but is right only about 40 % of the time on the whole trip; a face in three or more
  frames (`person_sure`) is right 60 % of the time and finds a third to a half of the people. Clouds, mirrors, grills and food
  counters fool the cascades, and they miss small, turned-away or shadowed people. An earlier check on 34 clips had suggested 92 %;
  it was too small and too easy. So the engine counts a sighting only as part of a person (60 % for `person_sure`, 25 % for the
  rest) and the storyboard reports how many shots with people are confirmed by the agent's notes. The agent's own `people` / `role`
  notes always win. A better detector (your own ONNX model) is the way to improve this; see `docs/CALIBRATION.md` to measure it.
- **Requirements:** OpenCV 4.x (OpenCV 5 removed the cascade classifier, so `requirements.txt` pins `<5`).
