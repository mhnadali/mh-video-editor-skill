# Changelog

All notable changes to this project. Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: [SemVer](https://semver.org/).

## [0.21.0] - 2026-10-29 (phase E: speed and scale, measured)
### Changed
- **Review proxies are faster and reused.** Proxies decode without the deblocking filter and the B-frames (21-32 % faster on real 4K clips; they are only for choosing) and hold 14 s instead of 24 s; the window is in the file name, so a page made again after `story feedback` only makes proxies for new clips. The real 35-shot page: 291 s -> 157 s, a rebuild 13 s.
- **Time left** in `score` progress and live progress (with time left) from `vision run`, which used to print nothing until it finished.

### Added
- `score --min-duration SECONDS`: clips shorter than that are not measured (5 = the selection's own rule; 11 % of the first real trip).

### Measured and not changed
- GPU decoding (d3d11va, dxva2, CUDA) was slower than software decoding on the test PC, so it is not the default. Scoring and `story refine` keep full decoding because their measurements (sharpness, motion) would change.

## [0.20.0] - 2026-10-29 (phase D: image quality, measured on real footage)
### Added
- **`color cameras`** (`vid_camera.py`): matches the colour of the cameras of one trip to each other, estimated only from clips of two cameras filmed within a minute of each other, kept only with enough pairs and above its uncertainty, and anchored on the camera that filmed most (`--reference` to choose). Out of sample on the first real trip the camera difference fell from 0.135 to 0.102. `--edl EDL --write` multiplies the gains into the shots' corrections once; `color plan` then leaves white balance alone by default and keeps the camera gain on `--overwrite`.
- **`plan --stabilize-shaky [SHAKE]`** (with `--scores`): light stabilisation for picks measured shaky. On the shakiest real clip it cut the jitter by 31 %.
- **`lint` `slowmo-invented-frames`:** slow motion that needs more frames than the clip has.

### Measured and not built
- Reframing 16:9 to 9:16: 468 of 486 real clips were already vertical. Slow-motion suggestions: no clip was above 30 fps. Sound balance: it needs listening.

## [0.19.0] - 2026-10-28 (phase C: the review experience)
### Added
- **`story review` plays the clips.** Next to the page it makes a small proxy video of every shown clip (`<page>_media/`: 240 p, no sound, at most 24 s starting 6 s before the shot, about 90 KB each on the first real trip) and shows a player under the frame strip, already at the shot's start. **Start here** uses the frame on screen as the new start (`story feedback` applies `{"action": "start", "slot", "clip", "start"}`; a start meant for a clip that was replaced in the same round is ignored). `--no-video` keeps the page a single self-contained file.
- **A timeline of the whole edit** at the top (each slot as wide as its shot, coloured by chapter, a click jumps to the slot), a **"What was left out, and why"** section (the exclusion reasons and the same-moment groups), **auto-save** in the browser (closing the page loses nothing), **Load decisions** (continue from an exported file) and **Start over**.
- `story review --workers`. Frame extraction and proxies run in parallel.

### Changed
- `story feedback` applies replacements first, then keeps and starts, then bans.

## [0.18.0] - 2026-10-28 (phase B: content understanding, measured)
Every change below was measured against clips labelled by eye on the first real trip; what failed the bar was left out (see `docs/CALIBRATION.md`).

### Added
- **YuNet face detector (optional, offline).** `python plugins/vision/setup.py --model` downloads the 232 KB MIT-licensed model once (SHA-256 checked) into `plugins/vision/models/` (git-ignored); `vision run` uses it automatically, else the cascades. On 60 labelled clips: `person` precision 0.83 / recall 0.71 (cascades: 0.34 / 0.79), `person_sure` (a face in 2+ frames, not tiny) precision 1.0 / recall 0.71. The engine reads the raw counts, so rules can change without running the plug-in again; each detector has its own certainty (YuNet 0.6 / 0.9, cascades 0.25 / 0.6).
- **`story audit` `drop-weak`:** the shot that starts on the drop must be a strong picture (energy or role action).

### Changed
- **Shot size is measured differently.** The old rule (detail in the centre means a close-up) agreed with 174 hand-labelled shots only 21 % of the time (the most common label alone: 42 %); the new one (little fine detail means wide, otherwise medium) agrees 63 %. A close-up now comes from a large, certain face (or from the notes). `score` repairs existing entries with a small decode (`SCHEMA` 5).

### Measured and not built
- Suggesting `shot-notes.json` from the measurements: role 52 % and mood 49 % in cross-validation against 174 hand-written notes (guessing the most common label: 35 % / 48 %). The agent must look at the frames.
- Speech / laughter detection: it cannot be checked without listening, so it is not shipped.

## [0.17.0] - 2026-10-28 (phase A: validation on real footage)
First run of the 0.16 selection on a whole real library (Kavir trip: 486 clips, 3 cameras, 2 days). Measured against clips labelled by looking at them; the numbers are in `docs/CALIBRATION.md`.

### Fixed
- **The three picture hashes of every clip were one frame repeated** (an FFmpeg `select` filter let the muxer fill the gaps with duplicates; all 462 real clips had three identical hashes), so "2 of 3 frames must match" was a one-frame test. The window is now decoded fully and three frames are chosen in Python. Cached scores are repaired by `score` with a small decode (`top_up`: the hashes and the clip's sound spikes) instead of a full measurement; `SCHEMA` is 4.
- **Same-moment strictness:** the brightness tolerance for a strict match is 10 (was 30, which joined two cameras filming one drive). On 20 labelled real pairs the strict rule went from precision 0.89 to 1.0.

### Added
- **Look-alike flags.** A looser reading of "same moment" never drops a clip; it only warns when two chosen shots look alike (`warnings`, `lookalike` on each row, a line on the review page). Desert footage is self-similar and cheap image features cannot tell a duplicate from a second shot of the same drive.
- **People certainty.** The classic face detector measured 40 % precision (60 % for a face in 3+ frames) on 60 labelled clips, far below the first estimate. A sighting by the plug-in now counts as 0.6 (`person_sure`, a face in 3+ frames) or 0.25 of a person in the people bonus, the notes of the agent count fully, and the storyboard reports how many shots with people are confirmed by notes. The rule is read from the raw counts in `vision.json`, so it can change without running the plug-in again.
- **`engine/tools/calibrate_selection.py`:** precision / recall of the same-moment rules and the people detector against your labels, plus a sweep of settings that never call a different pair "same". `docs/CALIBRATION.md` explains the procedure and records the first trip's numbers.
- `story review` extracts the frames in parallel.

## [0.16.1] - 2026-10-28 (deep review of the selection depth)
A line-by-line review of 0.16.0, a randomized check of the selection rules (thousands of random libraries, chapters, variants and profiles) and an end-to-end run on real clips. Every fix has a test (`test_deep_review.py`).

### Fixed
- **A clip the user insisted on (`must`) that was shorter than 5 s was still used.** `short`, `skipped`, `copy` and `banned` are never overridden.
- **Chapters could be given more shots than they had usable clips**, leaving holes in the storyboard while other chapters had spare clips. The slots now go to chapters that can fill them; when the whole library is too small each chapter gives all it has (and the storyboard says so).
- **The action peak of a clip:** on a clip where nothing moves it sat on frame 0 and pulled action slots to the start; a cut or flash (and the frames next to it after smoothing) could be taken for the peak. `peak_at` is now empty for a still clip.
- **Remembered taste:** a clip the user chose in place of a better-measured take of the same moment lost to it on the next `story select` (now the user's clip is pinned and preferred); a replacement could duplicate a clip already in the story, be shorter than 5 s or be banned (now refused); a replacement kept the old clip's moment analysis; unknown slots or clips were silently ignored; a banned clip stayed in the storyboard (now the first usable alternate takes its place, or the slot is marked and left out of the clip list); bans were not applied when the storyboard came from an earlier round; replacing and banning in one batch taught the engine to dislike the replacement; paths with different slashes did not match; a hand-edited `profile.json` was not checked.
- **`vision.json` was tied only to the clip's index**, which changes when clips are added and the inventory is made again: people could be attached to the wrong clips. Results now carry the file's path, size and time, are ignored for another file, and are reused when nothing changed (`vision run --force` looks again).
- **Score cache:** reuse no longer depends on a list of field names but on a schema number, so adding a measurement later cannot silently keep or re-measure everything.
- Clips without a capture time no longer break the storyboard, clip list and chapter names.
- **Review page:** keep and replace of the same slot no longer both exported; a banned alternate can no longer be chosen; kept slots stay marked; `<!--` in a file name cannot break the page. The page logic is now tested in Node (skipped when Node is missing).

## [0.16.0] - 2026-10-28 (selection depth)
Why: selection is the skill's most important job. The first story-first reel still used clips shorter than 5 s, two takes of the same moment, and chose shot by shot without looking at how neighbours fit.

### Added
- **Hard filters** (`vid_filter.py`, `story exclusions`): clips under 5 s, dark, blurry, shaky, covered, with an internal cut or low quality are left out with a reason code; `pick`/`rank` also skip short clips (`--min-duration`, `--keep-repeats`).
- **Same-moment clusters:** copies of one moment (same camera, or two cameras) are found from three window hashes, colours and light, and only the best take is kept.
- **Usable-moment extraction** (`vid_moment.py`, `story refine`): the best segment inside each chosen clip, with the motion type (static, pan, tilt, push/pull, hand-held) and the end of the action.
- **Sequence optimiser:** a beam search over the whole edit with pair costs (size, light, direction, colour, topic, camera); three variants (balanced, action, people); `--method greedy` keeps the 0.15 behaviour. A long single chronological chapter no longer strands the search (every clip is offered when the favourites fail).
- **Optional offline vision plug-in** (`plugins/vision`, own virtual environment, OpenCV cascades; `vision run|status`): faces, people; the engine stays standard-library only. `score` also measures sound spikes (`transient`) and shake, texture, per-window hashes.
- **Remembered taste** (`vid_profile.py`, `profile.json`): banned, favoured and pinned clips, capped topic / role biases and weight overrides, learned only from explicit decisions; `story select --profile / --no-profile`, `story feedback`, `story profile`.
- **`story review`:** one offline HTML page with three-frame strips, alternates, keep / replace / ban, the energy curve against the music, and an export for `story feedback`.
- **`engine/tools/eval_selection.py`:** a synthetic library with a known truth and floors for short clips, duplicates, best take, order, fill, coverage, distinct pictures, energy fit and people share.

## [0.15.0] - 2026-10-28 (story-first selection)
Why: the first reel's shots were picked by eye from one thumbnail each. Audited with numbers: half of the shots came from the block that held half of the footage, a whole morning of the second day had none, the loudest music carried a plate of food and the calmest picture sat on the quiet music, only 7 of 35 shots had people, the Osmo was 17 % of the edit and 30 % of the library, and the user never saw a plan.

### Added
- **`story chapters | select | audit`** (`vid_story.py`): draft chapters from the capture timeline; slots from the beat layout with the music's energy; a dynamic programme shares the slots between the chapters so that peak chapters sit on loud music; clips are picked per chapter by a score (quality, energy fit, own sound, people, wanted role, under-used camera, spread over time) with penalties for the same scene, the same topic and one dominant shot size; action slots start on the clip's most moving window; every shot has a reason and two alternates; a teaser hook is optional (the trip's order is the default). Output: `storyboard.json`, `storyboard.md` and a clip list.
- **`story audit EDL`**: `trip-order`, `chapter-missing`, `chapter-balance`, `camera-underused`, `few-people`, `topic-run`, `energy-mismatch`, `energy-curve`, `short-reading-time`.
- **`sheets --only RANGE --frames 3`**: start, middle and end of every clip side by side, for a chosen range of the inventory.
- `score` keeps every measured window (`wins`) and the clip's own sound level (`audio_db`, `sound` percentile); older cached scores are re-measured.
- `references/story-selection.md`; discovery asks for the story (never for text); the skill and the agent require a storyboard the user approves before `plan` (an autonomous agent stops with `NEEDS_APPROVAL`).

## [0.14.0] - 2026-10-28 (quality, natural colour, no text, speed: from the first reel's review)
### Changed
- **Final renders are `--quality high` by default** (`standard` | `high` | `max`): the cached parts are encoded at CRF 12 and the assembled video at CRF 16 with the medium preset (was 16 and 18, slow): about 70 Mbps at 4K, while the sources are 43-46 Mbps, audio AAC 256k, so the second encode no longer compounds the first; Lanczos resampling for the cover reframe and the zoom. Previews stay light. 4K presets `reels-9x16-4k` (2160x3840) and `youtube-16x9-4k` for 4K sources (1080p output of 4K footage was judged "very compressed").
- **Natural colour by default:** `style.grade` and `plan --grade` default to `neutral`. The skill and the agent no longer use a warm look or the end flare unless asked, and warn against neutralising sunsets and firelight with `color plan --wb`.
- **No on-screen text by default:** discovery no longer asks about text, the agent adds none; text stays available only when the user asks for it unprompted.

### Faster analysis (same results)
- `score` decodes each window once: exposure, motion, sharpness, the 9x8 hash frame and the tiny gray and RGB frames for the editorial and colour measurements all come from the same FFmpeg pass (it used to decode the best window three more times). 40 real 4K clips: 91 s -> 42 s (2.2x) with identical best windows and quality, the same shot size on 37 of 40 clips, hue within 0.002; decoder threads capped at 2 with 8 workers.
- Contact sheets decode only key frames after the seek: 4x faster (60 clips: 13.9 s -> 3.5 s).

## [0.13.0] - 2026-10-28 (first real trip: capture time, chronological inventory, fixes found on real footage)
### Added
- **Capture time:** `probe` reads the device and the capture time (Apple local time with its offset, or the container's UTC time), builds one comparable timeline across cameras, writes `timeline.csv` (gap to the previous clip) and **numbers the inventory in capture order**. `--utc-offset` and repeatable `--clock-shift DEVICE=MINUTES` correct a camera with a wrong clock or time zone (found on a trip: the Osmo Pocket 3 was 30 minutes off; the clips of the same breakfast only interleaved once corrected).
- `need` / `plan --body-start SECONDS`: start the beat-by-beat body at a chosen time, for example at the music's drop.
- `qa --notes shot-notes.json`: the agent's shot sizes and movement override the measurements in the editorial checks.

### Fixed (found by running the engine on 486 real clips)
- **`qa` crashed with `KeyError: 'id'`** whenever an editorial warning existed (editorial checks were keyed by `code`, the report prints `id`); never seen in CI because synthetic clips produced no editorial warning.
- **A drop that opens the body (`drop_beat` 0) got no flash**; the planner required the drop to be at least one beat into the body.
- **`graphic-match` fired on 19 of 34 cuts** because most points of interest are near the middle of the frame; it now needs one point clearly off-centre. **`colour-match`** is switched off when most shots share one dominant colour (a desert).
- Regression tests for each.

## [0.12.1] - 2026-10-28 (debug pass over 0.9-0.12)
### Fixed
- **QA false errors on long dips:** the black/white frames of a `fadeblack` / `fadewhite` with a longer `transition_frames` were reported as unplanned (the expected window was fixed at +-0.15 s); it now follows the real overlap.
- **Budget:** in a short video the drop flash and the closing fade together could exceed the 50 % transition share (lint `transition-share`); the closing fade now respects the ceiling. Transitions already in the EDL are kept by `cuts`, count against the budget and their names are not proposed again (`--overwrite` replaces them); the table shows them as `[name]`.
- **`internal-cut` false positives:** a fixed threshold would flag fast pans and whip pans as cuts. Detection is now relative (one step far larger than the rest); `flux` stays as information.
- `plan --smart-transitions --no-transitions` contradicted itself silently; it is now an error.
- With no measurements at all, a boundary no longer claims "no stronger relation"; it says there are no measurements.
- Regression tests for each of these (`Regressions`, `QaWindows`, `internal_cut`).

### Known limits (unchanged, documented)
- A shot whose `src_start` is closer to the start of its clip than half of the overlap reads from the beginning of the clip, so the content at the cut can be shifted by up to the overlap (at most 0.4 s).
- `cuts --align` reads the motion of the whole clip window, not the exact last frames of the previous shot.

## [0.12.0] - 2026-10-28 (cut and transition craft, phase 4: measurement)
### Added
- **Cheap content features, no ML** (`vid_editorial.py`, stored on the score entries): dominant hue and colourfulness, `local_motion` (a subject moving over the background, after cancelling the camera's motion) and `flux` (abrupt change inside the chosen window = a cut or flash in the source).
- **`content_change(a, b)`** in `vid_cutlogic.py` (0 = same scene, 1 = a different world: picture hash, light, saturation, hue, shot size); used for the calm-dissolve choice. A new cut type `colour-match`: two different places that share a dominant colour.
- **QA warning `internal-cut`** for a kept shot whose window contains an abrupt change.
- **Cut-quality metrics** in `engine/tools/eval_quality.py` (`cuts`): reason coverage, variety of cut types and transition families, agreement of directional transitions with the measured movement, risky cuts, hard-cut share; a pure test on synthetic scores.
- Docs: the honest ML boundary in `docs/ARCHITECTURE.md`, a by-eye "Cuts" checklist in `docs/QA-PROTOCOL.md`.

### Changed
- Cached scores from before this version are re-measured on the next `score` (they lack the new fields); the measurement adds one small RGB decode per clip.

## [0.11.0] - 2026-10-28 (cut and transition craft, phase 3: cut types in the renderer)
### Added
- **Per-transition duration:** EDL `transition_frames` (`{"cut": even frames 2-24}`, default 4). The overlap is split evenly over the two shots, the cut stays centred and the assembled length still equals the project length (checked on random layouts). `lint` errors when a shot cannot hold its overlaps and warns for a key with no transition. The decision engine gives the closing fade and calm dissolves 8 frames, section lifts 6, and shrinks overlaps that short shots cannot hold.
- **J-cut / L-cut for clip sound:** `shots[i].audio.lead` / `lag` (0-1 s) with `style.ambient`; the lead is limited by the start of the video and of the source clip; retimed shots are untouched; the audio cache key changes only when they are used.
- **Match on action:** `cuts --align` measures windows of each clip and proposes a `src_start` that continues the previous shot's movement; `--write` stores it.
- Tests in `test_cut_types.py` (pure timing model, lint, lead/lag, alignment) and one end-to-end render with real media (runs where FFmpeg exists).

### Changed
- Opt-in only: without `transition_frames`, `lead`/`lag` or `--align` the render is identical to 0.10.0.

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
