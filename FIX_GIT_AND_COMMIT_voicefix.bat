@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo === Step 1/3: repair the corrupted .git\packed-refs ===
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0REPAIR_GIT.ps1"
if errorlevel 1 ( echo packed-refs repair FAILED - see message above. & pause & exit /b 1 )

echo.
echo === Step 2/3: confirm git is readable again ===
git status --short
if errorlevel 1 ( echo git is still unhappy - see message above. & pause & exit /b 1 )

echo.
echo === Step 3/3: commit the voice-over crash fix ===
git config --local user.name  "SilvertongueRED"
git config --local user.email "slvrtnge@gmail.com"
git add "Mods/pokedex_voice_over/fish_speech/setup.py"
git add "Mods/pokedex_voice_over/fish_speech/server.py"
git commit -m "Fix TTS crash after slim install: keep torch/testing; slim before smoke test" -m "slim_install() deleted site-packages/torch/testing - a public submodule torch's own __init__ imports - so import torch aborted half-way in the server. CUDA then read as unavailable (device=cpu) and the next import raised 'cannot import name nn from partially initialized module torch (circular import)'. Stop deleting torch/testing and the blanket per-package test/tests dirs. Run slim_install BEFORE smoke_test so the smoke test validates the shipped (slimmed) env and setup fails loudly if a trim breaks torch. detect_device now logs the real torch-import error instead of silently using CPU."
if errorlevel 1 (
  echo.
  echo Nothing committed ^(no changes staged, or commit failed - see above^).
  pause & exit /b 1
)

echo.
git --no-pager log -1 --stat
echo.
echo Committed. IMPORTANT - to rebuild the broken environment, do a clean slim reinstall
echo in the copy you actually run ^(your Steam install^):
echo   1^) delete  fish_speech\python   AND   fish_speech\.installed   ^(both, together^)
echo   2^) launch  fish_speech\Start_TTS_Server.bat   ^(or just start the game^)
echo.
pause
endlocal
