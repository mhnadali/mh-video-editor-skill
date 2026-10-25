# Formats: one EDL, three shapes

```
render video.edl.json --format youtube-16x9         # one extra format
render video.edl.json --all-formats                  # reels-9x16, youtube-16x9 and square-1x1 (the project's own size is rendered as it is)
render video.edl.json --preview --format square-1x1 # half size, for a quick look
qa out_16x9.mp4 --edl video.edl.json                 # QA of a different format: render --qa does this for you
```
Presets: `reels-9x16` 1080x1920, `youtube-16x9` 1920x1080, `square-1x1` 1080x1080 (`engine/presets/*.json`, all 30 fps). Output files are named `<project>_<tag>.mp4` (`_9x16`, `_16x9`, `_1x1`, plus `_preview`).

What is shared and what is not: the picture parts are rendered and cached **per output size**; the mastered audio (music, whooshes, ambient) is built once and reused; cuts, transitions and effects are identical in every format; text is laid out again for each frame (safe areas differ).

## Reframing (how footage of another aspect ratio fills the frame)
| Mode | Result | Use |
|---|---|---|
| `cover` | scales until the frame is full and crops the overflow | same aspect ratio, or when losing the edges is fine |
| `blur-fill` | the whole picture, centred, over a blurred and darkened copy of itself | vertical footage in a 16:9 frame (the standard solution) |
| `fit` | the whole picture, black bars | when nothing may be cropped and blur is unwanted |

`style.reframe` sets the default (`cover`); `shot.reframe` overrides it. **When you render another format and the EDL says nothing, shots use `blur-fill`**: silently cropping a 9:16 shot to a 16:9 strip throws away most of the picture.

`shot.focus: [x, y]` (0..1) chooses which part of the picture `cover` keeps: `[0, 0.5]` keeps the left edge, `[1, 0.5]` the right, `[0.5, 0.5]` is the centre (default). Use it for a subject that is not in the middle (check the result with the contact sheet).

## Notes
- Vertical phone footage in 16:9 is mostly blurred background; for a landscape edit prefer landscape clips (drone, gimbal) and keep vertical ones short.
- The gate checks the size of the format it was told about (`qa --edl`, `render --qa` do it automatically); a 16:9 file checked against a 9:16 EDL fails the `aspect` check.
- Text in a different format may need a different `size`; the style presets scale with the frame height.
