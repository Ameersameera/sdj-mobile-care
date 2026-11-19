@echo off
REM Run SDJ Mobile Care from source (after build_windows.bat or "pip install -r requirements.txt")
cd /d "%~dp0"
if exist .venv\Scripts\pythonw.exe (
  start "" .venv\Scripts\pythonw.exe main.py %*
) else (
  start "" pythonw main.py %*
)
