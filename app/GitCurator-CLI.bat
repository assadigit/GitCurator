@echo off
REM ============================================================
REM  GitCurator-CLI.bat - one double-click, fully automatic run
REM  (bot SYNC -> PROCESS -> VaultSeal -> Good Repos)
REM
REM  First time? Run GitCurator-CLI-Setup.bat once to save your
REM  credentials locally, then use this file for every run.
REM  v0.14.1: prefers the private .venv created by 1-INSTALL.bat.
REM ============================================================
setlocal
title GitCurator CLI - automatic run
cd /d "%~dp0"

REM v0.09.2: this file is PURE ASCII and never changes the codepage.
REM v0.09.1 ran "chcp 65001" here; a codepage switch INSIDE a batch
REM file makes cmd.exe re-parse the file at a shifted byte offset, so
REM fragments of the REM header ran as commands ("'GitCurator' is not
REM recognized" error wall). Python handles console encoding itself at
REM startup (cli.py: SetConsoleOutputCP(65001) + UTF-8 std streams),
REM so the chcp was redundant anyway. ASCII-only, codepage-free.

REM ---- find a Python: the app's private .venv first, then system ----
if exist ".venv\Scripts\python.exe" goto :run
set "PY=python"
where python >nul 2>nul
if errorlevel 1 (
    set "PY=py -3"
)
%PY% --version >nul 2>nul
if errorlevel 1 (
    echo.
    echo  [GitCurator] Python was not found on PATH.
    echo                Double-click 1-INSTALL.bat first - it sets up
    echo                everything ^(and tells you what to do if Python
    echo                is missing^).
    echo.
    pause
    exit /b 1
)

:run
echo.
%PY% main.py --cli --auto --yes
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
