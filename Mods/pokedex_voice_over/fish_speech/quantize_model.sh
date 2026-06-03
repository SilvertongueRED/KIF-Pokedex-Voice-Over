#!/usr/bin/env bash
# ============================================================================
# quantize_model.sh - build a faster, smaller copy of the voice model
#                     (Linux/macOS counterpart of Quantize_Model.bat).
#
# Produces an int8 (default) or int4 quantized copy of the text->semantic model
# under checkpoints/fish-speech-1.5-int8/ (or -int4-g128-q/).  The server uses
# it automatically on the next launch (--quant auto) and verifies a real
# generation first, falling back to fp16 if it can't run here - so this can
# never break narration.
#
#   int8  recommended: ~1.3-1.8x faster decode, ~half the weights on disk,
#         negligible quality change; runs on CPU.
#   int4  more aggressive; needs an NVIDIA GPU to BUILD.
#
# Usage:   ./quantize_model.sh          # int8
#          ./quantize_model.sh int4     # int4
#
# Run AFTER first-run setup has completed.  To revert to fp16, delete the new
# checkpoints/fish-speech-1.5-int8 (or -int4-g128-q) folder.
# ============================================================================
set -uo pipefail
cd "$(dirname "$0")"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

MODE="${1:-int8}"

# Prefer the mod's own venv (created by start_tts_server.sh), then overrides.
if [[ -n "${POKEDEX_VO_PYTHON:-}" ]]; then
    PYCMD="$POKEDEX_VO_PYTHON"
elif [[ -x "venv/bin/python" ]]; then
    PYCMD="venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYCMD=python3
else
    echo "ERROR: no Python found. Run ./start_tts_server.sh once first." >&2
    exit 1
fi

echo "==========================================================="
echo " Quantizing the voice model to ${MODE} (one-time)."
echo " This loads the full model and can take a few minutes."
echo "==========================================================="
"$PYCMD" setup.py --quantize "$MODE" --skip-install --skip-download --skip-smoke
RC=$?

echo
if [[ "$RC" -eq 0 ]]; then
    echo "Done. The server will use the ${MODE} model automatically next launch."
    echo "Delete checkpoints/fish-speech-1.5-${MODE}* to revert to fp16."
else
    echo "Quantization exited with code ${RC} - see the messages above."
    echo "The mod will keep using the fp16 model."
fi
exit "$RC"
