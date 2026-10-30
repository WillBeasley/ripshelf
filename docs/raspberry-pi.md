# Raspberry Pi 5

Run ripshelf on a Raspberry Pi 5 with a USB DVD or Blu-ray drive. You can run it in Docker, natively from a checkout, or just run the test suite.

Two scripts do most of the work, and a Claude Code session on the Pi can run them too.

- [scripts/pi/setup-pi.sh](../scripts/pi/setup-pi.sh) installs what's needed. It's safe to run again, `--dry-run` prints the commands without running them, and it asks before accepting MakeMKV's licence or rebooting.
- [scripts/pi/check-pi.sh](../scripts/pi/check-pi.sh) checks everything and changes nothing.

```sh
scripts/pi/setup-pi.sh --tests      # the test suite's dependencies, then run it
scripts/pi/setup-pi.sh --native     # run ripshelf from the checkout (builds MakeMKV)
scripts/pi/setup-pi.sh --docker     # Docker, then pull the image
scripts/pi/check-pi.sh              # what's there and what's missing
scripts/pi/check-pi.sh --worker     # and whether the controller answers and takes the worker's token
```

## What works on a Pi

| | |
|---|---|
| **Ripping** (DVD, Blu-ray, UHD) | yes. MakeMKV 2.0.0 ships an arm64 `makemkvcon`. It's an aarch64 ELF that needs glibc 2.34 (Bookworm has 2.36 and Trixie 2.41), with 64 KiB segment alignment, so the Pi 5's default 16 KiB-page kernel loads it |
| **Drive discovery, serials, eject** | yes. sysfs, the cdrom ioctls and SG_IO are the same on arm64 as on x86-64 |
| **Docker image** | the Dockerfile builds for `linux/arm64`, and the [images workflow](../.github/workflows/images.yml) builds it on a native `ubuntu-24.04-arm` runner. Only the Intel VAAPI driver is skipped there |
| **Hardware transcoding** | **no**. A Pi 5 has no H.264 or H.265 encoder (only HEVC decode). ripshelf falls back to x265 on the CPU (below) |

Because of that, a Pi works best as a **rip-only remote worker** ([workers.md](workers.md)). It rips, and a controller with a GPU transcodes.

## Hardware

- **Pi 5**, 4 GB or more, with the **active cooler**, because MakeMKV and x265 run all four cores flat out.
- **Boot from NVMe** (an M.2 HAT), or at least keep scratch on an SSD. A UHD rip writes 50 to 100 GB, and SD cards are too slow and wear out. Keep about **100 GB free** for scratch.
- **PSU.** Use the official 27 W (5 V 5 A) one. With it the USB ports may draw **1.6 A** in total. With any other PSU the firmware limits them to 600 mA, too little for a bus-powered Blu-ray drive.
  - `usb_max_current_enable=1` in `/boot/firmware/config.txt` (`setup-pi.sh --usb-current`) lifts that limit with a lesser PSU, at your own risk, because the Pi may brown out when the drive spins up.
  - Better still, use a drive with **its own power supply** (most 5.25" drives in an enclosure), or a **powered USB 3 hub**.
- **Drive.** Any USB DVD or Blu-ray drive works. For UHD, a drive that supports LibreDrive (see the MakeMKV forum's list). Use the blue USB 3 ports. Each one has its own controller on the Pi 5.

## OS

**Raspberry Pi OS (64-bit)**, Lite is enough, flashed with Raspberry Pi Imager (set the hostname, user and SSH there). Both releases work.

| | Bookworm (Debian 12) | Trixie (Debian 13) |
|---|---|---|
| Python | 3.11, which is **too old** (ripshelf needs 3.12), so setup-pi.sh uses [uv](https://docs.astral.sh/uv/) to get 3.12 | 3.13, fine |
| Node.js | 18, too old for the UI build, so setup-pi.sh adds NodeSource's Node 22 (what CI uses) | 20, fine |
| Java (BD-J) | `default-jre-headless` (OpenJDK 17) | `default-jre-headless` (OpenJDK 21) |
| ffmpeg | 5.1 | 7.1 |

The Docker image doesn't care, because it brings its own Python 3.12, Node, ffmpeg and Java on Debian Bookworm.

After the first boot, run `sudo apt update && sudo apt full-upgrade`, reboot, then clone the repository and run the scripts.

**sudo.** setup-pi.sh uses sudo for apt, MakeMKV's `make install` and Docker. Current Raspberry Pi OS has no passwordless sudo rule (older images had `/etc/sudoers.d/010_pi-nopasswd`), so it asks for your password.

- **From a terminal on the Pi, or `ssh -t`,** it asks once.
- **Over plain ssh, as a remote Claude Code session does,** there's nowhere to type the password, and setup-pi.sh stops and says so. Either run it in a terminal yourself, or allow passwordless sudo just for the setup like this

  ```sh
  echo "$USER ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/90-ripshelf-setup && sudo chmod 440 /etc/sudoers.d/90-ripshelf-setup
  ```

  and take it away again afterwards with `sudo rm /etc/sudoers.d/90-ripshelf-setup`.
- Raspberry Pi OS's `010_global-tty` makes a typed sudo password count for every session for about 15 minutes. So sudo can seem to work without a password in an ssh session just after you used sudo in a terminal.

## Packages

This is what setup-pi.sh installs for each route (Debian package names).

| For | Packages |
|---|---|
| everything | `ca-certificates curl wget git util-linux p7zip-full ffmpeg sg3-utils lsscsi usbutils` |
| tests | the above, Python 3.12+ in `.venv` with the packages from `requirements-dev.lock` and `pip install --no-deps -e .`, Node 20+ with `npm ci` in `web/` |
| native run | the test set, plus MakeMKV built from source (below) and `default-jre-headless` |
| Docker | Docker Engine with the compose and buildx plugins, from `get.docker.com` (Bookworm's `docker.io` has no compose v2) |

Every Python dependency with compiled parts (pydantic-core, rapidfuzz, uvloop, httptools, watchfiles, pyyaml, ruff) has aarch64 manylinux wheels, so nothing compiles. `web/package-lock.json` has the arm64 builds of rollup and esbuild.

The tests need `ffmpeg` and `7z` (as in [CI](../.github/workflows/test.yml)). Tests that need a VAAPI GPU skip themselves on a Pi.

## MakeMKV, natively

The Docker image builds it. For a native run, setup-pi.sh does the same as the Dockerfile, using the version and checksums the Dockerfile pins (it reads them from the Dockerfile, so they stay in step).

```sh
sudo apt install build-essential pkg-config libc6-dev libssl-dev libexpat1-dev zlib1g-dev \
    libavcodec-dev default-jre-headless
V=2.0.0    # ARG MAKEMKV_VERSION in the Dockerfile
wget https://www.makemkv.com/download/makemkv-oss-$V.tar.gz https://www.makemkv.com/download/makemkv-bin-$V.tar.gz
sha256sum makemkv-*-$V.tar.gz    # compare with MAKEMKV_OSS_SHA256 / MAKEMKV_BIN_SHA256 in the Dockerfile
tar xzf makemkv-oss-$V.tar.gz && tar xzf makemkv-bin-$V.tar.gz
cd makemkv-oss-$V && ./configure --disable-gui && make -j4 && sudo make install && cd ..
cd makemkv-bin-$V && make && sudo make install && cd ..   # make shows the licence to accept
sudo ldconfig
```

- `--disable-gui` builds only what `makemkvcon` needs, so Qt (`qtbase5-dev`) isn't needed. Leave it out, and add `qtbase5-dev libgl1-mesa-dev`, for the MakeMKV GUI.
- makemkv-bin's Makefile picks `bin/arm64/makemkvcon` from `uname -m` (`aarch64`).
- Once a newer release comes out, 2.0.0 moves to `/download/old/` (setup-pi.sh tries both).
- **Java** is for BD-J, because some Blu-rays only show the right playlist through their Java menus.
- **UHD.** MakeMKV doesn't need libaacs, because it reads UHDs through LibreDrive. Import the drive's `_private_data.tar` from an existing MakeMKV install in Settings → Import MakeMKV private data. A fresh MakeMKV home with no data can stall a UHD scan.
- **Key.** Set it in ripshelf's Settings (or `RIPSHELF_MAKEMKV_KEY`), and ripshelf writes it into each drive's MakeMKV home.

## Drive access

A USB drive shows up as `/dev/srN` (block) and `/dev/sgN` (SCSI generic). ripshelf needs both, `sr` for status and eject, and `sg` for the drive's identity and serial number (SG_IO) and for MakeMKV.

- **sg module.** Run `sudo modprobe sg`, and `echo sg | sudo tee /etc/modules-load.d/sg.conf` to load it at boot. Without it there's no `/dev/sgN`.
- **Groups.** Debian gives `/dev/sr*` and an optical drive's `/dev/sg*` to group `cdrom`, so run `sudo usermod -aG cdrom $USER`, then log in again. On some setups `sg` is group `disk`. check-pi.sh shows the owner of each node, and setup-pi.sh adds you to whichever group it is.
- **Pairs.** `ls /sys/class/block/sr*/device/scsi_generic` shows which `sgN` belongs to which `srN`.
- **Drive ids.** They come from the vendor, model and serial number (MMC feature 0x108), the same on a Pi as on x86, so a drive keeps its id on any port and on any machine. check-pi.sh prints each drive's id and serial as ripshelf sees them. If a USB bridge hides the serial, the id falls back to the USB port, so keep the drive in the same port.
- **UAS.** Most enclosures use the `uas` driver (check-pi.sh shows which). If `dmesg` shows `uas_eh_abort_handler`, resets, or the drive disappearing mid-rip, force the older driver. Find the bridge's id with `lsusb` (for example `152d:0583`), add `usb-storage.quirks=152d:0583:u` to the single line in `/boot/firmware/cmdline.txt`, and reboot.

## Docker route

```sh
scripts/pi/setup-pi.sh --docker     # Docker, then docker pull ghcr.io/willbeasley/ripshelf:latest
```

The published image includes arm64, so Docker pulls the right one. To build it on the Pi instead, add `--build-image` (it takes 15 to 30 minutes, because MakeMKV compiles) and put `RIPSHELF_IMAGE=localhost/ripshelf:latest` in a `.env` file next to the compose file. To build arm64 on a PC, use buildx with QEMU ([building.md](building.md#for-another-architecture)).

Then follow [installation.md](installation.md) with [compose.yaml](../compose.yaml), with these changes:

- the `group_add` cdrom id (`getent group cdrom`, usually 24)
- **delete the render group and the `/dev/dri` line**, because the Pi's GPU can't encode
- the drives, each `/dev/srN` with its own `/dev/sgN`
- scratch on the NVMe

As a **remote worker** for a controller elsewhere, use the compose file the controller's Settings → Remote workers makes ([workers.md](workers.md)), with the same drive lines.

## Native route

```sh
scripts/pi/setup-pi.sh --native     # .venv, the UI build, MakeMKV
.venv/bin/ripshelf                   # UI on http://<pi>:8380
```

A native run keeps `data/`, `scratch/` and `library/` next to the checkout unless you change them in Settings. A few settings, such as the port, aren't on the Settings page. To change those, create `ripshelf.env` in the checkout and add a line for each, for example `RIPSHELF_PORT=8390`. [settings.md](settings.md) lists them all. It listens on all addresses (port 8380) and has no login, so keep it on your LAN.

To run it as a remote worker instead:

```sh
RIPSHELF_CONTROLLER_URL=http://<controller>:8380 RIPSHELF_WORKER_TOKEN=<from the controller> \
RIPSHELF_WORKER_ID=pi5 RIPSHELF_WORKER_NAME="Pi 5" .venv/bin/ripshelf worker
```

To start it at boot, add a systemd unit at `/etc/systemd/system/ripshelf.service` (change the user and path):

```ini
[Unit]
Description=ripshelf
After=network-online.target
Wants=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/ripshelf
ExecStart=/home/pi/ripshelf/.venv/bin/ripshelf
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

then `sudo systemctl daemon-reload && sudo systemctl enable --now ripshelf`.

## Run the tests

```sh
scripts/pi/setup-pi.sh --tests
```

runs the same checks as CI, which are `ruff check src tests`, `pytest -q` and `npm run check` in `web/`. To run them again by hand:

```sh
.venv/bin/ruff check src tests && .venv/bin/pytest -q
(cd web && npm run check)
RIPSHELF_REAL_DRIVES=1 .venv/bin/pytest -q tests/test_drives.py   # also reads the real drives' sysfs
```

## Transcoding on a Pi

With the encoder set to `auto` (the default), ripshelf uses the first of Quick Sync, VAAPI and NVENC whose test HEVC encode works (Quick Sync and VAAPI on `/dev/dri/renderD128`), else x265. A native Pi 5 has `/dev/dri/renderD128` (the v3d GPU), but VAAPI has no driver for it, so the tests fail within a second and ripshelf uses x265. Nothing tries `hevc_v4l2m2m`, which Debian's ffmpeg lists but the Pi 5 can't do. Settings → Transcoding shows the result.

x265 (`-preset medium`) on a Pi 5's four Cortex-A76 cores is slow, roughly a few frames a second for 1080p, so many hours per film, and far longer for UHD. `scripts/pi/check-pi.sh --bench` times a short 1080p encode on the Pi. Real films encode slower than its synthetic picture. Unless the Pi has nothing else to do, either:

- turn transcoding off (Settings → Transcoding), and keep the MakeMKV files as they are, or
- run the Pi as a worker for a controller that transcodes on a GPU.

## First rip test plan

With a USB Blu-ray drive, in this order:

1. **Run `scripts/pi/check-pi.sh`.** There should be no FAILs. The drive is listed with its `sr`/`sg` pair, both readable and writable, a serial number, and `bluray` in its capabilities. `vcgencmd get_throttled` is `0x0`.
2. **Start ripshelf** (either route) and open `http://<pi>:8380`. The setup page shows the drive under its vendor and model. Set the MakeMKV key and press Test. For UHD, import the drive's `_private_data.tar`.
3. **Unplug the drive and plug it into the other USB 3 port.** It comes back with the same id, not a second drive.
4. **A DVD.** Insert it, and check it's scanned, identified, ripped and copied into the library. Eject works from the drive card.
5. **A Blu-ray film.** The same, checking the main title's runtime. With Java installed, a disc that needs BD-J picks the right playlist.
6. **A TV Blu-ray or DVD.** Episodes are found, ordered and named.
7. **A UHD disc.** The scan log (job details → logs) says MakeMKV is using LibreDrive, and the rip decrypts. While it rips, check `vcgencmd get_throttled` (power) and `vcgencmd measure_temp` (cooling), and `dmesg` for USB resets.
8. **Transcoding.** Rip one DVD with transcoding on, to see x265 work and how long it takes. Then decide whether to turn it off.
9. **As a remote worker** of another controller. See the [remote worker test plan](#remote-worker-test-plan).
10. **Restart during a rip** (`systemctl restart ripshelf` or `docker compose restart`). The job fails with "ripshelf restarted while this job was running" and Retry works.

## Remote worker test plan

This is how a worker behaves, from [workers.md](workers.md) and the code (`src/ripshelf/workers/`), so each step says what to expect.

- **Connection.** The worker registers its drives, heartbeats every 5 s with each drive's status (and the disc's label once per insertion), and long-polls the controller for commands.
- **Errors.** Connection errors, timeouts and 5xx answers are retried every 2 s without ending anything. The worker registers again only when the controller no longer knows it (it restarted). A refused token stops it, with one error in the log, then a check every 5 minutes.
- **Abandoned runs.** Each heartbeat lists the runs the worker is working on. The controller answers with any it has given up on (after 45 s without heartbeats, a revoke, or a restart), and the worker stops their makemkvcon and deletes their files.
- **Timeouts.** The controller marks a worker offline after 45 s without a heartbeat and fails its runs.
- **MakeMKV setup.** On every registration the worker fetches the MakeMKV key, its drives' `_private_data.tar` and every `dump_*.tgz`. It installs only what is missing or has changed.
- **Ripping.** makemkvcon writes to `<worker scratch>/remote-run-<n>/`. When it finishes, each MKV is uploaded to the controller's scratch with an HTTP PUT, then deleted on the worker. The upload starts after the rip, not during it. Scan and rip output streams to the job page as it happens.
- **Versions.** Nothing checks that the worker and controller run the same version, so keep them on the same commit.

### Getting ready

1. On the controller, go to Settings → Remote workers, name it (for example "Pi 5") and click **Add remote worker**. The controller URL in the compose file must be the address the Pi uses to reach it. Copy the compose file, because the token is shown only once.
2. On the controller, go to Settings → Import MakeMKV private data. For an LG GGC-H20N, the `dump_*.tgz` MakeMKV needs ([troubleshooting.md](troubleshooting.md#an-older-drive-hangs-in-makemkv-lg-ggc-h20n-and-others)). For a UHD drive, its `_private_data.tar`.
3. On the Pi, run it one of two ways.
   - With Docker, use the compose file with this drive's `sr`/`sg` lines (and `RIPSHELF_IMAGE=localhost/ripshelf:latest` if you built the image on the Pi).
   - Natively, put `RIPSHELF_CONTROLLER_URL`, `RIPSHELF_WORKER_TOKEN`, `RIPSHELF_WORKER_ID` and `RIPSHELF_WORKER_NAME` in `ripshelf.env` in the checkout, then run `.venv/bin/ripshelf worker`.
4. `scripts/pi/check-pi.sh --worker` checks that the controller answers and accepts the token, without registering anything.

### 1. The Pi as a remote worker

| # | Do | Expect |
|---|---|---|
| 1 | Start the worker | Its log says `registered with <url>: 1 drives`. The drive appears on the controller's Overview, marked "on Pi 5". Settings → Remote workers shows it online, and "last seen" moves (it's updated at most every 30 s) |
| 2 | Look in the worker's data folder | `makemkv/<drive id>/.MakeMKV/` has `settings.conf` with the key, plus the dump or private data from step 2 (the log lists what was installed). Restart the worker, and nothing is installed again |
| 3 | Import a new copy of the file on the controller, then restart the worker | It's installed again. Changes MakeMKV made to its own `_private_data.tar` are otherwise kept |
| 4 | Compare the drive id with the one it had as a local drive elsewhere | The same (from the serial number). Only an id another drive already uses becomes `<worker>:<id>` |
| 5 | Insert a disc | Within about 5 s the card shows the disc, and the scan log streams live on the job page |
| 6 | Let it rip | During the rip, `worker-scratch/remote-run-<n>/` fills. After it comes the upload, so time it (1 Gb/s is about 110 MB/s, so 8 minutes for 50 GB). Then `worker-scratch` is empty, and the controller identifies, transcodes and files the rip into **its** library |
| 7 | Eject and close the tray from the card | Both work. Commands reach the worker on its next poll |
| 8 | Restart the controller while idle | The worker gets "controller does not know this worker" and registers again within about 5 s. The drive card comes back |
| 9 | Restart the controller during a rip | The job fails ("ripshelf restarted while this job was running"). The worker gets "does not know this worker", stops that makemkvcon, deletes `worker-scratch/remote-run-<n>/` and registers again. Retry works straight away |
| 10 | Stop the worker during a rip (`docker compose stop`, or Ctrl+C natively) | After 45 s the job fails ("the worker stopped answering"), or sooner if the worker comes back ("the worker restarted"). A worker that starts again deletes any `remote-run-*` folder left behind. Retry works once it's back |
| 11 | Unplug the Pi's network for over 45 s during a rip, then plug it back in | After 45 s the job fails ("the worker stopped answering"). When the network is back, the worker's next heartbeat is told the controller gave up on that run. It stops makemkvcon and deletes its files without registering again (the worker log says `run <n>: the controller has given up on it; stopping it`) |
| 12 | Unplug the network for only about 10 s during a rip | **Nothing fails.** The worker logs `controller unreachable while …; retrying`, then `controller answering again`, and the rip carries on. Its output lines reach the job page once the network is back |
| 13 | Drop the network briefly during the upload | The controller deletes the partial file (`.<name>.upload`), and the worker starts that file's upload again from the beginning once the network is back. A drop longer than 45 s ends as in 11 |
| 14 | Revoke the worker (Settings → Remote workers) while it's idle | The drive goes to error at once. The worker logs one error ("controller refused the worker token (401) … needs a new token") and checks again only every 5 minutes. `check-pi.sh --worker` says the token is refused |
| 15 | Revoke during a rip | The job fails at once ("its token was revoked"), and the worker stops that makemkvcon and deletes its files |
| 16 | Add the worker again under the same name (new token) and restart it | It gets its old worker id back, and the drive keeps its id and history |

### 2. The Pi as controller with its own drive (single container)

[compose.yaml](../compose.yaml) on the Pi (the Docker route above), or `.venv/bin/ripshelf` natively. The drive is local, so there's no "on …" marker and no upload, and makemkvcon writes straight into the controller's scratch.

1. The setup page, the MakeMKV key test, and the dump or private data import.
2. A DVD and a Blu-ray from insert to library, with transcoding off.
3. The same with transcoding on (x265), timed, to see whether it's usable at all.
4. Optionally, [compose.per-drive.yaml](../compose.per-drive.yaml) with the one drive. It's a worker on the same host with shared scratch, so the job page shows it "on" the worker, and there's no upload (step 6 above has no `remote-run` folder).

### 3. Optionally, another machine's drives as workers for the Pi

The Pi as controller, and a PC's drive as its worker (the PC's own image, for example amd64). Uploads go from the PC to the Pi's NVMe. Turn transcoding off on the Pi.

**Don't give this worker a drive that another ripshelf already uses.** Two MakeMKVs on one drive make each other fail. Use a drive nothing else uses, or stop the other stack first.

### Not covered by the automated tests

`tests/test_workers.py` and `tests/test_remote_setup.py` cover these against an in-process controller:

- registration, tokens, revoking and per-worker ids
- the MakeMKV bootstrap
- a rip uploaded into the library, and shared scratch
- cancelling, the 45 s timeout, and failing runs when a worker registers again

They now also cover:

- a short outage during a rip (no new registration, and the rip completes)
- a run the controller gave up on being stopped on the worker, with its files deleted
- a refused token logging one error
- leftover rip folders

Still not covered:

- reconnecting after a real controller restart (the 404 path)
- an upload cut off part-way
- real network speeds, and HTTPS through a real reverse proxy (only a private-CA client setup is tested).

## Troubleshooting

| What you see | What to do |
|---|---|
| No drive in ripshelf | Run `scripts/pi/check-pi.sh`. If there's no `/dev/sgN`, load `sg`. If it isn't readable, join the group and log in again. If it isn't in `/sys/class/block`, check the power (the drive's own PSU or a powered hub), the cable and `dmesg` |
| The drive drops out when it spins up, or `get_throttled` isn't `0x0` | there isn't enough USB power. Use the official 27 W PSU, a powered hub, or a drive with its own PSU |
| USB resets or `uas_eh_abort_handler` in `dmesg` | the `usb-storage.quirks=<vid>:<pid>:u` line above |
| A binary fails with "ELF load command address/offset not page-aligned" or "Unsupported system page size" | something was built for 4 KiB pages. `makemkvcon` 2.0.0 is fine. For anything else, add `kernel=kernel8.img` to `config.txt` (4 KiB pages) and reboot |
| `requires-python >=3.12` from pip on Bookworm | the venv was made with the system's 3.11. Delete `.venv` and run setup-pi.sh, which uses uv |
| A UHD scan stalls with a fresh MakeMKV home | import the drive's `_private_data.tar` (Settings → Import MakeMKV private data) |
| An older drive hangs in MakeMKV | [troubleshooting.md](troubleshooting.md#an-older-drive-hangs-in-makemkv-lg-ggc-h20n-and-others) (the same firmware-dump fix as on x86) |
| Transcoding takes many hours | that's expected with x265 on a Pi. Turn it off, or transcode on another machine |

## Not checked yet

These need the real hardware.

- `makemkvcon` 2.0.0 running on a Pi 5 and reading a UHD through LibreDrive over USB (it should, because it's the same code as x86).
- makemkv-oss 2.0.0 compiling against Trixie's ffmpeg 7.1 (`libavcodec61`). The Dockerfile builds it on Bookworm's ffmpeg 5.1, which works.
- Whether a given USB bridge passes the drive's serial number through (GET CONFIGURATION feature 0x108), and its eject and tray status.
- Real x265 speeds, and how hot the Pi gets during a long rip.
- The test suite on arm64. CI runs it on x86-64 only (the arm64 runner only builds the image).
