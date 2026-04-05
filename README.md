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
| **Python 3.8+** | For the audio-generation script only; Python 3.13+ is fully supported via `audioop-lts` (installed automatically by `pip install -r tools/requirements.txt`) |
| **ffmpeg** | Required by pydub to export OGG files — [download here](https://ffmpeg.org/download.html) |
| **Internet access** | Required for the recommended `--backend fakeyou` (the real anime Pokédex voice) |

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

#### 2b. Run the generator

**Recommended: Use FakeYou for the real anime Pokédex voice**

The `--backend fakeyou` option uses the [FakeYou](https://fakeyou.com/) AI
voice model **"Pokedex (Pokemon, 4Kids)"** — a community-trained model that
replicates the actual Dexter voice from the original Pokémon anime.  This is
the same approach used by [adriantwarog/Pokedex-RL](https://github.com/adriantwarog/Pokedex-RL).

```bash
# Generate all Pokémon entries (regular + fusions) with the real anime voice
python tools/generate_voices.py --game-dir /path/to/KIF --backend fakeyou

# With FakeYou login (auto-obtains session cookie — recommended)
python tools/generate_voices.py --game-dir /path/to/KIF --backend fakeyou --fakeyou-username YOUR_USER --fakeyou-password YOUR_PASS

# With FakeYou priority queue access (set FAKEYOU_COOKIE env var or pass directly)
python tools/generate_voices.py --game-dir /path/to/KIF --backend fakeyou --fakeyou-cookie YOUR_COOKIE

# Generate for a single Pokémon
python tools/generate_voices.py --game-dir /path/to/KIF --backend fakeyou --species BULBASAUR

# Skip fusion entries — generate only regular Pokémon
python tools/generate_voices.py --game-dir /path/to/KIF --backend fakeyou --no-fusions

# Overwrite any existing files
python tools/generate_voices.py --game-dir /path/to/KIF --backend fakeyou --overwrite

# Retry only entries that failed in a previous run
python tools/generate_voices.py --game-dir /path/to/KIF --backend fakeyou --retry-failed
```

> **Authenticating with FakeYou (recommended for faster generation):**
>
> **Option A — Login directly (easiest):** Pass `--fakeyou-username` and
> `--fakeyou-password` on the command line.  The script will log in
> automatically and use the session for priority queue access.  You can
> also use `FAKEYOU_USERNAME` and `FAKEYOU_PASSWORD` environment variables.
>
> **Option B — Session cookie:** Create a free account at
> [fakeyou.com](https://fakeyou.com/), log in, then copy the `session`
> cookie value from your browser's developer tools (Application → Cookies
> → fakeyou.com → `session`).  Pass it via `--fakeyou-cookie` or the
> `FAKEYOU_COOKIE` environment variable.

**Alternative: Offline/generic TTS backends**

If you prefer offline generation or do not have internet access, the
`pyttsx3` and `gTTS` backends are available as fallbacks.  These use generic
system voices with post-processing effects to approximate the Pokédex sound,
but they will not match the real anime voice.

```bash
# Use offline system TTS (pyttsx3 — generic voice with effects)
python tools/generate_voices.py --game-dir /path/to/KIF

# Use Google TTS (gTTS — generic voice with effects)
python tools/generate_voices.py --game-dir /path/to/KIF --backend gtts

# List available pyttsx3 voices (to pick one)
python tools/generate_voices.py --list-voices
python tools/generate_voices.py --game-dir /path/to/KIF --voice 1
```

The script will save `.ogg` files to:

```
<KIF game root>/Audio/SE/
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

## Where Pokédex Data Comes From

The generator script automatically detects and reads Pokédex data from your
game installation:

1. **`Data/species.dat`** (KIF — preferred) — The compiled species database
   that ships with every KIF installation.  Requires the `rubymarshal`
   Python package (installed automatically by `pip install -r requirements.txt`).
2. **`PBS/pokemon.txt`** (vanilla PIF fallback) — The plain-text PBS file
   used by original Pokémon Infinite Fusion builds.  If your installation
   has a `PBS/` folder, the script will use it when `species.dat` is not
   available.

If neither file is found, use `--pbs-file` to point the script at a PBS
file manually.

---

## Fused Pokémon Support

The mod hooks into the same Pokédex scene that displays fused entries.  When
a fusion's audio file exists, it plays automatically.  If no fusion-specific
file is found, the mod falls back to silence (it does **not** play the head
or body species voice instead, to avoid confusing entries).

To generate fusion audio you need KIF builds that store fusion Pokédex
descriptions.  The script checks two sources automatically (fusions are
generated by default — use `--no-fusions` to skip):

1. **`Data/pokedex/dex.json`** (KIF) — Community-contributed fusion entries
   included with KIF.  The script uses `Data/species.dat` to map sprite IDs
   to species names automatically.
2. **PBS fusion files** (vanilla PIF fallback):

```
PBS/fusions.txt
PBS/fusion_dex.txt
PBS/fusionmon.txt
```

---

## How the Voice Generation Works

### FakeYou backend (recommended — `--backend fakeyou`)

The generator uses the [FakeYou](https://fakeyou.com/) text-to-speech API
with the community-trained **"Pokedex (Pokemon, 4Kids)"** AI voice model.
This model was trained on actual Pokédex Dexter voice clips from the 4Kids
English dub of the Pokémon anime, producing audio that sounds like the real
thing.  This is the same voice model used by the
[real-life Pokédex project](https://github.com/adriantwarog/Pokedex-RL).

No post-processing effects are applied — the AI voice model produces the
authentic Pokédex sound directly.

### pyttsx3 / gTTS backends (offline fallbacks)

When using the generic `pyttsx3` or `gTTS` backends, the generator applies
three post-processing steps to approximate the Pokédex sound:

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
| Voice doesn't sound like the anime Pokédex | Use `--backend fakeyou` — the default `pyttsx3`/`gTTS` backends use generic voices with effects. FakeYou uses an AI model trained on the real anime voice. |
| FakeYou job times out | The anonymous queue can be slow. Use `--fakeyou-username`/`--fakeyou-password` to log in automatically, or pass a session cookie via `--fakeyou-cookie` / `FAKEYOU_COOKIE` env var for priority access. |
| FakeYou returns "rate limited" | Wait a few minutes and try again. With a FakeYou account you get higher rate limits. For large batches, the script automatically pauses and retries. |
| FakeYou login/cookie error | Make sure your FakeYou username/password are correct. If passing a cookie manually, you only need the token value — the script handles `session=` prefixes automatically. |
| `requests` not installed | Run `pip install -r tools/requirements.txt` to install all dependencies including `requests` (needed for `--backend fakeyou`). |
| No voice plays | Check that `.ogg` files are in `Audio/SE/` (inside your KIF game root) |
| Some entries failed to generate | Re-run with `--retry-failed` to retry only the failed entries — check `Audio/SE/failed_entries.json` for details |
| `No Pokédex entries found` | Run `pip install rubymarshal` so the script can read `Data/species.dat` directly.  If your game has a `PBS/` folder instead, it will be used automatically. |
| `ffmpeg not found on PATH` warning | Install ffmpeg and add it to your PATH — see the [ffmpeg download page](https://ffmpeg.org/download.html).  On Windows, open a **new** terminal after updating PATH so the change takes effect. |
| `pydub processing failed: [WinError 2]` | ffmpeg is not on PATH (or the terminal was opened before ffmpeg was added to PATH).  Install ffmpeg and restart your terminal, then re-run the script. |
| `pydub` import fails with `ModuleNotFoundError: No module named 'audioop'` (Python 3.13+) | Run `pip install -r tools/requirements.txt` — this installs `audioop-lts` which replaces the `audioop` module removed in Python 3.13. |
| pyttsx3 voices sound wrong | Try `--voice 1` (or another index shown by `--list-voices`) — or switch to `--backend fakeyou` for the real anime voice |
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
