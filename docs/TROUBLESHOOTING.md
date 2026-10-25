# Troubleshooting

Start with `python "<skill-dir>/engine/scripts/mh_video.py" doctor`. It must end with `READY`, and it lists which optional features your FFmpeg build supports. Every error is one line on stderr with exit code 2; `MH_DEBUG=1` shows the traceback of unexpected ones.

## Environment variables
| Variable | Effect |
|---|---|
| `MH_FFMPEG_DIR` | folder that contains `ffmpeg` and `ffprobe` (when they are not on PATH) |
| `MH_HWACCEL` | decoder acceleration for source clips (`cuda`, `d3d11va`, `videotoolbox` ...); default is automatic CUDA with a software fallback |
| `MH_FONT_DIR` | extra folder searched for fonts (Persian text) |
| `MH_DEBUG` | `1` = full Python traceback for unexpected errors |

## FFmpeg
| Symptom | Cause and fix |
|---|---|
| `FFmpeg not found` | Windows: `winget install Gyan.FFmpeg`. The engine also searches the winget folder, so a new terminal is not required, but a running desktop app needs a restart to see PATH changes. macOS: `brew install ffmpeg`. Linux: your package manager. Or set `MH_FFMPEG_DIR`. |
| `doctor` prints `FAIL filter ...` | The build is too minimal. Install a full build (Gyan "full" on Windows, `brew install ffmpeg` on macOS). |
| `doctor` prints `warn <feature>: FFmpeg lacks ...` | Only that feature is unavailable. `lint` reports `ffmpeg-missing` when an EDL uses it. Missing `ass` (libass) = no on-screen text, `vidstabdetect` (libvidstab) = no stabilisation, `zscale` (libzimg) = no HDR tone mapping. |
| Older FFmpeg | Developed and tested with FFmpeg 9.0.2. The engine chooses `-/filter_complex` (FFmpeg 7+) or `-filter_complex_script` by version (`doctor` shows which), and the continuous-integration run uses the distribution's FFmpeg, but versions below 6 have not been tried: update if something fails oddly. |

## Rendering
| Symptom | Cause and fix |
|---|---|
| `EDL has errors; fix them first` | Run `lint EDL`. `--force` skips lint errors but never structural ones (a malformed EDL cannot be rendered). |
| `shot N (...) failed to render` | The message ends with FFmpeg's own text. Usual causes: unreadable or truncated clip (`lint` checks), a source on a disconnected drive, a full disk. |
| Render is slow | First render of a shot is the cost; later renders reuse cached parts (`<name>_work/parts`). `--preview` for a quick look; NVENC is automatic when the GPU supports it (`doctor` says). More than 4 `--workers` rarely helps. |
| Output is huge | Over 6 MB per second triggers a QA warning. Check for grain or a very high-bitrate source encoded with `--encoder libx264` at low CRF; the default settings give 50-75 MB for 28 s. |
| HDR (iPhone) clips look dark | v1-v4 used `hdr_tonemap: "legacy"`; the default `hable-203` is brighter. Choose with `style.hdr_tonemap`. |
| Cache seems stale | Parts are keyed by content (clip size/mtime, window, filters). `--no-cache` forces everything. Deleting `<name>_work` is always safe. |
| Two renders at once | Safe: cache files are written atomically. They share the `luts` folder next to the work folder. |

## Text and Persian
| Symptom | Fix |
|---|---|
| Letters are separate, or the order is reversed | The font lacks Arabic-script shaping. `text fonts` shows what is used; install or point `MH_FONT_DIR` to Vazirmatn, IRANSans, Sahel or similar. Tahoma, Segoe UI and Arial also work. |
| Squares instead of letters | Same: the font has no Persian glyphs. Set `font` or `font_file` on the text. |
| Text hidden behind the app interface | Use the default positions (they respect the safe areas). A `custom` position gets a warning when it falls inside the UI zone. |
| `text preview` shows it right but the render does not | Preview and render use the same code. Check you rendered the same EDL and the same format (`--format`). |

## Quality gate warnings
| Check | Meaning and what to do |
|---|---|
| `loudness` | Outside the target by more than -3/+2 LU. Check `style.loudness`; a very quiet or very loud music file can be the cause. |
| `true-peak` | Above -1 dBFS. The master limiter holds -2 dBTP; a warning means something else (an external re-encode). |
| `cut-timing` / `cut-detection` | A hard cut is not where the EDL planned it, or neighbouring shots look alike so the cut cannot be verified. Look at the time stamps in the contact sheet. |
| `exposure-jumps` | Brightness changes sharply at many cuts: run `color plan --match exposure`. |
| `look-consistency` | More than one grade in the video: use one look and vary the strength. |
| `black-frames` / `white-frames` | Blank frames outside planned fades: look at the stamped moments; a bad transition or a clip that is black at its start. |
| `restraint` | More effects/transitions than the budget. Remove some rather than raising the budget. |

## Python
- Python 3.9 or newer, standard library only. `python` vs `python3` and `py` depend on the OS; the installers pick the first that works.
- The test suite needs FFmpeg for the end-to-end tests and skips them when it is missing. Tests that need `libass` or `libvidstab` skip when the build lacks them.
- `coverage_report.py` (in `engine/tests`) needs Python 3.12+.

## Still stuck
Run `doctor`, `lint` and `qa` and keep their output; the three together answer most questions. Never edit rendered files: change the EDL and render again.
