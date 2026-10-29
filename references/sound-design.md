# Sound design and the natural sound of the clips

Everything is synthesised with FFmpeg; no sound files are downloaded or bundled. Music stays the user's own file.

## J-cut and L-cut (0.11)
`shots[i].audio.lead` / `lag` (seconds, 0 to 1) move a shot's own sound earlier or later than its picture, under the music. Use a lead when the next place has a distinctive sound (engine, waves, crowd) and you want the ear to arrive first; a lag to keep the mood of the shot you are leaving. Shots with `speed` keep their sound unchanged.

## Whooshes
```json
"style": {"sound_design": {"whoosh": true, "level_db": -24, "max_events": 12, "duck": true}}
```
A short whoosh is placed on every transition that moves, panned the way the picture moves:
| Transition | Whoosh |
|---|---|
| slideleft / smoothleft / wipeleft / coverleft / revealleft / hlslice / hlwind / horzopen | sweeps right -> left |
| the right-hand versions | sweeps left -> right |
| slideup / coverup / vertopen ... | brighter, centred |
| slidedown / vertclose ... | darker, centred |
| circleopen / circleclose / radial / fadeblack | long swell, centred |
| pixelize / hblur / distance / squeezeh / diag* / wipetl ... | short swish, centred |
| fade / dissolve / fadegrays / fadewhite | silent (soft transitions need no sound; the drop already has the boom) |

`max_events` keeps an even spread when there are more transitions than that. `level_db` is relative to nothing fixed: the whole mix is mastered afterwards.
**Ducking:** the music is lowered by about 4 dB under every whoosh (sidechain compressor with peak detection; the threshold follows `level_db`, because a fixed threshold never triggered for quiet whooshes). `"duck": false` turns it off.
`sfx plan video.edl.json` prints the whooshes and ambient segments without rendering.

The riser before the drop and the 50 Hz boom on it are separate (`style.riser`, `style.boom`).

## Ambient (the recorded sound of each shot)
```json
"style": {"ambient": {"level_db": -14, "fade_ms": 40, "shots": "all", "highpass_hz": 90}}
"shots": [{"clip": "...", "audio": {"use": false}}, {"clip": "...", "audio": {"gain_db": 3}}]
```
Wind, engines and voices under the music. The level is **relative to the music**: every shot's own mean level is measured and brought to `music mean + level_db` (gains limited to -12..+18 dB), so a loud clip does not jump out. Each segment is faded in and out (40 ms) to avoid clicks and high-passed at 90 Hz (wind rumble).
- Retimed (slow/fast) shots are muted unless `"audio": {"speed_follow": true}` (tempo follows the speed, 0.5-2).
- Clips without an audio track and silent recordings are skipped.
- `shots: [0, 3, 7]` restricts it to those shots.
- Ambient sound is part of the mix that gets mastered, so the loudness target still holds.
- **Wind and clipping are left out** (0.26): every shot's window is measured (`vid_audio`: the share under 150 Hz, whether that low
  sound repeats like an engine or not, the syllable rhythm of the speech band, clipping). A window that reads as wind (low and not
  periodic) or clipped is skipped and the render log says which shots; `"audio": {"use": true}` on a shot keeps it, `"skip": []`
  keeps everything, `"skip": ["wind", "clipped", "rumble"]` also drops engines. The readings are provisional until checked by ear:
  `audio scan --work W` measures every clip, `audio review --work W` makes a listening page of 30 excerpts, and
  `engine/tools/calibrate_audio.py --work W --labels audio_labels.json` compares; put better values in `"thresholds"`.

## The song's own ending (0.26)
`music fit --beats beats.json --edl video.edl.json` lists the ways to end on the song's real ending instead of a fade: cut the
music at a downbeat and go on at a later one, so the final hit lands 0.4-3 s before the end of the video. Each row shows the
loudness and band-balance step at the join and where the beats sit against the planned cuts after the join next to where the
original music had them (they must match: the speed of the ending and a shift of at most 0.1 s are chosen for that). `--preview
DIR` writes a short audio file per join; listen, then `--choose N --write` stores `project.music_edit` and the next render joins
the song with 15 ms fades on the downbeat. The music after the join is a different part of the song: tell the user, and look
at the end of the storyboard again.

## Mastering (always)
Music + riser + boom + whooshes + ambient are mixed, then loudness is set on the FINAL mix in two passes (measure, then a linear gain), target `style.loudness` (default -14 LUFS), true peak held at -2 dBTP, sample peak limited to -1.5 dBFS. The QA gate measures loudness, true peak and silences.

## What the tests found (keep these in mind)
- `sidechaincompress` ends when its SHORTEST input ends: the key (whoosh bus) must be padded to the full length or the music disappears after the last whoosh.
- RMS detection did not duck short whooshes at all; peak detection with a level-relative threshold does.
- A sparse click track has such a high crest factor that the master limiter flattens its kicks: test ducking on the pre-master mix.
