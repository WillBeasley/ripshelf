# Changelog

What changed in each release of ripshelf, and what to do when upgrading to it. The newest release is at the top. Versions follow [docs/ci-github.md](docs/ci-github.md#releasing): while ripshelf is `0.x`, a new minor version brings new features or changes to settings or the database, and a patch brings fixes only.

Each release lists its pull requests on [GitHub](https://github.com/WillBeasley/ripshelf/releases). **Upgrading** notes come first where a release needs something from you. Update as [installation.md](docs/installation.md#updating) says, and not in the middle of a rip.

## Unreleased

This will be 0.7.0. Release candidates: [0.7.0-rc1](https://github.com/WillBeasley/ripshelf/releases/tag/v0.7.0-rc1) and [0.7.0-rc2](https://github.com/WillBeasley/ripshelf/releases/tag/v0.7.0-rc2), both on 2026-11-11.

### Upgrading

- **The show collections are migrated, and there's no way back.** Collections used to be keyed by the show's TMDB id. On its first start, 0.7.0 rewrites the `collections` table of `data/ripshelf.db` to key them as `tmdb:<id>`, so shows found on TheTVDB or TVmaze can have one too. Earlier releases can't read the new table. To be able to go back, copy `data/ripshelf.db` before updating (or keep a daily backup from `./backups` made before the update) and restore it if you downgrade. Jobs ripped after the update aren't in that copy.
- The API's propose request takes `show_key` (`"tmdb:1399"`, `"tvmaze:82"`). `show_tmdb_id` is still accepted from older clients.
- **Windows:** `start-ripshelf.bat` now installs the Python packages and builds the UI again whenever they change. After a `git pull` from 0.5.0 to 0.6.0 it did neither, so 0.6.0 failed with "No module named 'PIL'" and served the old UI. Updating to 0.7.0 with the new script fixes that by itself.

### Added

- Identify a finished job again: "Wrong title? Identify again" sends it back to Review, and confirming the right title moves its files, extras, cover scans and `.disc` record, with nothing ripped again ([#27](https://github.com/WillBeasley/ripshelf/pull/27)).
- TV without a TMDB key: the show search asks TheTVDB and TVmaze, and episode lists come from the show's own provider ([#31](https://github.com/WillBeasley/ripshelf/pull/31)).
- Crop a cover scan by hand, by dragging its edges or with sliders. Automatic trimming now also cuts past the glass's edge shadow ([#26](https://github.com/WillBeasley/ripshelf/pull/26)).
- One list of a TV disc's titles with an episode picker. A title can hold any episodes, from any season, and the box's picks are taken title by title ([#30](https://github.com/WillBeasley/ripshelf/pull/30)).
- Every long title the box's episodes are in is split, not just the first ([#33](https://github.com/WillBeasley/ripshelf/pull/33)).
- The UI has unit tests (Vitest) and browser tests (Playwright) against simulated drives and a pretend TMDB, run in CI ([#34](https://github.com/WillBeasley/ripshelf/pull/34)).
- The daily backup keeps `settings.json` and the MakeMKV data (`makemkv/`, `makemkv-files/`) too, as `ripshelf-<date>-files.tar.gz` beside the database copy ([settings.md](docs/settings.md#daily-backups)).
- Dependabot proposes updates to the UI's packages, the CI actions and the Dockerfile's base images each week, and the base images are pinned by digest. A weekly workflow checks the Python locks for known vulnerabilities.
- The barcode on a cover scan is looked up on TheDiscDB, then UPCitemdb, then a paid barcode service if you set one up (Barcode Lookup or EAN-Search, Settings → Identify → Barcodes on covers), then your SearXNG. A lookup that failed is tried again next time instead of being kept as "not found", and a changed barcode is looked up again.
- A covers shelf: scan a stack of case sheets ahead of their discs (both sides at once, or in two passes turning the stack over), or upload pictures and PDFs. Each sheet is read, blank sides are left out, and when its disc goes in the job offers it to use as its scans ([cover-scans.md](docs/cover-scans.md#the-covers-shelf)). PDFs are read with pypdfium2, a new Python dependency.

### Changed

- The UI says when a TV disc's episode order is only a guess ([#28](https://github.com/WillBeasley/ripshelf/pull/28)), and up front when a long title holds several episodes.
- Windows support was fixed: Quick Sync without VAAPI, Tesseract and zbar checks on the setup page, and `.disc` records written with `/` ([#25](https://github.com/WillBeasley/ripshelf/pull/25)).

### Fixed

- A `settings.json` with only disabled virtual drives is treated as a new install, so the setup page shows instead of ripping whatever is in the drives.
- A long title isn't copied whole into the library once it has been split ([#33](https://github.com/WillBeasley/ripshelf/pull/33)).
- A rip on a worker could be called failed when it had worked. MakeMKV's last lines ("Copy complete"), which say the rip worked, were dropped if they were still on their way to the controller as the rip ended, which a busy worker or a slow link made likely.
- **Windows:** a disc's content fingerprint is the same as on Linux. Its structure files were named with backslashes, so a disc read on Windows wasn't recognised from a Linux rip, or by TheDiscDB.
- **Windows:** a library, TV, backup or daily-backup folder you can't write to is reported on the setup page and stops the job before the copy. Windows ignored the folder's permissions, so the job only failed when the copy started.
- **Windows:** identifying a TV disc again wrote its `.disc/disc.json` file names with backslashes.
- A kind of disc with transcoding switched off isn't read with ffprobe first, so without ffprobe it's copied as is instead of warning that the file couldn't be read.
- The whole test suite runs on Windows in CI, not just the Windows-only code.

## 0.6.0 (2026-11-08)

Tagged without a GitHub release; its changes are listed in [0.7.0-rc1's notes](https://github.com/WillBeasley/ripshelf/releases/tag/v0.7.0-rc1).

### Upgrading

- **Native installs:** cover scans read text with Tesseract and barcodes with zbar. Both are optional and already in the image. On Linux install `tesseract-ocr` and `libzbar0`. On Windows see [windows.md](docs/windows.md): Tesseract from UB-Mannheim, and the Visual C++ 2013 runtime for zbar.
- **Windows:** `start-ripshelf.bat` in 0.6.0 doesn't reinstall the Python packages after a `git pull`, so run `.venv\Scripts\python -m pip install -r requirements.lock` and rebuild the UI (`cd web`, `npm ci`, `npm run build`) by hand, or go straight to 0.7.0.
- Settings has nine sections, each on its own page, under new names ([docs/settings.md](docs/settings.md)). The names in `settings.json` and the `RIPSHELF_` variables are unchanged.
- `tv_episode_order` can't be edited any more. It is still read from older `settings.json` files.
- A value in `settings.json` outside a setting's limits is now skipped with a warning in the log, where it used to stop ripshelf from starting. Check the log after updating.

### Added

- Cover scans: keep pictures of the box, scan them from an eSCL scanner or upload a photo, before the disc goes in or while it's being identified ([#22](https://github.com/WillBeasley/ripshelf/pull/22)).
- The text and barcode on the box are read, and its listing is matched to the disc's titles: the box's episode names pick the show and its episodes, and extras it names are filed in Emby's extras folders.
- An optional vision model (Ollama, any OpenAI-compatible server, or Anthropic) reads the box beside Tesseract.
- Bonus episodes of another show are ripped and filed under that show.
- The Shows page can take a disc out of a collection.

### Changed

- Settings: a page per section, with a sidebar, search, phone layout, and each section saved on its own with a warning before leaving unsaved changes ([#23](https://github.com/WillBeasley/ripshelf/pull/23)).
- Deleting a job takes its disc out of its show's collection, and can delete the files it filed in the library (asked, kept by default). Episodes whose files are gone from the library show as gone and don't count when a new disc carries on from the last one.
- A disc's files are read as UDF first, so more DVDs get a TheDiscDB hash, and a job without one can read the disc again.

### Fixed

- Anthropic vision requests go to Anthropic, not to an address left over from an Ollama setting.
- A scan of a complex multiplex may stay quiet for longer than the idle timeout without being stopped.

## 0.5.0 (2026-11-05)

Release candidates: [0.5.0-rc1](https://github.com/WillBeasley/ripshelf/releases/tag/v0.5.0-rc1) and [0.5.0-rc2](https://github.com/WillBeasley/ripshelf/releases/tag/v0.5.0-rc2) on 2026-11-04. [Release notes](https://github.com/WillBeasley/ripshelf/releases/tag/v0.5.0).

### Upgrading

- **Native installs:** splitting a TV disc's one long title into episodes needs `mkvmerge`, from MKVToolNix. The image has it. On Linux install `mkvtoolnix`; on Windows install MKVToolNix, which ripshelf finds in Program Files.
- **Notifications:** the "Ripped" event is now called "Done". A new "Rip finished" event is on by default, so expect one more notification per disc, or turn it off in Settings. "Transcode finished" and "Copied to library" are new and off by default.
- **Building the UI:** it moved to SvelteKit 3, which needs Node 20.19, 22.12 or later.

### Added

- Notifications when the rip, transcode and copy to the library finish, and a review link that opens the Review page by itself ([#13](https://github.com/WillBeasley/ripshelf/pull/13), [#14](https://github.com/WillBeasley/ripshelf/pull/14)).
- TV discs with every episode in one long title are split into episodes, at chapters or at breaks found in the picture and sound, with a timeline in Review to move the cuts ([#15](https://github.com/WillBeasley/ripshelf/pull/15), [#19](https://github.com/WillBeasley/ripshelf/pull/19), [docs/tv-splitting.md](docs/tv-splitting.md)).
- When nothing matches, Review can search the web, eject the disc and show frames from it ([#17](https://github.com/WillBeasley/ripshelf/pull/17)).
- TheDiscDB: release details, covers and every item type ([#18](https://github.com/WillBeasley/ripshelf/pull/18)).

### Changed

- The UI moved to SvelteKit 3 ([#12](https://github.com/WillBeasley/ripshelf/pull/12)).

## 0.4.0 (2026-10-31)

[Release notes](https://github.com/WillBeasley/ripshelf/releases/tag/v0.4.0).

### Upgrading

- With the encoder on `auto`, ripshelf now tries Quick Sync first, then VAAPI, then NVENC, and uses the first that passes a short test encode. An Intel machine that encoded with VAAPI may move to Quick Sync. Set the encoder to `vaapi` to keep it.

### Added

- Quick Sync and NVENC encoders. The image adds the oneVPL runtime (`libmfx-gen1.2`), so Quick Sync works on 12th gen Intel and later ([#5](https://github.com/WillBeasley/ripshelf/pull/5)).
- `transcode_parallel` (`RIPSHELF_TRANSCODE_PARALLEL`, 1 to 8, default 1), the number of transcodes at once ([#3](https://github.com/WillBeasley/ripshelf/pull/3)).

### Fixed

- Release region codes such as `WW`, `INT` and `NORDIC` are dropped from disc labels before searching.
- A film search with no results is tried again a word shorter ([#2](https://github.com/WillBeasley/ripshelf/pull/2)).
- Helper processes are stopped when ripshelf shuts down ([#7](https://github.com/WillBeasley/ripshelf/pull/7)).

## 0.3.0 (2026-10-30)

The first public release, with the image published as `ghcr.io/willbeasley/ripshelf`.
