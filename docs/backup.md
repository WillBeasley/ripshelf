# Full disc backups

Normally ripshelf rips titles, meaning the main feature as one MKV, or every episode of a TV disc. In **full backup** mode it copies the whole disc instead, as MakeMKV's own *Backup* does (people often call this "ISO mode").

Turn it on in **Settings → Drives and MakeMKV → What to rip**, for every drive or for one. For example, set the UHD drive to *Full backup* and leave the others on titles.

## What you get

A decrypted copy of the disc's file system, named after the identified film:

```
disc-backups/The Brass Heron (2024) - 2160p/
    BDMV/            index.bdmv, PLAYLIST/, STREAM/*.m2ts, ...
    CERTIFICATE/
    .disc/disc.json  label, fingerprints, MakeMKV version, and a SHA-256 for every file
    .disc/makemkv-scan.log
```

- **Everything is kept.** Menus, extras, every playlist, every audio and subtitle track, and the HDR / Dolby Vision layers, untouched.
- **Playable as it is.** Emby, Jellyfin, Kodi and VLC play a `BDMV` folder as the film. Add the disc backups folder as a Movies library.
- **Re-rippable later.** MakeMKV opens the folder like the disc (`makemkvcon info file:/path/to/folder`), with no drive and no decryption needed.
- **Never transcoded.** The transcode settings don't apply to backups.

## Why a folder and not an .iso

`makemkvcon backup` writes a folder, because MakeMKV has no ISO output. An ISO would mean building a UDF 2.50 image afterwards (another tool, and twice the scratch space for a 100 GB UHD disc) to get something that plays and re-rips no better than the folder. If you need an image for something else, `mkudffs` or `xorriso` can make one from the folder.

## What it doesn't do

- **DVDs.** MakeMKV backs up Blu-ray and UHD discs only. A DVD in a backup drive is ripped as titles, and the job log says so.
- **TV.** A backup keeps the whole disc, so there are no episodes to map. A TV disc is named like a film (search for the show and pick a name in review).
- **Remote workers that don't share scratch.** A backup is a folder tree, and those workers upload single files. Their drives rip titles instead. Per-drive workers on the same host (compose.per-drive.yaml) share scratch and back up fine.

## Space

A backup is the size of the disc, up to ~25 GB (BD-25), ~50 GB (BD-50) or ~100 GB (UHD). Scratch needs that much free while it's made, and the library copy is checked file by file (SHA-256) before scratch is cleared.

## Setup

compose.yaml mounts the folder:

```yaml
    environment:
      RIPSHELF_DISC_BACKUPS_DIR: /disc-backups
    volumes:
      - ./disc-backups:/disc-backups
```

Create the folder first (`mkdir disc-backups`). Without these lines, backups go to `<library>/Disc backups`. While any drive is in backup mode, a missing or read-only folder shows as a setup problem.

## How it works

MakeMKV's `backup` command only takes a drive by number (`disc:N`), not by device. ripshelf reads the number from the drive list in the disc's scan, then runs:

```
makemkvcon -r --progress=-same --decrypt backup disc:N <scratch>/job-ID
```

The rip counts as done when MakeMKV exits cleanly, reports "Backup done." (not "Backup done but N files failed hash check"), and a `BDMV` folder exists. Identification, review and the disc cache work as for films.
