# The QA gate (`qa`, `render --qa`)

The gate answers one question before a video reaches the user: **did the render do what the EDL says, without defects?** It is built from the bugs that really happened on the Maranjab project and from what it found in the engine itself. It measures; you still look.

```
python "<skill-dir>/engine/scripts/mh_video.py" qa out.mp4 --edl video.edl.json --scores "<work-dir>"
```
Exit code 0 = PASS or PASS WITH WARNINGS, 1 = FAIL (`--strict`: 1 on warnings too). Writes `out.qa.json` and `out_check.jpg`. Takes about 3-8 s for a 30 s video.

## What is measured
- **Picture** (low resolution, one pass): per-frame luma average (YAVG), darkest-10% luma (YLOW), peak (YMAX), chroma averages (U, V), scene-change score, frame-to-frame difference.
- **Sound** (one pass): integrated loudness (LUFS), loudness range, true peak (dBFS), silences.
- **The EDL tells the gate what is intended**: fade in/out at the ends, the white fade on the drop and the flash window around it, `fadeblack`/`fadewhite` transitions, and the exact frame of every hard cut.

## Checks, thresholds and why
| Check | Level | Rule | Origin |
|---|---|---|---|
| black-frames | error | YAVG < 20 and YMAX < 70 outside the planned black windows | a black frame from the `rectcrop` transition; dark night scenes with lights are not black |
| white-frames | error (warn if 1 frame or no EDL) | YAVG > 222 and YLOW > 200 outside the flash/fadewhite windows | the v1 bug (22 white frames at Y=235); natural frames of the approved video never exceeded YAVG 215 |
| flash-missing | warn | a flash is planned but no frame near it exceeds YAVG 200 | the flash silently failing |
| magenta-cast / green-cast | warn (1-2 s), error (3+ s) | mean U and V of one second both above 134 (magenta) or both below 106 (green) | the v3 purple bug; warm orange (U low, V high) is not flagged |
| frozen | warn | frame difference < 0.02 for 0.5 s or more, outside the fades | a clip shorter than its shot |
| cut-timing | error if median offset >= 2 frames, warn if >= 1 or worst > 2 | peak of the scene score within +-6 frames of each planned hard cut | xfade offset or beat-drift bugs; skipped (info) when neighbours look alike |
| cut-detection | warn | more than half of the planned hard cuts show no scene change at all | look-alike neighbouring shots: the cut cannot be verified |
| loudness | warn | outside target -3 / +2 LU (default target -14 LUFS) | riser/boom pushed the mix 2 LU hot |
| true-peak | warn above -1.0, error above -0.1 dBFS | | clipping; platforms re-encode louder |
| audio | error (info if the EDL has no music) | the file has no audio track although the EDL has music | lost audio |
| silence | warn | silence of 0.5 s or more away from the ends | muted section |
| frame-count / duration / aspect / fps | error | differ from the EDL by more than 1 frame / 0.06 s / 1% | truncated or wrong-size renders |
| file-size | warn above 6 MB/s, error above 400 MB | | temporal grain bloat (422 MB) |
| restraint, lint-* | warn | the linter rules on the EDL | too many effects, repeated or look-alike transitions |
| exposure-jumps | info, warn when more than 30% of hard cuts change brightness by more than 45 levels | mean luma of the 4 frames before vs after each hard cut | flicker between shots; `color plan --match exposure` evens them out (v4: median jump 12, 14% large) |
| look-consistency | warn | more than two different grades in one video | one look per video unless the story needs a change |
| content | info | summary of looks, corrections, speed changes, stabilised shots, texts, sound design | what the EDL asked for |
| beat-alignment | warn | fewer than 90% of cuts within 1.5 frames of a beat | cuts that ignore the music |
| weak-clips, similar-neighbours | warn | clips flagged dark/overexposed/blurry, or two neighbouring shots of the same scene (needs `--scores`) | phase 2 scoring |

Info lines (`chroma-balance`, `luma-range`, `clip-quality`) are context, never failures.

## How to read a report
1. FAIL: fix the cause (EDL or engine), re-render, run the gate again. Do not hand over a FAIL.
2. WARN: open the contact sheet and the time stamps in the message. Either fix it or say in the report why it is acceptable (e.g. "loudness -12.9: user approved this level in v4").
3. No EDL given: the gate still checks the picture and sound but cannot excuse a planned flash, so a flash shows as a warning.
4. What it cannot do: judge composition, story, taste, or hear the music. State that.

## Calibration (so thresholds can be revisited)
Measured on the approved Maranjab video: YAVG 16-229 (only the flash above 215), mean chroma U 121 / V 132 (warm), no second with both U and V above 134, black frames only inside the 0.4 s fade-in and 0.8 s fade-out. If a new genre of footage trips a threshold wrongly, change the constant at the top of `vid_qa.py`, add a test for it and note it in `pitfalls.md`.
