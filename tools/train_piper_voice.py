#!/usr/bin/env python3
"""
train_piper_voice.py — Prepare LJSpeech training data for a custom Piper TTS voice model

This script extracts audio–text pairs from your existing KIF Pokédex voice-over files
and organises them into the LJSpeech dataset format expected by Piper's training pipeline.
Once the dataset is ready you can fine-tune (or train from scratch) a Piper voice model
that sounds like Dexter — the Pokédex narrator — and use it as an offline TTS fallback
for fused entries that have no pre-recorded audio.

Usage
-----
    python tools/train_piper_voice.py --game-root /path/to/KIF

    # Custom output directory
    python tools/train_piper_voice.py --game-root /path/to/KIF --output-dir /path/to/dataset

    # Use 16 kHz sample rate (required by some Piper base models)
    python tools/train_piper_voice.py --game-root /path/to/KIF --sample-rate 16000

Prerequisites
-------------
    * Python 3.8+
    * ffmpeg on PATH (for OGG → WAV conversion)
      https://ffmpeg.org/download.html

Output structure
----------------
    tools/piper_training_data/
    ├── wavs/
    │   ├── dex_00001.wav
    │   ├── dex_00002.wav
    │   └── ...
    └── metadata.csv     (LJSpeech format: filename|text|normalized_text)

Next steps (printed at the end of the script)
----------------------------------------------
    1.  pip install piper-train
    2.  Preprocess the dataset with piper_train.preprocess
    3.  Train on Google Colab (free GPU) or locally
    4.  Export to ONNX: python -m piper_train.export_onnx ...
    5.  Copy model.onnx + model.onnx.json to Mods/pokedex_voice_over/piper/
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    """Light normalisation suitable for TTS training transcripts."""
    text = text.strip()
    # Collapse internal whitespace
    text = re.sub(r"\s+", " ", text)
    return text


def _ffmpeg_available() -> bool:
    """Return True when ffmpeg can be found on PATH."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def convert_ogg_to_wav(src_ogg: Path, dst_wav: Path, sample_rate: int = 22050) -> bool:
    """Convert *src_ogg* to a mono WAV at *sample_rate* Hz using ffmpeg.

    Returns True on success, False on failure.
    """
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(src_ogg),
                "-ar", str(sample_rate),
                "-ac", "1",
                str(dst_wav),
            ],
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            log.debug("ffmpeg stderr: %s", result.stderr.decode(errors="replace"))
        return result.returncode == 0 and dst_wav.exists()
    except FileNotFoundError:
        log.error("ffmpeg not found — install ffmpeg and add it to PATH")
        return False
    except subprocess.TimeoutExpired:
        log.warning("ffmpeg timed out converting %s", src_ogg.name)
        return False


def is_fusion_stem(stem: str) -> bool:
    """Return True when *stem* (e.g. 'dex_BULBASAUR_CHARMANDER') is a fusion entry.

    Fusion stems look like  dex_SPECIES1_SPECIES2  (optionally followed by _vN).
    Single-species stems look like  dex_BULBASAUR  or  dex_BULBASAUR_v2.
    Strategy: strip the 'dex_' prefix and any trailing '_vN', then split on '_'.
    Two or more remaining parts that are all alphabetic indicate a fusion.
    """
    base = stem
    # Remove dex_ prefix
    if base.startswith("dex_"):
        base = base[4:]
    # Remove trailing version suffix (_v2, _v3, …)
    base = re.sub(r"_v\d+$", "", base)
    parts = base.split("_")
    # A fusion has ≥ 2 parts; each part should be entirely alphabetic (species names)
    if len(parts) < 2:
        return False
    return all(p.isalpha() for p in parts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None) -> int:  # noqa: C901  (acceptable complexity for a standalone script)
    parser = argparse.ArgumentParser(
        description=(
            "Prepare a Piper TTS training dataset from KIF Pokédex audio files."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage\n")[0].strip(),
    )
    parser.add_argument(
        "--game-root",
        required=True,
        metavar="DIR",
        help="Root directory of your KIF game installation.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        metavar="DIR",
        help=(
            "Where to write the LJSpeech dataset "
            "(default: tools/piper_training_data/ next to this script)."
        ),
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=22050,
        metavar="HZ",
        help=(
            "Target WAV sample rate in Hz.  "
            "Use 22050 for most Piper models, 16000 for low-resource models.  "
            "(default: 22050)"
        ),
    )
    parser.add_argument(
        "--include-fusions",
        action="store_true",
        default=False,
        help=(
            "Also include fusion entries in the training dataset.  "
            "Disabled by default because fusion text is randomly generated "
            "at runtime and may not match the audio exactly."
        ),
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Show debug-level log messages.",
    )
    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # ------------------------------------------------------------------
    # Locate required files
    # ------------------------------------------------------------------
    game_root = Path(args.game_root).resolve()
    audio_dir = game_root / "Audio" / "SE" / "Pokedex"
    entry_map_path = audio_dir / "dex_entry_map.json"

    if not game_root.exists():
        log.error("Game root not found: %s", game_root)
        return 1

    if not audio_dir.exists():
        log.error(
            "Audio directory not found: %s\n"
            "Make sure you have generated audio files with tools/generate_voices.py first.",
            audio_dir,
        )
        return 1

    if not entry_map_path.exists():
        log.error(
            "dex_entry_map.json not found: %s\n"
            "Run tools/generate_voices.py to generate it first.",
            entry_map_path,
        )
        return 1

    if not _ffmpeg_available():
        log.error(
            "ffmpeg is not available on PATH.\n"
            "Install ffmpeg and make sure it is on your PATH:\n"
            "  https://ffmpeg.org/download.html"
        )
        return 1

    # ------------------------------------------------------------------
    # Load entry map
    # ------------------------------------------------------------------
    with open(entry_map_path, encoding="utf-8") as f:
        entry_map: dict[str, str] = json.load(f)
    log.info("Loaded %d entries from %s", len(entry_map), entry_map_path.name)

    # ------------------------------------------------------------------
    # Set up output directory
    # ------------------------------------------------------------------
    script_dir = Path(__file__).parent.resolve()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else script_dir / "piper_training_data"
    wavs_dir = output_dir / "wavs"
    wavs_dir.mkdir(parents=True, exist_ok=True)
    log.info("Dataset output directory: %s", output_dir)

    # ------------------------------------------------------------------
    # Collect audio–text pairs
    # ------------------------------------------------------------------
    pairs: list[tuple[str, Path, str]] = []
    skipped_no_audio = 0
    skipped_fusion = 0

    for stem, text in sorted(entry_map.items()):
        if not text or not text.strip():
            continue

        # Optionally skip fusion entries
        if not args.include_fusions and is_fusion_stem(stem):
            skipped_fusion += 1
            continue

        ogg_path = audio_dir / f"{stem}.ogg"
        if not ogg_path.exists():
            log.debug("OGG not found: %s", ogg_path)
            skipped_no_audio += 1
            continue

        pairs.append((stem, ogg_path, text))

    log.info(
        "Found %d entries to process  "
        "(%d skipped: %d fusions excluded, %d missing audio)",
        len(pairs),
        skipped_fusion + skipped_no_audio,
        skipped_fusion,
        skipped_no_audio,
    )

    if not pairs:
        log.error(
            "No valid audio–text pairs found.\n"
            "Check that dex_entry_map.json and .ogg files exist in:\n"
            "  %s",
            audio_dir,
        )
        return 1

    # ------------------------------------------------------------------
    # Convert to WAV and build metadata
    # ------------------------------------------------------------------
    metadata_rows: list[tuple[str, str, str]] = []
    converted = 0
    failed = 0

    for idx, (stem, ogg_path, text) in enumerate(pairs, start=1):
        wav_name = f"dex_{idx:05d}"
        dst_wav = wavs_dir / f"{wav_name}.wav"

        log.info("[%d/%d] Converting %s …", idx, len(pairs), ogg_path.name)

        if convert_ogg_to_wav(ogg_path, dst_wav, args.sample_rate):
            norm = normalize_text(text)
            metadata_rows.append((wav_name, text, norm))
            converted += 1
        else:
            log.warning("Failed to convert %s — skipping", ogg_path.name)
            failed += 1

    # ------------------------------------------------------------------
    # Write LJSpeech metadata.csv
    # ------------------------------------------------------------------
    metadata_path = output_dir / "metadata.csv"
    with open(metadata_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="|")
        for row in metadata_rows:
            writer.writerow(row)

    # ------------------------------------------------------------------
    # Summary + next-steps instructions
    # ------------------------------------------------------------------
    print()
    print("=" * 65)
    print(f"  Dataset prepared: {converted} files  ({failed} failed)")
    print(f"  WAV files:  {wavs_dir}")
    print(f"  Metadata:   {metadata_path}")
    print("=" * 65)

    if converted < 100:
        print()
        print("  ⚠  WARNING: fewer than 100 training samples.")
        print("     Piper benefits from at least 1 hour of clean audio")
        print("     (~1 000–2 000 short utterances).  With fewer samples")
        print("     the trained voice may sound robotic or unstable.")

    print()
    print("NEXT STEPS — TRAINING A PIPER VOICE MODEL")
    print("-" * 65)
    print()
    print("1. Install Piper training dependencies:")
    print("     pip install piper-train")
    print()
    print("2. Preprocess the dataset:")
    print(f'     python -m piper_train.preprocess \\')
    print(f'       --language en-us \\')
    print(f'       --input-dir "{output_dir}" \\')
    print(f'       --output-dir "{output_dir / "preprocessed"}" \\')
    print(f'       --sample-rate {args.sample_rate}')
    print()
    print("3. Train (recommended: Google Colab with a free GPU):")
    print("     Colab notebook: https://github.com/rhasspy/piper/blob/master/notebooks/piper_train.ipynb")
    print()
    print("   Or locally with a GPU:")
    print(f'     python -m piper_train \\')
    print(f'       --dataset-dir "{output_dir / "preprocessed"}" \\')
    print(f'       --accelerator gpu --devices 1')
    print()
    print("4. Export the trained model to ONNX:")
    print("     python -m piper_train.export_onnx \\")
    print("       --checkpoint <path/to/checkpoint.ckpt> \\")
    print("       --output model.onnx")
    print()
    print("5. Copy the resulting files to your mod directory:")
    print("     Mods/pokedex_voice_over/piper/model.onnx")
    print("     Mods/pokedex_voice_over/piper/model.onnx.json")
    print()
    print("   The mod auto-detects Piper on the next game launch and uses")
    print("   it as a fallback for fused entries without pre-recorded audio.")
    print()
    print("See README.md for full setup and troubleshooting instructions.")
    print("=" * 65)

    return 0


if __name__ == "__main__":
    sys.exit(main())
