# On-screen text, Persian first

Text is rendered by libass (HarfBuzz shaping + FriBidi right-to-left layout) from an ASS file the engine writes from the EDL. Persian letters join, run right to left and use Persian digits; mixed Persian/English lines work.

```json
"texts": [
  {"text": "کویر مرنجاب", "start": 0.4, "end": 3.0, "position": "title", "style": "title", "animation": "fade"},
  {"text": "مهر 1405", "start": 0.8, "end": 3.0, "position": "bottom", "style": "caption", "animation": "slide-up"},
  {"text": "Maranjab | Oct 2026", "start": 1.0, "end": 3.0, "position": "bottom", "style": "subtitle"}
]
```
| Field | Values |
|---|---|
| text | any Unicode; `\n` = line break; lines are balanced automatically (minimum raggedness) |
| start / end | seconds on the video timeline (at least 0.8 s on screen: the linter warns below that) |
| position | `title` (30% height), `center`, `top`, `bottom`, `lower-third` (hugs the right edge for Persian, left for English), `custom` with `x`,`y` 0..1 |
| style | `title` (5.8% of the height, bold), `caption` (3.6%, bold), `subtitle` (3.0%) |
| lang | `auto` (the first strong letter decides), `fa`, `en` |
| digits | `auto` (Persian text gets Persian digits), `fa`, `en` |
| animation | `fade` (0.3 s), `pop`, `slide-up`, `none` |
| size, color (#RRGGBB), outline, outline_width, shadow, box, box_color, align, font, font_file | overrides |

`style.text`: `{"font": "B Nazanin"}` sets a default family.

## Fonts
`text fonts` shows what will be used. Font files are found by reading their `name` table (so `IRANSans(FaNum)` is the family `IRANSansFaNum`), the chosen files are copied to `<work>/text/<format>/fonts` and passed to libass with `fontsdir`, which makes rendering independent of the OS font service. Preference for Persian: IRANSansFaNum, IRANSans, Vazirmatn, Vazir, Sahel, Samim, B Nazanin, Dubai, Tahoma, Segoe UI, Arial. For English: Segoe UI, Arial, Tahoma, Calibri, Noto Sans. Drop a font file into `MH_FONT_DIR` or use `font_file` to add your own (Vazirmatn is not bundled and never downloaded). An unknown family falls back instead of failing.

## Safe areas
Reels/TikTok/Shorts cover the top 12% and the bottom 20% of a vertical frame with their own UI, and 7% on each side. The engine keeps `top`/`bottom`/`title`/`center`/`lower-third` text inside those zones for the format being rendered (landscape: 7% / 10% / 5%; square: 8% / 12% / 6%). `custom` positions inside the UI zones get a warning.

## Subtitles from a file
`text import-srt video.edl.json captions.srt --position bottom --style caption` appends the cues (UTF-8, CRLF/LF/CR, with or without BOM). `text preview video.edl.json --at 1.2 --out frame.png [--video render.mp4] [--format youtube-16x9]` draws the texts over a gradient or a real frame so you can check Persian shaping and placement before rendering. Look at it.

## Rules and pitfalls
- Every wrapped line of a right-to-left text is wrapped in its own RLE ... PDF embedding, otherwise libass can flip the order of a line that starts with a number.
- The half-space (ZWNJ, U+200C) is kept as written; Arabic yeh/kaf are normalised to Persian forms.
- The ASS file is written with a UTF-8 BOM (libass reads it as UTF-8 only then).
- Text sits under the final fades, so it fades with the picture.
- Text is content, not an effect: it does not count against the effect budget, but a reel reads better with little of it (the linter warns when more than three texts together fill over 60% of the video).
- Not supported: word-by-word karaoke timing, per-character colours, vertical text.
- The default brief is "no text": add it only when the user asked.
