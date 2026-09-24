@echo off
REM ============================================================
REM  GitCurator CLI - one-click launcher (v0.09, merged lineage)
REM  Double-click this file. It:
REM    1. uses the config.json sitting NEXT TO this file,
REM    2. makes sure Python exists,
REM    3. runs the full pipeline automatically:
REM       pre-flight checks -> bot queue -> process -> Obsidian
REM       notes -> seal -> publish.
REM  (Kept from v0.08 for muscle memory — it now drives the SAME
REM   zero-dependency engine as GitCurator-CLI.bat: main.py --cli.)
REM ============================================================
setlocal
title GitCurator CLI
cd /d "%~dp0"

REM v0.09.1: UTF-8 codepage — the banner's block glyphs crash Python's
REM print() on legacy cp437/cp1252 consoles (UnicodeEncodeError killed the
REM CLI before any command ran). 65001 + the in-app reconfigure = safe.
chcp 65001 >nul

REM ---- find a Python (python.exe, then the py launcher) ----
set "PY=python"
where python >nul 2>nul
if errorlevel 1 (
    set "PY=py -3"
)
%PY% --version >nul 2>nul
if errorlevel 1 (
    echo.
    echo [ERROR] Python was not found on PATH.
    echo Install Python 3.10+ from https://www.python.org/downloads/
    echo ^(tick "Add python.exe to PATH" during install^)
    echo.
    pause
    exit /b 1
)

REM ---- run (v0.09: no extra packages needed - plain ANSI) ----
%PY% main.py --cli --auto --yes %*

echo.
pause
