# Installs the skill and agent into ~/.claude (run from the repo root).
$src = $PSScriptRoot
$userHome = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
$skillsDir = Join-Path $userHome ".claude/skills"
$agentsDir = Join-Path $userHome ".claude/agents"
$dest = Join-Path $skillsDir "mh-video-editor-skill"
New-Item -ItemType Directory -Force -Path $skillsDir | Out-Null
New-Item -ItemType Directory -Force -Path $agentsDir | Out-Null
New-Item -ItemType Directory -Force -Path $dest | Out-Null
# copy the repo contents (except .git) into $dest; -Force overwrites an earlier install
Get-ChildItem -LiteralPath $src -Force | Where-Object { $_.Name -ne ".git" } | ForEach-Object {
  Copy-Item -LiteralPath $_.FullName -Destination $dest -Recurse -Force -ErrorAction SilentlyContinue
}
Copy-Item -LiteralPath (Join-Path $dest "agents/mh-video-editor.md") -Destination $agentsDir -Force -ErrorAction SilentlyContinue
Write-Host "Installed to $dest. Checking FFmpeg ..."
# Run doctor but never let its exit code or a native-command error fail the install (install.sh uses `|| true`).
try { & python (Join-Path $dest "engine/scripts/mh_video.py") doctor } catch { Write-Host "doctor check skipped: $_" }
$global:LASTEXITCODE = 0
Write-Host "Start a new Claude Code session, then use /mh-video-editor-skill"
