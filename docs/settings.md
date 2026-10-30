# Settings

Every setting, where it is stored, and the environment variable that sets it.

Most settings are changed on the Settings page. A fresh install opens a short setup page first (drives, MakeMKV, folders, TMDB key, extras), and "Run the setup again" at the top of Settings brings it back.
- **Storage.** Settings are saved in `/data/settings.json` and win over the environment.
- **Secrets.** Keys and tokens are stored, but never sent back to the browser.
- **Outside a container,** folders that aren't set are next to ripshelf (the source checkout). These are `data/`, `scratch/`, `library/` and the env file `ripshelf.env`. You can change them in Settings. A checkout that already has state in `var/` keeps using it.
- **Environment.** Any setting can also be set as an environment variable. The name is `RIPSHELF_` followed by the setting's name in capitals. Put it in the `environment:` section of compose.yaml, or in `/data/ripshelf.env`.
- **Test buttons.** Each connection (TMDB, TheTVDB, OMDb, TheDiscDB, TVmaze, MakeMKV key, Pushover, Emby, Jellyfin, Plex) and the folders have a Test button. It tries the values as typed, without saving them.

## Settings that aren't on the Settings page

A few settings can only be set in the environment, mostly because ripshelf needs them before it can read settings.json, or because they belong to one machine. Set them in compose.yaml's `environment:` section, or put them in `ripshelf.env` with one setting per line, like this:

```sh
RIPSHELF_PORT=8390
RIPSHELF_HOST=127.0.0.1
```

In a container `ripshelf.env` lives in `/data`. Outside a container it's in the ripshelf folder, unless `RIPSHELF_ENV_FILE` points somewhere else.

| Variable | Default | What it does |
|---|---|---|
| `RIPSHELF_DATA_DIR` | `/data` | holds settings.json, the database, MakeMKV data and logs |
| `RIPSHELF_HOME` | the source checkout | the folder unset folders and `ripshelf.env` go next to, outside a container |
| `RIPSHELF_ENV_FILE` | `<data>/ripshelf.env` in a container, `ripshelf.env` in the ripshelf folder otherwise | the env file to read |
| `RIPSHELF_HOST` / `PORT` | `0.0.0.0` / 8380 | the address and port the web UI listens on |
| `RIPSHELF_BACKUP_DIR` | `/backups` | where daily database backups go. How many are kept is set in Settings |
| `RIPSHELF_MAKEMKVCON`, `FFMPEG`, `FFPROBE`, `SEVENZIP`, `BLKID` | the program name, found on the PATH | the programs ripshelf runs |
| `RIPSHELF_ACCEPT_MAKEMKV_EULA` | `no` | set `yes` once you have read and accepted MakeMKV's licence. Only needed where MakeMKV came with ripshelf (the image), and on every container that runs MakeMKV. Until it is set, MakeMKV isn't run and discs wait |
| `RIPSHELF_MAKEMKV_EULA_FILE` | none (the image sets it) | the licence of the MakeMKV that came with ripshelf. Setting it is what makes `ACCEPT_MAKEMKV_EULA` necessary |
| `RIPSHELF_MAKEMKV_PATH` | `<data>/makemkv` | where the per-drive MakeMKV folders are. A per-drive worker mounts the controller's folder here |
| `RIPSHELF_WORKER_TOKEN` | none | on a worker, its token (Settings → Remote workers). On the controller, a shared token accepted from any worker ([workers.md](workers.md)) |
| `RIPSHELF_WORKER_TOKEN_FILE` | none | a file the controller writes a random token to on first start, for workers that can read the same file |
| `RIPSHELF_CONTROLLER_URL` | `http://localhost:8380` | on a worker, the controller's address |
| `RIPSHELF_CONTROLLER_CA_FILE` | none | on a worker, a CA certificate (PEM) for an `https://` controller with a private CA |
| `RIPSHELF_WORKER_ID` / `WORKER_NAME` | the host name / none | on a worker, its id and the name shown in the UI |
| `RIPSHELF_WORKER_SHARED_SCRATCH` | off | on a worker, the controller's scratch folder is mounted at the same path, so rips aren't uploaded |
| `RIPSHELF_FAKE_HARDWARE` | off | simulated drives that replay recorded MakeMKV logs, for development |

## All environment variables

| Variable | Default | Notes |
|---|---|---|
| `RIPSHELF_DATA_DIR` / `SCRATCH_DIR` / `LIBRARY_DIR` / `BACKUP_DIR` | `/data`, `/scratch`, `/library`, `/backups` | The mounts. Library, scratch, films, TV and disc backup folders can also be changed in Settings → Drives and MakeMKV, and a folder changed there wins over the environment. The data folder can't be changed there, because it holds settings.json. In a container, a folder set in Settings must be inside a mount |
| `RIPSHELF_MOVIES_DIR` / `TV_DIR` | `<library>/Movies`, `<library>/TV` | separate roots for films and TV. When set, each must exist |
| `RIPSHELF_DISC_BACKUPS_DIR` | `<library>/Disc backups` | where full disc backups go. When set, it must exist ([backup.md](backup.md)) |
| `RIPSHELF_PORT` | 8380 | |
| `RIPSHELF_TMDB_API_KEY`, `RIPSHELF_MAKEMKV_KEY` | none | |
| `RIPSHELF_TVDB_API_KEY` / `TVDB_PIN` | none | the TheTVDB v4 key, and the PIN for user-supported keys. TheTVDB finds shows TMDB's search misses, and supplies episode lists |
| `RIPSHELF_TVDB_EPISODE_ORDER` | `aired` | `aired` or `dvd`, which of TheTVDB's episode orders is used. DVD order follows the discs, but Emby numbers episodes in aired order unless the library is set to DVD order |
| `RIPSHELF_OMDB_API_KEY` | none | the OMDb key, for a second film search ([providers.md](providers.md)) |
| `RIPSHELF_THEDISCDB_CONTRIBUTE` | `off` | `off`, `ask` or `auto`. Whether to send confirmed discs that TheDiscDB doesn't know ([thediscdb-contributing.md](thediscdb-contributing.md)) |
| `RIPSHELF_THEDISCDB_CONTRIBUTE_URL` / `THEDISCDB_API_KEY` | `https://thediscdb.com` / none | where contributions go, and an optional key sent as `Authorization: ApiKey` |
| `RIPSHELF_METADATA_PROVIDERS` | see [providers.md](providers.md) | the provider lists, as `{"movie": [{"name": "thediscdb", "enabled": true}, ...], "tv": [...], "episodes": [...]}`. Usually set in Settings |
| `RIPSHELF_TV_EPISODE_ORDER` | `tvdb_aired` | an old setting, only read to set up the lists for an install that has none yet. `tmdb` puts TMDB first for episodes, and `tvdb_dvd` means DVD order |
| `RIPSHELF_AUTO_CONFIRM_THRESHOLD` | 0.90 | films |
| `RIPSHELF_TV_AUTO_CONFIRM` / `TV_AUTO_THRESHOLD` | on / 0.90 | TV |
| `RIPSHELF_VERIFY_ORDER` | on | check a TV disc's episode order against its play-all title after ripping (Settings → Identification) |
| `RIPSHELF_AUTO_EJECT` | on | |
| `RIPSHELF_RIP_MODE` / `DRIVE_MODES` | `titles` / `{}` | `titles` or `backup` (a whole Blu-ray or UHD, decrypted). `DRIVE_MODES` sets it per drive id, for example `{"pioneer-...": "backup"}` ([backup.md](backup.md)) |
| `RIPSHELF_DRIVE_NAMES` | `{}` | a name for a drive id, shown instead of the model, for example `{"pioneer-...": "Top bay"}`. Usually set with the pencil on the drive card |
| `RIPSHELF_TRANSCODE_ENABLED` | on | also `TRANSCODE_CODECS` (`["vc1"]`), `TRANSCODE_CROP` (`off`, `when_transcoding` or `always`), `TRANSCODE_HDR`, `TRANSCODE_SOURCES` (per disc type, e.g. `{"bluray": {"transcode": true, "crop": false}}`), `TRANSCODE_ENCODER` (`auto`, `vaapi` or `x265`), `TRANSCODE_QUALITY` (21), `TRANSCODE_KEEP_ORIGINAL` and `TRANSCODE_PARALLEL` (1, how many jobs transcode at once, up to 8) |
| `RIPSHELF_TV_UNMATCHED` | `extras` | what happens to titles that aren't episodes. `extras`, `specials` (Season 00) or `skip` |
| `RIPSHELF_BACKUP_KEEP` | 7 | daily database backups kept, 0 turns them off (Settings → Drives and MakeMKV → Database backups) |
| `RIPSHELF_PUBLIC_URL` | none | this UI's address, used in notification links |
| `RIPSHELF_PUSHOVER_USER_KEY` / `PUSHOVER_APP_TOKEN` / `PUSHOVER_DEVICE` | none | notifications (Pushover only) |
| `RIPSHELF_NOTIFY_EVENTS` | `{"disc": null, "review": 1, "done": 0, "failed": 1, "setup": 0}` | which events notify, with their Pushover priority (-2 to 1). `null` is off |
| `RIPSHELF_NOTIFY_BATCH_S` | 120 | further notifications of one kind within this many seconds come as one summary. 0 sends every one |
| `RIPSHELF_EMBY_URL` / `EMBY_API_KEY` | none | Emby refresh |
| `RIPSHELF_EMBY_MOVIES_LIBRARY` / `EMBY_MOVIES_PATH`, `EMBY_TV_LIBRARY` / `EMBY_TV_PATH` | none | the Emby library for films and for TV, and its folder as Emby sees it. "Fetch libraries" in Settings sets these |
| `RIPSHELF_JELLYFIN_URL` / `JELLYFIN_API_KEY` | none | Jellyfin refresh. The library settings are as for Emby, with `JELLYFIN_` in place of `EMBY_` |
| `RIPSHELF_PLEX_URL` / `PLEX_TOKEN` | none | Plex refresh, with the same library settings (`PLEX_MOVIES_LIBRARY` and so on). Plex scans the folder each new file is in. With no library picked, it scans every library |
| `RIPSHELF_CATALOGUE_SEARCH` / `SEARXNG_URL` | off / none | looks catalogue-number labels up on the web ([catalogue.md](catalogue.md)) |
| `RIPSHELF_WORKER_TOKEN`, `CONTROLLER_URL`, `CONTROLLER_CA_FILE`, `WORKER_ID`, `WORKER_NAME`, `WORKER_SHARED_SCRATCH` | see [above](#settings-that-arent-on-the-settings-page) | remote workers ([workers.md](workers.md)) |
| `RIPSHELF_UHD_MODELS` | the Pioneer BDR-S11JX | the `VENDOR MODEL` of each LibreDrive drive |
