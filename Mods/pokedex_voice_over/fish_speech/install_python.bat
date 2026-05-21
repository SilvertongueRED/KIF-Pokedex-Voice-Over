@echo off
REM ============================================================================
REM install_python.bat - extract the official Python *embeddable* ZIP into
REM                      fish_speech\python\ and bootstrap pip there.
REM
REM This is intentionally **not** the python.org installer EXE.  The
REM embeddable distribution is just a ZIP of files; extracting it and running
REM the bundled python.exe never triggers SmartScreen.  No admin prompt, no
REM PATH changes, no registry edits - Python lives entirely inside the mod
REM folder and is uninstalled by deleting the mod folder.
REM
REM Source order:
REM   1. fish_speech\_embed\python-embed.zip    (bundled in the release zip)
REM   2. fish_speech\_embed\get-pip.py          (bundled in the release zip)
REM If either is missing, PowerShell downloads the canonical copy from
REM python.org / pypa.io and strips Mark-of-the-Web before extraction.
REM ============================================================================

setlocal EnableDelayedExpansion
cd /d "%~dp0"

REM --- Versions / URLs (one place to bump) ----------------------------------
set "PY_VERSION=3.12.7"
set "PY_EMBED_ZIP=python-%PY_VERSION%-embed-amd64.zip"
set "PY_EMBED_URL=https://www.python.org/ftp/python/%PY_VERSION%/%PY_EMBED_ZIP%"
set "GETPIP_URL=https://bootstrap.pypa.io/get-pip.py"

REM Use relative paths so apostrophes/parens in the game's install path don't
REM confuse CMD's block parser (known issue with paths like "Kuray's (KIF)").
REM The cd /d "%~dp0" above makes the CWD this bat file's folder, so relative
REM paths work correctly from here on.
set "EMBED_DIR=_embed"
set "EMBED_ZIP=%EMBED_DIR%\%PY_EMBED_ZIP%"
set "GETPIP=%EMBED_DIR%\get-pip.py"
set "PY_DIR=python"
set "PY_EXE=%PY_DIR%\python.exe"

REM --- Already extracted?  Just confirm pip works and exit. -----------------
if exist "%PY_EXE%" (
    "%PY_EXE%" -m pip --version >nul 2>nul
    if !ERRORLEVEL!==0 (
        echo Bundled Python already installed at %PY_DIR%
        exit /b 0
    )
)

if not exist "%EMBED_DIR%" mkdir "%EMBED_DIR%"

REM --- 1. Get the embeddable ZIP --------------------------------------------
if not exist "%EMBED_ZIP%" (
    echo.
    echo Downloading Python %PY_VERSION% embeddable distribution from python.org...
    echo   %PY_EMBED_URL%
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri '%PY_EMBED_URL%' -OutFile '%EMBED_ZIP%'; Unblock-File '%EMBED_ZIP%'"
    if not exist "%EMBED_ZIP%" (
        echo ERROR: Failed to download the Python embeddable ZIP.
        echo If your network blocks python.org, download
        echo   %PY_EMBED_URL%
        echo manually and place it at:
        echo   %EMBED_ZIP%
        exit /b 2
    )
) else (
    echo Using bundled Python embeddable ZIP at %EMBED_ZIP%
)

REM --- 2. Get get-pip.py ----------------------------------------------------
if not exist "%GETPIP%" (
    echo Downloading get-pip.py from pypa.io...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri '%GETPIP_URL%' -OutFile '%GETPIP%'; Unblock-File '%GETPIP%'"
    if not exist "%GETPIP%" (
        echo ERROR: Failed to download get-pip.py.
        exit /b 2
    )
)

REM --- 3. Extract embeddable ZIP into python\ -------------------------------
if exist "%PY_DIR%" rmdir /s /q "%PY_DIR%"
mkdir "%PY_DIR%"
echo Extracting embeddable distribution to %PY_DIR%
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "Expand-Archive -LiteralPath '%EMBED_ZIP%' -DestinationPath '%PY_DIR%' -Force"

if not exist "%PY_EXE%" (
    echo ERROR: extraction did not produce python.exe at %PY_EXE%
    exit /b 3
)

REM --- 4. Enable site-packages in the embeddable distribution ---------------
REM
REM The embeddable distribution ships with a python312._pth file that
REM hardcodes the module search path and disables site-packages by default
REM (the line "#import site" is commented out).  Uncomment it so pip-
REM installed packages are picked up.
echo Enabling site-packages in python312._pth
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$pth = Get-ChildItem -Path '%PY_DIR%' -Filter 'python*._pth' | Select-Object -First 1; if ($pth) { (Get-Content $pth.FullName) -replace '^#import site','import site' | Set-Content $pth.FullName }"

REM --- 5. Bootstrap pip in the embedded interpreter -------------------------
echo Bootstrapping pip...
"%PY_EXE%" "%GETPIP%" --no-warn-script-location

"%PY_EXE%" -m pip --version
if !ERRORLEVEL!==0 (
    echo.
    echo === Embeddable Python ready at %PY_DIR% ===
    "%PY_EXE%" --version
    exit /b 0
)

echo ERROR: pip did not install correctly.  See output above.
exit /b 4
