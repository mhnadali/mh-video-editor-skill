# Command reference

Generated from the program by `python engine/tools/gen_cli_doc.py`; do not edit by hand. Every command is run as
`python "<skill-dir>/engine/scripts/mh_video.py" <command> ...`.

Exit codes: 0 = success; 1 = a check failed (QA FAIL, lint errors, verify failed); 2 = bad input or an engine error (one-line message on stderr); 130 = interrupted. Set `MH_DEBUG=1` to get the full traceback for unexpected errors.

| Command | What it does |
|---|---|
| [`doctor`](#doctor) | check FFmpeg, filters, encoders and optional features |
| [`probe`](#probe) | inventory a footage folder (duration, rotation, fps, HDR, duplicates) |
| [`sheets`](#sheets) | numbered contact sheets of the inventory (look at them to choose shots) |
| [`score`](#score) | measure sharpness, exposure and motion of every clip; best window; same-scene hashes |
| [`rank`](#rank) | table of the best (or most energetic) clips |
| [`pick`](#pick) | story-ordered candidate clip list (calm intro, build, payoff, calm end) |
| [`beats`](#beats) | beat grid, tempo alternatives, downbeats and the drop of a music file |
| [`need`](#need) | how many clips a beat grid needs |
| [`plan`](#plan) | beat grid + ordered clip list -> EDL |
| [`lint`](#lint) | check an EDL: structure, restraint budget, files, FFmpeg capabilities |
| [`render`](#render) | render the video (cached parts; --preview for half size) |
| [`qa`](#qa) | quality gate on a rendered video: defects, loudness, cut timing, effect audit |
| [`verify`](#verify) | quick stream/duration/size checks plus a contact sheet of the result |
| [`color`](#color) | colour correction and grading tools |
| [`text`](#text) | on-screen text (Persian right-to-left supported) |
| [`sfx`](#sfx) | sound design and ambient bed |

## `doctor`

Check FFmpeg, filters, encoders and optional features.

```
mh_video doctor [-h]
```

## `probe`

Inventory a footage folder (duration, rotation, fps, HDR, duplicates).

```
mh_video probe [-h] --work WORK [--recursive] folder
```

| Argument | Required / default | Meaning |
|---|---|---|
| `folder` | required | folder with the footage |
| `--work WORK` | required | work folder (inventory, scores, sheets, beats live here) |
| `--recursive` |  | also scan sub-folders |

## `sheets`

Numbered contact sheets of the inventory (look at them to choose shots).

```
mh_video sheets [-h] --work WORK [--size SIZE] [--cols COLS] [--rows ROWS] [--pos POS]
```

| Argument | Required / default | Meaning |
|---|---|---|
| `--work WORK` | required | work folder (inventory, scores, sheets, beats live here) |
| `--size SIZE` | default `200` | thumbnail width in pixels |
| `--cols COLS` | default `8` | thumbnails per row |
| `--rows ROWS` | default `5` | rows per sheet |
| `--pos POS` | default `0.4` | position in each clip (0-1) of the frame shown |

## `score`

Measure sharpness, exposure and motion of every clip; best window; same-scene hashes.

```
mh_video score [-h] --work WORK [--workers WORKERS] [--limit LIMIT] [--rescore]
```

| Argument | Required / default | Meaning |
|---|---|---|
| `--work WORK` | required | work folder containing inventory.json |
| `--workers WORKERS` |  | parallel workers (default: min(4, CPUs)) |
| `--limit LIMIT` |  | measure only the first N clips |
| `--rescore` |  | ignore earlier scores (default: reuse unchanged files) |

## `rank`

Table of the best (or most energetic) clips.

```
mh_video rank [-h] --work WORK [--by {quality,energy}] [--top TOP] [--allow-flagged] [--allow-similar] [--only ONLY] [--exclude EXCLUDE]
```

| Argument | Required / default | Meaning |
|---|---|---|
| `--work WORK` | required | work folder (inventory, scores, sheets, beats live here) |
| `--by BY` | default `quality`; one of `quality`, `energy` | what to sort by |
| `--top TOP` | default `30` | how many rows to print |
| `--allow-flagged` |  | include clips flagged dark / blurry / static ... |
| `--allow-similar` |  | include look-alike clips |
| `--only ONLY` |  | restrict to inventory indexes, e.g. 100-330,420-445 (choose them from the contact sheets) |
| `--exclude EXCLUDE` |  | drop inventory indexes, e.g. 12,40-52 |

## `pick`

Story-ordered candidate clip list (calm intro, build, payoff, calm end).

```
mh_video pick [-h] --work WORK --count COUNT --out OUT [--intro-shots INTRO_SHOTS] [--beats BEATS] [--payoff-share PAYOFF_SHARE] [--allow-flagged] [--allow-similar] [--only ONLY] [--exclude EXCLUDE]
```

| Argument | Required / default | Meaning |
|---|---|---|
| `--work WORK` | required | work folder (inventory, scores, sheets, beats live here) |
| `--count COUNT` | required | how many clips to pick |
| `--out OUT` | required | clip list file to write |
| `--intro-shots INTRO_SHOTS` | default `2` | shots before the beat grid starts |
| `--beats BEATS` |  | beats.json: payoff share follows the drop |
| `--payoff-share PAYOFF_SHARE` | default `0.5` | share of the picks reserved for the payoff after the drop |
| `--allow-flagged` |  | include clips flagged dark / blurry / static ... |
| `--allow-similar` |  | include look-alike clips |
| `--only ONLY` |  | restrict to inventory indexes, e.g. 100-330,420-445 (choose them from the contact sheets) |
| `--exclude EXCLUDE` |  | drop inventory indexes, e.g. 12,40-52 |

## `beats`

Beat grid, tempo alternatives, downbeats and the drop of a music file.

```
mh_video beats [-h] [--out OUT] [--max-seconds MAX_SECONDS] [--bpm BPM] [--beats-per-bar BEATS_PER_BAR] [--first-beat FIRST_BEAT] audio
```

| Argument | Required / default | Meaning |
|---|---|---|
| `audio` | required | music file (mp3, wav, m4a ...) |
| `--out OUT` |  | write the full analysis to this beats.json |
| `--max-seconds MAX_SECONDS` |  | analyse only the first N seconds |
| `--bpm BPM` |  | force this tempo instead of estimating it (tap-tempo / known BPM) |
| `--beats-per-bar BEATS_PER_BAR` | default `4` | beats per bar for the downbeat grid (4 = common time, 3 = waltz) |
| `--first-beat FIRST_BEAT` |  | time in seconds of the first beat (overrides the detected phase) |

## `need`

How many clips a beat grid needs.

```
mh_video need [-h] [--duration DURATION] [--period PERIOD] [--intro-shots INTRO_SHOTS] beats
```

| Argument | Required / default | Meaning |
|---|---|---|
| `beats` | required | beats.json from the `beats` command |
| `--duration DURATION` |  | override the video duration in seconds |
| `--period PERIOD` |  | override the beat period in seconds |
| `--intro-shots INTRO_SHOTS` | default `2` | shots before the beat grid starts |

## `plan`

Beat grid + ordered clip list -> EDL.

```
mh_video plan [-h] --beats BEATS --clips CLIPS --out OUT [--name NAME] [--music MUSIC] [--preset PRESET] [--duration DURATION] [--period PERIOD] [--intro-shots INTRO_SHOTS] [--grade GRADE] [--no-transitions] [--bar-align] [--scores SCORES]
```

| Argument | Required / default | Meaning |
|---|---|---|
| `--beats BEATS` | required | beats.json from the `beats` command |
| `--clips CLIPS` | required | text file: one clip path per line, optional ` @ seconds` |
| `--out OUT` | required | EDL file to write |
| `--name NAME` | default `video` | project name (also the output file name) |
| `--music MUSIC` |  | music file written into the EDL |
| `--preset PRESET` | default `reels-9x16` | output format preset (reels-9x16, youtube-16x9, square-1x1) |
| `--duration DURATION` |  | override the video duration in seconds |
| `--period PERIOD` |  | override the beat period in seconds |
| `--intro-shots INTRO_SHOTS` | default `2` | shots before the beat grid starts |
| `--grade GRADE` | default `warm-desert` | look or preset name |
| `--no-transitions` |  | hard cuts only |
| `--bar-align` |  | start the body on a downbeat (needs bar_phase in beats.json) |
| `--scores SCORES` |  | work folder containing scores.json: fill src_start from each clip's best window |

## `lint`

Check an EDL: structure, restraint budget, files, FFmpeg capabilities.

```
mh_video lint [-h] [--no-files] edl
```

| Argument | Required / default | Meaning |
|---|---|---|
| `edl` | required | the *.edl.json file |
| `--no-files` |  | skip the file checks (offline lint) |

## `render`

Render the video (cached parts; --preview for half size).

```
mh_video render [-h] [--out OUT] [--preview] [--encoder {auto,libx264,nvenc,videotoolbox,qsv}] [--work WORK] [--no-cache] [--force] [--workers WORKERS] [--hwdecode {auto,none}] [--qa] [--format FORMAT] [--all-formats] [--delivery {reels,tiktok,shorts,stories,youtube,square}] [--target-size-mb TARGET_SIZE_MB] [--bitrate BITRATE] [--manifest] [--bundle] [--json JSON] edl
```

| Argument | Required / default | Meaning |
|---|---|---|
| `edl` | required | the *.edl.json file |
| `--out OUT` |  | output video (default: next to the EDL) |
| `--preview` |  | render at half size |
| `--encoder ENCODER` | default `auto`; one of `auto`, `libx264`, `nvenc`, `videotoolbox`, `qsv` | video encoder (auto uses NVENC when available) |
| `--work WORK` |  | work folder for the cached parts |
| `--no-cache` |  | re-render every part |
| `--force` |  | render despite lint errors |
| `--workers WORKERS` |  | parallel shot renders (default: min(4, CPUs)) |
| `--hwdecode HWDECODE` | default `auto`; one of `auto`, `none` | GPU decoding of source clips (auto = CUDA when available) |
| `--qa` |  | run the quality gate on the result (final renders) |
| `--format FORMAT` |  | render in another format preset, e.g. youtube-16x9 or square-1x1 (reframed with blur-fill) |
| `--all-formats` |  | render reels-9x16, youtube-16x9 and square-1x1 from the same EDL |
| `--delivery DELIVERY` | one of `reels`, `tiktok`, `shorts`, `stories`, `youtube`, `square` | platform delivery preset (sets the format, loudness target and container for that platform) |
| `--target-size-mb TARGET_SIZE_MB` |  | aim for this output file size in MB (two-pass bitrate on libx264) |
| `--bitrate BITRATE` |  | target video bitrate in kbit/s (overrides constant-quality encoding) |
| `--manifest` |  | write a reproducibility manifest (versions, hashes) next to the output |
| `--bundle` |  | zip the EDL, brief, beats and scores for hand-off (no media) |
| `--json JSON` |  | also write the render result (paths, sizes, verdict) to this JSON file |

## `qa`

Quality gate on a rendered video: defects, loudness, cut timing, effect audit.

```
mh_video qa [-h] [--edl EDL] [--scores SCORES] [--json JSON] [--sheet SHEET] [--strict] video
```

| Argument | Required / default | Meaning |
|---|---|---|
| `video` | required | the rendered video |
| `--edl EDL` |  | the EDL the video was rendered from (lets the gate excuse planned effects) |
| `--scores SCORES` |  | work folder with scores.json (audits clip quality) |
| `--json JSON` |  | where to write the report (default: next to the video) |
| `--sheet SHEET` |  | contact sheet file |
| `--strict` |  | exit 1 on warnings as well |

## `verify`

Quick stream/duration/size checks plus a contact sheet of the result.

```
mh_video verify [-h] [--sheet SHEET] edl video
```

| Argument | Required / default | Meaning |
|---|---|---|
| `edl` | required | the *.edl.json file |
| `video` | required | the rendered video |
| `--sheet SHEET` |  | contact sheet file |

## `color`

Colour correction and grading tools.

```
mh_video color [-h] {looks,lut,scope,analyze,plan} ...
```

### `color looks`

List the built-in looks.

```
mh_video color looks [-h]
```

### `color lut`

Export a look as a 3D .cube LUT.

```
mh_video color lut [-h] --look LOOK --out OUT [--strength STRENGTH] [--size SIZE]
```

| Argument | Required / default | Meaning |
|---|---|---|
| `--look LOOK` | required | look name (`color looks`) |
| `--out OUT` | required | .cube file to write |
| `--strength STRENGTH` | default `1.0` | how strongly the look is applied, 0-1 |
| `--size SIZE` | default `33` | LUT size (33 = standard) |

### `color scope`

Waveform / vectorscope / histogram sheet of a frame.

```
mh_video color scope [-h] --out OUT [--at AT] source
```

| Argument | Required / default | Meaning |
|---|---|---|
| `source` | required | video file |
| `--out OUT` | required | image file to write |
| `--at AT` |  | time in seconds |

### `color analyze`

Measure one frame and show what a look would do.

```
mh_video color analyze [-h] [--at AT] [--strength STRENGTH] [--scope SCOPE] source
```

| Argument | Required / default | Meaning |
|---|---|---|
| `source` | required | video file |
| `--at AT` |  | time in seconds (default: 40% into the clip) |
| `--strength STRENGTH` | default `0.8` | how strongly the look is applied, 0-1 |
| `--scope SCOPE` |  | also write a waveform/vectorscope/histogram sheet here |

### `color plan`

Propose per-shot corrections for an EDL (dry run unless --write).

```
mh_video color plan [-h] [--match {exposure,reference,none}] [--reference REFERENCE] [--strength STRENGTH] [--wb {off,median,gray-world}] [--only ONLY] [--write] [--overwrite] edl
```

| Argument | Required / default | Meaning |
|---|---|---|
| `edl` | required | the *.edl.json file |
| `--match MATCH` | default `exposure`; one of `exposure`, `reference`, `none` | how shots are matched to each other |
| `--reference REFERENCE` |  | shot number to match (with --match reference) |
| `--strength STRENGTH` | default `0.8` | how strongly the look is applied, 0-1 |
| `--wb WB` | one of `off`, `median`, `gray-world` | white balance: median (default with --match exposure), gray-world (opt-in), off |
| `--only ONLY` |  | shot numbers, e.g. 0-5,9 |
| `--write` |  | store the proposal in the EDL (otherwise only print it) |
| `--overwrite` |  | replace corrections that are already in the EDL |

## `text`

On-screen text (Persian right-to-left supported).

```
mh_video text [-h] {fonts,templates,import-srt,preview} ...
```

### `text fonts`

List the fonts the engine will use.

```
mh_video text fonts [-h]
```

### `text templates`

List the reusable text templates (title-card, lower-third, caption ...).

```
mh_video text templates [-h]
```

### `text import-srt`

Append the cues of an SRT file to the EDL as texts.

```
mh_video text import-srt [-h] [--position POSITION] [--style STYLE] [--animation ANIMATION] edl srt
```

| Argument | Required / default | Meaning |
|---|---|---|
| `edl` | required | the *.edl.json file |
| `srt` | required | SRT subtitle file |
| `--position POSITION` | default `bottom` | where the text sits (title, top, center, bottom, lower-third) |
| `--style STYLE` | default `caption` | text style (title, caption, subtitle) |
| `--animation ANIMATION` | default `fade` | fade, pop, slide-up or none |

### `text preview`

Draw the EDL texts on a frame (check Persian shaping and placement).

```
mh_video text preview [-h] --out OUT [--at AT] [--video VIDEO] [--format FORMAT] [--scale SCALE] edl
```

| Argument | Required / default | Meaning |
|---|---|---|
| `edl` | required | the *.edl.json file |
| `--out OUT` | required | image file to write |
| `--at AT` | default `1.0` | time in seconds on the video timeline |
| `--video VIDEO` |  | draw over a frame of this video instead of a gradient |
| `--format FORMAT` |  | format preset to lay the text out for |
| `--scale SCALE` | default `0.5` | preview size relative to the frame |

## `sfx`

Sound design and ambient bed.

```
mh_video sfx [-h] {plan} ...
```

### `sfx plan`

What the sound design and ambient bed will do.

```
mh_video sfx plan [-h] edl
```

| Argument | Required / default | Meaning |
|---|---|---|
| `edl` | required | the *.edl.json file |
