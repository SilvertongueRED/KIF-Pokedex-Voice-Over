# KIF Pokédex Voice Over

A mod for [Kuray's Infinite Fusion (KIF)](https://github.com/kurayamiblackheart/kurayshinyrevamp)
that reads every Pokédex entry aloud in the style of **Dexter**, the classic
robotic narrator from the original Pokémon anime — for every base Pokémon,
every custom dex entry, and every auto-generated fusion (head × body) the
game can produce.

The mod ships an offline voice-clone sidecar built on the open-source
[fish-speech](https://github.com/fishaudio/fish-speech) engine — the same
engine that powers the Fish Audio
[Pokédex Voice Over model](https://fish.audio/m/57a07a0af0954230a44d1db3adc77940/).
After a one-time first-launch install everything runs **fully offline**, with
**no API keys** and **no network calls at play time**.

---

## Install (plug-and-play, zero prerequisites)

1. **Download** the mod ZIP from the
   [Releases page](https://github.com/SilvertongueRED/KIF-Pokedex-Voice-Over/releases)
   (or via KIF's in-game Mod Browser once it's listed at
   [KIF-Mods/mods](https://github.com/KIF-Mods/mods)).
2. **Extract** so the `pokedex_voice_over/` folder lives in your KIF game's
   `Mods/` directory:

   ```
   <KIF game root>/
   └── Mods/
       └── pokedex_voice_over/
           ├── mod.json
           ├── main.rb
           └── fish_speech/   ← the offline TTS sidecar
   ```

3. **Launch KIF and open any Pokédex entry.** That's it.

That's the entire procedure — there is nothing else to install by hand.
The first time you open the Pokédex, the mod auto-spawns the Fish-Speech
sidecar in a minimised terminal window, which performs a complete one-time
first-run setup in this order:

* **Python** — Windows uses a **bundled portable Python 3.12** that lives
  entirely inside `fish_speech/python/`. The launcher extracts it from the
  official python.org embeddable ZIP — **no installer EXE, no SmartScreen
  prompt, no admin, no PATH changes, no registry edits**. Uninstall is just
  "delete the mod folder." macOS uses Homebrew; Linux uses your distro's
  package manager.
* **PyTorch** — installs the matching CPU or CUDA wheel (auto-detected).
* **Engine dependencies** — installs the small set of inference-only Python
  packages the engine needs, listed in `fish_speech/requirements-runtime.txt`.
  The Fish-Speech **engine itself is bundled with the mod** (vendored under
  `fish_speech/vendor/` — see *Offline & self-contained design* below), so
  setup never clones or downloads it from GitHub.
* **Model weights** — downloads the ~1.4 GB voice-clone model from the
  public [`fishaudio/fish-speech-1.5`](https://huggingface.co/fishaudio/fish-speech-1.5)
  Hugging Face repo. No token, no account, no API key required.

The first entry on first launch may stay silent while this runs; from the
second entry onward the voice plays normally. Every subsequent game launch
skips straight to the server — usually a 5–15 second warm-up.

If you'd rather pre-warm the install so the first entry isn't silent, run
the launcher once before opening the game:

* Windows: double-click `Mods/pokedex_voice_over/fish_speech/Start_TTS_Server.bat`
* macOS / Linux: `./Mods/pokedex_voice_over/fish_speech/start_tts_server.sh`

Leave the terminal open. When the game closes, the mod closes it for you.

---

## Features

* 🔊 Reads the full Pokédex description aloud the moment you open any entry.
* 🧬 Works for **every fused Pokémon** combination — head × body — including
  auto-generated AI fusion entries.
* 🚀 **Zero-prerequisite install.** No Python install, no pip commands, no
  manual model download — the first launch auto-installs everything per-user
  without an admin prompt.
* 🐟 **Fish-Speech voice cloning (offline, primary)** — clones the Pokédex
  narrator reference clip and reads any entry on the fly. No pre-generated
  audio files needed.
* 🗣️ **Piper TTS fallback** — if you'd rather not use the heavier
  Fish-Speech stack, drop a [Piper](https://github.com/rhasspy/piper) voice
  into `Mods/pokedex_voice_over/piper/` and the mod will use that instead.
* ⚡ **Cached output** — generated WAVs are cached by content hash in
  `Audio/SE/Pokedex/tts_cache/` so repeat visits play instantly.
* 🎵 **Music fades** while the Pokédex is open and resumes when you exit.
* 🔄 **Scroll-aware playback** — voice plays as you scroll through entries
  with the D-pad / arrow keys, not just when you open one.
* ⚙️ In-game settings (volume, enable/disable, mute music, re-read on page
  return, prefer Fish-Speech, prefer Piper) via the KIF Mod Manager and
  Stonewall's Mod Settings.
* 🔐 **No personal API keys.** The model comes from a public Hugging Face
  repo; no Fish Audio / FakeYou login is required.

---

## In-game settings

Settings are exposed both through **KIF's Mod Manager** (Title Screen → Mod
Manager → Installed Mods → Pokédex Voice Over → Settings) and through
**Stonewall's Mod Settings** if you have it installed (Options → Mod
Settings → Interface → Pokédex Voice Over).

| Setting | Default | Description |
|---|---|---|
| Enable Voice Over | On | Master on/off switch. |
| Voice Volume | 80 | Playback volume (0–100). |
| Mute Music in Pokédex | On | Fade background music while the Pokédex is open. |
| Re-read on Page Return | Off | Re-play the voice when navigating back to the description page. |
| Fish-Speech Voice (Primary) | On | Use the offline voice-clone server as the primary voice. |
| Auto-Start Fish-Speech Server | On | Let the mod spawn the sidecar on demand instead of requiring a manual launch. |
| TTS Fallback (Piper) | On | Use Piper if Fish-Speech is unavailable. |

---

## Requirements

| Requirement | Notes |
|---|---|
| **Kuray's Infinite Fusion** | The Kuray fork with Mod Manager support (v0.9.4+). |
| **Windows / macOS / Linux** | Anything that runs KIF works. Windows uses a bundled portable Python; macOS/Linux install Python via Homebrew or your distro. |
| **~3 GB disk** | ~1.4 GB for the model weights + ~1.5 GB for the PyTorch wheels. Both go in the mod folder, deletable any time. |
| **GPU (optional)** | CUDA NVIDIA GPU gets you ~0.5 s per entry. A modern CPU gets you ~3–8 s; the Pokémon cry masks most of that. |
| **Internet (one time)** | Only needed during first-launch setup (python.org + PyPI + Hugging Face). After that everything runs offline. |

---

## How it works

The mod is a single Ruby script (`main.rb`) that hooks KIF's Pokédex scene
to capture the exact text being displayed (including auto-generated fusion
entries containing the literal `POKENAME` placeholder, which it resolves to
the actual fused name). It then calls a tiny local HTTP server in
`fish_speech/` (`POST 127.0.0.1:7861/tts`) and plays the returned WAV
through RPGMaker's audio system.

The server is `fish_speech/server.py` — a thin wrapper around the
[fish-speech](https://github.com/fishaudio/fish-speech) **1.5** engine, which
is **vendored into the mod** at `fish_speech/vendor/` and added to Python's
import path at startup. It keeps the model loaded between requests so per-entry
latency is sub-second on GPU. The model and its tokenizer are downloaded once
from the public
[`fishaudio/fish-speech-1.5`](https://huggingface.co/fishaudio/fish-speech-1.5)
Hugging Face repo — no token, no account, no API key required.

If Fish-Speech is unavailable (e.g. the user declined the Python install
prompt or the GPU OOM'd) the mod falls through to its built-in **Piper**
path. Piper is the small offline neural TTS used by Home Assistant; drop
the matching `piper.exe` (or `piper` binary) and a `*.onnx` voice model
into `Mods/pokedex_voice_over/piper/` and the mod picks it up automatically.

---

## Offline & self-contained design

The Fish-Speech 1.5 engine source is **vendored** inside the mod at
`fish_speech/vendor/` (a trimmed, pinned copy of the upstream code). `server.py`
puts that folder first on Python's import path, so the mod always runs the exact
engine it shipped with — it never `pip install`s fish-speech from GitHub or
PyPI. First-run setup therefore only installs (a) the right PyTorch wheel and
(b) the inference-only leaf packages in `fish_speech/requirements-runtime.txt`.
After that one-time download, nothing reaches the network at play time.

> **Maintainer note — keep the engine on 1.5.** fish-speech 2.x reorganised the
> package and removed the firefly VQ-GAN module
> (`fish_speech.models.vqgan.modules.fsq.DownsampleFiniteScalarQuantize`) that
> the 1.5 checkpoint instantiates, so a 2.x engine *cannot* load these weights.
> Don't bump the vendored copy to 2.x without also rewriting `server.py` and
> swapping the model checkpoint. To refresh the vendored source, copy
> `fish_speech/` and `tools/` from the upstream `v1.5.0` tag into
> `fish_speech/vendor/`, keeping the `.project-root` marker and the
> per-directory `__init__.py` files.

---

## Uninstalling

Delete the `Mods/pokedex_voice_over/` folder. That's all — the bundled
Python interpreter, every pip-installed package, the model weights, and the
caches all live inside that folder, so there's no system cleanup left over.

(On macOS / Linux, Python was installed via Homebrew or your distro's
package manager and stays installed in case other tools need it; remove
it via your usual package-manager uninstall flow if you want it gone.)

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Pokédex entries are silent | Open `Mods/pokedex_voice_over/debug.log` — it records every hook fire, file check, and TTS call. On a fresh install the most likely cause is "first-launch model download still running"; wait a couple of minutes on first run. |
| Bundled Python extraction fails | Check `fish_speech/install_python.bat`'s output. If the network is blocked, manually download `python-3.12.7-embed-amd64.zip` from <https://www.python.org/ftp/python/3.12.7/> and drop it into `fish_speech/_embed/`, then re-run `Start_TTS_Server.bat`. |
| pip bootstrap fails inside the bundled Python | Delete `fish_speech/python/` and `fish_speech/_embed/get-pip.py`, then re-run `Start_TTS_Server.bat`. The launcher will redownload and rebootstrap. |
| First entry on first launch plays nothing | The model is downloading. Either pre-warm the server by double-clicking `Start_TTS_Server.bat` once before launching the game, or just wait for the second entry. |
| `CUDA out of memory` in the sidecar window | Run `Start_TTS_Server.bat --device cpu` (or edit the .bat to add `--device cpu`). |
| Voice sounds wrong | Replace `fish_speech/reference/voice.wav` with your own 15–20 s reference clip (see `fish_speech/reference/README.txt`). |
| I want a clean reinstall | Delete `Mods/pokedex_voice_over/fish_speech/.installed` and `Mods/pokedex_voice_over/fish_speech/checkpoints/`, then relaunch. |

---

## Credits & License

* **fish-speech** by Fish Audio — Apache 2.0
  ([github.com/fishaudio/fish-speech](https://github.com/fishaudio/fish-speech))
* The bundled **reference voice clip** was generated from the public Fish
  Audio [Pokédex Voice Over model](https://fish.audio/m/57a07a0af0954230a44d1db3adc77940/)
  and is included only as the voice-clone target. You can replace it with
  any other clip you prefer.
* **Piper** by Rhasspy — MIT
  ([github.com/rhasspy/piper](https://github.com/rhasspy/piper))
* **Pokémon Infinite Fusion** by Chardub/Frogman
* **Kuray's Infinite Fusion** by kurayamiblackheart and contributors
* This mod is MIT-licensed. Pokémon trademarks belong to Nintendo / Game
  Freak — this is a fan project, not affiliated with or endorsed by them.
