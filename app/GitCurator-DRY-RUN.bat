@echo off
REM ============================================================
REM  GitCurator-DRY-RUN.bat - the SAFE first run.
REM  The same fully-automatic run as GitCurator-CLI.bat, but it
REM  writes NOTHING: no notes, no vault changes, no backup pushes,
REM  no queue consumption. Everything it WOULD have done is
REM  printed so you can read the plan first.
REM
REM  Needs config first: double-click GitCurator-CLI-Setup.bat
REM  (or configure in the app: Settings).
REM ============================================================
setlocal
title GitCurator - dry run (writes nothing)
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
%PY% main.py --cli --auto --yes --dry-run
echo.
echo  [GitCurator] Dry run finished - nothing was written. Read the plan
echo                above; when you are happy with it, the real run is
echo                GitCurator-CLI.bat.
echo.
pause
endlocal
