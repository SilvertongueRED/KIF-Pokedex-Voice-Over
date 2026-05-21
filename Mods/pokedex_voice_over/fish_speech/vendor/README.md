# Vendored Fish-Speech engine (do not hand-edit)

This directory is a pinned, **offline** copy of the **fish-speech 1.5** engine
that `../server.py` loads at runtime (it is prepended to `sys.path`). It exists
so the mod never has to `pip install fish-speech` from GitHub or PyPI — see the
"Offline & self-contained design" section of the repository README.

## Provenance

| Field | Value |
|---|---|
| Upstream project | `fishaudio/fish-speech` — https://github.com/fishaudio/fish-speech |
| Release / tag | **v1.5.0** — https://github.com/fishaudio/fish-speech/releases/tag/v1.5.0 |
| Model checkpoint | `fishaudio/fish-speech-1.5` — https://huggingface.co/fishaudio/fish-speech-1.5 |
| License | Apache-2.0 (upstream `LICENSE`) |
| Vendored on | 2026-05-21 |
| What was copied | the upstream `fish_speech/` and `tools/` packages (incl. `fish_speech/configs/`) — inference path only |

Heavy training / ASR / web-UI assets were intentionally left out. The runtime
third-party dependencies the engine imports are pinned separately in
`../requirements-runtime.txt`.

## Local modifications (NOT upstream)

* `.project-root` — empty marker so `pyrootutils.setup_root(indicator=".project-root")` resolves this dir.
* An empty `__init__.py` was added to every package directory so the tree is an
  unambiguous *regular* package and always wins on `sys.path` over any stray
  pip-installed fish-speech. No upstream `.py` **content** was modified.

## Integrity

Manifest hash — `sha256` over `sha256sum` of every file here except this
`README.md` and `__pycache__`, paths sorted (95 files):

    faa6a3c99e9139fa2c3f7c8cc7033131da2df6745d56fe887e6073301a51241c

Verify from this directory:

    find . -type f -not -path '*__pycache__*' ! -name 'README.md' | LC_ALL=C sort | xargs sha256sum | sha256sum

## Refreshing to a newer fish-speech 1.x release

1. `git clone --branch <tag> https://github.com/fishaudio/fish-speech`
2. Replace this dir's `fish_speech/` and `tools/` with the clone's.
3. Re-add `.project-root` and an empty `__init__.py` in every package dir.
4. Verify `../server.py`'s imports resolve and the checkpoint still loads.

**Do not move to fish-speech 2.x** — it removed the firefly VQ-GAN module
(`fish_speech.models.vqgan.modules.fsq.DownsampleFiniteScalarQuantize`) that the
1.5 checkpoint instantiates. See the repo README maintainer note.
