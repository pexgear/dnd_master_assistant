<#
.SYNOPSIS
    Sets up Canon Keeper on Windows: checks Python, builds a virtualenv,
    installs the app and its dependencies.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install.ps1

.EXAMPLE
    # Include the developer tools (pytest, pytest-qt)
    powershell -ExecutionPolicy Bypass -File .\install.ps1 -Dev
#>
[CmdletBinding()]
param(
    [switch]$Dev,
    [switch]$Whisper,
    [string]$VenvPath = ".venv"
)

$ErrorActionPreference = "Stop"
$MinPython = [Version]"3.11"

Set-Location -Path $PSScriptRoot

function Write-Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }
function Write-Ok($message)   { Write-Host "    $message" -ForegroundColor Green }

# --- 1. Find a suitable Python -----------------------------------------------
Write-Step "Looking for Python $MinPython or newer"

$python = $null
foreach ($candidate in @("py -3", "python", "python3")) {
    $parts = $candidate.Split(" ")
    $exe = $parts[0]
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }

    try {
        $versionText = & $exe $parts[1..($parts.Length - 1)] -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    } catch { continue }

    if ($LASTEXITCODE -ne 0 -or -not $versionText) { continue }
    if ([Version]$versionText -ge $MinPython) {
        $python = $candidate
        Write-Ok "Found Python $versionText via '$candidate'"
        break
    }
}

if (-not $python) {
    Write-Host ""
    Write-Host "Python $MinPython or newer was not found." -ForegroundColor Red
    Write-Host "Install it, tick 'Add python.exe to PATH', then run this script again:"
    Write-Host "  https://www.python.org/downloads/windows/"
    Write-Host "  or:  winget install Python.Python.3.12"
    exit 1
}

$pythonParts = $python.Split(" ")
$pythonExe = $pythonParts[0]
$pythonArgs = @()
if ($pythonParts.Length -gt 1) { $pythonArgs = $pythonParts[1..($pythonParts.Length - 1)] }

# --- 2. Create the virtual environment ---------------------------------------
$venvPython = Join-Path $VenvPath "Scripts\python.exe"

if (Test-Path $venvPython) {
    Write-Step "Reusing the existing virtualenv at $VenvPath"
} else {
    Write-Step "Creating a virtualenv at $VenvPath"
    & $pythonExe @pythonArgs -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) { throw "Failed to create the virtualenv." }
}

# --- 3. Install ---------------------------------------------------------------
Write-Step "Upgrading pip"
& $venvPython -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { throw "Failed to upgrade pip." }

$extras = @()
if ($Dev)     { $extras += "dev" }
if ($Whisper) { $extras += "whisper" }

$target = "."
if ($extras.Count -gt 0) { $target = ".[" + ($extras -join ",") + "]" }

Write-Step "Installing Canon Keeper and its dependencies (this downloads Qt; give it a minute)"
& $venvPython -m pip install -e $target
if ($LASTEXITCODE -ne 0) { throw "Installation failed." }

# --- 4. Verify ----------------------------------------------------------------
Write-Step "Verifying the install"
$env:QT_QPA_PLATFORM = "offscreen"
& $venvPython -c "import canon_keeper, PySide6; print('Canon Keeper', canon_keeper.__version__, '/ PySide6', PySide6.__version__)"
Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue
if ($LASTEXITCODE -ne 0) { throw "The installed package could not be imported." }

Write-Host ""
Write-Ok "Done. Start Canon Keeper with:"
Write-Host "    .\$VenvPath\Scripts\canonkeeper.exe"
Write-Host "  or, from VS Code, press F5 and pick 'Canon Keeper'."
