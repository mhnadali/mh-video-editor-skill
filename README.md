# M.H. Video Editor Skill

![Version](https://img.shields.io/badge/version-0.5.0-blue)
![Claude Code](https://img.shields.io/badge/Claude%20Code-skill%20%2B%20agent-orange)
![License](https://img.shields.io/badge/license-MIT-green)

**One Claude Code skill and one autonomous agent for beat-synced short-form video editing with FFmpeg.** Point it at a folder of clips and a music file; it inventories the footage, finds the beat and the drop, plans the edit as a small JSON file, renders a cached preview and final, and checks the result.

Built from a real project: a 28-second Maranjab desert reel cut from 474 clips (37 GB, iPhone HDR + DJI), iterated v1 to v5.

> راهنمای کامل فارسی: [`docs/GUIDE.fa.md`](docs/GUIDE.fa.md)

## Why

| Problem | How this skill handles it |
|---|---|
| Edits are re-done from scratch after every comment | The video is an **EDL** (`*.edl.json`); changing a shot re-renders only that shot (cached parts) |
| Too many effects | A **restraint budget** is enforced by `lint`: at most 3 visible effects, a few varied transitions, hard cuts on the beat for the rest |
| Cuts that feel off | Beat grid + drop detected from the music; cuts land on main beats, the flash lands on the drop |
| Silent bugs (white frames, purple cast, 400 MB files) | Fixed in the engine, documented in `references/pitfalls.md`, covered by tests |
| iPhone footage looks grey or sideways | HLG tone-mapping and rotation handled automatically |
| Slow renders | NVENC encoding and CUDA decoding when available, parallel cached parts, cheap effects (28 s of 4K sources: cold final ~70 s, warm preview ~9 s) |
| Picking shots blind | `score` measures sharpness, exposure and motion, finds each clip's best moment and same-scene duplicates; `pick` proposes a story-ordered list |
| Unsure beat grid | Second tempo opinion, drift and onset checks, downbeats; notes tell you when to double-check |
| Defects reach the user | `qa` gate: black/white frames, colour cast, freezes, cut timing against the EDL, loudness and true peak, exposure jumps, size, effect audit; exit code for CI |
| Flat or inconsistent colour | Professional order: input transform, measured correction (`color plan`, scopes), then one of 11 looks baked into a 3D LUT (or your own `.cube`) |
| Shaky or too-fast moments, Persian titles, other platforms | `stabilize`, `speed` ramps with motion interpolation, right-to-left Persian text with safe areas, `--all-formats` (9:16, 16:9, 1:1) |
| Silent, lifeless cut | Synthesised whooshes that follow the transition direction (the music ducks under them) and the clips' own wind/engine sound, level-matched to the music |
| Broken input files or an FFmpeg build without a feature | One-line errors (never a traceback), `lint` explains malformed EDLs, `doctor` and `lint` say which FFmpeg filter is missing for the feature you use |
| Agent starts building before knowing what you want | Discovery gate: no confirmed `video-brief.md`, no editing |

## Pipeline

| Step | Command | Output |
|---|---|---|
| 0 | discovery interview (`references/discovery.md`) | `video-brief.md` |
| 1 | `probe` | `inventory.json/csv` |
| 2 | `sheets`, `score`, `rank`, `pick`, `beats` | contact sheets, `scores.json`, `beats.json`, candidate clip list |
| 3 | `need`, `plan`, `lint` | `video.edl.json` |
| 4 | `render --preview`, `render` (+ `color`, `text`, `sfx` helpers) | the video, in one or all formats |
| 5 | `qa`, `verify` | verdict + JSON report + contact sheet |

```bash
python engine/scripts/mh_video.py doctor
python engine/scripts/mh_video.py probe "D:/footage" --work work
python engine/scripts/mh_video.py sheets --work work
python engine/scripts/mh_video.py score --work work
python engine/scripts/mh_video.py beats "D:/footage/music.mp3" --out work/beats.json
python engine/scripts/mh_video.py need work/beats.json
python engine/scripts/mh_video.py pick --work work --count 39 --beats work/beats.json --out work/clips.txt
python engine/scripts/mh_video.py plan --scores work --beats work/beats.json --clips work/clips.txt --out work/video.edl.json --name trip
python engine/scripts/mh_video.py lint work/video.edl.json
python engine/scripts/mh_video.py render work/video.edl.json --preview
python engine/scripts/mh_video.py render work/video.edl.json --qa
python engine/scripts/mh_video.py qa work/trip.mp4 --edl work/video.edl.json --scores work
```

## Documentation

| Document | For |
|---|---|
| [`SKILL.md`](SKILL.md) | The workflow Claude follows |
| [`docs/GUIDE.fa.md`](docs/GUIDE.fa.md) | Complete Persian guide |
| [`docs/EXAMPLES.md`](docs/EXAMPLES.md) | Recipes: add a Persian title, make three formats, regrade, fix a flagged clip |
| [`docs/CLI.md`](docs/CLI.md) | Every command and argument (generated from the program) |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | Symptoms and fixes, environment variables |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Modules, timing model, per-shot filter chain, roadmap |
| [`references/`](references/) | EDL format, colour, motion, text, formats, sound design, QA gate, pitfalls, effect budget |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | State, the planned MP4 export / size-reduction layer with research and measurements, hand-off notes for continuing on another machine |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | How to change the engine safely |

## Install

Requirements: [Claude Code](https://claude.com/claude-code); Python 3.9+ (standard library only); FFmpeg (developed and tested with 9.0.2; `winget install Gyan.FFmpeg` on Windows, `brew install ffmpeg` on macOS; run `doctor` to check, it also finds winget installs that are not yet on PATH). Some features need FFmpeg libraries a minimal build may lack: libass (text), libvidstab (stabilisation), libzimg (HDR); `doctor` lists them.

**Windows (PowerShell)**: `.\install.ps1` **macOS / Linux**: `./install.sh`

Start a new Claude Code session, then use `/mh-video-editor-skill`. For autonomous builds call the `mh-video-editor` agent after the brief is confirmed.

## Tests

```bash
cd engine/tests
python run_tests.py              # everything (about 5-9 minutes with FFmpeg)
python run_tests.py --fast       # skips the slow end-to-end renders (about 2 minutes)
python run_tests.py -k color --durations 10
python coverage_report.py        # line coverage, standard library only (Python 3.12+)
python -m unittest discover -p "test_*.py"   # plain unittest works too
```
511 tests (96 % line coverage of the engine, measured with `coverage_report.py`). The end-to-end ones generate their own clips and music with FFmpeg (no footage needed); without FFmpeg they skip. They include a mutation fuzzer for malformed EDLs, a check that every filter the renderer uses is covered by `doctor`/`lint`, concurrency tests for the shared caches, a static Python 3.9 compatibility check, and a test that the generated CLI reference is current. A GitHub Actions workflow (`.github/workflows/mh-video-editor-skill.yml`) runs the suite on Linux and Windows.

## Status

Version 0.5.0 completes the five-phase plan (foundation, speed and accuracy, quality gate, new capabilities, hardening and documentation). It is tested on one Windows machine with FFmpeg 9.0.2 and synthetic media plus the author's own footage; other platforms rely on the continuous-integration run. Not for AI video generation or heavy compositing.

## License
MIT, see [`LICENSE`](LICENSE) and [`NOTICE.md`](NOTICE.md). FFmpeg is installed separately.
