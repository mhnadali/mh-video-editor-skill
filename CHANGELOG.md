# Changelog

All notable changes to this project. Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: [SemVer](https://semver.org/).

## [0.10.0] - 2026-10-28 (cut and transition craft, phase 2: decision engine)
### Added
- **`vid_cutlogic.py`:** for every boundary between two shots it names the kind of cut and gives a one-sentence reason from the content: `impact` (the drop), `closing`, `cut-on-action`, `direction-clash`, `graphic-match`, `contrast-cut`, `size-step`, `soften`, `jump-risk`, `section-lift`, `motion-bridge`, `calm-dissolve`, `montage-cut`. Features come from the scores (shot size, camera motion, point of interest, brightness, energy, similarity) and the music sections; the agent's `shot-notes.json` overrides them. Directional slides/wipes follow the measured movement (sign convention pinned by a test).
- **Transitions chosen under the budget:** highest-priority wishes win, at most the share cap, never two neighbours (except the drop and the ending), each name once, family cap, no banned names.
- **`cuts EDL --scores DIR [--beats F] [--notes F] [--write] [--overwrite]`** (dry run by default) and **`plan --smart-transitions --scores DIR [--notes F]`**. The previous positional planner stays the default.
- **EDL `rationale`** (validated by `lint`): `{cut: {type, why}}`; `lint` warns `transition-unmotivated` once rationale is in use and `flash-overuse` for more than one white flash; QA warns `direction-mismatch` and reports a `cut-report` (cut types, transitions with a reason).
- 24 tests in `test_cutlogic.py` (classification, budget, notes override, lint/QA checks, planner-level run).

### Changed
- Opt-in only: without `rationale`, `cuts` or `--smart-transitions` nothing changes.

## [0.9.0] - 2026-10-28 (cut and transition craft, phase 1: knowledge)
### Added
- **`references/cut-craft.md`:** the catalogue of cuts (hard cut on the beat, cut on action, graphic match, eyeline/direction match, smash cut, cutaway, cross-cut, montage, hold, J-cut, L-cut, jump cut) with when/why/avoid and the engine cue for each, the question to ask at every boundary, the 30- and 180-degree rules, reading time, and the short-form hook.
- **`references/transition-craft.md`:** what each transition family says, a decision tree, the direction rule, mood guide, and the honest note that today's overlaps are all 4 frames.
- **`references/footage-reading.md`** and **`shot-notes.json`** (example in `engine/examples/`): how to read the raw clips and record role, subject, camera, movement direction, size, mood and best moment; a cut-plan table before `plan`.
- `SKILL.md` step 2b and the agent pipeline: every cut and transition gets a type and a written reason; a transition that cannot be justified becomes a hard cut; the final report lists the cut types used. Persian guide section on cuts and transitions.

### Changed
- Documentation only; the engine behaves exactly as in 0.8.0. The decision engine, new cut types and evaluation follow in 0.10-0.12 (see the plan in `docs/ROADMAP.md`).

## [0.8.0] - 2026-10-25 (professional-editing arc, advanced items)
### Added
- **Full tempo map + sample-accurate onset (Track C):** `beats` now reports a `tempo_map` (local BPM over the track), `beats_refined` (a variable-spacing grid that follows a speeding-up/slowing-down tempo), and a `drop_refined` time snapped to the exact transient. `plan --follow-tempo` / `need --follow-tempo` place cuts on that grid so late cuts do not drift.
- **Hue-qualified secondary colour correction (Track D):** an EDL `correct.secondary` list ([{center, width, sat, shift, gain}, ...]) adjusts only pixels near a hue (sky / foliage / skin), baked into a small cached LUT and applied before the look.
- **Single quality score (Track F):** every QA report carries `quality_score` (0-100): 100 minus 22 per error and 6 per warning. Shown in the report header and documented in `references/qa-gate.md`.
- Tests for the tempo map, onset refinement, tempo-following layout, secondary correction and the quality score.

### Changed
- `beats.json` gains `tempo_map`, `beats_refined`, `drop_refined`; `correct` accepts a `secondary` list; all additive and opt-in.

### Fixed
- **Windows CI (root cause).** The failure was Windows-only and confined to the PowerShell installer smoke test; every test module and the full suite pass on Windows, and all FFmpeg versions pass on Linux. Three issues were fixed: (1) `install.ps1` used `$ErrorActionPreference = "Stop"` together with a brittle multi-path `New-Item`/`Resolve-Path`, so a single non-terminating condition became a terminating error - the installer is now non-throwing (separate `New-Item` calls, forward-slash paths, `-ErrorAction SilentlyContinue`, and the `doctor` check guarded like `install.sh`'s `|| true` with `$global:LASTEXITCODE = 0`); (2) `install.ps1` resolved the target home from PowerShell's `$HOME`, which does not follow a later `$env:USERPROFILE` change, so it now resolves `$env:USERPROFILE` first (falling back to `$HOME`); (3) the workflow smoke step assigned `$HOME = $fake`, but `$HOME` is read-only in PowerShell and the assignment threw - the step now overrides only `$env:USERPROFILE`. Confirmed by reproducing the full suite locally on FFmpeg 6.1.1, 8.1.3 and 9.0.2 (all green) and by splitting the Windows job into per-step diagnostic jobs that isolated the failure to the `$HOME` assignment.

## [0.7.0] - 2026-10-25 (tracks A-G: editorial intelligence, music structure, colour, reframing)
### Added
- **Editorial measurement (Track A, `vid_editorial.py`):** per-shot shot-size classification (wide/medium/close), camera-motion direction and magnitude (block matching on small grayscale frames), and a saliency centroid - pure Python over FFmpeg-decoded frames, no ML. `score` now stores these as cached fields.
- **Editorial lint + QA (Tracks A/F):** the QA gate (with `--scores`) now reports an `editorial` summary (shot-size mix, cut-on-motion share) and warns on `shot-size-repeat`, `jump-cut`, `screen-direction` (180-degree line) and `pacing-flat`.
- **Story structure / pacing (Track B):** `plan --pacing energy` and `need --pacing energy` hold calm beats and cut fast on strong ones (energy-aware shot lengths) instead of one-shot-per-beat.
- **Music structure (Track C):** `beats` now reports per-beat strength and musical `sections` (intro/build/drop/outro) from a sustained-loudness segmentation; `beats --bpm/--first-beat/--beats-per-bar` already added in 0.6. A music-selection tool (`engine/tools/pick_music.py`) ranks the user's own track library by fit to a brief (never downloads).
- **Colour (Track D):** `color plan --wb skin` - skin-tone-aware white balance that neutralises a cast but never cools the image, protecting warm skin.
- **Auto-reframe (Track E):** `focus: "auto"` on a shot keeps the subject in frame when reframing to another aspect, using the measured saliency centroid instead of a blind centre-crop.
- **The craft rubric (Track G):** `references/editorial-rubric.md` - the agent's decision framework (Murch's priorities, shot grammar, pacing, music structure, correct-then-grade). Architecture notes the engine-measures / agent-judges split and the honest ML boundary.
- Tests: `engine/tests/test_editorial.py` (measurement, editorial lint, sections, pacing, skin WB, music selection, auto-reframe).

### Changed
- `score.json` entries gain editorial fields (shot_size, motion_dx/dy/mag, focus_x/y); older entries are re-measured on the next `score`.
- All new behaviour is opt-in via a flag or an EDL field; the default pipeline is unchanged.

## [0.6.0] - 2026-10-25 (phases 6-12: trust, onboarding, delivery, intelligence, reproducibility)
### Added
- **Delivery and export layer** (`vid_deliver.py`, `references/delivery.md`): `render --delivery reels|tiktok|shorts|stories|youtube|square` sets the format, the -14 LUFS loudness target and the container (`yuv420p`, `+faststart`); `--target-size-mb N` targets a file size with two-pass libx264 (VBR on hardware encoders), treated as an upper bound; `--bitrate KBPS` sets the video bitrate directly. A `delivery_check` (size, aspect, container) is added to the render result.
- **Reproducibility and automation:** `render --manifest` writes `<output>.manifest.json` (engine and FFmpeg versions, SHA-256 of the EDL and output, input sizes/mtimes); `render --bundle` zips the EDL and its sidecars (brief, beats, scores, clip list) for hand-off, never media; `render --json FILE` writes the render result for scripting.
- **Multi-track music (music sections):** `project.music` may be a list of user-supplied tracks, concatenated (equal-loudness) before the usual fades, ducking and two-pass master.
- **Broader hardware support:** hardware H.264 encoder detection for NVENC, VideoToolbox, QSV and VAAPI; `--encoder auto` picks a working one (else libx264) and `doctor` lists them; `--encoder` now accepts `videotoolbox` and `qsv`. A cache garbage-collector (`vid_deliver.gc_cache`) and per-shot progress logging for long renders.
- **Editing intelligence:** `beats --bpm` (tap-tempo / known tempo), `--first-beat` and `--beats-per-bar` (non-4/4 downbeat grids); an evaluation harness (`engine/tools/eval_quality.py`) that reports beat error, pick diversity and restraint on a fixed synthetic corpus so changes are judged by numbers.
- **New capabilities:** reusable text templates (`text templates`: title-card, lower-third, caption, subtitle, location, end-card) usable via a `template` field on a text; three more looks (muted-matte, cool-blue, warm-film).
- **Onboarding & distribution:** a zero-footage sample project (`engine/tools/make_sample.py`), a README quickstart, `docs/ROADMAP.md` (the file the README already linked to), `SUPPORT.md` (platform / FFmpeg support matrix), `docs/QA-PROTOCOL.md` (real-footage manual QA), `docs/RELEASING.md`, and a tag-driven release workflow.
- **CI / trust:** the full render suite now runs on macOS as well as Linux and Windows; a CodeQL workflow; Dependabot for Actions; issue and pull-request templates; `CODEOWNERS`.
- **Security:** a no-network invariant test (static import scan plus a runtime socket guard) codifies the promise that the engine never reaches the network.

### Fixed
- **Cross-FFmpeg robustness:** `blurdetect` prints `-nan` for a textureless frame on FFmpeg 6.x, which crashed the metadata parser (`could not convert string to float: '-'`); all metadata readers now parse through `vidlib.parse_float`, which drops non-finite tokens. Found by running the suite on FFmpeg 6.1.1 (the project was developed on 9.0.2).

### Changed
- `project.music` accepts a list as well as a string; the linter and manifest handle both.
- Test count grew with suites for delivery, reproducibility, security, the sample project and the evaluation harness.

## [0.5.0] - 2026-10-16 (phase 5: hardening, documentation, Persian guide, CI)
### Added
- **Malformed-input handling.** `vid_edl.structure_errors` validates shape, types and magnitudes first; `lint` never raises (a safety net reports `edl-malformed`); `render --force` cannot bypass structural errors; the CLI prints one-line errors with exit code 2 (130 on Ctrl-C) and shows the traceback only with `MH_DEBUG=1`. A seeded mutation fuzzer (`test_fuzz.py`) damages valid EDLs thousands of times.
- **FFmpeg capability checks.** `vidlib.FEATURE_FILTERS`; `doctor` lists which optional features the build supports; `lint` reports `ffmpeg-missing` when an EDL needs a filter the build lacks (libass, libvidstab, libzimg ...). Tests that need such filters skip on minimal builds; `make_hlg_clip` falls back to H.264 without libx265. A test renders a project that uses everything and fails on any filter not covered by `doctor`/`lint`.
- **Tooling:** `engine/tests/run_tests.py` (`--fast`, `-k`, `--durations`), `engine/tests/coverage_report.py` (standard-library line coverage via `sys.monitoring`, Python 3.12+), `engine/tools/gen_cli_doc.py` (generates `docs/CLI.md`).
- **Static compatibility tests** for Python 3.9 (grammar, library names, annotations, f-string rules), a test for control characters in text files, FFmpeg discovery tests, CLI tests (help for every command, exit codes, generated reference up to date).
- **CI:** `.github/workflows/mh-video-editor-skill.yml` (Python 3.9 and 3.13 without FFmpeg; Linux and Windows with FFmpeg and installer smoke tests; coverage). Written and checked structurally; not run by the author because it needs GitHub.
- **Documentation:** `docs/GUIDE.fa.md` (complete Persian guide), `docs/CLI.md`, `docs/TROUBLESHOOTING.md`, `docs/EXAMPLES.md`, rewritten README and CONTRIBUTING, architecture notes on robustness and tests, `CODE_OF_CONDUCT.md`. Every argument of every command now has help text.

### Fixed (found by the new tests)
- **Race condition:** parallel part workers could use a LUT file another worker was still writing ("3D LUT is empty"); LUTs and all JSON files are now written atomically, LUTs are built once behind a lock, and Windows' "Access is denied" during a file swap is retried.
- An interrupted `save_json` (for example `color plan --write`) could truncate the user's EDL.
- `-h` of `color analyze` crashed (`%` in a help text).
- 80 % of randomly damaged EDLs raised raw Python exceptions; non-numeric, non-finite or huge values, wrong container types and missing `shots` are now explained.
- `doctor` mistook the legend of `ffmpeg -filters` for a filter.
- `render` on an EDL with a non-string preset or grade raised `OSError`.

### Changed
- `lint` and `render` check FFmpeg capabilities when files are checked.
- Test count 446 -> 511; line coverage of the engine 96.5 % (standard-library measurement).
- Cached parts and the audio master are reused only if they open and have the expected length (a part cut short by a crash used to be reused; a truncated WAV still claims its full length in the header, so `wav_complete` checks the data chunk).

## [0.4.0] - 2026-10-15 (phase 4: new capabilities)
### Added
- **Colour** (`vid_color.py`, `color looks|lut|analyze|plan|scope`): professional order of operations (input transform, correction, look, finish) in 16-bit RGB; correction by exposure (linear light), black/white points, gamma and per-channel gain; 11 looks (desert-gold, teal-orange, golden-hour, cinematic-soft, film-faded, bleach-bypass, moody-dark, vivid-travel, clean-bright, bw-contrast, neutral) built from ASC CDL, filmic S-curve, split-toning, vibrance, hue shifts and highlight shoulder, baked into a 33^3 `.cube` and applied with tetrahedral `lut3d`; import of your own `.cube` with strength; waveform / vectorscope / histogram sheet; `color plan` measures every shot and proposes explicit, reviewable corrections (exposure matching, median white balance, reference matching).
- **Motion** (`vid_motion.py`): `stabilize` (two-pass vid.stab, `light`/`strong`/custom, cached `.trf`), `speed` as constant or linear ramp with `native` / `mci` / `blend` / `auto` slow-motion modes.
- **Text** (`vid_text.py`): `texts` rendered with libass (HarfBuzz + FriBidi): Persian shaping and right-to-left, Persian digits, ZWNJ, balanced line wrapping, safe areas per format, fonts found by name table (IRANSansFaNum preferred) and passed with `fontsdir`, SRT import, `text preview`.
- **Formats**: `render --format youtube-16x9|square-1x1`, `--all-formats`; reframing `cover` / `blur-fill` / `fit` and `focus`; parts cached per output size.
- **Sound** (`vid_sfx.py`): directional synthesised whooshes with ducking of the music, and the clips' own sound as an ambient bed level-matched to the music; both inside the two-pass loudness master. `sfx plan`.
- QA gate: `exposure-jumps`, `look-consistency`, `content`; `qa`/`render --qa` check the format being rendered. Linter validates all new fields; slow motion counts as a visible effect.
- 200+ new tests (446 in total).

### Changed
- **Default HDR tone mapping is now `hable-203`** (HLG white at 203 nits). v1-v4 used peak 1000 nits, which is darker: set `style.hdr_tonemap: "legacy"` to reproduce an old render exactly.
- Legacy `style.grade` values (preset names, raw filters) keep working.

### Fixed (found while building it)
- Vignette angle inverted (larger angle = darker corners); SAR 481:480 after the zoom crop (assembly failure on real footage); sidechain ducking ended with the whoosh bus and RMS detection never ducked short whooshes; auto-correction was too aggressive on bright desert and clear sky (see `references/pitfalls.md`).

## [0.3.0] - 2026-10-15 (phase 3: automated quality gate)
### Added
- `qa VIDEO [--edl EDL] [--scores DIR] [--strict]` and `render --qa`: one picture pass (luma/chroma/scene score/frame difference at low resolution) and one sound pass (EBU R128 loudness, true peak, silence) feed EDL-aware checks: black and white frames, colour cast per second, frozen stretches, hard-cut timing against the EDL, loudness, true peak, silent gaps, frame count, duration, aspect, fps, file size. Planned fades, the drop flash and deliberate `fadeblack`/`fadewhite` dips are excluded using the EDL. Verdict PASS / PASS WITH WARNINGS / FAIL, exit codes, `*.qa.json`, contact sheet.
- Edit audit inside the gate: effect budget and transition mix (through the linter), cuts on the beat grid, clip quality of the used shots, flagged and look-alike neighbours (with scores).
- Transition probe test: every xfade transition is rendered between two test clips and inspected frame by frame.
- Two-pass audio master (`master_audio`): the final mix (music + riser + boom) is measured and normalised with a linear gain; true peak held at -2 dBTP.

### Fixed (found by the gate on real renders)
- `rectcrop` and `circlecrop` show a pure black frame in the middle of a cut and `squeezev` crashes FFmpeg: banned with reasons (`vidlib.BANNED_REASONS`), removed from the planner vocabulary.
- The riser/boom made the mix 2 LU hotter than the target and a one-pass `loudnorm` on the final mix missed by 3 LU with a +0.1 dBFS peak; replaced by the two-pass master (default target now -14 LUFS, the platform norm).
- The planner could put more than 50% of the cuts on transitions in short projects (drop fade and closing fade were added on top of the budget).
- White-frame detection used a threshold above limited-range white (235): the real v1 bug would have passed.

## [0.2.0] - 2026-10-15 (phase 2: speed and accuracy)
### Added
- `score`, `rank`, `pick`: per-clip sharpness, exposure, contrast, saturation and motion in up to three 1-second windows (one FFmpeg pass each), a best window (`best_start`), quality 0-100, energy percentile, flags (dark, overexposed, blurry, static, flat), perceptual-hash `similar_to`, and an incremental cache keyed by file size and mtime. `pick` orders a candidate list as calm intro, rising build, high-energy payoff, calm ending.
- `plan --scores` fills start times from best windows; `plan --bar-align` starts the body on a downbeat.
- Beat validation block in `beats.json`: independent inter-onset tempo estimate with its relation (same/double/half/different), tempo drift between halves, median onset error on the 16th grid, downbeats and bar confidence, with human-readable notes. `beats` prints whether the drop sits on a downbeat.
- Render report with `parts_seconds` / `assemble_seconds`; `--workers`, `--hwdecode auto|none` (CUDA decode with automatic software retry); `MH_HWACCEL` override.
- Cached, parallel ffprobe (`vidlib.probe`, `probe_many`).

- `rank` and `pick` accept `--only` / `--exclude` index lists (`0-40,52,100-120`): measured energy cannot tell an off-road jump from a hand-held close-up, so the agent restricts the pool using the contact sheets.

### Changed (speed)
- 28 s / 39-shot Maranjab project: assembly 40 s -> 6 s; warm-cache preview 53 s -> 9 s; cold preview 82 s -> 38 s; cold final 203 s -> 67 s. Causes fixed: a per-pixel expression blend over the whole video (now a fading source + FFmpeg's built-in screen blend), a global RGB-split filter (now applied inside the single part that carries the drop), serial probing in lint and render.

### Fixed
- Black or featureless clips were "unmeasurable" instead of flagged dark.
- The drift check now searches +-8% (a tempo ramp was invisible at +-1%); relation(double/half) semantics corrected.

## [0.1.0] - 2026-10-14 (phase 1: foundation)
### Added
- `SKILL.md` router with a discovery gate, ordered conflict rules and a five-step pipeline (probe, analyse, plan, render, verify).
- Engine (Python standard library + FFmpeg): `doctor`, `probe`, `sheets`, `beats`, `need`, `plan`, `lint`, `render`, `verify` behind one CLI (`engine/scripts/mh_video.py`).
- Beat engine: tempo + phase fit, half/double alternatives, confidence, loudness-step "drop" snapped to the beat grid.
- EDL (edit decision list) as the single source of truth, with a linter for the restraint budget (<= 3 effects, <= 50% transitions, each transition type once, look-alike families capped, banned transitions).
- Renderer: cached per-shot parts, one filter graph (xfade for transitions, concat for hard cuts), exact frame count, HLG/PQ tone-mapping, rotation handling, NVENC when available, preview mode, silent mode.
- `mh-video-editor` agent that refuses to start without a confirmed `video-brief.md`.
- References: discovery interview and brief template, effects/transitions budget, pitfalls, EDL format.
- 120+ tests on synthetic media (no real footage needed): unit, end-to-end, robustness (Persian/space paths, mixed fps, short clips, mp3, no drop, cache invalidation, corrupt files) and repo-consistency tests.
- Installers (`install.ps1`, `install.sh`).

### Fixed (bugs found while building from the Maranjab project)
- Flash painted the first frames white (`fade start_frame`), purple cast (yuv blend), 400 MB output (temporal grain), zoom-out crash, `zoomin` flat frame, odd preview sizes, false "drop" at the start of a track, short clips freezing for only 1 s, contact sheets all showing the last chunk, CRLF line endings.

### Verified
- The approved Maranjab v4 was rebuilt from the engine (39 shots, 18 transitions, 28.235 s, 847 frames, 51 MB, NVENC) and passed `verify`.
