# Metadata providers

ripshelf identifies discs with metadata providers, which are online databases of discs, films and shows. Settings → Metadata providers has one list per kind of lookup, as Emby has per library type.

| List | What it is for | How the list is used |
|---|---|---|
| **Films** | which film a disc is | TheDiscDB first, if the disc is in it. Then every ticked search runs, and their results are merged. A film that several providers find is one candidate, and it says which providers found it. |
| **TV shows** | which show a TV disc is | Asked in order until one finds the show. Later providers are fallbacks. |
| **TV episodes** | a season's episode list | The first provider that has the season wins. If none of them has it, TMDB's list is used. |

Drag a provider by its handle to reorder a list. On a phone, or from the keyboard, use the ↑ ↓ buttons, or focus the handle and press the arrow keys. Untick a provider to leave it out of that list. A provider that needs a key is skipped until the key is set.

Keys are shared by every list a provider is in. They go under the lists, each with a **Test connection** button that tries the key as typed, before you save. On the Review page, every candidate says which providers found it, for example "via TMDB · OMDb".

## The providers

| Provider | Key | Lists | Used for |
|---|---|---|---|
| **TheDiscDB** | none | Films, TV shows | Recognises the disc itself, and says which title is the film or which episode |
| **TMDB** | free | all three | Search, runtimes, posters. ripshelf names films and shows after TMDB |
| **TheTVDB** | free for personal use, or a subscriber PIN | TV shows, TV episodes | Finds shows TMDB's search misses. Episode lists in aired or DVD order |
| **TVmaze** | none | TV shows, TV episodes | Show search and episode lists when there is no TheTVDB key |
| **OMDb** | free (1,000 requests a day) | Films | A second film search (IMDb's titles, years and runtimes), and a cross-check of TMDB's results |

### TheDiscDB

[thediscdb.com](https://thediscdb.com) is a community database of discs. Each disc in it records which MakeMKV title is the main film, which titles are which episodes, and which are extras.

ripshelf finds a disc by two fingerprints. Both are read in seconds, from the small files it already reads before the scan.

- The **Disc ID**.
  - On a Blu-ray or UHD it's the SHA-1 of `AACS/Unit_Key_RO.inf`, the AACS Disc ID.
  - On a DVD it's libdvdread's disc id, an MD5 of the IFO files.
- The **content hash**, an MD5 of the sizes of the disc's video files (`BDMV/STREAM/*.m2ts`, or everything in `VIDEO_TS`). 7-Zip lists the sizes without reading the files.

When the disc is found, this is what happens.

- **Film or TV.** TheDiscDB's record decides whether the disc is a film or TV.
- **Films.**
  - The film it names becomes the top candidate, at a score of 0.95. That is above the default auto-confirm line.
  - If TheDiscDB marks a different title as the main film than ripshelf's own pick (some discs hide the film among decoy playlists), ripshelf rips TheDiscDB's title.
- **TV.**
  - The show and the season come from the record.
  - Each title is mapped to the episode TheDiscDB gives it. Titles it lists as extras stop being episodes.
  - With TV auto-confirm on, such a disc is confirmed without review, unless the play-all order check disagrees.

TheDiscDB only knows discs someone has added. The job log says "TheDiscDB doesn't know this disc yet" when it has no record. ripshelf can send the discs you confirm to TheDiscDB. This is opt-in (see [thediscdb-contributing.md](thediscdb-contributing.md)).

TheDiscDB numbers episodes as its contributors entered them, usually in aired order. With TheTVDB's DVD order chosen, check the mapping on Review.

### TMDB

Get a free key from [themoviedb.org](https://www.themoviedb.org/settings/api). Use the **v3 API key**, not the read access token.

Everything ripshelf files is identified by TMDB.

- Films are named from TMDB's record.
- TV collections are keyed by TMDB's show id.
- Shows that TheTVDB or TVmaze find are looked up on TMDB through their TheTVDB or IMDb ids.

So TV needs a TMDB key even with TMDB lower in the TV lists. A film found only by OMDb, with no TMDB key, is named from OMDb's title and year.

### TheTVDB

Get a key at [thetvdb.com](https://thetvdb.com/api-information). A user-supported key also needs your subscriber PIN.

The **Episode order** setting (aired or DVD) picks which of TheTVDB's lists is used. DVD order follows the discs, but Emby numbers episodes in aired order unless its TV library is set to DVD order. For a season with no DVD order, aired order is used.

### TVmaze

[TVmaze](https://www.tvmaze.com/api) needs no key. Its shows carry TheTVDB and IMDb ids, and ripshelf uses those to look a TVmaze show up on TMDB, and to find a TMDB show on TVmaze for its episodes.

TVmaze lists episodes in aired order and has no season 0.

### OMDb

Get a free key at [omdbapi.com](https://www.omdbapi.com/apikey.aspx). The key arrives by email and must be activated from the link in it.

OMDb starts unticked in the Films list. Tick it once the key is set.

OMDb's hits that TMDB's search missed are looked up on TMDB by IMDb id. If TMDB has them, they get TMDB's details.

## Not included

- **Artwork-only providers** (Fanart.tv) and **Trakt**, because they don't help identify a disc.
- **MusicBrainz**, because ripshelf doesn't rip audio CDs.
