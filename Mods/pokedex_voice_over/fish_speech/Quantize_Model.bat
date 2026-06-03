@echo off
REM ===========================================================================
REM Quantize_Model.bat - build a faster, smaller copy of the voice model.
REM
REM Produces an int8 (default) or int4 quantized copy of the text->semantic
REM model under checkpoints\fish-speech-1.5-int8\ (or -int4-g128-q\).  The
REM server then uses it AUTOMATICALLY on the next launch (--quant auto), and
REM verifies a real generation first - if the quantized model can't run on this
REM machine it silently falls back to the fp16 model, so this can never break
REM narration.
REM
REM   * int8  - recommended.  ~1.3-1.8x faster decode, ~half the weights on
REM             disk (faster load too), negligible quality change.  Runs on CPU.
REM   * int4  - more aggressive; needs an NVIDIA GPU to BUILD.  Try only if you
REM             want the extra speed and have A/B'd the quality.
REM
REM Usage:
REM     Quantize_Model.bat            (int8)
REM     Quantize_Model.bat int4       (int4)
REM
REM Run this AFTER the mod's first-run setup has completed (so torch + the
REM model weights are already installed).  To revert to fp16, just delete the
REM checkpoints\fish-speech-1.5-int8 (or -int4-g128-q) folder.
REM ===========================================================================

setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "MODE=%~1"
if "%MODE%"=="" set "MODE=int8"

set "PYCMD=python\python.exe"
if defined POKEDEX_VO_PYTHON set "PYCMD=%POKEDEX_VO_PYTHON%"
if not exist "%PYCMD%" (
    echo ERROR: bundled Python not found at "%PYCMD%".
    echo Run Start_TTS_Server.bat once first to install it.
    pause
    exit /b 1
)

echo ===========================================================
echo  Quantizing the voice model to %MODE% (one-time).
echo  This loads the full model and can take a few minutes.
echo ===========================================================
"%PYCMD%" setup.py --quantize %MODE% --skip-install --skip-download --skip-smoke
set "RC=!ERRORLEVEL!"

echo.
if "!RC!"=="0" (
    echo Done.  The server will use the %MODE% model automatically next launch.
    echo Delete the new checkpoints\fish-speech-1.5-%MODE%* folder to revert.
) else (
    echo Quantization exited with code !RC! - see the messages above.
    echo The mod will keep using the fp16 model.
)
echo.
pause
endlocal & exit /b %RC%
