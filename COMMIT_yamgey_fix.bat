@echo off
REM ============================================================================
REM  Commit the Yamgey stuttering/overlap voice-over fix (main.rb only).
REM  Double-click this file. It runs on your native Windows git (no sandbox),
REM  which avoids the .git/index corruption seen when committing from the tool.
REM ============================================================================
cd /d "%~dp0"

REM This repo has no global git identity configured - set it locally.
git config --local user.name  "SilvertongueRED"
git config --local user.email "slvrtnge@gmail.com"

echo.
echo Staging Mods\pokedex_voice_over\main.rb ...
git add "Mods/pokedex_voice_over/main.rb"

echo.
git commit -m "fix(tts): self-heal corrupt sentence-cache + guard against overlapping streamed sentences (Yamgey)"

echo.
echo Done. Review the commit above, then push from GitHub Desktop when ready.
pause
