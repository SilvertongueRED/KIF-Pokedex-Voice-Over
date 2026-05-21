#!/usr/bin/env bash
# Local cleanup before committing & pushing to KIF-Mods (macOS / Linux).
set -euo pipefail
cd "$(dirname "$0")"

echo "Removing bulky local artifacts..."
rm -rf Mods/pokedex_voice_over/fish_speech/checkpoints
rm -rf Mods/pokedex_voice_over/fish_speech/.torch_cache
rm -rf Mods/pokedex_voice_over/fish_speech/__pycache__
rm -f  Mods/pokedex_voice_over/fish_speech/=2.0.0
rm -f  Mods/pokedex_voice_over/fish_speech/server.log
rm -f  Mods/pokedex_voice_over/fish_speech/smoke_test.wav
rm -f  Mods/pokedex_voice_over/fish_speech/.installed
rm -f  Mods/pokedex_voice_over/debug.log
rm -f  Mods/PreModManagerpokedex_voice_over.zip
rm -f  .write_test

cat <<MSG

Done.  Suggested next steps:
  git add .gitignore README.md Mods/pokedex_voice_over
  git rm -r --cached piper_training_data   # (if you want to stop tracking the Piper training set)
  git commit -m "v2.0.0 - offline Fish-Speech voice clone, ready for KIF-Mods"
  git push origin main

MSG
