# v2.0.1 — Offline, self-contained voice-over

_Suggested tag: `v2.0.1` · Suggested title: “v2.0.1 — Offline, self-contained voice-over”_

Pokédex Voice Over reads every Pokédex entry aloud in the style of Dexter, the
classic anime narrator — for every base Pokémon, every custom dex entry, and
every auto-generated fusion. This release makes the offline Fish-Speech engine
**fully self-contained and reproducible**, and fixes a first-run setup failure
that could leave the voice silent and your GPU unused.

## Highlights

- **The voice engine now ships inside the mod.** The Fish-Speech 1.5 engine is
  bundled (vendored) in `fish_speech/vendor/`, so first-run setup no longer
  clones or downloads it from the internet. Setup now installs only PyTorch and
  a small, fixed set of inference dependencies, then downloads the voice model.
  Fewer moving parts, no reliance on an external repo staying available, and
  identical results on every install.
- **Fixed a first-run setup failure that also disabled GPU acceleration** (details below).
- **Setup now self-repairs.** Re-running it fixes a PyTorch install that ended
  up CPU-only on a CUDA machine, and removes a mismatched engine left by an
  earlier run.

## Bug fixes

- **Setup installed the wrong engine version.** Earlier setup pulled
  Fish-Speech **2.x**, which is incompatible with the 1.5 voice model this mod
  uses. Two things broke as a result:
  - The model failed to load with `Error locating target
    '…DownsampleFiniteScalarQuantize'` (the 2.x rewrite removed the audio module
    the 1.5 model needs), so the Pokédex stayed silent.
  - Installing 2.x also pulled a **CPU-only build of PyTorch**, silently
    replacing the CUDA build and dropping GPU acceleration — entries that should
    take ~0.5 s took several seconds.

  Setup now uses the bundled, pinned 1.5 engine and leaves your CUDA PyTorch
  intact.

## Upgrading (if your first install failed)

Do a clean rebuild — your downloaded model is cached and won't re-download:

1. Replace the mod's `fish_speech` folder with this release's.
2. Delete `Mods/pokedex_voice_over/fish_speech/python`.
3. Launch the game, or double-click `fish_speech/Start_TTS_Server.bat`.

You should see the server finish with `fish-speech ready (… device=cuda …)`.

## Install (new users)

Unchanged: drop `pokedex_voice_over/` into your KIF `Mods/` folder and open any
Pokédex entry. First launch performs a one-time setup (bundled portable Python
on Windows, PyTorch, and the ~1.4 GB voice model from Hugging Face); everything
after that runs fully offline with no API keys. See the README for details.

## For contributors / under the hood

- Engine vendored at `fish_speech/vendor/` — pinned Fish-Speech **1.5**
  (provenance + integrity hash in `fish_speech/vendor/README.md`). **Do not bump
  to 2.x** without rewriting `server.py` and swapping the model checkpoint; 2.x
  removed the firefly VQ-GAN module the 1.5 checkpoint instantiates.
- Inference dependencies are pinned in `fish_speech/requirements-runtime.txt`
  (only what the inference path imports — the heavy training / ASR / web-UI
  dependencies are intentionally excluded).
- Added `.gitattributes` to keep line endings consistent across clones and
  protect the vendored tree from CRLF conversion.

## Requirements

- Kuray's Infinite Fusion with Mod Manager support.
- Windows / macOS / Linux. Optional NVIDIA CUDA GPU for ~0.5 s/entry (CPU works
  at ~3–8 s).
- ~3 GB disk and a one-time internet connection for first-run setup; fully
  offline afterward.

## Credits

Fish-Speech by Fish Audio (Apache-2.0) · Piper by Rhasspy (MIT) · Pokémon
Infinite Fusion by Chardub/Frogman · Kuray's Infinite Fusion by
kurayamiblackheart and contributors. This mod is MIT-licensed and is a fan
project, not affiliated with or endorsed by Nintendo / Game Freak.
