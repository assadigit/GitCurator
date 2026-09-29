@echo off
REM ============================================================
REM  GitCurator-TEST-CONNECTION.bat - one double-click answer to
REM  "is everything up and ready?"
REM
REM  The CLI twin of the app's Test Connection button: vaults
REM  (found + writable), LLM (Ollama / llama.cpp / API), GitHub
REM  (token + backup repos), Telegram (bot + account login, LIVE).
REM  Exit code 0 = no errors found (warnings are fine).
REM  v0.17.0: prefers the private .venv created by 1-INSTALL.bat.
REM ============================================================
setlocal
title GitCurator - Test Connection
cd /d "%~dp0"

REM This file is PURE ASCII and never changes the codepage
REM (see GitCurator-CLI.bat for the v0.09.1 lesson).

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
%PY% main.py --cli --test-connection
set EXITCODE=%ERRORLEVEL%

echo.
if "%EXITCODE%"=="0" (
    echo  [GitCurator] Everything checked - no errors found.
    echo                Warnings above are things to look at, not blockers.
) else (
    echo  [GitCurator] Test Connection found problems ^(code %EXITCODE%^).
    echo                Read the lines with the red X above - each one
    echo                says what to fix. In the app, the same results
    echo                appear in the log when you click Test Connection.
)
echo.
pause
endlocal
