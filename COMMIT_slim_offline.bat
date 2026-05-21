@echo off
setlocal EnableExtensions
cd /d "%~dp0"

REM No global git identity on this machine - set a local one for this repo.
git config --local user.name  "SilvertongueRED"
git config --local user.email "slvrtnge@gmail.com"

echo Staging the slim / offline changes...
git add "Mods/pokedex_voice_over/fish_speech/setup.py"
git add "Mods/pokedex_voice_over/fish_speech/requirements-runtime.txt"

git commit -m "Slim install footprint: torchaudio resampling + post-install slim pass" -m "Drop librosa (pulls numba/llvmlite/scipy, ~250 MB); resample the reference WAV via torchaudio instead. Add slim_install(): uninstall non-runtime packages, delete torch *.lib / include / bundled tests, clear __pycache__. ~1.3-1.6 GB smaller on disk with no feature or clone-fidelity change."
if errorlevel 1 (
  echo.
  echo Nothing committed (no changes staged, or commit failed - see above).
  pause & exit /b 1
)

echo.
git --no-pager log -1 --stat
echo.
echo Committed. You can now run SHRINK_GIT_HISTORY.bat if you want to shrink .git too.
echo.
pause
endlocal
