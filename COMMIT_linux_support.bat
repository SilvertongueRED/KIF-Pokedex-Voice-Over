@echo off
setlocal EnableExtensions
cd /d "%~dp0"

REM A crashed git process can leave a stale lock that blocks all git writes.
if exist ".git\index.lock" del ".git\index.lock"

REM No global git identity on this machine - set a local one for this repo.
git config --local user.name  "SilvertongueRED"
git config --local user.email "slvrtnge@gmail.com"

echo Staging the Linux / macOS support changes...
git add "Mods/pokedex_voice_over/fish_speech/setup.py"
git add "Mods/pokedex_voice_over/fish_speech/start_tts_server.sh"

git commit -m "Add Linux/macOS support: venv isolation + cross-platform slim" -m "start_tts_server.sh now creates an isolated ./venv (mirrors the Windows embeddable Python) so installs never touch the system Python and PEP 668 distros work. setup.py slim_install() is cross-platform: finds venv site-packages via sysconfig, guards so it only ever slims the mod's own interpreter (never a shared/system Python), and deletes *.a static libs alongside *.lib. torch/testing stays; slim still runs before the smoke test."
if errorlevel 1 (
  echo.
  echo Nothing committed ^(no changes staged, or commit failed - see above^).
  pause ^& exit /b 1
)

echo.
git --no-pager log -1 --stat
echo.
echo Committed. Run SHRINK_GIT_HISTORY.bat next if you still want to shrink .git.
echo.
pause
endlocal
