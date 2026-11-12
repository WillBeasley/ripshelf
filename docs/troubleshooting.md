# Troubleshooting

Common problems, what ripshelf shows for them, and how to fix them.

Anything that stops ripshelf working shows as a yellow **Setup** box on the Overview page. A failed job shows its reason on the drive card, with Retry, Scan again and Eject buttons. The job's panel has the full MakeMKV log (and the transcode log), and the Logs page has ripshelf's own log.

## A drive isn't shown

- **The container can't see it.** Each drive needs both its `/dev/srN` and its own `/dev/sgN` in `devices:`. The numbers differ, and `ls /sys/class/block/sr*/device/scsi_generic` shows the pairs. They can change between boots, see [below](#drive-numbers-that-change-between-boots).
- **Permissions.** The container runs as your user, so it needs the host's cdrom group. Add the id from `getent group cdrom` to `group_add`.
- **It's disabled.** Settings → Drives and ripping has a switch for each drive.

## Drive numbers that change between boots

The kernel numbers drives in the order it finds them. `/dev/sgN` counts every SCSI device, disks and USB sticks too, so adding a disk can move a drive's `sg` number without moving its `sr` number. Then `compose.yaml` passes in the wrong pair.

ripshelf notices. The Setup box names the device, either "/dev/sr0 (…) is passed in without its own /dev/sg5", or "/dev/sg1 is passed in, but it belongs to /dev/sr3, which isn't". A worker's drives show there too, with the worker's name. Fix the `devices:` lines and run `docker compose up -d`. Nothing else needs changing, because a drive's id, history, settings and MakeMKV data come from its serial number, not its numbers.

### Stable names with udev

udev can give each drive a name that stays the same, from the port it's plugged into. Find each drive's port path (for USB drives it's the USB port, so keep each drive in the same one):

```sh
udevadm info -q property -n /dev/sr0 | grep ID_PATH=
```

Then add one pair of lines per drive to `/etc/udev/rules.d/99-ripshelf-drives.rules`, with that path and a short name:

```
SUBSYSTEM=="scsi_generic", ENV{ID_PATH}=="", IMPORT{builtin}="path_id"
SUBSYSTEM=="block", KERNEL=="sr[0-9]*", ENV{ID_PATH}=="pci-0000:01:00.0-sas-phy2-lun-0", SYMLINK+="ripshelf/ihas"
SUBSYSTEM=="scsi_generic", ENV{ID_PATH}=="pci-0000:01:00.0-sas-phy2-lun-0", SYMLINK+="ripshelf/ihas-sg"
```

The first line is needed once, because not every system gives `sg` devices a port path. Load the rules with `sudo udevadm control --reload && sudo udevadm trigger`, and `ls -l /dev/ripshelf` shows each name with the numbers it has now.

`devices:` can't list the names themselves. MakeMKV only finds drives called `/dev/sgN`, and rootless Docker can't rename a device (`/dev/ripshelf/ihas-sg:/dev/sg0` passes in the host's `/dev/sg0`). So turn the names into the numbers before starting ripshelf. Save this as `drives.sh` next to `compose.yaml`:

```sh
#!/bin/sh
# Writes drives.env, with each /dev/ripshelf name's current device.
cd "$(dirname "$0")" || exit 1
for l in /dev/ripshelf/*; do
  echo "DRIVE_$(basename "$l" | tr 'a-z-' 'A-Z_')=$(readlink -f "$l")"
done > drives.env
```

and use the names in `compose.yaml`:

```yaml
    devices:
      - ${DRIVE_IHAS}
      - ${DRIVE_IHAS_SG}
```

Start it with `./drives.sh && docker compose --env-file drives.env up -d` (add `--env-file .env` before it if you have a `.env`). Compose re-creates the container when a number has changed.

After a reboot, Docker restarts the container with the numbers it had before, so run the same command at boot too, for example from a systemd unit (a user unit, `systemctl --user`, for rootless Docker; `loginctl enable-linger` lets it start before you log in):

```ini
[Unit]
Description=ripshelf with this boot's drive numbers
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=/path/to/ripshelf
ExecStart=/bin/sh -c './drives.sh && docker compose --env-file drives.env up -d'

[Install]
WantedBy=default.target
```

## "MakeMKV rejected the registration key"

The key in Settings → Drives and ripping is wrong or has expired. The free beta key changes every month or two. Get the current one from the [MakeMKV forum](https://forum.makemkv.com/forum/viewtopic.php?t=1053), or buy a permanent key. Test checks the key without saving it.

## "MakeMKV does not know the volume key" / "could not decrypt this disc" (UHD)

UHD discs need the drive's LibreDrive data. Import the drive's `_private_data.tar` (from `~/.MakeMKV` on a machine where MakeMKV has already played a UHD disc in that drive) in **Settings → Drives and ripping → MakeMKV private data**, then Scan again. The drive must be LibreDrive-capable (see the [MakeMKV forum's drive list](https://forum.makemkv.com/forum/viewtopic.php?f=16&t=19634)).

## The scan times out

The job says "MakeMKV stopped responding while scanning" or "was still scanning after …". It's usually one of these:

- **A damaged or dirty disc.** The job says so when MakeMKV logged read errors. Clean it and Scan again.
- **An older drive that hangs while MakeMKV makes a firmware dump**, below.
- **Several drives in one container slowing each other down.** Use [one worker per drive](modes.md).

The limits are in Settings → Drives and ripping, under Advanced.

A DVD with a complex multiplex is the one slow scan that goes silent: MakeMKV logs "Complex multiplex encountered - N cells and M VOBUs have to be scanned" and then writes nothing until it's done, which took over 5 minutes for 4759 VOBUs on an LG GGC-H20N. The job notes it, and the scan is allowed up to an hour of silence after that message (or the timeout in Settings, if longer), within the overall limit.

## An older drive hangs in MakeMKV (LG GGC-H20N and others)

MakeMKV after 1.17.7 tries to make a firmware dump for a drive without LibreDrive support, and on some drives (the LG GGC-H20N is one) it never finishes. MakeMKV 1.17.6 makes the dump fine, and once a `dump_*.tgz` is in the drive's MakeMKV folder, current MakeMKV uses it and scans normally.

**The fix** is to import a `dump_*.tgz` for the drive in **Settings → Drives and ripping → MakeMKV private data**. It's copied to the drive (and to drives seen later), remote workers fetch it, and it's never deleted. If you've used the drive with another MakeMKV install, its `~/.MakeMKV` may already have one.

**To make a dump,** run MakeMKV 1.17.6 once against the drive with any disc in it. The Dockerfile builds that version:

```sh
docker build -t ripshelf:makemkv-1.17.6 \
  --build-arg MAKEMKV_VERSION=1.17.6 \
  --build-arg MAKEMKV_URL_BASE=https://www.makemkv.com/download/old \
  --build-arg MAKEMKV_OSS_SHA256=d9db4d772bf4f9061642b7eb22ee5140a48de2c71258ab8b14d949657a710d43 \
  --build-arg MAKEMKV_BIN_SHA256=287646005029f771d366cf0e4fbeb17fcf1033452d955547deae7b0999b4ed1b .

mkdir -p dump-home/.MakeMKV
echo 'app_Key = "<your MakeMKV key>"' > dump-home/.MakeMKV/settings.conf
docker run --rm --user "$(id -u):$(id -g)" --group-add "$(getent group cdrom | cut -d: -f3)" \
  --device /dev/srN --device /dev/sgM -v "$PWD/dump-home:/home/x" -e HOME=/home/x \
  --entrypoint makemkvcon ripshelf:makemkv-1.17.6 -r --cache=1 info dev:/dev/srN
ls dump-home/.MakeMKV/dump_*.tgz        # import this in Settings
```

Use the drive's own `srN`/`sgM` pair. The dump belongs to the drive and its firmware, so keep a copy with your backups.

**How to recognise it.** With `makemkvcon --debug`, the output stops after lines like `SDF auto v0a6: HL-DT-ST_BDDVDRW_GGC-H20N_…`, apart from a "DEBUG: Code 2 / Code 0" pair every 20 to 30 s, with one CPU core busy. Waiting, `--network=none`, `sdf_Stop = "1"` and SDF files from another install don't help.

## A disc goes to Review instead of being identified

- **No TMDB key.** Every disc TheDiscDB doesn't know needs one ([providers.md](providers.md)).
- **A cryptic label** (`BTTF2`, `DISC_1`). Search on the review card. Labels that are a catalogue number (`EO51710`) can be [looked up on the web](catalogue.md).
- **A close call.** The score is under the auto-confirm line (0.90 by default, Settings → Identify). The card shows the evidence for each candidate.

## Wrong episode order on a TV disc

Pick the episode order that matches your library, either TheTVDB's aired or DVD order (Settings → Identify). Emby uses aired order unless the TV library is set to DVD order. The Review page shows the mapping before anything is copied.

## "Library folder missing" / a folder is read-only

ripshelf never creates the Movies, TV or disc-backup folders, so an unmounted disk can't fill your system disk. Create the folder (or mount the disk) and check the container user can write to it. Folders you create before the first start are owned by you, but ones Docker creates are owned by root.

## "Scratch is full"

A UHD rip needs about 100 GB of scratch, a Blu-ray about 50 GB. Free space, or point scratch at a bigger disk, then Retry.

## "ripshelf restarted while this job was running"

The container restarted mid-rip, because of an update, Watchtower or a reboot. Retry the job. To avoid it, pin a version tag and don't update while ripping ([installation.md](installation.md#updating)).

## Transcoding uses the CPU, or fails

Settings → Transcoding lists the GPUs and the ffmpeg hardware encoders that work in the container, each tested with a short encode. For an Intel or AMD GPU, pass `/dev/dri` into the container and add the host's render group (`getent group render`) to `group_add`. A job's Transcode log has the ffmpeg command and its errors. With several GPUs, pick the one for Quick Sync and VAAPI under Settings → Transcoding; Auto tries Quick Sync, then VAAPI, then NVENC on it and uses the first that works. If Quick Sync fails with `unsupported (-3)` on a 12th gen or newer Intel CPU, the image is older than the one that added the oneVPL runtime (`libmfx-gen1.2`); pull a newer one.

## Still stuck?

Open an issue with the job's MakeMKV log (from its panel) and the relevant lines from the Logs page. Check them for anything you'd rather not share first.
