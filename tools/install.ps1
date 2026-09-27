param([string]$Root = 'D:\AnimaStudio')
$ErrorActionPreference = 'Continue'
$dl = Join-Path $Root '_dl'
$sevenz = 'C:\Program Files\7-Zip\7z.exe'

function Say($m) { Write-Output ("[install] " + $m) }

# ---------- 1. ComfyUI portable ----------
$p = Join-Path $dl 'ComfyUI_windows_portable_nvidia.7z'
$comfy = Join-Path $Root 'ComfyUI_windows_portable'
if ((Test-Path $p) -and -not (Test-Path (Join-Path $comfy 'python_embeded\python.exe'))) {
    Say 'extracting ComfyUI portable ...'
    & $sevenz x $p ("-o" + $Root) -y | Out-Null
}
Say ('comfy python_embeded: ' + (Test-Path (Join-Path $comfy 'python_embeded\python.exe')))

# ---------- 2. MinGit ----------
$mgz = Join-Path $dl 'MinGit-2.55.0.5-64-bit.zip'
$mgt = Join-Path $Root 'tools\MinGit'
if ((Test-Path $mgz) -and -not (Test-Path (Join-Path $mgt 'cmd\git.exe'))) {
    Say 'extracting MinGit ...'
    & $sevenz x $mgz ("-o" + $mgt) -y | Out-Null
}
Say ('MinGit git.exe: ' + (Test-Path (Join-Path $mgt 'cmd\git.exe')))

# ---------- 3. llama.cpp ----------
$lz = Join-Path $dl 'llama-b11177-bin-win-cpu-x64.zip'
$lt = Join-Path $Root 'tools\llamacpp'
if ((Test-Path $lz) -and -not (Test-Path (Join-Path $lt 'llama-server.exe'))) {
    Say 'extracting llama.cpp ...'
    & $sevenz x $lz ("-o" + $lt) -y | Out-Null
}
Say ('llama-server.exe: ' + (Test-Path (Join-Path $lt 'llama-server.exe')))

# ---------- 4. place weights ----------
$map = @(
    @{ f = 'anima-base-v1.0.safetensors';            d = 'models\diffusion_models' },
    @{ f = 'qwen_3_06b_base.safetensors';            d = 'models\text_encoders' },
    @{ f = 'qwen_image_vae.safetensors';             d = 'models\vae' },
    @{ f = 'anima-turbo-lora-v0.2.safetensors';      d = 'models\loras' },
    @{ f = 'anima_pose_preview2.safetensors';        d = 'models\loras' },
    @{ f = 'RealESRGAN_x4plus_anime_6B.pth';         d = 'models\upscale_models' },
    @{ f = 'Qwen3.5-9B-Q4_K_M.gguf';                 d = 'tools\gguf' },
    @{ f = 'wd-eva02-model.onnx';                    d = 'trainer\wd_tagger' },
    @{ f = 'selected_tags.csv';                      d = 'trainer\wd_tagger' }
)
foreach ($m in $map) {
    $src = Join-Path $dl $m.f
    $dstDir = Join-Path $Root $m.d
    New-Item -ItemType Directory -Force -Path $dstDir | Out-Null
    if (Test-Path $src) {
        $dst = Join-Path $dstDir $m.f
        if ((Test-Path $dst) -and ((Get-Item $dst).Length -eq (Get-Item $src).Length)) {
            Say ("skip (same size) " + $m.f)
        } else {
            Copy-Item $src $dst -Force
            Say ("placed " + $m.f + " -> " + $m.d)
        }
    } else {
        Say ("MISSING " + $m.f)
    }
}

# ---------- 5. pose detector cache (rtmlib expects ~/.cache/rtmlib/hub/checkpoints) ----------
$ckpt = Join-Path $Root 'env\rtmlib_cache\hub\checkpoints'
New-Item -ItemType Directory -Force -Path $ckpt | Out-Null
foreach ($onnx in @('yolox_m_8xb8-300e_humanart-c2c7a14a.onnx', 'rtmw-dw-x-l_simcc-cocktail14_270e-256x192_20231122.onnx')) {
    $src = Join-Path $dl $onnx
    if (Test-Path $src) { Copy-Item $src (Join-Path $ckpt $onnx) -Force; Say ("detector placed " + $onnx) }
    else { Say ("detector MISSING " + $onnx) }
}
$userCache = Join-Path $env:USERPROFILE '.cache\rtmlib'
if (-not (Test-Path $userCache)) {
    $parent = Split-Path $userCache -Parent
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    & cmd /c mklink /J "$userCache" (Join-Path $Root 'env\rtmlib_cache') | Out-Null
    Say ('rtmlib junction created: ' + (Test-Path $userCache))
} else {
    Say 'rtmlib cache path already exists (left untouched)'
}

# ---------- 5b. pose control custom nodes ----------
$nodeSrc = Join-Path $dl 'pose_control\comfyui'
$nodeDst = Join-Path $comfy 'ComfyUI\custom_nodes'
if ((Test-Path $nodeSrc) -and (Test-Path $comfy)) {
    New-Item -ItemType Directory -Force -Path $nodeDst | Out-Null
    foreach ($nd in @('anima_control_lora', 'ComfyUI-anima-pose-control')) {
        $s = Join-Path $nodeSrc $nd
        if (Test-Path $s) {
            Copy-Item $s (Join-Path $nodeDst $nd) -Recurse -Force
            Say ("custom node installed: " + $nd)
        } else {
            Say ("custom node MISSING: " + $nd)
        }
    }
} else {
    Say 'pose control node source not downloaded (skip)'
}

# ---------- 6. comfy extra_model_paths ----------
$stage = Join-Path $Root '_stage'
$target = Join-Path $comfy 'ComfyUI\extra_model_paths.yaml'
if ((Test-Path $comfy) -and -not (Test-Path $target)) {
    $srcYaml = Join-Path $stage 'extra_model_paths.yaml'
    if (Test-Path $srcYaml) { Copy-Item $srcYaml $target -Force; Say 'extra_model_paths.yaml placed' }
    else { Say 'extra_model_paths.yaml source missing (copy manually)' }
}
Say 'INSTALL-SCRIPT-DONE'