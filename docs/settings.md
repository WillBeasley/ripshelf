# Settings

Every setting, where it is stored, and the environment variable that sets it.

Most settings are changed on the Settings page. A fresh install opens a short setup page first (drives, MakeMKV, folders, TMDB key, extras), which uses the same fields, and "Run the setup again" under Settings → System brings it back.
- **Storage.** Settings are saved in `/data/settings.json` and win over the environment. Only the settings you change are saved there, so the rest keep following the environment.
- **Secrets.** Keys and tokens are stored, but never sent back to the browser. Clear removes one when you save.
- **Outside a container,** folders that aren't set are next to ripshelf (the source checkout). These are `data/`, `scratch/`, `library/` and the env file `ripshelf.env`. You can change them in Settings. A checkout that already has state in `var/` keeps using it.
- **Environment.** Any setting can also be set as an environment variable. The name is `RIPSHELF_` followed by the setting's name in capitals. Put it in the `environment:` section of compose.yaml, or in `/data/ripshelf.env`.
- **Test buttons.** Each connection (TMDB, TheTVDB, OMDb, TheDiscDB, TVmaze, MakeMKV key, Pushover, Emby, Jellyfin, Plex, the scanner, text reading, the vision model, the label model) and the folders have a Test button. It tries the values as typed, without saving them.

## The Settings page

Settings has nine sections, each at its own address (`/settings/identify` and so on):

| Section | What's in it |
|---|---|
| **Identify** | the metadata providers and their order, the TMDB, TheTVDB and OMDb keys, when a match is confirmed without review, web search (SearXNG and catalogue numbers), reading cryptic labels with an AI model, contributing to TheDiscDB |
| **Drives and ripping** | the MakeMKV key, what to rip, ejecting, [which audio and subtitle tracks are kept](#which-audio-and-subtitle-tracks-are-kept), each drive (grouped by the machine it's on), MakeMKV private data. Advanced: scan time limits, the poll interval, UHD drive models, MakeMKV's own selection rule |
| **Scanner and covers** | the network scanner, reading the box (Tesseract and a vision model), copying scans to the library. Advanced: text languages |
| **Library and filing** | the library, films and TV folders, how TV extras and the extras a box names are filed. Advanced: the scratch and disc backups folders, clearing failed jobs' scratch |
| **Media servers** | Emby, Jellyfin and Plex, one at a time |
| **Transcoding** | whether and what to transcode, the encoder and quality, the hardware found. Advanced: codecs, cropping, transcodes at once, the GPU |
| **Notifications** | Pushover, which events notify, batching, and this browser's alert sound |
| **Workers** | remote workers, and what they take from this machine |
| **System** | this UI's address (for notification links), daily backups, about ripshelf |

- **Wider than a phone,** a sidebar lists the sections. A dot marks a section with changes not saved yet, and **!** one a setup problem points to. On a phone, Settings opens on the list of sections, each with a line on what it's set to.
- **Search** at the top of the sidebar (or the phone's list) finds a setting by its name or what it's about, and jumps to it, opening its Advanced fold or media server if need be. A link to `/settings/<section>#setting-<name>` does the same.
- **Saving.** Save and Discard appear when the section shown has changes, and save that section only. Changes in other sections are kept, and named, until you save them there. Leaving Settings with changes not saved asks first. Ctrl+S saves.
- **Saved straight away.** MakeMKV private data and remote workers have their own buttons, and act at once.
- **This machine only.** Settings tagged so (UHD drive models, the encoder, transcodes at once, the GPU) are used by this machine and not sent to remote workers. A worker reads its own from its environment, such as `RIPSHELF_UHD_MODELS`.
- **Limits.** The server checks each setting's limits (the ranges and choices on the page), and a value in settings.json outside them is ignored, with a warning in the log, in favour of the default.

## Which audio and subtitle tracks are kept

makemkvcon has no option for picking tracks, but it follows MakeMKV's default selection rule (the app's "Default selection rule", in expert mode). With **Choose which audio and subtitle tracks are kept** on, ripshelf writes a rule into each drive's MakeMKV home before every rip, on remote workers too:

- **Languages.** Three-letter codes, such as `eng, jpn`. Languages with two codes (`fre` and `fra`, `ger` and `deu`) match either.
- **Audio.** In these languages, or every track. A disc's only audio track, and one with no language given, is kept either way. A stereo track with a surround one in the same language is dropped, as MakeMKV does by default.
- **Subtitles.** None, forced subtitles only, in these languages, or every track.
- **Keep commentary tracks.** Off drops the tracks MakeMKV marks as commentary.

Video is kept apart from a 3D disc's second view, as MakeMKV does. Under Advanced, **MakeMKV selection rule** takes a rule in MakeMKV's own syntax instead of the choices. Off leaves MakeMKV's own rule (its preferred language, English, and untagged tracks). A full disc backup keeps every track whatever is set. Transcoding copies the tracks the rip kept.

The choice is for every disc: a rip starts as soon as the scan is done, before there's a chance to pick tracks for one disc.

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
| `RIPSHELF_HOST` / `PORT` | `0.0.0.0` / 8380 | the address and port the web UI listens on. `0.0.0.0` is every network the machine is on. The Windows batch file sets `127.0.0.1`, so only that PC can open the UI ([windows.md](windows.md)) |
| `RIPSHELF_ALLOWED_HOSTS` | none | more names the UI is opened by, comma separated (`.example.com` for every name under it, `*` for any). IP addresses, `localhost`, names with no dots, names under `.local`, `.lan`, `.home`, `.home.arpa`, `.internal`, `.localhost` and `.localdomain`, and the public URL's name work without it ([troubleshooting.md](troubleshooting.md#ripshelf-isnt-served-at-)) |
| `RIPSHELF_BACKUP_DIR` | `/backups` | where [daily backups](#daily-backups) go. How many are kept is set in Settings |
| `RIPSHELF_MAKEMKVCON`, `FFMPEG`, `FFPROBE`, `SEVENZIP`, `BLKID`, `MKVMERGE` | the program name, found on the PATH | the programs ripshelf runs |
| `RIPSHELF_ACCEPT_MAKEMKV_EULA` | `no` | set `yes` once you have read and accepted MakeMKV's licence. Only needed where MakeMKV came with ripshelf (the image), and on every container that runs MakeMKV. Until it is set, MakeMKV isn't run and discs wait |
| `RIPSHELF_MAKEMKV_EULA_FILE` | none (the image sets it) | the licence of the MakeMKV that came with ripshelf. Setting it is what makes `ACCEPT_MAKEMKV_EULA` necessary |
| `RIPSHELF_MAKEMKV_PATH` | `<data>/makemkv` | where the per-drive MakeMKV folders are. A per-drive worker mounts the controller's folder here |
| `RIPSHELF_WORKER_TOKEN` | none | on a worker, its token (Settings → Workers). On the controller, a shared token accepted from any worker ([workers.md](workers.md)) |
| `RIPSHELF_WORKER_TOKEN_FILE` | none | a file the controller writes a random token to on first start, for workers that can read the same file |
| `RIPSHELF_CONTROLLER_URL` | `http://localhost:8380` | on a worker, the controller's address |
| `RIPSHELF_CONTROLLER_CA_FILE` | none | on a worker, a CA certificate (PEM) for an `https://` controller with a private CA |
| `RIPSHELF_WORKER_ID` / `WORKER_NAME` | the host name / none | on a worker, its id and the name shown in the UI |
| `RIPSHELF_WORKER_SHARED_SCRATCH` | off | on a worker, the controller's scratch folder is mounted at the same path, so rips aren't uploaded |
| `RIPSHELF_FAKE_HARDWARE` | off | simulated drives that replay recorded MakeMKV logs, for development |

## All environment variables

| Variable | Default | Notes |
|---|---|---|
| `RIPSHELF_DATA_DIR` / `SCRATCH_DIR` / `LIBRARY_DIR` / `BACKUP_DIR` | `/data`, `/scratch`, `/library`, `/backups` | The mounts. Library, scratch, films, TV and disc backup folders can also be changed in Settings → Library and filing, and a folder changed there wins over the environment. The data folder can't be changed there, because it holds settings.json. In a container, a folder set in Settings must be inside a mount |
| `RIPSHELF_MOVIES_DIR` / `TV_DIR` | `<library>/Movies`, `<library>/TV` | separate roots for films and TV. When set, each must exist |
| `RIPSHELF_DISC_BACKUPS_DIR` | `<library>/Disc backups` | where full disc backups go. When set, it must exist ([backup.md](backup.md)) |
| `RIPSHELF_PORT` | 8380 | |
| `RIPSHELF_TMDB_API_KEY`, `RIPSHELF_MAKEMKV_KEY` | none | |
| `RIPSHELF_TMDB_LANGUAGE` | `en-GB` | the language of TMDB's names and overviews |
| `RIPSHELF_TVDB_API_KEY` / `TVDB_PIN` | none | the TheTVDB v4 key, and the PIN for user-supported keys. TheTVDB finds shows TMDB's search misses, and supplies episode lists |
| `RIPSHELF_TVDB_EPISODE_ORDER` | `aired` | `aired` or `dvd`, which of TheTVDB's episode orders is used. DVD order follows the discs, but Emby numbers episodes in aired order unless the library is set to DVD order |
| `RIPSHELF_OMDB_API_KEY` | none | the OMDb key, for a second film search ([providers.md](providers.md)) |
| `RIPSHELF_THEDISCDB_CONTRIBUTE` | `off` | `off`, `ask` or `auto`. Whether to send confirmed discs that TheDiscDB doesn't know ([thediscdb-contributing.md](thediscdb-contributing.md)) |
| `RIPSHELF_THEDISCDB_CONTRIBUTE_URL` / `THEDISCDB_API_KEY` | `https://thediscdb.com` / none | where contributions go, and an optional key sent as `Authorization: ApiKey` |
| `RIPSHELF_METADATA_PROVIDERS` | see [providers.md](providers.md) | the provider lists, as `{"movie": [{"name": "thediscdb", "enabled": true}, ...], "tv": [...], "episodes": [...]}`. Usually set in Settings |
| `RIPSHELF_TV_EPISODE_ORDER` | `tvdb_aired` | an old setting, not on the Settings page. It's only read, from the environment or an old settings.json, to set up the provider lists and TheTVDB's episode order for an install that has neither yet. `tmdb` puts TMDB first for episodes, and `tvdb_dvd` means DVD order |
| `RIPSHELF_AUTO_CONFIRM_THRESHOLD` | 0.90 | films |
| `RIPSHELF_TV_AUTO_CONFIRM` / `TV_AUTO_THRESHOLD` | on / 0.90 | TV |
| `RIPSHELF_LABEL_AI` / `LABEL_AI_MODEL` | `off` / none | read cryptic labels with an AI model: `off`, `manual` (a button in Review) or `auto` (also when nothing found fits); the model, on the vision model's connection (blank uses the vision model) ([cryptic-labels.md](cryptic-labels.md)) |
| `RIPSHELF_VERIFY_ORDER` | on | check a TV disc's episode order against its play-all title after ripping (Settings → Identify) |
| `RIPSHELF_SCANNER_ENABLED` / `SCANNER_URL` | off / none | a network scanner (eSCL, AirScan) for scanning a disc's box, as `http://printer/eSCL` ([cover-scans.md](cover-scans.md)) |
| `RIPSHELF_SCANNER_DPI` / `SCANNER_SOURCE` | 300 / `platen` | the scan's resolution, and `platen` (the glass) or `feeder` |
| `RIPSHELF_SCANNER_TRIM` | on | cut away the empty glass around a scan (the original is kept) |
| `RIPSHELF_SCANS_TO_LIBRARY` | on | copy a job's scans into `scans/` in the film's or show's folder once it's filed |
| `RIPSHELF_SCAN_READ_TEXT` | on | read the text on scans and list what's on the disc ([cover-scans.md](cover-scans.md)) |
| `RIPSHELF_OCR_LANGUAGES` | `eng` | Tesseract's languages, joined with `+` (such as `eng+fra`); each needs its data installed |
| `RIPSHELF_TESSERACT` | `tesseract` | the Tesseract program (found in Program Files on Windows) |
| `RIPSHELF_VISION_MODE` | `off` | when a vision model reads scans: `off`, `struggles` (when Tesseract struggles), `every` or `manual` ([cover-scans.md](cover-scans.md#a-vision-model-as-a-second-reader)) |
| `RIPSHELF_VISION_PROVIDER` / `VISION_URL` / `VISION_MODEL` / `VISION_API_KEY` | `local` / none / the provider's / none | where the vision model is: `local` (an OpenAI-compatible server such as Ollama), `openai` (a hosted OpenAI-compatible service) or `anthropic`; its address (not for `anthropic`), model and key. Unset, the model is `qwen3-vl:4b-instruct` for `local` and `claude-sonnet-5-5` for `anthropic`; `openai` needs one named |
| `RIPSHELF_EXTRAS_EMBY_FOLDERS` | on | file extras the cover scan names in Emby's extras folders (featurettes/, trailers/...) under their names, and rip a film's ([cover-scans.md](cover-scans.md#extras-in-the-library)) |
| `RIPSHELF_FILM_UNNAMED_EXTRAS` | `skip` | a film's titles the box doesn't name: `skip`, or `extras` to rip them into extras/ as "Title N" |
| `RIPSHELF_TV_BONUS_EPISODES` | `extra` | a TV box's bonus episode of another show: `extra` files it in this show's `extras/` as "Other show - Episode", `own_show` in the other show's season folder ([cover-scans.md](cover-scans.md#bonus-episodes-of-other-shows)) |
| `RIPSHELF_AUTO_EJECT` | on | |
| `RIPSHELF_TRACK_SELECTION` | off | choose which audio and subtitle tracks are kept, with `TRACK_LANGUAGES` (`["eng"]`), `TRACK_AUDIO` (`languages` or `all`), `TRACK_SUBTITLES` (`none`, `forced`, `languages` or `all`), `TRACK_COMMENTARY` (on) and `TRACK_RULE` (MakeMKV's own syntax, used instead) ([above](#which-audio-and-subtitle-tracks-are-kept)) |
| `RIPSHELF_SCAN_TIMEOUT_S` / `SCAN_MAX_S` | 300 / 7200 | stop a scan once MakeMKV has said nothing for this long, and in any case after the limit. Settings takes 30 to 3600 and 300 to 43200 |
| `RIPSHELF_POLL_INTERVAL_S` | 3 | how often drives are checked for a disc. Settings takes 1 to 60 |
| `RIPSHELF_RIP_MODE` / `DRIVE_MODES` | `titles` / `{}` | `titles` or `backup` (a whole Blu-ray or UHD, decrypted). `DRIVE_MODES` sets it per drive id, for example `{"pioneer-...": "backup"}` ([backup.md](backup.md)) |
| `RIPSHELF_DISABLED_DRIVES` | `[]` | drive ids ripshelf leaves alone, for example `["pioneer-..."]`. Usually set with the ticks in Settings → Drives |
| `RIPSHELF_DRIVE_NAMES` | `{}` | a name for a drive id, shown instead of the model, for example `{"pioneer-...": "Top bay"}`. Usually set with the pencil on the drive card |
| `RIPSHELF_TRANSCODE_ENABLED` | on | also `TRANSCODE_CODECS` (`["vc1"]`), `TRANSCODE_CROP` (`off`, `when_transcoding` or `always`), `TRANSCODE_HDR`, `TRANSCODE_SOURCES` (per disc type, e.g. `{"bluray": {"transcode": true, "crop": false}}`), `TRANSCODE_ENCODER` (`auto`, `qsv`, `vaapi`, `nvenc` or `x265`; `auto` uses the first of Quick Sync, VAAPI and NVENC whose test encode works, else x265), `VAAPI_DEVICE` (the render node Quick Sync and VAAPI use, `/dev/dri/renderD128`), `TRANSCODE_QUALITY` (21), `TRANSCODE_KEEP_ORIGINAL` and `TRANSCODE_PARALLEL` (1, how many jobs transcode at once, up to 8) |
| `RIPSHELF_TV_RIP_EXTRAS` | on | TV titles that look like extras start as Extra in review. Off, they start as Skip |
| `RIPSHELF_TV_UNMATCHED` | `extras` | where a TV disc's extras go when the cover scan doesn't name them: `extras` (the show's extras folder), `specials` (Season 00, numbered after the specials already there) or `skip` (not copied to the library, and named extras aren't either). "TV extras go" in Settings → Library and filing |
| `RIPSHELF_BACKUP_KEEP` | 7 | [daily backups](#daily-backups) kept, 0 turns them off (Settings → System) |
| `RIPSHELF_PUBLIC_URL` | none | this UI's address, used in notification links. Its name is also one the UI may be opened by and changes may come from, as through a reverse proxy ([installation.md](installation.md#behind-a-reverse-proxy)) |
| `RIPSHELF_PUSHOVER_USER_KEY` / `PUSHOVER_APP_TOKEN` / `PUSHOVER_DEVICE` | none | notifications (Pushover only) |
| `RIPSHELF_NOTIFY_EVENTS` | `{"disc": null, "review": 1, "exists": 1, "ripped": 0, "transcoded": null, "copied": null, "done": 0, "failed": 1, "setup": 0}` | which events notify, with their Pushover priority (-2 to 1). `null` is off, and an event left out gets its default ([how-it-works.md](how-it-works.md#notifications)) |
| `RIPSHELF_NOTIFY_BATCH_S` | 120 | further notifications of one kind within this many seconds come as one summary. 0 sends every one |
| `RIPSHELF_EMBY_URL` / `EMBY_API_KEY` | none | Emby refresh |
| `RIPSHELF_EMBY_MOVIES_LIBRARY` / `EMBY_MOVIES_PATH`, `EMBY_TV_LIBRARY` / `EMBY_TV_PATH` | none | the Emby library for films and for TV, and its folder as Emby sees it. "Fetch libraries" in Settings sets these |
| `RIPSHELF_JELLYFIN_URL` / `JELLYFIN_API_KEY` | none | Jellyfin refresh. The library settings are as for Emby, with `JELLYFIN_` in place of `EMBY_` |
| `RIPSHELF_PLEX_URL` / `PLEX_TOKEN` | none | Plex refresh, with the same library settings (`PLEX_MOVIES_LIBRARY` and so on). Plex scans the folder each new file is in. With no library picked, it scans every library |
| `RIPSHELF_CATALOGUE_SEARCH` / `SEARXNG_URL` | off / none | looks catalogue-number labels up on the web ([catalogue.md](catalogue.md)) |
| `RIPSHELF_BARCODE_SERVICE` / `BARCODE_SERVICE_KEY` | `off` / none | a paid lookup for cover barcodes TheDiscDB and UPCitemdb don't know: `barcodelookup` or `eansearch`, and its key ([cover-scans.md](cover-scans.md#the-box-helps-identify-the-disc)) |
| `RIPSHELF_WORKER_TOKEN`, `CONTROLLER_URL`, `CONTROLLER_CA_FILE`, `WORKER_ID`, `WORKER_NAME`, `WORKER_SHARED_SCRATCH` | see [above](#settings-that-arent-on-the-settings-page) | remote workers ([workers.md](workers.md)) |
| `RIPSHELF_UHD_MODELS` | the Pioneer BDR-S11JX | the `VENDOR MODEL` of each LibreDrive drive. Each machine reads its own: set it on a worker for that worker's drives |

## Daily backups

Once a day ripshelf writes two files to the backups folder (`/backups`, or `RIPSHELF_BACKUP_DIR`), and keeps the newest of each as set in **Settings → System** (7 unless changed, 0 turns them off):

- `ripshelf-<date>.db`, a copy of the database, taken safely while ripshelf runs.
- `ripshelf-<date>-files.tar.gz`, with `settings.json`, the per-drive MakeMKV folders (`makemkv/`, LibreDrive data and firmware dumps) and the files kept for remote workers (`makemkv-files/`). It holds the API keys, the MakeMKV key and worker tokens, so it is readable only by ripshelf's user. Keep the backups folder as private as `data/`.

To restore, stop ripshelf, copy the `.db` file to `data/ripshelf.db`, and extract the archive into `data/` (`tar -xzf backups/ripshelf-<date>-files.tar.gz -C data`). With `RIPSHELF_MAKEMKV_PATH` set, move the extracted `makemkv/` folder there.
