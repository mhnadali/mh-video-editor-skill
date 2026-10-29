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

## The content plug-in (optional, 0.29)

`python plugins/vision/setup.py --content` sets up a second environment, `.venv-content` (onnxruntime has no wheels for the newest
Python yet: it uses Python 3.10-3.14, found with the `py` launcher on Windows), and downloads Google's SigLIP base model (Apache 2.0)
in its ONNX export by Xenova, quantized: the image part 99.5 MB, the text part 111 MB, the tokenizer 2.4 MB, all from one pinned
revision and each checked against its SHA-256 (`CONTENT_FILES` in setup.py). After that `content_run.py` works offline.

`mh_video.py content run --work W` embeds three frames of every clip (Kavir: 462 clips in 8.5 minutes, cached per file); `content search
"a car on a sand dune" --work W` lists the clips that fit a description. What the engine takes from it was measured first on the
Kavir labels (docs/CALIBRATION.md, phase K):

| Reading | Before | Content model | Used |
|---|---|---|---|
| People (60 hand labels) | YuNet 0.83 / 0.71 | 1.00 / 0.86 (153 noted clips: 1.00 / 0.72) | yes: a "yes" counts like a sure face |
| Shot size (174 noted clips) | 63 % | 74 % (zero-shot) | yes |
| Role of unnoted clips | 52 % (measurements) | 74 % (learned from the noted clips, 5-fold) | yes, as a guess (`story hook`) |
| Mood of unnoted clips | 49 % | 67 % | yes, as a guess |
| Same moment (20 pairs) | precision 1.0, recall 0.43 | 0.92 / 0.86 | only as a look-alike flag, never a drop |

The agent's notes always win over all of it.
