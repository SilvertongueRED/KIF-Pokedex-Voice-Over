#!/usr/bin/env bash
# One-click commit for the offline Fish-Speech 1.5 vendoring work (Git Bash / *nix).
# Stages ONLY the offline-TTS / vendoring changes; leaves your other in-progress
# edits untouched. Safe to delete this script afterwards.
set -e
cd "$(dirname "$0")"
git config user.email >/dev/null 2>&1 || git config --local user.email "slvrtnge@gmail.com"
git config user.name  >/dev/null 2>&1 || git config --local user.name  "SilvertongueRED"
[ -f .git/index.lock ] && rm -f .git/index.lock || true
git add .gitattributes README.md Mods/pokedex_voice_over/fish_speech ':(exclude)Mods/pokedex_voice_over/fish_speech/reference/voice.codes.pt'
echo "Files to be committed:"; git diff --cached --name-only
git commit -m "Offline Fish-Speech 1.5: vendor engine, leaf-only deps, line-ending policy" -m "Vendor the fish-speech 1.5 engine under fish_speech/vendor/ (loaded via sys.path by server.py) so setup never clones fish-speech. setup.py installs torch + requirements-runtime.txt leaf deps only and is repair-aware. server.py loads from vendor/. Adds .gitattributes and documents the design in README.md + vendor/README.md. Pinned to 1.5: 2.x removed the firefly VQ-GAN module the checkpoint needs."
git status --short
echo "To publish: git push"
