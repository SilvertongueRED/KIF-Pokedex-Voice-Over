@echo off
REM ===========================================================================
REM  One-click commit for the offline Fish-Speech 1.5 vendoring work.
REM  Double-click this file.  It stages ONLY the offline-TTS / vendoring changes
REM  and commits them; your other in-progress edits (main.rb, mod.json,
REM  .gitignore, the piper_training_data deletion) are left untouched.
REM  You can delete this file afterwards - it is not part of the commit.
REM ===========================================================================
setlocal
cd /d "%~dp0"

where git >nul 2>&1 || (echo ERROR: git is not on your PATH. & pause & exit /b 1)

echo Ensuring a git identity is set...
git config user.email >nul 2>&1 || git config --local user.email "slvrtnge@gmail.com"
git config user.name  >nul 2>&1 || git config --local user.name  "SilvertongueRED"

if exist ".git\index.lock" del /f /q ".git\index.lock"

echo Staging the offline Fish-Speech 1.5 work...
git add .gitattributes README.md "Mods/pokedex_voice_over/fish_speech"
REM drop the regenerable runtime cache if it got staged
git restore --staged "Mods/pokedex_voice_over/fish_speech/reference/voice.codes.pt" 2>nul

echo.
echo Files to be committed:
git diff --cached --name-only
echo.

git commit -m "Offline Fish-Speech 1.5: vendor engine, leaf-only deps, line-ending policy" -m "Vendor the fish-speech 1.5 engine under fish_speech/vendor/ (loaded via sys.path by server.py) so setup never clones fish-speech from GitHub/PyPI. setup.py now installs torch (cu121/cpu) + the inference leaf deps in requirements-runtime.txt only, and is repair-aware (fixes a CPU-only torch, removes any stray pip-installed fish-speech 2.x). server.py loads the engine from fish_speech/vendor/. Adds .gitattributes (force-LF vendored tree, CRLF .bat, binary blobs) and documents the offline design in README.md + a provenance stamp at fish_speech/vendor/README.md. Pinned to 1.5 on purpose: 2.x removed the firefly VQ-GAN module the 1.5 checkpoint instantiates."

echo.
echo ===========================================================================
echo Done. Current status:
git status --short
echo.
echo To publish:  git push    (or use your GitHub client)
echo ===========================================================================
pause
