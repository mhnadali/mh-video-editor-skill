# Installs the skill and agent into ~/.claude (run from the repo root).
$ErrorActionPreference = "Stop"
$src = $PSScriptRoot
$dest = Join-Path $HOME ".claude\skills\mh-video-editor-skill"
New-Item -ItemType Directory -Force (Join-Path $HOME ".claude\skills"), (Join-Path $HOME ".claude\agents") | Out-Null
$same = (Test-Path $dest) -and ((Resolve-Path $src).Path -eq (Resolve-Path $dest).Path)
if (-not $same) {
  if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
  Copy-Item $src $dest -Recurse -Force
  Remove-Item (Join-Path $dest ".git") -Recurse -Force -ErrorAction SilentlyContinue
}
Copy-Item (Join-Path $dest "agents\mh-video-editor.md") (Join-Path $HOME ".claude\agents\") -Force
Write-Host "Installed to $dest. Checking FFmpeg ..."
python (Join-Path $dest "engine\scripts\mh_video.py") doctor
Write-Host "Start a new Claude Code session, then use /mh-video-editor-skill"
