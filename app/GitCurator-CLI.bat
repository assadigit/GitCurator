@echo off
REM ============================================================
REM  GitCurator-CLI.bat — one double-click, fully automatic run
REM  (bot SYNC -> PROCESS -> VaultSeal -> Good Repos)
REM
REM  First time? Run GitCurator-CLI-Setup.bat once to save your
REM  credentials locally, then use this file for every run.
REM ============================================================
setlocal
title GitCurator CLI - automatic run
cd /d "%~dp0"

REM v0.09.1: UTF-8 codepage — the banner's block glyphs crash Python's
REM print() on legacy cp437/cp1252 consoles (UnicodeEncodeError killed the
REM CLI before any command ran). 65001 + the in-app reconfigure = safe.
chcp 65001 >nul

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo  [GitCurator] Python was not found on PATH.
    echo                Install Python 3.10+ from https://www.python.org/downloads/
    echo                (check "Add python.exe to PATH" during install), then re-run.
    echo.
    pause
    exit /b 1
)

echo.
python main.py --cli --auto --yes
set EXITCODE=%ERRORLEVEL%

echo.
if "%EXITCODE%"=="0" (
    echo  [GitCurator] Finished. You can close this window.
) else (
    echo  [GitCurator] Exited with code %EXITCODE%.
    echo                If credentials are missing, run GitCurator-CLI-Setup.bat first.
)
echo.
pause
endlocal
