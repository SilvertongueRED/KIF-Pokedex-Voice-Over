#!/usr/bin/env python3
"""
Pokédex Voice-Over Generator for KIF (Kuray's Infinite Fusion)
==============================================================

Generates TTS audio files for every Pokédex entry in your KIF game,
voiced in the style of Dexter — the classic Pokémon anime Pokédex narrator.

The script reads Pokédex description text from the game's PBS data files,
synthesises speech with pyttsx3 (offline) or gTTS (online), and applies
post-processing effects via pydub to produce the characteristic robotic,
band-limited quality of the original Dexter voice.

Output files are saved as OGG Vorbis to:
  <game-dir>/Mods/pokedex_voice_over/Audio/

Naming convention
-----------------
  dex_BULBASAUR.ogg               — regular Pokémon
  dex_BULBASAUR_CHARMANDER.ogg    — fused Pokémon (head_body)

Usage
-----
  # Generate all regular Pokémon entries (creates files, skips existing ones)
  python generate_voices.py --game-dir /path/to/KIF

  # Overwrite any previously generated files
  python generate_voices.py --game-dir /path/to/KIF --overwrite

  # Generate for a single species only
  python generate_voices.py --game-dir /path/to/KIF --species BULBASAUR

  # Also generate fusion-specific entries (if your KIF build has them)
  python generate_voices.py --game-dir /path/to/KIF --fusions

  # Use Google TTS instead of the default offline pyttsx3 backend
  python generate_voices.py --game-dir /path/to/KIF --backend gtts

  # List available pyttsx3 voices and exit (useful for --voice selection)
  python generate_voices.py --list-voices

Requirements
------------
  pip install -r requirements.txt

  On Windows, pyttsx3 uses the built-in SAPI voices (e.g. "Microsoft David
  Desktop") which sound convincingly retro.  On macOS, it uses "Alex" or
  similar.  On Linux, it uses eSpeak.

  ffmpeg must be installed and on your PATH for pydub to export OGG files.
"""

import argparse
import logging
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Optional dependency detection
# ---------------------------------------------------------------------------

try:
    import pyttsx3
    HAS_PYTTSX3 = True
except ImportError:
    HAS_PYTTSX3 = False

try:
    from gtts import gTTS
    HAS_GTTS = True
except ImportError:
    HAS_GTTS = False

try:
    from pydub import AudioSegment
    from pydub.effects import normalize
    HAS_PYDUB = True
except ImportError:
    HAS_PYDUB = False

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PBS parsing
# ---------------------------------------------------------------------------

# Pokemon Essentials PBS pokemon.txt comes in two flavours:
#   Old (v18/v19 — used by vanilla PIF):  sections start with a numeric [001]
#   New (v20+):                            sections start with [INTERNAL_NAME]
# Both store the description in a  PokedexEntry=  key.

_PBS_SECTION_RE = re.compile(r"^\[(\w+)\]", re.MULTILINE)
_INTERNAL_NAME_RE = re.compile(r"^InternalName\s*=\s*(.+)$", re.MULTILINE)
_DEX_ENTRY_RE = re.compile(
    r"^PokedexEntry\s*=\s*(.+?)(?=\n\w|\Z)", re.MULTILINE | re.DOTALL
)


def _clean_entry_text(raw: str) -> str:
    """Normalise a raw Pokédex entry string for TTS consumption."""
    # Replace escaped newlines with a space
    text = raw.replace("\\n", " ")
    # Remove any remaining backslash escapes
    text = re.sub(r"\\.", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # Strip trailing punctuation inconsistencies
    if text and text[-1] not in ".!?":
        text += "."
    return text


def parse_pbs_pokemon(pbs_file: Path) -> dict:
    """
    Return a {SPECIES_NAME: entry_text} dict parsed from a PBS pokemon.txt.
    """
    entries: dict = {}

    if not pbs_file.is_file():
        log.warning("PBS file not found: %s", pbs_file)
        return entries

    try:
        content = pbs_file.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.error("Cannot read %s: %s", pbs_file, exc)
        return entries

    # Split into per-Pokémon sections
    section_starts = [m.start() for m in _PBS_SECTION_RE.finditer(content)]
    for i, start in enumerate(section_starts):
        end = section_starts[i + 1] if i + 1 < len(section_starts) else len(content)
        section = content[start:end]

        # Determine the internal (species) name.
        # New-style PBS: the section header itself IS the internal name.
        header_match = _PBS_SECTION_RE.match(section)
        if not header_match:
            continue
        header_val = header_match.group(1)

        # Old-style PBS uses numeric section IDs, so we need InternalName=
        if header_val.isdigit():
            name_match = _INTERNAL_NAME_RE.search(section)
            if not name_match:
                continue
            species_name = name_match.group(1).strip().upper()
        else:
            species_name = header_val.upper()

        # Extract the Pokédex entry text
        entry_match = _DEX_ENTRY_RE.search(section)
        if not entry_match:
            continue

        raw_text = entry_match.group(1)
        entry_text = _clean_entry_text(raw_text)
        if entry_text:
            entries[species_name] = entry_text

    log.info("Parsed %d Pokédex entries from %s", len(entries), pbs_file.name)
    return entries


def parse_fusion_entries(game_dir: Path) -> dict:
    """
    Return a {(SPECIES1, SPECIES2): entry_text} dict from KIF fusion PBS data.

    KIF may store custom fusion Pokédex entries in various files; we attempt
    every known location.  Returns an empty dict if none are found.
    """
    fusion_entries: dict = {}

    candidate_files = [
        game_dir / "PBS" / "fusions.txt",
        game_dir / "PBS" / "fusion_dex.txt",
        game_dir / "PBS" / "fusionmon.txt",
    ]

    for path in candidate_files:
        if not path.is_file():
            continue
        log.info("Parsing fusion entries from %s", path)
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.warning("Cannot read %s: %s", path, exc)
            continue

        # Expected section format:
        #   [HEAD_BODY]
        #   PokedexEntry=...
        section_starts = [m.start() for m in _PBS_SECTION_RE.finditer(content)]
        for i, start in enumerate(section_starts):
            end = section_starts[i + 1] if i + 1 < len(section_starts) else len(content)
            section = content[start:end]

            header_match = _PBS_SECTION_RE.match(section)
            if not header_match:
                continue
            header_val = header_match.group(1).upper()

            # Expect a HEAD_BODY pair
            if "_" not in header_val:
                continue
            parts = header_val.split("_", 1)
            species1, species2 = parts[0], parts[1]

            entry_match = _DEX_ENTRY_RE.search(section)
            if not entry_match:
                continue
            raw_text = entry_match.group(1)
            entry_text = _clean_entry_text(raw_text)
            if entry_text:
                fusion_entries[(species1, species2)] = entry_text

    return fusion_entries


# ---------------------------------------------------------------------------
# TTS generation
# ---------------------------------------------------------------------------

def _generate_pyttsx3(text: str, out_wav: Path, speech_rate: int = 130,
                      voice_index: int = 0) -> None:
    """Synthesise *text* with pyttsx3 and save as WAV to *out_wav*.

    *speech_rate* is the words-per-minute speaking speed passed to SAPI.
    130 wpm is slightly slower than natural speech, which gives the measured,
    deliberate cadence associated with the classic Pokédex narrator.
    """
    engine = pyttsx3.init()
    voices = engine.getProperty("voices")
    if voices and voice_index < len(voices):
        engine.setProperty("voice", voices[voice_index].id)
    engine.setProperty("rate", speech_rate)
    engine.setProperty("volume", 1.0)
    engine.save_to_file(text, str(out_wav))
    engine.runAndWait()
    engine.stop()


def _generate_gtts(text: str, out_mp3: Path) -> None:
    """Synthesise *text* with gTTS and save as MP3 to *out_mp3*."""
    tts = gTTS(text=text, lang="en", slow=False)
    tts.save(str(out_mp3))


def _apply_dexter_effect(audio: "AudioSegment") -> "AudioSegment":
    """
    Apply post-processing to make the voice sound like Dexter from the
    classic Pokémon anime:

    1. High-pass filter  — removes bass, gives a thin, broadcast-radio quality
    2. Low-pass filter   — softens harsh high frequencies
    3. Echo overlay      — adds the characteristic resonant 'robot' quality
    4. Normalise         — ensures consistent loudness
    """
    # Band-pass simulation: cut below 350 Hz and above 4 000 Hz
    audio = audio.high_pass_filter(350)
    audio = audio.low_pass_filter(4000)

    # Subtle echo: quieter copy delayed by ~35 ms gives robotic resonance
    echo = audio - 13          # 13 dB quieter
    audio = audio.overlay(echo, position=35)

    audio = normalize(audio)
    return audio


def generate_voice_file(text: str, dest_ogg: Path, backend: str = "auto",
                        voice_index: int = 0) -> bool:
    """
    Generate a single voiced OGG file at *dest_ogg*.

    Returns True on success, False on failure.
    """
    dest_ogg = Path(dest_ogg)
    dest_ogg.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)

        # ---- step 1: raw TTS -----------------------------------------------
        raw_path: Path | None = None
        if backend in ("pyttsx3", "auto") and HAS_PYTTSX3:
            raw_path = tmp / "raw.wav"
            try:
                _generate_pyttsx3(text, raw_path, voice_index=voice_index)
            except Exception as exc:
                log.debug("pyttsx3 failed: %s", exc)
                raw_path = None

        if raw_path is None and backend in ("gtts", "auto") and HAS_GTTS:
            raw_path = tmp / "raw.mp3"
            try:
                _generate_gtts(text, raw_path)
            except Exception as exc:
                log.debug("gTTS failed: %s", exc)
                raw_path = None

        if raw_path is None or not raw_path.exists():
            log.error(
                "No TTS backend produced output.  "
                "Install pyttsx3 (offline) or gTTS (online)."
            )
            return False

        # ---- step 2: audio effects + export --------------------------------
        if HAS_PYDUB:
            try:
                audio = AudioSegment.from_file(str(raw_path))
                audio = _apply_dexter_effect(audio)
                audio.export(
                    str(dest_ogg),
                    format="ogg",
                    parameters=["-q:a", "5"],
                )
            except Exception as exc:
                log.error("pydub processing failed: %s", exc)
                # Fall back to raw copy
                shutil.copy(str(raw_path), str(dest_ogg.with_suffix(raw_path.suffix)))
                return True
        else:
            # No pydub — just save the raw audio (no Dexter effects)
            fallback = dest_ogg.with_suffix(raw_path.suffix)
            shutil.copy(str(raw_path), str(fallback))
            log.warning(
                "pydub not installed — saved raw TTS as %s (no Dexter effects).",
                fallback.name,
            )

    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def list_voices() -> int:
    if not HAS_PYTTSX3:
        print("pyttsx3 is not installed.  Run:  pip install pyttsx3")
        return 1
    engine = pyttsx3.init()
    voices = engine.getProperty("voices")
    if not voices:
        print("No voices found.")
        engine.stop()
        return 0
    print("Available pyttsx3 voices:")
    for i, v in enumerate(voices):
        print(f"  [{i}]  {v.name}")
        print(f"        id: {v.id}")
    engine.stop()
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate Pokédex voice-over audio files for the KIF mod.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--game-dir",
        metavar="PATH",
        help="Path to your KIF game root directory (contains PBS/, Mods/, etc.).",
    )
    p.add_argument(
        "--species",
        metavar="NAME",
        help="Generate a file for one species only (e.g. BULBASAUR).",
    )
    p.add_argument(
        "--fusions",
        action="store_true",
        help="Also generate voice files for fusion Pokédex entries.",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing audio files (default: skip).",
    )
    p.add_argument(
        "--backend",
        choices=["auto", "pyttsx3", "gtts"],
        default="auto",
        help="TTS backend.  'auto' tries pyttsx3 then gTTS (default: auto).",
    )
    p.add_argument(
        "--voice",
        type=int,
        default=0,
        metavar="INDEX",
        help="pyttsx3 voice index (see --list-voices).  Default: 0.",
    )
    p.add_argument(
        "--list-voices",
        action="store_true",
        help="Print available pyttsx3 voices and exit.",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show debug output.",
    )
    return p


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.verbose:
        log.setLevel(logging.DEBUG)
        logging.getLogger().setLevel(logging.DEBUG)

    # ---- list-voices shortcut -----------------------------------------------
    if args.list_voices:
        return list_voices()

    # ---- validate required args ---------------------------------------------
    if not args.game_dir:
        parser.error("--game-dir is required (unless using --list-voices).")

    game_dir = Path(args.game_dir)
    if not game_dir.is_dir():
        log.error("Game directory not found: %s", game_dir)
        return 1

    # ---- check at least one TTS backend is available ------------------------
    if not HAS_PYTTSX3 and not HAS_GTTS:
        log.error(
            "No TTS backend found.  "
            "Run:  pip install pyttsx3       (offline, recommended)\n"
            "  or: pip install gTTS          (online, requires internet)"
        )
        return 1

    if not HAS_PYDUB:
        log.warning(
            "pydub is not installed — Dexter audio effects will be skipped.\n"
            "Run:  pip install pydub\n"
            "Also ensure ffmpeg is installed and on your PATH."
        )

    # ---- resolve output directory -------------------------------------------
    output_dir = game_dir / "Mods" / "pokedex_voice_over" / "Audio"
    output_dir.mkdir(parents=True, exist_ok=True)
    log.info("Output directory: %s", output_dir)

    # ---- parse PBS ----------------------------------------------------------
    pbs_file = game_dir / "PBS" / "pokemon.txt"
    all_entries = parse_pbs_pokemon(pbs_file)

    if not all_entries:
        log.error(
            "No Pokédex entries found.  "
            "Make sure --game-dir points to the KIF game root "
            "(the folder that contains the PBS/ directory)."
        )
        return 1

    # ---- filter to single species if requested ------------------------------
    if args.species:
        key = args.species.strip().upper()
        if key not in all_entries:
            log.error("Species '%s' not found in Pokédex data.", key)
            return 1
        all_entries = {key: all_entries[key]}

    # ---- process regular Pokémon entries ------------------------------------
    generated = skipped = failed = 0

    for species_name, entry_text in sorted(all_entries.items()):
        dest = output_dir / f"dex_{species_name}.ogg"

        if not args.overwrite and dest.exists():
            log.debug("Skipping %s (already exists)", dest.name)
            skipped += 1
            continue

        log.info("Generating: %s", dest.name)
        log.debug("  Text: %s", entry_text[:80])

        ok = generate_voice_file(
            entry_text,
            dest,
            backend=args.backend,
            voice_index=args.voice,
        )
        if ok:
            generated += 1
        else:
            failed += 1

    # ---- process fusion entries (optional) ----------------------------------
    if args.fusions:
        fusion_entries = parse_fusion_entries(game_dir)
        if not fusion_entries:
            log.info(
                "No fusion Pokédex entries found.  "
                "KIF may use auto-generated fusion descriptions that are not "
                "stored in PBS files."
            )
        for (sp1, sp2), entry_text in sorted(fusion_entries.items()):
            dest = output_dir / f"dex_{sp1}_{sp2}.ogg"

            if not args.overwrite and dest.exists():
                skipped += 1
                continue

            log.info("Generating fusion: %s", dest.name)
            ok = generate_voice_file(
                entry_text,
                dest,
                backend=args.backend,
                voice_index=args.voice,
            )
            if ok:
                generated += 1
            else:
                failed += 1

    # ---- summary ------------------------------------------------------------
    print(
        f"\nDone — generated: {generated}, skipped: {skipped}, failed: {failed}"
    )
    print(f"Audio files saved to: {output_dir}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
