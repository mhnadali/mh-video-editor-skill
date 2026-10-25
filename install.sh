#!/usr/bin/env bash
# Installs the skill and agent into ~/.claude (run from the repo root).
set -euo pipefail
src="$(cd "$(dirname "$0")" && pwd)"
dest="$HOME/.claude/skills/mh-video-editor-skill"
mkdir -p "$HOME/.claude/skills" "$HOME/.claude/agents"
if [ "$src" != "$dest" ]; then
  rm -rf "$dest"
  cp -R "$src" "$dest"
  rm -rf "$dest/.git"
fi
cp "$dest/agents/mh-video-editor.md" "$HOME/.claude/agents/"
echo "Installed to $dest. Checking FFmpeg ..."
py=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c "import sys" >/dev/null 2>&1; then py="$c"; break; fi
done
if [ -n "$py" ]; then "$py" "$dest/engine/scripts/mh_video.py" doctor || true; else echo "Python 3 not found: install it to use the engine."; fi
echo "Start a new Claude Code session, then use /mh-video-editor-skill"
