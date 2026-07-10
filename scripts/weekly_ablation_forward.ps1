# scripts/weekly_ablation_forward.ps1 — P1.2 Fase B: weekly forward ablation run.
#
# Registered in Windows Task Scheduler as "IDX Ablation Forward"
# (Saturday 10:00 WIB — one hour after "IDX Weekly Pipeline" so the two
# LLM-heavy runs never overlap; StartWhenAvailable, AllowStartIfOnBatteries).
# One run executes the compare pipeline on the fixed 25-ticker universe and
# fans the verdicts out to the three sandboxed arm ledgers under
# output\ablation_forward\ (quant_only / single_gated / full_debate).
# Scoring happens separately via scripts\ablation_forward_eval.py once the
# 20-day horizon closes — this task only accumulates entries.
#
# Logs go to output\logs\weekly\ablation_YYYYMMDD_HHMMSS.log (last 12 kept).
#
# Manage the task:
#   Start-ScheduledTask   -TaskName "IDX Ablation Forward"   # run now
#   Unregister-ScheduledTask -TaskName "IDX Ablation Forward" # remove

$repo = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $repo "output\logs\weekly"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$log = Join-Path $logDir "ablation_$stamp.log"

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
# Stockbit post text contains emoji; redirected stdout defaults to cp1252
# on Windows, which would crash mid-run without this.
$env:PYTHONIOENCODING = "utf-8"
# cmd-level redirection keeps native stderr as plain bytes (no PowerShell
# ErrorRecord wrapping) and one consistent encoding in the log file.
cmd /c "`"$uv`" run python scripts\ablation_forward_run.py --live > `"$log`" 2>&1"
$code = $LASTEXITCODE

# Retention: keep the 12 most recent logs.
Get-ChildItem $logDir -Filter "ablation_*.log" |
    Sort-Object Name -Descending |
    Select-Object -Skip 12 |
    Remove-Item -Force -ErrorAction SilentlyContinue

exit $code
