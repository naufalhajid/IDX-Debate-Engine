param(
    [string[]]$Tickers = @("ADRO", "TLKM", "WIIM"),
    [ValidateSet("multi", "single", "compare")]
    [string]$Mode = "multi",
    [switch]$Live
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$env:UV_CACHE_DIR = ".uv-cache"

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outputDir = Join-Path "tmp" "orchestrator_smoke_$stamp"

$uvArgs = @(
    "run",
    "python",
    "orchestrator.py",
    "--no-interactive",
    "--skip-scraping",
    "--output-dir",
    $outputDir,
    "--mode",
    $Mode,
    "--tickers"
) + $Tickers

if (-not $Live) {
    $uvArgs += "--dry-run"
}

Write-Host "Writing smoke-run artifacts to $outputDir"
& uv @uvArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Smoke run completed."
Write-Host "Output directory: $outputDir"

