#!/usr/bin/env python3
# =============================================================================
# Fish-Speech TTS Sidecar Server
# =============================================================================
# Small dependency-light HTTP server that wraps the open-source fish-speech
# engine (the engine that powers fish.audio) so the KIF Pokedex Voice Over
# mod can request voice-cloned narration over a local socket.
#
# Endpoints:
#   GET  /health                    -> {"ok": true, "device": "cuda"|"cpu"|"mps"}
#   POST /tts   body: text=...      -> WAV bytes (audio/wav)
#
# Why HTTP and not a one-shot CLI?
#   fish-speech takes ~5-15 s just to LOAD its weights.  Spawning a fresh
#   Python process for every Pokedex entry would be unusably slow.  Keeping
#   the model resident in a tiny localhost server reduces per-entry latency
#   to actual inference time (~0.5 s GPU, ~3-8 s CPU on modern hardware).
#
# Reference voice cloning:
#   On first run the server reads the user's reference WAV from
#   <mod>/fish_speech/reference/voice.wav, encodes it once via the
#   fish-speech VQ-GAN tokenizer, and reuses the cached prompt for every
#   subsequent generation.
#
# API compatibility:
#   This file targets fish-speech 1.5.x, whose engine source is vendored into
#   the mod at <here>/vendor and loaded from there (see the sys.path insert
#   below).  fish-speech 2.x reorganised these import paths and dropped the
#   firefly VQ-GAN the 1.5 checkpoint needs, so it is intentionally NOT used.
#
# This file is invoked by start_tts_server.bat / .sh, or auto-spawned by
# main.rb the first time the mod needs TTS.
# =============================================================================

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import signal
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs

HERE = Path(__file__).resolve().parent
DEFAULT_REF_WAV = HERE / "reference" / "voice.wav"
DEFAULT_REF_TXT = HERE / "reference" / "voice.txt"
DEFAULT_CHECKPOINT_DIR = HERE / "checkpoints" / "fish-speech-1.5"
DEFAULT_PORT = 7861
DEFAULT_HOST = "127.0.0.1"
PID_FILE = HERE / "server.pid"

# -----------------------------------------------------------------------------
# Persistent torch.compile cache.
#
# Setting TORCHINDUCTOR_CACHE_DIR *before* importing torch tells torch.compile
# to persist its FX graph + Triton kernel cache to disk.  The first launch
# with --compile pays the full ~30-90 s JIT cost; every launch after that
# loads the cached kernels in ~5-10 s.  Without this, every server start re-
# compiles from scratch, which was the main reason startup was previously
# stuck around 45+ seconds.
#
# We set the env vars unconditionally - they are harmless when --no-compile
# is in effect, and we want them set the first time the user enables compile.
# -----------------------------------------------------------------------------
_TORCH_CACHE_DIR = HERE / ".torch_cache"
try:
    _TORCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("TORCHINDUCTOR_CACHE_DIR", str(_TORCH_CACHE_DIR / "inductor"))
    os.environ.setdefault("TORCHINDUCTOR_FX_GRAPH_CACHE", "1")
    os.environ.setdefault("TRITON_CACHE_DIR", str(_TORCH_CACHE_DIR / "triton"))
except Exception:
    # If we can't create the cache dir (read-only filesystem, etc.) just
    # carry on - torch will fall back to its in-memory cache.
    pass

# Default idle-shutdown timeout (seconds).  If no /tts request arrives for
# this long, the server shuts itself down - guards against orphaned processes
# when the game crashes and the at_exit shutdown hook never fires.
DEFAULT_IDLE_TIMEOUT = 300  # 5 minutes (parent-PID monitor is the primary safety net)

# The fish-speech 1.5 engine source is VENDORED inside the mod at <here>/vendor
# (a trimmed, pinned copy - see setup.py and requirements-runtime.txt).  Put it
# FIRST on sys.path so "import fish_speech" always resolves to this offline copy,
# never to whatever (if anything) is pip-installed system-wide.  The vendored
# package ships an __init__.py so it wins even over a leftover pip install.
_VENDOR_SRC = str(HERE / "vendor")
if _VENDOR_SRC not in sys.path:
    sys.path.insert(0, _VENDOR_SRC)

LOG_FILE = HERE / "server.log"

logging.basicConfig(
    filename=str(LOG_FILE),
    filemode="a",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("fish-tts")

_model_lock = threading.Lock()
_model_state: dict = {
    "ready": False,
    "device": None,
    "engine": None,
    "prompt_audio_bytes": None,
    "prompt_text": "",
    "api_version": None,
    "load_error": None,
    "compiled": False,
}

# Tracks last activity for the idle-shutdown watchdog (see _idle_watchdog).
_last_activity_ts = time.time()
_activity_lock = threading.Lock()


def _bump_activity() -> None:
    global _last_activity_ts
    with _activity_lock:
        _last_activity_ts = time.time()


# ---------------------------------------------------------------------------
# Parent-process liveness check
# ---------------------------------------------------------------------------

def _is_pid_alive(pid: int) -> bool:
    """
    Return True if the process `pid` is still running.

    On Windows we use OpenProcess + GetExitCodeProcess via ctypes so we can
    distinguish "process is still running" from "process exited" reliably.
    The previous implementation used os.kill(pid, 0) which on Windows raises
    PermissionError both when the PID is valid-but-inaccessible AND when the
    PID has been recycled/invalidated - that ambiguity meant we treated dead
    processes as alive, leaving the server (and its terminal window) running
    forever after a game crash.

    On POSIX we keep the simple os.kill(pid, 0) approach which is unambiguous.
    """
    if pid <= 0:
        return False

    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259  # WinNT.h STATUS_PENDING
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                # Process doesn't exist (or we don't have rights to query it).
                # ERROR_INVALID_PARAMETER (87) means the PID is invalid/dead;
                # any other failure we treat as dead too - better to shut down
                # spuriously than leak the terminal forever.
                return False
            try:
                exit_code = wintypes.DWORD()
                ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                if not ok:
                    return False
                return exit_code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception as exc:
            log.debug("ctypes pid-alive check failed for %d: %s", pid, exc)
            # Fall through to the POSIX-style check as a last resort.

    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # POSIX: pid exists but belongs to another user.
        return True
    except Exception:
        return True  # Be conservative on truly unexpected errors.


def _parent_pid_monitor(parent_pid: int) -> None:
    """
    Background daemon thread that shuts the server down when the game (parent)
    process exits.

    This is the primary shutdown path when the game is force-killed or when
    MKXP-Z's embedded Ruby runtime bypasses at_exit (a common occurrence on
    Windows when the player clicks the X button).

    Checks every 5 seconds.  Exits via _async_shutdown so the main thread
    gets a clean serve_forever() teardown.
    """
    log.info("Parent PID monitor started (watching pid=%d)", parent_pid)
    while True:
        time.sleep(5)
        if not _is_pid_alive(parent_pid):
            log.info("Parent process %d has exited - shutting down server", parent_pid)
            _async_shutdown(f"parent pid {parent_pid} exited")
            return


# ---------------------------------------------------------------------------
# fish-speech 1.5 loader helpers (imported lazily after sys.path is set)
# ---------------------------------------------------------------------------

def _load_fish_speech_1_5(checkpoint_dir: Path, device: str, compile_model: bool):
    """
    Load the fish-speech 1.5 models and return a ready TTSInferenceEngine.

    fish-speech 1.5 requires three separate steps:
      1. Launch a thread-safe queue that owns the LLaMA (text->VQ) model.
      2. Load the Firefly VQ-GAN decoder model.
      3. Wire both into TTSInferenceEngine.

    The bundled source directory must be on sys.path before this is called
    (see the sys.path.insert() at the top of this file).

    When `compile_model` is True we ask fish-speech to torch.compile() the
    LLaMA and decoder paths.  This adds ~30-90 s to the first generation
    (JIT compile) but cuts steady-state inference latency by ~2-3x on CUDA,
    which is the single biggest win available to us.  Always off on CPU/MPS
    where torch.compile typically slows things down or fails outright.
    """
    import torch

    # -------------------------------------------------------------------------
    # Free GPU speedups (CUDA only).  None of these change the audio output in
    # any audible way, they just let the 4090's tensor cores run the matmuls
    # faster:
    #   * allow_tf32          - use TF32 for fp32 matmuls/conv (Ampere/Ada).
    #   * set_float32_matmul_precision("high") - same idea, newer API surface.
    #   * cudnn.benchmark     - autotune conv kernels for our fixed shapes.
    # These help the prefill / decoder matmuls regardless of torch.compile, so
    # they are always worth setting and are the safe baseline win.
    # -------------------------------------------------------------------------
    if device == "cuda":
        try:
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
            torch.backends.cudnn.benchmark = True
            torch.set_float32_matmul_precision("high")
            log.info("CUDA math tuning enabled (TF32 matmul + cudnn.benchmark).")
        except Exception as exc:
            log.debug("CUDA math tuning skipped: %s", exc)

    # Use float16 on CUDA (matches fish-speech training precision, faster on GPU
    # tensor cores).  Bfloat16 on CPU/MPS to avoid float16 underflow off-GPU.
    precision = torch.float16 if device == "cuda" else torch.bfloat16

    # torch.compile only helps on CUDA.  Force-disable on CPU/MPS regardless
    # of what the user asked for - saves us debugging weird Triton errors.
    do_compile = bool(compile_model) and device == "cuda"

    # SAFETY NET: when we ask for torch.compile, tell TorchDynamo to fall back
    # to plain eager execution if anything in the compile pipeline fails
    # (e.g. Triton missing on a Windows box where setup.py couldn't install
    # triton-windows).  Without this a compile failure would raise on the first
    # /tts and break narration entirely; with it the mod just runs uncompiled
    # (no speedup, but no feature loss).  cache_size_limit is bumped so the
    # handful of sequence-length specialisations we hit don't thrash the cache.
    if do_compile:
        try:
            import torch._dynamo
            torch._dynamo.config.suppress_errors = True
            torch._dynamo.config.cache_size_limit = 64
            log.info("torch.compile requested - dynamo suppress_errors ON "
                     "(falls back to eager if Triton/inductor is unavailable).")
        except Exception as exc:
            log.debug("Could not configure torch._dynamo: %s", exc)

    from fish_speech.models.text2semantic.inference import launch_thread_safe_queue
    from fish_speech.models.vqgan.inference import load_model as load_decoder_model
    from fish_speech.inference_engine import TTSInferenceEngine

    decoder_ckpt = checkpoint_dir / "firefly-gan-vq-fsq-8x1024-21hz-generator.pth"

    # launch_thread_safe_queue calls BaseTransformer.from_pretrained(checkpoint_path),
    # which expects a DIRECTORY (it loads config.json, tokenizer.tiktoken, and model.pth
    # from inside that directory). Pass the directory, not the .pth file directly.
    log.info("Launching LLaMA queue (dir=%s device=%s compile=%s)",
             checkpoint_dir, device, do_compile)
    llama_queue = launch_thread_safe_queue(
        checkpoint_path=str(checkpoint_dir),
        device=device,
        precision=precision,
        compile=do_compile,
    )

    log.info("Loading VQ-GAN decoder (ckpt=%s)", decoder_ckpt)
    decoder_model = load_decoder_model(
        config_name="firefly_gan_vq",
        checkpoint_path=str(decoder_ckpt),
        device=device,
    )

    engine = TTSInferenceEngine(
        llama_queue=llama_queue,
        decoder_model=decoder_model,
        precision=precision,
        compile=do_compile,
    )
    return engine, precision, do_compile


# ---------------------------------------------------------------------------
# Warmup helper
# ---------------------------------------------------------------------------
#
# Warmup is split into two phases:
#
#   (A) reference-prime: encode the user's reference WAV through the VQ-GAN
#       tokenizer once so subsequent /tts requests can short-circuit via
#       use_memory_cache="on".  Cheap (~1-2 s on CUDA) and worth doing
#       eagerly.  The resulting prompt is also serialised to disk so the
#       next process startup can skip re-encoding entirely.
#
#   (B) kernel-warm: run a tiny "Hello." generation so the LLaMA path's
#       CUDA kernels are JIT'd/loaded.  Slightly more expensive (~4 s on
#       CUDA without --compile, ~30-90 s WITH --compile on the first ever
#       launch, then near-instant once the inductor cache hits).
#
# On the FIRST launch after install we want the /health endpoint to flip
# to "ready" as soon as the weights are in memory, not at the end of (B).
# That lets the Pokedex display its first entry without timing out.
# We therefore run warmup in a BACKGROUND thread that mutates the engine
# in the same way the main inference path does (fish-speech's LLaMA queue
# is already thread-safe via launch_thread_safe_queue), and only the
# disk-cache write happens at the end.
# ---------------------------------------------------------------------------

# Cached encoded-reference filename.  Format = pickled (audio_hash, [codes_tensor_cpu]).
# Lives alongside the reference WAV so swapping voices invalidates it naturally.
_REF_CACHE_PATH = HERE / "reference" / "voice.codes.pt"


def _prime_reference(engine, ref_audio_bytes: Optional[bytes], ref_text: str) -> None:
    """
    Pre-encode the reference WAV (Phase A).

    Runs an extremely short fish-speech inference whose only purpose is to
    push the reference audio through the VQ-GAN tokenizer and into the
    engine's `use_memory_cache="on"` table.  After this completes, every
    subsequent /tts request hits the cache and skips ~1-2 s of encoding.

    Cheap enough to always run synchronously immediately after the model
    weights load.  Saved to _REF_CACHE_PATH so the next process can prime
    the cache from disk without paying the encode cost at all.
    """
    if not ref_audio_bytes:
        return
    try:
        # Try the disk-cached path first.  Hash the raw WAV bytes so any
        # change to reference/voice.wav invalidates the cache automatically.
        import hashlib
        audio_hash = hashlib.sha1(ref_audio_bytes).hexdigest()

        cached = _try_load_reference_cache(engine, ref_audio_bytes, ref_text,
                                           audio_hash)
        if cached:
            log.info("Reference cache hit (sha1=%s) - skipped re-encoding",
                     audio_hash[:10])
            return

        from fish_speech.utils.schema import ServeTTSRequest, ServeReferenceAudio
        log.info("Reference cache miss - encoding reference (~1-2 s)...")
        t0 = time.time()
        req = ServeTTSRequest(
            text="Hi.",
            references=[ServeReferenceAudio(audio=ref_audio_bytes, text=ref_text)],
            use_memory_cache="on",
            # Cap aggressively - we don't actually need the audio, just the
            # side effect of populating the reference cache and JIT'ing the
            # encode path.
            max_new_tokens=8,
            temperature=0.6,
            top_p=0.85,
            streaming=False,
        )
        for _ in engine.inference(req):
            pass
        log.info("Reference primed in %.2fs", time.time() - t0)

        # Best-effort save of the encoded reference for the next process.
        _save_reference_cache(engine, audio_hash)
    except Exception as exc:
        log.warning("Reference prime failed (non-fatal): %s", exc)


def _try_load_reference_cache(engine, ref_audio_bytes: bytes, ref_text: str,
                              audio_hash: str) -> bool:
    """
    Pre-populate engine.ref_by_hash from _REF_CACHE_PATH without re-encoding
    the WAV through the VQ-GAN.  Returns True when the engine's memory cache
    was successfully primed (next /tts request will skip the encode step).

    Layout reminder (fish-speech 1.5, inference_engine/reference_loader.py):
        class TTSInferenceEngine(ReferenceLoader, VQManager): ...
        ReferenceLoader populates `self.ref_by_hash[sha1] = (prompt_tokens,
        prompt_texts)` and the inference path checks `ref_by_hash` first.

    We persist the exact `(prompt_tokens, prompt_texts)` pair plus the SHA1
    of the raw WAV bytes; on a subsequent launch with the same WAV we just
    drop the tuple back into `engine.ref_by_hash` keyed by the SAME sha1
    that fish-speech itself would compute for the audio.
    """
    try:
        if not _REF_CACHE_PATH.exists():
            return False
        if not hasattr(engine, "ref_by_hash"):
            return False
        import torch
        blob = torch.load(_REF_CACHE_PATH, map_location="cpu", weights_only=False)
        if not isinstance(blob, dict):
            return False
        if blob.get("audio_hash") != audio_hash:
            log.info("Reference cache present but WAV has changed - re-encoding")
            return False

        prompt_tokens = blob.get("prompt_tokens")
        prompt_texts = blob.get("prompt_texts")
        engine_hash_key = blob.get("engine_hash_key")  # the exact sha1 fish-speech keys by
        if prompt_tokens is None or engine_hash_key is None:
            return False

        # Move tokens to the engine's device so the inference path doesn't
        # pay a host->device copy on every generation.
        try:
            device = next(engine.decoder_model.parameters()).device
            if isinstance(prompt_tokens, list):
                prompt_tokens = [t.to(device) if hasattr(t, "to") else t
                                 for t in prompt_tokens]
            elif hasattr(prompt_tokens, "to"):
                prompt_tokens = prompt_tokens.to(device)
        except Exception:
            pass

        engine.ref_by_hash[engine_hash_key] = (prompt_tokens, prompt_texts or [])
        return True
    except Exception as exc:
        log.debug("Reference cache load skipped: %s", exc)
        return False


def _save_reference_cache(engine, audio_hash: str) -> None:
    """Persist the freshly-encoded reference so the next launch can use it."""
    try:
        if not hasattr(engine, "ref_by_hash") or not engine.ref_by_hash:
            return
        # We only ever encode one reference voice per server, so there's a
        # single (key, value) pair to save.  Storing fish-speech's own key
        # is what lets the next launch hit the cache without us having to
        # reimplement its sha1 derivation.
        engine_hash_key, payload = next(iter(engine.ref_by_hash.items()))
        if not isinstance(payload, tuple) or len(payload) != 2:
            return
        prompt_tokens, prompt_texts = payload

        import torch
        # CPU-side copy so the file can be loaded on a different device.
        if isinstance(prompt_tokens, list):
            saved_tokens = [t.detach().cpu() if hasattr(t, "detach") else t
                            for t in prompt_tokens]
        elif hasattr(prompt_tokens, "detach"):
            saved_tokens = prompt_tokens.detach().cpu()
        else:
            saved_tokens = prompt_tokens

        torch.save({
            "audio_hash": audio_hash,
            "engine_hash_key": engine_hash_key,
            "prompt_tokens": saved_tokens,
            "prompt_texts": list(prompt_texts) if prompt_texts else [],
        }, _REF_CACHE_PATH)
        log.info("Saved reference encoding to %s", _REF_CACHE_PATH)
    except Exception as exc:
        log.debug("Reference cache save skipped: %s", exc)


def _warmup_kernels(engine, ref_audio_bytes: Optional[bytes], ref_text: str,
                    compiled: bool, full: bool = False) -> None:
    """
    Phase B - run "Hello." through the full pipeline so the LLaMA CUDA
    kernels (and, with --compile, the inductor-compiled graphs) are ready
    before the player's first real Pokedex entry hits the server.

    This is INTENTIONALLY run on a daemon thread after _model_state["ready"]
    has been flipped to True.  fish-speech's LLaMA queue (built by
    launch_thread_safe_queue) is thread-safe with respect to concurrent
    generation requests, so a real /tts coming in while the warmup is in
    flight is safe - it just queues behind the warmup request, which is
    usually the right thing anyway because they share the cached kernels.
    """
    try:
        from fish_speech.utils.schema import ServeTTSRequest, ServeReferenceAudio

        refs = []
        if ref_audio_bytes:
            refs = [ServeReferenceAudio(audio=ref_audio_bytes, text=ref_text)]

        warmup_req = ServeTTSRequest(
            text="Hello.",
            references=refs,
            use_memory_cache="on",
            max_new_tokens=64,
            temperature=0.6,
            top_p=0.85,
            streaming=False,
        )
        log.info("Background kernel warmup starting%s...",
                 " (torch.compile JIT)" if compiled else "")
        t0 = time.time()
        for _ in engine.inference(warmup_req):
            pass
        log.info("Background warmup pass 1 done in %.2fs", time.time() - t0)

        # Optional second pass - only when explicitly requested via --full-warmup.
        if compiled and full:
            t0 = time.time()
            longer_req = ServeTTSRequest(
                text="It uses its sturdy front legs to push through the densest forests.",
                references=refs,
                use_memory_cache="on",
                max_new_tokens=384,
                temperature=0.6,
                top_p=0.85,
                streaming=False,
            )
            for _ in engine.inference(longer_req):
                pass
            log.info("Background warmup pass 2 (compile specialisation) done in %.2fs",
                     time.time() - t0)
        log.info("Background warmup complete - kernels ready.")
    except Exception as exc:
        log.warning("Background warmup failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Model loader
# ---------------------------------------------------------------------------

def _verify_generation(engine, ref_audio_bytes, ref_text, compiled: bool = False):
    """Run ONE real generation and confirm it produces audio.

    Returns (True, None) on success or (False, "<reason>") on failure.  When
    `compiled` is True this call also triggers the torch.compile JIT, so it can
    take ~30-90 s the first time on a cold inductor cache.  This is the heart of
    the verify-then-commit safety net: a compiled engine that cannot actually
    generate is detected HERE (before the server is marked ready), so the mod is
    never left returning silence.
    """
    try:
        from fish_speech.utils.schema import ServeTTSRequest, ServeReferenceAudio
        refs = []
        if ref_audio_bytes:
            refs = [ServeReferenceAudio(audio=ref_audio_bytes, text=ref_text)]
        req = ServeTTSRequest(
            text="The Pokedex voice is ready.",
            references=refs,
            use_memory_cache="on",
            max_new_tokens=128,
            temperature=0.6,
            top_p=0.85,
            streaming=False,
        )
        log.info("Verifying generation%s (the first compiled run also JIT-compiles)...",
                 " [compiled]" if compiled else "")
        t0 = time.time()
        got_audio = False
        for result in engine.inference(req):
            code = getattr(result, "code", None)
            if code == "error":
                return False, f"engine error: {getattr(result, 'error', 'unknown')}"
            if code in ("final", "segment") and getattr(result, "audio", None) is not None:
                _sr, samples = result.audio
                if samples is not None and len(samples) > 0:
                    got_audio = True
        if not got_audio:
            return False, "engine produced no audio"
        log.info("Verification generation OK in %.1fs%s",
                 time.time() - t0, " [compiled]" if compiled else "")
        return True, None
    except Exception as exc:
        log.exception("Verification generation raised")
        return False, f"{exc.__class__.__name__}: {exc}"


def _free_engine(engine) -> None:
    """Best-effort release of a discarded engine's GPU memory.

    Used when a compiled engine fails verification and we rebuild uncompiled.
    The vendored LLaMA queue owns a worker thread we cannot cleanly stop, but
    dropping references + emptying the CUDA cache reclaims the bulk of the VRAM
    before the second (uncompiled) model loads.
    """
    try:
        import gc
        import torch
        try:
            del engine
        except Exception:
            pass
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception as exc:
        log.debug("engine free skipped: %s", exc)


class ModelHolder:
    """Loads fish-speech once and holds the engine + reference prompt."""

    @staticmethod
    def detect_device() -> str:
        try:
            import torch
        except ImportError as exc:
            # Both "torch genuinely not installed" and "torch present but its
            # import blew up half-way" land here (ModuleNotFoundError is an
            # ImportError).  Log the REAL exception so a broken / over-slimmed
            # install is diagnosable, instead of silently masquerading as a
            # CPU-only machine and then dying on the next import with a
            # confusing "partially initialized torch / circular import".
            log.error("torch import failed in detect_device: %r - falling back to CPU. "
                      "If this machine has a GPU the install is broken; re-run setup.py.", exc)
            print(f"[fish-tts] WARNING: torch import failed ({exc}) - falling back to CPU.",
                  file=sys.stderr)
            return "cpu"
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            print(f"[fish-tts] CUDA detected: {name} - using GPU.", file=sys.stderr)
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            print("[fish-tts] MPS detected - using Apple Silicon GPU.", file=sys.stderr)
            return "mps"
        # CUDA not available - print diagnostics to help the user fix it
        cuda_ver = getattr(torch.version, "cuda", None)
        print("[fish-tts] WARNING: CUDA not available - falling back to CPU.", file=sys.stderr)
        print(f"[fish-tts]   torch version : {torch.__version__}", file=sys.stderr)
        print(f"[fish-tts]   torch.version.cuda : {cuda_ver}", file=sys.stderr)
        if cuda_ver is None:
            print("[fish-tts]   -> This torch build has NO CUDA support (CPU-only wheel).",
                  file=sys.stderr)
            print("[fish-tts]   Fix: pip install torch torchaudio "
                  "--index-url https://download.pytorch.org/whl/cu121",
                  file=sys.stderr)
        return "cpu"

    @classmethod
    def load(cls, checkpoint_dir: Path, ref_wav: Path, ref_txt: Path,
             device_override: Optional[str] = None,
             compile_model: bool = False,
             full_warmup: bool = False,
             skip_warmup: bool = False) -> None:
        """
        Load fish-speech weights and (optionally) warm the inference path.

        The critical-path work (loading the LLaMA weights + the VQ-GAN
        decoder) happens under the model lock, then `ready=True` is set and
        the lock is RELEASED so /health and /tts unblock immediately.  The
        background warmup runs on a daemon thread without holding the lock
        - this is the change that cuts first-launch time-to-ready from
        ~45 s (load + 4 s warmup) to ~41 s, with the 4 s of warmup
        overlapping with the player walking from the title screen to the
        Pokedex.
        """
        with _model_lock:
            if _model_state["ready"]:
                return

            device = device_override or cls.detect_device()
            want_compile = bool(compile_model) and device == "cuda"
            log.info("Loading fish-speech 1.5 (device=%s, ckpt=%s, compile=%s)",
                     device, checkpoint_dir, want_compile)

            try:
                prompt_text = ref_txt.read_text(encoding="utf-8").strip() if ref_txt.exists() else ""
                ref_audio_bytes = ref_wav.read_bytes() if ref_wav.exists() else None

                # Build the engine (compiled only if requested AND on CUDA).
                engine, precision, compiled = _load_fish_speech_1_5(
                    checkpoint_dir, device, want_compile)

                # --------------------------------------------------------------
                # VERIFY-THEN-COMMIT - the safety net that makes enabling
                # compile impossible to turn into "no audio".  We run a REAL
                # generation now and only flip ready=True once it has actually
                # produced audio.  When compiled, this first generation also
                # pays the torch.compile JIT (~30-90 s the very first time; the
                # inductor cache makes later launches fast).  If the COMPILED
                # engine fails to generate (Triton missing/broken, a bad kernel,
                # etc.) we discard it and rebuild WITHOUT compile, then verify
                # again - so the worst case is "no speedup", never silence.
                # --------------------------------------------------------------
                if not skip_warmup:
                    _prime_reference(engine, ref_audio_bytes, prompt_text)
                ok, err = _verify_generation(engine, ref_audio_bytes, prompt_text,
                                             compiled=compiled)
                if not ok and want_compile:
                    _model_state["compile_note"] = err
                    log.warning("Compiled engine FAILED verification (%s) - "
                                "rebuilding WITHOUT torch.compile so narration "
                                "still works (no speedup, but no silence).", err)
                    _free_engine(engine)
                    engine, precision, compiled = _load_fish_speech_1_5(
                        checkpoint_dir, device, False)
                    if not skip_warmup:
                        _prime_reference(engine, ref_audio_bytes, prompt_text)
                    ok, err = _verify_generation(engine, ref_audio_bytes,
                                                 prompt_text, compiled=False)
                if not ok:
                    _model_state["load_error"] = f"Generation verification failed: {err}"
                    log.error("fish-speech load failed verification: %s", err)
                    return

                _model_state["engine"] = engine
                _model_state["precision"] = precision
                _model_state["device"] = device
                _model_state["prompt_text"] = prompt_text
                _model_state["prompt_audio_bytes"] = ref_audio_bytes
                _model_state["api_version"] = "1.5"
                _model_state["compiled"] = compiled
                if compiled:
                    _model_state["compile_note"] = None
                _model_state["ready"] = True
                log.info("fish-speech ready (api=1.5 device=%s compiled=%s ref=%s transcript=%d chars)",
                         device, compiled, ref_wav.exists(), len(prompt_text))
            except ImportError as exc:
                msg = (f"Could not import fish-speech 1.5 from {DEFAULT_CHECKPOINT_DIR}: {exc}. "
                       f"Ensure the checkpoint directory exists and run setup.py.")
                log.error(msg)
                _model_state["load_error"] = msg
                return
            except Exception as exc:
                log.exception("fish-speech load failed: %s", exc)
                _model_state["load_error"] = f"{exc.__class__.__name__}: {exc}"
                return

        # ------------------------------------------------------------------
        # Optional extra warmup pass OUTSIDE the lock (daemon thread, never
        # joined).  Only meaningful when we actually ended up compiled - it
        # specialises the compiled kernels for a longer sequence so the first
        # multi-sentence entry is already fast.  Generation already works at
        # this point (verified above), so this is pure best-effort polish.
        # ------------------------------------------------------------------
        if not skip_warmup and full_warmup and _model_state.get("compiled"):
            threading.Thread(
                target=_warmup_kernels,
                args=(engine, ref_audio_bytes, prompt_text, True),
                kwargs={"full": True},
                name="fish-tts-kernel-warmup",
                daemon=True,
            ).start()


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def synthesize(text: str) -> bytes:
    if not _model_state["ready"]:
        raise RuntimeError(_model_state.get("load_error") or "Model not ready")

    text = (text or "").strip()
    if not text:
        raise ValueError("Empty text")

    engine = _model_state["engine"]

    import numpy as np
    from fish_speech.utils.schema import ServeTTSRequest, ServeReferenceAudio

    ref_bytes = _model_state.get("prompt_audio_bytes")
    ref_text = _model_state.get("prompt_text", "")

    # Build the reference list (empty = no voice cloning, model uses default voice)
    references = []
    if ref_bytes:
        references = [ServeReferenceAudio(audio=ref_bytes, text=ref_text)]

    # Sampling knobs tuned for speed without noticeably hurting quality:
    #   - temperature 0.6 (was 0.7): tighter sampling = fewer "uncertain" tokens
    #     and slightly faster convergence to natural prosody.
    #   - top_p 0.85 (was 0.7): slightly wider nucleus avoids the model getting
    #     stuck repeating itself, which is the most common cause of long
    #     generations on short inputs.
    #   - max_new_tokens 768 (was 1024): fish-speech 1.5 emits ~168 tokens per
    #     second of audio.  768 tokens ~ 4.5 s of speech, which covers every
    #     plausible single-sentence Pokedex chunk we send.  Capping lower
    #     prevents pathological runaway generations from wasting GPU time.
    request = ServeTTSRequest(
        text=text,
        references=references,
        temperature=0.6,
        top_p=0.85,
        repetition_penalty=1.2,
        max_new_tokens=768,
        chunk_length=200,
        format="wav",
        streaming=False,
        use_memory_cache="on",  # Cache the encoded reference prompt across requests
    )

    # engine.inference() is a generator yielding InferenceResult objects.
    # Each result has: .code ("header"|"segment"|"final"|"error"), .audio, .error
    audio_chunks: list[bytes] = []
    sample_rate: Optional[int] = None

    for result in engine.inference(request):
        if result.code == "error":
            raise RuntimeError(f"Inference error: {result.error}")
        if result.code in ("final", "segment") and result.audio is not None:
            sr, samples = result.audio
            if sample_rate is None:
                sample_rate = sr
            if isinstance(samples, np.ndarray):
                if samples.dtype != np.int16:
                    samples = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
                audio_chunks.append(samples.tobytes())

    if not audio_chunks or not sample_rate:
        raise RuntimeError("No audio produced by engine")

    pcm = b"".join(audio_chunks)
    return _wrap_wav(pcm, sample_rate, channels=1, sampwidth=2)


def _wrap_wav(pcm: bytes, sample_rate: int, channels: int = 1,
              sampwidth: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

# Module-level reference to the running ThreadingHTTPServer.  The /shutdown
# handler needs to call .shutdown() on this from a background thread (calling
# it from the request thread itself deadlocks).  Wired up in main().
_http_server: Optional[ThreadingHTTPServer] = None


def _async_shutdown(reason: str) -> None:
    """Stop the HTTP server from a background thread."""
    def _do_shutdown() -> None:
        log.info("Shutting down: %s", reason)
        srv = _http_server
        if srv is not None:
            try:
                srv.shutdown()
            except Exception as exc:
                log.warning("server.shutdown() raised: %s", exc)
    threading.Thread(target=_do_shutdown, name="fish-tts-shutdown",
                     daemon=True).start()


class TTSHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        log.debug("%s - %s", self.address_string(), format % args)

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.startswith("/health"):
            ready = _model_state["ready"]
            err = _model_state.get("load_error")
            # Reporting "warming" lets the Ruby side log a clear message
            # ("server still loading, falling back to Piper") instead of the
            # generic "not ready" we get during normal startup.
            status = "ready" if ready else ("error" if err else "warming")
            self._send_json(200, {
                "ok": ready,
                "status": status,
                "device": _model_state.get("device"),
                "api_version": _model_state.get("api_version"),
                "compiled": _model_state.get("compiled", False),
                "error": err,
            })
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        # /shutdown - graceful HTTP-driven shutdown, used by the Pokedex Voice
        # Over mod's at_exit hook so the server stops when the game closes.
        if self.path.startswith("/shutdown"):
            self._send_json(200, {"ok": True, "message": "shutting down"})
            try:
                self.wfile.flush()
            except Exception:
                pass
            _async_shutdown("/shutdown requested")
            return

        if not self.path.startswith("/tts"):
            self._send_json(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        ctype = self.headers.get("Content-Type", "")

        text = ""
        if ctype.startswith("application/json"):
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
                text = (payload.get("text") or "").strip()
            except Exception as exc:
                self._send_json(400, {"error": f"bad json: {exc}"})
                return
        else:
            try:
                parsed = parse_qs(raw.decode("utf-8"))
                text = (parsed.get("text", [""])[0] or "").strip()
            except Exception as exc:
                self._send_json(400, {"error": f"bad form: {exc}"})
                return

        if not text:
            self._send_json(400, {"error": "empty text"})
            return

        # Reject /tts cleanly while the model is still loading so the mod's
        # _generate_tts_fish_speech can fall through to Piper/silence on the
        # very first request after server boot instead of seeing a 500.
        if not _model_state["ready"]:
            err = _model_state.get("load_error")
            self._send_json(503, {
                "error": err or "model still loading",
                "status": "error" if err else "warming",
            })
            return

        _bump_activity()
        try:
            # No model lock here on purpose:
            #
            # fish-speech 1.5's `launch_thread_safe_queue` wraps the LLaMA
            # path in its own work queue, and the decoder/VQ-manager ops we
            # call below are pure-functional w.r.t. the request.  Holding
            # _model_lock here used to serialise /tts behind the in-progress
            # background warmup (which itself does NOT hold the lock any
            # more), defeating the point of running warmup off the critical
            # path.  The first user request now overlaps with kernel warmup
            # which is faster overall - both calls share the same JIT'd
            # kernel cache by the time the second one is decoded.
            wav_bytes = synthesize(text)
        except Exception as exc:
            log.exception("synthesis failed: %s", exc)
            self._send_json(500, {"error": str(exc)})
            return
        finally:
            _bump_activity()

        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(wav_bytes)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(wav_bytes)


# ---------------------------------------------------------------------------
# Process-management helpers (PID file + idle watchdog)
# ---------------------------------------------------------------------------

def _write_pid_file() -> None:
    """Write the current PID so external launchers can find this process."""
    try:
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        log.info("Wrote PID file: %s (%d)", PID_FILE, os.getpid())
    except Exception as exc:
        log.warning("Could not write PID file %s: %s", PID_FILE, exc)


def _remove_pid_file() -> None:
    try:
        if PID_FILE.exists():
            PID_FILE.unlink()
            log.info("Removed PID file: %s", PID_FILE)
    except Exception as exc:
        log.warning("Could not remove PID file %s: %s", PID_FILE, exc)


def _idle_watchdog(idle_timeout: float) -> None:
    """
    Shut the server down after `idle_timeout` seconds of no /tts activity.
    Belt-and-braces fallback for when the game crashes and the at_exit
    hook in main.rb never fires.  Set to <=0 to disable.
    """
    if idle_timeout <= 0:
        return
    log.info("Idle watchdog enabled: %ds", int(idle_timeout))
    while True:
        time.sleep(min(60.0, idle_timeout / 2))
        with _activity_lock:
            idle = time.time() - _last_activity_ts
        if idle >= idle_timeout:
            log.info("Idle for %ds (>= %ds) - auto-shutting down", int(idle), int(idle_timeout))
            _async_shutdown("idle timeout")
            return


def _install_signal_handlers() -> None:
    """Convert SIGTERM/SIGINT into a clean server.shutdown()."""
    def _handler(signum, frame):
        log.info("Received signal %s - shutting down", signum)
        _async_shutdown(f"signal {signum}")
    for sig_name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, _handler)
            except (ValueError, OSError):
                # signal() can fail when called outside the main thread
                # on Windows; safe to ignore.
                pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Fish-Speech TTS sidecar")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--checkpoint-dir", type=Path, default=DEFAULT_CHECKPOINT_DIR)
    parser.add_argument("--reference-wav", type=Path, default=DEFAULT_REF_WAV)
    parser.add_argument("--reference-txt", type=Path, default=DEFAULT_REF_TXT)
    parser.add_argument("--device", choices=["auto", "cuda", "cpu", "mps"], default="auto")
    # torch.compile gives 2-3x faster inference on CUDA after a ~30-90 s JIT
    # compile during warmup.  Default OFF - the first launch then takes ~10 s
    # instead of ~50 s, at the cost of slightly slower per-request inference.
    # Persistent compile artifacts (TORCHINDUCTOR_CACHE_DIR, set at the top of
    # this file) mean that turning compile back on later only pays the full
    # JIT cost once across all future launches.
    parser.add_argument("--compile", dest="compile_model", action="store_true",
                        default=True,
                        help="Enable torch.compile() for ~2-3x faster inference on CUDA "
                             "(DEFAULT ON). setup.py installs Triton + the Python dev "
                             "files it needs, and the server verifies a real generation "
                             "before committing - if compile can't work on this machine "
                             "it silently falls back to uncompiled (never silent). The "
                             "first successful launch compiles (~1-3 min); the cached "
                             "kernels make later launches fast. No effect on CPU/MPS.")
    parser.add_argument("--no-compile", dest="compile_model", action="store_false",
                        help="Disable torch.compile() and always run uncompiled.")
    parser.add_argument("--compile-backend",
                        choices=["inductor", "cudagraphs", "aot_eager"],
                        default="inductor",
                        help="torch.compile backend when --compile is on. "
                             "'inductor' (DEFAULT) gives the real ~2-3x and "
                             "compiles ONCE (cached). It needs Triton (setup.py "
                             "installs triton-windows) and the embeddable Python's "
                             "headers/libs (setup.py provisions these). "
                             "'cudagraphs' needs neither but on fish-speech it "
                             "re-compiles every call and is usually SLOWER here, "
                             "so only use it if inductor cannot be made to work.")
    parser.add_argument("--compile-mode",
                        choices=["default", "reduce-overhead", "none"],
                        default="default",
                        help="Inductor-only mode (ignored for the cudagraphs "
                             "backend). 'default' is robust; 'reduce-overhead' "
                             "adds CUDA graphs for a little more speed.")
    parser.add_argument("--self-test", action="store_true", default=False,
                        help="Load the model (honouring --compile / --compile-mode), "
                             "run a few timed test generations, print PASS/FAIL + "
                             "timings, then EXIT without starting the HTTP server. "
                             "Use this to confirm compile works and measure the "
                             "speedup before enabling it for gameplay.")
    parser.add_argument("--full-warmup", action="store_true", default=False,
                        help="Run a second, longer warmup pass after model load. "
                             "Only useful when --compile is on; adds ~20 s.")
    parser.add_argument("--fast-start", action="store_true", default=False,
                        help="Skip ALL warmup work after loading the weights. "
                             "Time-to-ready drops by ~4 s but the very first "
                             "/tts request will be slower (CUDA kernels JIT on "
                             "demand, reference is encoded on the fly).  Recommended "
                             "only when the player is willing to trade first-entry "
                             "latency for a faster boot.")
    parser.add_argument("--idle-timeout", type=float, default=DEFAULT_IDLE_TIMEOUT,
                        help="Auto-shutdown after N seconds of no /tts activity. 0 to disable. "
                             f"Default: {DEFAULT_IDLE_TIMEOUT}.")
    parser.add_argument("--parent-pid", type=int, default=0,
                        help="Exit automatically when this PID is no longer running. "
                             "Pass the game's process ID so the server shuts down when "
                             "the game closes (even if at_exit doesn't fire).")
    args = parser.parse_args()

    # Tell the vendored engine which torch.compile backend + mode to use; both
    # are read in fish_speech/models/text2semantic/inference.py at compile time.
    os.environ["POKEDEX_VO_COMPILE_BACKEND"] = args.compile_backend
    os.environ["POKEDEX_VO_COMPILE_MODE"] = args.compile_mode

    # Compile is ON by default and safe (verify-then-commit falls back to
    # uncompiled if it can't compile here).  To avoid paying a failed-compile
    # cost on EVERY launch, a machine that already failed to compile drops a
    # .compile_disabled marker (written after load, below); honour it here and
    # skip compile until a (re)install clears it or the user forces a retry with
    # POKEDEX_VO_COMPILE=1.  --self-test always honours the explicit flag.
    _compile_marker = HERE / ".compile_disabled"
    _force_compile = os.environ.get("POKEDEX_VO_COMPILE", "") == "1"
    if (args.compile_model and not _force_compile and not args.self_test
            and _compile_marker.exists()):
        log.info("Compile previously failed on this machine (%s present) - running "
                 "uncompiled this launch. Delete it or re-run setup.py to retry.",
                 _compile_marker.name)
        print("[fish-tts] .compile_disabled present (a previous compile failed) - "
              "running uncompiled. Delete it to retry.", file=sys.stderr)
        args.compile_model = False

    # When compile is in effect, pre-warm thoroughly: the second warmup pass
    # generates a longer sentence so torch.compile specialises the kernels for
    # realistic Pokedex-entry sequence lengths DURING startup - i.e. while the
    # player is still walking to the Pokedex.  Skipped entirely under --fast-start.
    if args.compile_model and not args.fast_start:
        args.full_warmup = True

    if not args.checkpoint_dir.exists():
        print(f"[fish-tts] ERROR: checkpoint dir not found: {args.checkpoint_dir}",
              file=sys.stderr)
        print("[fish-tts] Run setup.py to download the model.", file=sys.stderr)
        return 2

    if not args.reference_wav.exists():
        print(f"[fish-tts] WARNING: reference WAV missing: {args.reference_wav}",
              file=sys.stderr)
        print("[fish-tts] The server will run but voices won't be cloned.",
              file=sys.stderr)

    device = None if args.device == "auto" else args.device

    # Pre-flight CUDA check: catch CPU-only torch wheels before we crash deep in a thread.
    if device == "cuda":
        try:
            import torch as _torch
            if not _torch.cuda.is_available():
                cuda_ver = getattr(_torch.version, "cuda", None)
                print("[fish-tts] ERROR: --device cuda requested but CUDA is not available.",
                      file=sys.stderr)
                print(f"[fish-tts]   torch version    : {_torch.__version__}", file=sys.stderr)
                print(f"[fish-tts]   torch.version.cuda: {cuda_ver}", file=sys.stderr)
                if cuda_ver is None:
                    print("[fish-tts]   This torch build has NO CUDA support (CPU-only wheel).",
                          file=sys.stderr)
                    print("[fish-tts]   Run this to fix it, then restart the server:",
                          file=sys.stderr)
                    print(f"[fish-tts]     {sys.executable} -m pip install torch torchaudio "
                          "--index-url https://download.pytorch.org/whl/cu121", file=sys.stderr)
                return 3
        except ImportError:
            print("[fish-tts] ERROR: torch is not installed.", file=sys.stderr)
            return 3

    # ------------------------------------------------------------------------
    # IMPORTANT ORDERING: bind the HTTP socket BEFORE loading the model.
    #
    # Previously we loaded the model synchronously here and only started the
    # HTTP server after warmup finished - which meant /health returned
    # ECONNREFUSED for ~50 s on every startup with --compile, even though the
    # mod is perfectly happy to be told "not ready yet, try again later".
    #
    # We now bind the socket immediately, start serve_forever() in a thread,
    # and load the model in the background.  /health reports
    # {ok: false, status: "warming"} until load completes, which is the
    # signal the Ruby side uses to fall back to Piper (or just skip audio)
    # for the very first Pokedex entry.  Subsequent entries pick up the
    # cloned voice automatically once warmup finishes.
    # ------------------------------------------------------------------------
    # ------------------------------------------------------------------------
    # --self-test: load (honouring --compile), run a few timed generations,
    # report, and exit WITHOUT binding the socket.  Lets the user verify compile
    # works + measure the speedup on their own GPU before enabling it in-game.
    # ------------------------------------------------------------------------
    if args.self_test:
        print(f"[fish-tts] SELF-TEST: loading model (compile={args.compile_model}, "
              f"mode={args.compile_mode})...", file=sys.stderr)
        ModelHolder.load(args.checkpoint_dir, args.reference_wav, args.reference_txt,
                         device_override=device, compile_model=args.compile_model,
                         full_warmup=False, skip_warmup=False)
        if not _model_state["ready"]:
            print(f"[fish-tts] SELF-TEST FAILED to load: {_model_state.get('load_error')}",
                  file=sys.stderr)
            return 5
        if args.compile_model and not _model_state.get("compiled"):
            print(f"[fish-tts] SELF-TEST NOTE: --compile (backend="
                  f"{args.compile_backend}) was requested but the compiled engine "
                  f"FAILED verification, so the server fell back to UNCOMPILED.",
                  file=sys.stderr)
            note = _model_state.get("compile_note")
            if note:
                print(f"[fish-tts] SELF-TEST: compile failure reason => {note}",
                      file=sys.stderr)
            print("[fish-tts] SELF-TEST: try a different backend, e.g. "
                  "  server.py --compile --compile-backend inductor   (needs "
                  "Triton+cl.exe), or paste the reason above for help.",
                  file=sys.stderr)
        sentences = [
            "This gluttonous Pokemon only assists people with their work because it wants treats.",
            "It has an extremely sharp sense of direction and can unerringly return home to its nest.",
            "Ratmander is very skittish and will wave its tail at anything it sees as a threat.",
        ]
        times = []
        for i, sentence in enumerate(sentences, 1):
            t0 = time.time()
            try:
                wav = synthesize(sentence)
            except Exception as exc:
                print(f"[fish-tts] SELF-TEST gen {i} FAILED: {exc}", file=sys.stderr)
                return 6
            dt = time.time() - t0
            times.append(dt)
            print(f"[fish-tts] SELF-TEST gen {i}: {dt:.2f}s ({len(wav)} bytes)",
                  file=sys.stderr)
        avg = sum(times) / len(times) if times else 0.0
        print(f"[fish-tts] SELF-TEST PASS: device={_model_state.get('device')} "
              f"compiled={_model_state.get('compiled')} "
              f"avg={avg:.2f}s/sentence over {len(times)} gens.", file=sys.stderr)
        return 0

    global _http_server
    try:
        _http_server = ThreadingHTTPServer((args.host, args.port), TTSHandler)
    except OSError as exc:
        print(f"[fish-tts] ERROR: could not bind {args.host}:{args.port}: {exc}",
              file=sys.stderr)
        return 4

    # Allow the process to exit immediately when serve_forever() returns,
    # even if a synthesis thread is still running.  Without this, Python
    # waits for every active handler thread to finish (which can take 30+
    # seconds on CPU) before it exits - keeping the window open long after
    # the game has already closed.
    _http_server.daemon_threads = True

    compiled_note = " (torch.compile ON)" if args.compile_model else ""
    print(f"[fish-tts] HTTP listening on http://{args.host}:{args.port} "
          f"- loading model in background{compiled_note}...",
          file=sys.stderr)
    log.info("Listening on %s:%d (model loading in background)",
             args.host, args.port)

    # Record our PID and start the auxiliary threads (signal handlers,
    # idle-shutdown watchdog, parent-PID monitor) so the server cleans
    # itself up cleanly under all the ways a game session can end.
    _write_pid_file()
    _install_signal_handlers()
    _bump_activity()
    # Arm the idle-shutdown watchdog ONLY when there is no live parent
    # process to monitor.  When the game passes --parent-pid (it always
    # does), the parent-PID monitor below is the real safety net: it
    # shuts the server down within ~5s of the game closing, even when
    # at_exit never fires.  In that case the idle watchdog has no job to
    # do except kill a perfectly healthy, in-use server after a few quiet
    # minutes of play -- exactly the mid-session "TTS just stopped"
    # failure this guards against.  Manual launches with no --parent-pid
    # still get the idle watchdog as orphan protection.
    parent_is_monitored = bool(args.parent_pid and args.parent_pid > 0
                               and _is_pid_alive(args.parent_pid))
    if args.idle_timeout and args.idle_timeout > 0 and not parent_is_monitored:
        threading.Thread(target=_idle_watchdog,
                         args=(args.idle_timeout,),
                         name="fish-tts-idle-watchdog",
                         daemon=True).start()
    elif parent_is_monitored:
        log.info("Idle watchdog disabled: parent pid %d is being monitored "
                 "(parent-PID monitor is the shutdown safety net).",
                 args.parent_pid)
    if args.parent_pid and args.parent_pid > 0:
        # Primary shutdown path for the "game closed without at_exit firing"
        # case (common with MKXP-Z on Windows).
        threading.Thread(target=_parent_pid_monitor,
                         args=(args.parent_pid,),
                         name="fish-tts-parent-monitor",
                         daemon=True).start()

    # Kick off model load + warmup in a background thread.  /health
    # answers "warming" until this completes, then "ready".
    def _background_load() -> None:
        try:
            ModelHolder.load(args.checkpoint_dir, args.reference_wav,
                             args.reference_txt,
                             device_override=device,
                             compile_model=args.compile_model,
                             full_warmup=args.full_warmup,
                             skip_warmup=args.fast_start)
            if _model_state["ready"]:
                # Remember the compile outcome so we neither keep paying a failed
                # compile cost every launch, nor stay disabled once it works.
                # Only touch the marker when we actually attempted compile.
                try:
                    if args.compile_model and not _model_state.get("compiled"):
                        _compile_marker.write_text(
                            "torch.compile verification failed on this machine; "
                            "delete this file (or re-run setup.py) to retry.",
                            encoding="utf-8")
                        log.info("Wrote %s - compile will be skipped next launch.",
                                 _compile_marker.name)
                    elif _model_state.get("compiled"):
                        try:
                            _compile_marker.unlink()
                        except OSError:
                            pass
                except Exception as exc:
                    log.debug("compile marker update skipped: %s", exc)
                print(f"[fish-tts] Model READY "
                      f"(device={_model_state['device']}, "
                      f"api={_model_state['api_version']}, "
                      f"compiled={_model_state.get('compiled')})",
                      file=sys.stderr)
            else:
                print(f"[fish-tts] Model load FAILED: "
                      f"{_model_state.get('load_error')}", file=sys.stderr)
        except Exception as exc:
            log.exception("Background model load crashed: %s", exc)
            _model_state["load_error"] = f"{exc.__class__.__name__}: {exc}"

    threading.Thread(target=_background_load,
                     name="fish-tts-model-loader",
                     daemon=True).start()

    try:
        _http_server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down (KeyboardInterrupt)")
    finally:
        try:
            _http_server.server_close()
        finally:
            _remove_pid_file()
    return 0


if __name__ == "__main__":
    sys.exit(main())
