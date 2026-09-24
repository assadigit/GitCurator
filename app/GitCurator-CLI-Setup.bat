@echo off
REM ============================================================
REM  GitCurator-CLI-Setup.bat - FIRST-RUN wizard (run me once)
REM
REM  Asks for your Telegram / GitHub / LLM credentials and saves
REM  them locally to config.json (same file the GUI uses).
REM  After this, double-click GitCurator-CLI.bat for the
REM  fully-automatic run.
REM ============================================================
setlocal
title GitCurator CLI - first-run setup
cd /d "%~dp0"

REM v0.09.2: this file is PURE ASCII and never changes the codepage.
REM v0.09.1 ran "chcp 65001" here; a codepage switch INSIDE a batch
REM file makes cmd.exe re-parse the file at a shifted byte offset, so
REM fragments of the REM header ran as commands ("'GitCurator' is not
REM recognized" error wall). Python handles console encoding itself at
REM startup (cli.py: SetConsoleOutputCP(65001) + UTF-8 std streams), so
REM the chcp was redundant anyway. ASCII-only, codepage-free.

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
