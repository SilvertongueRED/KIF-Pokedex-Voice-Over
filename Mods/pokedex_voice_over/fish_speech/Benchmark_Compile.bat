@echo off
REM ===========================================================================
REM Benchmark torch.compile WITHOUT launching the game.
REM Runs the server's --self-test (loads model, times a few generations, exits)
REM so you can see whether compile engages on THIS machine and how much faster
REM it is.  Run a clean reinstall FIRST (see COMPILE_GUIDE.md) so Triton + the
REM Python headers are in place, otherwise inductor will just fall back.
REM ===========================================================================
cd /d "%~dp0"
set "PYCMD=python\python.exe"
if defined POKEDEX_VO_PYTHON set "PYCMD=%POKEDEX_VO_PYTHON%"
if not exist "%PYCMD%" (
    echo ERROR: bundled Python not found at "%PYCMD%".
    echo Run Start_TTS_Server.bat once first to install it.
    pause
    exit /b 1
)

echo ===========================================================
echo  1/2  BASELINE  (uncompiled - today's behavior)
echo ===========================================================
"%PYCMD%" server.py --no-compile --self-test

echo.
echo ===========================================================
echo  2/2  INDUCTOR  (the real 2-3x; compiles once, ~30-90s first gen)
echo ===========================================================
"%PYCMD%" server.py --compile --compile-backend inductor --self-test

echo.
echo ===========================================================
echo  Compare the "avg ... s/sentence" lines.
echo  WANT: the INDUCTOR run shows  compiled=True  and a LOWER avg than baseline.
echo  Compile is already ON BY DEFAULT in normal play - this is just to confirm
echo  it works and measure the gain; you do NOT need to enable anything.
echo    - If it shows compiled=False, the line just above prints WHY. The usual
echo      cause is missing Python headers/libs - re-run the install (setup.py
echo      now bundles + installs them automatically). Send me that line if stuck.
echo  (To force compile OFF in normal play: set POKEDEX_VO_COMPILE=0 or create a
echo   disable_compile.flag file. cudagraphs is intentionally not tested - on
echo   fish-speech it recompiles every call and is slower than baseline.)
echo ===========================================================
pause
