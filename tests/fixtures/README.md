# Test fixtures

`*.log` are MakeMKV robot-mode output (`makemkvcon -r`) from real discs in a four-drive test machine (MakeMKV 2.0.0). The titles, labels and episode names in them, and everywhere else in the tests, are made up and stand in for the real ones. New tests should make up their titles too.

| File | Source |
|---|---|
| `scan-uhd-brass-heron.log` | `info` of The Brass Heron (UHD) on the Pioneer BDR-S11JX, LibreDrive |
| `scan-dvd-moonlit-orchard.log` | `info` of Moonlit Orchard (PAL DVD) on the iHAS122, with all four drives visible |
| `scan-dvd-pippa-goose.log` | `info` of Pippa & Goose: Kite Tram (a DVD compilation with a play-all and 10 episodes) on the GHC0N |
| `rip-dvd-episode.log` | `mkv ... all` of that Pippa & Goose disc |
| `rip-uhd-partial.log` | the first 3000 lines of The Brass Heron's main-feature rip |

The failure logs are short excerpts, rebuilt by hand. They have the real start-up and drive lines from the Brass Heron scan, then the failure. The 3303, 5010 and 5020 lines are copied from real MakeMKV output. The 3346 ("LibreDrive required") text is a placeholder, because only the message code is parsed.

| File | Failure |
|---|---|
| `scan-uhd-volume-key-unknown.log` | 3303 volume key unknown, then 5010 failed to open |
| `scan-uhd-libredrive-required.log` | 3346 LibreDrive required, then 5010 |
| `scan-bad-key.log` | 5020 invalid registration key |

`tmdb/` has its own README. `tmdb_tv/` holds TMDB TV responses (search, show and season details) in the same way, with made-up show and episode names.
