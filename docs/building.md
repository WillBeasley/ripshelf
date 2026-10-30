# Building the image yourself

Build the Docker image from source, for development, another MakeMKV release, or another architecture.

## Requirements

You need a checkout of the code and Docker with buildx (included in current Docker). Podman works too, with `podman build` in place of `docker build`.

The [Dockerfile](../Dockerfile) builds the web UI, builds MakeMKV's open-source part from source, adds MakeMKV's `makemkvcon`, then installs ripshelf. It builds for `linux/amd64` and `linux/arm64`. The Intel VAAPI driver, for transcoding on an Intel GPU, is only installed on amd64.

## Build and run it

```sh
git clone https://github.com/WillBeasley/ripshelf.git && cd ripshelf
docker build -t localhost/ripshelf:latest .
```

Then tell Compose to use it rather than the registry's image. In the folder holding `compose.yaml`, create a `.env` file:

```sh
RIPSHELF_IMAGE=localhost/ripshelf:latest
```

and start it as usual (`docker compose up -d`). Every service, the per-drive workers too, uses that variable. Leave out `docker compose pull`, because there's nothing to pull.

### Or let Compose build it

From the checkout, [compose.build.yaml](../compose.build.yaml) adds `build: .` to the controller:

```sh
echo RIPSHELF_IMAGE=localhost/ripshelf:latest > .env
docker compose -f compose.yaml -f compose.build.yaml up -d --build
```

This keeps the stack in the checkout. If it lives in its own folder (for Portainer and similar), build with `docker build` as above instead.

## Updating

```sh
git pull && docker build -t localhost/ripshelf:latest .
```

then `docker compose up -d` in the stack folder (or the `-f compose.yaml -f compose.build.yaml up -d --build` line). As with a pulled image, don't do it in the middle of a rip.

## Pinned versions

A rebuild months later gets the same software. The Python packages come from `requirements.lock`, with their hashes checked, and the UI's from `web/package-lock.json`. MakeMKV is a fixed release with checksums (below). The Debian packages and the base images follow Debian 12 (bookworm), which only gets security and bug fixes. To move the Python packages to newer versions, see [Dependencies](../CONTRIBUTING.md#dependencies).

## Another MakeMKV release

The MakeMKV version and its checksums are build arguments:

```sh
docker build -t localhost/ripshelf:latest \
  --build-arg MAKEMKV_VERSION=<version> \
  --build-arg MAKEMKV_URL_BASE=https://www.makemkv.com/download/old \
  --build-arg MAKEMKV_OSS_SHA256=<sha256 of makemkv-oss-<version>.tar.gz> \
  --build-arg MAKEMKV_BIN_SHA256=<sha256 of makemkv-bin-<version>.tar.gz> .
```

Only the newest release is under `/download`. Older ones are under `/download/old`.

## For another architecture

To build on a PC for a Raspberry Pi (or the other way round), or for both at once, use buildx with QEMU:

```sh
docker run --privileged --rm tonistiigi/binfmt --install arm64   # once per boot; amd64 for the other way
docker buildx create --name multi --use                          # once
docker buildx build --platform linux/amd64,linux/arm64 \
  -t registry.example.com/you/ripshelf:latest --push .
```

A multi-arch image can't be loaded into the local Docker, so it's pushed to a registry. For one other platform, `--platform linux/arm64 -t localhost/ripshelf:arm64 --load` loads it locally instead.

Under QEMU, building MakeMKV for the other architecture takes much longer than a native build. Building on the Pi itself also works.

## Your own registry

To build in CI and pull on each host, use the GitHub workflow in [ci-github.md](ci-github.md). It builds amd64 and arm64 and pushes them to GHCR.

Then set `RIPSHELF_IMAGE` to that image and follow [installation.md](installation.md).
