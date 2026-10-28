# Roadmap and state

This document is the honest state of the project and where it is going. It also carries the research and measurements
behind the MP4 export / size-reduction layer, and hand-off notes for continuing on another machine.

## State

Version **0.8.0**. The original five-phase plan (foundation; speed and accuracy; the automated QA gate; new
capabilities; hardening, documentation and CI) is complete, the first post-v0.5 arc landed in 0.6.0 (cross-environment
trust, onboarding, a delivery/export layer, editing-quality tooling, broader hardware support, reproducibility), and a
professional-editing arc landed in 0.7.0 (tracks A-G below). The engine is standard-library Python over FFmpeg, with
540+ tests on synthetic media.

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
  README, and a tag-driven release workflow. Remaining: the first tagged GitHub Release.
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
