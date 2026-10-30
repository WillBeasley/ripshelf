# How it works

What happens to a disc from the moment it goes in, and where the files end up.

```
detected → scanning → identifying → ripping → [transcoding] → finalising → done
                                  ↘ review ↗
```

## Identification

- **TheDiscDB.** If the disc is in [TheDiscDB](https://thediscdb.com), its record says what the disc is and which title is the film or which episode. ripshelf finds it by fingerprints read from the disc in seconds, before the scan.
- **Films.** Otherwise the label (and a Blu-ray's own title metadata) is matched by title, year and runtime, on TMDB and any other film search switched on. A score of 0.90 or more is confirmed automatically.
- **TV.** The show is found, episodes are mapped by runtime, and their order is checked against the play-all title.
- **Catalogue numbers.** [Catalogue-number labels](catalogue.md) can be looked up on the web.
- **Review.** A disc ripshelf isn't sure about waits on the Review page with its candidates and the evidence for each. The rip carries on meanwhile, so confirming it doesn't delay the rip.

[providers.md](providers.md) lists the providers and the order they're asked in.

## Ripping

MakeMKV rips the main feature of a film, or every episode of a TV disc. A drive can instead make a [full disc backup](backup.md). Each drive has its own MakeMKV data folder (LibreDrive data, firmware dumps), so UHD drives and older drives can sit side by side.

Once a disc is ripped, its job leaves the drive card so the drive can take the next disc, and shows under **Transcoding and copying** on the Overview page until it reaches the library.

## Transcoding

Transcoding is optional, and on by default for VC-1 only. VC-1 is re-encoded to H.265 on the GPU (VAAPI) or the CPU (x265), with black bars cropped. HDR and UHD are left alone unless you say otherwise. Settings → Transcoding sets the codecs, cropping, encoder and quality, whether DVDs, Blu-rays and 4K UHDs are each transcoded and cropped (say, transcode Blu-rays but never crop them), and lists the GPUs and ffmpeg hardware encoders that work in the container (each tested with a short encode).

A job's panel has a **Transcode log** next to the MakeMKV log. It has the ffmpeg command, a progress line every 5%, ffmpeg's errors and how it ended.

## Finalising

Files are copied into the library, checked by SHA-256, and never overwritten. They're named like this:

- Films go in `Movies/Title (Year)/Title (Year).mkv`
- TV goes in `TV/Show (Year)/Season 01/Show - S01E03 - Name.mkv`

Titles on a TV disc that aren't episodes go to extras, Season 00, or are skipped (Settings). Afterwards Emby, Jellyfin and Plex (whichever are set up) are told about the new files, Pushover notifies you, and the disc is ejected.

## Drives

- **Drive ids.** They come from the drive's vendor, model and serial number, read from the drive. Its history, settings, name and MakeMKV data stay with it on another port, another `/dev/srN`, or another machine. A drive that reports no serial falls back to an id from its port (logged at start-up).
- **Names.** The pencil next to a drive's name (on its card, or in Settings) gives it a name such as "Top bay", used everywhere instead of the model.
- **Statistics.** The Overview page shows all-time statistics per drive and the free space on each disk ripshelf writes to.

## Architecture

ripshelf is one service. A controller (Python, FastAPI and SQLite) serves a SvelteKit web UI, and an embedded worker runs MakeMKV on the local drives. Workers can also run [one per drive](modes.md) or on [other machines](workers.md), and the controller keeps the UI, the database, identification, transcoding and the library.
