# How it works

What happens to a disc from the moment it goes in, and where the files end up.

```
detected → scanning → identifying → ripping → [transcoding] → finalising → done
                                  ↘ review ↗
```

A job can also wait in two other states on the way.

- **Already in library** (`clash`). The film or episodes are in the library already. The job waits for you to overwrite them, keep both (films), skip the episodes already there (TV), or skip the disc.
- **Ejected to check** (`checking`). You ejected the disc from Review to look at it. The job carries on when the same disc goes back in.

Any job can end as failed or cancelled instead of done.

## Identification

- **TheDiscDB.** If the disc is in [TheDiscDB](https://thediscdb.com), its record says what the disc is and which title is the film or which episode. ripshelf finds it by fingerprints read from the disc in seconds, before the scan.
- **Films.** Otherwise the label (and a Blu-ray's own title metadata) is matched by title, year and runtime, on TMDB and any other film search switched on. A score of 0.90 or more is confirmed automatically.
- **TV.** The show is found, episodes are mapped by runtime, and their order is checked against the play-all title.
- **Catalogue numbers.** [Catalogue-number labels](catalogue.md) can be looked up on the web.
- **Review.** A disc ripshelf isn't sure about waits on the Review page with its candidates and the evidence for each. The rip carries on meanwhile, so confirming it doesn't delay the rip.

[providers.md](providers.md) lists the providers and the order they're asked in.

### Reviewing a TV disc

The Review card lists the disc's titles in disc order, one row each: the episodes it holds, why (the box's name and running time, the season's order, your pick), and an edit button. A title that still needs an episode is outlined, says what the [cover scan](cover-scans.md) calls it, and offers its closest episodes to tap. Play-alls, extras and skipped titles are under **Also on the disc**.

The edit button opens the episode picker (a sheet from the bottom on a phone, a panel on a computer):

- **Search** by name, or by code: `s2e5`, `S02E05`, `2x05` and `205` all find season 2, episode 5. The season buttons show one season.
- **Best guesses** from the box's name come first.
- **Several episodes** can be ticked, from any seasons: a two-part story, or a compilation's title holding two short episodes. A run of one season is filed as a range (`S01E04-E05`); anything else takes the first episode's code, and the name lists the others (`S01E05 - Name & S03E07 Other Name`).
- **An episode another title has** is greyed with "on title 3". Ticking it still works: that title gets this one's old episode instead, or none, to pick again.
- **Not an episode** makes the title an extra, a play-all, or skipped.

Each change is saved as you make it, so it survives a reload or picking up on another device, and the cover scan never changes a title you set. Nothing is filed until you press **Confirm**; on a phone it stays at the bottom of the screen with how many titles still need an episode. The show sits at the top with **Change**. Scanning and the box's listing are under **Cover scans**, and the run order from the case, splitting a long title, snapshots and ejecting to check are under **More tools**.

### TV discs with every episode in one title

Some TV discs put every episode into one long title. ripshelf rips it whole, finds where each episode starts (chapters, or black frames and silence), and cuts it into one file per episode without re-encoding, after you review the cuts. See [TV discs with every episode in one title](tv-splitting.md).

## Ripping

MakeMKV rips the main feature of a film, or every episode of a TV disc. A drive can instead make a [full disc backup](backup.md). Each drive has its own MakeMKV data folder (LibreDrive data, firmware dumps), so UHD drives and older drives can sit side by side.

Once a disc is ripped, its job leaves the drive card so the drive can take the next disc, and shows under **Transcoding and copying** on the Overview page until it reaches the library.

## Transcoding

Transcoding is optional, and on by default for VC-1 only. VC-1 is re-encoded to H.265 on the GPU (Intel Quick Sync, VAAPI or NVIDIA NVENC) or the CPU (x265), with black bars cropped. HDR and UHD are left alone unless you say otherwise. Settings → Transcoding sets the codecs, cropping, encoder and quality, whether DVDs, Blu-rays and 4K UHDs are each transcoded and cropped (say, transcode Blu-rays but never crop them), and lists the GPUs and ffmpeg hardware encoders that work in the container (each tested with a short encode).

One job transcodes at a time by default, and the others wait under **Transcoding and copying**. **Transcodes at once** raises that, up to 8. It is one limit for the whole install, because workers never transcode: every rip comes back to the controller, which runs ffmpeg. A job still does its own files (a TV disc's episodes) one after another. One is right for x265, which already uses every CPU core; a GPU can often run two or three encodes side by side.

A job's panel has a **Transcode log** next to the MakeMKV log. It has the ffmpeg command, a progress line every 5%, ffmpeg's errors and how it ended.

## Finalising

Files are copied into the library, checked by SHA-256, and never overwritten. They're named like this:

- Films go in `Movies/Title (Year)/Title (Year).mkv`
- TV goes in `TV/Show (Year)/Season 01/Show - S01E03 - Name.mkv`

Titles on a TV disc that aren't episodes go to extras, Season 00, or are skipped (Settings). Afterwards Emby, Jellyfin and Plex (whichever are set up) are told about the new files, Pushover notifies you, and the disc is ejected.

### Filed as the wrong title

A disc confirmed as the wrong film or show (auto-confirmed, most likely) can be put right after it's done. Open the job and choose **Wrong title? Identify again**. It goes back to Review, on the same card as a new disc: search, pick the film, or the show, season and episodes, and confirm. Nothing is ripped or transcoded again. The files it put in the library move to the new names: the film or episodes, kept originals, extras, cover scans and the `.disc` record, which notes what it was before. The old folder goes once nothing else is in it (a media server's artwork and `.nfo` files of the wrong title go with it). The disc moves to the right show on the Shows page, and a film's disc is recognised as the right film from then on.

- Nothing is moved over a file already in the library. If the new title's file is there already, confirming says so and nothing moves; move or delete it, pick another title, or keep it as it was.
- A TV disc can only map the titles it put in the library the first time. A title it skipped needs the disc ripping again.
- **Keep it as …** (on the card, or Cancel) leaves it as it was. If a move failed half way (a share went away), Retry carries on from that file, and Keep it as … puts back the files already moved.
- A disc already sent to TheDiscDB under the wrong title has to be corrected on TheDiscDB's site; the job's history says so.

### Shows and old jobs

The **Shows** page lists each show's discs and which episodes they hold, and a new disc of the show carries on from them (its first episode follows the last disc's). It goes by the library: an episode whose file isn't in the show's folder any more (deleted or moved by hand) is shown as gone, and doesn't count. When the TV folder can't be seen at all, as with a library that's offline, nothing is taken as gone.

- **Remove from collection** takes a disc off the page. Its files are kept.
- **Deleting a job** (its panel, or the Finished page) takes its disc off the Shows page too. You're then asked whether to delete the files it put in the library as well; Cancel keeps them. Only the files it filed are deleted (as the disc's `.disc` record lists them, or the film and its extras), never anything outside the films and TV folders, and never a full disc backup.

## Notifications

Pushover tells you about each job as it goes. Under Settings → Notifications each event can be off or have its own priority:

| Event | When | Default |
|---|---|---|
| Disc inserted | a disc goes into a drive | off |
| Needs review | it couldn't be identified for sure | high |
| Already in library | a copy is already there, so you decide | high |
| Rip finished | MakeMKV is done. It says what happens next: transcoding to H.265, or copying straight into the library (and why) | normal |
| Transcode finished | the transcode step is done, with how many files were transcoded and the size saved | off |
| Copied to library | the files are copied and checked, with where they went | off |
| Done | everything is finished and the media servers are told | normal |
| Failed | a job stopped with a problem | high |
| Setup problem | something in Settings needs fixing | normal |

Each event comes once per job (a TV disc's episodes are one notification, with a count), with a link to the job. Rips from remote workers come back to the controller, which transcodes, copies and notifies, so they work the same way. Settings saved before an event existed give it its default.

## Drives

- **Drive ids.** They come from the drive's vendor, model and serial number, read from the drive. Its history, settings, name and MakeMKV data stay with it on another port, another `/dev/srN`, or another machine. A drive that reports no serial falls back to an id from its port (logged at start-up).
- **Names.** The pencil next to a drive's name (on its card, or in Settings) gives it a name such as "Top bay", used everywhere instead of the model.
- **Statistics.** The Overview page shows all-time statistics per drive and the free space on each disk ripshelf writes to.

## Architecture

ripshelf is one service. A controller (Python, FastAPI and SQLite) serves a SvelteKit web UI, and an embedded worker runs MakeMKV on the local drives. Workers can also run [one per drive](modes.md) or on [other machines](workers.md), and the controller keeps the UI, the database, identification, transcoding and the library.
