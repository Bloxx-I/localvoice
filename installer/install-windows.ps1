# LocalVoice for Windows: installs Python, all packages and the speech model,
# then creates "LocalVoice" shortcuts on the desktop and in the start menu.
# Recordings go to Documents\LocalVoice Aufnahmen and are never touched by updates.
param(
    [string]$InstallDir = "$env:LOCALAPPDATA\LocalVoice",
    [switch]$SkipModel,      # e.g. for tests; the app can download the model later
    [switch]$NoShortcuts,
    [switch]$Cpu,            # force the CPU build of PyTorch even with an NVIDIA GPU
    [string]$Model = ""      # e.g. Qwen/Qwen3-ASR-1.7B; default depends on the GPU
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$UvVersion = '0.12.17'
$Source = Split-Path -Parent $PSScriptRoot

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what fehlgeschlagen (Code $LASTEXITCODE)." } }

Write-Host "LocalVoice wird installiert nach: $InstallDir"

Step "Programmdateien kopieren"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
if ((Resolve-Path $Source).Path -ne (Resolve-Path $InstallDir).Path) {
    robocopy $Source $InstallDir /E /NFL /NDL /NJH /NJS /NP `
        /XD .git .github .venv Aufnahmen Pruefaufnahmen recordings Logs tools python `
            (Join-Path $Source 'models\qwen3-asr') `
        /XF preferences.json download-status.json | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Kopieren fehlgeschlagen (robocopy $LASTEXITCODE)." }
    $global:LASTEXITCODE = 0
}

Step "Paketmanager uv $UvVersion bereitstellen"
$Tools = Join-Path $InstallDir 'tools'
$Uv = Join-Path $Tools 'uv.exe'
if (-not (Test-Path $Uv)) {
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'aarch64' } else { 'x86_64' }
    $zip = Join-Path $env:TEMP "uv-$UvVersion.zip"
    Invoke-WebRequest "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-$arch-pc-windows-msvc.zip" -OutFile $zip
    New-Item -ItemType Directory -Force -Path $Tools | Out-Null
    Expand-Archive $zip -DestinationPath $Tools -Force
    Remove-Item $zip
}
$env:UV_PYTHON_INSTALL_DIR = Join-Path $InstallDir 'python'
$env:UV_CACHE_DIR = Join-Path $env:TEMP 'localvoice-uv-cache'
$env:UV_LINK_MODE = 'copy'

Step "Python 3.12 und Umgebung einrichten"
$Venv = Join-Path $InstallDir '.venv'
$Py = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path $Py)) {
    & $Uv venv --python 3.12 $Venv; Check "Python-Umgebung"
}

$gpu = $false
if (-not $Cpu -and (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    & nvidia-smi -L *> $null
    $gpu = ($LASTEXITCODE -eq 0)
    $global:LASTEXITCODE = 0
}
if ($gpu) {
    Step "NVIDIA-Grafikkarte gefunden: PyTorch mit CUDA installieren (gross, einige Minuten)"
    & $Uv pip install --python $Py torch --index-url https://download.pytorch.org/whl/cu128; Check "PyTorch (CUDA)"
} else {
    Write-Host "Keine NVIDIA-Grafikkarte: Spracherkennung laeuft auf dem Prozessor (kleineres Modell)."
}

Step "Pakete installieren"
& $Uv pip install --python $Py -r (Join-Path $InstallDir 'requirements-windows.txt'); Check "Pakete"
& $Py -c "import PySide6, sounddevice, soundfile, onnxruntime, qwen_asr, torch; print('Pakete ok - torch', torch.__version__, '- CUDA', torch.cuda.is_available())"; Check "Paketpruefung"

if (-not $SkipModel) {
    Step "Sprachmodell laden (einmalig, 2 bis 4 GB)"
    Push-Location $InstallDir
    try {
        if ($Model) { & $Py src\download_model.py --console --repo $Model } else { & $Py src\download_model.py --console }
        Check "Modell-Download"
    } finally { Pop-Location }
}

if (-not $NoShortcuts) {
    Step "Verknuepfungen anlegen"
    $shell = New-Object -ComObject WScript.Shell
    foreach ($folder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
        $link = $shell.CreateShortcut((Join-Path $folder 'LocalVoice.lnk'))
        $link.TargetPath = Join-Path $Venv 'Scripts\pythonw.exe'
        $link.Arguments = '"' + (Join-Path $InstallDir 'src\combined.py') + '"'
        $link.WorkingDirectory = $InstallDir
        $link.IconLocation = Join-Path $InstallDir 'installer\LocalVoice.ico'
        $link.Description = 'LocalVoice - Vorlesungen aufnehmen, nachhoeren und lokal transkribieren'
        $link.Save()
    }
}

Remove-Item -Recurse -Force $env:UV_CACHE_DIR -ErrorAction SilentlyContinue
Step "Fertig"
Write-Host "Starten ueber 'LocalVoice' auf dem Desktop oder im Startmenue."
Write-Host "Aufnahmen: $([Environment]::GetFolderPath('MyDocuments'))\LocalVoice Aufnahmen"
