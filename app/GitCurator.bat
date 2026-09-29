@echo off
REM ============================================================
REM  GitCurator.bat - double-click to start the desktop app.
REM  (Run 1-INSTALL.bat once first.)
REM
REM  A console window opens BEHIND the app - that is normal; it
REM  shows the app's log lines. If something breaks, screenshot
REM  that window: it has the exact error.
REM ============================================================
setlocal
title GitCurator
cd /d "%~dp0"

REM v0.14.1: PURE ASCII, never changes the codepage (v0.09.1 lesson).

REM ---- find Python: the app's private .venv first, then system ----
if exist ".venv\Scripts\python.exe" goto :run
set "PY=python"
where python >nul 2>nul
if errorlevel 1 (
    set "PY=py -3"
)
%PY% --version >nul 2>nul
if errorlevel 1 (
    echo.
    echo  [GitCurator] Python was not found.
    echo                Double-click 1-INSTALL.bat first - it sets up
    echo                everything ^(and tells you what to do if Python
    echo                is missing^).
    echo.
    pause
    exit /b 1
)

:run
%PY% main.py
if errorlevel 1 (
    echo.
    echo  [GitCurator] The app exited with an error - the message is above.
    echo                Screenshot this window if you need help with it.
    pause
)
endlocal
