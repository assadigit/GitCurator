@echo off
REM ============================================================
REM  GitCurator-CLI-Setup.bat — FIRST-RUN wizard (run me once)
REM
REM  Asks for your Telegram / GitHub / LLM credentials and saves
REM  them locally to config.json (same file the GUI uses).
REM  After this, double-click GitCurator-CLI.bat for the
REM  fully-automatic run.
REM ============================================================
setlocal
title GitCurator CLI - first-run setup
cd /d "%~dp0"

REM v0.09.1: UTF-8 codepage — the wizard's glyphs crash Python's print()
REM on legacy cp437/cp1252 consoles. 65001 + the in-app reconfigure = safe.
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
python main.py --cli --init
set EXITCODE=%ERRORLEVEL%

echo.
if "%EXITCODE%"=="0" (
    echo  [GitCurator] Setup complete! Next: double-click GitCurator-CLI.bat
    echo                for the fully-automatic run, or run:
    echo                    python main.py --cli --status
) else (
    echo  [GitCurator] Setup exited with code %EXITCODE%.
)
echo.
pause
endlocal
