param(
    [Parameter(Mandatory=$true)][ValidateSet('hf','gh')][string]$Group,
    [string]$Root = 'D:\AnimaStudio'
)
$ErrorActionPreference = 'Continue'
$dl = Join-Path $Root '_dl'
New-Item -ItemType Directory -Force -Path $dl | Out-Null

$hf = 'https://hf-mirror.com'
$ghPrefix = 'https://gh-proxy.com/'

$hfItems = @(
    @{ n='anima-base-v1.0.safetensors'; u="$hf/circlestone-labs/Anima/resolve/main/split_files/diffusion_models/anima-base-v1.0.safetensors" },
    @{ n='qwen_3_06b_base.safetensors'; u="$hf/circlestone-labs/Anima/resolve/main/split_files/text_encoders/qwen_3_06b_base.safetensors" },
    @{ n='Qwen3.5-9B-Q4_K_M.gguf';     u="$hf/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf" },
    @{ n='wd-eva02-model.onnx';         u="$hf/SmilingWolf/wd-eva02-large-tagger-v3/resolve/main/model.onnx" },
    @{ n='selected_tags.csv';           u="$hf/SmilingWolf/wd-eva02-large-tagger-v3/resolve/main/selected_tags.csv" },
    @{ n='yolox_m_8xb8-300e_humanart-c2c7a14a.onnx'; u="$hf/Claquasse/Anima-Control-Pose/resolve/main/detector/yolox_m_8xb8-300e_humanart-c2c7a14a.onnx" },
    @{ n='rtmw-dw-x-l_simcc-cocktail14_270e-256x192_20231122.onnx'; u="$hf/Claquasse/Anima-Control-Pose/resolve/main/detector/rtmw-dw-x-l_simcc-cocktail14_270e-256x192_20231122.onnx" }
)

$ghItems = @(
    @{ n='ComfyUI_windows_portable_nvidia.7z'; u=($ghPrefix + 'https://github.com/Comfy-Org/ComfyUI/releases/download/v0.37.0/ComfyUI_windows_portable_nvidia.7z') },
    @{ n='RealESRGAN_x4plus_anime_6B.pth';    u=($ghPrefix + 'https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth') },
    @{ n='MinGit-2.55.0.5-64-bit.zip';         u=($ghPrefix + 'https://github.com/git-for-windows/git/releases/download/v2.55.0.windows.5/MinGit-2.55.0.5-64-bit.zip') }
)

$items = if ($Group -eq 'hf') { $hfItems } else { $ghItems }

function Get-RemoteLength([string]$u) {
    $h = & curl.exe -sIL -m 30 $u 2>$null
    $l = ($h -split "`n" | Where-Object { $_ -match '^[Cc]ontent-[Ll]ength:' } | Select-Object -Last 1)
    if ($l) { return [int64](($l -replace '[^0-9]','')) }
    return 0
}

Write-Output ("=== group " + $Group + " start " + (Get-Date -Format 'HH:mm:ss') + " ===")

foreach ($it in $items) {
    $out = Join-Path $dl $it.n
    $want = Get-RemoteLength $it.u
    $stall = 0
    while ($true) {
        $cur = if (Test-Path $out) { (Get-Item $out).Length } else { 0 }
        if ($want -gt 0 -and $cur -ge $want) { break }
        if ($stall -ge 6) { Write-Output ("GIVEUP " + $it.n + " at " + $cur + "/" + $want); break }
        & curl.exe -L -C - -sS --max-time 1200 -o $out $it.u
        $after = if (Test-Path $out) { (Get-Item $out).Length } else { 0 }
        if ($after -le $cur) {
            $stall++
            Start-Sleep -Seconds 3
        } else {
            $stall = 0
            Write-Output ("  .. " + $it.n + " " + [math]::Round($after/1MB,1) + "MB / " + [math]::Round($want/1MB,1) + "MB")
        }
    }
    $fin = if (Test-Path $out) { (Get-Item $out).Length } else { 0 }
    if ($want -gt 0 -and $fin -ge $want) {
        Write-Output ("OK " + $it.n + " " + $fin)
    } else {
        Write-Output ("INCOMPLETE " + $it.n + " " + $fin + "/" + $want)
    }
}

if ($Group -eq 'gh') {
    $f = Join-Path $dl 'ComfyUI_windows_portable_nvidia.7z'
    if ((Test-Path $f) -and (Get-Item $f).Length -eq 1925204508) {
        $h = (Get-FileHash $f -Algorithm SHA256).Hash.ToLower()
        if ($h -eq '7805f634fab51f63a238aaf0cfe2a9833bb7c86ddfc8400a60919f44460d7d65') {
            Write-Output 'COMFYUI-HASH-OK'
        } else {
            Write-Output ('COMFYUI-HASH-MISMATCH ' + $h)
        }
    } else {
        Write-Output 'COMFYUI-HASH-SKIPPED (incomplete)'
    }
}

Write-Output ("FETCH-" + $Group + "-DONE " + (Get-Date -Format 'HH:mm:ss'))