# Cover scans

A disc's box often says more than the disc does. The back lists the extras, the inside of the sleeve lists the episodes or chapters, and the front shows which edition it is. ripshelf keeps pictures of the box with each job. They come from a network scanner, an uploaded picture, or a phone's camera.

**Where to add them:**
- **Overview:** each drive with a disc in has **Add a cover scan**, which opens the job straight at its scans. That works while the disc is still being scanned, so the box can help identify it.
- **Any job:** in the job's panel, **Cover scans** is near the top, and it's there for every job, from the moment a disc is detected.
- **Review cards:** at the bottom.
- **Long before the disc goes in:** the **Covers** page keeps a shelf of case sheets scanned ahead of their discs, a whole stack at a time. See [The covers shelf](#the-covers-shelf).
- **Before the disc goes in:** a drive with no disc has **Add a scan for the next disc**. The pictures wait there, for up to a day, and the next disc put in that drive takes them as soon as it's detected. They're read while MakeMKV scans the disc, in time to help identify it. Pictures can be removed before then, and are forgotten after a day if no disc comes. Each picture is labelled with what it shows:

| Kind | What it is |
|---|---|
| Front cover, Back cover | one side of the box |
| Whole outside | the outer sheet unfolded: back, spine and front side by side |
| Inside of the cover | the sheet's inner face, where the contents are often printed |
| Insert page | booklet or leaflet pages, numbered in order |
| Other | anything else (a slipcover, a steelbook's inside) |

You can change a picture's kind after adding it, turn it a quarter at a time (⟳) and remove it. Click a thumbnail to see it larger, or to open the original file.

## What's on the disc

ripshelf reads the text on each scan and lists what the box says is on the disc, under the scans in **What's on the disc**:

- **Extras.** These are the items under a heading such as "Special features", "Bonus material" or "Extras", with:
  - their running times;
  - a type, TheDiscDB's (Featurette, Deleted scene, Trailer, Interview, Music, Short and so on), guessed from their words;
  - a name. When an item quotes a title ("In-depth documentary on the making of the film 'Return to the Orchard'"), the quoted title is its name.
- **Episodes.** These are the items under an "Episodes" or "Disc 2" heading, or a column of short titles (a children's DVD's ten episode names), with their numbers and running times where printed.
- **About the release.** Where printed:
  - the title, the year and the top billing;
  - the running time, and an episode count ("Contains 10 wide-eyed adventures");
  - the barcode, decoded from the bars or read from its digits, with the check digit verified;
  - the catalogue number, region, PAL or NTSC, country and aspect ratio;
  - the audio tracks, each with its format (Dolby Digital, DTS-HD MA, Atmos…) and channels;
  - the subtitles, flagging hard of hearing ones;
  - any 4K, HDR or Dolby Vision claims.

Pointing at (or tapping into) a row marks where on the scan it was read. Each row can be edited, removed or added to. **Save listing** keeps your version. Reading the scans again (when one is added, changed or turned, or with **Read again**) never replaces it. If a new reading finds something different, the panel asks whether to use it or keep yours.

In the TheDiscDB panel, **From cover scan** fills the release details' empty fields with the barcode, region and locale read here. Nothing is saved until you press **Save details**.

Reading takes a few seconds a picture, one job at a time.

### How the reading works

[Tesseract](https://github.com/tesseract-ocr/tesseract) reads the text, and [zbar](https://github.com/mchehab/zbar) the barcode. For each picture:

1. **Size.** It is scaled to about 300 dpi.
2. **Orientation.** It is turned the right way up if it was scanned on its side. The scan's thumbnail turns with it. A whole outer sheet (much wider than tall, or with a spine down it) is only ever turned to lie flat, never stood on its end; a front or back on its own is turned whichever way its text runs.
3. **Parts.** A whole outer sheet is read as its back, spine and front, cut where the spine is. **Cut into front and back covers…** moves the cut.
4. **Two readings.** Each part is read as it is (best for flatbed scans) and with local contrast (best for phone photos, with glare and coloured stripes behind the text). The fuller reading of each line is kept.

What it reads well:
- printed text on a flatbed scan
- a phone photo taken flat and in focus

What it reads badly, or not at all:
- stylised titles and text over pictures (the front cover's title is usually read from the spine instead)
- curved or angled photos
- anything printed as an icon, such as a region globe or a PAL logo (the region is then guessed from "PAL" and "U.K." where those are printed, and marked as guessed)

Check the listing against the box, as you would a TheDiscDB submission.

**Settings → Scanner and covers** has a switch for reading, Tesseract's languages (`eng`, or `eng+fra` and so on; each needs its data installed) and **Test text reading**.

The container image includes Tesseract and zbar. Elsewhere:
- Debian or Ubuntu: `apt install tesseract-ocr tesseract-ocr-eng tesseract-ocr-osd libzbar0`
- Windows: `winget install -e --id UB-Mannheim.TesseractOCR`. ripshelf finds it in Program Files. pyzbar brings zbar with it, but zbar needs the Visual C++ 2013 runtime: `winget install -e --id Microsoft.VCRedist.2013.x64`.

Without Tesseract, scans are kept as before, and the panel says text reading isn't available, so you can type the listing in yourself.

### A vision model as a second reader

Tesseract reads printed text well but misses some things:
- what's printed as an icon (the region in its globe, a PAL logo)
- small print
- stylised titles
- text in photos taken at an angle

A vision model, a language model that reads pictures, gets most of these. ripshelf can ask one to read each scan as well, and merges what it reads with Tesseract's reading.

**When it runs** is set in Settings → Scanner and covers → Vision model:
- **Never** (the default).
- **Only when Tesseract struggles.** This is the recommended choice. It applies to scans of the back, the whole sheet, its inside or an insert, when Tesseract:
  - read only a few lines with confidence, or wasn't confident;
  - found no episodes or extras;
  - read neither the region nor the video system;
  
  and also to every phone photo.
- **Every scan.**
- **Only when you press Read with the vision model** on a scan. The button is there in every mode except Never.

**Where it runs:**
- **On your network:** Ollama, or any other server with an OpenAI-compatible API (LM Studio, vLLM…). Set its address; the model is `qwen3-vl:4b-instruct` unless you name another.
- **A cloud service with an OpenAI-compatible API:** its address, the model and a key.
- **Anthropic's API:** a key. The model is `claude-opus-5-5` unless you name another from Anthropic's model list (a smaller one, such as `claude-haiku-5-5`, costs less a scan).

Left blank, the model is the default for where it runs; a hosted OpenAI-compatible service has none, so name one of its models. Switching where it runs clears a model name that was the old one's default.

With a cloud service, the pictures of the box leave your network. Settings says so, and only the scans are sent.

**Test the vision model** sends a small made-up back cover and checks that the trailer on it is read.

**How the two readings are combined:**
- **Barcode:** always comes from the bars when zbar decoded them.
- **Running times:** come from Tesseract when it read them. Every model tried got "2Hrs 45mins" wrong.
- **Other details:** each one comes from Tesseract when it read it, and from the model otherwise. A region Tesseract only guessed from "PAL" and "U.K." gives way to one the model read off the globe.
- **Items both found** are one item, marked **both**. Tesseract's place on the scan is kept, and the model's type is used where Tesseract could only say "Extra".
- **An item only the model found** is added. If every word of its title is in a line Tesseract read, it's marked **both** and highlighted there. Otherwise it's marked **vision model** and flagged "Only the vision model read this", because models sometimes invent lines.
- **Your edits** are marked **you**. A new reading never replaces them: the panel asks, as with Tesseract.

Details the model supplied are labelled "vision model" in the listing, and the decoded barcode "from the bars".

**Running it with Ollama** (no cloud):
1. Add Ollama next to ripshelf. The example [compose.yaml](../compose.yaml) has an `ollama` service to uncomment: the two containers share the compose file's network, so Ollama needs no published port. Keep it that way, or publish it on `127.0.0.1` only. Ollama has no login, so don't put it on the internet.
2. Pull the model once:
   ```sh
   docker compose exec ollama ollama pull qwen3-vl:4b-instruct
   ```
3. In Settings → Scanner and covers → Vision model, choose **On my network**. Address `http://ollama:11434`, model `qwen3-vl:4b-instruct`.

If Ollama already runs elsewhere (another compose stack, another machine), use its address instead. For another compose stack, either join both to one docker network, or publish Ollama's port on the LAN address and use `http://<host>:11434`.

**Which model:**
- **`qwen3-vl:4b-instruct`** is the default: 3.3 GB, about 5 GB of memory while it reads, and a minute or two a picture on a desktop CPU. In a trial on real boxes:
  - It read every episode on a phone photo of a children's DVD, with its running time, regions and PAL.
  - On a flatbed scan of a film's sheet, it read the extras, the barcode, the catalogue number and the region in its globe.
  - It invented nothing.
- **`qwen2.5vl:7b`** reads extras' types and audio tracks more carefully, at about twice the time and memory.
- **Avoid "thinking" models** (such as plain `qwen3-vl:4b`). They think until they run out of room and answer nothing; ripshelf says so when it happens.

**Speed and load.**
- A local model keeps most of the CPU busy while it reads. ripshelf reads one scan at a time across all jobs, and a local model waits while a transcode runs.
- On Intel integrated graphics (a UHD 770), Ollama's Vulkan backend found the GPU, but requests failed or ran far slower than on the CPU. Plan on the CPU.
- A cloud model takes seconds and leaves the CPU alone.

### The box helps identify the disc

What a scan reads is used to identify the disc as well as to list what's on it:
- **Film title:** the title read off the box (usually from the spine) is searched for first, ahead of the disc's label, and the year printed on it counts.
- **Barcode:** asked of these in turn until one names it, and that name is searched for too:
  1. [TheDiscDB](https://thediscdb.com), whose search knows each release's UPC, when TheDiscDB is ticked in Settings → Identify. Its name is the film's or show's own.
  2. [UPCitemdb](https://www.upcitemdb.com) (free, about 100 lookups a day). Good on US UPCs, thin on UK and European EANs.
  3. A paid service, if one is chosen with its key in Settings → Identify → Barcodes on covers: [Barcode Lookup](https://www.barcodelookup.com/api) or [EAN-Search](https://www.ean-search.org/ean-database-api.html).
  4. Your SearXNG, when catalogue lookup uses it: shop pages that print the barcode, and the title at least two of them agree on.

  The answer is kept with the job and the barcode, so each barcode is asked once. If a source failed (offline, rate limited, the day's lookups used up) and nothing was found, it is asked again the next time the disc is identified. Only the barcode leaves ripshelf.
- **Evidence:** a candidate found this way says so ("From the cover scan: the cover scan reads “…”").
- **TV:** a disc whose label says nothing searches for the show the spine names.
- **TV episodes:** when a TV disc waits for review, the shows it could be (the candidates, the box's title and the barcode's product) are checked against the episode names the box lists. A show that has at least three in five of them is proposed instead, at the season most of them are in. Names match past a prefix the box or the show puts on every one ("Pat's Special Delivery: …", "Postman Pat and …") and a leading "A" or "The".

A disc TheDiscDB recognises still goes by TheDiscDB. A box never overrides a confident match.

If a scan is read after identification, while the job waits for review with nothing confirmed, the disc is identified again with what the box says. That happens once for each new reading of the title or barcode.

A film's extras the box names are ripped with the film when the scan was read before the rip started. Those named by a scan read during the rip are ripped straight after the film, before the disc is ejected.

## Matching the listing to the disc

ripshelf matches each line of the listing to one of the disc's titles. **Matched from the cover scan** shows each title with the line it was matched to, a confidence (high, medium or low) and, when you tap it, why: "runs 22:06 against 22 min printed", or "“Brolly Bus” is S03E34; listed 1st, title 1 is 1st".

What decides a match:
- **Episode names.** A name found in the season's episode list (from TMDB, TheTVDB or TVmaze) gives its SxxEyy, wherever it is on the disc. "Part 1" and "Pt. One" are the same.
- **Runtimes.** Printed runtimes are rounded, so within about 10% or 2 minutes counts as agreeing, and PAL's 4% speed-up is allowed for. An extra with no printed runtime is judged by what its type usually runs to: a trailer a couple of minutes, a featurette twenty.
- **Order.** Boxes usually list things in disc order. This only tips the balance, as some discs scramble their playlists, but it is all that tells alike titles apart, such as the parts of [one long title split into episodes](tv-splitting.md): those are matched as titles of their own, in the disc's order. When the box lists more or fewer than the disc has, places are counted from the start of the list, and a match that only the order on the box placed is never high.
- **The main feature.** For a film, it is picked from the long titles by the printed running time, the printed audio languages, and a commentary track.
- **What ripshelf already thinks.** If the TV mapping or the film's main title agrees, the match is more sure. If it disagrees, the match is shown with what ripshelf has instead, for you to settle.
- **Box sets.** Discs of one TheDiscDB release share the set's listing. A disc only takes the lines that no other disc of the set took, and only lines printed for its own disc number.

The play-all, duplicates and a Blu-ray's loose clips are left out. Lines and titles nothing matched are listed underneath.

How the matches are used:
- **TV review.** Each title the box places (high or medium) gets that episode, and the card starts with a summary: "Back cover scanned · 10 names read. 8 titles placed from the box. 2 need you to pick." A title the box placed only with low confidence, or whose name matches no episode, says what the box calls it ("Box says “Lost Hat”") with its closest episodes by name and running time, to tap. When every episode the box lists is placed (or the box lists more than the disc holds and every episode title is placed), a title still mapped as an episode becomes an extra. Titles you set by hand are never changed by the box, and a show you picked is kept. A mapping set from the scan is never confirmed on its own: you press Confirm.
- **TheDiscDB.** The naming list for extras starts from the names and types the scan gives.
- **The library.** Matched extras are filed by name (below). Low matches aren't used for names.

## Extras in the library

An extra the cover scan names (matched with high or medium confidence) is filed under its name, in Emby's extras folder for its type:

| TheDiscDB type | Folder |
|---|---|
| Featurette | `featurettes`, or `behind the scenes` when its name says making-of, behind the scenes or on set |
| Deleted scene | `deleted scenes` |
| Trailer | `trailers` |
| Interview | `interviews` |
| Scene | `scenes` |
| Short | `shorts` |
| Extra, Music, Other | `extras` |

These are the extras folders Emby's naming guides list ([films](https://emby.media/support/articles/Movie-Naming.html), [TV](https://emby.media/support/articles/TV-Naming.html)): extras, specials, shorts, scenes, featurettes, behind the scenes, deleted scenes, interviews and trailers. Jellyfin and Plex read them too.

```text
Movies/Moonlit Orchard (1998)/featurettes/Growing the Orchard.mkv
Movies/Moonlit Orchard (1998)/trailers/Theatrical trailer.mkv
TV/Pippa & Goose (2013)/behind the scenes/Making the Kite Tram.mkv
```

- **Films.** Films didn't have their extras filed before. Now a film's extras are ripped with it when the box was scanned before the rip started. Otherwise, **Rip the N extras it names** in the matches rips them while the disc is still in its drive. They go into the library with the film, or straight away if the film is already there. Extras are copied as ripped: transcoding is for the film only.
- **TV.** A TV disc's extras go in these folders under the show, instead of `extras/<disc label> - title N.mkv`. Extras the box doesn't name are filed as before.
- **Nothing is replaced.** A file already there gets a new one beside it, with " (2)" added.

### Bonus episodes of other shows

Some boxes carry an episode of a different show ("Includes Bonus Episode", "Plus meet Jess in an all new series", with the episode's title in quotes). The reading notes it as an extra with that show's name, the vision model is asked for it too, and on a TV disc waiting for review the episode is looked up in that show's episode list. Its runtime then picks its title, which is ripped whatever "Rip TV extras by default" says, and filed:

```text
TV/Postman Pat - Special Delivery Service (2008)/extras/Guess with Jess - Where's My Feather Gone.mkv
```

or, with **Bonus episodes of other shows** set to the other show's own folder:

```text
TV/Guess with Jess (2009)/Season 01/Guess with Jess - S01E24 - Where's My Feather Gone.mkv
```

TheDiscDB gets it as an extra named "Guess with Jess - Where's My Feather Gone?".

In Settings → Library and filing:
- **File named extras into Emby's folders** turns all this off.
- **Bonus episodes of other shows**: an extra of this show (the default), or an episode in the other show's own folder.
- **A film's other titles** can rip the titles the box doesn't name into `extras/` as "Title N". This is off by default, as before.

## TheDiscDB covers

When the disc is being sent to [TheDiscDB](thediscdb-contributing.md), a front or back scan also becomes that cover. You don't need to upload it again in the TheDiscDB panel.

A whole outside scan is cut into both covers at its spine, which ripshelf finds by itself once the sheet is the right way up: two edges running down the sheet near its middle, about as far apart as a DVD's spine (14 mm of a 273 mm sheet) or a Blu-ray's (11.5 mm of 275 mm), whichever the sheet's shape says it is. The scan then says **Auto-cut at its spine**, and **Adjust the cut…** opens the cut editor on it. When the spine isn't clear, the sheet isn't cut on a guess: choose **Cut into front and back covers…**, then set:
- where the spine is
- how wide the spine is
- **The front is on the left**, for the occasional sheet printed the other way round

The outline on the picture shows which part becomes which cover. Pictures added while the disc waits for review become its covers once you confirm what the disc is, unless it already has its own.

The inside of the cover and insert pages are never used as covers. They're kept for reading what's printed on them.

## The covers shelf

The **Covers** page holds sheets from your cases, scanned ahead of their discs: scan a whole stack while the drives are busy with something else, and each disc finds its sheet when it goes in. The shelf is kept in `data/covers` until a cover is used or deleted, up to 500 covers.

**A cover** is one case sheet with up to two sides: its **outside** (back, spine and front, unfolded) and its **inside**. When it's used for a disc, these become that job's *Whole outside* and *Inside of the cover* scans.

### Scanning a stack

**Scan a stack** feeds the stack through the scanner's feeder. Choose how:

- **Both sides at once**: a feeder with duplex scans each sheet's two sides one after the other.
- **Two passes (turn the stack over)**: for a feeder without duplex. The first pass scans one side of every sheet. The page then says **Now turn the stack over and scan the other side**: put the stack back face down, in the same order, and press **Scan the other side**. The turned stack comes out last sheet first, so the first pass's first page pairs with the second pass's last page. **Cancel and discard** lets go of the first pass.
- **One side only**: each sheet makes a cover with only its outside.

The covers appear as the pages arrive (with two passes, once the second pass is in).

**When the passes don't pair up.** If the second pass has more or fewer pages than the first (a sheet fed twice, two fed together, one left behind), nothing is paired, so one slip can't put every later inside with the wrong outside. Both passes are shown side by side, each page with its sheet number. **Leave out** the extra pages until both have as many, then **Make the covers**, or **Scan the second pass again**.

The scanner scans for one thing at a time: a stack can't start while it scans for a job, and the other way round.

### Uploading

**Upload pictures or PDFs** takes many pictures at once, and PDFs, one page each (drawn at 300 dpi). With **Both sides of each sheet** ticked (the default), the pages pair up:

- **Each sheet's sides one after the other**: outside, inside, outside, inside... as a duplex scan gives them. An odd number of pages is refused rather than guessed at.
- **All the outsides, then all the insides (turned stack)**: two passes. Either one PDF holding both passes back to back (its first half, then its second half, last sheet first), or the outsides in the upload and the insides in **Insides in a separate file…**, in the order the turned stack came out. Passes that don't pair up are held as above.

Untick it for one cover a page.

### Blank sides, and which is the outside

- **Blank sides** are left out: a side that's one even colour, or paper with hardly a mark on it once the glass is trimmed off (dust doesn't count; a short chapter list in small print does). A single-sided sheet makes a cover with only an outside, and a sheet blank on both sides is skipped.
- **The outside** is the side zbar finds a barcode on, or else the side with a spine down it. When neither tells them apart, the first side scanned is the outside. **Swap sides** on a cover puts it right, and reads it again.
- The same sheet added twice (the same outside file) is skipped.

### Reading

Each cover is read in the background as it arrives, one at a time and taking turns with the jobs' readings: Tesseract and zbar on both sides, the vision model when Settings → Scanner and covers say (**For every scan**, or **When Tesseract struggles**, which here means the title and year weren't both read), and the barcode lookups (as in [The box helps identify the disc](#the-box-helps-identify-the-disc)). A cover shows its title and year, its barcode and what the barcode names, and DVD, Blu-ray or 4K when the cover says. The search box searches all of these.

Open a cover to see each side large, and to **Read again**, **Read with the vision model** (now, whatever the setting says, in the same one-at-a-time queue), **Swap sides** or **Delete** it. **⟲**, **180°** and **⟳** under the picture turn the side shown, saved straight away.

The list beside **Read with the vision model** picks the model to read it with, this once, without changing Settings: Anthropic's Haiku, Sonnet and Opus, or the models your server lists (Ollama's, say). **Another…** takes any model name, for when the list couldn't be fetched. To compare models on a cover, read it with one, note what it found, then read it with the next.

**Deleting several.** Press and hold a cover (or Ctrl-click it, or Cmd-click on a Mac) to start picking. Tap or click more to pick them or leave them out, Shift-click to pick everything between, or **Select all**. **Delete selected** deletes them all, after asking; **Cancel** (or Esc) stops picking.

### Editing a cover

**Edit…** on an open cover edits the side shown, when the scanner or a phone got it wrong:

- **⟲** and **⟳** turn it a quarter, and **Flip ↔** and **Flip ↕** mirror it (a sheet scanned through the case's sleeve from behind, or a photo from a front camera).
- **Straighten** levels a sheet laid askew, up to 15° either way. The picture turns within its own frame, so its corners turn out of it (shown white): crop them off.
- **Crop** with the box over the picture: drag its edges or corners, or use the **Left**, **Right**, **Top** and **Bottom** sliders.

**Save** keeps it all; **Cancel** (or closing the cover) keeps none of it. The original file is never changed: the edits are kept beside it, so **As it came** undoes them all, back to the trim ripshelf found. Once a side is edited, the cover is read again as it now shows (Tesseract, zbar and the barcode lookups). The vision model isn't asked again: its last reading of the cover is kept, and **Read with the vision model** reads the edited cover when you want it to. The reading no longer turns it the way its text runs, so it stays as you set it.

When an edited cover is used on a job or for a drive's next disc, the edited sides go as you see them (a JPEG of each), and the sides you didn't edit go as they were scanned.

### Using a cover

- **On a job.** The job's panel and its Review card say **This cover looks like it's on the shelf: …** with the cover's thumbnail, when one fits the disc. **Use it** makes the cover's sides the job's scans: they're read, used to identify the disc, and filed with it like any other scan, and the cover leaves the shelf. **Not this one** hides the suggestion. **Pick a cover from the shelf** opens every cover, best match first, with a search box.
- **For a drive's next disc.** Open the cover on the Covers page, choose the drive and **Use for this drive's next disc**. The sides wait with the drive's other pictures for its next disc, for up to a day.

Nothing is ever used without you choosing it. A cover is suggested when:

- its barcode is the job's: the barcode read off the job's own scans, the UPC in its TheDiscDB release details, or a disc label that is a barcode; or
- its title (or the name its barcode was looked up as) is close to what the disc may be: the confirmed title, the show, TheDiscDB's title, the candidates while it waits for review, a title set by hand, or the disc's label. A year that agrees helps, and one more than a year off counts against it, so a remake isn't offered for the original.

## A network scanner

Most network printers with a scanner speak eSCL, which Apple calls AirScan. ripshelf talks to it directly, so it needs no driver and nothing installed on the printer. In **Settings → Scanner and covers**:

1. Turn on **Scan from a network scanner**.
2. Type the printer's address, such as `http://192.168.1.20`. ripshelf adds `/eSCL`.
3. Press **Test the scanner**. It shows the model, what it can scan from, and its resolutions. Nothing is scanned.

Use `http://`, not `https://`. Some printers (Brother's MFCs among them) accept a secure connection and then never answer on it.

Each job then has buttons for:
- **Scan front**, **Scan back**, **Scan whole outside** and **Scan inside**, from the glass
- **Scan insert pages**, from the feeder when there is one, with **Both sides** for a two-sided feeder

The scan runs in the background, and the job shows how it's going. It also shows why a scan failed: the scanner is busy, the feeder is empty or jammed, a cover is open, or the scanner can't be reached. The scanner scans for one job at a time.

**Laying things on the glass.** Put the picture face down. The glass is A4 (or Letter), and an unfolded DVD or Blu-ray sheet (about 273 mm wide) is wider than that. Lay it sideways along the glass's long edge, then turn the scan with ⟳.

**Trimming.** The scan covers the whole glass, so ripshelf finds the picture on it and leaves the empty glass out of the thumbnails, covers and reading. The glass is any side of the scan in one even near-white or near-black colour (the lid, closed or open). Each side is cut on its own, to a small margin around the picture: the thin grey shadow often along the glass's edge and specks of dust don't count as the picture, and a pale cover's small print and the corners of a sheet laid a little askew aren't cut off. An uploaded flatbed scan is trimmed the same way, and a phone photo isn't. **Cut away the empty glass around a scan** in Settings turns this off. The original file is always kept as the scanner sent it, and that's the copy filed in the library.

**Cropping by hand.** **Crop…** under a scan shows the whole scan with a box around the part that's used. Drag its edges or corners, or move the **Left**, **Right**, **Top** and **Bottom** sliders, then **Use this crop**. The thumbnails, covers and readings then use only what's inside the box, and the scan is read again. **Trim automatically** goes back to the trim ripshelf finds, and **Whole scan** uses all of it. Photos can be cropped too. Turning the scan afterwards keeps the crop.

300 dpi is plenty for covers and for reading small print.

Scanning from the printer's own buttons into a watched folder isn't supported yet.

## Phones and uploads

**Upload image** takes JPEG, PNG or WebP pictures of up to 20 MB, several at once. Pick what they show in the box beside it first.

On a phone or tablet there is also **Take photo**, which opens the camera straight away. **Upload image** still offers the gallery, and on most phones the camera too. A photo taken on its side is turned the right way up from the phone's own orientation data. Photograph the box flat, in even light, filling the frame.

## In the library

Once a disc is filed, its pictures are copied into a `scans` folder in the film's or show's folder. Pictures added after that are copied when they're added.

```text
Movies/Moonlit Orchard (1998)/scans/outside-full.jpg
Movies/Moonlit Orchard (1998)/scans/inside-full.jpg
Movies/Moonlit Orchard (1998)/scans/insert-01.jpg
TV/Pippa & Goose (2013)/scans/PIPPA_&_GOOSE_KITE_TRAM - front.jpg
```

- **Names.** The files are copied exactly as they were scanned or uploaded. Their names are `front`, `back`, `outside-full`, `inside-full`, `insert-NN` and `other-NN`.
- **Several discs in one folder.** The names start with the disc's label for TV discs, which share the show's folder, and for a film released on several discs.
- **Nothing is replaced.** A different file with the same name stays. The new one gets " (2)", " (3)" and so on. The same file already there is left as it is.
- **Removing.** Removing a picture from the job doesn't delete its copy in the library.
- **Turning off.** **Copy scans to the library** in Settings → Scanner and covers turns the copies off.

**Emby, Jellyfin and Plex.** None of these names is an artwork name (`poster`, `folder`, `cover`, `fanart`, `backdrop`, `disc`, `logo`…), and nothing is written in the film's folder itself. So the media server's own posters and backgrounds aren't replaced. `scans` isn't one of the extras folders Emby looks in (`extras`, `specials`, `shorts`, `scenes`, `featurettes`, `behind the scenes`, `deleted scenes`, `interviews`, `trailers`). Emby ignores a folder with no video in it, so the pictures don't show up as a film or an extra.

## The API

| Request | What it does |
|---|---|
| `GET /api/jobs/{id}/scans` | the job's pictures (also in the job, as `scans`) |
| `POST /api/jobs/{id}/scans` | add a picture: a form with `file`, `kind` and `source` (`upload` or `camera`) |
| `POST /api/jobs/{id}/scans/scanner` | scan: `{"kind": "insert", "source": "feeder", "duplex": true}`. The job's `scanner` says how it's going |
| `DELETE /api/jobs/{id}/scans/scanner` | stop the scan, or clear a failed one's message |
| `GET /api/jobs/{id}/scans/{scan}` | the original file |
| `GET /api/jobs/{id}/scans/{scan}/thumb` | a trimmed, turned JPEG (`?large=true` for up to 1600 px, `?whole=true` for a large one turned but not trimmed) |
| `PATCH /api/jobs/{id}/scans/{scan}` | change `kind`, `page`, `rotate`, `trim` (`true` trims again, `false` uses the whole scan) or `crop` (`[left, top, right, bottom]` as fractions of the whole scan as shown, turned) |
| `DELETE /api/jobs/{id}/scans/{scan}` | remove the picture |
| `POST /api/jobs/{id}/scans/{scan}/covers` | use it as the TheDiscDB covers, with `{"position": 0.5, "spine": 0.05, "swap": false}` for a whole sheet |
| `GET /api/scanner` | the scanner's model and state |
| `GET /api/drives/{drive}/pending-scans` | the pictures waiting for the drive's next disc (and a scan under way for it) |
| `POST /api/drives/{drive}/pending-scans` | add one: a form with `file`, `kind` and `source` |
| `POST /api/drives/{drive}/pending-scans/scanner` | scan one from the network scanner |
| `DELETE /api/drives/{drive}/pending-scans/{scan}` | remove one |
| `GET /api/jobs/{id}/listing` | what's on the disc: the reading (`listing.found`), your version (`listing.edited`), which one counts (`current`), and whether a newer reading differs from your version |
| `PUT /api/jobs/{id}/listing` | save your version |
| `DELETE /api/jobs/{id}/listing/edits` | drop your version and go back to the reading |
| `POST /api/jobs/{id}/listing/read` | read every scan again |
| `GET /api/jobs/{id}/scan-match` | the listing matched to the disc's titles, made again from the job as it is now (also on the job, as `scan_match`) |
| `POST /api/jobs/{id}/extras/rip` | rip a film's extras the box names, while the disc is in its drive |
| `POST /api/jobs/{id}/scans/{scan}/vision` | read the scan with the vision model now (its `vision` and `vision_message` say how it goes) |
| `GET /api/jobs/{id}/scans/{scan}/text` | the lines read on one scan, with their boxes and confidence, and its barcodes |
| `GET /api/covers` | the shelf, newest first (`?q=` searches), and a stack being scanned or held |
| `POST /api/covers/upload` | add covers: a form with `files` (pictures and PDFs), `insides` (a second pass of its own), `both_sides`, `layout` (`alternate` or `passes`) and `reverse_insides` |
| `POST /api/covers/scanner` | scan a stack: `{"mode": "duplex"}`, `"two_pass"` or `"one_side"` |
| `POST /api/covers/scanner/second-pass` | scan the turned-over stack (again, if it didn't pair up) |
| `DELETE /api/covers/stack/pages/{page}` | leave a held page out |
| `POST /api/covers/stack/pair` | make the held passes into covers |
| `DELETE /api/covers/stack` | stop a stack being scanned, or let go of the pages held |
| `GET /api/covers/{cover}/{side}` | `outside` or `inside`: the original file (`?thumb=true` or `?large=true` for a trimmed, turned JPEG) |
| `PATCH /api/covers/{cover}/{side}` | edit a side by hand: `rotate`, `flip`, `straighten` (degrees clockwise, as shown), `crop` (fractions of the whole side as shown), or `{"reset": true}` to undo it all. `GET` it with `?whole=true` for the turned, flipped picture to crop on |
| `POST /api/covers/{cover}/swap` | swap the outside and the inside |
| `POST /api/covers/{cover}/read` | read it again |
| `POST /api/covers/{cover}/vision` | read it with the vision model now (its `reading.vision_state` says how it goes); `{"model": "..."}` asks another model on the same connection, this once |
| `GET /api/vision/models` | the models to pick from for that: `provider`, `current` (Settings'), `models`, and a `message` when the server couldn't list them |
| `DELETE /api/covers/{cover}` | delete it |
| `POST /api/covers/delete` | delete several: `{"ids": [...]}`; says which were `deleted` and which were `missing` |
| `POST /api/covers/{cover}/use/job/{id}` | make it the job's scans, and take it off the shelf |
| `POST /api/covers/{cover}/use/drive/{drive}` | keep it for the drive's next disc instead |
| `GET /api/jobs/{id}/shelf-suggestions` | covers that look like the job's disc, with a score and why (`?all=true` for every cover, scored) |

In development, `scripts/dev-fake.sh` serves a pretend scanner at `/fake-escl/eSCL` and points Settings at it. Its glass holds a made-up DVD's cover sheet, and its feeder holds three insert pages.


It also serves a pretend vision model at `/fake-vision` (OpenAI-compatible) and `/fake-anthropic`, and points Settings at the first. It "reads" every picture as the pretend sheet, plus one line that isn't there, as real models sometimes add.