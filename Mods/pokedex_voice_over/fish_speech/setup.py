#!/usr/bin/env python3
# =============================================================================
# Fish-Speech offline TTS — one-shot setup script
# =============================================================================
# Run this once after installing the mod.  It:
#   1. Verifies Python >= 3.10
#   2. Detects whether CUDA is usable and installs the right torch wheels
#   3. Installs the inference leaf dependencies (the engine itself is vendored)
#   4. Downloads the fish-speech 1.5 model weights from HuggingFace
#   5. Validates the reference WAV (resamples / trims if necessary)
#   6. Runs a smoke test (synthesises one short clip)
#
# Usage:
#   python setup.py                           # interactive, full setup
#   python setup.py --reference path/to.wav   # pre-supplies the reference
#   python setup.py --skip-install            # only download model + prep ref
#   python setup.py --check                   # just verify install
# =============================================================================

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REFERENCE_DIR = HERE / "reference"
REFERENCE_WAV = REFERENCE_DIR / "voice.wav"
REFERENCE_TXT = REFERENCE_DIR / "voice.txt"
CHECKPOINT_DIR = HERE / "checkpoints" / "fish-speech-1.5"
HF_REPO = "fishaudio/fish-speech-1.5"

# Files the fish-speech 1.5 release ships.  We download these explicitly so
# we don't pull every blob in the repo (the checkpoint folder has 8+ GB of
# optional alternative formats).
HF_FILES = [
    "firefly-gan-vq-fsq-8x1024-21hz-generator.pth",
    "model.pth",
    "tokenizer.tiktoken",
    "config.json",
    "special_tokens.json",
]

REQUIRED_PYTHON = (3, 10)

# ---------------------------------------------------------------------------
# Vendored fish-speech engine (IMPORTANT)
# ---------------------------------------------------------------------------
# The fish-speech 1.5 engine source is VENDORED into the mod at ./vendor and put
# on sys.path by server.py, so setup never has to reach GitHub - the only thing
# we pip-install is the third-party leaf dependencies the inference path imports
# (requirements-runtime.txt) plus torch/torchaudio (separate, CUDA-aware).
#
# Why vendored and not pip-installed: fish-speech is package version "0.1.0" for
# the entire 1.x line and the 1.5 code is not on PyPI; installing 2.x (or @main)
# both breaks model loading (the 2.x rewrite removed
# fish_speech.models.vqgan.modules.fsq.DownsampleFiniteScalarQuantize, which the
# 1.5 checkpoint instantiates) and drags in torch==2.8.0 (a CPU wheel) that
# clobbers the CUDA build.  Vendoring pins the exact 1.5 code offline.
VENDORED_FISH_SPEECH_RELEASE = "v1.5.0"   # the fish-speech release ./vendor was copied from
VENDOR_DIR = HERE / "vendor"
REQUIREMENTS_RUNTIME = HERE / "requirements-runtime.txt"

# Put the vendored engine first on sys.path so `import fish_speech` in --check
# and in the smoke test resolves to ./vendor, never to a stray pip install.
if str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))


# ---------------------------------------------------------------------------
# Pretty printing
# ---------------------------------------------------------------------------

def info(msg: str) -> None:
    print(f"\033[36m[setup]\033[0m {msg}")

def ok(msg: str) -> None:
    print(f"\033[32m[ ok ]\033[0m {msg}")

def warn(msg: str) -> None:
    print(f"\033[33m[warn]\033[0m {msg}")

def err(msg: str) -> None:
    print(f"\033[31m[err ]\033[0m {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_python() -> None:
    if sys.version_info < REQUIRED_PYTHON:
        err(f"Python {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}+ is required "
            f"(you have {sys.version.split()[0]})")
        sys.exit(1)
    ok(f"Python {sys.version.split()[0]}")


def detect_cuda() -> bool:
    """Best-effort CUDA detection BEFORE torch is installed."""
    # 1. Check for nvidia-smi
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.check_output(["nvidia-smi", "-L"],
                                          stderr=subprocess.DEVNULL,
                                          timeout=10).decode("utf-8", errors="ignore")
            if "GPU" in out:
                ok(f"NVIDIA GPU detected: {out.strip().splitlines()[0]}")
                return True
        except Exception:
            pass
    info("No NVIDIA GPU detected — will install CPU-only torch wheels.")
    return False


def run_pip(args: list[str]) -> None:
    cmd = [sys.executable, "-m", "pip", "install"] + args
    info("$ " + " ".join(cmd))
    subprocess.check_call(cmd)


def run_pip_uninstall(packages: list[str]) -> None:
    cmd = [sys.executable, "-m", "pip", "uninstall", "-y"] + packages
    info("$ " + " ".join(cmd))
    # Uninstall must never abort setup - the package may simply be absent.
    subprocess.call(cmd)


def _module_importable(name: str) -> bool:
    """True if `import <name>` would succeed in the current interpreter."""
    import importlib.util
    return importlib.util.find_spec(name) is not None


def _torch_has_cuda() -> bool:
    """Return True if the *installed* torch reports CUDA support.

    Probed in a subprocess (rather than importing torch here) because torch may
    have just been (re)installed and importing a heavy module is unnecessary.  A
    CPU-only wheel reports torch.version.cuda == None even on a working GPU - the
    exact situation the fish-speech 2.x torch==2.8.0 pin left this install in.
    """
    code = ("import torch, sys; "
            "sys.exit(0 if getattr(torch.version, 'cuda', None) else 1)")
    try:
        return subprocess.call(
            [sys.executable, "-c", code],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0
    except Exception:
        return False


def _pip_has_distribution(dist_name: str) -> bool:
    """True if a pip-installed distribution by this name exists in site-packages."""
    try:
        from importlib.metadata import distribution, PackageNotFoundError
        try:
            distribution(dist_name)
            return True
        except PackageNotFoundError:
            return False
    except Exception:
        return False


def _vendor_is_present() -> bool:
    """Sanity-check that the vendored 1.5 engine actually shipped with the mod.

    We verify the exact file the 1.5 checkpoint depends on
    (fsq.py, holding DownsampleFiniteScalarQuantize) plus the Hydra config dir
    and the .project-root marker pyrootutils needs, so a truncated copy fails
    loudly here instead of deep inside model load.
    """
    needed = [
        VENDOR_DIR / "fish_speech" / "models" / "vqgan" / "modules" / "fsq.py",
        VENDOR_DIR / "fish_speech" / "configs" / "firefly_gan_vq.yaml",
        VENDOR_DIR / "fish_speech" / "inference_engine" / "__init__.py",
        VENDOR_DIR / ".project-root",
    ]
    return all(p.exists() for p in needed)


# ---------------------------------------------------------------------------
# Install steps
# ---------------------------------------------------------------------------

def install_torch(use_cuda: bool) -> None:
    """Install torch with the correct index URL for CPU vs. CUDA.

    Re-runs are smart: if torch + torchaudio already import AND the build has the
    CUDA support we expect, this is a fast no-op.  If a CPU-only wheel got
    installed on a CUDA machine (e.g. a previous run pulled a package that
    hard-pinned a CPU torch), we force-reinstall the CUDA wheels rather than
    silently leaving the user on CPU.
    """
    have = _module_importable("torch") and _module_importable("torchaudio")
    if have and (not use_cuda or _torch_has_cuda()):
        ok("torch + torchaudio already installed (CUDA OK) - skipping.")
        return

    force: list[str] = []
    if have and use_cuda and not _torch_has_cuda():
        warn("Installed torch is a CPU-only build but a GPU was detected - "
             "reinstalling the CUDA wheels.")
        force = ["--force-reinstall"]

    info("Installing torch...")
    if use_cuda:
        # CUDA 12.1 wheels - works for most modern NVIDIA cards (compute >= 5.0).
        # The cu121 channel tops out at torch 2.5.1, which is inside
        # fish-speech 1.5's supported range.  Users with older GPUs can re-run
        # with TORCH_INDEX_URL set.
        index = os.environ.get("TORCH_INDEX_URL",
                               "https://download.pytorch.org/whl/cu121")
    else:
        index = os.environ.get("TORCH_INDEX_URL",
                               "https://download.pytorch.org/whl/cpu")
    run_pip(["torch", "torchaudio", "--index-url", index] + force)
    ok("torch installed.")


def install_fish_speech() -> None:
    """Install the inference-only leaf dependencies for the VENDORED engine.

    fish-speech 1.5 itself is NOT pip-installed: a trimmed copy of its source
    lives under ./vendor and is put on sys.path by server.py, so no GitHub clone
    is ever required.  We only install the third-party leaf packages the
    inference path imports, listed in requirements-runtime.txt.

    Re-runs are smart: a no-op when the key leaf packages already import.
    """
    if not _vendor_is_present():
        err(f"Vendored fish-speech engine missing/incomplete under {VENDOR_DIR}. "
            f"Re-extract the mod - ./vendor must ship with it.")
        sys.exit(1)

    # A stale pip-installed fish-speech (e.g. a 2.x left by an older setup.py)
    # would sit shadowed behind the vendored copy, but it is what dragged in a
    # CPU torch and it is confusing - remove it so only ./vendor is in play.
    if _pip_has_distribution("fish-speech"):
        warn("Removing pip-installed fish-speech - the engine is now vendored "
             "under ./vendor and loaded from there.")
        run_pip_uninstall(["fish-speech"])

    # Representative leaf modules: if these all import, the runtime set is in.
    probe = ["torch", "transformers", "tiktoken", "vector_quantize_pytorch",
             "hydra", "omegaconf", "pydantic", "loguru", "soundfile",
             "torchaudio", "huggingface_hub", "lightning", "pytorch_lightning"]
    missing = [m for m in probe if not _module_importable(m)]
    if not missing:
        ok("Inference dependencies already installed - skipping.")
        return

    if not REQUIREMENTS_RUNTIME.exists():
        err(f"Missing {REQUIREMENTS_RUNTIME}")
        sys.exit(1)

    info(f"Installing inference dependencies (missing: {', '.join(missing)})...")
    run_pip(["-r", str(REQUIREMENTS_RUNTIME)])
    ok("Inference dependencies installed.")


def download_model(token: str | None) -> None:
    info(f"Downloading model weights to {CHECKPOINT_DIR}")
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        err("huggingface_hub is not installed — re-run with --skip-install removed.")
        sys.exit(1)

    for fname in HF_FILES:
        target = CHECKPOINT_DIR / fname
        if target.exists() and target.stat().st_size > 0:
            ok(f"  ✓ {fname} (cached)")
            continue
        info(f"  ⤓ {fname}")
        path = hf_hub_download(
            repo_id=HF_REPO, filename=fname,
            local_dir=str(CHECKPOINT_DIR),
            local_dir_use_symlinks=False,
            token=token,
        )
        # huggingface_hub may put the file at a nested path; normalise.
        if Path(path) != target and Path(path).exists():
            shutil.move(path, target)
    ok("Model weights ready.")


def prepare_reference(provided: Path | None) -> None:
    """Copy / resample user's reference WAV into reference/voice.wav."""
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)

    src = provided
    if src is None:
        if REFERENCE_WAV.exists():
            ok(f"Using existing reference WAV at {REFERENCE_WAV}")
        else:
            # Interactive prompt
            print()
            info("Reference voice clip required.")
            print("  Drop a 10–30 second WAV of the Pokédex narrator at:")
            print(f"    {REFERENCE_WAV}")
            print("  Then re-run setup.py.")
            print()
            print("  Optional: place a plain-text transcript of the clip at:")
            print(f"    {REFERENCE_TXT}")
            print("  This dramatically improves cloning accuracy.")
            sys.exit(0)
    else:
        if not src.exists():
            err(f"Reference file does not exist: {src}")
            sys.exit(1)
        info(f"Importing reference clip from {src}")

    # Resample / mono / 16-bit normalise the reference so fish-speech ingests it
    # cleanly regardless of source format.
    try:
        import soundfile as sf
        import numpy as np
    except ImportError:
        warn("soundfile not installed — copying reference verbatim.")
        if src and src != REFERENCE_WAV:
            shutil.copy2(src, REFERENCE_WAV)
        return

    if src:
        data, sr = sf.read(str(src), always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1)            # downmix to mono
        target_sr = 44100
        if sr != target_sr:
            # Resample with torchaudio (already a required runtime dep) so we
            # do not need librosa (numba/llvmlite/scipy, ~250 MB) at all.
            import torch, torchaudio
            wav = torch.from_numpy(np.ascontiguousarray(data)).float().unsqueeze(0)
            wav = torchaudio.functional.resample(wav, orig_freq=sr,
                                                 new_freq=target_sr)
            data = wav.squeeze(0).contiguous().numpy()
            sr = target_sr
        # Cap at 30 seconds — fish-speech doesn't benefit from longer prompts
        max_samples = sr * 30
        if len(data) > max_samples:
            warn(f"Reference > 30s — trimming to first 30s")
            data = data[:max_samples]
        sf.write(str(REFERENCE_WAV), data, sr, subtype="PCM_16")
    ok(f"Reference WAV ready: {REFERENCE_WAV}")

    if not REFERENCE_TXT.exists():
        warn("No transcript file at reference/voice.txt — cloning will still "
             "work but will be less accurate.  Optionally add a plain-text "
             "transcript matching the spoken contents.")


def smoke_test() -> None:
    info("Running smoke test — synthesising 'Hello trainer.'")
    sys.path.insert(0, str(HERE))
    # Import the server module just for its synthesise function.
    import importlib
    server_mod = importlib.import_module("server")
    server_mod.ModelHolder.load(CHECKPOINT_DIR, REFERENCE_WAV, REFERENCE_TXT)
    if not server_mod._model_state["ready"]:
        err(f"Model failed to load: {server_mod._model_state.get('load_error')}")
        sys.exit(4)
    try:
        wav = server_mod.synthesize("Hello trainer.")
    except Exception as exc:
        err(f"Synthesis failed: {exc}")
        sys.exit(5)
    out = HERE / "smoke_test.wav"
    out.write_bytes(wav)
    ok(f"Smoke test passed ({len(wav)/1024:.1f} KB) — wrote {out}")


# ---------------------------------------------------------------------------
# Post-install slimming (safe; idempotent)
# ---------------------------------------------------------------------------
# A clean install from requirements-runtime.txt no longer pulls these, but an
# env upgraded in place from an older (pre-vendoring) setup may still carry
# them.  None are imported by the inference path; librosa/numba/llvmlite go now
# that reference resampling uses torchaudio.
NON_RUNTIME_PACKAGES = [
    "librosa", "numba", "llvmlite",
    "gradio", "gradio_client", "wandb", "modelscope",
    "tensorboard", "tensorboard-data-server",
    "pandas", "pyarrow", "scikit-learn", "matplotlib",
    "silero-vad", "silero_vad", "faster-whisper", "funasr",
    "datasets", "jedi",
]


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def _site_packages() -> Path:
    base = Path(sys.executable).resolve().parent
    cand = base / "Lib" / "site-packages"
    if cand.exists():
        return cand
    found = list(base.rglob("site-packages"))
    return found[0] if found else cand


def slim_install() -> None:
    """Trim the install to the inference-only footprint.

    Idempotent and safe to run after every (re)install.  It (1) uninstalls
    packages the inference path never imports, (2) deletes torch's compile-only
    artefacts (*.lib, C++ headers, bundled tests), and (3) drops __pycache__ and
    packaged test suites.  It never touches the CUDA runtime DLLs or the model
    weights, so GPU inference and clone fidelity are unchanged.
    """
    info("Slimming the install to the inference-only footprint...")
    site = _site_packages()
    before = _dir_size(site) if site.exists() else 0

    present = [p for p in NON_RUNTIME_PACKAGES if _pip_has_distribution(p)]
    if present:
        info(f"Uninstalling {len(present)} non-runtime package(s): "
             f"{', '.join(present)}")
        run_pip_uninstall(present)

    torch_lib = site / "torch" / "lib"
    if torch_lib.exists():
        for f in torch_lib.glob("*.lib"):
            try:
                f.unlink()
            except OSError:
                pass
    # torch/include = C++ headers, torch/_C_tests = bundled C-extension tests,
    # torch/test = bundled test scripts.  All are compile/dev-only and safe.
    # NOTE: do NOT delete torch/testing.  That is the PUBLIC, importable
    # torch.testing submodule that torch's own __init__ pulls in while it is
    # importing.  Deleting it makes `import torch` abort half-way: the server
    # then sees a partially-initialised torch (CUDA reported "unavailable",
    # then "cannot import name 'nn' from partially initialized module 'torch'
    # ... circular import").  That is the regression this line caused.
    for sub in ("include", "test", "_C_tests"):
        d = site / "torch" / sub
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)

    for pyc in site.rglob("__pycache__"):
        shutil.rmtree(pyc, ignore_errors=True)
    # NOTE: we deliberately do NOT blanket-delete "<pkg>/test" / "<pkg>/tests"
    # across site-packages.  Several packages ship *importable* subpackages
    # under those names, so nuking them risks breaking an import the inference
    # path relies on.  The torch headers + .lib deleted above are by far the
    # largest win; the marginal savings here are not worth the risk.

    after = _dir_size(site) if site.exists() else 0
    if before:
        ok(f"Slimmed env: {_human(before)} -> {_human(after)} "
           f"(freed ~{_human(max(before - after, 0))})")
    else:
        ok("Slim pass complete.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:

    parser = argparse.ArgumentParser(description="Fish-Speech setup")
    parser.add_argument("--reference", type=Path, default=None,
                        help="Path to your reference Pokédex-voice WAV "
                             "(any sample rate; will be resampled).")
    parser.add_argument("--skip-install", action="store_true",
                        help="Don't run pip — only download model + prep reference.")
    parser.add_argument("--skip-download", action="store_true",
                        help="Don't download weights (assume already present).")
    parser.add_argument("--skip-smoke", action="store_true",
                        help="Don't run the final smoke test.")
    parser.add_argument("--check", action="store_true",
                        help="Verify install + exit (no install / download).")
    parser.add_argument("--hf-token", default=os.environ.get("HF_TOKEN"),
                        help="HuggingFace access token (only if repo is gated).")
    parser.add_argument("--force-cpu", action="store_true",
                        help="Install CPU-only torch even if CUDA is available.")
    parser.add_argument("--no-slim", action="store_true",
                        help="Skip the post-install slimming pass (keeps torch "
                             "headers/.lib and any dev packages installed).")
    args = parser.parse_args()

    info(f"Platform: {platform.platform()}")
    check_python()

    if args.check:
        try:
            import torch, fish_speech    # noqa: F401
            ok("torch + fish-speech import OK")
        except ImportError as exc:
            err(f"Missing deps: {exc}")
            return 1
        if not CHECKPOINT_DIR.exists():
            err(f"Checkpoint dir missing: {CHECKPOINT_DIR}")
            return 1
        ok("Install looks good.")
        return 0

    use_cuda = (not args.force_cpu) and detect_cuda()

    if not args.skip_install:
        install_torch(use_cuda)
        install_fish_speech()
        # Defence in depth: installing the runtime deps resolves a large tree;
        # make sure nothing quietly swapped torch for a CPU wheel.  This call is
        # a fast no-op when the CUDA build is still in place.
        install_torch(use_cuda)

    if not args.skip_download:
        download_model(args.hf_token)

    prepare_reference(args.reference)

    # IMPORTANT ORDERING: slim BEFORE the smoke test.
    #
    # The smoke test loads the full model exactly the way server.py does, so it
    # is our one chance to prove the *shipped* environment works.  If we slim
    # AFTER it, the smoke test validates the un-slimmed install and a slim step
    # that breaks torch sails through undetected - the failure only surfaces
    # later, when the player launches the game.  Slimming first means the smoke
    # test exercises the real, trimmed environment, and setup exits non-zero
    # (leaving .installed unwritten) if the trim broke anything.
    if not args.skip_install and not args.no_slim:
        slim_install()

    if not args.skip_smoke:
        smoke_test()

    print()
    ok("All done.  Launch the server with:")
    if platform.system() == "Windows":
        print("    Start_TTS_Server.bat")
    else:
        print("    ./start_tts_server.sh")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
