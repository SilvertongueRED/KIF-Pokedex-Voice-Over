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
REM Force UTF-8 for Python's stdout/stderr.  Without this, when setup.py's
REM output is captured to setup.log (a redirected pipe, not a console) Python
REM falls back to the legacy cp1252 codepage and CRASHES the first time it
REM prints a non-ASCII glyph (e.g. the download arrow) -> the install aborts
REM before the model downloads and .installed is never written.  This also
REM keeps setup.log / server output clean.  Scoped to this process only.
REM ---------------------------------------------------------------------------
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

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
REM Step 1b: self-heal Git-LFS "stub" weights.
REM
REM A GitHub ZIP/tarball download (what a mod manager uses) does NOT resolve
REM Git-LFS, so model.pth / the firefly decoder can arrive as ~130-byte text
REM pointer stubs instead of the real 1.4 GB of weights.  Loading a stub crashes
REM the engine with "invalid load key, 'v'".  Detect any stub here, delete it,
REM and clear the .installed marker so the first-run setup below re-downloads
REM the real weights from HuggingFace.
REM ---------------------------------------------------------------------------
set "CKPT=checkpoints\fish-speech-1.5"
for %%F in ("%CKPT%\model.pth" "%CKPT%\firefly-gan-vq-fsq-8x1024-21hz-generator.pth") do (
    if exist "%%~F" (
        set "WSIZE=%%~zF"
        if !WSIZE! LSS 1000000 (
            echo [weights] %%~nxF is only !WSIZE! bytes - a Git-LFS stub, re-downloading the real model.
            del /q "%%~F"
            if exist ".installed" del /q ".installed"
        )
    )
)

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
    echo A full transcript of this setup is saved to  fish_speech\setup.log
    echo.
    if defined POKEDEX_VO_PYTHON (
        REM Advanced override interpreter: run directly.
        "%PYCMD%" setup.py
    ) else (
        REM Bundled Python: tee the WHOLE transcript (python prints + pip output)
        REM to setup.log so a first-run failure stays diagnosable after the
        REM window closes.  Relative paths keep PowerShell quoting safe even when
        REM the game path has spaces/parens/apostrophes (e.g. "Kuray's ... (KIF)").
        powershell -NoProfile -ExecutionPolicy Bypass -Command "& '.\python\python.exe' '.\setup.py' 2>&1 | Tee-Object -FilePath '.\setup.log'; exit $LASTEXITCODE"
    )
    if !ERRORLEVEL!==0 (
        echo. > "%INSTALL_MARKER%"
    ) else (
        echo.
        echo Setup failed.  Full transcript:
        echo     %~dp0setup.log
        echo Re-run this script after fixing the error above, or run
        echo     "%PYCMD%" setup.py
        echo manually to see the full output.
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

REM ---------------------------------------------------------------------------
REM torch.compile (~2-3x faster generation on CUDA) is ON BY DEFAULT in
REM server.py.  setup.py installs everything it needs (Triton + Python dev
REM files); the server verifies a real generation and silently falls back to
REM uncompiled if compile cannot work on this machine, so it can NEVER silence
REM the mod.  A machine where compile fails remembers that and skips it next
REM time.  To force it OFF, set POKEDEX_VO_COMPILE=0 or create disable_compile.flag.
REM See COMPILE_GUIDE.md.
REM ---------------------------------------------------------------------------
set "COMPILE_ARGS="
if "%POKEDEX_VO_COMPILE%"=="0" set "COMPILE_ARGS=--no-compile"
if exist "disable_compile.flag" set "COMPILE_ARGS=--no-compile"

"%PYCMD%" server.py %COMPILE_ARGS% %*
set "FISHTTS_EXIT=!ERRORLEVEL!"

if not "!FISHTTS_EXIT!"=="0" (
    echo.
    echo Fish-Speech server exited with code !FISHTTS_EXIT!.
    echo Press any key to close this window.
    pause >nul
)

REM ---------------------------------------------------------------------------
REM Step 4: close this wrapper terminal once the server process has ended.
REM
REM When the game exits, server.py's --parent-pid monitor shuts the Python
REM server down cleanly (exit code 0) and control returns here.  We must NOT
REM rely on `start "FishSpeechTTS" Start_TTS_Server.bat` auto-closing its own
REM window: depending on the Windows shell / .bat file-association config, the
REM hosting cmd.exe can drop to an idle "C:\...>" prompt after the batch
REM finishes instead of terminating, which is exactly what leaves an orphaned
REM "FishSpeechTTS" terminal onscreen long after the game (and the server) are
REM already gone.  A bare `exit` terminates this cmd.exe outright, closing the
REM window in every case.  On the error path above we `pause` first so the user
REM can read the message, THEN fall through here to close.
REM ---------------------------------------------------------------------------
endlocal & exit
