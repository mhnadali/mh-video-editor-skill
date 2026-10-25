# Recipes

Short, copy-pasteable edits on an existing `work/video.edl.json`. Every change is an edit to the EDL followed by a cached re-render, so only the touched shots are rendered again. Field details are in the references; the whole command list is in [`CLI.md`](CLI.md).

Shortcut: `python engine/scripts/mh_video.py` is written `mh` below.

## 1. Add a Persian title and an English caption
Add to the EDL (top level):
```json
"texts": [
  {"text": "کویر مرنجاب", "start": 0.4, "end": 3.0, "position": "title", "style": "title", "animation": "fade"},
  {"text": "Maranjab | Oct 2026", "start": 0.8, "end": 3.0, "position": "bottom", "style": "subtitle"}
]
```
```
mh text fonts
mh text preview work/video.edl.json --at 1.5 --out frame.png      # look at frame.png before rendering
mh render work/video.edl.json --preview
```
Details: [`references/text-persian.md`](../references/text-persian.md).

## 2. One edit, three formats
```
mh render work/video.edl.json --all-formats --qa
```
Gives `trip_9x16.mp4`, `trip_16x9.mp4` and `trip_1x1.mp4`. Vertical clips in the 16:9 version sit over a blurred copy of themselves. For a subject at the left edge set `"focus": [0.2, 0.5]` on that shot and use `"reframe": "cover"`. Details: [`references/formats.md`](../references/formats.md).

## 3. Even out the colour, then give the video a look
```
mh color plan work/video.edl.json --match exposure             # prints the numbers and the reasons
mh color plan work/video.edl.json --match exposure --write
mh color scope "D:/footage/clip.mov" --at 3.2 --out scope.jpg  # check a doubtful shot on the scopes
```
Then in the EDL: `"style": {"grade": {"look": "desert-gold", "strength": 0.9}}`, render a preview and run `qa`; the `exposure-jumps` line should improve. Export the look for another editor: `mh color lut --look desert-gold --out desert-gold.cube`. Details: [`references/color-grading.md`](../references/color-grading.md).

## 4. Steady a shaky shot and slow the drop
```json
{"clip": "D:/footage/car.mov", "span": [3.2, 4.0], "stabilize": "light"}
{"clip": "D:/footage/jump.mov", "span": [8.5, 9.8], "speed": {"from": 1.0, "to": 0.5}}
```
Slow motion counts as one of the three visible effects; `lint` tells you when the budget is exceeded. Details: [`references/motion.md`](../references/motion.md).

## 5. Whooshes and the sound of the clips
```json
"style": {"sound_design": {"whoosh": true}, "ambient": {"level_db": -14}}
```
and `"audio": {"use": false}` on a shot whose own sound is unwanted. `mh sfx plan work/video.edl.json` prints what will be placed. You cannot hear the result through the tools: listen to it. Details: [`references/sound-design.md`](../references/sound-design.md).

## 6. The gate flagged a clip
`qa` printed `weak-clips` or `black-frames` with time stamps. Open the contact sheet at those times, then:
```
mh rank --work work --top 20                    # alternatives
```
Change that shot's `clip` (and `src_start`) in the EDL, keep the `span`, and render again: one part is re-rendered.

## 7. Reproduce an old render exactly
Videos made before 0.4.0 used the darker HDR mapping: add `"hdr_tonemap": "legacy"` to `style`.

## 8. Check an EDL without FFmpeg or footage (CI, another machine)
```
mh lint work/video.edl.json --no-files
```
Exit code 0 = clean, 1 = lint errors, 2 = unreadable file.

## 9. Interrupted render or corrupt cache
Just run the same render again; interrupted parts are never reused, and `--no-cache` or deleting `work/video_work` forces everything. Cache files are written atomically, so two renders may run at once.
