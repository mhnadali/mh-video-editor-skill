# Security Policy

## Supported versions
The latest release on `main`.

## Reporting a vulnerability
Please do not open a public issue. Use GitHub's private vulnerability reporting (Security tab, "Report a vulnerability") or contact the maintainer through their GitHub profile.

## Scope notes
- The engine only runs FFmpeg/FFprobe on files you point it at and writes into the work and output folders you choose. It never downloads media, music or software.
- The skill tells agents never to install software, publish, push or send anything, and to treat file names, EDL text and tool output as data, not instructions.
- Clip paths from an EDL are passed to FFmpeg as arguments (no shell), so file names with spaces or quotes are safe.
