# C:\QC-CI\setup_venv.ps1 —— 一次性：给服务器 CI 建独立 venv 并装测试依赖
# 与生产完全隔离（C:\QC-CI\venv），python 优先用生产同款 3.12（测出真实环境差异）
$ErrorActionPreference = "Stop"
$rt = "C:\QC-Packet\qc-pocket-space\runtime\python\python.exe"
$g314 = "C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe"
$vp = "C:\QC-CI\venv\Scripts\python.exe"
$mirror = "https://pypi.tuna.tsinghua.edu.cn/simple"
$testsReq = "C:\QC-CI\requirements-tests.txt"
$backendReq = "C:\QC-CI\requirements-backend.txt"

function Make-Venv($py) {
    if (Test-Path "C:\QC-CI\venv") { Remove-Item -Recurse -Force "C:\QC-CI\venv" }
    & $py -m venv C:\QC-CI\venv 2>&1 | Out-Null
    if (!(Test-Path $vp)) { return $false }
    & $vp -m pip --version 2>&1 | Out-Null
    return ($LASTEXITCODE -eq 0)
}

if (!(Test-Path $vp)) {
    $ok = Make-Venv $rt
    if (!$ok) { $ok = Make-Venv $g314 }
    if (!$ok) { Write-Output "SETUP_FAIL: venv/pip 不可用"; exit 1 }
}

& $vp -m pip install -q --disable-pip-version-check -i $mirror -r $testsReq -r $backendReq
if ($LASTEXITCODE -ne 0) {
    Write-Output "pip 镜像失败，回退官方源重试..."
    & $vp -m pip install -q --disable-pip-version-check -r $testsReq -r $backendReq
    if ($LASTEXITCODE -ne 0) { Write-Output "SETUP_FAIL: pip install 失败"; exit 1 }
}

& $vp -m pytest --version
if ($LASTEXITCODE -ne 0) { Write-Output "SETUP_FAIL: pytest 不可用"; exit 1 }
Write-Output "SETUP_OK"
