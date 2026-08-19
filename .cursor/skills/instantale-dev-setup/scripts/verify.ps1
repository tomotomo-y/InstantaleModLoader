# CI (.github/workflows/ci.yml) と同じ検査を同じ順で回す。
# 使い方: powershell -NoProfile -ExecutionPolicy Bypass -File .cursor/skills/instantale-dev-setup/scripts/verify.ps1
#
# 手元の python は 3.13 なので、ゲーム内の 3.10 互換は check_mods.py の
# ast(feature_version=(3,10)) が見る。本物の 3.10 コンパイルは CI 側。
#
# 本家は検査を tools/tests/ へ移した。fork 固有の tools/test_*.py も残す。

$ErrorActionPreference = "Continue"
$root = (Resolve-Path (Join-Path $PSScriptRoot "../../../..")).Path
Push-Location $root

$failures = @()

Write-Host "== compileall =="
python -m compileall -q runtime tools -x "(mods.9[0-9][0-9]_|test_wip_)"
if ($LASTEXITCODE -ne 0) { $failures += "compileall" }

Write-Host "== check_mods =="
python tools/check_mods.py
if ($LASTEXITCODE -ne 0) { $failures += "check_mods" }

Write-Host "== offline tests =="
$wip = Get-ChildItem tools/tests/test_wip_*.py -ErrorAction SilentlyContinue |
       Sort-Object Name
foreach ($w in $wip) { Write-Host ("  skip " + $w.Name + "  (開発中)") }

$tests = @()
if (Test-Path "tools/tests") {
    $tests += Get-ChildItem "tools/tests/test_*.py" |
              Where-Object { $_.Name -notlike "test_wip_*" }
}
$tests += Get-ChildItem "tools/test_*.py" -ErrorAction SilentlyContinue
$tests = $tests | Sort-Object FullName

foreach ($f in $tests) {
    $output = & python $f.FullName 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host ("  FAIL " + $f.Name)
        $output | Select-String -Pattern "FAIL|Error|error" | Select-Object -First 5
        $failures += $f.Name
    } else {
        Write-Host ("  ok   " + $f.Name)
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
