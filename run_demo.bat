@echo off
REM Open SDJ Mobile Care with the SAMPLE data (kept separate from real shop data).
REM Demo logins:  admin / admin123   and   staff / staff123
cd /d "%~dp0"
call run_app.bat --demo
