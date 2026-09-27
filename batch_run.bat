@echo off
REM ============================================================
REM  AnimaStudio - unattended end-to-end run (ASCII only)
REM  story -> plan -> draft render -> QC -> review -> final -> page
REM  Example:
REM    batch_run.bat --story "D:\AnimaStudio\workspace\jobs\story.txt" --panels 8
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

echo [AnimaStudio] unattended run starting...
"%PY%" "%ROOT%\pipeline\orchestrate.py" %*
echo [AnimaStudio] exit code: %ERRORLEVEL%
endlocal