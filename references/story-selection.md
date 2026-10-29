# Story-first selection

Choosing shots because they "look nice on the contact sheet" produces a reel with no logic: the shots come from where the
most footage is, the loudest music carries the calmest picture, the same subject repeats, and the people the brief asked for
are missing. This is the method that replaces it. The engine ranks and checks; you decide and the user approves.

## The method

1. **Message.** One sentence on what the video is about and what the viewer should feel ("a team beating the desert", not
   "a trip to Maranjab"). It comes from the discovery interview (`discovery.md`), or from you as a labelled assumption.
2. **Chapters.** `story chapters --work W` drafts them from the capture timeline (a pause of 35 minutes starts a new
   chapter). Look at what is in each chapter (`sheets --only 140-180 --frames 3`: start, middle and end of every clip),
   then write `chapters.json`:
   ```json
   {"message": "a team beating the desert", "hook": "chronological", "min_people_share": 0.25, "max_per_topic": 3,
    "chapters": [
      {"name": "Dawn drive", "from": 0, "to": 31, "weight": 1.5, "energy": "calm", "roles": ["establishing"]},
      {"name": "Obstacles", "from": 32, "to": 150, "weight": 3, "energy": "building", "roles": ["action", "portrait"]},
      {"name": "Dunes", "from": 151, "to": 234, "weight": 4, "energy": "peak", "roles": ["action"], "order": "energy"}
    ]}
   ```
   `from`/`to` are inventory indexes (the inventory is numbered in capture order); chapters follow the trip and must not
   overlap. **Weight is what the story needs, not how much was filmed** (the draft starts at the square root of the minutes so a
   big block does not take everything). `energy` is `calm`, `building`, `peak` or 0 to 1. Optional per chapter: `must`
   (clip indexes to include), `avoid`, `roles` (wanted roles), `order` (`chronological` by default, `energy` for a montage
   whose inner order does not matter: the liveliest clips go on the loudest music).
3. **Shot notes.** `shot-notes.json` (`footage-reading.md`) gives each candidate a `role`, `mood`, `people` (true if a person is
   clearly in it), `topic` (food, tyres, camp ...), `shot_size` and `skip`. The more clips you describe, the better the choice;
   undescribed clips are judged on the measurements only.
4. **Select.** `story select --work W --beats beats.json --chapters chapters.json --notes shot-notes.json --duration 45
   --body-start <drop time> --pacing energy` writes `storyboard.json`, `storyboard.md` and a clip list. What it does:
   - makes the slots from the beat layout and gives each slot the music's energy;
   - shares the slots between the chapters so that peak chapters sit on loud music (a small dynamic programme over the
     chapter boundaries, counts within +-30 % of the weights, or the config's `"flex"` from 0 to 0.9);
   - picks the clips of each chapter by a score: quality 0.30, fit between the clip's energy and the slots 0.28, its own
     sound 0.08, spread over the chapter's time 0.10, plus bonuses for people (until `min_people_share`), an under-used
     camera and a wanted role, minus penalties for the same scene (picture hash), the same topic beyond `max_per_topic` and
     one shot size dominating;
   - starts action slots on the clip's **action moment** (its most moving window), others on its best-looking window;
   - gives every slot a reason and two alternates.
   `hook: "teaser"` opens with the best action shot of the peak chapter (a flash-forward). The default is strictly
   chronological: the trip's order wins unless the user asks for a teaser.
5. **Storyboard checkpoint.** Show `storyboard.md` (chapters, every shot with its reason and alternates, coverage) and wait for
   the user's approval or changes (swap a shot, change a weight). Only then `plan --clips <clip list> --smart-transitions ...`.
   An autonomous agent cannot ask: it stops after writing the storyboard (`NEEDS_APPROVAL`) unless the caller pre-approved it.
6. **Audit.** After planning, `story audit video.edl.json --work W --beats beats.json --notes shot-notes.json --chapters
   chapters.json` checks the EDL against the same principles and prints findings:

   | Finding | Meaning |
   |---|---|
   | `trip-order` | a shot is earlier in the trip than the one before it |
   | `chapter-missing` / `chapter-balance` | a chapter without a shot, or far more or less than its weight |
   | `camera-underused` | a camera that is a big part of the library is barely used |
   | `few-people` | people are in fewer shots than the story asked |
   | `topic-run` | three or more shots in a row on one topic |
   | `energy-mismatch` / `energy-curve` | loud music under a calm shot; the pictures do not follow the music |
   | `short-reading-time` | a wide, establishing or portrait shot is on screen less than 0.9 s |

## Selection depth (0.16): who gets in, and who stays out
Before any clip competes for a slot it must be usable. `story exclusions --work W [--chapters chapters.json]` prints why each
clip is left out and writes `exclusions.json/.csv`.

| Reason | Rule |
|---|---|
| `short` | shorter than 5 s (`filters.min_duration`); never used, not even as `must` |
| `dark` / `blurry` / `shaky` / `covered` / `internal-cut` / `low-quality` | measured defects; a chapter short of clips may relax these, never the next two |
| `same-moment:N` | the weaker take of a moment filmed twice (same camera within 150 s, or two cameras within 90 s, with the same picture, colours and light): only the best take is kept, chosen by quality, steadiness, sharpness, exposure and length |
| `banned` | the user banned it in the review page |

`story select` then optimises the **whole sequence at once** (a beam search): every shot is scored alone, and every neighbour pair
is scored too (the same shot size twice, a jump of light, a reversing direction, a colour clash, the same topic); the three
variants (`--variants`: balanced, action, people) tell the same story three ways. `--method greedy` keeps the older chapter-by-chapter
choice. `story refine` moves each start time to the best moment inside the clip (sharp, steady, the action peak, away from the
end of the clip). With the optional offline plug-in (`python plugins/vision/setup.py`, then `vision run --work W`) people and faces
are found automatically; your `people` notes always win.

Two chosen shots that only look alike (a looser reading than the one that drops a clip) are flagged in the storyboard's warnings and on the review page, never dropped. People found only by the face detector count as part of a person (0.25, or 0.6 for a face in three frames) because it is right about half the time; your `people` notes count fully.

`story audit` also reports `drop-weak` when the shot that starts on the music's drop is a calm picture. The measured shot size is only wide or medium (63 % agreement with shots labelled by eye); a close-up comes from a large certain face or from your notes, so write `shot_size` in `shot-notes.json` for the shots that matter.

### Reviewing and teaching
`story review` writes `review.html` and a `review_media/` folder of small proxy videos (keep them together; `--no-video` for a single
file with frames only). The page shows the whole edit as a timeline, every chosen clip and its alternates as three frames and a
player that starts at the shot's start, the reason for each choice, the music against the pictures, and what was left out and why.
*Start here* sets a shot's start to the frame on screen; decisions are saved in the browser as you go and can be exported,
re-loaded or cleared. This page is the one job in the pipeline that needs eyes, and it needs no server or install, so a separate
desktop GUI would add nothing to it; the engine's other steps are commands.
`story review --work W --storyboard storyboard.json` writes one offline HTML page: a strip of three frames per chosen clip and per
alternate, the reason for the choice, and the music's energy against the shots'. The user presses *Keep*, *Use this one* or *Ban*
and exports `decisions.json`; `story feedback --work W --storyboard storyboard.json --feedback decisions.json` applies it (the
storyboard changes) and remembers the taste in `profile.json`: topics and roles gain or lose a small, capped bias, a clip chosen in place of another is favoured and pinned (it also wins over a better-measured take of the same moment),
banned clips are never used (a slot that used one takes its first usable alternate, or is left out of the clip list until `story select` runs again),
kept clips are pinned. A replacement can never be a clip already in the story, a banned clip or one under 5 s. The next `story select` uses it automatically (`--profile`
to point elsewhere, `--no-profile` to ignore it; a global profile at `~/.mh-video-skill/profile.json` or `MH_PROFILE` applies to
every project, the project's own file wins). `story profile --work W` shows what is remembered. Nothing is learned without an
explicit decision, and the file is plain JSON the user may edit.

### Other lengths from one approval (cut-downs)
Once the user approved a storyboard, other lengths need no new approval of every shot:
`story select --work W --beats beats.json --chapters chapters.json --duration 15 --cut-from storyboard.json --out storyboard_15.json`
uses only the approved shots, each at its approved start, and fills the new music by the same rules. When the new length has
fewer slots than there are chapters, the chapters of lowest weight (then lowest energy) are left out with a warning. On the first
real trip a 15 s cut of the approved 45 s storyboard kept 11 of 35 shots, all in trip order, the stronger ones (mean score 0.80
against 0.76 for the dropped ones), and left out three light chapters. Other aspect ratios come from the render
(`render --format` / `--all-formats`, see `formats.md`). Show the cut-down's storyboard before rendering it.

### Taste across projects
`story profile --work W --promote` adds the project's topic and role biases (averaged with what the global profile holds) and its
weights to the global profile (`~/.mh-video-skill/profile.json`, or `MH_PROFILE`); clip decisions (banned, favoured, pinned)
belong to that footage and stay in the project. `story profile --work W --forget-global` shows and deletes the global profile.

### Measuring the selection
`python engine/tools/eval_selection.py --check` builds a synthetic library with a known truth (short clips, same-moment copies on
one and two cameras, people clips) and reports: short clips used (must be 0), both takes of a moment used (0), best take kept,
order errors (0), slots filled, chapters covered, distinct pictures, energy fit with the music, people share. The tests run it.

## Principles behind the numbers
- **Importance, not volume.** A 4 % block of the footage can be a chapter of its own.
- **Energy follows the music.** The biggest musical moment gets the strongest picture, not a plate of food.
- **People are the story.** A reel of landscapes and cars with the team missing is a postcard.
- **Variety is enforced, not hoped for:** scene, topic, shot size, camera.
- **See what happens.** One frame per clip hides the moment; use three-frame strips and the action window.
- **Nothing is final until the user has seen the storyboard.**

## Limits
The engine cannot see faces, meaning or whether a moment is finished. `people`, `topic`, `role` and `mood` come from your notes.
`sound` is the clip's loudness (engines, voices, cheering), not a recognition of what is heard. The slot lengths still come from
the beats; a wide shot of one beat is flagged `short-reading-time` rather than lengthened automatically.
