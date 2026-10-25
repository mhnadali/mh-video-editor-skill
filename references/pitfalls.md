# Pitfalls already met (and fixed in the engine)

Each item cost real time on the Maranjab project. Read before changing the renderer.

## FFmpeg filters
| Symptom | Cause | Fix baked into the engine |
|---|---|---|
| First 22 frames of the video were white | `fade=t=in:start_frame=N:color=white` paints every frame BEFORE N | flash = `xfade=fadewhite` at the cut (+ short `rgbashift` with `enable='between(n,a,b)'`); never fade with start_frame |
| Whole video turned purple/pink | `blend` with a custom expression on yuv420p also shifts chroma | convert to `format=gbrp` before blending, back to yuv420p after |
| Output was 422 MB for 28 s | `noise=alls=6:allf=t` temporal grain defeats compression | no grain; same video is ~50-75 MB |
| FFmpeg crashed (exit 139 / 0xC0000005) on some clips | `scale=...:eval=frame` zoom-OUT | zoom is push-in only; lint rejects negative zoom |
| One flat single-colour frame at a cut | xfade `zoomin` | banned in `vidlib.BANNED_TRANSITIONS` |
| iPhone HDR clips looked grey and washed out | HLG (`arib-std-b67`) needs tone-mapping | probe sets `hdr`; part filter starts with zscale/tonemap=hable to bt709 |
| Phone clips came out sideways or the wrong aspect | rotation lives in side data | `parse_probe` swaps width/height for +-90; FFmpeg autorotates |
| Cuts drifted off the beat after transitions | xfade shortens the timeline | pad shots by 2 frames only on transition sides; offset = (accumulated - overlap)/fps; assert total frames |
| Preview size 135 px wide -> odd dimension | floor division | `vidlib.even` rounds to the nearest even number |
| A clip shorter than its shot froze for only 1 s, so the video came out short | `tpad=stop_mode=clone:stop=30` pads a fixed 30 frames | `stop=-1` clones forever; `-frames:v N` ends the part (regression test) |
| Every contact sheet showed the LAST chunk of clips | `tile` + `-frames:v 40` + `-update 1` kept rewriting one file | `-frames:v 1` (tile emits one frame per sheet); test asserts sheets differ |

## FFmpeg / OS
- `-filter_complex_script` no longer exists in FFmpeg 7+: use `-/filter_complex file`. `vidlib.filter_script_flag()` picks by version.
- On Windows the PATH of a running app does not update after `winget install`. `vidlib.find_ffmpeg()` looks in the winget package folder, so run `doctor` before telling the user FFmpeg is missing (this mistake was made once).
- Windows Python cannot use Git-Bash paths like `/c/Users/...`; use `C:/Users/...` (`vidlib.ffp`).
- `tile` writes one image: add `-update 1`. Frame numbers in thumbnails must be contiguous (`%04d.jpg`); failed clips get a dark placeholder tile.
- Filter text arguments (fontfile, subtitles) need `:` escaped: `vidlib.filter_escape_path`.
- Audio: `loudnorm` on the music bed only; mix riser/boom with `amix normalize=0` and `alimiter`, otherwise the sum clips.

## Found by the quality gate (phase 3)
| Symptom | Cause | Fix |
|---|---|---|
| One black frame at a cut, mid-video | xfade `rectcrop` (and `circlecrop`) draw a black frame | banned; `tests/test_transitions_audio.py` renders every transition and inspects the frames |
| FFmpeg crash (0xC0000005) | xfade `squeezev` | banned |
| Mix 2 LU above the target, true peak -0.6 dBFS | riser and boom added after the music was normalised | master the FINAL mix (`master_audio`) |
| One-pass `loudnorm` missed -15 by 3 LU, peak +0.1 | dynamic one-pass mode on 28 s of material | two passes: measure, then `linear=true` with the measured values; target TP -2 because AAC overshoots |
| "22 white frames" would not be detected | limited-range white is Y=235 and the threshold was 238 | blown-out = YAVG > 222 and YLOW > 200 (natural frames never exceeded YAVG 215 on the approved video) |
| Planner exceeded its own transition budget on short videos | forced drop fade and closing fade were extra | forced fades count inside the 50% ceiling |

## Found while building phase 4
| Symptom | Cause | Fix |
|---|---|---|
| Vignette darkened the centre or nothing at all | in FFmpeg a LARGER `angle` darkens MORE (it is a lens angle) | `angle = min(pi/2*0.8, (pi/7)*amount/0.2)`; amount 0.2 is the approved look |
| Assembly failed on real footage after zoom (SAR 481:480) | `scale` for the zoom leaves a non-square pixel aspect | `setsar=1` straight after the zoom crop |
| Phone HDR clips came out too dark with `peak=1000` | HLG reference white is 203 nits, not 1000 | default `hable-203` (npl 203); `legacy` reproduces v1-v4 |
| Auto-correction crushed shadows on a bright desert and tinted the sky | black point from the 10th percentile; gray-world white balance | black/white points only from YMIN/YMAX, levels before exposure, EV capped at +-0.45, white balance `median` by default (gray-world opt-in) |
| Music disappeared after the last whoosh | `sidechaincompress` ends with its shortest input | pad the key with `apad whole_dur` |
| Whooshes did not duck the music | RMS detection averages short bursts away | `detection=peak` with a threshold that follows the whoosh level |
| SRT test with a doubled CR failed | text-mode reading collapses CR LF handling | read bytes, normalise CRLF / CR / LF |
| Test results changed between runs | the `gradients` source is random | use `geq` for deterministic test pictures |
| An integration test overwrote its own source clip | the output was named like the input (`c1.mp4`) | outputs are named `out_*.mp4` |
| Scope sheet failed with odd sizes | odd width or height after tiling | round to even sizes |
| Ambient level was too loud on loud clips | a fixed gain ignores the recording level | measure each shot's mean volume and set it relative to the music, clamped to -12..+18 dB |
| Slowed-down engine sound | audio untouched by `setpts` | retimed shots are muted unless `speed_follow` is set |

## Found while hardening (phase 5)
| Symptom | Cause | Fix |
|---|---|---|
| Render died with "3D LUT is empty" | parallel part workers checked `path.is_file()` and used a LUT another worker was still writing | atomic write (temp file + replace), a lock per LUT, `vl.replace_file` retries Windows' "Access is denied" during a swap |
| `-h` of one command crashed with `TypeError: %i format` | a `%` in an argparse help text | write `%%`; a test prints the help of every command |
| 80 % of randomly damaged EDLs raised raw exceptions | validation assumed types | `vid_edl.structure_errors`, safety net in `lint`, specific messages (fuzzer asserts it) |
| `doctor` read the legend line of `ffmpeg -filters` as a filter called `=` | parser accepted any second column | names must match `[a-z][a-z0-9_]*` |
| An interrupted `color plan --write` could truncate the user's EDL | JSON written in place | `save_json` writes a temp file and swaps it |
| A stock FFmpeg without libass/libvidstab failed in the middle of a render | no capability check | `doctor` feature list, `lint` error `ffmpeg-missing`, tests skip when the filter is absent |
| Newer syntax or library calls could slip in unnoticed (development happens on 3.12 and 3.14) | no old interpreter available locally | static compatibility tests; CI runs 3.9 |
| Scripted edits corrupted backslash escapes in source files (a newline inside a string literal, a backspace character in a regex) | quoting layers between the shell, the tool and Python | edit source with the editor tools; compile everything and run the repository tests after a scripted edit |

## Speed (measured on 28 s, 39 shots, 4K HEVC/H.264 iPhone + DJI, RTX 3050 Ti laptop)
- `blend=all_expr` evaluates an expression for every pixel of every frame: 35 s extra. Use a source that fades up and down from black and FFmpeg's built-in `blend=all_mode=screen` (screen with black is a no-op).
- A global filter with `enable=` is still in the graph; apply short effects inside the one part that needs them (the RGB split sits in the drop shot).
- Decoding 4K dominates part rendering (about 1-2 s per shot); CUDA decode is about 20% faster, more workers than 4 do not help (8 was slower). x264 ultrafast vs veryfast: no difference; NVENC parts were slower than libx264 for tiny parts.
- Probing 40 files serially cost about 10 s per render; probe results are now cached and warmed in parallel.
- Scoring costs about 1.3 s per clip however many windows (fixed per-process cost), so results are cached and only new files are measured.

## Repo hygiene
- Files created on Windows may get CRLF: `test_repo` fails on CRLF in scripts and `.gitattributes` forces LF.
- The linter once flagged the planner's own output (a "digital" family lumped five unlike transitions): families must group things that LOOK alike, nothing else.

## Analysis
- Beat grid from onsets is a measurement: report confidence and the half/double alternative. The autocorrelation prior favours 100-140 BPM.
- Drop = largest sustained 2 s loudness step (ratio >= 1.25), then the first grid beat where the next second is at >= 90% of the new level. Never search for it before the music has started (a silent lead-in looks like a step).
- The first strong beat is not the body start; the planner puts the body start at about 11% of the video, snapped to the grid.
- A static thumbnail says nothing about motion or blur (phase 2 adds scoring). Default source start is 40% into the clip so the frame the user reviewed is what they get.

## Process
- Inventory indexes are positions in the CURRENT folder listing: deleting or adding files re-numbers them. Choose `--only`/`--exclude` ranges from sheets made after the latest `probe`; guessed ranges (from an older numbering) pulled grill and restaurant clips into a desert payoff.
- Measured energy is motion, not excitement: hand-held close-ups outrank off-road driving. `pick` is a candidate generator inside a pool the agent chose by looking.
- Files vanish: users clean their footage folder mid-project (28 files, including a favourite). `lint` checks every clip and the music before every render.
- "More effects" is not a spec. v3 followed it literally and was rejected; propose a budget (see `effects-and-transitions.md`).
- Intermediate files and scripts in a scratch folder are lost. Everything reusable lives in this skill.
- Verify the render with a contact sheet before reporting; three of the four bugs above were caught that way, not by the tests. `qa` now automates them.
