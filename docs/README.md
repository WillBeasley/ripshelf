# ripshelf documentation

Start with [Installation](installation.md).

## Getting started

| Page | What it covers |
|---|---|
| [Installation](installation.md) | the prebuilt image with Docker Compose, first-run configuration, tags and updating |
| [How it works](how-it-works.md) | what happens to a disc, from scan to library, and how files are named |
| [Settings](settings.md) | every setting and its environment variable |
| [Troubleshooting](troubleshooting.md) | common problems and fixes, including drives that hang in MakeMKV |

## Identification

| Page | What it covers |
|---|---|
| [Metadata providers](providers.md) | TheDiscDB, TMDB, TheTVDB, TVmaze and OMDb, their keys, and the order they're asked in |
| [Contributing to TheDiscDB](thediscdb-contributing.md) | sending discs you confirmed back to TheDiscDB (opt-in), what is sent and what never is |
| [Catalogue numbers](catalogue.md) | looking up labels that are a publisher's catalogue number, via a self-hosted SearXNG web search |

## Ripping and running

| Page | What it covers |
|---|---|
| [Full disc backups](backup.md) | MakeMKV's backup ("ISO mode") instead of MKV titles |
| [One container or one per drive](modes.md) | the single container, or a worker container per drive (faster with several drives) |
| [Remote workers](workers.md) | drives on other machines, including a Raspberry Pi |
| [Raspberry Pi 5](raspberry-pi.md) | a Pi 5 with a USB drive, with setup scripts, Docker or native, tests and a first-rip checklist |
| [Windows](windows.md) | running natively on Windows, without Docker |

## Building and CI

| Page | What it covers |
|---|---|
| [Building the image](building.md) | building from source, another MakeMKV release, another architecture |
| [CI on GitHub](ci-github.md) | tests and amd64 + arm64 images on GHCR |
| [Contributing](../CONTRIBUTING.md) | running from source, simulated drives, tests |
