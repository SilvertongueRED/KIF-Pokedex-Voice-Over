Python 3.12 C headers (Include/) and import libraries (libs/) for the bundled
embeddable Python. The Windows embeddable distribution ships WITHOUT these, but
torch.compile's inductor backend (via Triton) needs them to build its launcher.
setup.py's provision_python_dev_files() copies these into python/Include and
python/libs on install so compile works out of the box - no download required.
These are the standard, redistributable CPython 3.12 dev files.
