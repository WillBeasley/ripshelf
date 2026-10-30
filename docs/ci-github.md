# CI on GitHub

Run the tests and publish multi-arch images to GHCR with GitHub Actions.

There are two workflows in `.github/workflows/`:
- **`test.yml`** runs on pushes to `main`, on `v*` tags and on pull requests. It runs ruff, pytest (12 at a time, with pytest-xdist) and svelte-check, with Python 3.12 and Node 22. Changes only to `docs/` or Markdown files don't start it, and a newer push to the same branch or pull request cancels a run still going. Run it by hand from Actions → test → Run workflow.
- **`images.yml`** builds the image for `linux/amd64` and `linux/arm64` and pushes it to the GitHub Container Registry as `ghcr.io/willbeasley/ripshelf`. It runs only for release tags, so pushes to `main` and pull requests build no image. The tags are:

  | Event | Tags |
  |---|---|
  | tag `v1.2.3` | `:1.2.3`, `:1.2`, `:latest` |
  | tag `v1.3.0-rc.1` (a pre-release) | `:1.3.0-rc.1` only |
  | run by hand from a tag | the same tags again, rebuilt |
  | run by hand from a branch | nothing (the jobs are skipped) |

  Running it by hand from a tag rebuilds that release in place, for example to pick up a new MakeMKV. In Actions → images → Run workflow, set "Use workflow from" to the tag.

## How the image is built

- Each platform builds natively on its own runner, `ubuntu-24.04` for amd64 and `ubuntu-24.04-arm` for arm64. No QEMU, so MakeMKV compiles at full speed on both.
- Each build is pushed by digest only. A last job, `merge`, joins the two digests into one multi-arch manifest and gives it the tags.
- Layers are cached in the GitHub Actions cache, one scope per platform. A tag can't read another tag's cache, so a new release builds from scratch, MakeMKV included, and a rebuild of the same tag reuses its own cache.

## Setting it up

There's nothing to configure. The workflow logs in with the job's own `GITHUB_TOKEN` and has `packages: write`.

- **Visibility.** A new package starts private. To let anyone pull it, go to the package's page (your profile → Packages → ripshelf) → Package settings → Change visibility → Public. On the same page, "Connect repository" puts it on the repo's front page.
- **Arm runners.** `ubuntu-24.04-arm` is free for public repositories. A private repository needs a paid plan for it. Otherwise change the arm64 runner to `ubuntu-24.04` and add `docker/setup-qemu-action@v4` before `docker/setup-buildx-action` in the `build` job. This is slower, because MakeMKV is then compiled under emulation.

## Releasing

1. Make sure `main` is what you want to ship and the tests pass: `ruff check src tests && pytest -q`, and `npm run check` in `web/`.
2. Pick the version. While it's `0.x`, raise the minor (`0.3.0` → `0.4.0`) for new features or anything that changes settings or the database, and the patch (`0.4.0` → `0.4.1`) for fixes only.
3. Set it in three places, `version` in `pyproject.toml`, `__version__` in `src/ripshelf/__init__.py` and the release badge at the top of `README.md` (its text and link), and commit: `git commit -am "release 0.4.0"`.
4. Tag that commit and push both. An annotated tag carries a message and, with signing set up, a signature:

   ```sh
   git tag -s v0.4.0 -m "ripshelf 0.4.0"   # -a instead of -s for an unsigned tag
   git push origin main v0.4.0
   ```

5. The tag starts `images.yml` (and `test.yml`). Watch it under Actions. The first release takes longest, because MakeMKV is compiled on both runners.
6. On GitHub, Releases → Draft a new release → choose the tag → "Generate release notes", edit them, and publish. Tick "Set as a pre-release" for an `-rc` tag.
7. Check the image: `docker pull ghcr.io/willbeasley/ripshelf:0.4.0`.

A tag that went wrong (a failed build, a missed commit) can be deleted and pushed again before anyone pulls it: `git tag -d v0.4.0 && git push origin :refs/tags/v0.4.0`. After the image is out, release `0.4.1` instead, because `:0.4.0` is meant never to move.

Installing from the registry is covered in [installation.md](installation.md).
