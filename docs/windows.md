# Running on Windows

Run ripshelf natively on Windows 10 or 11, without Docker.

One batch file starts the server, and you use the UI in your browser at http://localhost:8380. The drives are found by drive letter, MakeMKV rips, and ffmpeg transcodes.

## Requirements

Install everything with winget, in a terminal (PowerShell or Command Prompt):

```powershell
winget install -e --id Python.Python.3.12     # runs ripshelf
winget install -e --id GuinpinSoft.MakeMKV    # does the ripping
winget install -e --id Git.Git                # to get (and update) the source
winget install -e --id OpenJS.NodeJS.LTS      # builds the web UI, once
winget install -e --id Gyan.FFmpeg            # optional, for transcoding and episode checks
winget install -e --id Microsoft.OpenJDK.21   # optional, for Blu-ray menus (BD-J), which some discs need
```

Then open a new terminal, so the new programs are on the PATH.

| | |
|---|---|
| **Python 3.12** | runs ripshelf. If you install it from python.org instead, tick "Add python.exe to PATH", and click "Disable path length limit" at the end. |
| **MakeMKV** | ripshelf finds it in Program Files. Register it once, in the MakeMKV app or with your key in ripshelf's Settings. |
| **Git** | to clone the source and `git pull` updates. You can download a zip from the repository instead. |
| **Node.js** | only needed once, to build the web UI (and again after updates that change it). |
| **ffmpeg** (optional) | transcoding, and checking the episode order of TV discs. On Windows, transcoding uses x265 on the CPU. |
| **Java** (optional) | some Blu-rays need their menus (BD-J) read to find the right playlist. |

## Installation

1. Get the source with `git clone https://github.com/WillBeasley/ripshelf.git`, or download it as a zip and unpack it. Put it where you want ripshelf to keep its things (see below), for example `D:\ripshelf`.
2. Double-click **`scripts\windows\start-ripshelf.bat`**. The first start sets up Python and builds the UI, which takes a few minutes. Later starts take seconds.
3. Your browser opens ripshelf's **setup page**. Name your drives, check MakeMKV, choose the folders, and add your TMDB key (and any optional extras). Each step is saved as you go. You can skip anything and change it later in Settings, where "Run the setup again" brings the page back.

Close the window (or press Ctrl+C) to stop it.

Everything stays in the ripshelf folder unless you change it in Settings:

| | |
|---|---|
| `data\` | database, settings, logs, MakeMKV data |
| `scratch\` | rips in progress. A UHD rip needs about 100 GB, so you may want it on a bigger disk |
| `library\` | finished films in `Movies`, TV in `TV` |
| `.venv\` | ripshelf's Python environment |

A network share works as the library, either mapped to a drive letter or as a `\\server\share` path.

A few settings aren't on the Settings page, such as the port. To change one of those, create a file called `ripshelf.env` in the ripshelf folder and add a line for each setting, for example `RIPSHELF_PORT=8390`. [settings.md](settings.md) lists them all.

## Good to know

- **Only this PC can open the UI** by default. To use it from a phone or another computer, add `RIPSHELF_HOST=0.0.0.0` to `ripshelf.env` and allow Python through Windows Firewall when it asks. Anyone on your network can then use it.
- **Turn AutoPlay off for discs** (Settings → Bluetooth & devices → AutoPlay), so Windows doesn't open a player or a dialog for each disc.
- **Exclude the scratch folder from virus scanning** (Windows Security → Virus & threat protection → Exclusions). Scanning 50 to 100 GB files as they're written slows the rip, and holds new files open.
- **Sleep.** A rip takes a while. Set the PC not to sleep while it's plugged in, or it stops mid-rip.
- **Long paths.** Episode names can make paths longer than Windows' old 260-character limit. The Python installer's "Disable path length limit" turns that off (or set `LongPathsEnabled` to 1 under `HKLM\SYSTEM\CurrentControlSet\Control\FileSystem`).
- **No administrator rights** are needed. Drive status, eject and the drive's serial number all work as a normal user.

## MakeMKV on Windows

- **Registration key.** makemkvcon on Windows reads its key from the registry (`HKEY_CURRENT_USER\Software\MakeMKV`), once per Windows user, shared with the MakeMKV app.
- **Per-drive data.** Each drive still gets its own MakeMKV folder under `data\makemkv\<drive id>\.MakeMKV` (LibreDrive data, firmware dumps), as on Linux. ripshelf points makemkvcon at it with `USERPROFILE`, which only affects that one makemkvcon. Your own MakeMKV app keeps using `%USERPROFILE%\.MakeMKV`.
- **UHD.** A new drive folder needs LibreDrive/HK data, or MakeMKV says the volume key is unknown (or stalls). On its first start ripshelf uses the `_private_data.tar` your MakeMKV app already has (`%USERPROFILE%\.MakeMKV`), so run a UHD disc through the MakeMKV app once first. Or import one in Settings → Import MakeMKV private data.
- **One disc opens at a time.** Each makemkvcon checks every drive when it starts, and two started together break each other's disc handshake (AACS). ripshelf's start-up queue lets the next one start once the previous has opened its disc, then they run side by side.

## Same drives, same ids

A drive's id comes from its vendor, model and serial number, read the same way on Windows and Linux. So a drive moved between machines keeps its name and settings.

## Using a Windows PC as a remote worker

A Windows PC can also be a remote worker for a controller on Linux (or the other way round). Run `ripshelf worker` with `RIPSHELF_CONTROLLER_URL` and the worker's token, as in [workers.md](workers.md). Leave `RIPSHELF_WORKER_SHARED_SCRATCH` off. The two machines see different paths, so ripped files are uploaded instead.
