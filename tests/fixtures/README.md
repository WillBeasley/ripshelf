# Test fixtures

`*.log` are MakeMKV robot-mode output (`makemkvcon -r`) from real discs in a four-drive test machine (MakeMKV 2.0.0):

| File | Source |
|---|---|
| `scan-uhd-wild-robot.log` | `info` of The Wild Robot (UHD) on the Pioneer BDR-S11JX, LibreDrive |
| `scan-dvd-practical-magic.log` | `info` of Practical Magic (PAL DVD) on the iHAS122, with all four drives visible |
| `scan-dvd-sarah-duck.log` | `info` of Sarah & Duck: Brolly Bus (a DVD compilation with a play-all and 10 episodes) on the GHC0N |
| `rip-dvd-episode.log` | `mkv ... all` of that Sarah & Duck disc |
| `rip-uhd-partial.log` | the first 3000 lines of The Wild Robot's main-feature rip |

The failure logs are short excerpts, rebuilt by hand. They have the real start-up and drive lines from the Wild Robot scan, then the failure. The 3303, 5010 and 5020 lines are copied from real MakeMKV output. The 3346 ("LibreDrive required") text is a placeholder, because only the message code is parsed.

| File | Failure |
|---|---|
| `scan-uhd-volume-key-unknown.log` | 3303 volume key unknown, then 5010 failed to open |
| `scan-uhd-libredrive-required.log` | 3346 LibreDrive required, then 5010 |
| `scan-bad-key.log` | 5020 invalid registration key |

The `tmdb/` and `tmdb_tv/` folders have their own READMEs.
