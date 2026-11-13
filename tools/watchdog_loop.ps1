# Runs the watchdog every $IntervalMin minutes, forever, in one hidden process.
#
# Why a loop and not a scheduled task: registering a task needs administrator
# rights on this machine (schtasks and Register-ScheduledTask both answer
# "Access is denied"), and a Startup shortcut does not. Put this file's
# launcher in the Startup folder and it comes back after every logon.
#
# Only one copy runs: a named mutex guards it.

param(
    [int]$IntervalMin = 15
)

$ErrorActionPreference = 'Continue'

$Root     = $PSScriptRoot
$Watchdog = Join-Path $Root 'watchdog.ps1'
$LogFile  = Join-Path $Root 'watchdog.log'

function Write-Log($message) {
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $message
    try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 } catch { }
}

$mutex = New-Object System.Threading.Mutex($false, 'Global\DSHMagpieWatchdogLoop')
$owned = $false
try { $owned = $mutex.WaitOne(0) } catch { $owned = $true }
if (-not $owned) {
    Write-Log '巡检循环：已经有一个在跑，这个退出'
    exit 0
}

Write-Log ('巡检循环：启动，每 {0} 分钟检查一次' -f $IntervalMin)

while ($true) {
    try {
        $out = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Watchdog 2>&1
        # watchdog.ps1 already appends its own lines; only surface real errors
        foreach ($line in $out) {
            if ($line -match '失败|警告') { Write-Log ('  ' + $line) }
        }
    } catch {
        Write-Log ('巡检循环：调用出错 ' + $_.Exception.Message)
    }
    Start-Sleep -Seconds ($IntervalMin * 60)
}
