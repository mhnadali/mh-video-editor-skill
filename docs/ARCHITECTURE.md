# Architecture

```
Request -> Step 0 discovery -> video-brief.md (CONFIRMED, frozen)
              |
   probe --> inventory.json         (duration, rotation, fps, HDR, duplicates)
   sheets -> sheetNN.jpg            (what the footage looks like; the agent READS these)
   score --> scores.json            (quality, energy, best window, flags, same-scene hash; cached per file)
   pick ---> clips.txt              (story-ordered CANDIDATES, reviewed by the agent)
   beats --> beats.json             (BPM, period, first beat, drop, downbeats, validation notes)
   need/plan -> video.edl.json      (single source of truth)
   lint ---> errors block, warnings = restraint budget
   render -> parts (cached) -> one filter graph -> encoder (NVENC | libx264)
   qa -----> picture + sound measurements, EDL-aware checks, verdict, JSON report, contact sheet
   verify -> quick structural checks
```

## Principles
1. The EDL is the truth; renders are disposable. Editing = changing the EDL and re-rendering (unchanged shots come from the cache).
2. Restraint is a rule, not a taste: the linter counts effects and transition looks.
3. Measure, then look: tools measure (tempo, duration, frames), the agent looks (contact sheets) and says what it could not check (audio by ear).
4. Standard library only; FFmpeg is the only external dependency.
5. Sub-agents cannot ask questions, so discovery happens in the main conversation and the agent builds only from a confirmed brief.

## Modules (`engine/scripts/`)
| File | Role |
|---|---|
| `vidlib.py` | FFmpeg discovery (also winget installs not on PATH), probe parsing, transition vocabulary and families, helpers |
| `vid_probe.py` | footage inventory, duplicate detection |
| `vid_sheets.py` | numbered contact sheets for clips; frame sheet for a render |
| `vid_beats.py` | decode, onset envelope, tempo, grid fit, drop, second tempo opinion, drift, downbeats |
| `vid_score.py` | clip measurements (one FFmpeg pass per window), best window, same-scene hashes, ranking, story-ordered picking |
| `vid_edl.py` | EDL load, timeline in frames, effective transitions, linter |
| `vid_plan.py` | layout geometry, clip list parsing, EDL generation, transition picking |
| `vid_render.py` | per-shot parts, graph building, two-pass audio master, assembly, encoder choice, basic verify |
| `vid_qa.py` | quality gate: measurement passes, expected-event windows from the EDL, checks, edit audit, report |
| `vid_motion.py` | stabilisation presets (vid.stab), speed validation, retime chains (constant, ramp, native / interpolated / blended) |
| `vid_color.py` | correction maths, looks, 33^3 LUT builder, .cube read/write, auto-correction planner, scopes |
| `vid_text.py` | font discovery, Persian shaping helpers, wrapping, safe areas, SRT, ASS builder |
| `vid_sfx.py` | whoosh mapping and synthesis, ambient segments and levels |
| `vid_editorial.py` | editorial measurement (shot size, camera motion, saliency) and editorial lint - the 'why' of the cut |
| `vid_deliver.py` | delivery presets, size/bitrate targeting, render manifest, project bundle, cache GC |
| `mh_video.py` | CLI |

## Editorial intelligence: the engine measures, the agent judges

Shot grammar, pacing, continuity, colour balance and music structure are all **measured** with FFmpeg + pure-Python
signal processing (`vid_editorial.py`, `vid_beats.detect_sections`, `vid_color`); the creative **judgement** - which cut
serves the emotion and the story - stays with the agent, guided by `references/editorial-rubric.md`. Every measured
signal is a reviewable number on a score entry or in the EDL, never a hidden decision. Truly semantic perception
(faces, objects, speech) would need ML, which the project excludes (standard library + FFmpeg, no downloads); the
heuristic signals (motion saliency, edge/entropy, colour) are enough for reframe, interest and continuity.

### Cut decisions (0.9-0.12)
`vid_cutlogic.py` turns the per-shot measurements into a named cut and a one-sentence reason for every boundary, then applies the transition budget. Inputs: `score.json` fields (shot size, camera motion, point of interest, brightness, saturation, energy, picture hash, dominant hue, `local_motion`, `flux`), the music sections of `beats.json`, and the agent's `shot-notes.json`, which overrides the measurements. Output: `transitions`, `transition_frames` and `rationale` in the EDL (`cuts --write`, `plan --smart-transitions`), plus optional `src_start` alignments (`cuts --align`). The rules are explicit and tested; nothing is learned.

**The honest ML boundary.** Pure-Python measurements can say *how big, how fast, which way, how bright, what colour, how alike*. They cannot say *who*, *what is happening*, *whether the gesture is finished* or *whether a face is turned toward the camera*. Those judgements are the agent's: it looks at the frames, writes `shot-notes.json`, and its notes win. Adding a vision model would break the zero-dependency, offline promise (see `SECURITY.md`), so it is deliberately not part of the engine.

## Per-shot part chain (order matters)
```
tonemap (HLG -> SDR) -> retime (setpts / minterpolate / fps) -> reframe (cover | blur-fill | fit, focus)
 -> setsar -> vidstab transform -> zoom push-in -> setsar=1
 -> colour (gbrp16le: correction curves -> look lut3d -> vignette) -> [RGB split on the drop part]
 -> format=yuv420p -> tpad stop=-1
```
Parts are cached by a hash of everything that influences them and live under `parts/<format tag>`, so one shot re-renders alone and other formats do not collide. Text (libass) is applied to the assembled picture; audio is built once (`master_audio`: music + riser + boom + whooshes + ambient, music ducked by a sidechain, two-pass loudness on the final mix).

## Robustness (phase 5)
- **Input is untrusted.** `vid_edl.structure_errors` checks shape, types and magnitudes of the core fields before anything else runs; `lint` never raises (a safety net turns any leftover exception into an `edl-malformed` error); `render --force` skips lint errors but never structural ones; the CLI prints one-line errors (exit 2) and brings the traceback back only with `MH_DEBUG=1`. A seeded mutation fuzzer (`test_fuzz.py`) damages valid EDLs thousands of times and requires an explained error each time.
- **Capabilities.** `vidlib.FEATURE_FILTERS` maps each optional feature (text, stabilisation, HDR ...) to FFmpeg filters; `doctor` prints what the build supports, `lint` reports `ffmpeg-missing` for features the EDL uses. A test renders a project with everything on and fails when the renderer uses a filter that neither list knows.
- **Shared files are written atomically** (`vl.save_json`, `vl.replace_file`, `vid_color.write_cube`): parallel part renders share LUT and cache files, and a race once produced "3D LUT is empty" mid-render. Look LUTs are built once per process behind a lock.
- **Python 3.9+** is enforced statically (grammar, library names, annotations, f-string rules), because only CI runs old interpreters.

## Tests (`engine/tests`)
| File group | What it covers |
|---|---|
| `test_vidlib`, `test_edl_plan`, `test_beats*`, `test_score`, `test_speed` | probing, timeline maths, planner, beat analysis, scoring, hardware options |
| `test_render`, `test_transitions_audio`, `test_robustness` | real renders: frame counts, every transition, odd paths, corrupt input |
| `test_qa` | the quality gate on deliberately broken videos |
| `test_color`, `test_motion`, `test_text`, `test_formats`, `test_sfx`, `test_phase4_integration` | the phase-4 capabilities, down to pixel checks, and one project that uses all of them in three formats |
| `test_fuzz`, `test_hardening`, `test_capabilities`, `test_cli`, `test_repo` | malformed input, atomic writes and concurrency, Python-version compatibility, FFmpeg discovery, filter coverage, command line, documentation consistency |
| `run_tests.py`, `coverage_report.py` | runner with `--fast` and timings; standard-library line coverage (3.12+) |

`engine/tools/gen_cli_doc.py` writes `docs/CLI.md` from the argument parser.

## Timing model
All cut points are quantised to frames (`round(t * fps)`). A transition lasts 4 frames centred on the cut; a shot is padded by 2 frames only on the sides touching a transition. Assembled length is asserted equal to `round(duration * fps)` before encoding, and tests check random layouts.

## Roadmap (phases)
1. Foundation (v0.1). 2. Speed and accuracy (v0.2): cached probing, cheap effects, GPU decode, shot scoring, same-scene detection, beat validation. 3. Automated QA gate and effect audit (v0.3). 4. New capabilities (v0.4): colour correction and grading, stabilisation, slow motion, Persian text, 16:9 and 1:1, sound design, clip audio. 5. Hardening, documentation, Persian guide and CI (this version, v0.5).

After phase 5 the next steps depend on use, not on the plan: run it on other machines and footage and fix what that finds.
