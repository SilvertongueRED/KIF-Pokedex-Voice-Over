@echo off
setlocal EnableExtensions
cd /d "%~dp0"

REM A crashed git process can leave a stale lock that blocks all git writes.
if exist ".git\index.lock" del ".git\index.lock"

REM No global git identity on this machine - set a local one for this repo.
git config --local user.name  "SilvertongueRED"
git config --local user.email "slvrtnge@gmail.com"

echo Staging the TTS resident-server / self-heal fix...
git add "Mods/pokedex_voice_over/main.rb"
git add "Mods/pokedex_voice_over/fish_speech/server.py"

git commit -m "Fix: keep TTS sidecar resident during play + self-heal restart" -m "server.py: skip the idle-shutdown watchdog whenever a live --parent-pid is being monitored (the parent-PID monitor already shuts the server down ~5s after the game closes), so a healthy in-use server is no longer killed after 5 min of quiet play. main.rb: _fish_speech_try_autostart is now a throttled retry (once per 30s) instead of a permanent one-shot latch, so the sidecar self-heals if it ever dies mid-session; fish_speech_available? re-polls a DOWN server every 5s (positives still cached 60s) so recovery is prompt."
if errorlevel 1 (
  echo.
  echo Nothing committed ^(no changes staged, or commit failed - see above^).
  pause ^& exit /b 1
)

echo.
git --no-pager log -1 --stat
echo.
echo Committed.
echo.
pause
endlocal
