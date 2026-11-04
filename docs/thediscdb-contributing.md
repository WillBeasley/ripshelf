# Contributing discs to TheDiscDB

ripshelf looks discs up on [TheDiscDB](https://thediscdb.com) (see [providers.md](providers.md)). TheDiscDB only knows discs that someone has added. If you turn this option on, ripshelf sends the discs you have confirmed, and that TheDiscDB doesn't know yet, so other people's rips recognise them.

This is **off by default**. Nothing is sent until you turn it on.

## Turning it on

It's under Settings → Metadata providers → **Contribute to TheDiscDB**.

| Setting | |
|---|---|
| Contribute confirmed discs | **Off** (default), **Ask** (a *Send to TheDiscDB* button on each job), or **Automatically, once I confirm a disc** |
| TheDiscDB address | `https://thediscdb.com`. Change it only to point at a test server |
| API key | Optional. TheDiscDB doesn't need one at the moment. If you set one, it is sent as `Authorization: ApiKey <key>`. Like other keys, it is stored but never shown again |

In automatic mode, ripshelf only sends discs **you** confirmed (on Review, or from the job), and only once you have named their extras (below). Discs that ripshelf confirmed by itself, or recognised from its own cache, are not sent on their own. Their job has a Send button, so you can check them and send them yourself.

## Which discs can be sent

- You confirmed what the disc is, either a film with its TMDB id or a TV disc with its episodes mapped.
- TheDiscDB doesn't already know the disc. If TheDiscDB identified it, the job says "Already in TheDiscDB" and there is nothing to send.
- It is a DVD, Blu-ray or UHD Blu-ray, and ripshelf read its content hash (from the sizes of the disc's video files) and has its MakeMKV scan. A Blu-ray job without one gets it from the clip info files kept with the job. A DVD job without one needs the disc inserted again.
- You have named its extras, or said it has none (below). Nothing is sent before.

## Naming the extras

TheDiscDB makes one item for each title ripshelf lists, named after what ripshelf sends with it. A disc full of `00800.mpls`-style names, or of menus, logos and duplicate playlists, gets sent back. So before a disc can be sent, its TheDiscDB panel lists the disc's titles (number, length, size, playlist and chapters):

- **The film** goes under its confirmed title (for example *Pulp Fiction*). On a TV disc each **episode** goes under its episode name from TMDB (or *Episode N* if TMDB has none).
- **Every other title** is left out unless you pick a type for it and give it a name, such as *Behind the Scenes*. The types are TheDiscDB's own: *Extra*, *Featurette*, *Deleted scene*, *Trailer*, *Interview*, *Scene*, *Music*, *Short* and *Other*. All but *Deleted scene* and *Trailer* count as extras when TheDiscDB identifies discs. On TV discs, the titles the episode mapping made extras start picked, still waiting for names.
- **Save names** is only possible once every picked title has a name. Saving with nothing picked means the disc has no extras worth sending.

Until you save, the Send button doesn't appear, automatic mode doesn't send, and the API refuses to send. You can change the names later with **Change**. If the disc was already sent, it goes back to *not sent*, so sending it again replaces the copy TheDiscDB has.

## The job's TheDiscDB panel

Open a job to see its **TheDiscDB** section:

- **Status.** Not sent, sending, sent (with the time), or failed (with the reason and a **Retry** button). A failed send never affects the rip.
- **Titles.** The naming list above, or once it is saved, what is sent (with a **Change** link).
- **Preview what is sent.** Shows the exact JSON that will be posted, and how many scan lines go with it.
- **Release details.** The box rather than the disc: UPC/EAN, ASIN (or *Not sold on Amazon*), release date, locale and region code, and pictures of the front and back cover. See [Release details](#release-details) below.
- **Release.** Discs sent with the same release id become one release on TheDiscDB, for example a box set or the discs of a season. By default a disc joins the most recent release of the same title (and the same season and disc type) from the last 7 days. TV discs keep the disc number their label gives, and other discs are numbered in the order they were sent. **Start a new release instead** gives the disc a release of its own.
- **Finish on TheDiscDB →.** This appears once the disc is sent. Sending doesn't publish anything. On TheDiscDB's site, signed in with GitHub, you fill in its form and submit the release for review. Under the button, ripshelf lists the release details its form asks for, each with a Copy button.

## Release details

TheDiscDB's form asks for the release date, ASIN (or "not available"), UPC, locale and region code, and takes a front and back cover. Its import only reads the **UPC** (which fills in the form for you) and the **covers** (which become the contribution's covers). The rest you type or paste into the form, so ripshelf keeps them for you to copy.

A disc that joins a release takes the details and covers of the release's other discs, unless it has its own.

**Look up** suggests values, and saves nothing until you press **Save details**:

| Value | Where it comes from |
|---|---|
| UPC and ASIN | [UPCitemdb](https://www.upcitemdb.com)'s free API (no key, about 100 lookups a day). With a barcode typed in, or a disc whose volume label is a barcode, it looks the barcode up. Without one, it searches listings by title and format, and lists the likely ones, each with **Use this**. Check the barcode on the box: one film has many releases. |
| ASIN, if UPCitemdb has none | A web search of Amazon's site for the locale (amazon.co.uk for en-GB), through your SearXNG, if catalogue lookups use SearXNG (Settings → Identification). |
| Release date | TMDB's physical release dates for the locale's country, preferring one whose note names the disc's format. Films only, because TMDB has no disc dates for TV. |
| Locale | The barcode's GS1 country prefix (50 is the UK, 00 to 13 the US and Canada), otherwise your TMDB language. |
| Region code | DVDs: read from the disc (VIDEO_TS.IFO), such as `2` or `2,4`. Blu-rays: the usual region for the locale's country (A, B or C), so check the box. UHD Blu-rays: `Region free`. |

Only the barcode, or the title and format, goes to UPCitemdb and the search. Nothing is looked up unless you press **Look up**.

**Covers** are a photo or scan of this release's box, as JPEG, PNG or WebP of up to 20 MB, not the film's poster. Add them from a file, or use a listing's picture with **Use its picture as the front cover** (downloaded by ripshelf from public https addresses only). They're sent after the disc and scan.

Changing the details or covers of a disc that was sent marks it *not sent*, so sending it again replaces what TheDiscDB has.

### What you still do on TheDiscDB's site

- Paste the release date, ASIN, locale and region code into its form (they're listed under **Finish on TheDiscDB →**).
- After creating the contribution, add **audio track names** (such as commentaries), **subtitle names** and **chapter names** on its edit page. TheDiscDB's import has no fields for them, so ripshelf can't send them.

Sending the same disc again replaces the earlier copy, because TheDiscDB keys discs by their content hash.

## What is sent

ripshelf uses TheDiscDB's import API. Up to four requests are made, in this order:

1. `POST {address}/api/engram/disc` (JSON), with these parts.
   - `disc` has the content hash (MD5 of the video files' sizes, the same hash used for lookups), the volume label, the disc type (`dvd`, `blu-ray`, `4k`), the disc number and the release id (a random UUID).
   - `identification` has the TMDB id, the title, the season (TV), and how sure the identification is (1.0 for discs you confirmed).
   - `titles` lists only the film or episodes and the extras you named, each with its type (`MainMovie`, `Episode` with season and episode, or one of the extra types: `Extra`, `Featurette`, `DeletedScene`, `Trailer`, `Interview`, `Scene`, `Music`, `Short`, `Other`), length, size, chapter count and segment map. The name goes in `source_filename`, because that is the field TheDiscDB names the item after. TheDiscDB matches each one to the scan by its segment map, and skips the scan's other titles. The playlists still reach it in the scan log.
   - `upc` (if you gave one). `asin` and `release_date` go too, as Engram sends them, though TheDiscDB doesn't read them yet.
   - `engram_version: "ripshelf/<version>"`, `export_version`, `exported_at` and `contribution_tier` describe the export.
2. `POST {address}/api/engram/disc/{content_hash}/logs/scan` (plain text) sends MakeMKV's scan of the disc, which TheDiscDB uses to build the title list. Only the disc, title and stream lines are sent (`CINFO`, `TINFO`, `SINFO`, `TCOUNT`).
3. `POST {address}/api/engram/release/{release_id}/images/front` and `.../back` (multipart, a `file` field) send the covers you added, if any.

## Privacy

ripshelf never sends anything about your system.

- MakeMKV's drive lines (`DRV`) are left out of the scan. They contain the drive model, serial number and device path.
- MakeMKV's messages (`MSG`) are left out too. They can contain folder paths, drive names and the LibreDrive id.
- No file paths, host names, user names, drive ids or library locations are sent.

Use **Preview** to check exactly what will be sent before you send it.

TheDiscDB's terms apply to whatever you submit on its site.

## Turning it off

Set **Contribute confirmed discs** back to **Off**. Nothing more is sent, and the job panels disappear. A disc you have already sent stays in TheDiscDB's queue until you finish or abandon its release on the site.
