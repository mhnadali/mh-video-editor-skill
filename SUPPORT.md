# Support and compatibility

## Getting help
- A bug or wrong result: open a [bug report](.github/ISSUE_TEMPLATE/bug_report.md). Include the `doctor` output and, if relevant, the EDL and the QA report.
- A question or idea: open a [feature request](.github/ISSUE_TEMPLATE/feature_request.md) or a blank issue.
- A security problem: do **not** open a public issue; see [`SECURITY.md`](SECURITY.md).

Always run `python engine/scripts/mh_video.py doctor` first. It prints the FFmpeg version, which optional features your build supports, and which hardware H.264 encoders work on your machine.

## Support matrix

| Axis | Supported | Notes |
|---|---|---|
| Python | 3.9 – 3.13 | Standard library only; 3.9 is exercised in CI, development is on 3.12+ |
| FFmpeg | 6.x, 7.x, 9.x | Developed on 9.0.2; CI installs the distro build (6.x on Ubuntu). `doctor` reports missing optional filters |
| OS | Linux, macOS, Windows | CI runs the full render suite on Linux and Windows; macOS is validated with the manual QA protocol (`docs/QA-PROTOCOL.md`) |
| Encoders | libx264 (always), NVENC, VideoToolbox, QSV | `--encoder auto` picks a working hardware encoder, else libx264. VAAPI is detected and reported; it is not auto-selected |
| Decoders | software (always), CUDA (`--hwdecode auto`) | automatic software fallback if GPU decode fails |

Optional features need FFmpeg libraries a minimal build may lack: libass (text), libvidstab (stabilisation), libzimg (HDR). `doctor` lists them; `lint` reports `ffmpeg-missing` when an EDL uses a feature the build cannot render.

## Not supported
AI video generation, heavy compositing, downloading media/music/fonts/models. The engine only runs FFmpeg on files you point it at.
