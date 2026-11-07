# Cover scans

A disc's box often says more than the disc does. The back lists the extras, the inside of the sleeve lists the episodes or chapters, and the front shows which edition it is. ripshelf keeps pictures of the box with each job. They come from a network scanner, an uploaded picture, or a phone's camera.

Open a job (on Drives, Review or Finished) and expand **Cover scans**. Each picture is labelled with what it shows:

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
2. **Orientation.** It is turned the right way up if it was scanned on its side. The scan's thumbnail turns with it.
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

**Settings → Scanner** has a switch for reading, Tesseract's languages (`eng`, or `eng+fra` and so on; each needs its data installed) and **Test text reading**.

The container image includes Tesseract and zbar. Elsewhere:
- Debian or Ubuntu: `apt install tesseract-ocr tesseract-ocr-eng tesseract-ocr-osd libzbar0`
- Windows: `winget install -e --id UB-Mannheim.TesseractOCR`. ripshelf finds it in Program Files, and pyzbar brings zbar with it.

Without Tesseract, scans are kept as before, and the panel says text reading isn't available, so you can type the listing in yourself.

### A vision model as a second reader

Tesseract reads printed text well but misses some things:
- what's printed as an icon (the region in its globe, a PAL logo)
- small print
- stylised titles
- text in photos taken at an angle

A vision model, a language model that reads pictures, gets most of these. ripshelf can ask one to read each scan as well, and merges what it reads with Tesseract's reading.

**When it runs** is set in Settings → Scanner → Vision model:
- **Never** (the default).
- **Only when Tesseract struggles.** This is the recommended choice. It applies to scans of the back, the whole sheet, its inside or an insert, when Tesseract:
  - read only a few lines with confidence, or wasn't confident;
  - found no episodes or extras;
  - read neither the region nor the video system;
  
  and also to every phone photo.
- **Every scan.**
- **Only when you press Read with the vision model** on a scan. The button is there in every mode except Never.

**Where it runs:**
- **On your network:** Ollama, or any other server with an OpenAI-compatible API (LM Studio, vLLM…). Set its address and the model.
- **A cloud service with an OpenAI-compatible API:** its address, the model and a key.
- **Anthropic's API:** a key and the model name from Anthropic's model list.

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
3. In Settings → Scanner → Vision model, choose **On my network**. Address `http://ollama:11434`, model `qwen3-vl:4b-instruct`.

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

## Matching the listing to the disc

ripshelf matches each line of the listing to one of the disc's titles. **Matched from the cover scan** shows each title with the line it was matched to, a confidence (high, medium or low) and, when you tap it, why: "runs 22:06 against 22 min printed", or "“Brolly Bus” is S03E34; listed 1st, title 1 is 1st".

What decides a match:
- **Episode names.** A name found in the season's episode list (from TMDB, TheTVDB or TVmaze) gives its SxxEyy, wherever it is on the disc. "Part 1" and "Pt. One" are the same.
- **Runtimes.** Printed runtimes are rounded, so within about 10% or 2 minutes counts as agreeing, and PAL's 4% speed-up is allowed for. An extra with no printed runtime is judged by what its type usually runs to: a trailer a couple of minutes, a featurette twenty.
- **Order.** Boxes usually list things in disc order. This only tips the balance, as some discs scramble their playlists. When titles are alike and the box lists fewer than the disc has, the match says that only the order on the box placed it, and is never high.
- **The main feature.** For a film, it is picked from the long titles by the printed running time, the printed audio languages, and a commentary track.
- **What ripshelf already thinks.** If the TV mapping or the film's main title agrees, the match is more sure. If it disagrees, the match is shown with what ripshelf has instead, for you to settle.
- **Box sets.** Discs of one TheDiscDB release share the set's listing. A disc only takes the lines that no other disc of the set took, and only lines printed for its own disc number.

The play-all, duplicates and a Blu-ray's loose clips are left out. Lines and titles nothing matched are listed underneath.

How the matches are used:
- **TV review.** Each title shows the cover scan's pick, and **Use the cover scan's picks** fills in the high and medium ones. Nothing changes until you press Confirm, and nothing is confirmed on its own.
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

In Settings → Scanner:
- **File named extras into Emby's folders** turns all this off.
- **A film's other titles** can rip the titles the box doesn't name into `extras/` as "Title N". This is off by default, as before.

## TheDiscDB covers

When the disc is being sent to [TheDiscDB](thediscdb-contributing.md), a front or back scan also becomes that cover. You don't need to upload it again in the TheDiscDB panel.

A whole outside scan can be cut into both covers. Choose **Cut into front and back covers…**, then set:
- where the spine is
- how wide the spine is
- **The front is on the left**, for the occasional sheet printed the other way round

The outline on the picture shows which part becomes which cover. Pictures added while the disc waits for review become its covers once you confirm what the disc is, unless it already has its own.

The inside of the cover and insert pages are never used as covers. They're kept for reading what's printed on them.

## A network scanner

Most network printers with a scanner speak eSCL, which Apple calls AirScan. ripshelf talks to it directly, so it needs no driver and nothing installed on the printer. In **Settings → Scanner**:

1. Turn on **Scan from a network scanner**.
2. Type the printer's address, such as `http://192.168.1.20`. ripshelf adds `/eSCL`.
3. Press **Test the scanner**. It shows the model, what it can scan from, and its resolutions. Nothing is scanned.

Use `http://`, not `https://`. Some printers (Brother's MFCs among them) accept a secure connection and then never answer on it.

Each job then has buttons for:
- **Scan front**, **Scan back**, **Scan whole outside** and **Scan inside**, from the glass
- **Scan insert pages**, from the feeder when there is one, with **Both sides** for a two-sided feeder

The scan runs in the background, and the job shows how it's going. It also shows why a scan failed: the scanner is busy, the feeder is empty or jammed, a cover is open, or the scanner can't be reached. The scanner scans for one job at a time.

**Laying things on the glass.** Put the picture face down. The glass is A4 (or Letter), and an unfolded DVD or Blu-ray sheet (about 273 mm wide) is wider than that. Lay it sideways along the glass's long edge, then turn the scan with ⟳.

**Trimming.** The scan covers the whole glass, so ripshelf finds the picture on it and leaves the empty glass out of the thumbnails, covers and reading. The glass is any side of the scan in one even near-white or near-black colour (the lid, closed or open). An uploaded flatbed scan is trimmed the same way, and a phone photo isn't. **Untrim** shows the whole scan again, and **Cut away the empty glass around a scan** in Settings turns this off. The original file is always kept as the scanner sent it.

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
- **Turning off.** **Copy scans to the library** in Settings → Scanner turns the copies off.

**Emby, Jellyfin and Plex.** None of these names is an artwork name (`poster`, `folder`, `cover`, `fanart`, `backdrop`, `disc`, `logo`…), and nothing is written in the film's folder itself. So the media server's own posters and backgrounds aren't replaced. `scans` isn't one of the extras folders Emby looks in (`extras`, `specials`, `shorts`, `scenes`, `featurettes`, `behind the scenes`, `deleted scenes`, `interviews`, `trailers`). Emby ignores a folder with no video in it, so the pictures don't show up as a film or an extra.

## The API

| Request | What it does |
|---|---|
| `GET /api/jobs/{id}/scans` | the job's pictures (also in the job, as `scans`) |
| `POST /api/jobs/{id}/scans` | add a picture: a form with `file`, `kind` and `source` (`upload` or `camera`) |
| `POST /api/jobs/{id}/scans/scanner` | scan: `{"kind": "insert", "source": "feeder", "duplex": true}`. The job's `scanner` says how it's going |
| `DELETE /api/jobs/{id}/scans/scanner` | stop the scan, or clear a failed one's message |
| `GET /api/jobs/{id}/scans/{scan}` | the original file |
| `GET /api/jobs/{id}/scans/{scan}/thumb` | a trimmed, turned JPEG (`?large=true` for up to 1600 px) |
| `PATCH /api/jobs/{id}/scans/{scan}` | change `kind`, `page`, `rotate` or `trim` |
| `DELETE /api/jobs/{id}/scans/{scan}` | remove the picture |
| `POST /api/jobs/{id}/scans/{scan}/covers` | use it as the TheDiscDB covers, with `{"position": 0.5, "spine": 0.05, "swap": false}` for a whole sheet |
| `GET /api/scanner` | the scanner's model and state |
| `GET /api/jobs/{id}/listing` | what's on the disc: the reading (`listing.found`), your version (`listing.edited`), which one counts (`current`), and whether a newer reading differs from your version |
| `PUT /api/jobs/{id}/listing` | save your version |
| `DELETE /api/jobs/{id}/listing/edits` | drop your version and go back to the reading |
| `POST /api/jobs/{id}/listing/read` | read every scan again |
| `GET /api/jobs/{id}/scan-match` | the listing matched to the disc's titles, made again from the job as it is now (also on the job, as `scan_match`) |
| `POST /api/jobs/{id}/extras/rip` | rip a film's extras the box names, while the disc is in its drive |
| `POST /api/jobs/{id}/scans/{scan}/vision` | read the scan with the vision model now (its `vision` and `vision_message` say how it goes) |
| `GET /api/jobs/{id}/scans/{scan}/text` | the lines read on one scan, with their boxes and confidence, and its barcodes |

In development, `scripts/dev-fake.sh` serves a pretend scanner at `/fake-escl/eSCL` and points Settings at it. Its glass holds a made-up DVD's cover sheet, and its feeder holds three insert pages.


It also serves a pretend vision model at `/fake-vision` (OpenAI-compatible) and `/fake-anthropic`, and points Settings at the first. It "reads" every picture as the pretend sheet, plus one line that isn't there, as real models sometimes add.