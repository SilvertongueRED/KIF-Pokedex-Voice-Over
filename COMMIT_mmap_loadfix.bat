@echo off
REM ============================================================
REM  Commit the model-load + timeout fixes
REM ============================================================
REM  Fixes the runtime crash on game launch:
REM    "RuntimeError: mmap can only be used with files saved with
REM     torch.save(_use_new_zipfile_serialization=True)"
REM  by making the vendored engine fall back to a non-mmap load
REM  when model.pth is a legacy (non-zipfile) checkpoint, and
REM  replaces the Ruby 3.2-only IO::TimeoutError (which crashed
REM  on MKXP-Z's Ruby 3.1.3) with a self-defined ReadTimeout.
REM
REM  Run this from the repo root:  COMMIT_mmap_loadfix.bat
REM ============================================================
cd /d "%~dp0"

echo Staging changed files...
git add Mods/pokedex_voice_over/fish_speech/vendor/fish_speech/models/text2semantic/llama.py
git add Mods/pokedex_voice_over/fish_speech/vendor/fish_speech/models/vqgan/inference.py
git add Mods/pokedex_voice_over/main.rb

echo.
git status --short
echo.

echo Committing...
git commit -m "Fix launch crash: non-mmap fallback for legacy model.pth; Ruby 3.1-safe ReadTimeout (replaces IO::TimeoutError)"
echo.

echo Done. Review the log above, then run:  git push
echo.
pause
