#!/bin/sh
# Run the backend on simulated drives (recorded MakeMKV logs), with state in var/fake/.
# Picks up the TMDB key from ripshelf.env (or the older var/ripshelf.env) if present.
set -eu
cd "$(dirname "$0")/.."
for f in var/ripshelf.env ripshelf.env; do
    if [ -f "$f" ]; then set -a; . "./$f"; set +a; fi
done
export RIPSHELF_ENV_FILE=/dev/null
export RIPSHELF_FAKE_HARDWARE=1
export RIPSHELF_DATA_DIR=var/fake/data RIPSHELF_SCRATCH_DIR=var/fake/scratch RIPSHELF_LIBRARY_DIR=var/fake/library
export RIPSHELF_PORT="${RIPSHELF_PORT:-8381}"
export RIPSHELF_SCAN_TIMEOUT_S="${RIPSHELF_SCAN_TIMEOUT_S:-30}" RIPSHELF_POLL_INTERVAL_S=1
export RIPSHELF_FAKE_RIP_SECONDS="${RIPSHELF_FAKE_RIP_SECONDS:-60}"
mkdir -p var/fake/library
exec .venv/bin/ripshelf
