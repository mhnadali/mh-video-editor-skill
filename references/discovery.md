# Discovery interview (run in the main conversation, in the user's language)

Goal: a confirmed `video-brief.md` before any editing. Ask **one batch of focused questions** (4 to 6), with a recommended default for each so the user can answer in one line. Skip anything the user's message or the project already answers, and say what you inferred.

First, silently: run `doctor`, `probe` the footage folder and `beats` the music if a path is known. Open the interview with what you found (e.g. "474 clips, 128 min, 6 HDR, 124 vertical; the music is 28.2 s at 92 BPM") so the questions are concrete.

## Questions

1. **Format**: vertical 9:16 (Reels/Shorts/TikTok/Stories), landscape 16:9 (YouTube/TV), square 1:1, or several? *Default: what most clips already are.*
2. **Length**: exact seconds, or "match the music"? If the user gives a music file with a length, that length wins.
3. **Music**: path to the file (the engine never downloads music). No music -> silent edit with natural sound off; ask whether to keep clip sound.
4. **On-screen text: do NOT ask and do NOT add any.** The default is a video without text. Only if the user themselves asks for a title or captions in their own words, read `references/text-persian.md` and preview it with `text preview` first.
5. **Mood and pace**: fast and energetic / calm cinematic / mixed. *Default: cut on main beats; hard cuts for roughly half the cut points.*
6. **Content rules**: what must appear, what must NOT appear (e.g. no food, no faces, no logos), any clip the user already loves.
7. **Effects**: say the default out loud: "a few effects only (gentle zoom, one flash on the drop, one sun flare at the end) and varied transitions". Ask if they want more or less. Never stack effects on every beat.
8. **Delivery**: file name/folder, and whether 16:9 or 1:1 versions are wanted too (`render --all-formats`).

Do not ask about things you can measure (clip lengths, HDR, resolution, tempo).

## video-brief.md template

```markdown
# Video brief: <name>
Status: CONFIRMED (<date>, confirmed by <user>)

- Footage folder: <path>            (inventory: <n> clips, <minutes> min, <hdr> HDR, <dups> duplicates)
- Music: <path>   (<seconds> s, <bpm> BPM, drop at <t> s)
- Format: <9:16 | 16:9 | 1:1>, <fps> fps
- Duration: <s> s
- Pace and mood: <...>
- On-screen text: none (never asked; added only if the user asks for it unprompted)
- Must include: <...>
- Must NOT include: <...>
- Locked clips (do not change): <...>
- Effects budget: <default | custom>
- Output: <folder>, name <name>
- Language for reports: <e.g. Persian>
```

After the user confirms, treat the brief as frozen. Later requests are scoped edits to the EDL.
