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

**Trimming.** The scan covers the whole glass, so ripshelf finds the picture on it and leaves the empty glass out of the thumbnails and covers. **Untrim** shows the whole scan again, and **Cut away the empty glass around a scan** in Settings turns this off. The original file is always kept as the scanner sent it.

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

In development, `scripts/dev-fake.sh` serves a pretend scanner at `/fake-escl/eSCL` and points Settings at it. Its glass holds a made-up DVD's cover sheet, and its feeder holds three insert pages.
