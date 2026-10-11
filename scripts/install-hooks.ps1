<#
  安装 / 卸载 git 钩子（把 core.hooksPath 指到仓库内的 .githooks/）

  用法（仓库根）：
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-hooks.ps1
    powershell ... -File .\scripts\install-hooks.ps1 -Uninstall

  注意：core.hooksPath 是【每个克隆各自的本地配置】，不会随 git 提交传播。
  所以换一台机器/重新克隆后，要再跑一次这个脚本。这也是它放在 scripts/ 的原因。
#>
param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

if ($Uninstall) {
    & git config --unset core.hooksPath 2>$null
    Write-Host "[ok] 已卸载（core.hooksPath 清空，提交不再自动检查）"
    exit 0
}

$hook = Join-Path $root ".githooks\pre-commit"
if (-not (Test-Path $hook)) {
    Write-Host "[X] 找不到 $hook" -ForegroundColor Red
    exit 1
}

& git config core.hooksPath .githooks
$now = (& git config --get core.hooksPath)
Write-Host "[ok] core.hooksPath = $now"
Write-Host ""
Write-Host "以后每次 git commit 会自动跑："
Write-Host "  ① 变更 Python 文件的语法自检（几秒）"
Write-Host "  ② pyflakes（几秒）"
Write-Host "  ③ 全量 pytest（约 30 秒，仅在动了 backend/ 或 scripts/ 时）"
Write-Host ""
Write-Host "完整门（含 55 组红绿回滚，约 9 分钟）请另行跑："
Write-Host "  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check_all.ps1"
