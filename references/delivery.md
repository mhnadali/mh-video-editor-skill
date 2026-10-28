# Delivery, size targeting and reproducibility

The render is the same EDL; delivery decides how it leaves the engine. Platforms re-encode everything you upload, so the
aim is a clean, correctly shaped, correctly loud file that survives the re-encode and respects the size limit — not to
out-smart the platform.

## Delivery presets

`render EDL --delivery <platform>` sets the output format, the integrated-loudness target and the container.

| Preset | Format | Loudness |
|---|---|---|
| `reels` | 9:16 | −14 LUFS |
| `tiktok` | 9:16 | −14 LUFS |
| `shorts` | 9:16 | −14 LUFS |
| `stories` | 9:16 | −14 LUFS |
| `youtube` | 16:9 | −14 LUFS |
| `square` | 1:1 | −14 LUFS |

The container is always `yuv420p` with `+faststart` (the player can start before the whole file arrives). If you have
authored a custom size, `--format` still overrides the preset's format.

## Size and bitrate

- `--target-size-mb N` aims for a file of about N megabytes. The engine computes a video bitrate from the duration and
  the audio bitrate and runs **two passes** on libx264 (VBR on a hardware encoder). A size target is an **upper bound**:
  busy footage fills it; flat or short footage undershoots, which is fine. A target that leaves less than 120 kbit/s for
  the picture is refused with a message.
- `--bitrate KBPS` sets the video bitrate directly (skip the size maths).
- Neither flag changes the default: without them the render uses constant quality, as before.

The render result carries a `delivery_check` (size within tolerance, aspect, container) so automation can gate on it.

## Multi-track music (music sections)

`project.music` may be a **list** of audio files instead of one. They are concatenated (equal-loudness) into one track
before the usual fades, ducking and two-pass master, so a montage can move through several user-supplied pieces. Only
user-supplied audio is ever used; nothing is downloaded.

## Reproducibility

- `--manifest` writes `<output>.manifest.json`: the engine and FFmpeg versions, a SHA-256 of the EDL and of the output,
  and the size and modification time of every input clip and music file. Another machine can compare its manifest to
  tell whether it reproduced the same render.
- `--bundle` writes `<edl>.bundle.zip`: the EDL plus its small sidecars (`video-brief.md`, `beats.json`, `scores.json`,
  `clips.txt`, the manifest) for hand-off. Media is never bundled.
- `--json FILE` also writes the render result (paths, sizes, verdict) for scripting.

## Cache housekeeping

Large libraries leave gigabytes of cached parts behind. `vid_deliver.gc_cache(work_dir, keep_days)` deletes cached
parts, LUTs and audio masters older than `keep_days` (default 14), so a current render's cache is never lost.
