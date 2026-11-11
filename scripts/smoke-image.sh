#!/bin/sh
# Start a built image and check it comes up: the API answers, the UI is in it, and
# makemkvcon finds all its libraries. CI runs this before an image is pushed.
#   scripts/smoke-image.sh localhost/ripshelf:latest
# Needs docker (or DOCKER=podman) and curl. The container gets no drives and keeps
# nothing: its volumes are thrown away with it.
set -eu
image=$1
docker=${DOCKER:-docker}
port=${SMOKE_PORT:-18380}
name=ripshelf-smoke-$$

fail() {
  echo "smoke: $*" >&2
  "$docker" logs "$name" >&2 2>&1 || true
  exit 1
}

libs=$("$docker" run --rm --entrypoint ldd "$image" /usr/bin/makemkvcon) || { echo "smoke: no makemkvcon" >&2; exit 1; }
case $libs in *'not found'*) echo "smoke: makemkvcon is missing libraries:" >&2; echo "$libs" >&2; exit 1 ;; esac

"$docker" run -d --name "$name" -p "127.0.0.1:$port:8380" "$image" >/dev/null
trap '"$docker" rm -fv "$name" >/dev/null 2>&1 || true' EXIT

# The database is created and the drives looked for before the port opens.
i=0
until status=$(curl -fsS "http://127.0.0.1:$port/api/status" 2>/dev/null); do
  i=$((i + 1))
  [ "$i" -lt 60 ] || fail "no answer on /api/status after 60 s"
  [ "$("$docker" inspect -f '{{.State.Running}}' "$name")" = true ] || fail "the container stopped"
  sleep 1
done
echo "smoke: /api/status: $status"
case $status in *'"version"'*) ;; *) fail "/api/status has no version" ;; esac

curl -fsS "http://127.0.0.1:$port/" | grep -qi '<!doctype html' || fail "/ doesn't serve the UI"
echo "smoke: ok"
