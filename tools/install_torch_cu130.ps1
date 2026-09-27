param([string]$Root = 'D:\AnimaStudio')
$ErrorActionPreference = 'Continue'
$vpy = Join-Path $Root 'trainer\venv\Scripts\python.exe'
$dl = Join-Path $Root '_dl'
$whl = Join-Path $dl 'torch-2.14.0+cu130-cp311-cp311-win_amd64.whl'
$url = 'https://download.pytorch.org/whl/cu130/torch-2.14.0%2Bcu130-cp311-cp311-win_amd64.whl'
$want = 1990587822

New-Item -ItemType Directory -Force -Path $dl | Out-Null

function SZ($p) {
    if (-not (Test-Path $p)) { return 0 }
    $fs = [System.IO.File]::Open($p, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try { return $fs.Length } finally { $fs.Close() }
}

$stall = 0
while ($true) {
    $cur = SZ $whl
    if ($cur -ge $want) { break }
    if ($stall -ge 6) { Write-Output ("GIVEUP at " + $cur + "/" + $want); exit 1 }
    & curl.exe -L -C - -sS --max-time 900 --speed-limit 262144 --speed-time 60 -o $whl $url
    $after = SZ $whl
    if ($after -le $cur) {
        $stall++
        Start-Sleep -Seconds 5
    } else {
        $stall = 0
        Write-Output ("  .. " + [math]::Round($after / 1MB, 1) + "MB / " + [math]::Round($want / 1MB, 1) + "MB")
    }
}
Write-Output ("download ok: " + (SZ $whl) + " bytes")

Write-Output 'installing local wheel (deps from aliyun mirror)'
& $vpy -m pip install $whl --index-url https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com

$arch = (& $vpy -c "import torch;print(torch.__version__, torch.version.cuda, ','.join(torch.cuda.get_arch_list()))") -join ' '
Write-Output ("torch now: " + $arch)
if ($arch -match 'sm_120') { Write-Output 'TORCH-CUDA-OK' } else { Write-Output 'TORCH-CUDA-BAD' }