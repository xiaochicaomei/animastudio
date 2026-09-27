@echo off
REM ============================================================
REM  AnimaStudio - style LoRA training loop (ASCII only)
REM  Runs style_train.py with the embedded python (it has numpy /
REM  Pillow / onnxruntime). The trainer venv is used only for the
REM  actual training subprocess.
REM  Example:
REM    run_style_train.bat run --name orig_student --steps 600
REM ============================================================
setlocal
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "PY=%ROOT%\ComfyUI_windows_portable\python_embeded\python.exe"

if not exist "%PY%" (
    echo [ERROR] portable python not found: "%PY%"
    pause
    exit /b 1
)

"%PY%" "%ROOT%\pipeline\style_train.py" %*
echo [AnimaStudio] exit code: %ERRORLEVEL%
endlocal