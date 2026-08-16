# Install / refresh Windows Scheduled Tasks for LeiBot auto-updates.
# Run from project root:  powershell -ExecutionPolicy Bypass -File .\install_scheduled_tasks.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root "venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $Python -or -not (Test-Path $Python)) {
    throw "Python not found. Create venv first: python -m venv venv && .\venv\Scripts\pip install -r requirements.txt"
}

$Script = Join-Path $Root "update_jobs.py"

function Ensure-Task {
    param(
        [string]$Name,
        [string]$Args,
        [string]$Schedule,
        [string]$Time,
        [string]$Days = ""
    )
    $tr = "`"$Python`" `"$Script`" $Args"
    $cmd = @(
        "/Create", "/F",
        "/TN", $Name,
        "/TR", $tr,
        "/SC", $Schedule,
        "/ST", $Time,
        "/RL", "LIMITED"
    )
    if ($Days) {
        $cmd += @("/D", $Days)
    }
    & schtasks.exe @cmd | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "schtasks failed for $Name (exit $LASTEXITCODE). Try running PowerShell as Administrator."
    }
}

Write-Host "Python: $Python"
Write-Host "Script: $Script"

Ensure-Task -Name "LeiBot-RefreshUniverse" -Args "--universe" -Schedule "WEEKLY" -Time "10:00" -Days "SUN"
Ensure-Task -Name "LeiBot-RefreshPrices" -Args "--prices" -Schedule "WEEKLY" -Time "13:15" -Days "MON,TUE,WED,THU,FRI"

Write-Host ""
Write-Host "Installed:"
schtasks /Query /TN "LeiBot-RefreshUniverse" /FO LIST | Select-String -Pattern "TaskName|Next Run|Status"
Write-Host "---"
schtasks /Query /TN "LeiBot-RefreshPrices" /FO LIST | Select-String -Pattern "TaskName|Next Run|Status"
Write-Host ""
Write-Host "Logs: $Root\data\logs\update_jobs.log"
Write-Host "Times use this PC local clock (prefer Pacific Time)."
