# Cut craft: which cut, and why

A cut is a decision, not a default. Before every boundary between shot A and shot B, you must be able to say **what kind of cut it is and why the viewer needs it there**. "It is the next beat" is a rhythm reason, the weakest one (see the priorities in `editorial-rubric.md`); say what else the cut does.

The engine measures (shot size, camera motion direction and size, the point of interest, similarity, brightness, energy, music sections). You judge, because you have seen the frames. Numbers below are guidelines, not laws: the brief and what you see win.

## The question to ask at every boundary
1. **What did the viewer just see and feel?** (end of shot A: its motion, direction, subject, light, energy)
2. **What do they need next?** (more of the same, the detail, the reaction, the wider context, a change of pace)
3. **What does shot B give?** (its size, motion, subject, light, role from `shot-notes.json`)
4. **Which relation connects A and B?** Pick one from the catalogue below, write it as the cut's `type` and give a one-sentence `why`.
5. **Is a transition needed, or is the hard cut stronger?** (`transition-craft.md`; a transition must earn its place)

## Catalogue

| Cut | What it is | Use it when | Do not use it when | Cue the engine gives |
|---|---|---|---|---|
| **Hard cut on the beat** | Instant change at a main beat | Default of this skill; energy, clarity | Two near-identical shots (it reads as a mistake) | `beat-alignment` (QA), beat strength |
| **Cut on action / match-on-action** | Cut while something is moving, continuing the same movement in B | Camera or subject is moving at the end of A and B continues that movement in the same direction: the cut hides itself | The movements reverse direction; B is static | `motion_mag` high in both, `motion_dx` same sign |
| **Graphic match** | B echoes A's shape, colour or position (point of interest in the same place) | Two different places that look alike: a bridge of form carries the viewer | The match is accidental and the content clashes | `focus_x/y` close, similar brightness/colour |
| **Eyeline / direction match** | Subject looks or travels one way in A, B shows where they look or arrives from the other side | Travel, driving, walking: A leaves frame right, B enters from the left | You would cross the line (see 180 degrees) | `motion_dx` sign, `screen-direction` note |
| **Contrast cut / smash cut** | Sudden jump in loudness, brightness or energy | Payoff, humour, after a calm breath, on the drop | Overused: it works once or twice per video | large brightness or energy delta |
| **Cutaway / insert** | A detail or reaction shot inserted into a run of the same scene | Hides a jump cut, adds information, gives the eye a rest between two similar shots | The detail is unrelated (it becomes a non sequitur) | `similar_to`, `jump-cut` warning |
| **Cross-cut / parallel** | Alternating two lines of action | Two places or subjects that build to one point | Short videos with no second line of action | `shot-notes` subjects |
| **Montage cut** | A run of short shots joined by theme, not continuity | Energy, "the trip in 20 seconds"; every cut on the beat | No unifying theme (it feels random) | `pacing`, `energy` |
| **Breathing / hold cut** | A longer shot after fast ones | Let the picture land after a build, before the payoff, at the ending | Everywhere (the video drags) | calm beats (`--pacing energy`) |
| **J-cut** | The sound of B starts before its picture | Smooth, natural entry into a new place; the ear leads the eye | There is no usable sound of the clips (music-only edit) | `shot.audio.lead` (0.11) |
| **L-cut** | The sound of A continues over the picture of B | Keep the mood while the picture changes | Same as J-cut | `shot.audio.lag` (0.11) |
| **Jump cut** | Two similar shots of the same subject and size | Almost never; sometimes deliberate for comedy or rhythm | Always avoid by changing size or angle by about 30 degrees or more, or insert a cutaway | `jump-cut` warning |

## Rules that carry most of the quality
- **Vary size and angle**: wide (establish), medium (develop), close or detail (emphasis). Three shots of one size in a row (`shot-size-repeat`) is monotone. Going wide -> medium -> close is the natural way to move in; the reverse is a release.
- **30-degree rule**: two successive shots of the same subject differ in angle by about 30 degrees or more, or in size by a clear step (e.g. medium to close). Less reads as a jump cut.
- **180-degree rule**: keep the camera on one side of the line of action, so left stays left. Two shots of a car heading left and then right look like it turned around. The engine approximates this with the camera's horizontal motion (`screen-direction`); confirm by eye.
- **Cut on movement, not on stillness**: a cut hidden in motion is invisible; a cut between two static frames is noticed. Prefer to end A and start B in motion when the brief wants smoothness.
- **Reading time**: the viewer needs time to understand a shot. Wide and complex frames need longer (about two beats or more at short-form tempos); close-ups and details can be one beat. Use `plan --pacing energy` so calm beats are held and strong beats cut fast.
- **One idea per shot**: if the shot contains a good moment and a boring remainder, choose `src_start` on the moment (`score` gives `best_start`).
- **Land the biggest change on the biggest musical boundary**: the drop and the section boundaries (`beats.sections`).
- **Establish, then detail**: a new location starts wide. A detail shot first needs a reason (a hook).
- **Short-form hook**: the first one to two seconds carry the strongest, clearest frame; do not open on a transition.

## Movement and the direction of cuts
Direction is continuity, not symbolism: reading direction and culture change what left and right "mean", so do not lean on it. If the content of A travels toward one side of the frame, start B travelling the same way, or break it on purpose (a clash) and say that is intended in the cut's `why`.

## Engine support for the sound cuts and for match on action
- **J-cut / L-cut:** `shots[i].audio = {"lead": 0.4}` lets that shot's own sound enter 0.4 s before its picture; `{"lag": 0.3}` lets it linger. Needs `style.ambient` and clips with audible sound (wind, engine, voices). See `references/edl-format.md`.
- **Match on action:** `cuts EDL --scores DIR --align` proposes a `src_start` for a shot whose clip has a window moving the way the previous shot ends; `--write` stores it. It reads frames, so confirm the result on the contact sheet.

## What the engine cannot see (you must)
Faces and expressions, the meaning of a moment, whether a gesture is complete, culture and taste. Read the footage (`footage-reading.md`), write what you see into `shot-notes.json`, and let those notes override a heuristic.

## Pitfalls
- Never justify a cut with "variety" alone; a type without a reason is decoration.
- Do not stack: a hard cut on action that also carries a transition is two ideas at once.
- A cut that works in the preview can fail at full size (motion blur, crop); check a frame at the cut in the contact sheet.
