# Faster generation with torch.compile (now automatic)

The ~2-3x speedup from `torch.compile` (inductor backend) is **ON by default**.
You don't have to do anything to turn it on — installing the mod and launching
the game sets it up and uses it. This file just explains how it works and how
to turn it off if you ever need to.

## What happens automatically

1. **Launch the game.** The mod auto-starts the TTS server. On the very first
   run it installs everything needed, with no extra steps:
   - torch (CUDA build) + the inference dependencies,
   - `triton-windows` (the compiler backend; it ships its own C compiler, so
     you do NOT need Visual Studio / MSVC),
   - the Python C headers + import libs the embeddable Python is missing — these
     are **bundled with the mod** (`fish_speech/python_dev/`) and copied into
     place, so no download is required.
2. **First successful launch compiles the model** (~1-3 minutes). During that
   window the Pokedex falls back to its normal behavior; once it's done,
   narration is compiled and fast.
3. **The compiled kernels are cached** (`fish_speech/.torch_cache/`), so every
   later launch loads them in a few seconds — no recompile.

## It cannot break narration

Before committing the compiled model, the server runs a real test generation.
If compile can't work on a given machine (no NVIDIA GPU, an unusual setup,
etc.), it automatically falls back to normal uncompiled generation — audio
always works. A machine where compile fails writes a small
`fish_speech/.compile_disabled` marker and skips compile on later launches (so
it doesn't waste time retrying every time). A reinstall clears that marker.

On non-NVIDIA machines (CPU / Apple Silicon) compile is simply not attempted.

## Turning it off (optional)

You normally never need to, but to force plain uncompiled generation:

- set the environment variable `POKEDEX_VO_COMPILE=0`, **or**
- create an empty file `fish_speech/disable_compile.flag`.

Remove either to go back to the default (compiled).

## Checking / benchmarking (optional)

`fish_speech/Benchmark_Compile.bat` times baseline vs compiled without launching
the game. In `fish_speech/server.log` a healthy compiled run shows:
`Compiling function... (backend=inductor ...)`, then
`Verification generation OK ... [compiled]`, then
`fish-speech ready (... compiled=True ...)`.

## If a machine can't compile

Everything still works at normal speed. To retry after fixing the environment,
delete `fish_speech/.compile_disabled` (a reinstall does this automatically), or
force one attempt with `POKEDEX_VO_COMPILE=1`. As a last resort you can point the
mod at a full Python 3.12 install (which already has headers/libs) via the
`POKEDEX_VO_PYTHON` environment variable.
