param(
    [switch]$FullRuff,
    [switch]$SkipTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string[]]$Command
    )

    Write-Host ""
    Write-Host "==> $Name"
    & $Command[0] $Command[1..($Command.Length - 1)]
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

$env:UV_CACHE_DIR = ".uv-cache"

$ruffTargets = if ($FullRuff) {
    @(".")
} else {
    @("orchestrator.py", "run_debate.py", "run_quant_filter.py", "core", "services", "tests")
}

Invoke-Step "Ruff" (@("uv", "run", "ruff", "check") + $ruffTargets)

if (-not $SkipTests) {
    Invoke-Step "Pytest" @("uv", "run", "pytest", "tests/", "--tb=short", "-q")
}

Invoke-Step "Syntax check" @("uv", "run", "python", "-m", "py_compile", "orchestrator.py", "run_debate.py", "run_quant_filter.py")

Write-Host ""
Write-Host "All verification checks passed."
