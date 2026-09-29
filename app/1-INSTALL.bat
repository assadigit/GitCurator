@echo off
REM ============================================================
REM  GitCurator 1-INSTALL.bat - run me ONCE after unzipping.
REM  Creates the app's private Python environment (.venv folder)
REM  and installs everything the app needs. Takes 1-2 minutes and
REM  needs internet. Safe to re-run any time (it only tops up).
REM
REM  After this: double-click GitCurator.bat to start the app.
REM ============================================================
setlocal
title GitCurator - first install
cd /d "%~dp0"

REM v0.14.1: PURE ASCII, never changes the codepage (v0.09.1 lesson).
REM Python sets its own console encoding at startup.

REM ---- find a usable Python 3.10+ (python.exe, then py -3) ----
set "PY="
where python >nul 2>nul
if not errorlevel 1 (
    python -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
        if not errorlevel 1 set "PY=py -3"
    )
)
if not defined PY (
    echo.
    echo  [GitCurator] Python 3.10 or newer was not found on this computer.
    echo.
    echo                1. Install it from https://www.python.org/downloads/
    echo                2. IMPORTANT: tick "Add python.exe to PATH" on the
    echo                   first installer screen
    echo                3. Double-click 1-INSTALL.bat again
    echo.
    pause
    exit /b 1
)

echo.
echo  [GitCurator] Using Python:
%PY% --version
echo.

if exist ".venv\Scripts\python.exe" goto :install
echo  [GitCurator] Creating the private environment (.venv) ...
%PY% -m venv .venv
if errorlevel 1 (
    echo.
    echo  [GitCurator] ERROR: could not create .venv - read the message above.
    pause
    exit /b 1
)

:install
echo  [GitCurator] Installing the app's packages (1-2 minutes) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo  [GitCurator] ERROR: package install failed. Check your internet
    echo                connection, then double-click 1-INSTALL.bat again.
    pause
    exit /b 1
)

echo.
echo  ------------------------------------------------------------
echo  [GitCurator] Install complete.
echo                Next: double-click GitCurator.bat
echo  ------------------------------------------------------------
echo.
pause
endlocal
