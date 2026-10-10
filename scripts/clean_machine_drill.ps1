# Clean-machine install drill (Phase 1 item 3).
# ASCII-only on purpose: PowerShell 5.1 reads BOM-less UTF-8 as GBK and would choke on Chinese.
#
# What it does:
#   1) git archive HEAD -> a pristine sandbox (exactly what a fresh clone has:
#      no .venv / node_modules / data / config.json / frontend\dist)
#   2) preflight BEFORE  (must report the missing pieces with actionable fixes)
#   3) run install.bat   (venv + pip install + npm ci + config + build)
#   4) preflight AFTER   (must be all-green)
param(
    [string]$Repo = "D:\AI\agent-shell",
    [string]$Log  = "$env:TEMP\clean-machine-drill.log"
)
$ErrorActionPreference = "Continue"
$out = New-Object System.Collections.Generic.List[string]
function Say($m) { $out.Add($m); Write-Host $m }

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$sandbox = Join-Path $env:TEMP "as-clean-$stamp"
New-Item -ItemType Directory -Force -Path $sandbox | Out-Null

Say "=== 1) build pristine sandbox (a fresh clone, nothing installed) ==="
Say "sandbox = $sandbox"
Remove-Item $sandbox -Recurse -Force -ErrorAction SilentlyContinue   # clone needs an empty target
# NOTE: deliberately NOT `git archive HEAD | tar -x` -- on Windows the bundled tar
# SILENTLY drops every path containing non-ASCII (Chinese) characters. Measured on
# 2026-10-04: 266 tracked files -> 201 extracted; the whole skill library either
# vanished or came back with mangled names, while the app still started happily.
# A real clean machine gets the code via git clone / 7-Zip, so clone (local, fast).
# The tar trap itself stays pinned by scripts\preflight.py's skill-library check.
& git clone --quiet --no-hardlinks $Repo $sandbox 2>&1 | ForEach-Object { Say "  clone: $_" }
Remove-Item "$sandbox\.git" -Recurse -Force -ErrorAction SilentlyContinue

$files = (Get-ChildItem $sandbox -Recurse -File -ErrorAction SilentlyContinue).Count
$dirs  = (Get-ChildItem $sandbox -Recurse -Directory -ErrorAction SilentlyContinue).Count
Say "  extracted: $files files / $dirs dirs"
foreach ($must_be_absent in @("backend\.venv", "frontend\node_modules", "config.json", "frontend\dist", "data")) {
    $p = Join-Path $sandbox $must_be_absent
    Say ("  absent-check {0,-26} = {1}" -f $must_be_absent, (-not (Test-Path $p)))
}

$py = "$Repo\backend\.venv\Scripts\python.exe"   # host python (a real clean machine lacks it: see note)

Say ""
Say "=== 2) preflight BEFORE install (expect venv/config/ui = fail) ==="
& $py "$sandbox\scripts\preflight.py" --root $sandbox 2>&1 | ForEach-Object { Say "  $_" }
Say "  (exit code = $LASTEXITCODE ; 1 = blocked, which is the correct answer here)"

Say ""
Say "=== 3) run install.bat (this is the real drill; may take minutes) ==="
# A machine may have a *broken* `python` on PATH (python.exe + DLLs but no Lib\).
# install.bat then correctly refuses with an actionable message (live-verified
# 2026-10-04). For the DRILL we want to exercise the install pipeline, so point
# AGENT_SHELL_PYTHON at the interpreter this repo's venv was built from
# (pyvenv.cfg -> home) -- which is exactly what the message tells a user to do.
if (-not $env:AGENT_SHELL_PYTHON) {
    $cfg = Join-Path $Repo "backend\.venv\pyvenv.cfg"
    if (Test-Path $cfg) {
        $line = (Select-String -Path $cfg -Pattern '^home\s*=\s*(.+)$' | Select-Object -First 1).Line
        if ($line) {
            $home = ($line -split '=', 2)[1].Trim()
            $cand = Join-Path $home "python.exe"
            if (Test-Path $cand) {
                $env:AGENT_SHELL_PYTHON = $cand
                Say "  AGENT_SHELL_PYTHON = $cand   (from backend\.venv\pyvenv.cfg)"
            }
        }
    }
}
$sw = [System.Diagnostics.Stopwatch]::StartNew()
Push-Location $sandbox
# NOTE: stdin must come from NUL so the "pause" inside install.bat returns immediately.
# The first version wrote `cmd /c "install.bat < nul"` -- cmd then treated "<" as part of
# the *filename* and nothing ran (the drill "passed" with zero artifacts installed).
& cmd /c "install.bat < NUL" 2>&1 | ForEach-Object { Say "  $_" }
$installRc = $LASTEXITCODE
Pop-Location
$sw.Stop()
Say "  install.bat exit=$installRc  elapsed=$([int]$sw.Elapsed.TotalSeconds)s"

Say ""
Say "=== 4) preflight AFTER install (expect all ok) ==="
& $py "$sandbox\scripts\preflight.py" --root $sandbox 2>&1 | ForEach-Object { Say "  $_" }
$afterRc = $LASTEXITCODE
Say "  (exit code = $afterRc ; 0 = ready)"

Say ""
Say "=== 5) artifacts ==="
foreach ($p in @("backend\.venv\Scripts\python.exe", "config.json", "frontend\node_modules", "frontend\dist\index.html")) {
    $full = Join-Path $sandbox $p
    $note = if (Test-Path $full) { "present" } else { "MISSING" }
    Say ("  {0,-34} {1}" -f $p, $note)
}
Say ""
Say "SANDBOX=$sandbox"
$out | Set-Content -Path $Log -Encoding UTF8
Write-Host "log -> $Log"

# ★ 2026-10-06：**必须显式给退出码** ✗ —— 原来没有 exit，脚本的退出码就成了最后一条命令的
#   退出码（本班实测：演练明明全绿，外面拿到的是 1 ✓，看起来像失败 ✗）。
#   这是一个"守门脚本"，红/绿必须可靠 ✓：
#     · install.bat 失败        ⇒ 1
#     · preflight AFTER 不为 0  ⇒ 1（它是"装完能不能用"的判据 ✓）
#     · 任一关键产物缺失        ⇒ 1
$missing = @()
foreach ($p in @("backend\.venv\Scripts\python.exe", "config.json", "frontend\node_modules", "frontend\dist\index.html")) {
    if (-not (Test-Path (Join-Path $sandbox $p))) { $missing += $p }
}
if ($installRc -ne 0 -or $afterRc -ne 0 -or $missing.Count -gt 0) {
    Say ""
    Say "DRILL FAILED: install=$installRc preflight=$afterRc missing=$($missing -join ', ')"
    exit 1
}
Say ""
Say "DRILL PASSED: install=0 preflight=0 artifacts=all-present"
exit 0
