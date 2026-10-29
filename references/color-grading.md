# Colour correction and grading

**Default: natural colour.** `style.grade` is `neutral` (no creative change) and `style.correct` is added only for a shot with a real problem. Creative looks, and the warm end flare, are used only when the user asks for them; `desert-gold` on a desert trip was judged "far too warm". Do not use `color plan --wb` to neutralise sunsets, firelight or night scenes: they are warm on purpose.

Do it in this order, every time. A grade cannot rescue a shot that is technically wrong, and "fixing" exposure with a look makes every shot need a different grade.

```
1 input transform   HLG/PQ phone clips -> SDR Rec.709          style.hdr_tonemap
2 correction        exposure, black/white point, white balance  shot.correct / style.correct     (numbers, reviewed)
3 look (grade)      the creative decision                       style.grade / shot.grade          (one look per video)
4 finish            vignette, legal range, dithering            part of the look / final encode
```
Everything between 1 and the final conversion runs in 16-bit planar RGB (`gbrp16le`) and is dithered once, so skies do not band.

## 1. Input transform (HDR iPhone clips)
`style.hdr_tonemap`: `hable-203` (default), `mobius-203`, `reinhard-203`, `legacy`. The 203 variants place HLG reference white (203 nits, ITU-R BT.2408) at SDR white: cabin interiors stay readable and skies keep detail. `legacy` (peak 1000 nits) is what Maranjab v1-v4 used and is 6-10 levels darker with flatter highlights; use it only to reproduce an old render. On the real footage the legacy mapping also created three brightness jumps above 60 levels between cuts that disappear with `hable-203`.

## 2. Correction
```json
"correct": {"ev": 0.25, "black": 0.04, "white": 0.95, "gamma": 1.0, "gain": [1.02, 1.0, 0.97]}
```
| Field | Meaning | Range |
|---|---|---|
| ev | exposure in stops, applied in linear light | -3..3 |
| black / white | levels: encoded values mapped to 0 / 1 | black 0..0.3, white 0.6..1 (range at least 0.4) |
| gamma | mid-tones (above 1 = brighter mids), ends fixed | 0.5..2 |
| gain | per-channel gains in linear light = white balance (warmer: R up, B down) | 0.5..2 each |

Set `style.correct` for every shot or `shot.correct` for one. Identity values are dropped.

### `color plan`: measure, then decide
```
color plan video.edl.json --match exposure          # dry run: prints the numbers and WHY
color plan video.edl.json --match exposure --write  # stores them in the EDL (existing corrections are kept; --overwrite replaces)
color plan ... --match reference --reference 12     # make shots look like shot 12 (exposure and colour balance)
color plan ... --match none                         # only repair technical problems, no cross-shot matching
color plan ... --wb off|median|gray-world --strength 0.8 --only 0-9
```
What it does, and why it is conservative (all learned on the real desert footage):
- **Levels first, then exposure.** Black/white points move the mean; exposure aims from where the mean ends up.
- **Black point only when the darkest PIXEL (YMIN) is high** (above 0.12). A bright desert has a high 10th percentile and is not hazy: the first version raised every shot's black point by 0.12 and crushed shadows.
- **White point only when the brightest pixel (YMAX) stays below 0.80.**
- **Exposure:** dead band 0.07 in mean luma, at most +-0.45 EV, at most +0.25 EV for low-key shots (a dark cabin is meant to be dark).
- **White balance `median`** moves a shot a little (at most +-10%) towards the median colour balance of all shots: consistency, not neutrality. **`gray-world`** assumes the average colour is neutral, which is false for a blue sky or an orange desert (it asked for -10% red / +19% blue on clear sky), so it is opt-in and capped at +-15%.
- It is a proposal. Always review: `color scope clip.mov --at 3.2 --out scope.jpg` and `color analyze clip.mov --at 3.2 --scope s.jpg`, then edit the numbers if needed.

Check the result with the gate: `qa` reports `exposure-jumps` (median brightness change across hard cuts). On the Maranjab cut: 12 levels before, 10 after correction, 7 with the look.

### Reading a scope (`color scope`)
The sheet shows frame, luma waveform, vectorscope and RGB histogram.
- **Waveform** (top right): the trace should reach near the bottom (blacks) and near the top (whites) without flattening against the edges. A compressed trace in the middle = flat/hazy (black/white point); a pile-up at the top = clipped highlights.
- **Vectorscope** (bottom left): the cloud should sit around the centre. Pointing towards the orange/red lines = warm, towards blue/cyan = cool; a strong lean on every shot of a video is a cast, a lean that differs between shots is a mismatch.
- **Histogram**: red, green and blue peaks that sit apart in the shadows or highlights show a colour cast there.

### `color cameras`: match the cameras to each other (several cameras on one trip)
Phones and action cameras render the same light differently; cutting between them makes the picture jump. `color cameras`
estimates each camera's colour offset ONLY from clips of two cameras that started within a minute of each other (the same place,
the same light), keeps an offset only with enough pairs and well above its own uncertainty, and matches the other cameras to the
one that filmed most of the trip (`--reference "DJI OsmoPocket3"` to choose another). It never touches the light of a scene.
```
color cameras --work W                                   # the evidence and the gains (writes W/camera_match.json)
color cameras --work W --edl video.edl.json --write      # multiply the gains into the shots' corrections (once)
```
On the first real trip (iPhone 17 Pro Max, iPhone 15 Pro, DJI Osmo Pocket 3; 130 simultaneous pairs) the iPhone 17 was 5 % redder
and 8 % less blue than the Osmo. Estimated on day 1 and applied through FFmpeg to day 2, the camera difference fell from 0.135 to
0.102 (the Osmo-iPhone 17 offset roughly halved; gains are capped at +-12 %). After `color cameras`, `color plan` leaves white
balance alone unless `--wb` is given (pulling every shot to the median balance would also flatten real differences of light), and
a later `color plan --overwrite` keeps each shot's camera gain. Look at a frame of each camera after matching (`color scope`).

## 3. Looks
`style.grade` (or `shot.grade`): a look name, or an object `{"look": "desert-gold", "strength": 0.8, ...overrides...}`. `color looks` lists them; `color lut --look NAME --out x.cube` exports one as a 3D LUT for other software.

| Look | Character |
|---|---|
| neutral | nothing |
| clean-bright | fresh vlog: lifted mids, gentle contrast, vibrance |
| desert-gold | warm sand and deep blue sky, golden highlights, cool-teal shadows, mild vignette (the look for Maranjab) |
| teal-orange | warm skin/sand against teal shadows and sky |
| golden-hour | amber cast, soft contrast, glowing highlights |
| cinematic-soft | lifted blacks, rolled-off highlights, muted colour, cool shadows |
| film-faded | milky blacks, softened highlights |
| bleach-bypass | desaturated, heavy contrast |
| moody-dark | low-key, dense shadows, cool tint |
| vivid-travel | punchy, saturated, crisp |
| bw-contrast | black and white with a red filter, strong contrast |

A look is built from grading vocabulary: `exposure` (EV), `contrast` + `pivot` (a filmic S-curve; slope 1+contrast at the pivot, ends fixed), `black`/`white` (lifted blacks / lowered whites), `shoulder` (soft highlight roll-off from this level), `cdl` {slope, offset, power} per channel (ASC CDL), `shadows`/`mids`/`highlights` (split-toning tints, +-0.2), `saturation`, `vibrance` (boosts muted colours more), `hue_shifts` [{center, width, shift, sat}], `mono` {weights}, `vignette` (0..1; 0.2 = the gentle vignette of Maranjab v4).
The whole look is baked into one 33^3 `.cube` LUT (cached per look and strength) and applied with tetrahedral interpolation. `strength` blends towards the untouched picture.

Your own LUT: `"grade": {"lut": "D:/luts/film.cube", "lut_strength": 0.7}` (3D `.cube` only; the file is validated first, strength below 1 bakes a blended copy). Combine with `look` if you want both.

Legacy `style.grade` values still work: a grade preset name (`warm-desert`, `neutral`) or a raw FFmpeg filter string is applied as before, so v1-v4 renders reproduce.

## Rules of thumb
- One look per video (the gate warns above two). Vary strength, not the look.
- Correct first, grade second. If a look needs a different exposure on every shot, the correction is missing.
- Skin and sky are the reference: if people turn orange or the sky turns cyan, lower the strength.
- Vignette: FFmpeg's `angle` is a lens angle, **larger = darker corners**. Amount 0.2 is the approved level; do not go above 0.4.
- Never rely on automatic correction without looking at the frames and, for doubtful shots, the scopes.

## Secondary correction (hue-qualified)
A shot's `correct` may carry a `secondary` list that adjusts only pixels near a hue (sky, foliage, skin), after the
primary correction and before the look. Each entry is `{ "center": hue 0-360, "width": degrees, "sat": 0-2,
"shift": -60..60, "gain": 0.5-2 }`; it is baked into a small cached LUT. Example: desaturate a cyan sky without
touching skin — `"correct": {"secondary": [{"center": 220, "width": 50, "sat": 0.5}]}`. White balance has a
skin-protecting mode: `color plan --wb skin` neutralises a cast but never cools the image.
