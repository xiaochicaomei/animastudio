@echo off
REM ============================================================
REM  AnimaStudio - local agent LLM launcher (llama.cpp)
REM
REM  Delegates to tools\start_agent_headless.ps1, which:
REM    * picks the CUDA build when the card has room, otherwise the
REM      CPU build. The CUDA build burns ~1.1GB of VRAM even at
REM      -ngl 0, so it is NOT a valid CPU fallback - a low-VRAM
REM      situation has to switch binaries, not just the layer count
REM    * verifies the server actually came up
REM    * falls back to the CPU build once if the GPU start failed
REM
REM  Options are that script's own, e.g.:
REM    start_agent.bat -Ngl 0              force the CPU build
REM    start_agent.bat -Ngl 28             partial offload
REM    start_agent.bat -Extra --no-mmap    pass flags to llama-server
REM  ASCII only on purpose
REM ============================================================
setlocal
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"

if not exist "%ROOT%\tools\start_agent_headless.ps1" (
    echo [ERROR] launcher not found: "%ROOT%\tools\start_agent_headless.ps1"
    pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\tools\start_agent_headless.ps1" %*
echo [AnimaStudio] exit code: %ERRORLEVEL%
endlocal
