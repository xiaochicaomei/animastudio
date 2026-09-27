param(
    [string]$Root = 'D:\AnimaStudio'
)
# ============================================================================
#  AnimaStudio - stop the local agent LLM so the GPU goes back to the renderer.
#
#  llama.cpp's server has no unload endpoint: once loaded, the weights sit in VRAM
#  for the life of the process. Handing the GPU back therefore means ending it.
#  Restarting costs ~10s while the GGUF is still in the page cache, which is small
#  next to a render stage - see pipeline\orchestrate.py.
#
#  Only servers started from this studio's own tools\llamacpp* directories are
#  touched. A llama-server running from anywhere else is left alone.
# ============================================================================
$mine = @(
    (Join-Path $Root 'tools\llamacpp'),
    (Join-Path $Root 'tools\llamacpp-cpu')
)

$stopped = 0
foreach ($p in Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" -ErrorAction SilentlyContinue) {
    $exe = $p.ExecutablePath
    if (-not $exe) { continue }
    foreach ($dir in $mine) {
        if ($exe -like "$dir\*") {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            $stopped++
            break
        }
    }
}
Write-Output ("agent stop: " + $stopped + " process(es) stopped")
