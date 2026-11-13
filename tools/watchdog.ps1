# Watchdog: keep DeepSeek Harness on the China WorkBuddy accounts.
#
# Magpie parks a provider after it sees one 429 and then quietly serves
# everything from the paid relay (fan). This script notices that and pulls
# the traffic back once the China gateway answers again.
#
# What it decides with:
#   - did wb8788 serve anything in the last $WindowMin minutes?
#   - did fan serve anything in that window?
#   - does the China gateway answer a tiny request right now?
# Restart Magpie only when: fan is serving, wb8788 is not, the gateway is
# healthy, nothing is in flight, and it has not restarted recently.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File watchdog.ps1 -WhatIf   (dry run)
#   powershell -ExecutionPolicy Bypass -File watchdog.ps1           (act)

param(
    [switch]$WhatIf,
    [switch]$Force,
    [switch]$TestRestart
)

$ErrorActionPreference = 'Stop'

$Root        = $PSScriptRoot
$LogFile     = Join-Path $Root 'watchdog.log'
$StateFile   = Join-Path $Root 'watchdog.state.json'
# ↓↓ 这两行按你自己的 Magpie 安装位置改 ↓↓
$MagpieCli   = 'C:\Program Files\Magpie\magpie-cli-windows-amd64.exe'
$MagpieApp   = 'C:\Program Files\Magpie\magpie-windows-amd64.exe'
$RoutingDir  = Join-Path $env:USERPROFILE '.config\magpie\routing'
$GatewayRoot = 'D:\自己开发的程序\WorkBuddy2API'
$GatewayUrl  = 'http://127.0.0.1:8788/v1/chat/completions'
$GatewayKey  = ''          # 留空 = 自动从网关的 accounts\settings.json 读第一个启用的 key

function Get-GatewayKey {
    if ($GatewayKey) { return $GatewayKey }
    $settings = Join-Path $GatewayRoot 'accounts\settings.json'
    try {
        $j = Get-Content $settings -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($k in $j.api_keys) { if ($k.enabled -and $k.key) { return $k.key } }
    } catch { }
    return ''
}

$WindowMin       = 15   # look at this many minutes of routing log
$RestartCooldown = 20   # never restart twice inside this many minutes
$IdleSeconds     = 45   # do not cut a request that is probably in flight

function Write-Log($message) {
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $message
    try {
        Add-Content -Path $LogFile -Value $line -Encoding UTF8
        # keep the log small: last 800 lines
        $all = Get-Content $LogFile -Encoding UTF8
        if ($all.Count -gt 800) {
            $all | Select-Object -Last 800 | Set-Content $LogFile -Encoding UTF8
        }
    } catch { }
    Write-Output $line
}

function Get-State {
    if (Test-Path $StateFile) {
        try {
            $raw = Get-Content $StateFile -Raw -Encoding UTF8
            if ($raw) {
                $o = $raw | ConvertFrom-Json
                return @{ lastRestart = $o.lastRestart; restarts = [int]$o.restarts }
            }
        } catch { }
    }
    return @{ lastRestart = $null; restarts = 0 }
}

function Save-State($state) {
    try { $state | ConvertTo-Json | Set-Content $StateFile -Encoding UTF8 } catch { }
}

function Test-Gateway {
    $body = '{"model":"deepseek-v4.1-flash","messages":[{"role":"user","content":"watchdog ping"}],"max_tokens":8,"stream":false}'
    try {
        $resp = Invoke-WebRequest -Uri $GatewayUrl -Method Post -TimeoutSec 30 `
            -Headers @{ Authorization = 'Bearer ' + (Get-GatewayKey) } `
            -ContentType 'application/json' -Body $body -UseBasicParsing
        return ($resp.StatusCode -eq 200)
    } catch {
        return $false
    }
}

# ---------- read the routing log ----------
$today = (Get-Date).ToString('yyyy-MM-dd')
$routingFile = Join-Path $RoutingDir ($today + '.jsonl')

$recentCn = $false
$recentFan = $false
$lastRequest = $null

if (Test-Path $routingFile) {
    $cutoff = (Get-Date).AddMinutes(-1 * $WindowMin)
    $lines = Get-Content $routingFile -Encoding UTF8 -Tail 600
    foreach ($raw in $lines) {
        if ([string]::IsNullOrWhiteSpace($raw)) { continue }
        try { $o = $raw | ConvertFrom-Json } catch { continue }
        if (-not $o.time) { continue }
        $t = [datetime]::Parse($o.time)
        if ($lastRequest -eq $null -or $t -gt $lastRequest) { $lastRequest = $t }
        if ($t -lt $cutoff) { continue }
        if ($o.served -eq 'deepseek-v4.1-flash') { $recentCn = $true }
        if ($o.served -eq 'deepseek-flash') { $recentFan = $true }
    }
}

$idleFor = $null
if ($lastRequest) { $idleFor = ((Get-Date) - $lastRequest).TotalSeconds }

Write-Log ('检查：窗口={0}分钟 国内服务={1} 中转服务={2} 最近请求={3}秒前' -f `
    $WindowMin, $recentCn, $recentFan, $(if ($idleFor -ne $null) { [int]$idleFor } else { '无' }))

# ---------- decide ----------
if ($TestRestart) {
    Write-Log '（-TestRestart：直接演练重启流程）'
}
$state = Get-State

if (-not $TestRestart) {
    if ($recentCn) {
        Write-Log '结论：国内正在服务，不用动'
        exit 0
    }
    if (-not $recentFan) {
        Write-Log '结论：最近没有流量，不用动'
        exit 0
    }
    if (-not $Force -and $idleFor -ne $null -and $idleFor -lt $IdleSeconds) {
        Write-Log ('结论：{0} 秒前还有请求在跑，这轮先不动' -f [int]$idleFor)
        exit 0
    }

    if (-not $Force -and $state.lastRestart) {
        try {
            $since = ((Get-Date) - [datetime]::Parse($state.lastRestart)).TotalMinutes
            if ($since -lt $RestartCooldown) {
                Write-Log ('结论：{0} 分钟前刚重启过，冷却中' -f [int]$since)
                exit 0
            }
        } catch { }
    }
}

Write-Log '流量在中转站，正在探测国内网关是否恢复…'
$healthy = Test-Gateway
if (-not $healthy) {
    Write-Log '结论：国内网关仍然不通（账号限流中），保持现状，等账号恢复'
    exit 0
}

Write-Log '国内网关已恢复，重启 Magpie 把流量拉回国内'
if ($WhatIf) {
    Write-Log '（-WhatIf：只演练，没有真的重启）'
    exit 0
}

try {
    Get-Process | Where-Object { $_.ProcessName -match '^magpie-windows-amd64' } | Stop-Process -Force
    Start-Sleep -Seconds 4
    Start-Process -FilePath $MagpieApp | Out-Null
    Start-Sleep -Seconds 18

    $up = $false
    for ($i = 0; $i -lt 10; $i++) {
        try {
            $t = Test-NetConnection -ComputerName '127.0.0.1' -Port 3425 -WarningAction SilentlyContinue
            if ($t.TcpTestSucceeded) { $up = $true; break }
        } catch { }
        Start-Sleep -Seconds 3
    }

    if ($up) {
        # Magpie rewrites DSH's config on startup and the reasoning level drops
        # back to its default; put it back to max. Started as its own process:
        # the CLI exits non-zero for its stderr chatter even on success.
        try {
            Start-Process -FilePath $MagpieCli -ArgumentList 'dsh', 'effort', 'max' -WindowStyle Hidden -Wait -ErrorAction Stop | Out-Null
            Write-Log '已把 DSH 思考档位恢复成 max'
        } catch {
            Write-Log '警告：没能把思考档位设回 max，请手动跑 magpie dsh effort max'
        }
        $state.lastRestart = (Get-Date).ToString('o')
        $state.restarts = [int]$state.restarts + 1
        Save-State $state
        Write-Log ('完成：Magpie 已重启，3425 在监听（累计第 {0} 次）' -f $state.restarts)
    } else {
        Write-Log '警告：重启后 3425 没有起来，请人工检查'
        exit 1
    }
} catch {
    Write-Log ('失败：' + $_.Exception.Message)
    exit 1
}
