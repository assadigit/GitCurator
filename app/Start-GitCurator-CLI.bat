@echo off
REM ============================================================
REM  GitCurator CLI - one-click launcher (v0.09, merged lineage)
REM  Double-click this file. It:
REM    1. uses the config.json sitting NEXT TO this file,
REM    2. makes sure Python exists,
REM    3. runs the full pipeline automatically:
REM       pre-flight checks -> bot queue -> process -> Obsidian
REM       notes -> seal -> publish.
REM  (Kept from v0.08 for muscle memory - it now drives the SAME
REM   zero-dependency engine as GitCurator-CLI.bat: main.py --cli.)
REM ============================================================
setlocal
title GitCurator CLI
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
