# Removes LocalVoice and its shortcuts. Recordings in Documents\LocalVoice Aufnahmen stay.
param([string]$InstallDir = "$env:LOCALAPPDATA\LocalVoice", [switch]$Force)
$ErrorActionPreference = 'Stop'
if (-not $Force) {
    $answer = Read-Host "LocalVoice aus $InstallDir entfernen? Aufnahmen bleiben erhalten. (j/n)"
    if ($answer -notmatch '^[jJyY]') { Write-Host "Abgebrochen."; exit 0 }
}
foreach ($folder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
    Remove-Item (Join-Path $folder 'LocalVoice.lnk') -ErrorAction SilentlyContinue
}
if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir }
Write-Host "LocalVoice wurde entfernt. Deine Aufnahmen liegen weiterhin unter Dokumente\LocalVoice Aufnahmen."
