#!/usr/bin/env bash
# ============================================================================
# Start the Fish-Speech TTS sidecar for the KIF Pokedex Voice Over mod.
#
# First-run flow (fully plug-and-play):
#   1.  If python3 is not on PATH, this script invokes install_python.sh,
#       which uses Homebrew (macOS) or your distro's package manager (Linux)
#       to install Python 3.10+.
#   1b. Create an isolated virtual environment in ./venv - the Linux/macOS
#       equivalent of the Windows build's bundled embeddable Python - so the
#       mod's packages never touch your system Python.
#   2.  If the fish-speech Python deps + model are not yet installed, this
#       script invokes setup.py to install them and download the model.
#   3.  Then it starts server.py.
#
# Subsequent runs detect the .installed marker and skip straight to the
# server.  Keep the terminal open while you play.  Ctrl+C to stop.
# ============================================================================

set -uo pipefail

cd "$(dirname "$0")"

# ---------------------------------------------------------------------------
# Step 1: locate a system Python to bootstrap the virtual environment from.
#
# Order of preference:
#   1. POKEDEX_VO_PYTHON env var  (user override)
#   2. python3
#   3. python
#
# If none resolve, run install_python.sh and try again.
# ---------------------------------------------------------------------------
find_python() {
    if [[ -n "${POKEDEX_VO_PYTHON:-}" ]]; then
        PYCMD="$POKEDEX_VO_PYTHON"
    elif command -v python3 >/dev/null 2>&1; then
        PYCMD=python3
    elif command -v python >/dev/null 2>&1; then
        PYCMD=python
    else
        PYCMD=""
    fi
}

find_python
if [[ -z "$PYCMD" ]]; then
    echo
    echo "Python is not installed - launching the one-time installer now."
    echo
    if ! bash "./install_python.sh"; then
        echo
        echo "ERROR: Python install failed.  Install Python 3.10+ manually" >&2
        echo "from https://www.python.org/downloads/ and re-run this script." >&2
        exit 1
    fi
    find_python
    if [[ -z "$PYCMD" ]]; then
        echo "ERROR: Python install reported success but python3 is still not" >&2
        echo "on PATH.  Open a new terminal and try again." >&2
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# Step 1b: create an isolated virtual environment for the mod.
#
# Everything the mod installs (torch, fish-speech deps) lands in ./venv and
# never touches your system Python - this mirrors the Windows build's bundled
# embeddable Python.  It also lets setup.py's slim pass safely uninstall
# non-runtime packages, and sidesteps PEP 668 ("externally-managed") pip
# blocks on modern distros (Debian / Ubuntu / Fedora).
# ---------------------------------------------------------------------------
VENV_DIR="venv"
VENV_PY="$VENV_DIR/bin/python"
VENV_CREATED=0
if [[ ! -x "$VENV_PY" ]]; then
    echo "Creating isolated Python environment in ./$VENV_DIR ..."
    if ! "$PYCMD" -m venv "$VENV_DIR"; then
        echo >&2
        echo "ERROR: could not create a virtualenv with \"$PYCMD -m venv\"." >&2
        echo "On Debian/Ubuntu install the venv module first:" >&2
        echo "    sudo apt-get install -y python3-venv" >&2
        echo "then re-run this script." >&2
        exit 1
    fi
    VENV_CREATED=1
fi
# Make sure pip exists inside the venv (a few distros ship venv without it).
if ! "$VENV_PY" -m pip --version >/dev/null 2>&1; then
    "$VENV_PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
fi
# From here on, use the venv's interpreter for everything.
PYCMD="$VENV_PY"

# ---------------------------------------------------------------------------
# Step 2: first-run install of torch + fish-speech + model weights.
# ---------------------------------------------------------------------------
INSTALL_MARKER=".installed"

# A freshly created venv has no deps yet, so force setup to run even if a
# stale marker from a previous (system-Python) install is lying around.
if [[ "$VENV_CREATED" == "1" ]]; then
    rm -f "$INSTALL_MARKER"
fi

if [[ ! -f "$INSTALL_MARKER" ]]; then
    echo
    echo "=== First-run setup ==="
    echo "Installing Fish-Speech and downloading the voice-clone model."
    echo "This is a one-time step.  Re-runs skip straight to the server."
    echo "The model weights are ~1.4 GB - the download takes a few minutes."
    echo
    if "$PYCMD" setup.py; then
        touch "$INSTALL_MARKER"
    else
        echo
        echo "Setup failed.  Re-run this script after fixing the error above," >&2
        echo "or run \"$PYCMD setup.py\" manually to see the full output." >&2
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# Step 3: launch the TTS server.
# ---------------------------------------------------------------------------
echo "Starting Fish-Speech TTS server on http://127.0.0.1:7861 ..."
echo "Loading the model takes ~10-30 seconds on first launch."
echo "Leave this terminal open while you play.  Ctrl+C to stop."
echo

exec "$PYCMD" server.py "$@"
