param([string]$Root = 'D:\AnimaStudio')
$ErrorActionPreference = 'Continue'
$py311 = Join-Path $Root 'trainer\py311\python.exe'
$venv = Join-Path $Root 'trainer\venv'
$vpy = Join-Path $venv 'Scripts\python.exe'
$mirror = 'https://mirrors.aliyun.com/pypi/simple/'
$logs = Join-Path $Root 'workspace\logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$log = Join-Path $logs 'setup_trainer.log'

function Say($m) {
    $line = "[trainer-setup] " + $m
    Write-Output $line
    Add-Content -Path $log -Value $line
}

if (-not (Test-Path $vpy)) {
    Say 'creating venv'
    & $py311 -m venv $venv
}
Say 'upgrading pip'
& $vpy -m pip install --upgrade pip -q --index-url $mirror --trusted-host mirrors.aliyun.com

Say 'installing torch (PyPI build via aliyun mirror)'
& $vpy -m pip install torch -q --index-url $mirror --trusted-host mirrors.aliyun.com
$arch = (& $vpy -c "import torch;print(torch.__version__, torch.version.cuda, ','.join(torch.cuda.get_arch_list()))") -join ' '
Say ("torch: " + $arch)

if ($arch -notmatch 'sm_120') {
    Say 'sm_120 missing -> trying cu130 build from pytorch.org'
    & $vpy -m pip install torch --upgrade --index-url https://download.pytorch.org/whl/cu130
    $arch = (& $vpy -c "import torch;print(torch.__version__, torch.version.cuda, ','.join(torch.cuda.get_arch_list()))") -join ' '
    Say ("torch after upgrade: " + $arch)
}

Say 'installing sd-scripts requirements (this pulls transformers/diffusers/accelerate)'
Push-Location (Join-Path $Root 'trainer\sd-scripts')
& $vpy -m pip install -r requirements.txt -q --index-url $mirror --trusted-host mirrors.aliyun.com
Pop-Location

$check = (& $vpy -c "import accelerate, transformers, diffusers, safetensors;print('deps ok')") -join ' '
Say ("deps: " + $check)
Say 'TRAINER-SETUP-DONE'