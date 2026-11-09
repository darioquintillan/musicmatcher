@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    where py >nul 2>&1
    if errorlevel 1 (
        python -m venv .venv
    ) else (
        py -3 -m venv .venv
    )
    if errorlevel 1 goto setup_error
)
if not exist ".venv\Scripts\musicmatcher.exe" (
    ".venv\Scripts\python.exe" -m pip install -e .
    if errorlevel 1 goto setup_error
)
".venv\Scripts\python.exe" -m musicmatcher web
if errorlevel 1 pause
exit /b
:setup_error
echo Install Python 3.11 or newer from https://www.python.org/downloads/
echo Then try again. Details are in README.es.md.
pause
exit /b 1
