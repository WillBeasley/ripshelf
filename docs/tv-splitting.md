# TV discs with every episode in one title

Most TV discs have one title per episode. Some, mostly older DVDs, put every episode into one long title instead. ripshelf rips that title whole, then cuts it into one file per episode, so each episode is filed and named like any other.

## When a disc is split

Once the show is known, ripshelf looks for one title that is at least **1.8 times** the season's typical episode runtime, on a disc where no other title is one episode long. If it finds one, that title is marked to be split, and the disc **always waits for review**: nothing is cut or copied until you confirm.

ripshelf only treats a disc as TV when its label looks like TV (a season or volume number) and it has a title of 40 minutes or more. Otherwise, use **This is a TV disc** on the Review card.

## How the cuts are found

1. **Chapters first.** If the title has at least one chapter per episode, the chapter marks are where it can be cut. Each mark is checked for black frames and silence, which is what a break between episodes usually looks like.
2. **Otherwise, a search.** With fewer chapters than episodes, often none, ripshelf searches the whole title for black frames and silence, and those breaks are where it can be cut. This decodes the entire title: about half a minute to a few minutes for a DVD, longer for a Blu-ray.
3. **Fit to the runtimes.** From the possible cut points, ripshelf picks the ones that best match the episode runtimes from TMDB, TheTVDB or TVmaze. A break with both black and silence beats one with only one of them. The proposal comes with a confidence score.
   - **A season disc** (a run of episodes): the parts are fitted in order, to the runtimes of the episodes starting at the disc's first one, and each part gets its episode.
4. **Which part is which episode.** The parts usually run alike, so their runtimes can't tell them apart, and discs often hold the episodes in another order than the season's. When a [cover scan](cover-scans.md) lists the episodes, the parts take the box's order: the first part gets the first episode listed, and so on. A box listing more episodes than the disc holds (all 13 of the season for a disc of 10) lists the disc's first. Without a scan the parts keep the season's order, and the Review card says that's a guess: check the frames, scan the box, or paste its run order.
   - **A compilation** (episodes from anywhere in the show): each part is fitted to the show's typical runtime, and starts with no episode picked.

## Reviewing the cuts

The Review card (under **More tools**, open while the cuts wait for a look) and the job's details show the title as a bar, with a tick at each chapter or break, the proposed cuts, and a frame from just after each cut.

- **Click a tick** to cut there or not.
- **Click anywhere else on the bar, or type a time** (like `25:10`) under it, to add a cut. It moves to the nearest black frames or silence within 10 seconds, or stays where you put it if there are none.
- **Use these cuts** applies your changes. New cuts propose the episodes again, so get the cuts right first, then pick the episodes.
- **Pick episodes** in the list as usual. The parts show where the title is, as "0.1", "0.2" and so on ("Title 0, part 1" in the picker). Or paste the run order from the case under **More tools**, and the names go onto the parts in order. After you change the cuts the parts get the season's order, and then the box's, when a cover scan lists the episodes.
- **Keep it as one title** turns the split off, if the title really is one long episode.

## The split

When you confirm, `mkvmerge` (MKVToolNix, included in the image) cuts the file at the chosen chapters, or at the chosen times, **without re-encoding**: picture, sound and subtitles are copied as they are. The parts are then transcoded (if that's on) and copied to the library like any episode. On Windows, install MKVToolNix; ripshelf finds `mkvmerge` in Program Files.

## Example

A DVD of *Burrow Folk* (a made-up show) holds two 24-minute episodes in one 50:22 title with two chapters:

- The title is about twice the 24-minute runtime, and it's the only long title, so it's marked to be split.
- It has two chapters for two episodes, which is enough. The second chapter starts at **24:38**, and there are black frames and silence there.
- Two parts of 24:38 and 25:44 match the two 24-minute runtimes, so ripshelf proposes one cut at 24:38, with full confidence.
- Without the chapters, the search would have found nine breaks, but only the one at 24:38 had black and silence together, and it would have proposed the same cut. A cut typed at 24:45 would also move to 24:38.

You check the frame after the cut, confirm the two episodes, and get two files.

## Limits

- **No theme-tune matching.** Cuts come from chapters, black frames and silence. A show that runs straight from one episode into the next, with no black or quiet moment, needs its cuts placed by hand.
- **Cuts land on keyframes.** The file can only be cut at a keyframe, so a cut lands at the first one at or after the time chosen, usually within a second.
- **TheDiscDB has no chapter data.** It lists titles, not chapters, so it can't say where to cut. If it lists the long title as an episode, the parts start from that episode. If it lists a title per episode, nothing is split.
- **The search takes time.** It decodes the whole title, so expect minutes, not seconds, for a Blu-ray.
