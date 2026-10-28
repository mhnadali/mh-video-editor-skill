# EDL format (version 1)

Plain JSON. `plan` writes it; you may edit it by hand. Example: `engine/examples/edl.example.json`.

```json
{
  "version": 1,
  "project": {
    "name": "maranjab", "preset": "reels-9x16", "width": 1080, "height": 1920, "fps": 30,
    "duration": 28.235, "music": "D:/Kavir1405/All Videos/Music.mp3",
    "fade_in": 0.4, "fade_out": 0.8, "music_fade_in": 0.3, "music_fade_out": 0.9
  },
  "beats": { "t0": 3.2, "period": 0.6537, "drop_beat": 18 },
  "style": { "grade": "warm-desert", "zoom": 0.05, "drop_flash": true, "end_flare": true,
             "riser": true, "boom": true, "loudness": -14 },
  "shots": [
    { "clip": "D:/footage/a.MOV", "span": [0, 1.6] },
    { "clip": "D:/footage/b.MOV", "span": [1.6, 3.2], "src_start": 4.0 }
  ],
  "transitions": { "1": "fade", "2": "slideleft" }
}
```

## Phase-4 fields (all optional)
| Where | Field | Meaning | Doc |
|---|---|---|---|
| shot | `stabilize` | `light` / `strong` / `true` / object | `motion.md` |
| shot | `speed`, `speed_mode` | number or `{from,to}`; `auto`/`native`/`mci`/`blend` | `motion.md` |
| shot | `grade`, `correct` | per-shot look and correction (override the style) | `color-grading.md` |
| shot | `reframe`, `focus` | `cover`/`blur-fill`/`fit`; `[x,y]` 0..1 | `formats.md` |
| shot | `audio` | `{use, gain_db, speed_follow}` clip ambient sound | `sound-design.md` |
| top level | `texts` | list of on-screen texts | `text-persian.md` |
| style | `grade` | look name, `{look, strength, lut, lut_strength, ...}`, legacy preset or raw filter | `color-grading.md` |
| style | `correct`, `reframe`, `hdr_tonemap` | defaults for all shots; `hable-203` default, `legacy` = v1-v4 | `color-grading.md` |
| style | `sound_design`, `ambient` | whooshes + ducking; clip sound under the music | `sound-design.md` |

## project
`width`, `height`, `fps` come from `preset` (reels-9x16, youtube-16x9, square-1x1) unless set. `duration` must equal the shots' end and should equal the music length. `music: null` makes a silent video.

## beats
`t0` is the time of beat 0 (the body start), `period` seconds per beat; `drop_beat` is the beat index (relative to `t0`) where the drop lands. Used for `beats` spans, the drop flash and the riser/boom.

## shots
Ordered, contiguous (each starts where the previous ended, to half a frame; the first at 0, the last at `duration`). Use `span: [seconds, seconds]` or `beats: [a, b]` (grid beats). `src_start` picks the start inside the source clip; the default is 40% in. Each shot must be at least 8 frames.

## transitions
Keys are cut points: `"j"` is the cut between shot `j-1` and shot `j` (1 .. n-1). Missing = hard cut. Names: any xfade transition except the banned ones (see `references/effects-and-transitions.md`). With `drop_flash` on, the cut that carries the drop becomes `fadewhite` automatically.

## rationale (optional, 0.10)
Why each boundary is the kind of cut it is, written by `cuts --write` / `plan --smart-transitions` or by you:
`"rationale": {"3": {"type": "motion-bridge", "why": "A ends moving left; a slide left carries it"}}`. Keys are cut numbers like `transitions`; `type` and a non-empty `why` are required. Types: `cut-on-action`, `graphic-match`, `contrast-cut`, `size-step`, `montage-cut`, `direction-clash`, `jump-risk` (hard cuts); `impact`, `closing`, `section-lift`, `motion-bridge`, `soften`, `calm-dissolve` (transitions). Once an EDL has a `rationale`, `lint` warns (`transition-unmotivated`) for every transition without one. See `references/cut-craft.md`.

## style
| Key | Default | Meaning |
|---|---|---|
| grade | warm-desert | a look name (`color looks`), an object (`{"look": ..., "strength": ...}` or `{"lut": "x.cube"}`), a legacy preset (`engine/presets/*.json`) or a raw FFmpeg filter string starting with `eq=`/`colorbalance`/`curves`/`lut3d` |
| zoom | 0.05 | total push-in per shot (0 = off) |
| drop_flash | true | white fade + RGB split on the drop |
| end_flare | true | warm sun flare at the end (`flare_time` seconds to move it) |
| riser, boom | true | synthesised sounds around the drop |
| loudness | -14 | integrated LUFS of the FINAL mix (music + riser + boom), set by a two-pass master; true peak is held at -2 dBTP |
| budget | see effects doc | override lint limits (only with the user's agreement) |
