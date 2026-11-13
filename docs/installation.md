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

There's no login on the UI, so keep it on your LAN or VPN. It does refuse changes sent by web pages on other sites, and answers only to the names a LAN uses, so a page open in your browser can't use it behind your back. If you open it by a public name (through a reverse proxy, or a Tailscale name), set that address in Settings → System or add the name to `RIPSHELF_ALLOWED_HOSTS` ([behind a reverse proxy](#behind-a-reverse-proxy)).

The compose files have a healthcheck, so `docker compose ps` shows the controller as `healthy` once it answers and its database opens. It asks `GET /api/health` (200, or 503 while shutting down or when the database fails), which monitoring tools such as Uptime Kuma can use too. In `compose.per-drive.yaml` the drive workers wait for it.

## First-run configuration

Everything is in Settings, and anything still missing shows as a yellow Setup box on the Overview page. Each connection has a Test button that tries the values as typed.

- **Metadata providers.** A free TMDB v3 API key from themoviedb.org is recommended. Without it, films not in TheDiscDB go to Review with no candidates, and TV shows are found through TVmaze (no key) or TheTVDB instead. TheTVDB and OMDb keys are optional. See [providers.md](providers.md).
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

Before moving to a new release, read its **Upgrading** notes in its [release notes](https://github.com/WillBeasley/ripshelf/releases). With `:latest` this gets the newest release, and with `:1.2` the newest `1.2.x`. To move to another release, change the tag in `.env` (or the `image:` line) and run the same command. For an image you built, rebuild it ([building.md](building.md#updating)), then `docker compose up -d`.

**Don't update in the middle of a rip.** The container restarts and the job fails with "ripshelf restarted while this job was running", ready to retry. The same goes for Watchtower and similar tools, so give ripshelf the label `com.centurylinklabs.watchtower.enable=false`, or pin a release tag.

The database, settings and MakeMKV data are in `./data`, so they survive updates and switching between a pulled image and one you built. Daily database backups go to `./backups`.

When a release changes how the database is laid out, the first start copies it aside first, as `data/ripshelf-before-vN.db`, and then upgrades it. To go back to the older release, stop ripshelf and put that copy back as `data/ripshelf.db` (jobs from after the upgrade are lost). From 0.7 on, a release refuses to open a database a later release has upgraded, rather than misread it. A job the new release can't read at all is left out of the job list and named in the log (`skipping jobs row N`), instead of stopping ripshelf.

## Behind a reverse proxy

To open ripshelf by a name of its own over HTTPS, put it behind Caddy, nginx or Traefik and set its address in Settings → System → this UI's address, for example `https://rip.example.com` (or `RIPSHELF_PUBLIC_URL`). Open the UI by its IP address to get there the first time. That address is the name ripshelf answers to, and the page changes may come from, so nothing else is needed whether the proxy passes the `Host` header on or replaces it with ripshelf's own address. `RIPSHELF_ALLOWED_HOSTS` adds more names ([settings.md](settings.md#settings-that-arent-on-the-settings-page)).

- It needs a name of its own. The UI asks for `/api/...` and its pages from the root, so it can't be served under a path such as `/ripshelf/`.
- `X-Forwarded-Host` and `Forwarded` are ignored on purpose, since any client can send them. ripshelf goes by `Host` and the address you set.
- The proxy must pass the browser's `Origin` header on unchanged. Proxies do unless told not to.
- The UI is kept up to date by a stream at `/api/events`, which must not be buffered. It sends a ping every 15 seconds, and a remote worker's poll waits 20, so the usual 60-second timeouts are fine.
- Remote workers can reach the controller by any name, since `/api/workers/...` has its own tokens. If they go through the proxy, it must take uploads as big as a rip (up to about 110 GB for a 4K disc) without holding them on its own disk first.

Caddy (`reverse_proxy` passes `Host` on and doesn't buffer the event stream):

```
rip.example.com {
	reverse_proxy 192.168.1.10:8380
}
```

nginx:

```nginx
server {
    listen 443 ssl;
    server_name rip.example.com;
    # ssl_certificate ...;

    client_max_body_size 0;          # uploads from remote workers are whole rips
    proxy_request_buffering off;

    location / {
        proxy_pass http://192.168.1.10:8380;
        proxy_set_header Host $host;
        proxy_http_version 1.1;
        proxy_buffering off;         # the UI's event stream (ripshelf also sends X-Accel-Buffering: no)
    }
}
```

Traefik, with ripshelf's container on the same Docker network (it passes `Host` on and has no upload limit by default):

```yaml
    labels:
      - traefik.enable=true
      - traefik.http.routers.ripshelf.rule=Host(`rip.example.com`)
      - traefik.http.routers.ripshelf.entrypoints=websecure
      - traefik.http.routers.ripshelf.tls=true
      - traefik.http.services.ripshelf.loadbalancer.server.port=8380
```

## Other ways to run it

- [Build the image yourself](building.md), for another MakeMKV release or architecture.
- [One worker container per drive](modes.md), faster with several drives.
- [Remote workers](workers.md), for drives on other machines, including a Raspberry Pi.
- [Natively on Windows](windows.md), without Docker.
- [On a Raspberry Pi 5](raspberry-pi.md), in Docker or natively.
