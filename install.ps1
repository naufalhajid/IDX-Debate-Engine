param(
    [string]$InstallDir = "$HOME\idx-debate-engine",
    [string]$Repository = "https://github.com/naufalhajid/IDX-Debate-Engine.git"
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host "[idx] $Message" -ForegroundColor Cyan
}

function Require-Command {
    param(
        [string]$Name,
        [string]$InstallHint
    )

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Write-Host "[idx] Missing dependency: $Name" -ForegroundColor Red
        Write-Host $InstallHint
        exit 1
    }
}

Require-Command "git" "Install Git for Windows from https://git-scm.com/download/win, then rerun this command."
Require-Command "uv" "Install uv first, then rerun this command. See https://docs.astral.sh/uv/getting-started/installation/"

if (Test-Path $InstallDir) {
    Write-Step "Updating existing checkout at $InstallDir"
    Push-Location $InstallDir
    git pull --ff-only
} else {
    Write-Step "Cloning IDX Debate Engine into $InstallDir"
    git clone $Repository $InstallDir
    Push-Location $InstallDir
}

try {
    Write-Step "Installing Python dependencies with uv"
    uv sync

    Write-Host ""
    Write-Host "IDX Debate Engine is ready." -ForegroundColor Green
    Write-Host "Next commands:"
    Write-Host "  cd `"$InstallDir`""
    Write-Host "  uv run idx pipeline --dry-run --no-interactive --output-dir tmp/dry_run"
    Write-Host "  uv run idx pipeline"
} finally {
    Pop-Location
}
