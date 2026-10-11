# 重启后端 —— ★ 显式把 User 级环境变量里的 API Key 注入到新进程。
#
# 为什么必须有这个脚本（2026-10-04 实测踩过的坑）：
#   Windows 上设置环境变量只对【之后新开】的程序生效；**已经开着的 shell 不会自动刷新**。
#   从这样一个"环境快照过期"的 shell 里启动后端 ⇒ 新后端拿不到 XIAOMI_MIMO_API_KEY
#   ⇒ providers/openai_compat.py 在构造时就抛
#        ValueError: 环境变量 XIAOMI_MIMO_API_KEY 未设置
#   ⇒ K6b 的"只警告不拒绝启动"让服务照常起来，但**模型连接是坏的** ⇒ 用户发消息没有回复。
#   实测时间线：10:42 那次重启起，连续 4 个后端进程（19728/32572/25924/16588）
#   全部报"模型提供者不可用"，直到本脚本从注册表显式读取才恢复。
#
# 用法：  powershell -NoProfile -ExecutionPolicy Bypass -File .\restart-backend.ps1
#   可选：-KeyName XXX_API_KEY  换一个环境变量名

param(
    [string]$KeyName = "XIAOMI_MIMO_API_KEY",
    [int]$Port = 8642
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# ① 从【注册表】读 User 级变量（不受当前 shell 过期环境的影响），注入本进程
$keyVal = [Environment]::GetEnvironmentVariable($KeyName, "User")
if ($keyVal) {
    Set-Item -Path "Env:$KeyName" -Value $keyVal
    Write-Host "[ok] 已注入 $KeyName（长度 $($keyVal.Length)）—— 子进程会继承到"
} else {
    Write-Warning "User 级 $KeyName 读不到。后端仍会启动，但模型连接建不起来（agent 不会回复）。"
    Write-Warning "请在【系统环境变量】里设置它，或用界面「设置」填 Key（那样只对当前这次运行生效）。"
}

# ② 停掉旧后端（只认 app.main，避免误杀别的进程）
$conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    $owner = $conn.OwningProcess
    $cmdline = (Get-CimInstance Win32_Process -Filter "ProcessId=$owner").CommandLine
    if ($cmdline -match "app\.main") {
        Stop-Process -Id $owner -Force
        Write-Host "[ok] 已停旧后端 PID $owner"
    } else {
        throw "端口 $Port 被非后端进程占用，拒绝操作：$cmdline"
    }
} else {
    Write-Host "[..] 端口 $Port 上没有监听，直接启动"
}

Start-Sleep -Seconds 2

# ③ 启动新后端（★ 必须用 cmd.exe；本仓实测 Start-Process pwsh 会报 cannot find the file specified）
Start-Process -FilePath "cmd.exe" `
    -ArgumentList "/c", "cd /d $root\backend && .venv\Scripts\python.exe -m app.main >> $root\backend_restart.log 2>&1" `
    -WindowStyle Hidden

Start-Sleep -Seconds 8

# ④ 收尾验活：端口 + HTTP + 模型提供者就绪
$conn2 = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $conn2) { throw "新后端起不来（端口 $Port 无监听）—— 看 $root\backend_restart.log" }
Write-Host "[ok] 新后端 PID $($conn2.OwningProcess)"

# ★ 第 8d 处：开了局域网直连后，**/api/* 一律要访问密码** —— 探活不带密码必然 401，
#   原先那版会把"其实起来了"误判成失败（本班实测踩到：脚本抛错但其实后端已就绪）。
#   所以这里读 config.json：host=0.0.0.0 且有 access_token 时，探活带上 token。
$probe = "http://127.0.0.1:$Port/api/v1/settings"
try {
    $cfgTxt = [System.IO.File]::ReadAllText((Join-Path $root 'config.json'), [System.Text.Encoding]::UTF8)
    $cfgObj = $cfgTxt | ConvertFrom-Json
    if ($cfgObj.server.host -eq '0.0.0.0' -and $cfgObj.server.access_token) {
        # ★ 必须写 ${probe}：PowerShell 里 `?` 会被当成变量名的一部分 ——
        #   "$probe?token=..." 实际解析成变量 $probe?token（未定义→空串），
        #   URL 只剩下 "=xxx"，报"无效的 URI: 未能分析主机名"（本班实测踩到）。
        $probe = "${probe}?token=$($cfgObj.server.access_token)"
        Write-Host "[..] 局域网直连已开启 —— 探活带访问密码"
    }
} catch {
    Write-Host "[..] 读 config.json 失败（按未开局域网处理）：$($_.Exception.Message)"
}

try {
    $r = Invoke-WebRequest $probe -UseBasicParsing -TimeoutSec 10
    Write-Host "[ok] HTTP $($r.StatusCode)"
} catch {
    throw "HTTP 探活失败：$_"
}

# ⑤ ★ 关键一步：确认这次启动【模型提供者是就绪的】，而不是"服务活着但模型是坏的"
$tail = Get-Content "$root\backend_restart.log" -Tail 40 -Encoding utf8 -ErrorAction SilentlyContinue
if ($tail | Where-Object { $_ -match "provider=.*就绪|provider=\w+ " }) {
    Write-Host "[ok] 模型提供者就绪 ✓"
} elseif ($tail | Where-Object { $_ -match "模型提供者不可用|未设置" }) {
    Write-Warning "★ 模型提供者仍不可用 —— agent 不会回复。检查上面的 Key 提示。"
} else {
    Write-Host "[..] 日志里没看到提供者状态行，请人工确认：$root\backend_restart.log"
}
