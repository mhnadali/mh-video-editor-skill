# Editorial rubric — the craft of the cut

This is the decision framework the agent follows so the edit has editorial *intent*, not just beat-synced clips. The
engine measures (shot size, camera motion, saliency, musical sections, colour balance); this rubric is how the agent
(you) turns those numbers, the brief, and what you see in the frames into professional choices. The engine never ships
a creative decision it cannot measure and explain; taste stays with the agent.

## 1. Know the purpose first (Track B)
Before any cut, the confirmed `video-brief.md` must state the **purpose** (sell / inspire / document), the **audience**,
the **one message**, and the **call to action**. Every shot choice serves that purpose. If the brief is missing, stop
and run discovery.

## 2. Priorities of the cut (Walter Murch)
When shots compete for a cut point, decide in this order:
1. **Emotion** — does the cut serve how the viewer should feel? (highest priority)
2. **Story** — does it advance the message?
3. **Rhythm** — is it on the beat / musically right?
4. **Eye-trace** — does the viewer's gaze land where the next shot wants it?
5. **2D screen** — does screen placement stay coherent?
6. **3D continuity** — is spatial continuity preserved? (lowest; sacrifice first)

A cut that serves emotion and story may break spatial continuity on purpose. A cut that only serves rhythm is the
weakest kind.

## 3. Structure with a shape (Track B)
- **Hook** (first ~1 s, vertical especially): the strongest, clearest moment — stop the scroll.
- **Build**: rising energy; vary shot size and angle; establish → develop → detail.
- **Payoff**: the biggest picture change on the **drop** / the biggest musical section boundary.
- **Resolution**: breathe, land the message / CTA.

## 4. Shot grammar
The full catalogue of cut types, with when and why, is in `cut-craft.md`; transitions in `transition-craft.md`; reading the raw clips in `footage-reading.md`. A cut is *motivated* when you can name what it does for the viewer; write that reason down.

- Vary shot size: avoid three shots of the same size in a row (the `shot-size-repeat` warning).
- Establish before you go close in a new location.
- **Match on action** — cut on motion for an invisible cut; the engine reports cut-on-motion share.
- Keep **screen direction**: do not reverse camera/subject motion across a hard cut without reason (the
  `screen-direction` note flags the 180-degree line).
- Avoid **jump cuts**: a hard cut between two look-alike shots of the same size reads as a mistake (the `jump-cut`
  warning) — unless the style is deliberate.

## 5. Rhythm carries emotion (Track B)
Shot length is an emotional device. Hold on calm, accelerate into the climax, breathe at the end. Use `plan --pacing
energy` (hold calm beats, cut fast on strong ones) rather than a mechanical one-shot-per-beat when the brief wants feel
over metronome. The `pacing-flat` note warns when shot lengths barely vary.

## 6. Music is structure, not wallpaper (Track C)
- Choose the right track for the brief first (`engine/tools/pick_music.py` ranks the user's own library; never download).
- Cut to **sections** (`beats` reports intro / build / drop / outro) and put the biggest change on the biggest boundary.
- Land hard cuts on the beat grid; the gate measures how tightly cuts sit on beats.

## 7. Colour: correct, then grade (Track D)
Correct every shot and match it to its neighbours before applying one look. Protect skin tones (`color plan --wb skin`
warms only, never cools). One look per video unless the story needs a change (the `look-consistency` warning).

## 8. Reframe for the subject (Track E)
When changing aspect, keep the subject in frame: `reframe: "cover"` with `focus: "auto"` tracks the saliency centroid
instead of blindly centre-cropping.

## 9. Prove it is clean (Track F)
Before delivering, run the QA gate with `--scores` so the editorial, sync and colour checks run. Fix or explain every
warning; open the contact sheet and look at every timestamp the gate names. The gate cannot hear or judge taste — say
what you checked and what you could not.
