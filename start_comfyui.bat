@echo off
REM ============================================================
REM  AnimaStudio - ComfyUI launcher
REM  ASCII only on purpose (chcp/UTF8 inside .bat breaks cmd parsing)
REM ============================================================
setlocal
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"

REM --- keep every cache inside the studio folder (delete folder = clean uninstall)
set "HF_HOME=%ROOT%\env\hf"
set "HF_ENDPOINT=https://hf-mirror.com"
set "TORCH_HOME=%ROOT%\env\torch"
set "PIP_CONFIG_FILE=%ROOT%\env\pip.ini"

REM --- portable git for ComfyUI-Manager custom node installs
set "PATH=%ROOT%\tools\MinGit\cmd;%PATH%"

set "PY=%ROOT%\ComfyUI_windows_portable\python_embeded\python.exe"

if not exist "%PY%" (
    echo [ERROR] portable python not found: "%PY%"
    echo         extract ComfyUI_windows_portable_nvidia.7z into "%ROOT%\ComfyUI_windows_portable"
    pause
    exit /b 1
)

cd /d "%ROOT%\ComfyUI_windows_portable"
echo [AnimaStudio] starting ComfyUI on http://127.0.0.1:8188

REM --- Memory flags. They are NOT optional here: an Anima-2.9B render peaks around
REM     7.0 GiB of a 7.96 GiB card on its own, and the hires refine pass on top of it
REM     cannot fit without them. Measured, the same graph OOMs bare and runs with about
REM     1 GiB to spare with them. Note that --lowvram is a documented no-op under
REM     dynamic VRAM, which is why these three are the ones that matter.
REM     Keep in sync with tools\start_comfyui_headless.ps1.
"%PY%" -s ComfyUI\main.py --windows-standalone-build --listen 127.0.0.1 --port 8188 --preview-method none --enable-dynamic-vram --async-offload --vram-headroom 0.5 %*
endlocal