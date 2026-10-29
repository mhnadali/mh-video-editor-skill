# Roadmap and state

This document is the honest state of the project and where it is going. It also carries the research and measurements
behind the MP4 export / size-reduction layer, and hand-off notes for continuing on another machine.

## State

Version **0.27.0**. Standard-library Python over FFmpeg (plus an optional, separately installed vision plug-in), 830+ tests on
synthetic media, and since 0.17 every selection change is measured against a real trip (486 clips, three cameras, two days;
numbers in `docs/CALIBRATION.md`).

## Story-first selection arc (0.15 - 0.16.1)
Chapters and a storyboard the user approves before anything is planned; hard filters (nothing under 5 s), same-moment groups (only
the best take), the best moment inside a clip, a beam search over the whole sequence with three variants, remembered taste, an
offline review page, an evaluation harness; then a deep review that fixed twelve bugs.

## Towards 1.0 (0.24 - 0.29, phases H - M)
- **H, the picture (0.24):** 10-bit parts, a 16-bit assembly dithered once, every conversion in Rec.709 (colours exact; the old chain
  was 1-2 levels dark), an optional 10-bit HEVC master. Banding and noise measured on all storyboard shots: no filter needed.
- **I, the opening, the ending, the whole flow (0.25):** *Play the storyboard* over the music on the review page, `story hook`
  (teaser candidates from the clips the agent read; measured energy cannot find a hook), `teaser_clip`, a calibrated loop check.
- **J, the sound (0.26):** the beat analysis' 0.23 % time-scale error fixed; clip sound measured (voices, wind, engines, clipping)
  and windy or clipped ambient left out; `music fit` ends on the song's own ending with the beats kept; a listening page and
  `calibrate_audio.py` (the thresholds wait for the user's ears).
- **L, workflow and speed (0.27):** `prepare` (footage to review page, every step cached, 2 s warm on the real project), `clean`,
  frame strips reused (a review rebuild 13.7 s -> 0.3 s).
- Next: M reliability (0.28); K content understanding, after the owner approves the model download; 1.0 on a second real project.

## Real-footage arc (0.17 - 0.23, phases A - G)
- **A, validation (0.17):** the picture hashes were one frame repeated (fixed, scores repaired in place); same-moment strictness
  tuned on labelled pairs; look-alikes flagged, not dropped; the cascade face detector measured at about 40 % precision, so its
  sightings count only partly; `calibrate_selection.py`.
- **B, content (0.18):** optional YuNet face detector (precision 0.83-1.0); shot size re-measured (21 % -> 63 % agreement);
  `drop-weak` audit. Role and mood from measurements failed cross-validation and were not built.
- **C, review (0.19):** proxy videos, timeline, start-here, auto-save, load and start over.
- **D, image (0.20):** camera colour matching from simultaneous clips (out of sample -24 %); `plan --stabilize-shaky`; slow-motion
  frame check.
- **E, speed (0.21):** faster reusable proxies (-46 % for a real page), time left in long steps, `score --min-duration`.
- **F, outputs (0.22):** cut-downs from an approved storyboard, taste promoted across projects, music ranked by the story arc.
- **G, engineering (0.23):** the randomized rule check is a tool and a test; these documents brought up to date.

## Next
- **Decisions that are the owner's:** branch protection on `main`, whether the repository stays public (every pushed `v*`
  tag already publishes a GitHub Release through `release.yml`, since 0.15.0), an FFmpeg-version matrix and a macOS runner in CI (both need a trusted FFmpeg
  source; the Actions policy blocks third-party setup actions).
- **1.0** when the pipeline has run end to end on a second real project with different footage without a serious problem, and the
  calibration numbers hold there (one trip cannot prove a threshold).
- Sound: J/L-cuts and the ambient bed are implemented and measured for level, but have not been judged by ear on real footage.

## Professional-editing arc (0.7.0, tracks A-G)

The guiding idea is **the engine measures, the agent judges** (see `references/editorial-rubric.md`). Implemented:

- **A - the science of the cut:** `vid_editorial.py` measures shot size, camera-motion direction, and a saliency
  centroid; the QA gate warns on jump cuts, repeated shot sizes, screen-direction reversals and flat pacing.
- **B - video with a purpose:** `plan --pacing energy` / `need --pacing energy` make shot length follow the music's
  energy instead of a mechanical one-shot-per-beat.
- **C - music structure & selection:** `beats` reports per-beat strength and musical sections (intro/build/drop/outro);
  `engine/tools/pick_music.py` ranks the user's own library by fit to a brief (never downloads).
- **D - colour:** `color plan --wb skin` protects warm skin tones (neutralises a cast, never cools).
- **E - framing:** `focus: "auto"` keeps the subject in frame when reframing to another aspect.
- **F - the flawless gate:** the editorial, sync and colour checks fold into the QA gate with `--scores`.
- **G - architecture & the ML boundary:** documented in `docs/ARCHITECTURE.md`; perception stays heuristic to keep the
  zero-dependency promise.

Next within this arc: combine more scoring cues; shot-to-shot colour matching across a sequence; a dedicated hook pass.
(Done in 0.8.0: sample-accurate onset refinement and a full tempo map with tempo-following cuts; hue-qualified
secondary colour correction; a single quality score in the QA gate.)

## Cut and transition craft arc (0.9.0 - 0.12.0)

Goal: every cut and transition has a type, a reason and a fit with the footage. The engine measures, the agent judges.

- **0.9.0 - knowledge (done):** `references/cut-craft.md`, `transition-craft.md`, `footage-reading.md`, `shot-notes.json`, the cut-plan table and the "every cut has a reason" rule in the skill and the agent.
- **0.10.0 - decision engine (done):** `vid_cutlogic.py` (pair features, an explainable classifier, `cuts` command with dry-run and `--write`), `plan --smart-transitions` within the existing budget, an EDL `rationale` field, lint/QA warnings (`transition-unmotivated`, `direction-mismatch`) and a `cut_report`.
- **0.11.0 - cut types in the renderer (done; direction-aware transitions arrived in 0.10):** per-transition duration, J-cut / L-cut for clip ambient sound, motion alignment (match-on-action), direction-aware transitions.
- **0.12.0 - measurement (done):** cheap content features (palette, subject vs camera motion, internal cuts), cut-quality metrics in `eval_quality.py`, a human checklist for real footage.

## State (prior arc)

See [`CHANGELOG.md`](../CHANGELOG.md) for what changed in each version and [`SUPPORT.md`](../SUPPORT.md) for the
platform / FFmpeg support matrix.

## Export and size-reduction layer (implemented in 0.6.0)

Platforms re-encode whatever you upload, so the goal is not to out-smart them but to hand them a clean, correctly
shaped, correctly loud file that survives the re-encode and respects their size limits.

- **Delivery presets** (`render --delivery reels|tiktok|shorts|stories|youtube|square`) set the output format, the
  integrated-loudness target (−14 LUFS, the platform norm) and the container (`yuv420p`, `+faststart`).
- **Size targeting** (`render --target-size-mb N`) computes a video bitrate from the duration and the audio bitrate and
  uses **two-pass** libx264 (or VBR on a hardware encoder). A size target is treated as an **upper bound**: busy footage
  fills it, flat footage undershoots, which is fine.
- **Bitrate** (`render --bitrate kbps`) sets the video bitrate directly.
- Verified by a delivery check (size within tolerance, aspect, container) in the render result.

### Measurements (synthetic 6 s, 8 shots, 180×320, libx264 on the CI Linux box)

| Request | Result |
|---|---|
| `--target-size-mb 1.5` (two-pass) | file ≤ target; flat test footage undershoots, as expected |
| `--delivery reels` | 9:16, −14 LUFS, faststart |
| default (constant quality) | unchanged from 0.5.x |

A megabyte here is 8·1000·1000 bits (how platforms quote limits); 2% is reserved for container overhead, and a target
that leaves less than 120 kbit/s for the picture is refused with a clear message.

## Post-0.5 roadmap (phases 6–12)

- **6 — Cross-environment trust.** CI runs the full render suite on Linux and Windows; `doctor` reports the working
  hardware encoders; a robustness fix handles FFmpeg builds (6.x) that print `-nan` from `blurdetect`. macOS is covered
  by the manual QA protocol rather than CI: the GitHub macOS runner's Homebrew ffmpeg install fails at setup, and the
  repo's Actions policy blocks the third-party `setup-ffmpeg` action. Remaining: a first green CI run observed on GitHub,
  branch protection, an FFmpeg-version matrix in CI, and a workable macOS CI ffmpeg source.
- **7 — Onboarding & distribution.** A zero-footage sample project (`engine/tools/make_sample.py`), a quickstart in the
  README, and a tag-driven release workflow (it has published a Release for every tag since 0.15.0).
- **8 — Export & delivery.** Done (above).
- **9 — Editing intelligence.** An evaluation harness (`engine/tools/eval_quality.py`) reports beat error, pick diversity
  and restraint on a fixed synthetic corpus, so changes are judged by numbers. `beats --bpm/--first-beat/--beats-per-bar`
  allow tap-tempo and non-4/4 grids. Next: combine more scoring cues; section-aware pacing.
- **10 — New capabilities.** Reusable text templates (`text templates`), three more looks, and multi-track music
  (`project.music` as a list of sections, concatenated). Next: more title/caption animations; per-platform mastering
  curves beyond loudness.
- **11 — Performance & scale.** Hardware-encoder detection (NVENC, VideoToolbox, QSV; VAAPI reported), a cache
  garbage-collector (`vid_deliver.gc_cache`), and per-shot progress logging. Next: resumable scoring for very large
  libraries; measured throughput on 500+ clips.
- **12 — Reproducibility & automation.** A render manifest (`--manifest`: engine/FFmpeg versions, EDL and output hashes,
  input sizes), a project bundle (`--bundle`: EDL + brief + beats + scores, no media), and `--json` result output.

## Hand-off notes (continuing on another machine)
- Clone, then `python engine/scripts/mh_video.py doctor`. Install FFmpeg if it is missing (`winget install Gyan.FFmpeg`
  on Windows, `brew install ffmpeg` on macOS, `apt install ffmpeg` on Linux).
- `cd engine/tests && python run_tests.py --fast` finishes in about two minutes without the slow renders; the full suite
  needs FFmpeg and runs the end-to-end renders on synthetic media (no footage required).
- The reproducibility manifest (`render --manifest`) records the FFmpeg version and output hash, so you can tell whether
  another machine produced the same render. A project bundle (`render --bundle`) carries the EDL and its sidecars between
  machines; the scratch files that used to get lost now travel with the edit.
- Everything reusable lives in this repository. Intermediate files in a work folder are disposable (and
  `vid_deliver.gc_cache` reclaims them).
