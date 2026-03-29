# KIF Pokédex Voice Over

A mod for **Kuray's Infinite Fusion (KIF)** that reads every Pokédex entry
aloud in the style of **Dexter** — the classic robotic Pokédex narrator from
the original Pokémon anime — whenever you open a Pokémon's Pokédex entry,
including every fused variant.

---

## Features

- 🔊 Reads the full Pokédex description text aloud the moment you open any
  entry
- 🧬 Works for **all fused Pokémon** combinations (head × body) when
  fusion-specific audio files are available
- ⚙️ In-game settings (volume, enable/disable, re-read on page return) via
  the KIF Mod Manager
- 🔇 Silently skips entries that have no audio file — safe to install before
  generating audio
- 🛑 Automatically stops the voice when you close the Pokédex

---

## Requirements

| Requirement | Notes |
|---|---|
| **Kuray's Infinite Fusion (KIF)** | The Kuray fork with Mod Manager support |
| **Python 3.8+** | For the audio-generation script only |
| **ffmpeg** | Required by pydub to export OGG files — [download here](https://ffmpeg.org/download.html) |

---

## Installation

### Step 1 — Install the mod

Copy the `Mods/pokedex_voice_over/` folder into your KIF game's `Mods/`
directory:

```
<KIF game root>/
└── Mods/
    └── pokedex_voice_over/
        ├── mod.json
        └── main.rb
```

Alternatively, install it through the in-game **Mod Browser** once the mod
has been published to the KIF-Mods repository.

### Step 2 — Generate audio files

The mod does **not** bundle pre-generated audio (the files would be hundreds
of megabytes).  You generate them yourself from your own game data using the
included Python script.

#### 2a. Install Python dependencies

```bash
cd tools/
pip install -r requirements.txt
```

> **Windows users:** `pyttsx3` uses the built-in Microsoft SAPI voices (e.g.
> "Microsoft David Desktop"), which sound naturally robotic — ideal for
> replicating the Dexter voice.  No extra setup needed.
>
> **macOS/Linux users:** `pyttsx3` uses the system voice (Alex / eSpeak).
> You may prefer `--backend gtts` for a slightly cleaner result.

#### 2b. Run the generator

```bash
# Generate all regular Pokémon entries
python tools/generate_voices.py --game-dir /path/to/KIF

# Generate and overwrite any existing files
python tools/generate_voices.py --game-dir /path/to/KIF --overwrite

# Generate for a single Pokémon
python tools/generate_voices.py --game-dir /path/to/KIF --species BULBASAUR

# Also generate fusion-specific entries (if your KIF build has them in PBS)
python tools/generate_voices.py --game-dir /path/to/KIF --fusions

# Use Google TTS instead of offline pyttsx3
python tools/generate_voices.py --game-dir /path/to/KIF --backend gtts

# List available pyttsx3 voices (to pick the most Dexter-like one)
python tools/generate_voices.py --list-voices
python tools/generate_voices.py --game-dir /path/to/KIF --voice 1
```

The script will save `.ogg` files to:

```
<KIF game root>/Mods/pokedex_voice_over/Audio/
```

Audio files are named after the Pokémon's internal species name:

| File | Pokémon |
|---|---|
| `dex_BULBASAUR.ogg` | Bulbasaur |
| `dex_CHARMANDER.ogg` | Charmander |
| `dex_BULBASAUR_CHARMANDER.ogg` | Bulbasaur/Charmander fusion |

### Step 3 — Launch KIF

Start the game.  The Mod Manager loads the plugin automatically.  Open any
Pokédex entry and the voice will play.

---

## Mod Settings

Access settings from **Title Screen → Mod Manager → Installed Mods →
Pokédex Voice Over → Settings**.

| Setting | Default | Description |
|---|---|---|
| **Enable Voice Over** | On | Master on/off switch |
| **Voice Volume** | 80 | Playback volume (0–100) |
| **Re-read on Page Return** | Off | Re-play the voice when navigating back to the description page |

---

## Fused Pokémon Support

The mod hooks into the same Pokédex scene that displays fused entries.  When
a fusion's audio file exists, it plays automatically.  If no fusion-specific
file is found, the mod falls back to silence (it does **not** play the head
or body species voice instead, to avoid confusing entries).

To generate fusion audio you need KIF builds that store fusion Pokédex
descriptions in one of the following PBS files:

```
PBS/fusions.txt
PBS/fusion_dex.txt
PBS/fusionmon.txt
```

Run the generator with `--fusions` to process these files.

---

## How the Dexter Voice Effect Works

The generator applies three post-processing steps to any TTS voice to
approximate the classic Pokédex sound:

1. **High-pass filter (350 Hz)** — removes bass, producing the thin,
   broadcast-radio quality
2. **Low-pass filter (4 000 Hz)** — softens harsh sibilance
3. **Echo overlay (35 ms, −13 dB)** — adds the characteristic robotic
   resonance

These effects are applied by `pydub`.  If `pydub` is not installed the raw
TTS audio is saved without effects.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| No voice plays | Check that `.ogg` files are in `Mods/pokedex_voice_over/Audio/` |
| `ffmpeg not found` error | Install ffmpeg and add it to your PATH |
| pyttsx3 voices sound wrong | Try `--voice 1` (or another index shown by `--list-voices`) |
| gTTS fails | Check your internet connection |
| Voice cuts off mid-sentence | Normal for the ME channel — the full file plays but in-game ME events can interrupt it |
| Mod not loading | Ensure `Mods/pokedex_voice_over/mod.json` and `main.rb` are both present |

---

## Repository Structure

```
KIF-Pokedex-Voice-Over/
├── Mods/
│   └── pokedex_voice_over/     ← copy this folder into your game's Mods/
│       ├── mod.json            ← KIF mod manifest
│       └── main.rb             ← Ruby plugin (hooks Pokédex scene + plays audio)
├── tools/
│   ├── generate_voices.py      ← Python script to generate TTS audio files
│   └── requirements.txt        ← Python dependencies
└── README.md
```

---

## Contributing

Pull requests and issue reports are welcome.  If you discover the exact
instance-variable name KIF uses for the second fusion species, or the correct
entry-page index for a specific KIF version, please open an issue or PR so
the hook can be made more precise.

---

## Licence

This mod is released under the [MIT Licence](https://opensource.org/licenses/MIT).
Pokémon is a trademark of Nintendo / Game Freak.  This is an unofficial fan
project with no affiliation to Nintendo, Game Freak, or The Pokémon Company.
