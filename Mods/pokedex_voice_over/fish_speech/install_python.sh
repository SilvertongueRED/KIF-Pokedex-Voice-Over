#!/usr/bin/env bash
# ============================================================================
# install_python.sh - install Python 3.10+ if missing (macOS / Linux).
#
# macOS: uses Homebrew if present; otherwise prints clear install steps.
# Linux: detects apt / dnf / pacman and runs the appropriate command (with
#        sudo); if no supported package manager is detected, prints steps.
#
# Returns 0 if python3 is available on PATH afterwards, non-zero otherwise.
# This script is idempotent: re-running it is safe.
# ============================================================================

set -u

if command -v python3 >/dev/null 2>&1; then
    echo "Python already installed: $(python3 --version)"
    exit 0
fi

uname_s="$(uname -s 2>/dev/null || echo unknown)"

echo
echo "=== Installing Python 3 (one-time setup) ==="
echo

case "$uname_s" in
    Darwin)
        if command -v brew >/dev/null 2>&1; then
            echo "Installing python@3.12 via Homebrew..."
            brew install python@3.12 || {
                echo "ERROR: brew install python@3.12 failed." >&2
                exit 2
            }
            brew link --force python@3.12 >/dev/null 2>&1 || true
        else
            echo "Homebrew is not installed.  Install it first:" >&2
            echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"' >&2
            echo "then re-run this script.  Or install Python directly from" >&2
            echo "  https://www.python.org/downloads/macos/" >&2
            exit 2
        fi
        ;;
    Linux)
        if   command -v apt-get >/dev/null 2>&1; then
            echo "Installing python3 + pip via apt (sudo)..."
            sudo apt-get update && sudo apt-get install -y python3 python3-pip python3-venv || exit 2
        elif command -v dnf >/dev/null 2>&1; then
            echo "Installing python3 + pip via dnf (sudo)..."
            sudo dnf install -y python3 python3-pip || exit 2
        elif command -v pacman >/dev/null 2>&1; then
            echo "Installing python via pacman (sudo)..."
            sudo pacman -S --noconfirm python python-pip || exit 2
        elif command -v zypper >/dev/null 2>&1; then
            echo "Installing python3 + pip via zypper (sudo)..."
            sudo zypper install -y python3 python3-pip || exit 2
        else
            echo "No supported package manager found (apt/dnf/pacman/zypper)." >&2
            echo "Please install Python 3.10+ manually using your distro's tools." >&2
            exit 2
        fi
        ;;
    *)
        echo "Unsupported platform: $uname_s" >&2
        echo "Please install Python 3.10+ manually from" >&2
        echo "  https://www.python.org/downloads/" >&2
        exit 2
        ;;
esac

if command -v python3 >/dev/null 2>&1; then
    echo
    echo "=== Python installed successfully ==="
    python3 --version
    exit 0
fi

echo "ERROR: Python install reported success but python3 is still not on PATH." >&2
echo "Please open a new terminal and try again." >&2
exit 3
