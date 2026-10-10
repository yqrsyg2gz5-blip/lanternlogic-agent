<#
  LanternLogic Agent —— 一键全量自检（「守门」的唯一入口）

  为什么要有它：
    仓库里有 938 个用例 + 55 组红绿回滚资产，但此前【没有任何机制保证它们被运行】。
    2026-10-04 实测教训：审计方自己两次弄坏东西（一次修得不全、一次改注释打断
    两组回归锚点），都是靠手工跑全量才发现的 —— 那说明"靠人记得跑"不成立。

  用法（在仓库根）：
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check_all.ps1
    :: 只跑快检（跳过约 9 分钟的红绿）：
    powershell ... -File .\scripts\check_all.ps1 -Fast

  退出码：0 = 全绿；1 = 有失败项（明细见输出末尾的汇总表）
#>
param(
    [switch]$Fast
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$py = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "[X] 找不到 venv python：$py" -ForegroundColor Red
    exit 1
}

$results = @()
function Record($name, $ok, $detail) {
    $script:results += [pscustomobject]@{ 项 = $name; 结果 = $(if ($ok) { "PASS" } else { "FAIL" }); 说明 = $detail }
}

# ── 0. 红绿要求干净树：先看树脏不脏（脏了红绿一定跑不起来，提前说清楚）──
$dirty = (& git status --porcelain -uall -- backend frontend/src scripts docs 2>$null) | Where-Object { $_ -ne "" }
$treeClean = -not $dirty

Write-Host "=== [1/5] pytest（全量）===" -ForegroundColor Cyan
Push-Location (Join-Path $root "backend")
$env:PYTHONDONTWRITEBYTECODE = "1"
$pytestOut = & $py -m pytest -o addopts="" -p no:cacheprovider 2>&1
$pytestRc = $LASTEXITCODE
Pop-Location
$pytestLine = ($pytestOut | Select-String -Pattern "passed|failed|error" | Select-Object -Last 1).Line
Write-Host "  $pytestLine"
Record "pytest" ($pytestRc -eq 0) $pytestLine

Write-Host "=== [2/5] pyflakes ===" -ForegroundColor Cyan
Push-Location (Join-Path $root "backend")
$flakeOut = & $py -m pyflakes app tests ../scripts 2>&1
$flakeRc = $LASTEXITCODE
Pop-Location
if ($flakeRc -eq 0) { Write-Host "  exit 0（无输出）" }
else { $flakeOut | ForEach-Object { Write-Host "  $_" } }
Record "pyflakes" ($flakeRc -eq 0) "exit=$flakeRc"

Write-Host "=== [3/5] tsc --noEmit ===" -ForegroundColor Cyan
Push-Location (Join-Path $root "frontend")
$tscOut = & node node_modules\typescript\bin\tsc --noEmit 2>&1
$tscRc = $LASTEXITCODE
Pop-Location
if ($tscRc -eq 0) { Write-Host "  exit 0" } else { $tscOut | Select-Object -First 10 | ForEach-Object { Write-Host "  $_" } }
Record "tsc" ($tscRc -eq 0) "exit=$tscRc"

# [新] 前端产物新鲜度：用户的应用吃的是 frontend/dist，源码改了没构建 = 用户看不到。
# 2026-10-05 的教训：一整天界面修复用户一个都没看到，还因此踩到早就修好的崩溃。
Write-Host "=== [4/5] 前端产物新鲜度 ===" -ForegroundColor Cyan
$distOut = & $py (Join-Path $root "scripts\check_dist_freshness.py") 2>&1
$distRc = $LASTEXITCODE
$distOut | ForEach-Object { Write-Host "  $_" }
if ($distRc -eq 0) {
    Record "dist(产物新鲜)" $true "最新"
} else {
    Record "dist(产物过期)" $false "先 cd frontend; npm run build"
}

if ($Fast) {
    Record "redgreen(已跳过)" $true "已跳过（-Fast）"
} elseif (-not $treeClean) {
    Write-Host "=== [5/5] redgreen：跳过 —— 受保护范围有未提交改动 ===" -ForegroundColor Yellow
    $dirty | Select-Object -First 8 | ForEach-Object { Write-Host "    $_" }
    Record "redgreen(未跑)" $false "树不干净，跑不了（先提交或还原）"
} else {
    Write-Host "=== [5/5] redgreen（全组，约 9 分钟）===" -ForegroundColor Cyan
    $rgLog = Join-Path $env:TEMP "as_check_all_redgreen.log"
    & $py (Join-Path $root "scripts\redgreen_check.py") *> $rgLog
    $rgRc = $LASTEXITCODE
    $tail = Get-Content $rgLog -Tail 40 -ErrorAction SilentlyContinue
    $total = ($tail | Select-String -Pattern "总计").Line
    Write-Host "  $total"
    # 逐组核对 note 格式：只认「回滚跑=RED(断言) 恢复跑=GREEN」
    $badNote = ($tail | Select-String -Pattern "回滚跑=" | Where-Object { $_.Line -notmatch "回滚跑=RED\(断言\)\s+恢复跑=GREEN" }).Count
    if ($badNote -gt 0) { Write-Host "  [X] 有 $badNote 组的 note 不是「RED(断言)/GREEN」—— 红源可疑" -ForegroundColor Red }
    # ★ 组名不再写死数字（此前硬编码"55 组"，每加一组就要改脚本，还会让人误以为
    #   只有 55 组在跑）。从「总计 N/M 组 PASS」里取真实组数。
    $m = [regex]::Match([string]$total, '(\d+)\s*/\s*(\d+)')
    $rgLabel = if ($m.Success) { "redgreen($($m.Groups[2].Value) 组)" } else { "redgreen(组数未解析)" }
    Record $rgLabel (($rgRc -eq 0) -and ($badNote -eq 0)) "$total（非标准 note $badNote 组）  日志=$rgLog"
}

Write-Host ""
Write-Host "================ 汇总 ================" -ForegroundColor Cyan
$results | Format-Table -AutoSize
$failed = @($results | Where-Object { $_.结果 -eq "FAIL" }).Count

if (-not $treeClean -and -not $Fast) {
    Write-Host "提示：受保护范围当前有未提交改动，红绿组没能验证。提交后再跑一次才算完整。" -ForegroundColor Yellow
}
if ($failed -eq 0) {
    Write-Host "★ 全绿（$($results.Count) 项）" -ForegroundColor Green
    exit 0
} else {
    Write-Host "★ 有 $failed 项失败 —— 逐条看上表" -ForegroundColor Red
    exit 1
}
