# Real-footage QA protocol

The automated gate (`qa`) measures the picture and the sound, but it cannot hear, and it cannot judge composition, story or taste. CI proves the engine runs on Linux, macOS and Windows with synthetic media. This checklist is for a human validating a build on **real footage and a real machine** — especially a Mac (Apple Silicon, VideoToolbox) or a non-NVIDIA PC, where the encode/decode paths differ from the author's.

## Setup
1. `python engine/scripts/mh_video.py doctor` — confirm `READY`, note the FFmpeg version and which hardware encoders are detected.
2. If you have no footage handy, generate a sample project: `python engine/tools/make_sample.py /tmp/mhsample` and use `/tmp/mhsample/sample.edl.json`.

## Picture
- [ ] Render a preview: `render EDL --preview`. Open the output. No white first frames, no purple/green cast, no frozen clips.
- [ ] `qa OUT.mp4 --edl EDL` passes or only warns; open the contact sheet and look at every time stamp a warning names.
- [ ] HDR (iPhone HLG) clips look natural, not grey or blown out.
- [ ] Rotated phone clips are upright and the right shape.
- [ ] The drop flash lands on the drop; cuts feel on the beat.

## Sound (you must listen)
- [ ] Music is present and not clipping; whooshes (if any) duck the music, they do not bury it.
- [ ] Reported integrated loudness is within a couple of LU of the target (`qa` prints it).

## Cuts (real footage, by eye)
- [ ] Run `cuts EDL --scores work --beats work/beats.json` and read every reason against the contact sheet. Is each stated reason true? Note the ones that are wrong; fix them with `shot-notes.json`.
- [ ] No cut looks like a jump cut (two near-identical shots); no slide or wipe runs against the movement in the picture.
- [ ] The cuts on action really do continue the movement; open the frames on both sides of three of them.
- [ ] Every transition has a reason you would defend; the video does not feel decorated.
- [ ] Longer dissolves (`transition_frames` 8-12) feel calm, not mushy; J/L-cuts (`audio.lead` / `lag`) are audible and not distracting (you must listen).
- [ ] `qa` shows no `internal-cut` for a shot you kept.

## Hardware paths
- [ ] `render EDL --encoder auto` selects the expected encoder (VideoToolbox on a Mac, NVENC on an NVIDIA PC) and the file plays everywhere.
- [ ] `render EDL --encoder libx264` still works (the universal fallback).
- [ ] A second render is almost all cache (`parts_cached` equals the shot count).

## Delivery
- [ ] `render EDL --delivery reels` produces a 9:16 file at the platform loudness; `--target-size-mb N` stays under N.
- [ ] `--manifest` records your FFmpeg version and the output hash; `--bundle` zips the EDL and sidecars (no media).

Report anything that failed with the OS, FFmpeg version and the `doctor` output.
