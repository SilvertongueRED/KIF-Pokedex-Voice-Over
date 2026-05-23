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

    # Use float16 on CUDA (matches fish-speech training precision, faster on GPU
    # tensor cores).  Bfloat16 on CPU/MPS to avoid float16 underflow off-GPU.
    precision = torch.float16 if device == "cuda" else torch.bfloat16

    # torch.compile only helps on CUDA.  Force-disable on CPU/MPS regardless
    # of what the user asked for - saves us debugging weird Triton errors.
    do_compile = bool(compile_model) and device == "cuda"

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
            log.info("Loading fish-speech 1.5 (device=%s, ckpt=%s, compile=%s)",
                     device, checkpoint_dir, compile_model)

            try:
                engine, precision, compiled = _load_fish_speech_1_5(
                    checkpoint_dir, device, compile_model)

                prompt_text = ref_txt.read_text(encoding="utf-8").strip() if ref_txt.exists() else ""
                ref_audio_bytes = ref_wav.read_bytes() if ref_wav.exists() else None

                _model_state["engine"] = engine
                _model_state["precision"] = precision
                _model_state["device"] = device
                _model_state["prompt_text"] = prompt_text
                _model_state["prompt_audio_bytes"] = ref_audio_bytes
                _model_state["api_version"] = "1.5"
                _model_state["compiled"] = compiled
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
        # Warmup is intentionally OUTSIDE the lock - the model is fully
        # constructed at this point and fish-speech's LLaMA queue is
        # thread-safe under concurrent inference.  This means /health
        # answers "ready" the instant the weights are in memory, and the
        # first /tts request races with (or shortly follows) the warmup.
        # ------------------------------------------------------------------

        # Phase A - prime the reference encoding.  Cheap, always synchronous.
        # If we have a disk-cached encoding for this exact reference WAV we
        # skip the encode entirely; otherwise we encode once and persist the
        # result for next time.
        if not skip_warmup:
            _prime_reference(engine, ref_audio_bytes, prompt_text)

            # Phase B - kernel warmup on a daemon thread.  We DO NOT
            # join() this thread - the server is allowed to start serving
            # the moment phase A returns.
            warmup_thread = threading.Thread(
                target=_warmup_kernels,
                args=(engine, ref_audio_bytes, prompt_text, compiled),
                kwargs={"full": full_warmup},
                name="fish-tts-kernel-warmup",
                daemon=True,
            )
            warmup_thread.start()
        else:
            log.info("Warmup skipped (--fast-start) - first /tts request will "
                     "be slower than subsequent ones.")


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
                        default=False,
                        help="Enable torch.compile() for 2-3x faster inference on CUDA. "
                             "Adds ~30-90 s to the FIRST launch only (cache persists).")
    parser.add_argument("--no-compile", dest="compile_model", action="store_false",
                        help="Disable torch.compile() (default - fastest startup).")
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
                print(f"[fish-tts] Model READY "
                      f"(device={_model_state['device']}, "
                      f"api={_model_state['api_version']})",
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
