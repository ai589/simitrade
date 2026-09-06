<#
.SYNOPSIS
  (Re)register every Windows scheduled task this project needs, pointing at wherever
  the project currently lives.

.DESCRIPTION
  The tasks used to hardcode "D:\AI stuffs\ECL - trade - main". When the folder moved to
  "D:\AI stuffs - personal\ECL - trade - main" on 2026-09-02 all three tasks started
  failing silently (ERROR_DIRECTORY / exit 1) and the dashboard quietly went stale for
  two days. This script fixes that class of bug permanently: it derives every path from
  its own location, so after any future move you just double-click it again.

  Idempotent - safe to run as many times as you like. Existing tasks of the same name
  are replaced, not duplicated.

  Tasks registered:
    NYSE Screener Refresh        08:00 SGT daily          -> refresh_silent.bat
    US Market Open-Close Refresh 21:40/22:40/04:15/05:15   -> refresh_market.bat
                                 (src\market_guard.py drops the two that fall outside
                                  the NY open/close window, so exactly two fire per day
                                  whichever side of US daylight saving we are on)
    ECL Morning Brief            08:05 SGT weekdays       -> morning_brief.bat

  Also disables the dead "ECL Trading Daily" task, which points at
  D:\Dropbox\AI stuffs\ECL - tiger\trading-system\DAILY_RUN.bat - a path that no longer
  exists, for a project dormant since mid-July.

.PARAMETER WakeToRun
  Let the 04:15 / 05:15 SGT post-US-close refresh wake the machine from sleep. Off by
  default because it changes power behaviour. Without it, a sleeping PC simply misses
  that run and catches up at 08:00 (StartWhenAvailable is set on every task).

.PARAMETER WhatIf
  Show what would change without touching the scheduler.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File "tools\install_tasks.ps1"
  powershell -ExecutionPolicy Bypass -File "tools\install_tasks.ps1" -WakeToRun
#>

[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$WakeToRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# --- Resolve the project root from this script's own location -----------------------
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Root) { throw "Could not resolve the project root from '$PSScriptRoot'." }

Write-Host "Project root: $Root" -ForegroundColor Cyan

# --- Sanity: refuse to register tasks pointing at a broken checkout ------------------
$required = @(
    'src\screener.py',
    'src\market_guard.py',
    'refresh_silent.bat',
    'refresh_market.bat',
    'publish_silent.bat'
)
$missing = $required | Where-Object { -not (Test-Path (Join-Path $Root $_)) }
if ($missing) {
    throw ("Refusing to register tasks - these files are missing under '$Root':`n  " +
           ($missing -join "`n  ") +
           "`nRun this script from inside the real project folder.")
}

# morning_brief.bat is created later in the build-out; warn rather than fail so the two
# refresh tasks can be fixed today.
$briefBat = Join-Path $Root 'morning_brief.bat'
$haveBrief = Test-Path $briefBat
if (-not $haveBrief) {
    Write-Warning "morning_brief.bat not found - skipping the 'ECL Morning Brief' task. Re-run this script once it exists."
}

# --- Shared task settings -----------------------------------------------------------
# StartWhenAvailable: a run missed because the PC was off/asleep fires as soon as it can,
# instead of being silently dropped until tomorrow.
$settingsArgs = @{
    StartWhenAvailable         = $true
    AllowStartIfOnBatteries    = $true
    DontStopIfGoingOnBatteries = $true
    ExecutionTimeLimit         = (New-TimeSpan -Hours 1)
    MultipleInstances          = 'IgnoreNew'
}
if ($WakeToRun) { $settingsArgs['WakeToRun'] = $true }
$settings = New-ScheduledTaskSettingsSet @settingsArgs

# Run as the current interactive user. Interactive logon type needs no stored password,
# but the machine does have to be logged on at the trigger time.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
                                        -LogonType Interactive -RunLevel Limited

function Register-ProjectTask {
    param(
        [Parameter(Mandatory)][string]   $Name,
        [Parameter(Mandatory)][string]   $BatFile,   # relative to $Root
        [Parameter(Mandatory)][object[]] $Triggers,
        [Parameter(Mandatory)][string]   $Description
    )

    $full = Join-Path $Root $BatFile
    if (-not (Test-Path $full)) { throw "Missing batch file for task '$Name': $full" }

    # cmd.exe /c so the .bat runs headless; WorkingDirectory pinned so a task with no
    # working dir can never resolve relative paths against System32.
    $action = New-ScheduledTaskAction -Execute 'cmd.exe' `
                                      -Argument "/c `"$full`"" `
                                      -WorkingDirectory $Root

    $existing = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    $verb = if ($existing) { 'Updating' } else { 'Creating' }

    if ($PSCmdlet.ShouldProcess($Name, $verb)) {
        try {
            Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Triggers `
                                   -Settings $settings -Principal $principal `
                                   -Description $Description -Force | Out-Null
        } catch {
            throw ("Failed to register '$Name': $($_.Exception.Message)`n" +
                   "If this says access is denied, the existing task was created elevated - " +
                   "re-run this script from an Administrator PowerShell.")
        }
        Write-Host ("  {0,-9} {1}" -f $verb, $Name) -ForegroundColor Green
        Write-Host ("            -> {0}" -f $full) -ForegroundColor DarkGray
    }
}

# Daily-at triggers take a DateTime; only the time-of-day matters.
function New-DailyAt {
    param([Parameter(Mandatory)][string]$Time)   # "HH:mm"
    New-ScheduledTaskTrigger -Daily -At ([datetime]::ParseExact($Time, 'HH:mm', $null))
}
function New-WeekdayAt {
    param([Parameter(Mandatory)][string]$Time)
    New-ScheduledTaskTrigger -Weekly -At ([datetime]::ParseExact($Time, 'HH:mm', $null)) `
        -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday
}

Write-Host "`nRegistering tasks..." -ForegroundColor Cyan

Register-ProjectTask -Name 'NYSE Screener Refresh' -BatFile 'refresh_silent.bat' `
    -Triggers @(New-DailyAt '08:00') `
    -Description 'Daily 08:00 SGT screener refresh + publish to GitHub/Vercel. Registered by tools\install_tasks.ps1.'

# Four triggers; market_guard.py exits 1 on the two that are outside the NY open/close
# window, so exactly one open-run and one close-run survive each weekday, DST included.
Register-ProjectTask -Name 'US Market Open-Close Refresh' -BatFile 'refresh_market.bat' `
    -Triggers @(
        (New-DailyAt '21:40'),   # US open, EDT
        (New-DailyAt '22:40'),   # US open, EST
        (New-DailyAt '04:15'),   # US close, EDT
        (New-DailyAt '05:15')    # US close, EST
    ) `
    -Description 'Refresh ~10-15 min after the US open and close (market_guard.py gates for DST). Registered by tools\install_tasks.ps1.'

if ($haveBrief) {
    Register-ProjectTask -Name 'ECL Morning Brief' -BatFile 'morning_brief.bat' `
        -Triggers @(New-WeekdayAt '08:05') `
        -Description 'Weekday 08:05 SGT trading brief from the overnight US session. Registered by tools\install_tasks.ps1.'
}

# --- Retire the dead task -----------------------------------------------------------
$dead = Get-ScheduledTask -TaskName 'ECL Trading Daily' -ErrorAction SilentlyContinue
if ($dead) {
    $target = ($dead.Actions | ForEach-Object { $_.Execute }) -join '; '
    if ($dead.State -eq 'Disabled') {
        Write-Host "`n  Already disabled  ECL Trading Daily" -ForegroundColor DarkGray
    } elseif ($PSCmdlet.ShouldProcess('ECL Trading Daily', 'Disable')) {
        try {
            Disable-ScheduledTask -TaskName 'ECL Trading Daily' | Out-Null
            Write-Host "`n  Disabled  ECL Trading Daily" -ForegroundColor Yellow
            Write-Host "            it pointed at $target (gone)" -ForegroundColor DarkGray
        } catch {
            Write-Warning "Could not disable 'ECL Trading Daily': $($_.Exception.Message)"
        }
    }
}

# --- Report -------------------------------------------------------------------------
Write-Host "`nCurrent state:" -ForegroundColor Cyan
'NYSE Screener Refresh', 'US Market Open-Close Refresh', 'ECL Morning Brief', 'ECL Trading Daily' |
    ForEach-Object {
        $t = Get-ScheduledTask -TaskName $_ -ErrorAction SilentlyContinue
        if (-not $t) { return }
        $i = $t | Get-ScheduledTaskInfo
        $last = if ($i.LastTaskResult -eq 0) { 'ok' }
                elseif ($null -eq $i.LastTaskResult) { '-' }
                else { '0x{0:X8}' -f $i.LastTaskResult }
        '{0,-30} {1,-9} last={2,-10} next={3}' -f $t.TaskName, $t.State, $last, $i.NextRunTime
    }

Write-Host "`nDone. 'last=' still shows the result of the previous (pre-fix) run until each" -ForegroundColor Cyan
Write-Host "task next fires. To verify now:" -ForegroundColor Cyan
Write-Host "  Start-ScheduledTask -TaskName 'US Market Open-Close Refresh'" -ForegroundColor Gray
Write-Host "  Get-Content '$Root\logs\refresh_log.txt' -Tail 5" -ForegroundColor Gray
Write-Host "  (outside the NY open/close window it should log 'off-hours -> skip' and stop there)" -ForegroundColor DarkGray
