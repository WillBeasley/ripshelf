# Remote workers

A worker is the same image run as `ripshelf worker`. It owns a machine's optical drives and runs MakeMKV on them. The controller keeps the UI, the database, identification, transcoding and the library.

- **In the UI.** Worker drives appear like local ones, marked "on <worker name>", with live progress and logs.
- **Files.** Rips are uploaded to the controller over HTTP. On the same host they can instead be written straight into the controller's scratch folder, if it's mounted at the same path.
- **If a worker goes quiet.** A short network drop ends nothing, because the worker retries until the controller answers. After 45 s without a heartbeat its jobs fail with that reason, ready to retry. When the worker is back, it stops the rips the controller gave up on and deletes their files, so the drive is free for the retry.

## Add a remote worker

1. **On the controller**, go to Settings → Remote workers, give it a name and click **Add remote worker**. The token is shown once, inside a ready-to-paste compose file.
2. **On the worker machine**, get the same image as the controller, either pulled ([installation.md](installation.md)) or built there ([building.md](building.md)). Save the compose file as `compose.yaml`, and change the lines marked `<--`. These are the user and cdrom group ids, and the drives, each `/dev/srN` with its own `/dev/sgN` (`ls /sys/class/block/sr*/device/scsi_generic` shows which goes with which).
3. Run `docker compose up -d`. The worker's drives appear on the Overview, marked "on <worker name>".

The compose file looks like this:

```yaml
services:
  ripshelf-worker:
    image: ${RIPSHELF_IMAGE:-ghcr.io/willbeasley/ripshelf:latest}   # <-- the controller's image
    command: ["ripshelf", "worker"]
    restart: unless-stopped
    user: "1000:1000"                    # <-- your user and group ids
    group_add: ["24"]                    # <-- the cdrom group id on this machine
    environment:
      RIPSHELF_ACCEPT_MAKEMKV_EULA: "no"  # <-- yes once you accept MakeMKV's licence, as on the controller
      RIPSHELF_CONTROLLER_URL: "http://ripper.lan:8380"   # the controller, as this machine reaches it
      RIPSHELF_WORKER_TOKEN: "<made by the controller>"
      RIPSHELF_WORKER_ID: "den-pc"
      RIPSHELF_WORKER_NAME: "Den PC"
    devices:                             # <-- this machine's drives, each srN with its sgN
      - /dev/sr0
      - /dev/sg0
    volumes:
      - ./worker-data:/data              # MakeMKV data per drive
      - ./worker-scratch:/scratch
```

**MakeMKV's licence is accepted on each machine.** A worker container runs its own copy of MakeMKV, so it needs `RIPSHELF_ACCEPT_MAKEMKV_EULA: "yes"` in its compose file too. Until it has it, its drives get no jobs, and the Overview names the worker under Setup.

**MakeMKV setup comes from the controller.** Each time a worker connects it fetches the MakeMKV key and the files imported in Settings → Import MakeMKV private data (`_private_data.tar` for its drives, every `dump_*.tgz`), and installs what is missing or changed into each drive's MakeMKV home. Nothing is copied by hand, and the worker needs no `RIPSHELF_MAKEMKV_KEY` (one set on the worker still wins). A file is installed again only when a new copy is imported on the controller, so MakeMKV's own changes to its `_private_data.tar` are kept. Deleting a file in Settings stops offering it, but drives that have it keep it.

**Tokens.** Each worker has its own, and it works for that worker id only. Only its sha256 is stored. **Revoke** in Settings refuses it at once, and the worker's jobs fail with that reason. The worker logs that its token was refused, and checks again only every 5 minutes. To bring it back, add it again and give it the new token. A shared `RIPSHELF_WORKER_TOKEN` (or token file, as in compose.per-drive.yaml) works for any worker id.

**Over the network.** The controller has no login and speaks plain HTTP, so the token, the MakeMKV key and the private data cross the LAN in the clear. For a worker on another machine, do one of these.

- **Put the controller behind a reverse proxy with TLS** (Caddy, Traefik, nginx) and use an `https://` controller URL. For a private CA, mount its certificate on the worker and set `RIPSHELF_CONTROLLER_CA_FILE: /certs/ca.pem`.
- **Connect the machines over a VPN** such as WireGuard or Tailscale, and use the controller's VPN address.

At rest, the controller keeps the key in `data/settings.json` and the files in `data/makemkv-files/`, readable only by its user (0600). They aren't encrypted, because the key to decrypt them would have to sit in the same folder. Protect `data/` and its backups instead.

## One worker per drive on the same host

[compose.per-drive.yaml](../compose.per-drive.yaml) gives each drive its own worker container, which is faster with several drives. See [modes.md](modes.md). The token is made by the controller and shared through `data/makemkv`, and each drive keeps its id, history and MakeMKV data.

## Raspberry Pi 5

A Pi with USB drives can be a rip-only worker. It rips, the controller transcodes on its GPU, and the files go over the network.

[raspberry-pi.md](raspberry-pi.md) covers setting one up, from the OS to a first rip.

- **Image.** The prebuilt image includes arm64, so `docker compose pull` on the Pi gets the right one. To build it on the Pi instead, see [building.md](building.md). MakeMKV ships an arm64 `makemkvcon`, and everything else has arm64 builds. The Intel media driver and Quick Sync runtime are skipped there.
- **Devices.** Leave out `/dev/dri`, because a worker never transcodes.
- **Hardware.** Use a powered USB hub for several drives, and an SSD for scratch with about 100 GB free (one UHD rip).
- **Pi as the controller.** Turn transcoding off in Settings. A Pi 5 has no hardware HEVC encoder, so x265 would take hours per film.
