# Troubleshooting

Common problems, what ripshelf shows for them, and how to fix them.

Anything that stops ripshelf working shows as a yellow **Setup** box on the Overview page. A failed job shows its reason on the drive card, with Retry, Scan again and Eject buttons. The job's panel has the full MakeMKV log (and the transcode log), and the Logs page has ripshelf's own log.

## A drive isn't shown

- **The container can't see it.** Each drive needs both its `/dev/srN` and its own `/dev/sgN` in `devices:`. The numbers differ, and `ls /sys/class/block/sr*/device/scsi_generic` shows the pairs. They can change between boots after adding or moving a drive.
- **Permissions.** The container runs as your user, so it needs the host's cdrom group. Add the id from `getent group cdrom` to `group_add`.
- **It's disabled.** Settings → Drives and ripping has a switch for each drive.

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

- **No TMDB key.** A film TheDiscDB doesn't know needs one, or an OMDb key, to get candidates. TV shows are still found through TVmaze or TheTVDB without it ([providers.md](providers.md)).
- **A cryptic label** (`BTTF2`, `DISC_1`). Search on the review card, or [ask an AI model](cryptic-labels.md) what it stands for. Labels that are a catalogue number (`EO51710`) can be [looked up on the web](catalogue.md).
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

## "ripshelf isn't served at ..."

ripshelf only answers to the names a LAN uses: IP addresses, `localhost`, names with no dots (`hestia`), and names under `.local`, `.lan`, `.home`, `.home.arpa` and `.internal`. That stops a web page on a name someone points at your server's address (DNS rebinding) from using it. If you open the UI by another name, such as through a reverse proxy or a `ts.net` Tailscale name, set that address as this UI's address in Settings → System (open it by IP to get there), or add the name to `RIPSHELF_ALLOWED_HOSTS`, for example `RIPSHELF_ALLOWED_HOSTS=rip.example.com` or `.example.com` for every name under it. Remote workers aren't affected.

"Refused a change sent from another web page" means a page that wasn't the ripshelf UI tried to eject, cancel or change something, through your browser. If it was the UI itself behind a reverse proxy that changes the `Host` header, set the public address in Settings → System.

## Still stuck?

Open the job and press **Download debug bundle** (or `GET /api/jobs/{id}/debug-bundle`). It's a zip of everything ripshelf has about the job: its record and timeline, the disc's titles, every identification candidate and its score, the MakeMKV and ffmpeg logs, the box scans' readings (and the pictures, if small), the lines of ripshelf's own log from while it ran, the drive and remote worker, the settings and the versions of ripshelf, MakeMKV, ffmpeg and the OS. `manifest.json` in it lists every file and what wasn't there, and `README.txt` sums the job up.

API keys, tokens, passwords and the MakeMKV key are replaced with `[REDACTED]`: settings and JSON keys by name, URL parameters, headers and Bearer tokens in logs by pattern, and the keys set in Settings wherever they appear. File paths, disc labels and titles stay, so look through it before sharing it.

Open an issue with the bundle, or with the job's MakeMKV log (from its panel) and the relevant lines from the Logs page.
