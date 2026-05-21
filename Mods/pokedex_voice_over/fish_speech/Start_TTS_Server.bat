@echo off
REM ============================================================================
REM Start the Fish-Speech TTS sidecar for the KIF Pokedex Voice Over mod.
REM
REM Plug-and-play first-run flow:
REM   1. If fish_speech\python\python.exe (the bundled embeddable Python)
REM      doesn't exist yet, invoke install_python.bat which extracts it from
REM      the bundled embeddable ZIP (or downloads + extracts if the ZIP isn't
REM      bundled).  This NEVER runs an installer EXE, so SmartScreen stays
REM      out of the user's way.
REM   2. If the fish-speech Python deps + model are not yet installed, this
REM      script invokes setup.py to install them and download the model.
REM   3. Then it starts server.py.
REM
REM Subsequent runs detect the .installed marker and skip straight to the
REM server.  The window title is "FishSpeechTTS" so the mod can find and
REM terminate this terminal automatically when the game exits.
REM ============================================================================

setlocal EnableDelayedExpansion
title FishSpeechTTS
cd /d "%~dp0"

REM ---------------------------------------------------------------------------
REM Step 1: locate Python.
REM
REM Hard preference for the *bundled* embeddable interpreter at
REM   fish_speech\python\python.exe
REM This isolates the mod's Python from anything the user has system-wide,
REM so a half-broken global pip or Anaconda env can't break the mod, and
REM the mod can't pollute the user's environment.
REM
REM POKEDEX_VO_PYTHON can be set as an override (advanced users only).
REM ---------------------------------------------------------------------------
if defined POKEDEX_VO_PYTHON (
    set "PYCMD=%POKEDEX_VO_PYTHON%"
    goto :have_python
)

REM Use relative paths so apostrophes/parens in the game's install path don't
REM confuse CMD's block parser (known issue with paths like "Kuray's (KIF)").
REM The cd /d "%~dp0" above makes the CWD this bat file's folder.
set "PYCMD=python\python.exe"
if exist "%PYCMD%" goto :have_python

echo.
echo Setting up the bundled Python (one-time, no admin required^)...
echo.
call "install_python.bat"
if !ERRORLEVEL! neq 0 (
    echo.
    echo ERROR: Bundled Python install failed.  See output above.
    echo.
    pause
    exit /b 1
)

set "PYCMD=python\python.exe"
if not exist "%PYCMD%" (
    echo ERROR: install_python.bat reported success but python.exe is missing.
    pause
    exit /b 1
)

:have_python

REM ---------------------------------------------------------------------------
REM Step 2: first-run install of torch + fish-speech + model weights.
REM
REM We invoke setup.py with the bundled Python so all wheels land in the
REM bundled interpreter's site-packages and never touch the user's system
REM Python.  Uninstall = delete this folder.
REM ---------------------------------------------------------------------------
set "INSTALL_MARKER=.installed"

if not exist "%INSTALL_MARKER%" (
    echo.
    echo === First-run setup ===
    echo Installing Fish-Speech and downloading the voice-clone model.
    echo This is a one-time step.  Re-runs skip straight to the server.
    echo The model weights are ~1.4 GB - the download takes a few minutes.
    echo.
    "%PYCMD%" setup.py
    if !ERRORLEVEL!==0 (
        echo. > "%INSTALL_MARKER%"
    ) else (
        echo.
        echo Setup failed.  Re-run this script after fixing the error above,
        echo or run  "%PYCMD%" setup.py  manually to see the full output.
        echo.
        pause
        exit /b 1
    )
)

REM ---------------------------------------------------------------------------
REM Step 3: launch the TTS server.
REM ---------------------------------------------------------------------------
echo.
echo Starting Fish-Speech TTS server on http://127.0.0.1:7861 ...
echo The HTTP socket opens immediately; the model loads in the background
echo and takes ~5-15 seconds (longer on the very first launch with --compile).
echo Leave this window open while you play.  Press Ctrl+C to stop.
echo.

"%PYCMD%" server.py %*
set "FISHTTS_EXIT=!ERRORLEVEL!"

if not "!FISHTTS_EXIT!"=="0" (
    echo.
    echo Fish-Speech server exited with code !FISHTTS_EXIT!.
    echo Press any key to close this window.
    pause >nul
)
endlocal
