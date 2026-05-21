@echo off
REM ============================================================================
REM Local cleanup before committing & pushing to KIF-Mods.
REM
REM Deletes the bulky artifacts that should NOT be tracked in git:
REM   - The 1.4 GB downloaded Fish-Speech model + vendored upstream repo
REM   - The Mod-Manager backup zip
REM   - Runtime logs, install markers, torch caches, pycache, etc.
REM
REM Safe to re-run: nothing the mod needs at runtime is touched, because
REM Start_TTS_Server.bat will redownload everything on first launch.
REM ============================================================================

setlocal
cd /d "%~dp0"

echo Removing bulky local artifacts...

if exist "Mods\pokedex_voice_over\fish_speech\checkpoints"  rmdir /s /q "Mods\pokedex_voice_over\fish_speech\checkpoints"
if exist "Mods\pokedex_voice_over\fish_speech\.torch_cache" rmdir /s /q "Mods\pokedex_voice_over\fish_speech\.torch_cache"
if exist "Mods\pokedex_voice_over\fish_speech\__pycache__" rmdir /s /q "Mods\pokedex_voice_over\fish_speech\__pycache__"
if exist "Mods\pokedex_voice_over\fish_speech\=2.0.0"      del /q   "Mods\pokedex_voice_over\fish_speech\=2.0.0"
if exist "Mods\pokedex_voice_over\fish_speech\server.log"  del /q   "Mods\pokedex_voice_over\fish_speech\server.log"
if exist "Mods\pokedex_voice_over\fish_speech\smoke_test.wav" del /q "Mods\pokedex_voice_over\fish_speech\smoke_test.wav"
if exist "Mods\pokedex_voice_over\fish_speech\.installed"  del /q   "Mods\pokedex_voice_over\fish_speech\.installed"
if exist "Mods\pokedex_voice_over\debug.log"               del /q   "Mods\pokedex_voice_over\debug.log"
if exist "Mods\PreModManagerpokedex_voice_over.zip"        del /q   "Mods\PreModManagerpokedex_voice_over.zip"
if exist ".write_test"                                     del /q   ".write_test"

echo.
echo Done.  Suggested next steps:
echo   git add .gitignore README.md Mods/pokedex_voice_over
echo   git rm -r --cached piper_training_data   (if you want to stop tracking the Piper training set)
echo   git commit -m "v2.0.0 - offline Fish-Speech voice clone, ready for KIF-Mods"
echo   git push origin main
echo.
pause
