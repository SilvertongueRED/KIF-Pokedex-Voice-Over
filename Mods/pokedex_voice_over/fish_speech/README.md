# Fish-Speech Offline TTS for Pokédex Voice Over

This folder contains the **fish-speech** open-source voice-clone TTS engine
that reads every Pokédex entry — base, custom, or auto-generated fusion —
in the same Dexter-style voice as the Fish Audio
[Pokédex Voice Over model](https://fish.audio/m/57a07a0af0954230a44d1db3adc77940/),
fully **offline**, with **no pre-generated audio files** and **no API keys**.

## Plug-and-play install — nothing to download or configure

You do **not** need to install Python, install pip packages, or download
the model yourself. The first time you launch KIF with the mod installed,
the mod auto-spawns `Start_TTS_Server.bat` (Windows) or `start_tts_server.sh`
(macOS / Linux), which performs a complete first-run setup:

1. **Python check** — if Python isn't on PATH:
   * **Windows**: `install_python.bat` silently downloads the official
     Python 3.12 installer from python.org and runs it with
     `/quiet InstallAllUsers=0 PrependPath=1` — i.e. **per-user install
     with no admin prompt**, and PATH is updated automatically.
   * **macOS**: `install_python.sh` runs `brew install python@3.12` if
     Homebrew is present (and prints clear install steps if it isn't).
   * **Linux**: `install_python.sh` detects apt / dnf / pacman / zypper and
     runs the appropriate `sudo install python3 python3-pip` command.
2. **Detects** whether your machine has an NVIDIA GPU (CUDA) or only a CPU.
3. **Installs** the matching PyTorch wheel.
4. **Installs** `fish-speech` and its small runtime helpers.
5. **Downloads** the ~1.4 GB model from the public
   [`fishaudio/fish-speech-1.5`](https://huggingface.co/fishaudio/fish-speech-1.5)
   Hugging Face repo (no token / login required — it's a public model).
6. **Normalises** the bundled reference clip and **runs a smoke test**.

A `.installed` marker is written when setup succeeds so every future launch
skips straight to the server. From that point on the mod runs fully offline.

> **No prerequisites.** Drop the mod into your KIF `Mods/` folder, launch
> the game, open a Pokédex entry. That's the entire install procedure.

## Running setup manually

If you'd rather pre-warm the install (or you hit an error and want to see
the full output), you can run setup yourself from inside this folder:

```bash
python setup.py            # full setup
python setup.py --check    # just verify the install
python setup.py --force-cpu  # force CPU torch even if a GPU is present
```

`setup.py` is idempotent: re-runs detect already-installed packages and
already-downloaded model files and skip them.

## Changing the reference voice

The bundled `reference/voice.wav` clones the Pokédex-narrator voice from the
Fish Audio model linked above. You can replace it with any other 10–30s clip
to clone a different voice. See `reference/README.txt` for tips on picking a
good reference clip.

## How it works

* `server.py` — small HTTP server (`127.0.0.1:7861`) that keeps the
  fish-speech model resident in memory and exposes `/health` + `/tts`.
* `main.rb` (in the parent mod folder) calls `POST /tts` with the exact text
  KIF is currently displaying and plays the returned WAV.
* The first call after each game launch is the slowest (model warming);
  subsequent calls are ~0.5 s on GPU, ~3–8 s on a modern CPU.
* The Pokémon's cry plays first for a short delay, masking generation time.
* Generated WAVs are cached by content hash in `Audio/SE/Pokedex/tts_cache/`,
  so re-visiting the same entry plays instantly.

## Hardware

| Setup                | Per-entry latency | Notes                                |
|----------------------|-------------------|--------------------------------------|
| NVIDIA GPU (CUDA)    | ~0.3 – 0.8 s      | Recommended.  Auto-detected.         |
| Apple Silicon (MPS)  | ~1 – 3 s          | Partial fish-speech support.         |
| Modern x86 CPU       | ~3 – 8 s          | Works fine; cached after first play. |
| Old / low-RAM CPU    | 15 s+ or OOM      | Fall back to Piper (built-in).       |

The mod also has a built-in **Piper TTS** fallback that activates if this
server isn't running — provided you drop a Piper voice into
`Mods/pokedex_voice_over/piper/`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `install_python.bat` fails to download | Your network blocks python.org. Install Python 3.10+ manually from <https://www.python.org/downloads/> (tick "Add Python to PATH"), then re-run `Start_TTS_Server.bat`. |
| First launch hangs at "Installing torch" | Wheels are 1+ GB; first install takes a few minutes. Subsequent launches are instant. |
| `huggingface_hub.errors.LocalEntryNotFoundError` | Network was down during the model download. Delete `.installed`, re-run `Start_TTS_Server.bat`. |
| Generated voice sounds wrong | Replace `reference/voice.wav` with a cleaner / longer clip and add a transcript at `reference/voice.txt`. |
| First entry plays after long delay | Model is loading — pre-launch the server via `Start_TTS_Server.bat` to hide the warm-up. |
| `CUDA out of memory` | Run `Start_TTS_Server.bat --device cpu` (or edit the .bat to pass `--device cpu`). |
| Port 7861 already in use | Edit the `.bat`/`.sh` to add ` --port 7862` and update `FISH_SPEECH_PORT` in `../main.rb`. |

## Resetting

To force a clean reinstall, delete the `.installed` marker and the
`checkpoints/` folder. The next launch will redownload everything.

## License

fish-speech is Apache 2.0. The mod itself is MIT. Pokémon trademarks belong
to Nintendo / Game Freak — this is a fan project.
