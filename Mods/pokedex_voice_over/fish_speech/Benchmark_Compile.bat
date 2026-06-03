@echo off
REM ===========================================================================
REM Benchmark torch.compile + quantization WITHOUT launching the game.
REM Runs the server's --self-test (loads model, times a few generations, exits)
REM so you can see what engages on THIS machine and how much faster it is.
REM Run a clean reinstall FIRST (see COMPILE_GUIDE.md) so Triton + the Python
REM headers are in place, otherwise inductor will just fall back.
REM
REM Each run prints a line like:
REM   SELF-TEST PASS: device=cuda compiled=True quant=int8 mode=reduce-overhead avg=0.42s/sentence
REM Compare the "avg ... s/sentence" numbers across the runs below.
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
echo  1/3  BASELINE  (uncompiled - slowest)
echo ===========================================================
"%PYCMD%" server.py --no-compile --self-test

echo.
echo ===========================================================
echo  2/3  INDUCTOR, mode=default  (the standard 2-3x; compiles
echo       once, ~30-90s first gen)
echo ===========================================================
set "POKEDEX_VO_COMPILE_MODE=default"
"%PYCMD%" server.py --compile --compile-backend inductor --self-test

echo.
echo ===========================================================
echo  3/3  INDUCTOR, mode=reduce-overhead  (adds CUDA graphs;
echo       often a further 10-30%% on the tiny per-token decode)
echo ===========================================================
set "POKEDEX_VO_COMPILE_MODE=reduce-overhead"
"%PYCMD%" server.py --compile --compile-backend inductor --self-test
set "POKEDEX_VO_COMPILE_MODE="

echo.
echo ===========================================================
echo  Compare the "avg ... s/sentence" lines.
echo    - WANT: runs 2/3 show compiled=True and a LOWER avg than baseline.
echo    - If reduce-overhead (run 3) is faster AND still sounds correct, make
echo      it the default in play with:  set POKEDEX_VO_COMPILE_MODE=reduce-overhead
echo      (e.g. in Start_TTS_Server.bat). It is left at "default" out of the box
echo      because CUDA graphs can be finicky on some setups.
echo    - The "quant=" field shows whether a quantized model was used. To A/B
echo      fp16 vs int8, build int8 with Quantize_Model.bat and re-run; add
echo      --quant none to any line above to force fp16 for comparison.
echo    - If a run shows compiled=False, the line just above prints WHY (usually
echo      missing Python headers/libs - re-run setup.py, which bundles them).
echo ===========================================================
pause
