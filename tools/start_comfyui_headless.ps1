param([string]$Root = 'D:\AnimaStudio')
$env:HF_HOME = Join-Path $Root 'env\hf'
$env:HF_ENDPOINT = 'https://hf-mirror.com'
$env:TORCH_HOME = Join-Path $Root 'env\torch'
$env:PIP_CONFIG_FILE = Join-Path $Root 'env\pip.ini'
$env:PATH = (Join-Path $Root 'tools\MinGit\cmd') + ';' + $env:PATH

$py = Join-Path $Root 'ComfyUI_windows_portable\python_embeded\python.exe'
$logs = Join-Path $Root 'workspace\logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$log = Join-Path $logs 'comfyui.log'

$cargs = @(
    '-s', 'ComfyUI\main.py',
    '--windows-standalone-build',
    '--listen', '127.0.0.1',
    '--port', '8188',
    '--preview-method', 'none',
    '--output-directory', (Join-Path $Root 'workspace\out\raw'),
    # Memory management, which this launcher had none of. The card is the binding
    # constraint on every render - Anima-2.9B alone peaks at ~7.0 GiB of 7.96, which is
    # why a hires refine pass on top of it never fit. ComfyUI's current memory stack can
    # stream weights instead of keeping a model resident, and it is NOT on by default here:
    # --lowvram is a documented no-op under dynamic VRAM, so these are the flags that
    # still do something.
    '--enable-dynamic-vram',
    '--async-offload',
    '--vram-headroom', '0.5'
)

$p = Start-Process -FilePath $py -ArgumentList $cargs -WorkingDirectory (Join-Path $Root 'ComfyUI_windows_portable') -RedirectStandardOutput $log -RedirectStandardError ($log + '.err') -WindowStyle Hidden -PassThru
Write-Output ("comfyui pid=" + $p.Id + " log=" + $log)