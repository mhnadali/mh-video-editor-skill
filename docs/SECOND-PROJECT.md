# The second project (the 1.0 gate)

The engine's thresholds — what counts as the same moment, how close a cut has to land on the beat, how reliable a
"person" sighting is — were calibrated on one real trip (Kavir: 486 clips, three cameras, two days; the numbers are in
[`CALIBRATION.md`](CALIBRATION.md)). One trip cannot prove a threshold. **1.0 is reached when the whole pipeline runs
end to end on a second real project, with different footage, and the calibration numbers still hold there.** This page
is the protocol for that run. The footage never enters the repository.

> **Why a second project at all?** A threshold tuned on one body of footage can be *overfit* to it: the same-moment
> rule might lean on a quirk of one camera's clock, or the beat tolerance on one song's tempo. A second project with
> different cameras, light and music is the cheapest honest test that the numbers describe editing in general, not one
> trip in particular.

## Before you start
- A folder of the second project's footage (anything FFmpeg reads) and one music track.
- `python engine/scripts/mh_video.py doctor` is clean (FFmpeg found, the optional features you need present).
- Optional but recommended for the people/sound checks: a `shot-notes.json` for a handful of clips and a small
  `labels.json` (same-moment pairs and a few people labels), exactly as for the first project.

## Step 1 — run the pipeline to a storyboard
```
python engine/scripts/mh_video.py prepare --footage FOOTAGE --music SONG.mp3 --work WORK2 --duration 45
```
`prepare` stops at `NEEDS_INPUT` (write `chapters.json`) or `NEEDS_APPROVAL` (open the review page). Approve a
storyboard the way you would for a real edit. If anything crashes or produces an obviously wrong result, that is a
1.0 blocker — record it and stop here.

## Step 2 — measure the second project
```
python engine/tools/eval_real.py --work WORK2 --chapters WORK2/chapters.json \
    --notes shot-notes.json --save baseline.project2.json
```
Add `--video RENDER.mp4 --edl WORK2/video.edl.json` to include the beat sync measured on a real render's own sound.

## Step 3 — check the calibration holds on both projects
Keep the first project's baseline (`baseline.kavir.json`, saved the same way on the Kavir work folder). Then:
```
python engine/tools/eval_real.py --cross baseline.kavir.json baseline.project2.json
```
This checks that **both** projects clear the absolute floors (not a per-project regression):

| Metric | Floor | Meaning |
|---|---|---|
| `same.precision` | ≥ 0.95 | the same-moment rule almost never groups two different moments |
| `people.precision` | ≥ 0.80 | a "person" sighting is usually right |
| `beats.median_abs` | ≤ 0.02 s | the beat grid sits on the music |
| `qa.median_abs` | ≤ 0.045 s | cuts planned on a beat land on it in the render (the QA `beat-sync` threshold) |
| `select.deterministic` | = 1 | the same input selects the same storyboard twice (no hidden randomness) |

The tool prints each number for both projects and the spread between them, and exits non-zero if either project is
below a floor. A number that passes comfortably on one project and only barely on the other is a warning sign even
when it passes: look at the spread.

## Step 4 — judge what the numbers cannot
Some things have no number yet and need your eyes and ears on the second project's footage (see the
[QA protocol](QA-PROTOCOL.md) and the sound A/B page from `audio ab`):
- J-cuts and L-cuts: does the sound lead or trail the picture in a way that helps, or just sounds loose?
- The ambient bed: does leaving windy/clipped clips out of it actually sound better?
- Colour: do the camera matches and the look hold up across this footage, or does a shot stand out?

## When it passes
When Steps 1–4 come back clean on a genuinely different project, the calibration is no longer a one-trip guess, and
the milestone in [`ROADMAP.md`](ROADMAP.md) is met. Record the second project's numbers next to the Kavir numbers in
`CALIBRATION.md` so the evidence for 1.0 is written down.
