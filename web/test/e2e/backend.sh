#!/bin/sh
# The backend the browser tests run against: the simulated drives, scanner and vision model
# (as scripts/dev-fake.sh), with fresh state each run and nothing asked of the internet.
set -eu
cd "$(dirname "$0")/../../.."
state=web/test/e2e/.state
rm -rf "$state"
mkdir -p "$state/library"
export RIPSHELF_ENV_FILE=/dev/null
export RIPSHELF_FAKE_HARDWARE=1
export RIPSHELF_DATA_DIR="$state/data" RIPSHELF_SCRATCH_DIR="$state/scratch" RIPSHELF_LIBRARY_DIR="$state/library"
export RIPSHELF_PORT="${RIPSHELF_PORT:-8391}"
export RIPSHELF_STATIC_DIR=web/build
export RIPSHELF_SCAN_TIMEOUT_S=5 RIPSHELF_POLL_INTERVAL_S=1
export RIPSHELF_FAKE_RIP_SECONDS=2 RIPSHELF_FAKE_VISION_SECONDS=0
export RIPSHELF_SCANNER_ENABLED=1 RIPSHELF_SCANNER_URL="http://127.0.0.1:$RIPSHELF_PORT/fake-escl/eSCL"
export RIPSHELF_VISION_MODE=struggles RIPSHELF_VISION_MODEL=fake-vl
export RIPSHELF_VISION_URL="http://127.0.0.1:$RIPSHELF_PORT/fake-vision"
# TMDB is the pretend one (the made-up films and shows in tests/fixtures). Nothing is
# confirmed without review, so every disc waits for the tests. The providers that need no
# key are switched off, and anything else sent out goes to a proxy that isn't there.
export RIPSHELF_FAKE_TMDB=1 RIPSHELF_TMDB_API_KEY=fake
export RIPSHELF_AUTO_CONFIRM_THRESHOLD=1 RIPSHELF_TV_AUTO_CONFIRM=false
export RIPSHELF_METADATA_PROVIDERS='{"movie":[{"name":"thediscdb","enabled":false}],"tv":[{"name":"thediscdb","enabled":false},{"name":"tvmaze","enabled":false}],"episodes":[{"name":"tvmaze","enabled":false}]}'
export HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 NO_PROXY=127.0.0.1,localhost
if [ -x .venv/bin/ripshelf ]; then exec .venv/bin/ripshelf; else exec ripshelf; fi
