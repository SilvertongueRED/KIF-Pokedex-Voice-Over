@echo off
REM ============================================================================
REM download_embeddable.bat - pre-populate fish_speech\_embed\ with the
REM                           Python embeddable ZIP + get-pip.py so the next
REM                           released mod zip is fully self-contained
REM                           (no internet needed at first launch for Python).
REM ============================================================================

setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "PY_VERSION=3.12.7"
set "PY_EMBED_ZIP=python-%PY_VERSION%-embed-amd64.zip"
set "PY_EMBED_URL=https://www.python.org/ftp/python/%PY_VERSION%/%PY_EMBED_ZIP%"
set "GETPIP_URL=https://bootstrap.pypa.io/get-pip.py"
set "EMBED_DIR=%~dp0_embed"

if not exist "%EMBED_DIR%" mkdir "%EMBED_DIR%"

echo Fetching %PY_EMBED_URL% ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri '%PY_EMBED_URL%' -OutFile '%EMBED_DIR%\%PY_EMBED_ZIP%'; Unblock-File '%EMBED_DIR%\%PY_EMBED_ZIP%'"

echo Fetching %GETPIP_URL% ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri '%GETPIP_URL%' -OutFile '%EMBED_DIR%\get-pip.py'; Unblock-File '%EMBED_DIR%\get-pip.py'"

echo.
dir /b "%EMBED_DIR%"
echo.
echo Done.  Re-zip the mod and the embeddable Python is now bundled - no
echo network required at first launch for the Python install step.
