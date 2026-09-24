@echo off
REM ============================================================
REM  GitCurator-CLI.bat - one double-click, fully automatic run
REM  (bot SYNC -> PROCESS -> VaultSeal -> Good Repos)
REM
REM  First time? Run GitCurator-CLI-Setup.bat once to save your
REM  credentials locally, then use this file for every run.
REM ============================================================
setlocal
title GitCurator CLI - automatic run
cd /d "%~dp0"

REM v0.09.2: this file is PURE ASCII and never changes the codepage.
REM v0.09.1 ran "chcp 65001" here; a codepage switch INSIDE a batch
REM file makes cmd.exe re-parse the file at a shifted byte offset (the
REM dash characters in the header comments are 3 bytes in UTF-8 but 1
REM in ANSI), so fragments of those REM lines ran as commands - the
REM "'tlocal' / 'Double-click' / 'pre-flight' is not recognized" error
REM wall before the app started. Python handles all console encoding
REM itself at startup (cli.py: SetConsoleOutputCP(65001) + UTF-8 std
REM streams), so the chcp was redundant anyway. Launchers stay
REM ASCII-only and codepage-free.

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
