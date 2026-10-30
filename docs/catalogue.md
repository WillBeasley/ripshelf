# Catalogue-number labels

Some DVDs are labelled with the publisher's catalogue number, not the title.
For example, `EO51710` is Entertainment One's UK DVD of *The Mortal Instruments:
City of Bones*. TMDB can't search for that, and there's no free database that
maps catalogue numbers to titles.

- dvdcompare and Blu-ray.com list releases but have no API, and their search
  isn't by catalogue number.
- Discogs has a `catno` search, but it barely covers video (EO51710 isn't in it).
- UPC databases are keyed by barcode, which isn't on the disc.

Shop listings do name the number ("Catalogue No: EO51710"), though, so a web
search for it finds the title. That's what ripshelf does.

## How it works

1. ripshelf spots a label that's just a catalogue number. That's letters then
   4 to 8 digits (`EO51710`, `OPTD2345_D1`, `CDR-30123`), or a bare number of
   6 or more digits. Abbreviations such as `BTTF2`, and years such as `1917`, aren't
   treated as catalogue numbers.
2. The web is searched for `"EO51710" dvd` (or `blu-ray`). If nothing comes
   back, the bare number is searched instead.
3. Only results that mention the number are used. Their page titles are
   cleaned of shop names, format words and brackets.
4. The titles are counted, and the top ones are searched on TMDB and scored as
   usual, so the runtime still has to fit. A title that only one page names is
   capped at 0.85, so it goes to review instead of auto-confirming.

A Blu-ray with a real title in its `bdmt` metadata skips the lookup. TV discs
use the top title as the show name.

The job log records each step, for example `Catalogue number EO51710: 4 web
page(s) mention it; titles named: “The Mortal Instruments: City of Bones” (4)`.

## Search backend (Settings → Catalogue numbers)

**SearXNG (self-hosted, free).** SearXNG is a metasearch engine that queries
Google, Bing, DuckDuckGo and others for you. Add it to the stack:

```yaml
  searxng:
    image: docker.io/searxng/searxng:latest
    container_name: searxng
    restart: unless-stopped
    volumes:
      - ./searxng:/etc/searxng
```

Start it once so it writes `./searxng/settings.yml`. Then turn on JSON output
and restart it:

```yaml
search:
  formats:
    - html
    - json
```

Set the address to `http://searxng:8080`. If JSON output is still off,
SearXNG answers 403, and the Test button says so.

The Test button looks up EO51710 and shows what it found.
