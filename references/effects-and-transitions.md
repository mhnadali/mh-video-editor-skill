# Effects and transitions: the restraint budget

Learned on the Maranjab desert edit: v3 stacked zoom (every shot), camera shake (36 of 39 shots), glow, a light-leak pulse and an RGB split on every beat, plus a transition on all 38 cuts. The user called it overwhelming. v4 kept three light effects and varied transitions and was called perfect. `lint` enforces this.

## Effect budget (default, in `vid_edl.DEFAULT_BUDGET`)
| Rule | Default | Why |
|---|---|---|
| Visible effect kinds in the whole video | <= 3 | v4 had three |
| Effects that fire on every beat | forbidden | v3's per-beat pulses hid the footage |
| Zoom | push-in only, <= 8% (default 5%) | zoom-out crashes some FFmpeg builds; strong zoom reads as shaky |
| Impact effects (white flash + RGB split) | once, on the drop | an event only works if it is rare |
| Sun flare / light leak | once, at the ending | warm closing beat |
| Shake, glow, grain | not in the engine | footage is already shaky; grain bloats the file 5x |

Engine effects today: `zoom`, `drop_flash` (white fade + 5-frame RGB split), `end_flare` (warm gradient blend), `slow-motion` (a shot with `speed` below 1 or a ramp), audio `riser` and `boom` (synthesised). All are switchable in `style`.

Not effects, so outside the budget of three: colour correction and the grade (one look per video), stabilisation, text, whooshes and ambient sound. They still obey restraint: add them when the brief asks, not because they exist.

## Transition budget
The budget says *how many*. *Which one and why* is in `transition-craft.md`: a transition must say something a hard cut cannot, and each one gets a written reason.

| Rule | Default |
|---|---|
| Share of cut points with a transition | <= 50% (v4: 17 of 38 = 45%); the rest are hard cuts on the beat |
| Same transition type | each at most once (`fade` twice: in and out) |
| Looks-alike family | <= 3 per family (a family is one look: see below) |
| banned | `zoomin` (flat single-colour frame), `rectcrop` and `circlecrop` (a black frame), `squeezev` (crashes FFmpeg): see `vidlib.BANNED_REASONS`; every transition is probed by a test |
| deliberate dips | `fadeblack`, `fadewhite`: allowed, the QA gate expects their black/white frames |

Count LOOKS, not names: `slideleft`, `smoothleft`, `wipeleft` and `horzopen` all read as "a horizontal slide". v3 had 13 names but about two thirds were horizontal or vertical slides, so it felt like 1-2 transitions.

### Families (`vidlib.TRANSITION_FAMILIES`)
- horizontal: slideleft/right, smoothleft/right, wipeleft/right, horzopen/close, cover/reveal left/right
- vertical: slideup/down, smoothup/down, wipeup/down, vertopen/close, cover/reveal up/down
- diagonal: diagtl/tr/bl/br, wipetl/tr/bl/br
- circular: circleopen, circleclose, circlecrop, radial
- fade: fade, fadeblack, fadewhite, fadegrays, fadefast, fadeslow, dissolve
- single looks: hblur, pixelize, squeezeh/v, distance, rectcrop
- slice/wind: hlslice, hrslice, vuslice, vdslice, hlwind, hrwind, vuwind, vdwind

### The approved v4 sequence (planner order)
fade, slideleft, circleopen, hblur, pixelize, radial, coverup, wipetl, **fadewhite on the drop**, hlslice, squeezeh, vertopen, diagbl, slideup, distance, circleclose, fade (ending).

## Transition mechanics (do not change casually)
Transitions last 4 frames (2 either side of the cut) so the cut stays on the beat; the renderer pads each shot by 2 frames only on sides that touch a transition and asserts that the assembled length equals the project length.

## If the user asks for more
Do not just add. Show the current counts, propose one more *kind* of effect, and say what it replaces. Raise `style.budget` only with their explicit agreement.
