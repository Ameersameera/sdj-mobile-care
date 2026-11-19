@echo off
REM ============================================================================
REM  SDJ Mobile Care - build the Windows app (.exe) and installer
REM  Needs: Python 3.10-3.13 64-bit from python.org ("Add python.exe to PATH" ticked)
REM  Optional: Inno Setup 6 (https://jrsoftware.org) for the setup .exe
REM ============================================================================
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.12 from python.org and tick "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist .venv (
  echo Creating virtual environment...
  python -m venv .venv || goto :fail
)
call .venv\Scripts\activate.bat

echo Installing requirements (first time takes a few minutes)...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt || goto :fail

echo Running tests...
set QT_QPA_PLATFORM=offscreen
python -m pytest -q
if errorlevel 1 (
  echo Some tests failed - stopping the build.
  goto :fail
)
set QT_QPA_PLATFORM=

echo Building SDJMobileCare.exe ...
pyinstaller --noconfirm sdj_mobile_care.spec || goto :fail
echo Checking the built app...
start /wait "" dist\SDJMobileCare\SDJMobileCare.exe --self-test
if errorlevel 1 (
  type "%TEMP%\sdj_selftest.txt"
  echo The built app failed its self-test.
  goto :fail
)
type "%TEMP%\sdj_selftest.txt"
echo.
echo App folder ready: dist\SDJMobileCare\SDJMobileCare.exe

set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist %ISCC% (
  echo Building installer...
  %ISCC% installer\sdj_mobile_care.iss || goto :fail
  echo Installer ready in dist\installer
) else (
  echo Inno Setup 6 not found - skipping the installer. The app folder above works on its own.
)
echo.
echo DONE.
pause
exit /b 0

:fail
echo.
echo BUILD FAILED - see the messages above.
pause
exit /b 1
