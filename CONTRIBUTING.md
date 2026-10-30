# Contributing

How to run ripshelf from source, test it, and send changes.

## Development setup

ripshelf is one service, Python 3.12 and FastAPI with SQLite, serving a SvelteKit UI that it builds into itself.

```sh
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.lock && .venv/bin/pip install --no-deps -e .
(cd web && npm ci && npm run build)    # the UI, served by the backend from web/build
cp example.env ripshelf.env             # add your TMDB key (git ignores this file)
.venv/bin/ripshelf                      # http://localhost:8380 with the local drives
scripts/dev-fake.sh                    # http://localhost:8381 with simulated drives
```

The simulated drives replay recorded MakeMKV logs of a UHD film, a DVD film, a TV compilation, and a scan that never finishes. Fake rips are tiny real videos when ffmpeg is installed.

`cd web && npm run dev` gives a hot-reloading UI that proxies `/api` to `RIPSHELF_BACKEND` (default `http://127.0.0.1:8381`).

## Tests

```sh
.venv/bin/ruff check src tests
.venv/bin/pytest -n 12                 # about 2 minutes in parallel, 11 without; add --cov for branch coverage
.venv/bin/pytest -n 12 -m "not slow"   # without the dozen end-to-end tests that take 10 s or more
(cd web && npm run check)              # svelte-check
```

The tests mostly wait on simulated rips, so `-n` can be about three times the number of cores. The transcode and TV tests need `ffmpeg` on the PATH and are skipped without it. They cover:

- **Parsing, scoring and TMDB.** Recorded MakeMKV logs and TMDB responses in `tests/fixtures` (see its README).
- **Pipeline.** Whole jobs on the fake drives, with stand-in makemkvcon scripts for crashes, hangs and bad output.
- **Workers.** A real controller and worker talking over HTTP.
- **Real drives.** `RIPSHELF_REAL_DRIVES=1` adds a read-only check of the local drives.

## Dependencies

The Python packages are pinned, with hashes, in `requirements.lock` (what the image and the Windows start script install) and `requirements-dev.lock` (that plus the test tools). Both cover Linux amd64 and arm64 and Windows. `pyproject.toml` keeps the version ranges ripshelf works with.

After changing the dependencies in `pyproject.toml`, run `scripts/lock-deps.sh` to add them to the locks without moving anything else. To move everything to the newest versions the ranges allow, run `scripts/lock-deps.sh --upgrade`, then the tests, and commit the two lock files together. The script needs uv (`.venv/bin/pip install uv` is enough).

The UI's packages are pinned in `web/package-lock.json`, and `npm ci` installs exactly those.

## Code layout

| Path | What's there |
|---|---|
| `src/ripshelf/jobs.py` | the job pipeline (scan, identify, rip, finalise) |
| `src/ripshelf/identify/`, `tv/`, `providers/` | film and TV identification, and metadata providers |
| `src/ripshelf/makemkv/` | running `makemkvcon` and parsing its output |
| `src/ripshelf/workers/` | the worker agent and the controller's side of it |
| `src/ripshelf/hw/` | drive discovery (Linux sysfs and SG_IO, Windows) |
| `src/ripshelf/dev/` | simulated drives and a fake `makemkvcon` |
| `web/` | the SvelteKit UI |

`src/ripshelf/models.py` is the API contract. After changing it, run `scripts/gen-types.sh` to regenerate `web/src/lib/api-types.ts`.

## Sending changes

- Keep a change to one thing, with tests for what it fixes or adds.
- Run ruff, pytest and svelte-check before sending it. CI runs the same checks ([ci-github.md](docs/ci-github.md)).
- Don't commit real keys, drive serial numbers, host names or MakeMKV logs with paths in them. Test fixtures use placeholder serials (`SN000000000N`).
