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
#
# Optional Authenticode signing (see docs/windows-signing.md).
# WINDOWS_CERT_PFX_BASE64 absent or blank → unsigned build, exit 0.
# A password without that certificate is ignored. A certificate without
# a password is an error (signing was requested but cannot run).
#   WINDOWS_CERT_PFX_BASE64   Base64-encoded code-signing .pfx
#   WINDOWS_CERT_PASSWORD     Password for that .pfx
#   WINDOWS_TIMESTAMP_URL     RFC3161 timestamp URL
#                             (default: http://timestamp.digicert.com)
#
# When signing is configured, dist\Stowe\Stowe.exe is signed before Inno
# Setup runs (so the installed binary is signed) and the setup exe is
# signed afterward. The temporary .pfx is deleted on exit.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Path of the decoded .pfx, if signing is enabled. The finally block deletes it.
$script:StowePfxPath = $null

function Find-SignTool {
    $roots = @()
    if (-not [string]::IsNullOrWhiteSpace(${env:ProgramFiles(x86)})) {
        $roots += (Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin")
    }
    if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
        $roots += (Join-Path $env:ProgramFiles "Windows Kits\10\bin")
    }
    $found = @()
    foreach ($root in $roots) {
        if ([string]::IsNullOrWhiteSpace($root)) { continue }
        if (-not (Test-Path -LiteralPath $root)) { continue }
        $found += @(Get-ChildItem -LiteralPath $root -Recurse -Filter "signtool.exe" -ErrorAction SilentlyContinue)
    }
    $x64 = @($found | Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } | Sort-Object { $_.Directory.Name } -Descending)
    if ($x64.Count -gt 0) {
        return $x64[0].FullName
    }
    $any = @($found | Sort-Object { $_.Directory.Name } -Descending)
    if ($any.Count -gt 0) {
        return $any[0].FullName
    }
    $cmd = Get-Command "signtool.exe" -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    return $null
}

function Add-StoweJobSummary {
    param([Parameter(Mandatory = $true)][string]$Markdown)
    if ([string]::IsNullOrWhiteSpace($env:GITHUB_STEP_SUMMARY)) {
        return
    }
    if (-not $Markdown.EndsWith("`n")) {
        $Markdown += "`n"
    }
    [System.IO.File]::AppendAllText($env:GITHUB_STEP_SUMMARY, $Markdown)
}

function Import-StoweSigningPfx {
    if ($script:StowePfxPath) {
        return
    }
    $b64 = ($env:WINDOWS_CERT_PFX_BASE64 -replace '\s', '')
    try {
        $bytes = [Convert]::FromBase64String($b64)
    } catch {
        Write-Error "WINDOWS_CERT_PFX_BASE64 is not valid base64."
        exit 1
    }
    $script:StowePfxPath = Join-Path ([System.IO.Path]::GetTempPath()) ("stowe-" + [guid]::NewGuid().ToString("n") + ".pfx")
    [System.IO.File]::WriteAllBytes($script:StowePfxPath, $bytes)
}

function Invoke-StoweAuthenticodeSign {
    param(
        [Parameter(Mandatory = $true)][string]$SignTool,
        [Parameter(Mandatory = $true)][string]$Target
    )
    $timestampUrl = "http://timestamp.digicert.com"
    if (-not [string]::IsNullOrWhiteSpace($env:WINDOWS_TIMESTAMP_URL)) {
        $timestampUrl = $env:WINDOWS_TIMESTAMP_URL.Trim()
    }
    Import-StoweSigningPfx
    Write-Host "==> Signing $Target"
    & $SignTool sign /fd SHA256 /tr $timestampUrl /td SHA256 /f $script:StowePfxPath /p $env:WINDOWS_CERT_PASSWORD $Target
    if ($LASTEXITCODE -ne 0) {
        Write-Error "signtool failed for $Target (exit $LASTEXITCODE)"
        exit 1
    }
}

try {

$ROOT = Split-Path -Parent $PSScriptRoot
Set-Location $ROOT

# WINDOWS_CERT_PFX_BASE64 is the switch. Absent or blank means unsigned,
# even when WINDOWS_CERT_PASSWORD is set (GitHub leaves unused secrets
# empty, and a leftover password must not fail the unsigned build).
# A PFX without a password still fails: signing was requested.
$hasPfx = -not [string]::IsNullOrWhiteSpace($env:WINDOWS_CERT_PFX_BASE64)
$hasPfxPassword = -not [string]::IsNullOrWhiteSpace($env:WINDOWS_CERT_PASSWORD)
$signing = $false
$signTool = $null
if (-not $hasPfx) {
    Write-Host "==> No Windows certificate configured — build will be unsigned"
    $summary = "### Windows installer: unsigned`n`nAuthenticode signing was skipped because ``WINDOWS_CERT_PFX_BASE64`` is not set. The setup executable uploaded by this job is unsigned."
    if ($hasPfxPassword) {
        Write-Host "WARNING: WINDOWS_CERT_PASSWORD is set, but WINDOWS_CERT_PFX_BASE64 is absent. Signing is skipped."
        $summary += "`n`n``WINDOWS_CERT_PASSWORD`` is set and was ignored."
    }
    Add-StoweJobSummary -Markdown $summary
} elseif (-not $hasPfxPassword) {
    Add-StoweJobSummary -Markdown "### Windows installer: signing failed`n`n``WINDOWS_CERT_PFX_BASE64`` is set but ``WINDOWS_CERT_PASSWORD`` is empty. Set both to sign, or leave the certificate unset for an unsigned build."
    Write-Error "WINDOWS_CERT_PFX_BASE64 is set but WINDOWS_CERT_PASSWORD is empty. Set both to sign, or leave the certificate unset for an unsigned build."
    exit 1
} else {
    $signTool = Find-SignTool
    if (-not $signTool) {
        Write-Error "signtool.exe not found. Install the Windows SDK (Windows Kits\10\bin\<version>\x64\signtool.exe)."
        exit 1
    }
    $signing = $true
    Write-Host "==> Code signing enabled (Authenticode)"
}

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

# Sign the payload before Inno Setup copies it into the installer.
if ($signing) {
    Invoke-StoweAuthenticodeSign -SignTool $signTool -Target "dist\Stowe\Stowe.exe"
}

# ── Inno Setup ────────────────────────────────────────────────────────────────
Write-Host "==> Running Inno Setup"
& $iscc "stowe.iss"

$installer = "Stowe-$version-windows-setup.exe"
if (-not (Test-Path $installer)) {
    Write-Error "Inno Setup did not produce $installer"
    exit 1
}

if ($signing) {
    Invoke-StoweAuthenticodeSign -SignTool $signTool -Target $installer
}

$item = Get-Item $installer
$hash = (Get-FileHash -Algorithm SHA256 -Path $installer).Hash.ToLowerInvariant()
Write-Host ""
Write-Host "Done: $installer  ($([math]::Round($item.Length / 1MB, 1)) MB)"
Write-Host "Bytes: $($item.Length)"
Write-Host "SHA256: $hash"
if (-not $signing) {
    Add-StoweJobSummary -Markdown ("`n- File: ``$installer```n- Bytes: $($item.Length)`n- SHA-256: ``$hash```n")
}
} finally {
    if ($script:StowePfxPath -and (Test-Path -LiteralPath $script:StowePfxPath)) {
        Remove-Item -LiteralPath $script:StowePfxPath -Force
        $script:StowePfxPath = $null
    }
}
