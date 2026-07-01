# scripts/weekly_pipeline.ps1 — C1: weekly data-accumulation run.
#
# Registered in Windows Task Scheduler as "IDX Weekly Pipeline"
# (Saturday 09:00 WIB, StartWhenAvailable, AllowStartIfOnBatteries —
# without the battery flag Task Scheduler silently queues runs on a
# laptop that is not plugged in). One pipeline run does all the
# accumulation C1 needs: evaluate_memory() at pipeline start closes open
# trades from history (outcomes for agent calibration), then the run records
# fresh observations, verdicts, and fundamental snapshots.
#
# Logs go to output\logs\weekly\pipeline_YYYYMMDD_HHMMSS.log (last 12 kept).
#
# Manage the task:
#   Start-ScheduledTask   -TaskName "IDX Weekly Pipeline"   # run now
#   Unregister-ScheduledTask -TaskName "IDX Weekly Pipeline" # remove

$repo = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $repo "output\logs\weekly"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$log = Join-Path $logDir "pipeline_$stamp.log"

# Scheduled tasks get a minimal PATH; resolve uv.exe defensively.
$uv = $null
$cmd = Get-Command uv -ErrorAction SilentlyContinue
if ($cmd) { $uv = $cmd.Source }
if (-not $uv) {
    foreach ($candidate in @(
        "$env:USERPROFILE\.local\bin\uv.exe",
        "$env:USERPROFILE\.cargo\bin\uv.exe"
    )) {
        if (Test-Path $candidate) { $uv = $candidate; break }
    }
}
if (-not $uv) {
    Set-Content -Path $log -Value "uv.exe not found on PATH or known install locations" -Encoding utf8
    exit 1
}

Set-Location $repo
# cmd-level redirection keeps native stderr as plain bytes (no PowerShell
# ErrorRecord wrapping) and one consistent encoding in the log file.
cmd /c "`"$uv`" run idx pipeline --no-interactive > `"$log`" 2>&1"
$code = $LASTEXITCODE

# Retention: keep the 12 most recent logs.
Get-ChildItem $logDir -Filter "pipeline_*.log" |
    Sort-Object Name -Descending |
    Select-Object -Skip 12 |
    Remove-Item -Force -ErrorAction SilentlyContinue

exit $code
