# Transition craft: which transition, and why

A transition says something a hard cut cannot: *time passed*, *we moved somewhere else*, *the energy changed*, *a section ended*, *look at this*. If it says none of these, use a hard cut on the beat: it is stronger. The restraint budget (`effects-and-transitions.md`) caps how many you may use; this file is about choosing the right ones.

**Engine today:** every transition overlaps 4 frames (about 0.13 s at 30 fps), so all of them are *quick* blends that keep the cut on the beat. Long, slow dissolves are not available yet; for a slow moment use a hard cut after a held shot, or `fadeblack` / `fade` at the very start or end. Do not pretend a 4-frame dissolve is a slow one.

## What each family means (`vidlib.TRANSITION_FAMILIES`)

| Family | Names | It says | Use it when | Never when |
|---|---|---|---|---|
| **fade / dissolve** | `fade`, `dissolve`, `fadegrays`, `fadefast`, `fadeslow` | Soft passing of time, gentle link | Calm sections, start and end of the video, same mood into same mood | On a strong beat of a fast section (it softens the energy you built) |
| **dip to black** | `fadeblack` | A pause, an ending, a big jump in time or place | The close, or a chapter break after a build | Mid-build (it kills momentum); more than once or twice |
| **dip to white / flash** | `fadewhite` | Impact, light, revelation | **The drop** (the planner forces it there when `drop_flash` is on) | Anywhere else: an event only works if it is rare |
| **slide / smooth / cover / reveal (horizontal)** | `slideleft`, `slideright`, `smoothleft`, `smoothright`, `coverleft`, `coverright`, `revealleft`, `revealright`, `wipeleft`, `wiperight`, `horzopen`, `horzclose` | Travel, "next place", a lateral move | The content at the cut is already moving sideways: the slide *continues the motion* | Static shots, or against the direction of travel (it fights the footage) |
| **slide / wipe (vertical)** | `slideup`, `slidedown`, `smoothup`, `smoothdown`, `coverup`, `coverdown`, `revealup`, `revealdown`, `wipeup`, `wipedown`, `vertopen`, `vertclose` | Rise, descent, drone up/down, "reveal" | A tilt, a drone climb or descent, a rising section | Horizontal motion, or when a horizontal slide was just used (same look) |
| **diagonal** | `diagtl`, `diagtr`, `diagbl`, `diagbr`, `wipetl`..`wipebr` | Dynamic, playful turn | Energetic montage, a corner-to-corner motion | Calm or serious moods |
| **circular** | `circleopen`, `circleclose`, `radial` | Focus, a reveal from a point | Opening onto a landmark, a subject at the centre | When the point of interest is at the edge |
| **blur** | `hblur` | Speed, a whip pan | Fast horizontal camera motion at the cut | Static footage (the blur appears from nowhere) |
| **pixelize** | `pixelize` | Digital, glitch, tech | Tech or playful briefs | Nature, travel, warm moods |
| **slice / wind** | `hlslice`, `hrslice`, `vuslice`, `vdslice`, `hlwind`, `hrwind`, `vuwind`, `vdwind` | Mechanical, rhythmic cuts | Beat-driven sections with a graphic feel | Organic footage |
| **squeeze, distance (morph)** | `squeezeh`, `distance` | Stylised warp | Rarely: a deliberate stylistic accent | Serious or documentary moods |
| **banned** | `zoomin`, `rectcrop`, `circlecrop`, `squeezev` | (render a flat frame, a black frame or crash) | Never | `vidlib.BANNED_REASONS` |

Count **looks, not names**: `slideleft`, `smoothleft` and `wipeleft` are one look. The budget allows at most three of one family and each name once.

## Decision tree (top to bottom, the first that fits wins)
1. **Is it the drop?** -> `fadewhite` (forced by the engine; do not add another flash).
2. **Is it the first or last cut of the video, or a chapter break after a build?** -> `fade` (calm) or `fadeblack` (final).
3. **Does the content at the cut move sideways or vertically, and does B continue that motion?** -> a **cut on action** (hard cut) is usually best. If you want to emphasise it, use the slide/wipe *in the direction the content moves*; at high speed with blur, `hblur`.
4. **Do A and B look alike in form, colour or position (graphic match)?** -> hard cut; a transition would break the bridge.
5. **Is there a big change of place, time, light or palette, in a calm stretch?** -> `fade` / `dissolve` family.
6. **Is it a section boundary of the music (`beats.sections`), going up in energy?** -> a directional slide/wipe or a circle reveal; going down -> `fade`.
7. **Does the brief ask for a graphic or tech feel?** -> `pixelize`, slice/wind, diagonal, but in one section only.
8. **None of the above.** -> hard cut on the beat. Do not add a transition to fill the budget.

## Direction rule
A slide or wipe has a direction. It should agree with how the content *appears to move on screen* at the cut: if the scene slides left because the camera pans right, a `slideleft` continues that motion, a `slideright` fights it. Verify the engine's sign for `motion_dx` against a sample clip before trusting an automatic choice (the decision engine's tests do this), and look at one frame of the result.

## Mood and tempo
- Calm, warm, nostalgic: `fade`, `dissolve`; hard cuts after held shots.
- Energetic, sporty, off-road: hard cuts on action; `hblur`, slides in the direction of travel; one flash on the drop.
- Elegant, minimal: almost no transitions; `fade` at both ends.
- Playful, tech: `pixelize`, diagonal, slice: one family per section.

## Mechanics you must respect
Transitions last 4 frames, centred on the cut so the cut stays on the beat; the renderer asserts the assembled length equals the project length. Do not put a transition between two very short shots (each shot must keep enough picture of its own); the linter checks structure and budget, you check taste with a frame from the contact sheet.

## Rationale
For each transition you use, write why: `"3": "slideleft - the quad bike moves right-to-left at the end of A and B continues it"`. If you cannot write that sentence, delete the transition.
