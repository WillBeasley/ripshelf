# CI on GitHub

Run the tests and publish multi-arch images to GHCR with GitHub Actions.

There are three workflows in `.github/workflows/`:
- **`test.yml`** runs on pushes to `main` and on pull requests. It runs ruff, svelte-check, the UI build and then pytest (12 at a time, with pytest-xdist, so the tests check the built UI is served), with Node 22. A second job, `image`, builds the image for amd64 without pushing it and starts it with `scripts/smoke-image.sh`, which checks that the API answers, the UI is served and `makemkvcon` finds all its libraries. Only `main` writes that job's layer cache, which pull requests then read. It runs in the image's base, `python:3.12-slim-bookworm`, so the tests use the same Python and the same Debian ffmpeg the image transcodes with, and pytest runs as a normal user so the file-permission tests aren't skipped. Changes only to `docs/` or Markdown files don't start it, and a newer push to the same branch or pull request cancels a run still going. A third job, `ui`, runs the UI's unit tests and its browser tests (Playwright, Chromium) against the backend on the simulated drives, directly on the Ubuntu runner, and keeps the Playwright report when they fail. A fourth, `windows`, runs the whole suite on a Windows runner (4 at a time), so the Windows-only code (process Job Objects, drive letters, volume details) runs against the real kernel32 and the rest meets Windows paths, file locks and encodings. The tests' fake programs are Python scripts, which `fake_program()` in `tests/conftest.py` turns into `.exe` launchers on Windows. It builds the UI and installs MKVToolNix, Tesseract and ffmpeg with Chocolatey first, so episode splitting and cover reading are tested with the real programs, found in their installers' folders the way the app finds them. Tests of Linux-only code (sysfs, SG_IO, signals, process groups, file modes) skip there. It has no optical drive either, so the drive tests check the calls work, not what a drive says. Both test jobs measure coverage, and a fifth job, `coverage`, combines the two: the run's summary page shows the total for Linux, for Windows and for both, the files Windows covers more than 5 points less of than Linux, and the combined report by file, and the `coverage-html` artifact has the full HTML report. `scripts/coverage-summary.py` writes that summary, and `[tool.coverage.paths]` in `pyproject.toml` maps both platforms' file names to `src/ripshelf`. No coverage threshold fails the run yet. Run it by hand from Actions → test → Run workflow.
- **`images.yml`** builds the image for `linux/amd64` and `linux/arm64` and pushes it to the GitHub Container Registry as `ghcr.io/willbeasley/ripshelf`. It runs only for release tags, so pushes to `main` and pull requests publish no image. It runs `test.yml` first and pushes nothing if the tests fail, and each platform's image is started with `scripts/smoke-image.sh` before it's pushed. The tags are:

  | Event | Tags |
  |---|---|
  | tag `v1.2.3` | `:1.2.3`, `:1.2`, `:latest` |
  | tag `v1.3.0-rc.1` (a pre-release) | `:1.3.0-rc.1` only |
  | run by hand from a tag | the same tags again, rebuilt |
  | run by hand from a branch | nothing (the jobs are skipped) |

  Running it by hand from a tag rebuilds that release in place, for example to pick up a new MakeMKV. In Actions → images → Run workflow, set "Use workflow from" to the tag.
- **`audit.yml`** checks the Python lock files with [pip-audit](https://pypi.org/project/pip-audit/) against the Python advisory database, every Monday and on any push or pull request that changes them. It fails when a locked package has a known vulnerability; `scripts/lock-deps.sh --upgrade` usually fixes it.

Dependabot covers the rest (`.github/dependabot.yml`): one grouped pull request a week at most for each of the UI's npm packages, the actions these workflows use, and the Dockerfile's base images, which are pinned by digest. Its security updates are switched on in Settings → Code security and open a pull request as soon as an advisory affects a UI package.

## How the image is built

- Each platform builds natively on its own runner, `ubuntu-24.04` for amd64 and `ubuntu-24.04-arm` for arm64. No QEMU, so MakeMKV compiles at full speed on both.
- Each build is pushed by digest only. A last job, `merge`, joins the two digests into one multi-arch manifest and gives it the tags.
- Layers are cached in the registry, as `ghcr.io/<owner>/ripshelf:buildcache-linux-amd64` and `:buildcache-linux-arm64`, every stage included (`mode=max`). Unlike the GitHub Actions cache, which drops what goes unused for a week and which a tag can't share with another tag, it lasts from one release to the next, so a release compiles ffmpeg and MakeMKV again only when they or the Debian image they build on change. The amd64 build also reads `main`'s Actions cache from `test.yml`, and `test.yml` reads the registry cache (when the package is public, or the job can log in). Those two tags show up beside the release tags in the package; deleting them only makes the next release build from scratch.
- ffmpeg and MakeMKV build on `debian:bookworm-slim`, pinned on its own, and the final stage copies them in. A newer Python image then reinstalls only the packages and ripshelf.

## Setting it up

There's nothing to configure. The workflow logs in with the job's own `GITHUB_TOKEN` and has `packages: write`.

- **Visibility.** A new package starts private. To let anyone pull it, go to the package's page (your profile → Packages → ripshelf) → Package settings → Change visibility → Public. On the same page, "Connect repository" puts it on the repo's front page.
- **Arm runners.** `ubuntu-24.04-arm` is free for public repositories. A private repository needs a paid plan for it. Otherwise change the arm64 runner to `ubuntu-24.04` and add `docker/setup-qemu-action@v4` before `docker/setup-buildx-action` in the `build` job. This is slower, because MakeMKV is then compiled under emulation.

## Releasing

1. Make sure `main` is what you want to ship and the tests pass: `ruff check src tests && pytest -q`, and `npm run check && npm test && npm run test:e2e` in `web/`. To check an image you built, `scripts/smoke-image.sh localhost/ripshelf:latest`.
2. Pick the version. While it's `0.x`, raise the minor (`0.3.0` → `0.4.0`) for new features or anything that changes settings or the database, and the patch (`0.4.0` → `0.4.1`) for fixes only.
3. Set it in three places, `version` in `pyproject.toml`, `__version__` in `src/ripshelf/__init__.py` and `version` in `web/package.json` and its lock file (`cd web && npm version 0.4.0 --no-git-tag-version`). Commit: `git commit -am "release: 0.4.0"`. The release badge in `README.md` reads the newest tag from GitHub, so it needs no change.
4. Tag that commit and push both. An annotated tag carries a message and, with signing set up, a signature:

   ```sh
   git tag -s v0.4.0 -m "ripshelf 0.4.0"   # -a instead of -s for an unsigned tag
   git push origin main v0.4.0
   ```

5. The tag starts `images.yml`, which runs the tests first. Watch it under Actions. The first release takes longest, because MakeMKV is compiled on both runners.
6. On GitHub, Releases → Draft a new release → choose the tag, and write the release notes from the commits since the last release (`git log --oneline v0.3.0..v0.4.0`, or "Generate release notes" to start from the pull requests). Put anything an upgrade needs first, under **Upgrading**, then Added, Changed and Fixed. Publish. Tick "Set as a pre-release" for an `-rc` tag.
7. Check the image: `docker pull ghcr.io/willbeasley/ripshelf:0.4.0`.

A tag that went wrong (a failed build, a missed commit) can be deleted and pushed again before anyone pulls it: `git tag -d v0.4.0 && git push origin :refs/tags/v0.4.0`. After the image is out, release `0.4.1` instead, because `:0.4.0` is meant never to move.

Installing from the registry is covered in [installation.md](installation.md).
