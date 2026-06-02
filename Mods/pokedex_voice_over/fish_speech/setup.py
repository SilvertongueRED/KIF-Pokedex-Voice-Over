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
import time
import subprocess
import sys
import warnings
from pathlib import Path

# Suppress deprecation warnings from third-party packages (torch / vector_quantize_pytorch).
# These are cosmetic — the functionality is unaffected — but they print in red
# and confuse users who see them in setup.log.
warnings.filterwarnings("ignore", category=FutureWarning, module=r"vector_quantize_pytorch")
warnings.filterwarnings("ignore", message=r".*torch\.cuda\.amp\.autocast.*", category=FutureWarning)
warnings.filterwarnings("ignore", message=r".*sdp_kernel.*", category=FutureWarning)

# ---------------------------------------------------------------------------
# Force UTF-8 stdio (IMPORTANT - prevents a first-run install crash)
# ---------------------------------------------------------------------------
# setup.py prints a few non-ASCII glyphs (e.g. the "downloading" arrow in
# download_model).  When the launcher captures setup output to setup.log the
# child's stdout is a redirected PIPE, not a console, so Python falls back to
# the legacy Windows codepage (cp1252) which cannot encode those glyphs - the
# bare print() then raises UnicodeEncodeError and aborts the whole install
# before the model is even downloaded (so .installed is never written and the
# server never starts).  Reconfiguring to UTF-8 with errors="replace" makes
# every print safe no matter how stdout is connected, and is a harmless no-op
# when stdout is already UTF-8.  (The launchers also set PYTHONUTF8=1.)
for _std in (sys.stdout, sys.stderr):
    try:
        _std.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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
    # --no-warn-script-location suppresses pip's "WARNING: The script X is
    # installed in '...\\Scripts' which is not on PATH" messages.  Those print
    # in alarming red during a normal install, but they are harmless *for this
    # mod*: the bundled embeddable Python is private to the mod folder and
    # nothing here ever calls those console scripts by bare name (server.py
    # imports the libraries directly), so Scripts/ being off PATH does not
    # matter.  The flag is scoped to THIS single pip invocation only - it does
    # not touch the user's global pip config, environment, or any unrelated
    # terminal/pip usage outside the mod.  (install_python.bat passes the same
    # flag to get-pip for exactly this reason.)
    cmd = [sys.executable, "-m", "pip", "install",
           "--no-warn-script-location"] + args
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


VENDOR_MARKER = VENDOR_DIR / ".project-root"


def _vendor_core_files() -> list[Path]:
    """Real engine code/config that setup CANNOT regenerate offline.

    These are the files the 1.5 checkpoint depends on - fsq.py (holding
    DownsampleFiniteScalarQuantize), the Hydra config, and the inference_engine
    package - so they MUST physically ship inside ./vendor.  If any are absent
    the mod was not extracted/copied in full and we cannot fix it here.
    """
    return [
        VENDOR_DIR / "fish_speech" / "models" / "vqgan" / "modules" / "fsq.py",
        VENDOR_DIR / "fish_speech" / "configs" / "firefly_gan_vq.yaml",
        VENDOR_DIR / "fish_speech" / "inference_engine" / "__init__.py",
    ]


def _ensure_vendor_marker() -> None:
    """Recreate ./vendor/.project-root if only that marker got dropped.

    .project-root is an EMPTY hidden file pyrootutils uses to locate the project
    root - only its existence matters.  Hidden dotfiles are exactly what naive
    zip extraction, file-by-file mod-manager downloads, and OneDrive sync skip,
    so when the real engine code is present we silently restore the marker
    instead of making the user re-extract the entire mod.
    """
    if VENDOR_MARKER.exists():
        return
    if all(p.exists() for p in _vendor_core_files()):
        try:
            VENDOR_MARKER.write_text("", encoding="utf-8")
            warn("Restored missing vendor marker .project-root (it was dropped "
                 "in distribution; the engine code itself is present).")
        except Exception as exc:
            warn(f"Could not recreate {VENDOR_MARKER} ({exc}).")


def _vendor_missing_files() -> list[Path]:
    """Return any required vendored-engine files that are absent."""
    return [p for p in (_vendor_core_files() + [VENDOR_MARKER]) if not p.exists()]


def _vendor_is_present() -> bool:
    """Sanity-check that the vendored 1.5 engine actually shipped with the mod,
    so a truncated copy fails loudly here instead of deep inside model load."""
    return not _vendor_missing_files()


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


def install_triton(use_cuda: bool) -> None:
    """Install Triton so torch.compile's inductor backend works on CUDA.

    server.py now enables torch.compile by default on CUDA, which gives a
    2-3x inference speedup - but the inductor backend needs Triton to JIT the
    GPU kernels.  On Linux/macOS Triton ships as a dependency of the CUDA torch
    wheel, so there is normally nothing to do.  On WINDOWS, PyTorch publishes
    no Triton wheel at all, so we install the community `triton-windows` build
    whose 3.1.x line matches the torch 2.5.x we pull from the cu121 channel.

    Best-effort by design: if the wheel can't be installed (offline, unusual
    ABI, etc.) we warn and carry on.  server.py sets torch._dynamo
    suppress_errors, so a missing Triton just means compile silently falls back
    to eager execution - the mod still narrates fine, it just loses the 2-3x.
    """
    if not use_cuda:
        return  # Triton only matters for the CUDA inductor backend.
    if _module_importable("triton"):
        ok("Triton already present - torch.compile inductor backend ready.")
        return
    if sys.platform == "win32":
        info("Installing triton-windows (enables torch.compile speedup on Windows)...")
        try:
            # 3.1.x is the line matched to torch 2.5.x (cu121 channel's top).
            run_pip(["triton-windows>=3.1,<3.2"])
            ok("triton-windows installed - torch.compile speedup enabled.")
        except Exception as exc:
            warn(f"Could not install triton-windows ({exc}). torch.compile will "
                 f"fall back to eager mode - TTS still works, just without the "
                 f"2-3x speedup. You can retry later or run the server with "
                 f"--no-compile to silence the fallback log noise.")
    else:
        info("Installing triton (enables torch.compile speedup)...")
        try:
            run_pip(["triton>=3.1,<3.2"])
            ok("triton installed - torch.compile speedup enabled.")
        except Exception as exc:
            warn(f"Could not install triton ({exc}). torch.compile will fall "
                 f"back to eager mode.")


def provision_python_dev_files(use_cuda: bool) -> None:
    """Give the bundled embeddable Python the C headers + import libs that
    torch.compile's INDUCTOR backend (via Triton) needs to build its launcher.

    The Windows 'embeddable' Python ships WITHOUT Include/ (Python.h) and
    libs/python3.lib.  triton-windows looks for them and, when missing, fails
    with 'Python.h not found' / 'Failed to find Python libs', so inductor
    silently falls back to uncompiled (no speedup).  We fetch them from the
    official 'python' NuGet package (the same CPython build, redistributable)
    and drop them next to python.exe; triton then finds them automatically.

    Best-effort and Windows-embeddable-ONLY: a normal/system Python already has
    these, and any failure here just means inductor won't engage (cudagraphs and
    uncompiled generation still work).
    """
    if not use_cuda or sys.platform != "win32":
        return
    if not _is_mod_owned_env():
        return  # never modify a shared/system Python
    base = Path(sys.executable).resolve().parent  # the mod's python/ folder
    inc = base / "Include"
    libs = base / "libs"
    if (inc / "Python.h").exists() and (libs / "python3.lib").exists():
        ok("Python dev headers/libs already present (inductor can compile).")
        return

    # Prefer the dev files BUNDLED with the mod (offline + deterministic): the
    # embeddable Python omits Include/ + libs/, so we ship a known-good copy in
    # fish_speech/python_dev/ and just drop it next to python.exe.  No network.
    bundled = HERE / "python_dev"
    if (bundled / "Include" / "Python.h").exists() and (bundled / "libs" / "python3.lib").exists():
        try:
            import shutil
            inc.mkdir(parents=True, exist_ok=True)
            libs.mkdir(parents=True, exist_ok=True)
            shutil.copytree(bundled / "Include", inc, dirs_exist_ok=True)
            shutil.copytree(bundled / "libs", libs, dirs_exist_ok=True)
            if (inc / "Python.h").exists() and (libs / "python3.lib").exists():
                ok(f"Installed bundled Python dev headers/libs into {base} "
                   f"(inductor can compile - no download needed).")
                return
            warn("Bundled dev-file copy incomplete - trying NuGet download.")
        except Exception as exc:
            warn(f"Could not copy bundled dev files ({exc}) - trying NuGet download.")

    vi = sys.version_info
    versions = [f"{vi.major}.{vi.minor}.{vi.micro}"]
    for micro in (10, 9, 8, 7, 6, 4):  # known-good 3.x.y fallbacks on NuGet
        v = f"{vi.major}.{vi.minor}.{micro}"
        if v not in versions:
            versions.append(v)

    import io as _io
    import zipfile
    import urllib.request

    info("Fetching Python dev headers/libs for the embeddable interpreter "
         "(needed by the inductor backend; cudagraphs/uncompiled do not need them)...")
    nupkg = None
    for v in versions:
        url = f"https://www.nuget.org/api/v2/package/python/{v}"
        try:
            info(f"  trying NuGet python {v} ...")
            with urllib.request.urlopen(url, timeout=60) as resp:
                data = resp.read()
            nupkg = zipfile.ZipFile(_io.BytesIO(data))
            ok(f"  downloaded python {v} dev package.")
            break
        except Exception as exc:
            warn(f"  {v}: {exc}")
    if nupkg is None:
        warn("Could not download Python dev files. The inductor backend will "
             "not engage; use --compile-backend cudagraphs, or point "
             "POKEDEX_VO_PYTHON at a full Python install. (Uncompiled still works.)")
        return
    try:
        inc.mkdir(parents=True, exist_ok=True)
        libs.mkdir(parents=True, exist_ok=True)
        n_inc = n_lib = 0
        for name in nupkg.namelist():
            if name.endswith("/"):
                continue
            low = name.lower()
            if low.startswith("tools/include/"):
                dest = inc / name[len("tools/include/"):]
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(nupkg.read(name))
                n_inc += 1
            elif low.startswith("tools/libs/"):
                dest = libs / name[len("tools/libs/"):]
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(nupkg.read(name))
                n_lib += 1
        if (inc / "Python.h").exists() and (libs / "python3.lib").exists():
            ok(f"Installed {n_inc} headers -> {inc} and {n_lib} libs -> {libs} "
               f"(inductor can now compile).")
        else:
            warn(f"Copied {n_inc} headers / {n_lib} libs but Python.h or "
                 f"python3.lib still missing - inductor may not engage.")
    except Exception as exc:
        warn(f"Failed to install Python dev files ({exc}). inductor may not engage.")


def install_fish_speech() -> None:
    """Install the inference-only leaf dependencies for the VENDORED engine.

    fish-speech 1.5 itself is NOT pip-installed: a trimmed copy of its source
    lives under ./vendor and is put on sys.path by server.py, so no GitHub clone
    is ever required.  We only install the third-party leaf packages the
    inference path imports, listed in requirements-runtime.txt.

    Re-runs are smart: a no-op when the key leaf packages already import.
    """
    # Self-heal a dropped .project-root marker before failing (the engine code
    # may be fully present and only the hidden marker got skipped in transit).
    _ensure_vendor_marker()
    missing = _vendor_missing_files()
    if missing:
        err(f"Vendored fish-speech engine missing/incomplete under {VENDOR_DIR}.")
        for _p in missing:
            err(f"  missing: {_p.relative_to(VENDOR_DIR)}")
        err("Re-extract / re-copy the mod so ./vendor ships in FULL - make sure "
            "hidden files like .project-root are included (some unzip tools and "
            "mod managers skip dotfiles).")
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


# Git-LFS pointer files are tiny text stubs that GitHub (and any mod manager
# that downloads a repo as a ZIP/tarball) serves IN PLACE OF the real blob when
# Git-LFS is not resolved.  They begin with the magic line below.  Loading one
# as a checkpoint raises "_pickle.UnpicklingError: invalid load key, 'v'"
# ('v' = the leading "version ..." text).  We must detect and replace them.
_LFS_POINTER_MAGIC = b"version https://git-lfs.github.com/spec/v1"

# Minimum plausible byte size for the large weight blobs.  Anything far below
# this (a ~130-byte LFS stub, or a truncated download) is not the real file and
# must be re-fetched from HuggingFace.
_MIN_CHECKPOINT_BYTES = {
    "firefly-gan-vq-fsq-8x1024-21hz-generator.pth": 100_000_000,  # real ~188 MB
    "model.pth": 1_000_000_000,                                   # real ~1.27 GB
}


def _checkpoint_is_valid(target: Path, fname: str) -> bool:
    """True only if *target* is the real weight file, not a Git-LFS pointer
    stub, a truncated download, or an empty placeholder."""
    if not target.exists():
        return False
    try:
        size = target.stat().st_size
    except OSError:
        return False
    if size <= 0:
        return False
    # Reject Git-LFS pointer stubs (and any other tiny text masquerade).
    try:
        with open(target, "rb") as fh:
            if fh.read(len(_LFS_POINTER_MAGIC)) == _LFS_POINTER_MAGIC:
                return False
    except OSError:
        return False
    # Enforce a sane floor for the big binary blobs (catches truncated files
    # that are not pointer stubs).
    if size < _MIN_CHECKPOINT_BYTES.get(fname, 1):
        return False
    return True


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
        if _checkpoint_is_valid(target, fname):
            ok(f"  ✓ {fname} (cached)")
            continue
        info(f"  ⤓ {fname}")
        if target.exists():
            warn(
                f"  {fname} is a Git-LFS stub / truncated "
                f"({target.stat().st_size} bytes); fetching the real weights "
                f"from HuggingFace"
            )
            try:
                target.unlink()
            except OSError:
                pass
        # Network downloads can blip on a ~1.4 GB first-run fetch.
        # hf_hub_download resumes partial files, so retry a few times before
        # giving up instead of hard-failing the whole install on one hiccup.
        last_err = None
        for attempt in range(1, 4):
            try:
                path = hf_hub_download(
                    repo_id=HF_REPO, filename=fname,
                    local_dir=str(CHECKPOINT_DIR),
                    token=token,
                )
                # huggingface_hub may put the file at a nested path; normalise.
                if Path(path) != target and Path(path).exists():
                    shutil.move(path, target)
                if _checkpoint_is_valid(target, fname):
                    last_err = None
                    break
                last_err = "downloaded file failed validation"
            except Exception as exc:  # noqa: BLE001 - retry on any network error
                last_err = str(exc)
            if attempt < 3:
                warn(f"  {fname} download attempt {attempt}/3 failed "
                     f"({last_err}); retrying in 5s...")
                time.sleep(5)
        if last_err is not None or not _checkpoint_is_valid(target, fname):
            err(
                f"Downloaded {fname} is still not a valid weight file "
                f"({target.stat().st_size if target.exists() else 0} bytes) "
                f"after 3 attempts. Last error: {last_err}. "
                f"Verify network access to https://huggingface.co/{HF_REPO}."
            )
            sys.exit(1)
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
    # Cross-platform: sysconfig reports this interpreter's real site-packages
    # (Linux/macOS venv -> lib/pythonX.Y/site-packages; Windows embeddable ->
    # Lib/site-packages).
    import sysconfig
    for key in ("purelib", "platlib"):
        sp = sysconfig.get_paths().get(key)
        if sp and Path(sp).exists():
            return Path(sp)
    base = Path(sys.executable).resolve().parent
    cand = base / "Lib" / "site-packages"
    if cand.exists():
        return cand
    found = list(base.parent.rglob("site-packages"))
    return found[0] if found else cand


def _is_mod_owned_env() -> bool:
    """True only when running from the mod's OWN interpreter - the Windows
    embeddable python/ or the Linux/macOS venv/, both of which live directly
    under this fish_speech folder.  Refuse to slim a shared/system Python so we
    never uninstall packages or delete torch files the user relies on
    elsewhere."""
    try:
        prefix = Path(sys.prefix).resolve()
        return HERE == prefix or HERE in prefix.parents
    except Exception:
        return False


def slim_install() -> None:
    """Trim the install to the inference-only footprint.

    Idempotent and safe to run after every (re)install.  It (1) uninstalls
    packages the inference path never imports, (2) deletes torch's compile-only
    artefacts (*.lib / *.a, C++ headers, bundled tests), and (3) drops __pycache__ and
    packaged test suites.  It never touches the CUDA runtime DLLs or the model
    weights, so GPU inference and clone fidelity are unchanged.
    """
    if not _is_mod_owned_env():
        warn("Skipping slim pass - not running from the mod's own interpreter "
             "(a shared/system Python was detected).  Launch via "
             "Start_TTS_Server.bat or start_tts_server.sh so the bundled / venv "
             "interpreter is used; slimming then runs automatically.")
        return
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
        # *.lib (Windows) and *.a (Linux/macOS) are compile-only static / import
        # libraries; runtime inference loads the *.dll / *.so instead.
        for pattern in ("*.lib", "*.a"):
            for f in torch_lib.glob(pattern):
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
        bad = [f for f in HF_FILES if not _checkpoint_is_valid(CHECKPOINT_DIR / f, f)]
        if bad:
            err(f"Invalid / Git-LFS-stub checkpoint files: {', '.join(bad)}")
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
        # Triton powers torch.compile's inductor backend (the 2-3x CUDA win).
        # Done AFTER the torch re-check so it installs against the final,
        # known-good CUDA torch build.  No-op on CPU installs.
        install_triton(use_cuda)
        # Give the embeddable Python the headers/libs Triton needs so the
        # inductor backend can actually compile (the 2-3x win).  No-op if they
        # are already present or on a non-embeddable/CPU install.
        provision_python_dev_files(use_cuda)
        # A fresh (re)install may have fixed whatever blocked compile before, so
        # clear the "compile failed here" marker and let the server re-attempt.
        try:
            (HERE / ".compile_disabled").unlink()
            info("Cleared .compile_disabled - server will re-attempt compile.")
        except OSError:
            pass

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
