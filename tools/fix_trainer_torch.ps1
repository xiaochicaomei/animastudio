param([string]$Root = 'D:\AnimaStudio')
$ErrorActionPreference = 'Continue'
$vpy = Join-Path $Root 'trainer\venv\Scripts\python.exe'
$logs = Join-Path $Root 'workspace\logs'
$log = Join-Path $logs 'setup_trainer.log'
function Say($m) {
    $line = "[trainer-torchfix] " + $m
    Write-Output $line
    Add-Content -Path $log -Value $line
}

Say 'current torch:'
$before = (& $vpy -c "import torch;print(torch.__version__, torch.version.cuda)") -join ' '
Say ("  " + $before)

Say 'uninstalling CPU-only torch'
& $vpy -m pip uninstall -y torch torchvision torchaudio

Say 'installing CUDA build from pytorch cu130 index (this is ~2.5GB)'
& $vpy -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu130

$after = (& $vpy -c "import torch;print(torch.__version__, torch.version.cuda, ','.join(torch.cuda.get_arch_list()))") -join ' '
Say ("after: " + $after)
if ($after -match 'sm_120') {
    Say 'TORCH-CUDA-OK'
} else {
    Say 'STILL-MISSING-sm_120 - try cu129 or cu128 index manually'
}
Say 'TORCH-FIX-DONE'