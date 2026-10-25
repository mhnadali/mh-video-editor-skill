# Motion: stabilisation and speed

Both are per-shot fields. Neither is on by default.

## Stabilise
```json
{"clip": "...", "span": [0, 0.65], "stabilize": "light"}      // light | strong | true | false | {"shakiness": 6, "smoothing": 15, "zoom": 2}
```
Two passes with vid.stab: the shot is analysed in a first FFmpeg run (the `.trf` file is cached in the work folder, named by a hash of clip, window, geometry and settings) and then transformed. Both passes see the same frames: after retiming and reframing, at the output size, so the analysis is cheap.
- `light`: shakiness 4, smoothing 8 frames. `strong`: shakiness 8, smoothing 20. `optzoom=1` zooms in just enough to hide the borders.
- On a synthetic shaky clip (+-25 px jitter) `light` removes about 20% and `strong` about 45% of the frame-to-frame motion; use it for car-window and handheld shots, not for clips that are already steady (it leaves those alone, but costs a pass).
- Stabilising an already slowed shot exaggerates residual wobble (the linter warns).

## Speed
```json
{"speed": 0.5}                                   // slow motion
{"speed": 1.6}                                   // speed-up
{"speed": {"from": 1.0, "to": 0.4}}              // a linear ramp over the shot (e.g. into the drop)
{"speed": 0.5, "speed_mode": "auto"}             // auto | native | mci | blend
```
Range 0.1 to 4. The shot keeps its length on the timeline: a 0.5 shot of 1 s reads 0.5 s of the clip. For ramps the source time is the integral of the speed (average speed for a linear ramp).

Slow motion needs frames that were never recorded, so the mode matters:
| Mode | What it does | When |
|---|---|---|
| native | uses the real frames (and repeats frames when there are not enough) | 60/120 fps footage slowed to 50%/25%: the best quality |
| mci | motion-compensated interpolation: smooth, can warp thin fast edges | 30 fps footage, shots up to 1.5 s |
| blend | cross-fades neighbouring frames: cheap, ghosts | longer shots |
| auto | native when the source has at least 95% of the output frame rate after slowing (`src_fps * slowest_speed >= 28.5`), else mci up to 1.5 s, else blend; speed-ups are always native | the default |

- Shoot slow-motion material at 60 or 120 fps; the iPhone/DJI settings are the cheapest quality upgrade. `probe` lists the fps of every clip, so look there for real slow-motion candidates.
- Retimed shots are muted in the ambient sound (a slowed engine sound is wrong) unless the shot says `"audio": {"speed_follow": true}`.
- Slow motion counts as a visible effect (`slow-motion`) against the budget of three: zoom + drop flash + slow motion fits, adding the end flare does not. Make room instead of raising the budget.
- Measured on test clips: the playback speed of the output equals the requested speed within 0.06 (constant) and the ramp follows speed(t) = from + (to - from) * t / L.
