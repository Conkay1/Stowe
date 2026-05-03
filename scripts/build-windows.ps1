# Build Stowe Windows installer.
#
# Prerequisites (one-time):
#   1. Python 3.10+ on PATH  (python.org/downloads)
#   2. Inno Setup 6+         (jrsoftware.org/isdl.php)
#      Default install path: C:\Program Files (x86)\Inno Setup 6\
#
# Usage (from repo root in PowerShell):
#   .\scripts\build-windows.ps1
#
# Output: Stowe-<version>-windows-setup.exe in the repo root.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ROOT = Split-Path -Parent $PSScriptRoot
Set-Location $ROOT

# ── Version from stowe.iss ────────────────────────────────────────────────────
$version = (Select-String -Path "stowe.iss" -Pattern '#define AppVersion\s+"([^"]+)"').Matches[0].Groups[1].Value
Write-Host "==> Stowe $version — Windows build"

# ── Inno Setup location ───────────────────────────────────────────────────────
$iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) {
    $iscc = "C:\Program Files\Inno Setup 6\ISCC.exe"
}
if (-not (Test-Path $iscc)) {
    Write-Error "Inno Setup not found. Install from https://jrsoftware.org/isdl.php"
    exit 1
}

# ── ICO check ────────────────────────────────────────────────────────────────
if (-not (Test-Path "assets\stowe.ico")) {
    Write-Error "assets\stowe.ico is missing. Run the ICO generator or check the assets folder."
    exit 1
}

# ── Python virtual environment ────────────────────────────────────────────────
Write-Host "==> Setting up Python environment"

# Prefer Python 3.12 or 3.11 -- pre-built wheels exist for pydantic-core and
# pythonnet on these versions. Python 3.13+ requires compiling from source
# (needs MSVC Build Tools + Rust), which is not required here.
$python = $null
$ErrorActionPreference = "SilentlyContinue"
foreach ($ver in @("3.12", "3.11", "3.10")) {
    $candidate = & py "-$ver" -c "import sys; print(sys.executable)"
    if ($LASTEXITCODE -eq 0 -and $candidate) {
        $python = $candidate.Trim()
        Write-Host "    Using Python $ver at $python"
        break
    }
}
$ErrorActionPreference = "Stop"
if (-not $python) {
    Write-Error "Python 3.10-3.12 not found. Install Python 3.12 from https://python.org/downloads and re-run."
    exit 1
}

if (-not (Test-Path ".venv")) {
    & $python -m venv .venv
}
& .venv\Scripts\python.exe -m pip install -q --upgrade pip
& .venv\Scripts\pip.exe install -q -r requirements.txt
& .venv\Scripts\pip.exe install -q pyinstaller

# ── Clean previous build ──────────────────────────────────────────────────────
Write-Host "==> Cleaning previous build artifacts"
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist")  { Remove-Item -Recurse -Force "dist" }
$old = "Stowe-$version-windows-setup.exe"
if (Test-Path $old) { Remove-Item -Force $old }

# ── PyInstaller ───────────────────────────────────────────────────────────────
Write-Host "==> Running PyInstaller"
& .venv\Scripts\python.exe -m PyInstaller stowe-windows.spec --noconfirm

if (-not (Test-Path "dist\Stowe\Stowe.exe")) {
    Write-Error "PyInstaller did not produce dist\Stowe\Stowe.exe"
    exit 1
}

# ── Inno Setup ────────────────────────────────────────────────────────────────
Write-Host "==> Running Inno Setup"
& $iscc "stowe.iss"

$installer = "Stowe-$version-windows-setup.exe"
if (-not (Test-Path $installer)) {
    Write-Error "Inno Setup did not produce $installer"
    exit 1
}

Write-Host ""
Write-Host "Done: $installer  ($([math]::Round((Get-Item $installer).Length / 1MB, 1)) MB)"
