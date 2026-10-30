# Installation

Run ripshelf from the prebuilt image with Docker Compose, then set it up in the web UI.

The image is `ghcr.io/willbeasley/ripshelf`, for `linux/amd64` and `linux/arm64` (a Raspberry Pi 5, for example), and Docker pulls the right one. It holds the controller, the embedded worker, MakeMKV (CLI), ffmpeg and the web UI. You don't need the source code.

## Install

1. **Make a folder for the stack** (for example `~/stacks/ripshelf/`) and copy [compose.yaml](../compose.yaml) into it. For several drives, [compose.per-drive.yaml](../compose.per-drive.yaml) is faster (see [modes.md](modes.md)). Compose managers that keep one stack per folder (Portainer and similar) work the same way.

2. **Change the lines marked `<--`.** These are:
   - your user and group ids (`id -u`, `id -g`)
   - the host's cdrom and render group ids (`getent group cdrom`, `getent group render`)
   - your drives, each `/dev/srN` with its own `/dev/sgN` (`ls /sys/class/block/sr*/device/scsi_generic` shows the pairs)
   - the movies, TV and scratch folders, and your time zone
   - `RIPSHELF_ACCEPT_MAKEMKV_EULA`. The image includes MakeMKV, which is proprietary software with its own licence. Read it (in the image at `/usr/share/doc/makemkv/eula_en_linux.txt`, or from the setup page) and, if you accept it, set this to `yes`. Until then ripshelf doesn't run MakeMKV, and discs wait.

3. **Create the folders and start it.** Relative paths are relative to the stack folder. Create them yourself, so that you own them rather than root:

   ```sh
   mkdir -p data scratch movies tv disc-backups backups
   docker compose pull && docker compose up -d
   ```

4. **Open `http://<host>:8380`.** A fresh install opens a short setup page (drives, MakeMKV, folders, TMDB key). See [First-run configuration](#first-run-configuration).

   To use another port, change the left-hand side of the `ports:` line, for example `"8480:8380"` for `http://<host>:8480`. Set the same address under Notifications so links in notifications work.

By default films go to `./movies` and TV to `./tv` next to the compose file. To use an existing library, change the left-hand side of the `/movies` and `/tv` mounts (for example `/media/Movies:/movies`). Or mount one folder holding `Movies/` and `TV/` at `/library`, and delete the `RIPSHELF_MOVIES_DIR` and `RIPSHELF_TV_DIR` lines. Each folder must already exist. ripshelf never creates them, so an unmounted disk can't fill your system disk.

There's no login on the UI, so keep it on your LAN or VPN.

## First-run configuration

Everything is in Settings, and anything still missing shows as a yellow Setup box on the Overview page. Each connection has a Test button that tries the values as typed.

- **Metadata providers.** You need a free TMDB v3 API key from themoviedb.org. Without it, every disc not in TheDiscDB goes to Review. TheTVDB and OMDb keys are optional. See [providers.md](providers.md).
- **MakeMKV key.** Use a purchased key, or the current free beta key from the [MakeMKV forum](https://forum.makemkv.com/forum/viewtopic.php?t=1053). The beta key is a courtesy while MakeMKV is in beta, so if you use ripshelf, please [buy a licence](https://www.makemkv.com/buy/).
- **Import MakeMKV private data.** This takes:
  - each UHD drive's `_private_data.tar` from an existing MakeMKV install (LibreDrive data)
  - a firmware dump (`dump_*.tgz`) for an older drive that MakeMKV hangs on (see [troubleshooting.md](troubleshooting.md#an-older-drive-hangs-in-makemkv-lg-ggc-h20n-and-others))
- **Notifications.** Enter this UI's address as a phone reaches it (for example `http://192.168.1.10:8380`, used in links), and your Pushover user key and application token.
- **Emby (optional).** Enter the URL and an API key, then "Fetch libraries" to pick the Movies and TV libraries. Jellyfin and Plex are set up the same way in Settings (Plex calls its key a token).

[settings.md](settings.md) lists every setting and its environment variable.

## Tags

| Tag | What it is |
|---|---|
| `:latest` | the newest release |
| `:1.2.3` | one release, never moved |
| `:1.2` | the newest `1.2.x` release |
| `:1.3.0-rc.1` | a pre-release, for trying the next release early |

Only releases are published. To run a commit from `main`, [build the image yourself](building.md). For a machine that rips unattended, pin `:1.2` or `:1.2.3`.

## Updating

```sh
docker compose pull && docker compose up -d
```

With `:latest` this gets the newest release, and with `:1.2` the newest `1.2.x`. To move to another release, change the tag in `.env` (or the `image:` line) and run the same command. For an image you built, rebuild it ([building.md](building.md#updating)), then `docker compose up -d`.

**Don't update in the middle of a rip.** The container restarts and the job fails with "ripshelf restarted while this job was running", ready to retry. The same goes for Watchtower and similar tools, so give ripshelf the label `com.centurylinklabs.watchtower.enable=false`, or pin a release tag.

The database, settings and MakeMKV data are in `./data`, so they survive updates and switching between a pulled image and one you built. Daily database backups go to `./backups`.

## Other ways to run it

- [Build the image yourself](building.md), for another MakeMKV release or architecture.
- [One worker container per drive](modes.md), faster with several drives.
- [Remote workers](workers.md), for drives on other machines, including a Raspberry Pi.
- [Natively on Windows](windows.md), without Docker.
- [On a Raspberry Pi 5](raspberry-pi.md), in Docker or natively.
