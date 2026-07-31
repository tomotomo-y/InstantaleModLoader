# CI (.github/workflows/ci.yml) と同じ検査を同じ順で回す。
# 使い方: powershell -NoProfile -ExecutionPolicy Bypass -File .cursor/skills/instantale-dev-setup/scripts/verify.ps1
#
# 手元の python は 3.13 なので、ゲーム内の 3.10 互換は check_mods.py の
# ast(feature_version=(3,10)) が見る。本物の 3.10 コンパイルは CI 側。

$ErrorActionPreference = "Continue"
$root = (Resolve-Path (Join-Path $PSScriptRoot "../../../..")).Path
Push-Location $root

$failures = @()

Write-Host "== compileall =="
python -m compileall -q runtime tools
if ($LASTEXITCODE -ne 0) { $failures += "compileall" }

Write-Host "== check_mods =="
python tools/check_mods.py
if ($LASTEXITCODE -ne 0) { $failures += "check_mods" }

Write-Host "== offline tests =="
Get-ChildItem "tools/test_*.py" | ForEach-Object {
    $output = & python $_.FullName 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host ("  FAIL " + $_.Name)
        $output | Select-String -Pattern "FAIL|Error|error" | Select-Object -First 5
        $failures += $_.Name
    } else {
        Write-Host ("  ok   " + $_.Name)
    }
}

Get-ChildItem -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

Pop-Location

if ($failures.Count -gt 0) {
    Write-Host ""
    Write-Host ("FAILED: " + ($failures -join ", "))
    exit 1
}

Write-Host ""
Write-Host "all checks passed"
