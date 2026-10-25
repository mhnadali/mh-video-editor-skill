# Installs the skill and agent into ~/.claude (run from the repo root).
$ErrorActionPreference = "Stop"
$src = $PSScriptRoot
# Use the standard Windows home (USERPROFILE); fall back to $HOME. PowerShell's $HOME is fixed at session start and does
# not follow a later $env:USERPROFILE change, so reading USERPROFILE is the reliable choice (and what CI overrides).
$userHome = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
$dest = Join-Path $userHome ".claude\skills\mh-video-editor-skill"
New-Item -ItemType Directory -Force (Join-Path $userHome ".claude\skills"), (Join-Path $userHome ".claude\agents") | Out-Null
$same = (Test-Path $dest) -and ((Resolve-Path $src).Path -eq (Resolve-Path $dest).Path)
if (-not $same) {
  if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
  New-Item -ItemType Directory -Force $dest | Out-Null
  # copy everything except .git (copying the git directory is slow and can hit Windows' long-path limit)
  Get-ChildItem -LiteralPath $src -Force | Where-Object { $_.Name -ne ".git" } | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $dest -Recurse -Force
  }
}
Copy-Item (Join-Path $dest "agents\mh-video-editor.md") (Join-Path $userHome ".claude\agents\") -Force
Write-Host "Installed to $dest. Checking FFmpeg ..."
# Run doctor but never let its exit code or a native-command error fail the install (install.sh uses `|| true`).
try { & python (Join-Path $dest "engine\scripts\mh_video.py") doctor } catch { Write-Host "doctor check skipped: $_" }
$global:LASTEXITCODE = 0
Write-Host "Start a new Claude Code session, then use /mh-video-editor-skill"
