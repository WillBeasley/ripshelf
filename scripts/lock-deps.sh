#!/bin/sh
# Regenerate the Python lock files from pyproject.toml, for every platform ripshelf
# runs on (Linux amd64 and arm64, Windows), with hashes. The image installs from
# requirements.lock, so a rebuild months later gets the same packages.
#   scripts/lock-deps.sh             keep the locked versions, add anything new
#   scripts/lock-deps.sh --upgrade   move everything to the newest allowed version
# Needs uv (pip install uv, or https://docs.astral.sh/uv/).
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
uv=${UV:-$(command -v uv || echo "$root/.venv/bin/uv")}
cd "$root"
lock() {
  out=$1; shift
  "$uv" pip compile --universal --python-version 3.12 --generate-hashes \
    --custom-compile-command "scripts/lock-deps.sh" --quiet -o "$out" "$@"
}
# The image builds ripshelf without build isolation, so setuptools is locked as well.
grep -o '"setuptools[<>=~!][^"]*"' pyproject.toml | tr -d '"' | lock requirements.lock pyproject.toml - "$@"
lock requirements-dev.lock pyproject.toml --extra dev "$@"
