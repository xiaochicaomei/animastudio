param(
    [string]$Root = 'D:\AnimaStudio',
    [string]$Model = '',
    [string]$Mmproj = '',
    [int]$Ctx = 6144,
    [int]$Threads = 16,
    [string[]]$Extra = @(),
    [string]$Draft = '',
    [string]$Ngl = 'auto'
)
# ============================================================================
#  AnimaStudio - local agent LLM launcher (llama.cpp)
#
#  There is only one brain: the 9B. -Model still overrides the path if you ever
#  drop a different GGUF in tools\gguf.
#
#  TWO BUILDS SIT SIDE BY SIDE, and which one runs is not cosmetic:
#     tools\llamacpp       CUDA build      - runs the model on the GPU
#     tools\llamacpp-cpu   no CUDA runtime - runs on the CPU, uses ZERO VRAM
#
#  The CUDA build reserves about 1.1 GiB of CUDA context and compute buffers even
#  at -ngl 0 (measured on this machine). It is therefore NOT a drop-in CPU
#  fallback: at zero layers it would still take VRAM that the renderer needs. A
#  low-VRAM situation switches BINARIES, not just the layer count.
#
#  Measured here (9B Q4_K_M + vision projector, 6144 ctx, RTX 5070 Laptop 7.96 GiB):
#     GPU -ngl 99     41.9 tok/s decode   ~6.3 GiB VRAM
#     CPU -ngl 0       4.8 tok/s decode    0 GiB VRAM
#
#  Anima-2.9B peaks at ~7.0 GiB to render, so the model and the renderer can never
#  hold the card at the same time. pipeline\orchestrate.py enforces that at stage
#  boundaries: it frees ComfyUI before starting this, and stops this before a
#  render stage.
# ============================================================================
$CudaExe = Join-Path $Root 'tools\llamacpp\llama-server.exe'
$CpuExe = Join-Path $Root 'tools\llamacpp-cpu\llama-server.exe'
$mdl = if ($Model) { $Model } else { Join-Path $Root 'tools\gguf\Qwen3.5-9B-Q4_K_M.gguf' }
$vision = if ($Mmproj) { $Mmproj } else { Join-Path $Root 'tools\gguf\mmproj-F16.gguf' }
$logs = Join-Path $Root 'workspace\logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$log = Join-Path $logs 'agent.log'

# What the fully offloaded 9B + projector actually occupy, plus a little headroom for
# the KV cache and compute buffers. Below this the CPU build is the honest answer.
$FullOffloadMiB = 6500

if (-not (Test-Path $mdl)) {
    Write-Output ("model not found: " + $mdl)
    exit 1
}

function Get-FreeVramMiB {
    # 0 doubles as "no usable GPU reading" and as the safe answer: it selects the CPU
    # build, which cannot fail for want of VRAM.
    try {
        $v = & nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>$null |
             Select-Object -First 1
        if (-not $v) { return 0 }
        return [int]$v
    } catch { return 0 }
}

# ----------------------------------------------------------------- pick a build
$free = Get-FreeVramMiB
if ($Ngl -eq 'auto') {
    if ($free -ge $FullOffloadMiB) {
        $exe = $CudaExe; $ngl = 99
        $why = "auto: ${free}MiB free >= ${FullOffloadMiB}MiB"
    } else {
        $exe = $CpuExe; $ngl = 0
        $why = "auto: ${free}MiB free < ${FullOffloadMiB}MiB"
    }
} else {
    # An explicit layer count only chooses between builds; it never forces the CUDA
    # build to run at 0, which would cost VRAM for no speed at all.
    $ngl = [int]$Ngl
    if ($ngl -gt 0) { $exe = $CudaExe } else { $exe = $CpuExe }
    $why = "explicit -Ngl $Ngl"
}
if (-not (Test-Path $exe)) {
    # Never leave the studio without a brain just because one build is missing.
    $alt = if ($exe -eq $CudaExe) { $CpuExe } else { $CudaExe }
    if (Test-Path $alt) {
        Write-Output ("build not found, falling back: " + $exe)
        $exe = $alt
        $ngl = if ($exe -eq $CudaExe) { 99 } else { 0 }
    } else {
        Write-Output ("llama-server not found: " + $exe)
        exit 1
    }
}

# ----------------------------------------------------------------- optional parts
# The vision projector is what lets the judge look at a rendered panel instead of
# guessing from a tag list, so it is attached whenever present.
$visionOn = $false
if (Test-Path $vision) { $visionOn = $true }

# Speculative decoding: a 0.8B same-family draft proposes tokens and the 9B verifies
# them in one batched pass.
#
# OFF BY DEFAULT, and that is deliberate. Measured on CPU: decode 4.52 -> 7.01 tok/s
# (1.55x, draft acceptance 0.68-0.74), but it costs ~0.96 GB on a 15.8 GB machine,
# which pushed the page file to 9.5 GB and once killed the server silently. On the GPU
# it is a worse trade still: decode is already 41.9 tok/s and the card is left with
# only a few hundred MiB, so a second model would not fit at all. Turn it on with
# -Draft auto only when the agent runs on CPU and nothing else needs the RAM.
$draftOn = $false
$draftPath = if ($Draft -eq 'auto') {
    Join-Path $Root 'tools\gguf\Qwen3.5-0.8B-Q8_0.gguf'
} else { $Draft }
if ($draftPath -and (Test-Path $draftPath)) { $draftOn = $true }

# ----------------------------------------------------------------- start + verify
function Start-Brain([string]$exePath, [int]$layers, [string]$logPath) {
    $cargs = @(
        '-m', $mdl,
        '-ngl', "$layers",
        '-t', "$Threads",
        '-c', "$Ctx",
        '-fa', 'on',
        '--jinja',
        '--reasoning-budget', '1024',  # cap a thinking block; 0 would end it instantly
                                       # and silently disable the optional --think mode.
                                       # Inactive unless a request enables thinking.
        '--host', '127.0.0.1',
        '--port', '8080'
    )
    if ($visionOn) { $cargs += @('--mmproj', $vision) }
    # -Extra passes anything else straight through, e.g. -Extra '--no-mmap'.
    if ($Extra.Count -gt 0) { $cargs += $Extra }
    if ($draftOn) {
        $cargs += @('-md', $draftPath, '--spec-type', 'draft-simple', '--spec-draft-n-max', '4')
    }
    return Start-Process -FilePath $exePath -ArgumentList $cargs -WorkingDirectory $Root `
        -RedirectStandardOutput $logPath -RedirectStandardError ($logPath + '.err') `
        -WindowStyle Hidden -PassThru
}

function Wait-Brain($proc, [int]$seconds) {
    for ($i = 0; $i -lt $seconds; $i++) {
        Start-Sleep -Seconds 1
        if ($proc.HasExited) { return $false }
        try {
            $r = Invoke-RestMethod 'http://127.0.0.1:8080/v1/models' -TimeoutSec 2
            if ($r) { return $true }
        } catch { }
    }
    return $false
}

$launchLog = Join-Path $logs 'agent-launch.log'

function Write-Launch([string]$line) {
    # Written to a file as well as stdout because the caller cannot read this script's
    # stdout: the server started below inherits the handle, so a capturing caller blocks
    # until that server exits (measured). pipeline\orchestrate.py reads the last line of
    # this file back to say in the run log whether the agent is on the GPU or the CPU.
    Write-Output $line
    Add-Content -Path $launchLog -Value ("[" + (Get-Date -Format 'HH:mm:ss') + "] " + $line)
}

Write-Launch ("agent start | " + $why + " | exe=" + (Split-Path (Split-Path $exe -Parent) -Leaf) +
              " ngl=" + $ngl + " vision=" + $visionOn + " draft=" + $draftOn +
              " ctx=" + $Ctx + " threads=" + $Threads)

$activeLog = $log
$p = Start-Brain $exe $ngl $activeLog
$ready = Wait-Brain $p 150

if (-not $ready -and $ngl -gt 0) {
    # The free-VRAM reading is a snapshot: another process can take the memory between
    # the check and the load. Rather than leave the studio with no brain at all, retry
    # once on the CPU build, which cannot fail for want of VRAM.
    Write-Launch "GPU start did not come up - retrying on the CPU build"
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
    $exe = $CpuExe; $ngl = 0
    $activeLog = Join-Path $logs 'agent-cpu.log'
    $p = Start-Brain $exe $ngl $activeLog
    $ready = Wait-Brain $p 180
}

Write-Launch ("agent pid=" + $p.Id + " ready=" + $ready +
              " exe=" + (Split-Path (Split-Path $exe -Parent) -Leaf) + " ngl=" + $ngl +
              " model=" + (Split-Path $mdl -Leaf) + " vision=" + $visionOn +
              " draft=" + $draftOn + " ctx=" + $Ctx + " threads=" + $Threads +
              " log=" + $activeLog)
if (-not $ready) { exit 1 }
