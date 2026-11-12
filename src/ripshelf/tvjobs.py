"""The TV half of the job pipeline (mixed into ``jobs.Manager``).

    scan -> media guess (shape + label) -> rip all titles
         -> identify show, season, disc; propose a title -> episode mapping
         -> review (always, for TV) with the mapping table, confirm
         -> thumbnails + order check against the play-all, once ripped
         -> finalise every mapped title into TV/Show (Year)/Season NN/

A disc with its episodes in one long title has that title split at chapters
(tv/split.py) after the rip, and the parts are mapped as titles of their own.
"""

from __future__ import annotations

import asyncio
import bisect
import logging
import re
import shutil
import statistics
from pathlib import Path
from typing import TYPE_CHECKING

from . import __version__, library, providers
from .identify.label import clean_bdmt_title
from .identify.scoring import title_similarity
from .identify.tmdb import Tmdb, TmdbError
from .models import (
    Candidate,
    Collection,
    CollectionDetail,
    CollectionDisc,
    DiscKind,
    DiscMatch,
    DiscMatchTitle,
    Episode,
    EpisodeRef,
    Evidence,
    JobState,
    MediaKind,
    Preset,
    Problem,
    RipState,
    SeasonCell,
    SeasonGrid,
    Show,
    TitleInfo,
    TitleMap,
    TitleMapUpdate,
    TitleRole,
    TitleSplit,
    TvPlan,
    parse_show_key,
)
from .providers import thediscdb
from .providers.tvmaze import Tvmaze, TvmazeError
from .tv import keyless, naming, shape, tmdb_tv, verify
from .tv import mapping as tvmap
from .tv import split as tvsplit
from .tv.labels import TvLabel, parse_tv_label, split_show_name
from .tv.tvdb import Tvdb, TvdbError

if TYPE_CHECKING:
    from .db import JobRecord

log = logging.getLogger(__name__)

# What a show or episode lookup can fail with.
LOOKUP_ERRORS = (TmdbError, TvdbError, TvmazeError)

# When a compilation disc is named after an episode ("KITE_TRAM"), that episode
# is usually the first title.
COMPILATION_NAME_MIN = 0.85
# Titles within this fraction of the season's shortest and longest episode are episodes.
RUNTIME_WINDOW = 0.15


def episode_key(e: EpisodeRef) -> tuple[int, int]:
    return (e.season, e.episode)


class TvMixin:
    # --- media guess ----------------------------------------------------------------

    def _classify_media(self, job: JobRecord) -> None:
        """Movie or TV from the disc's titles and label, right after the scan."""
        bluray = job.disc.kind in (DiscKind.bluray, DiscKind.uhd)
        sh = shape.analyse(job.titles, bluray)
        tvl = parse_tv_label(job.disc.label)
        evidence = list(sh.evidence)
        evidence += [h[0].upper() + h[1:] for h in tvl.hints]
        long = max(job.titles, key=lambda t: t.duration_s, default=None)
        one_long = bool(long and tvsplit.may_hold_several(long))
        is_tv = sh.looks_like_tv or (tvl.looks_like_tv and (len(sh.episodes) >= 2 or one_long))
        if is_tv and not sh.looks_like_tv and len(sh.episodes) < 2:
            evidence.append(f"title {long.index} is {round(long.duration_s / 60)} min, so maybe several episodes")
        if job.disc_match:  # the disc database knows
            is_tv = job.disc_match.media == MediaKind.tv
            evidence.insert(0, f"TheDiscDB lists it as {'a TV disc' if is_tv else 'a film'}")
        job.media = MediaKind.tv if is_tv else MediaKind.movie
        job.media_evidence = evidence
        if is_tv:
            nums = _disc_numbers(job)
            job.tv = TvPlan(season=nums.season, disc_number=nums.disc or nums.volume,
                            mapping=self._shape_mapping(job, sh))
            self._event(job, "info", "Looks like a TV disc, because of " + "; ".join(evidence[:3]))

    def _shape_mapping(self, job: JobRecord, sh: shape.DiscShape) -> list[TitleMap]:
        durations = {t.index: t.duration_s for t in job.titles}
        out = []
        for i, r in sh.roles.items():
            role, note = self._default_role(r), sh.notes.get(i)
            if role != r:
                note = f"{note}: an extra, not ripped (see Settings)" if note else "an extra, not ripped (see Settings)"
            out.append(TitleMap(title_index=i, duration_s=durations[i], role=role, note=note,
                                same_as=sh.same_as.get(i)))
        return out

    def _default_role(self, role: TitleRole) -> TitleRole:
        """The role a title starts with. Extras start as skip when Settings says not to rip them."""
        return TitleRole.skip if role == TitleRole.extra and not self.s.tv_rip_extras else role

    def _ordered_episode_titles(self, job: JobRecord, plan: TvPlan | None = None) -> list:
        """Episode titles in disc order. That's play-all clip order on Blu-ray, else title number."""
        plan = plan or job.tv
        roles = {m.title_index: m.role for m in plan.mapping} if plan else {}
        eps = [t for t in job.titles if roles.get(t.index) == TitleRole.episode]
        if plan and plan.splits:  # each long title's parts, in its place
            srcs = {sp.title_index for sp in plan.splits}
            eps = [t for t in eps if t.index not in srcs]
            parts = self._part_titles(plan)

            def where(t: TitleInfo) -> tuple:
                sp = plan.split_of(t.index) if tvsplit.is_part(t.index) else None
                return (sp.title_index, 1, t.index) if sp else (t.index, 0)

            return sorted([*eps, *parts], key=where)
        bluray = job.disc.kind in (DiscKind.bluray, DiscKind.uhd)
        pa = next((t for t in job.titles if roles.get(t.index) == TitleRole.play_all), None)
        order = shape.play_all_order(pa, eps, bluray)
        if order:
            by = {t.index: t for t in eps}
            return [by[i] for i in order]
        return sorted(eps, key=lambda t: t.index)

    # --- identification -------------------------------------------------------------

    def _tmdb(self) -> Tmdb | None:
        key = self.s.secret("tmdb_api_key")
        return Tmdb(key, self.s.tmdb_language) if key else None

    def _tvdb(self) -> Tvdb | None:
        key = self.s.secret("tvdb_api_key")
        return Tvdb(key, self.s.secret("tvdb_pin"), self.s.tmdb_language) if key else None

    def _show_sources(self) -> str:
        return " / ".join(providers.label(n) for n in providers.enabled(self.s, "tv") if n != "thediscdb") \
            or "No show search"

    def _keyless_sources(self) -> list[str]:
        """The show searches there are without a TMDB key (TheTVDB, TVmaze), in order."""
        return [n for n in providers.enabled(self.s, "tv") if n in keyless.SOURCES]

    async def _find_shows(self, tmdb: Tmdb | None, name: str, episode_s: float | None) -> list[Show]:
        """Shows for ``name`` from the providers in Settings' "TV shows" list, in order.
        Without a TMDB key, TheTVDB's or TVmaze's shows, keyed by their own ids."""
        sources = [n for n in providers.enabled(self.s, "tv") if n != "thediscdb"]
        tvdb = self._tvdb() if "tvdb" in sources else None
        maze = Tvmaze() if "tvmaze" in sources else None
        try:
            if not tmdb:
                return await keyless.find_shows(name, episode_s, sources=self._keyless_sources(), tvdb=tvdb,
                                                tvmaze=maze)
            return await tmdb_tv.find_shows(tmdb, name, episode_s, tvdb=tvdb, sources=sources, tvmaze=maze) \
                if sources else []
        finally:
            for c in (tvdb, maze):
                if c:
                    await c.aclose()

    async def _disc_match_show(self, tmdb: Tmdb | None, match: DiscMatch, episode_s: float | None) -> Show | None:
        """The show a disc database says this disc is, from TMDB (or without a key, TVmaze
        or TheTVDB), scored as certain."""
        if tmdb:
            tmdb_id = match.tmdb_id or await tmdb.find_id("tv", match.tvdb_id, match.imdb_id)
            if not tmdb_id:
                return None
            show = tmdb_tv.to_show({**await tmdb.tv_show(tmdb_id), "id": tmdb_id})
        else:
            maze = Tvmaze()
            try:
                show = await keyless.disc_match_show(match, self._keyless_sources(), maze)
            finally:
                await maze.aclose()
            if not show:
                return None
        show = tmdb_tv.score_show(show, [match.title], episode_s)
        show.excluded, show.score, show.providers = False, 1.0, ["thediscdb", show.source]
        show.evidence.insert(0, Evidence(ok=True, text=f"TheDiscDB knows this disc as {thediscdb.summary(match)}"))
        return show

    async def _identify_tv(self, job_id: int) -> None:
        job = self.db.get_job(job_id)
        self._set_state(job, JobState.identifying, "Identifying TV disc")
        plan = job.tv or TvPlan()
        tvl = parse_tv_label(job.disc.label)
        name = clean_bdmt_title(job.disc.name) or tvl.show
        if name and name == name.upper():
            name = parse_tv_label(name.replace(" ", "_")).show
        # For "Boardwalk Empire Season 1 Disc 2", search for the show and keep the numbers.
        named = split_show_name(name)
        name = named.show
        if plan.season is None and named.season is not None:
            plan.season, plan.disc_number = named.season, plan.disc_number or named.disc or named.volume
        tmdb = self._tmdb()
        searchable = bool(tmdb or self._keyless_sources())
        found = await self._catalogue_lookup(job) if searchable else None
        if found and found.names:
            name = found.names[0]
        hints = await self.scan_hints(job)
        if hints.names and not name:
            name = hints.names[0]  # the label says nothing; the box's spine does
            self._event(job, "info", f"Searching for the show the cover scan names: {name}")
        match = job.disc_match if job.disc_match and job.disc_match.media == MediaKind.tv \
            and "thediscdb" in providers.enabled(self.s, "tv") else None
        if not searchable:
            self._event(job, "warning", "No TV show search is on (a TMDB key, or TVmaze or TheTVDB in "
                                        "Settings → Identify), so pick the show by hand")
        elif not name and not match:
            self._event(job, "warning", "The disc label is generic; search for the show by hand")
        else:
            try:
                ordered = self._ordered_episode_titles(job)
                eps = [t.duration_s for t in ordered]
                typical = sorted(eps)[len(eps) // 2] if eps else None
                if len(ordered) == 1 and tvsplit.may_hold_several(ordered[0]):
                    typical = None  # one long title, maybe several episodes, so no episode length
                shows = await self._find_shows(tmdb, name, typical) if name else []
                if name:
                    self._event(job, "info", f"{self._show_sources()}: {len(shows)} shows for “{name}”")
                known = None
                if match:
                    try:
                        known = await self._disc_match_show(tmdb, match, typical)
                    except LOOKUP_ERRORS as e:
                        self._event(job, "warning", f"TheDiscDB's show could not be looked up on "
                                                    f"{'TMDB' if tmdb else 'TVmaze or TheTVDB'}: {e}")
                job = self.db.get_job(job_id)  # the rip may have saved while TMDB answered
                plan = job.tv or plan
                if known:
                    shows = [known, *(x for x in shows if x.key != known.key)]
                    seasons = [t.season for t in match.titles if t.kind == "episode" and t.season is not None]
                    if seasons:
                        plan.season = max(set(seasons), key=seasons.count)
                plan.candidates = shows[:6]
                best = next((s for s in shows if not s.excluded), None)
                job.tv = plan
                self._save(job)
                if best:
                    await self._propose(job.id, best, plan.season, plan.disc_number, None, None, tmdb)
                    job = self.db.get_job(job_id)
                    plan = job.tv
            except LOOKUP_ERRORS as e:
                self._event(job, "warning", str(e))
            finally:
                if tmdb:
                    await tmdb.aclose()
        job = self.db.get_job(job_id)
        job.tv = plan if job.tv is None else job.tv
        self._save(job)
        self._set_state(job, JobState.review, "Waiting for review of the episode mapping")
        await self.fix_show_from_scan(job_id)  # the box read before identification may know better
        await self._maybe_auto_confirm(job_id)

    def _next_for(self, show: Show, season: int | None, disc_number: int | None = None,
                  job_id: int | None = None) -> tuple[int | None, int | None, int | None]:
        """(season, next disc number, next episode) continuing the show's collection.

        With the disc number known, only the disc just before it counts. Disc 1 ripped
        after disc 2 starts at episode 1, not after disc 2's episodes."""
        c = self.db.collection_for_show(show.key)
        if not c:
            return season, None, None
        # Only what's still in the library counts: files deleted by hand don't carry on.
        here = self._episodes_on_disk(show)
        if here is not None:
            c.discs = [d.model_copy(update={"episodes": [e for e in d.episodes if (e.season, e.episode) in here]})
                       for d in c.discs]
            c.discs = [d for d in c.discs if d.episodes]
        discs = [d for d in c.discs if not d.compilation and d.job_id != job_id
                 and (season is None or d.season == season)]
        if not discs:
            return season, None, None
        last_season = season if season is not None else max(d.season or 1 for d in discs)
        in_season = [d for d in discs if d.season == last_season]
        if disc_number:
            prev = [d for d in in_season if d.disc_number == disc_number - 1]
            mapped = [e.episode for d in prev for e in d.episodes if e.season == last_season]
            return last_season, disc_number, (max(mapped) + 1 if mapped else None)
        disc_no = max((d.disc_number or 0) for d in in_season) + 1 if in_season else None
        mapped = [e.episode for d in in_season for e in d.episodes if e.season == last_season]
        return last_season, disc_no, (max(mapped) + 1 if mapped else None)

    async def _propose(self, job_id: int, show: Show, season: int | None, disc_number: int | None,
                       start_episode: int | None, compilation: bool | None, tmdb: Tmdb | None) -> None:
        """Fill the job's TV plan for ``show``, with episodes and a proposed mapping."""
        job = self.db.get_job(job_id)
        plan = job.tv or TvPlan()
        old_split = self._unsplit(plan)
        ordered = self._ordered_episode_titles(job, plan)
        others = {m.title_index: m.model_copy(update={"episodes": []}) for m in plan.mapping
                  if m.role != TitleRole.episode}
        pal = any(t.framerate == "25" for t in ordered)
        notes: list[str] = []

        tvl = parse_tv_label(job.disc.label)
        remainder = _label_remainder(tvl.show, show.name)
        if compilation is None:
            compilation = False
            if season is None and remainder:
                every = await self._all_episodes(show, tmdb)
                hit = max(every, key=lambda e: title_similarity(remainder, e.name), default=None)
                if hit and title_similarity(remainder, hit.name) >= COMPILATION_NAME_MIN:
                    compilation = True
                    notes.append(f"Disc is named after “{hit.name}” (S{hit.season:02d}E{hit.episode:02d}), "
                                 "with no season in the label, so a compilation")
        if compilation:
            episodes = await self._all_episodes(show, tmdb)
            plan.compilation, plan.season, plan.start_episode = True, None, None
            plan.season_poster_url = None
            plan.episodes = episodes
            split_note = self._plan_split(job, plan, show, old_split)
            if split_note:
                notes.append(split_note)
                ordered = self._ordered_episode_titles(job, plan)
                others = {m.title_index: m.model_copy(update={"episodes": []}) for m in plan.mapping
                          if m.role != TitleRole.episode}
            mapping = self._compilation_rows(ordered)
            if remainder and ordered:
                hit = max(episodes, key=lambda e: title_similarity(remainder, e.name), default=None)
                if hit and title_similarity(remainder, hit.name) >= COMPILATION_NAME_MIN:
                    mapping[0].episodes = [EpisodeRef(season=hit.season, episode=hit.episode)]
                    mapping[0].note = f"the disc is named “{hit.name}”"
            plan.mapping = sorted([*mapping, *others.values()], key=lambda m: m.title_index)
            plan.order_from = None
            self._label_parts(plan)
        else:
            s_guess, disc_guess, start_guess = self._next_for(show, season, disc_number, job_id)
            season = season if season is not None else (s_guess or 1)
            disc_number = disc_number or disc_guess
            episodes, season_poster, own = await self._season_details(show, season, tmdb)
            episodes, source = await self._episode_list(show, season, episodes, tmdb, own)
            if source:
                notes.append(source)
            if not episodes:
                notes.append(f"No episodes listed for season {season}")
            reshaped = self._reshape(job, plan, episodes)
            if reshaped:
                notes.append(reshaped)
                ordered = self._ordered_episode_titles(job, plan)
                others = {m.title_index: m.model_copy(update={"episodes": []}) for m in plan.mapping
                          if m.role != TitleRole.episode}
            continues = False
            if start_episode is None:
                start_episode = start_guess
                if start_guess:
                    continues = True
                    notes.append(f"Continues the collection from episode {start_guess}")
                elif disc_number and disc_number > 1 and ordered:
                    start_episode = (disc_number - 1) * len(ordered) + 1
                    notes.append(f"Disc {disc_number} with {len(ordered)} episodes per disc: "
                                 f"starting at episode {start_episode} (a guess)")
            plan.compilation, plan.season, plan.disc_number = False, season, disc_number
            plan.start_episode = start_episode or 1
            plan.continues_collection = continues
            plan.season_poster_url = season_poster
            placed = _disc_numbers(job)
            plan.label_placed = placed.season == season and (placed.disc or placed.volume) is not None
            plan.episodes = episodes
            split_note = self._plan_split(job, plan, show, old_split)
            if split_note:
                notes.append(split_note)
                ordered = self._ordered_episode_titles(job, plan)
                others = {m.title_index: m.model_copy(update={"episodes": []}) for m in plan.mapping
                          if m.role != TitleRole.episode}
            plan.mapping = tvmap.propose(ordered, others, episodes, plan.start_episode, pal)
            plan.order_from = "season"
            self._label_parts(plan)
            plan.disc_db = False
            mapped = self._disc_match_mapping(job, plan, show)
            if mapped:
                notes.append(mapped)
                plan.order_from = "disc_db"
        # Everything from TMDB is in hand, so apply it to the job as it is now (the rip
        # task saves to it while we wait on the network).
        fresh = self.db.get_job(job_id)
        plan.show = show
        plan.order_verified, plan.order_note = None, None
        if fresh.tv:
            plan.candidates = fresh.tv.candidates or plan.candidates
        job = fresh
        for n in notes:
            self._event(job, "info", n)
        job.tv = plan
        self._check_plan(job)
        self._save(job)
        if job.rip.state == RipState.done and job.rip.all_titles and not job.refile:
            self._spawn(self.drives.get(job.drive_id), self._after_tv_rip(job.id), job.id)

    def _disc_match_mapping(self, job: JobRecord, plan: TvPlan, show: Show) -> str | None:
        """Map titles to episodes as the disc database's record of this disc says, when
        it is this show and season. Titles it lists as something else stop being episodes."""
        match = job.disc_match
        if not (match and match.media == MediaKind.tv and "thediscdb" in providers.enabled(self.s, "tv")):
            return None
        if not _same_show(show, match):
            return None
        said = {t.scan_index: t for t in match.titles if t.scan_index is not None}
        for sp in plan.splits:  # mapped through its parts (see _plan_split)
            said.pop(sp.title_index, None)
        eps = {i: t for i, t in said.items() if t.kind == "episode" and t.season == plan.season and t.episode}
        if not eps:
            return None
        durations = {t.index: t.duration_s for t in job.titles}
        by_index = {m.title_index: m for m in plan.mapping}
        for i, t in said.items():
            m = by_index.get(i)
            if i in eps:
                m = by_index.setdefault(i, TitleMap(title_index=i, duration_s=durations.get(i, 0),
                                                    role=TitleRole.episode))
                m.role, m.episodes, m.match_cost = TitleRole.episode, [EpisodeRef(season=t.season, episode=t.episode)], 0.0
                m.note = f"TheDiscDB: “{t.name}”" if t.name else "TheDiscDB"
            elif m and m.role == TitleRole.episode:
                m.role = self._default_role(TitleRole.extra) if t.kind == "extra" else TitleRole.skip
                m.episodes, m.match_cost = [], None
                m.note = "an extra, says TheDiscDB" if t.kind == "extra" else "not an episode, says TheDiscDB"
        plan.mapping = sorted(by_index.values(), key=lambda m: m.title_index)
        plan.start_episode = min(t.episode for t in eps.values())
        plan.disc_db = True
        numbers = sorted(t.episode for t in eps.values())
        return (f"TheDiscDB knows this disc. It's season {plan.season}, episodes "
                f"{numbers[0]}–{numbers[-1]}" if len(numbers) > 1 else
                f"TheDiscDB knows this disc. It's season {plan.season}, episode {numbers[0]}")

    # --- one long title holding the episodes ---------------------------------------------

    def _unsplit(self, plan: TvPlan, title_index: int | None = None) -> list[TitleSplit]:
        """Take the parts out of the mapping, giving each long title its role back (only
        ``title_index``'s, when given). The splits taken out."""
        gone = [sp for sp in plan.splits if title_index is None or sp.title_index == title_index]
        for sp in gone:
            plan.mapping = [m for m in plan.mapping if not sp.has_part(m.title_index)]
            for m in plan.mapping:
                if m.title_index == sp.title_index:
                    m.role, m.note, m.by_hand = sp.was, None, False
        plan.splits = [sp for sp in plan.splits if sp not in gone]
        return gone

    def _disc_db_episodes(self, job: JobRecord, plan: TvPlan, show: Show) -> dict[int, DiscMatchTitle]:
        """Titles the disc database lists as episodes of this season (scan index -> its title)."""
        match = job.disc_match
        if not (match and match.media == MediaKind.tv and "thediscdb" in providers.enabled(self.s, "tv")):
            return {}
        if not _same_show(show, match):
            return {}
        return {t.scan_index: t for t in match.titles if t.scan_index is not None and t.kind == "episode"
                and t.season == plan.season and t.episode}

    def _plan_split(self, job: JobRecord, plan: TvPlan, show: Show, old: list[TitleSplit]) -> str | None:
        """Mark the long titles that hold this disc's episodes, when there are any, to be
        split at their chapters. Their parts take their place in the mapping. The box's
        listing, when read, says how many episodes there are and how long they run."""
        if plan.split_off:
            return None
        roles = {m.title_index: m.role for m in plan.mapping}
        typical, count = self._box_episodes(job)
        cands = [c for c in tvsplit.detect_all(job.titles, roles, plan.episodes, typical, count)
                 if c.title_index not in plan.whole]
        if not cands:
            return None
        listed = self._disc_db_episodes(job, plan, show)
        if any(i not in {c.title_index for c in cands} for i in listed):
            return None  # the disc database has a title per episode
        notes = []
        for n, cand in enumerate(cands):
            title = next(t for t in job.titles if t.index == cand.title_index)
            row = next((m for m in plan.mapping if m.title_index == title.index), None)
            if row is None:
                row = TitleMap(title_index=title.index, duration_s=title.duration_s, role=TitleRole.episode)
                plan.mapping = sorted([*plan.mapping, row], key=lambda m: m.title_index)
            kept = next((sp for sp in [*plan.splits, *old] if sp.title_index == title.index), None)
            sp = kept or TitleSplit(title_index=title.index, duration_s=title.duration_s, expected=cand.episodes,
                                    reason=cand.reason, was=row.role)
            sp.expected, sp.reason = cand.episodes, cand.reason
            sp.base = tvsplit.PART_BASE + 100 * n
            row.role, row.episodes, row.match_cost, row.by_hand = TitleRole.skip, [], None, False
            row.note = f"holds about {cand.episodes} episodes, so it's split into parts"
            plan.splits = [*[x for x in plan.splits if x.title_index != title.index], sp]
            notes.append(f"{cand.reason[0].upper()}{cand.reason[1:]}, so it's split into episodes at its chapters")
            first = listed.get(title.index)
            if first and n == 0:
                plan.start_episode = first.episode
                notes.append(f"TheDiscDB says it starts with episode {first.episode}")
            if sp.chapters and not sp.edited:
                self._cut(job, plan, sp)
        plan.splits.sort(key=lambda sp: sp.title_index)
        return "; ".join(notes)

    def _box_episodes(self, job: JobRecord) -> tuple[float | None, int | None]:
        """How long an episode runs (s) and how many there are, as the box says: its
        printed runtimes, or its total over its count. (None, None) without a reading."""
        listing = self._listing_for(job)
        if not listing:
            return None, None
        eps = [it for it in listing.items if it.kind == "episode"]
        count = listing.details.episode_count or len(eps) or None
        printed = [it.runtime_min for it in eps if it.runtime_min]
        total = listing.details.feature_runtime_min
        if printed:
            return statistics.median(printed) * 60, count
        if total and count:
            return total / count * 60, count
        return None, count

    def _cut(self, job: JobRecord, plan: TvPlan, sp: TitleSplit) -> None:
        """Propose where to cut, from the chapters and the season's runtimes (or the box's).
        A compilation's episodes come from anywhere in the show, so every part fits its
        typical runtime."""
        typical, _ = self._box_episodes(job)
        if plan.compilation or typical:
            runtimes = [e.runtime for e in plan.episodes if e.runtime]
            minutes = typical / 60 if typical else round(statistics.median(runtimes)) if runtimes else None
            eps = [Episode(season=0, episode=n + 1, runtime=minutes) for n in range(len(sp.chapters))]
        else:
            eps = sorted((e for e in plan.episodes if e.season == plan.season
                          and e.episode >= (plan.start_episode or 1)), key=lambda e: e.episode)
        pal = any(t.framerate == "25" for t in job.titles)
        got = tvsplit.propose_cuts(sp.chapters, sp.duration_s, sp.breaks, eps, pal)
        sp.cuts, sp.proposed, sp.confidence, sp.note = got.cuts, list(got.cuts), got.confidence, got.note

    def _part_titles(self, plan: TvPlan) -> list[TitleInfo]:
        return [TitleInfo(index=sp.part(k), duration_s=round(b - a), size_bytes=0, chapters=0)
                for sp in plan.splits if sp.state in ("ready", "split")
                for k, (a, b, _) in enumerate(tvsplit.parts(sp.chapters, sp.duration_s, sp.cuts))]

    def _label_parts(self, plan: TvPlan) -> None:
        for sp in plan.splits:
            bounds = tvsplit.parts(sp.chapters, sp.duration_s, sp.cuts) if sp.chapters else []
            for m in plan.mapping:
                k = m.title_index - sp.base
                if sp.has_part(m.title_index) and k < len(bounds):
                    m.part_of, m.start_s = sp.title_index, round(bounds[k][0], 2)

    def _remap(self, job: JobRecord, plan: TvPlan) -> None:
        """Propose the episodes again for the parts as they are now (no network needed)."""
        plan.mapping = [m for m in plan.mapping if not tvsplit.is_part(m.title_index)]
        ordered = self._ordered_episode_titles(job, plan)
        others = {m.title_index: m.model_copy(update={"episodes": []}) for m in plan.mapping
                  if m.role != TitleRole.episode}
        if plan.compilation:  # the parts start with nothing picked; other titles keep theirs
            kept = {m.title_index: m for m in plan.mapping}
            rows = [kept.get(t.index) or row for t, row in zip(ordered, self._compilation_rows(ordered), strict=True)]
            plan.mapping = sorted([*rows, *others.values()], key=lambda m: m.title_index)
        else:
            pal = any(t.framerate == "25" for t in job.titles)
            plan.mapping = tvmap.propose(ordered, others, plan.episodes, plan.start_episode, pal)
            plan.order_from = "season"
        self._label_parts(plan)

    @staticmethod
    def _compilation_rows(ordered: list[TitleInfo]) -> list[TitleMap]:
        return [TitleMap(title_index=t.index, duration_s=t.duration_s, role=TitleRole.episode,
                         note="pick the episode, or paste the run order from the case") for t in ordered]

    async def _analyse_split(self, job_id: int) -> None:
        """Find the marks each long title can be cut at, and propose the cuts.

        Those are its chapters, each checked for a break (black frames, silence) and
        with a frame grabbed after it. With fewer chapters than episodes, the whole title
        is searched for breaks instead, which decodes all of it."""
        job = self.db.get_job(job_id)
        waiting = [sp.title_index for sp in (job.tv.splits if job and job.tv else [])
                   if sp.state == "waiting" and sp.title_index in job.rip.files]
        ready = False
        for idx in waiting:
            ready = await self._analyse_one(job_id, idx) or ready
        if ready:
            # The parts were given the season's episodes in order; the box's list says which is which.
            self.rematch(job_id)
            self.apply_scan_picks(job_id)

    async def _analyse_one(self, job_id: int, idx: int) -> bool:
        """``_analyse_split`` for title ``idx``. True if its cuts are proposed."""
        job = self.db.get_job(job_id)
        sp = job.tv.split_of(idx)
        path = Path(job.rip.files[idx])
        chapters = await tvsplit.read_chapters(self.s.ffprobe, path)
        length = await verify.duration(self.s.ffprobe, path) or float(sp.duration_s)
        marks: list[float] = []
        error = "ffprobe could not read the rip's chapters" if chapters is None else None
        if chapters is not None and tvsplit.enough_chapters(len(chapters), sp.expected):
            marks, kinds, source = chapters, ["chapter"] * len(chapters), "chapters"
            breaks: list[float | None] = [None] * len(chapters)
            gate = asyncio.Semaphore(4)

            async def look(k: int, at: float) -> None:
                async with gate:
                    if 0 < k <= tvsplit.MAX_CHECKED:
                        breaks[k] = await tvsplit.break_score(self.s.ffmpeg, path, at)
                    await self._grab_mark(job, idx, path, at, length)

            await asyncio.gather(*(look(k, at) for k, at in enumerate(chapters)))
        elif chapters is not None:
            self._event(job, "info", f"Title {idx} has {len(chapters) or 'no'} chapters for about {sp.expected} "
                        "episodes, so it's searched for black frames and silence to cut at (this decodes all of it)")
            found = await tvsplit.scan_breaks(self.s.ffmpeg, path, length)
            if found is None:
                error = "ffmpeg could not search it for black frames and silence"
            else:
                marks = [0.0, *(br.at for br in found)]
                kinds, source = ["start"] + ["break"] * len(found), "scan"
                breaks = [None, *(br.score for br in found)]
                error = None if found else "it has no chapters to cut at, and no black frames or silence either"
        job = self.db.get_job(job_id)
        sp = job.tv.split_of(idx) if job.tv else None
        if not sp or sp.title_index != idx or sp.state != "waiting":
            return False
        if error or len(marks) < 2:
            sp.state, sp.error = "failed", error or "the rip has no chapters to split at"
            self._event(job, "warning", f"Title {idx} can't be split into episodes: {sp.error}")
        else:
            sp.state, sp.chapters, sp.kinds, sp.breaks = "ready", marks, kinds, breaks
            sp.marks_from, sp.duration_s = source, round(length, 3)
            if not sp.edited:
                self._cut(job, job.tv, sp)
            self._remap(job, job.tv)
            what = f"{len(marks)} chapters" if source == "chapters" else f"{len(marks) - 1} breaks"
            self._event(job, "info", f"Title {idx} has {what}. Proposed {len(sp.cuts) + 1} "
                        f"episodes: {sp.note}; confidence {sp.confidence:.0%}")
        self._check_plan(job)
        self._save(job)
        return sp.state == "ready"

    def _mark_thumb(self, job: JobRecord, title_index: int, at: float) -> Path:
        # By time, not mark number, as a cut put in by hand renumbers the marks after it.
        return self.work_dir(job) / "thumbs" / f"split-{int(title_index)}-{round(at * 1000)}.jpg"

    async def _grab_mark(self, job: JobRecord, title_index: int, src: Path, at: float, length: float) -> Path | None:
        out = self._mark_thumb(job, title_index, at)
        if not out.exists():
            await verify.thumbnail(self.s.ffmpeg, src, out,
                                   at_s=max(0.0, min(at + tvsplit.THUMB_AFTER_S, length - 1)))
        return out if out.exists() else None

    async def _add_cut(self, job: JobRecord, sp: TitleSplit, at: float) -> str:
        """Cut ``sp``'s title at ``at`` (s), or at the best break within ``SNAP_S`` of it,
        adding a mark there unless one is already that close. Says where it went."""
        if not 0 < at < sp.duration_s:
            raise ValueError(f"a cut must be inside the title (0:00 to {_clock(sp.duration_s)})")
        src = job.rip.files.get(sp.title_index)
        br = await tvsplit.snap(self.s.ffmpeg, Path(src), at) if src and Path(src).exists() else None
        where = br.at if br and 0 < br.at < sp.duration_s else at
        how = ("at black frames and silence" if br.score >= 1 else "at black frames or silence") \
            + f" near {_clock(at)}" if br else "with no break nearby"
        kinds = sp.kinds + ["chapter"] * (len(sp.chapters) - len(sp.kinds))  # splits saved before kinds
        near = min(range(len(sp.chapters)), key=lambda k: abs(sp.chapters[k] - where))
        if abs(sp.chapters[near] - where) < tvsplit.SAME_BREAK_S and near > 0:
            k = near
        else:
            k = bisect.bisect(sp.chapters, where)
            sp.chapters.insert(k, round(where, 3))
            kinds.insert(k, "manual")
            sp.breaks.insert(k, br.score if br else 0.0 if src else None)  # None: couldn't look
            sp.cuts = [c + (c >= k) for c in sp.cuts]
            sp.proposed = [c + (c >= k) for c in sp.proposed]
        sp.kinds = kinds
        sp.cuts = sorted({*sp.cuts, k})
        return f"Cut title {sp.title_index} at {_clock(sp.chapters[k])}, {how}"

    async def tv_split(self, job_id: int, cuts: list[int] | None, off: bool | None,
                       cut_at: float | None = None, title_index: int | None = None) -> JobRecord:
        """Change where a long title is cut, add a cut anywhere (``cut_at``), keep it whole
        (``off``), or split it after all. ``title_index`` says which (else the first)."""
        job = self._tv_job(job_id)
        self._refiling(job, "be split differently, as its parts are already in the library")
        plan = job.tv
        if not plan or not plan.show:
            raise ValueError("pick the show first")
        sp = plan.split_of(title_index) if title_index is not None else plan.split
        if off:
            if title_index is None or len(plan.splits) <= 1:
                self._unsplit(plan)
                plan.split_off = True
            else:
                self._unsplit(plan, title_index)
                plan.whole = sorted({*plan.whole, title_index})
            self._event(job, "info", "Keeping the long title whole" if title_index is None
                        else f"Keeping title {title_index} whole")
        elif off is False:
            plan.split_off = False
            plan.whole = [i for i in plan.whole if title_index is not None and i != title_index]
            if (title_index is None and not plan.splits) or (title_index is not None and sp is None):
                if not self._plan_split(job, plan, plan.show, []):
                    raise ValueError("no title on this disc is long enough to hold several episodes")
            if any(x.state == "waiting" for x in plan.splits) and job.rip.state == RipState.done:
                self._spawn(self.drives.get(job.drive_id), self._after_tv_rip(job.id), job.id)
        if cuts is not None or cut_at is not None:
            if not sp or sp.state != "ready":
                raise ValueError("there are no chapters to cut at yet")
            bad = [c for c in cuts or [] if not 0 < c < len(sp.chapters)]
            if bad:
                raise ValueError(f"chapter {bad[0]} can't start an episode (pick 1 to {len(sp.chapters) - 1})")
            if cuts is not None:
                sp.cuts = sorted(set(cuts))
            if cut_at is not None:
                self._event(job, "info", await self._add_cut(job, sp, cut_at))
            sp.edited = sp.cuts != sp.proposed
        plan.user_edited = True
        self._remap(job, plan)
        self._check_plan(job)
        self._save(job)
        # New cuts give the parts the season's order; the box's list says which is which.
        if not self.apply_scan_picks(job_id):
            self.rematch(job_id)
        return self.db.get_job(job_id)

    async def _split_title(self, job_id: int) -> bool:
        """Split each long title into one file per part (mkvmerge, no re-encoding). False if one failed."""
        job = self.db.get_job(job_id)
        for idx in [sp.title_index for sp in job.tv.splits if sp.state != "split"]:
            if not await self._split_one(job_id, idx):
                return False
        return True

    async def _split_one(self, job_id: int, idx: int) -> bool:
        job = self.db.get_job(job_id)
        sp = job.tv.split_of(idx)
        src = job.rip.files.get(sp.title_index)
        if not src or not Path(src).exists():
            self._fail(job, Problem(code="rip_incomplete", message=f"the ripped file of title {sp.title_index} "
                                    "is missing", actions=["retry"]))
            return False
        n = len(sp.cuts) + 1
        # At the chapters when every cut is one, else at the cuts' times (each at the next keyframe).
        on_chapters = all(c >= len(sp.kinds) or sp.kinds[c] == "chapter" for c in sp.cuts)
        times = None if on_chapters else [sp.chapters[c] for c in sp.cuts]
        where = (f"at chapters {', '.join(str(c + 1) for c in sp.cuts)}" if on_chapters
                 else f"at {', '.join(_clock(t) for t in times)}")
        self._set_state(job, JobState.finalising, f"Splitting title {sp.title_index} into {n} episodes")
        self._set_step(job, "Splitting into episodes", f"Title {sp.title_index} {where}, without re-encoding")
        out = self.scratch_dir(job) / ("split" if sp.base == tvsplit.PART_BASE else f"split-{sp.title_index}")
        try:
            files = await tvsplit.split_file(tvsplit.default_mkvmerge(self.s.mkvmerge), Path(src), out,
                                             sp.cuts, times)
        except tvsplit.SplitError as e:
            self._fail(job, Problem(code="split_failed", message=f"Could not split title {sp.title_index}: {e}",
                                    hint="Splitting needs mkvmerge, from MKVToolNix (the image has it)",
                                    actions=["retry"]))
            return False
        job = self.db.get_job(job_id)
        sp = job.tv.split_of(idx)
        for k, f in enumerate(files):
            job.rip.files[sp.part(k)] = str(f)
        sp.state = "split"
        self._event(job, "finalise", f"Split title {sp.title_index} into {len(files)} files")
        self._save(job)
        return True

    async def _season_details(self, show: Show, season: int,
                              tmdb: Tmdb | None) -> tuple[list[Episode], str | None, str | None]:
        """(episodes, season poster, note on where they came from) from the show's own
        provider. The note is None for TMDB."""
        if show.source == "tmdb":
            if not tmdb:
                raise TmdbError("No TMDB API key set")
            return (*await tmdb_tv.season_details(tmdb, show.tmdb_id, season), None)
        tvdb, maze = self._tvdb(), Tvmaze()
        try:
            got, what = await keyless.season_episodes(show, season, tvdb, maze, self.s.tvdb_episode_order == "dvd")
        finally:
            for c in (tvdb, maze):
                if c:
                    await c.aclose()
        return got, None, f"Episodes from {providers.label(show.source)}, {what}" if got else None

    async def _all_episodes(self, show: Show, tmdb: Tmdb | None) -> list[Episode]:
        """Every episode of the show, from its own provider (compilations)."""
        if show.source == "tmdb":
            if not tmdb:
                raise TmdbError("No TMDB API key set")
            return await tmdb_tv.all_episodes(tmdb, show)
        tvdb, maze = self._tvdb(), Tvmaze()
        try:
            return await keyless.all_episodes(show, tvdb, maze, self.s.tvdb_episode_order == "dvd")
        finally:
            for c in (tvdb, maze):
                if c:
                    await c.aclose()

    async def _episode_list(self, show: Show, season: int, episodes: list[Episode],
                            tmdb: Tmdb | None, own: str | None = None) -> tuple[list[Episode], str | None]:
        """The season's episodes from the first provider in Settings' "TV episodes" list
        that has them; ``episodes`` (from the show's own provider) when none does. Returns
        the episodes and a note on where they came from (``own``, None for TMDB, as asked)."""
        notes: list[str] = []
        # The show's own provider is in hand here (it found the show), whatever Settings says about its key.
        order = [r["name"] for r in providers.lists(self.s)["episodes"]
                 if r["enabled"] and (r["name"] == show.source or providers.configured(self.s, r["name"]))]
        for name in order:
            if name == show.source:
                break
            if name == "tmdb":
                continue  # TMDB's episodes need a TMDB show
            try:
                got, what = await (self._tvdb_episodes if name == "tvdb" else self._tvmaze_episodes)(
                    show, season, tmdb)
            except (TvdbError, TvmazeError, TmdbError) as e:
                notes.append(f"{providers.label(name)}: {e}")
                continue
            if not got:
                notes.append(what)
                continue
            if what.endswith("aired order"):
                # TheTVDB and TVmaze often lack runtimes where TMDB has them (same episode, same number).
                tm = {e.episode: e for e in episodes}
                for e in got:
                    if e.runtime is None and e.episode in tm:
                        e.runtime = tm[e.episode].runtime
            return got, "; ".join([*notes, what])
        if not notes:
            return episodes, own
        return episodes, "; ".join([*notes, own or f"using {providers.label(show.source)}'s episodes"])

    async def _tvdb_episodes(self, show: Show, season: int, tmdb: Tmdb | None) -> tuple[list[Episode], str]:
        tvdb = self._tvdb()
        try:
            await self._external_ids(show, tmdb)
            if show.tvdb_id is None:
                return [], (f"TheTVDB not used, because {providers.label(show.source)} has no TheTVDB id "
                            "for this show")
            dvd = self.s.tvdb_episode_order == "dvd"
            what = "DVD order" if dvd else "aired order"
            got = await tvdb.episodes(show.tvdb_id, season, "dvd" if dvd else "default")
            if not got and dvd:
                got, what = await tvdb.episodes(show.tvdb_id, season, "default"), "aired order (no DVD order)"
            if not got:
                return [], f"TheTVDB has no season {season}"
            return got, f"Episodes from TheTVDB, {what}"
        finally:
            await tvdb.aclose()

    async def _tvmaze_episodes(self, show: Show, season: int, tmdb: Tmdb | None) -> tuple[list[Episode], str]:
        maze = Tvmaze()
        try:
            maze_id = show.tvmaze_id
            if not maze_id:
                await self._external_ids(show, tmdb)
                found = await maze.lookup(show.tvdb_id, show.imdb_id)
                if not found:
                    return [], "TVmaze doesn't have this show"
                maze_id = found["id"]
            got = await maze.episodes(maze_id, season)
            return got, ("Episodes from TVmaze, aired order" if got else f"TVmaze has no season {season}")
        finally:
            await maze.aclose()

    async def _external_ids(self, show: Show, tmdb: Tmdb | None) -> None:
        """Fill in a TMDB show's TheTVDB and IMDb ids from TMDB, when there is no TheTVDB id."""
        if show.tvdb_id is None and show.tmdb_id and tmdb:
            known = tmdb_tv.to_show({**await tmdb.tv_show(show.tmdb_id), "id": show.tmdb_id})
            show.tvdb_id = show.tvdb_id or known.tvdb_id
            show.imdb_id = show.imdb_id or known.imdb_id

    def _reshape(self, job: JobRecord, plan: TvPlan, episodes: list[Episode]) -> str | None:
        """Re-pick the episode titles using the season's real episode lengths.

        Before the show is known, episodes are guessed as the most common title length,
        which goes wrong when the disc has more short extras than episodes, or a long
        pilot ("Boardwalk Empire" S1 disc 1 has a 72 min pilot, a 52 min episode, three
        4 min extras). Left alone when the roles were changed by hand."""
        runtimes = [e.runtime for e in episodes if e.runtime]
        if not runtimes or not job.titles:
            return None
        bluray = job.disc.kind in (DiscKind.bluray, DiscKind.uhd)
        default = shape.analyse(job.titles, bluray)
        if {m.title_index: m.role for m in plan.mapping} != {i: self._default_role(r) for i, r in default.roles.items()}:
            return None
        lo, hi = min(runtimes), max(runtimes)
        sh = shape.analyse(job.titles, bluray, (lo * 60 * (1 - RUNTIME_WINDOW), hi * 60 * (1 + RUNTIME_WINDOW)))
        if sh.roles == default.roles or not sh.episodes:
            return None
        plan.mapping = self._shape_mapping(job, sh)
        span = f"{lo} min" if lo == hi else f"{lo}–{hi} min"
        return (f"Episodes in this season run {span}: titles {', '.join(map(str, sh.episodes))} "
                "are the episodes")

    def _check_plan(self, job: JobRecord) -> None:
        plan = job.tv
        known = {(e.season, e.episode): e for e in plan.episodes} if plan.episodes else None
        plan.problems = ([] if plan.show else ["pick the show"]) + tvmap.problems(plan.mapping, known)
        for sp in plan.splits:
            if sp.state == "waiting":
                plan.problems.append(f"title {sp.title_index} holds several episodes, and its chapters are read "
                                     "once it's ripped")
            elif sp.state == "failed":
                plan.problems.append(f"title {sp.title_index} can't be split ({sp.error}), so keep it whole")
        if not job.rip.all_titles and job.rip.state == RipState.done:
            plan.problems.append("only the main title was ripped, so put the disc back to rip the episodes")
        plan.consistent = not plan.problems

    # --- user actions -----------------------------------------------------------------

    def _tv_job(self, job_id: int) -> JobRecord:
        job = self.db.get_job(job_id)
        if job is None:
            raise ValueError("no such job")
        if job.state not in (JobState.review, JobState.identifying, JobState.ripping, JobState.failed,
                             JobState.checking, JobState.clash):
            raise ValueError("the episode mapping can only change while the job waits for review")
        return job

    async def tv_propose(self, job_id: int, show_key: str, season: int | None, disc_number: int | None,
                         start_episode: int | None, compilation: bool) -> JobRecord:
        """Propose a mapping for the show with this key ("tmdb:1399", "tvmaze:82"; a bare
        number is a TMDB id)."""
        source, show_id = parse_show_key(str(show_key))
        job = self._tv_job(job_id)
        if job.media != MediaKind.tv:
            await self.set_media(job_id, MediaKind.tv)
        self._mark_edited(job_id)
        tmdb = self._tmdb()
        if source == "tmdb" and not tmdb:
            raise ValueError("No TMDB API key set")
        try:
            known = next((s for s in (job.tv.candidates if job.tv else []) if s.key == f"{source}:{show_id}"), None)
            show = await self._show_by_id(source, show_id, tmdb)
            if known:
                show.score, show.evidence, show.providers = known.score, known.evidence, known.providers
            await self._propose(job_id, show, season, disc_number, start_episode, compilation, tmdb)
        except LOOKUP_ERRORS as e:
            raise ValueError(str(e)) from None
        finally:
            if tmdb:
                await tmdb.aclose()
        await self._analyse_split(job_id)  # long titles already ripped are cut now
        return self.db.get_job(job_id)

    async def _show_by_id(self, source: str, show_id: int, tmdb: Tmdb | None) -> Show:
        if source == "tmdb":
            return tmdb_tv.to_show(await tmdb.tv_show(show_id))
        if source == "tvmaze":
            maze = Tvmaze()
            try:
                found = await maze.show(show_id)
            finally:
                await maze.aclose()
            if not found:
                raise TvmazeError("TVmaze has no such show")
            return keyless.tvmaze_show(found)
        tvdb = self._tvdb()
        if not tvdb:
            raise TvdbError("No TheTVDB API key set")
        try:
            return keyless.tvdb_show(await tvdb.series(show_id), tvdb.language)
        finally:
            await tvdb.aclose()

    async def tv_search(self, query: str) -> list[Show]:
        tmdb = self._tmdb()
        if not tmdb and not self._keyless_sources():
            raise ValueError("No TV show search is on: set a TMDB key, or switch TVmaze on in Settings → Identify")
        try:
            return await self._find_shows(tmdb, query, None)
        except LOOKUP_ERRORS as e:
            raise ValueError(str(e)) from None
        finally:
            if tmdb:
                await tmdb.aclose()

    async def tv_match_text(self, job_id: int, text: str) -> JobRecord:
        """Map titles, in disc order, to episodes named in pasted text (a case's run order)."""
        job = self._tv_job(job_id)
        plan = job.tv
        if not plan or not plan.show:
            raise ValueError("pick the show first")
        names = tvmap.split_names(text)
        if not names:
            raise ValueError("no episode names in the text")
        plan.user_edited = True
        episodes = plan.episodes
        if not plan.compilation:
            tmdb = self._tmdb()
            if not tmdb and plan.show.source == "tmdb":
                raise ValueError("No TMDB API key set")
            try:
                episodes = await self._all_episodes(plan.show, tmdb)
            except LOOKUP_ERRORS as e:
                raise ValueError(str(e)) from None
            finally:
                if tmdb:
                    await tmdb.aclose()
        job = self.db.get_job(job_id)
        plan = job.tv
        matches = tvmap.match_names(names, episodes)
        ordered = self._ordered_episode_titles(job)
        by_index = {m.title_index: m for m in plan.mapping}
        unmatched = [n for n, ep, _ in matches if ep is None]
        for t, (name, ep, sim) in zip(ordered, matches, strict=False):
            m = by_index[t.index]
            if ep:
                m.episodes = [EpisodeRef(season=ep.season, episode=ep.episode)]
                m.note = f"“{name}” matches “{ep.name}”" + (f" ({sim:.0%})" if sim < 0.99 else "")
                m.by_hand = True  # the run order given is the user's, over the box's picks
            else:
                m.episodes = []
                m.note = f"no episode called “{name}”"
        seasons = {e.season for _, e, _ in matches if e}
        contiguous = _contiguous([e for _, e, _ in matches if e])
        plan.compilation = not contiguous or len(seasons) > 1
        plan.episodes = episodes if plan.compilation else [e for e in episodes if e.season in seasons]
        if not plan.compilation and seasons:
            plan.season = seasons.pop()
        plan.order_from = "names"
        self._event(job, "info", f"Matched {len(matches) - len(unmatched)} of {len(names)} names to episodes"
                    + (f"; no match for {', '.join(unmatched[:3])}" if unmatched else ""))
        if len(names) != len(ordered):
            self._event(job, "warning", f"{len(names)} names for {len(ordered)} episode titles")
        self._check_plan(job)
        self._save(job)
        return job

    def _mark_edited(self, job_id: int) -> None:
        job = self.db.get_job(job_id)
        if job and job.tv:
            job.tv.user_edited = True
            self._save(job)

    def _auto_check(self, job: JobRecord) -> tuple[bool, list[str]]:
        """Can this TV disc be confirmed without review? (ok, reasons)."""
        plan = job.tv
        why: list[str] = []
        if not self.s.tv_auto_confirm:
            return False, ["auto-confirm is off"]
        if not plan or not plan.show:
            return False, ["no show"]
        if plan.splits:
            return False, ["a long title holds several episodes, so where it's cut needs a review"]
        if plan.user_edited:
            return False, ["changed by hand"]
        if plan.from_scan:
            return False, ["set from the cover scan, so it needs a look"]
        if plan.compilation:
            return False, ["a compilation, so episodes need picking by hand"]
        if plan.show.excluded or plan.show.score < self.s.tv_auto_threshold:
            return False, [f"show match {plan.show.score:.2f} is below {self.s.tv_auto_threshold:.2f}"]
        why.append(f"{plan.show.name} matched at {plan.show.score:.2f}")
        if plan.disc_db:
            why.append("TheDiscDB knows this disc")
        elif plan.label_placed:
            why.append(f"label says season {plan.season}, disc {plan.disc_number}")
        elif plan.continues_collection:
            why.append(f"continues the collection at episode {plan.start_episode}")
        else:
            return False, [*why, "nothing says which season and disc this is"]
        if not plan.consistent:
            return False, [*why, *plan.problems[:2]]
        eps = [m for m in plan.mapping if m.role == TitleRole.episode]
        bad = [m.title_index for m in eps if m.match_cost is None or m.match_cost > 0.2]
        if bad:
            return False, [*why, "runtimes disagree for titles " + ", ".join(map(str, bad))]
        why.append(f"all {len(eps)} episode runtimes match")
        has_play_all = any(m.role == TitleRole.play_all for m in plan.mapping)
        if plan.order_verified is False:
            return False, [*why, "episode order did not match the play-all"]
        if plan.disc_db:
            why.append("TheDiscDB says which title is which episode")
        elif plan.order_verified is True:
            why.append("order verified against the play-all")
        elif has_play_all and not plan.continues_collection:
            return False, [*why, "waiting for the order check"]
        elif not has_play_all and not plan.continues_collection:
            return False, [*why, "no play-all to check the order against"]
        else:
            why.append("continues a confirmed earlier disc")
        return True, why

    async def _maybe_auto_confirm(self, job_id: int) -> None:
        job = self.db.get_job(job_id)
        if not job or job.media != MediaKind.tv or not job.tv or job.identity or job.tv.auto_confirmed or job.refile:
            return
        if job.state not in (JobState.review, JobState.ripping, JobState.identifying):
            return
        ok, why = self._auto_check(job)
        job.tv.auto_evidence = why
        self._save(job)
        if ok:
            self._event(job, "info", "Confirming without review, because " + "; ".join(why))
            await self.tv_confirm(job_id, [], source="auto")
        elif "waiting for the order check" not in why or job.rip.state in ("done", "failed"):
            # Final, so a human is needed (the review notification was held back until now).
            self._spawn_notify(self.db.get_job(job_id), "review")

    async def tv_set_mapping(self, job_id: int, updates: list[TitleMapUpdate]) -> JobRecord:
        """Titles changed by hand in the review, kept before it's confirmed (on any device):
        the cover scan's picks leave them alone from now on."""
        job = self._tv_job(job_id)
        plan = job.tv
        if not plan or not plan.show:
            raise ValueError("pick the show first")
        by_index = {m.title_index: m for m in plan.mapping}
        for u in updates:
            m = by_index.get(u.title_index)
            if m is None:
                raise ValueError(f"title {u.title_index} is not on this disc")
            if any(sp.title_index == u.title_index for sp in plan.splits):
                raise ValueError(f"title {u.title_index} is split into parts: pick the parts' episodes, or keep "
                                 "it whole first")
            m.role, m.episodes = u.role, list(u.episodes) if u.role == TitleRole.episode else []
            m.by_hand, m.note = True, None
        plan.user_edited = True
        self._check_plan(job)
        self._save(job)
        self.rematch(job_id)
        return self.db.get_job(job_id)

    async def tv_confirm(self, job_id: int, updates: list[TitleMapUpdate], source: str = "user") -> JobRecord:
        job = self._tv_job(job_id)
        plan = job.tv
        if not plan or not plan.show:
            raise ValueError("pick the show first")
        by_index = {m.title_index: m for m in plan.mapping}
        for u in updates:
            m = by_index.get(u.title_index)
            if m is None:
                raise ValueError(f"title {u.title_index} is not on this disc")
            m.role, m.episodes = u.role, list(u.episodes)
            if u.role != TitleRole.episode:
                m.episodes = []
        self._check_plan(job)
        if not plan.consistent:
            self._save(job)
            raise ValueError("; ".join(plan.problems))
        if job.state == JobState.checking:
            # Out to be checked, which is fine if every title the mapping needs is already ripped.
            missing = self.missing_titles(job)
            if missing:
                raise ValueError(f"title(s) {', '.join(map(str, missing))} aren't ripped yet, so put the disc back first")
            job.rip.state, job.rip.paused, job.rip.all_titles = RipState.done, False, True
            job.checking_from = None
            self._set_state(job, JobState.review, "Confirmed with the disc out, because every title needed is ripped")
        show = plan.show
        job.identity = Candidate(source=show.source, tmdb_id=show.tmdb_id, title=show.name, year=show.year,
                                 poster_url=show.poster_url, score=show.score)
        job.identity_source = source
        plan.auto_confirmed = source == "auto"
        job.problem = None
        job.destination = str(naming.show_folder(self.s.tv, show))
        if job.refile:  # finished already, so its files move to the new names
            return await self._refile(job)
        n = sum(len(m.episodes) for m in plan.mapping)
        how = "Auto-confirmed" if source == "auto" else "Confirmed"
        self._event(job, "identity", f"{how}: {show.name}, {n} episodes"
                    + (f", season {plan.season}" if plan.season is not None else "")
                    + (f", disc {plan.disc_number}" if plan.disc_number else ""))
        self._add_to_collection(job)
        if job.state == JobState.failed:
            self._set_state(job, JobState.identifying, "Re-identified by hand")
        self._save(job)
        if source == "user":
            self._maybe_contribute(job.id)
        if self._check_clash(job):
            return self.db.get_job(job_id)
        if job.state == JobState.clash:  # the confirmed mapping clashes with nothing now
            job.clash = None
            await self._leave_clash(job, self.drives.get(job.drive_id))
            return self.db.get_job(job_id)
        await self._advance(job.id)
        return self.db.get_job(job_id)

    def missing_titles(self, job: JobRecord) -> list[int]:
        """Titles the mapping copies that haven't been ripped (yet)."""
        unsplit = {sp.title_index for sp in job.tv.splits if sp.state != "split"}
        needed = {m.part_of if m.part_of in unsplit else m.title_index
                  for m in job.tv.mapping if m.role in (TitleRole.episode, TitleRole.extra)}
        return sorted(i for i in needed if i not in job.rip.files or not Path(job.rip.files[i]).exists())

    async def set_media(self, job_id: int, media: MediaKind) -> JobRecord:
        """The user says this disc is TV (or a film) after all."""
        job = self._tv_job(job_id)
        if job.media == media:
            return job
        self._refiling(job, f"become {'a TV disc' if media == MediaKind.tv else 'a film'}")
        if job.mode == "backup":
            raise ValueError("a full disc backup keeps the whole disc, so it has no episodes to map")
        d = self.drives.get(job.drive_id)
        job.media = media
        job.identity = job.identity_source = job.destination = None
        self._event(job, "info", f"Marked as {'a TV disc' if media == MediaKind.tv else 'a film'}")
        if media == MediaKind.tv:
            job.tv = self._tv_plan_for(job)
            if not job.rip.all_titles:
                await self._rerip_all(job, d)
                job = self.db.get_job(job_id)
        else:
            if job.rip.all_titles and job.main_title:
                job.rip.file = job.rip.files.get(job.main_title.index)
        if job.state == JobState.failed:
            job.state = JobState.review
        self._save(job)
        self._spawn(d, self._identify(job_id, use_cache=False), job_id)
        return job

    def _tv_plan_for(self, job: JobRecord) -> TvPlan:
        """A first TV plan for a disc that didn't look like TV."""
        bluray = job.disc.kind in (DiscKind.bluray, DiscKind.uhd)
        sh = shape.analyse(job.titles, bluray)
        if not sh.episodes:  # nothing looks like an episode, so let the user map every title
            sh = shape.DiscShape(roles={t.index: TitleRole.episode for t in job.titles})
        tvl = parse_tv_label(job.disc.label)
        return TvPlan(season=tvl.season, disc_number=tvl.disc or tvl.volume, mapping=self._shape_mapping(job, sh))

    async def _use_tv_preset(self, job: JobRecord, p: Preset) -> bool:
        """Map the episodes of the show (and season, disc) the user named before the scan finished."""
        tmdb = self._tmdb()
        if not tmdb and p.show.source == "tmdb":
            self._event(job, "warning", f"No TMDB API key set, so {p.show.name}, the show you picked, can't be looked up")
            self._save(job)
            return False
        if job.media != MediaKind.tv:
            job.media = MediaKind.tv  # before the rip starts, so it rips every title
            job.tv = self._tv_plan_for(job)
        show = p.show.model_copy(update={
            "score": 1.0, "excluded": False, "evidence": [Evidence(ok=True, text="Picked by you")]})
        job.tv.candidates = [show]
        self._set_state(job, JobState.identifying, f"Identifying from what you said before the scan finished: "
                                                   f"{show.name}")
        try:
            await self._propose(job.id, show, p.season, p.disc_number, p.start_episode,
                                None if p.season is None else False, tmdb)
        except LOOKUP_ERRORS as e:
            self._event(job, "warning", str(e))
        finally:
            if tmdb:
                await tmdb.aclose()
        job = self.db.get_job(job.id)
        if job.tv.show and p.season is not None and (p.disc_number or p.start_episode):
            job.tv.label_placed = True  # placed by the user, which is as good as the label
        self._save(job)
        self._set_state(job, JobState.review, "Waiting for review of the episode mapping")
        await self._maybe_auto_confirm(job.id)
        return True

    async def _rerip_all(self, job: JobRecord, d) -> None:
        """Rip every title (the first rip only took the main one)."""
        if d and d.rip:
            d.rip.cancel()
            for _ in range(100):
                if d.rip is None:
                    break
                await asyncio.sleep(0.1)
        if not d or await self._drive_status(d) != "disc":
            job.rip.state = RipState.done if job.rip.file else job.rip.state
            self._event(job, "warning", "Put the disc back in to rip the episodes")
            self._save(job)
            return
        job.rip = type(job.rip)()
        self._save(job)
        self._spawn(d, self._rip(job.id), job.id)

    # --- rip, thumbnails, order check ---------------------------------------------------

    def _rip_args(self, job: JobRecord) -> tuple[str, int, str]:
        """(MakeMKV title argument, bytes expected, description)."""
        if job.media == MediaKind.tv:
            return "all", sum(t.size_bytes for t in job.titles), f"Ripping all {len(job.titles)} titles to scratch"
        mt = job.main_title
        return str(mt.index), mt.size_bytes, f"Ripping title {mt.index} to scratch"

    def _collect_files(self, job: JobRecord, out_dir: Path) -> dict[int, str]:
        files: dict[int, str] = {}
        for t in job.titles:
            candidates = [out_dir / t.output_file] if t.output_file else []
            candidates += sorted(out_dir.glob(f"*_t{t.index:02d}.mkv"))
            hit = next((p for p in candidates if p.exists()), None)
            if hit:
                files[t.index] = str(hit)
        return files

    async def _after_tv_rip(self, job_id: int) -> None:
        job = self.db.get_job(job_id)
        if not job or not job.tv or not job.rip.all_titles:
            return
        thumbs = self.work_dir(job) / "thumbs"
        for idx, f in job.rip.files.items():
            out = thumbs / f"{idx}.jpg"
            if not out.exists():
                length = await verify.duration(self.s.ffprobe, Path(f))
                if length is None:
                    t = next((t for t in job.titles if t.index == idx), None)
                    length = t.duration_s if t else None
                await verify.thumbnail(self.s.ffmpeg, Path(f), out, length=length)
        await self._analyse_split(job_id)
        job = self.db.get_job(job_id)
        if not self.s.verify_order or job.tv.splits:
            await self._maybe_auto_confirm(job_id)
            return
        plan = job.tv
        pa = next((m.title_index for m in plan.mapping if m.role == TitleRole.play_all), None)
        ordered = [t.index for t in self._ordered_episode_titles(job)]
        if pa is None or pa not in job.rip.files or not all(i in job.rip.files for i in ordered):
            note = "no play-all on this disc to check the order against" if pa is None else \
                "some titles were not ripped"
            check = verify.OrderCheck(None, note, {})
        else:
            check = await verify.verify_order(self.s.ffmpeg, self.s.ffprobe, Path(job.rip.files[pa]),
                                              [(i, Path(job.rip.files[i])) for i in ordered])
        job = self.db.get_job(job_id)
        job.tv.order_verified, job.tv.order_note = check.verified, check.note
        self._event(job, "info" if check.verified is not False else "warning", f"Episode order: {check.note}")
        self._save(job)
        await self._maybe_auto_confirm(job_id)

    def thumbnail_path(self, job_id: int, title_index: int) -> Path | None:
        job = self.db.get_job(job_id)
        if not job:
            return None
        first = self._part_mark(job, title_index)
        if first is not None:  # the frame after the part's first mark
            return self.split_thumb_path(job_id, job.tv.split_of(title_index).title_index, first)
        p = self.work_dir(job) / "thumbs" / f"{int(title_index)}.jpg"
        return p if p.exists() else None

    async def title_thumb(self, job_id: int, title_index: int) -> Path | None:
        """``thumbnail_path``, grabbing a part's frame now if it wasn't already."""
        job = self.db.get_job(job_id)
        first = self._part_mark(job, title_index) if job else None
        if first is not None:
            return await self.split_thumb(job_id, job.tv.split_of(title_index).title_index, first)
        return self.thumbnail_path(job_id, title_index)

    def _part_mark(self, job: JobRecord, title_index: int) -> int | None:
        m = next((m for m in job.tv.mapping if m.title_index == title_index), None) if job.tv else None
        sp = job.tv.split_of(title_index) if m and m.part_of is not None else None
        if not sp:
            return None
        return next((c for a, _, c in tvsplit.parts(sp.chapters, sp.duration_s, sp.cuts) if round(a, 2) == m.start_s),
                    None)

    def split_thumb_path(self, job_id: int, title_index: int, mark: int) -> Path | None:
        job = self.db.get_job(job_id)
        sp = job.tv.split_of(title_index) if job and job.tv else None
        if not sp or sp.title_index != title_index or not 0 <= mark < len(sp.chapters):
            return None
        p = self._mark_thumb(job, title_index, sp.chapters[mark])
        return p if p.exists() else None

    async def split_thumb(self, job_id: int, title_index: int, mark: int) -> Path | None:
        """The frame just after a mark, grabbed now if it wasn't already (a scan finds many)."""
        p = self.split_thumb_path(job_id, title_index, mark)
        if p:
            return p
        job = self.db.get_job(job_id)
        sp = job.tv.split_of(title_index) if job and job.tv else None
        src = job.rip.files.get(title_index) if sp and sp.title_index == title_index else None
        if not src or not Path(src).exists() or not 0 <= mark < len(sp.chapters):
            return None
        return await self._grab_mark(job, title_index, Path(src), sp.chapters[mark], sp.duration_s)

    # --- finalise -----------------------------------------------------------------------

    def _tv_targets(self, job: JobRecord) -> list[tuple[int, Path, list[Episode] | None]]:
        """(title, destination, episodes or None for an extra) for every title to copy."""
        plan = job.tv
        show = plan.show
        known = {(e.season, e.episode): e for e in plan.episodes}
        out = []
        specials_taken = {e.episode for e in plan.episodes if e.season == 0}
        c = self.db.collection_for_show(show.key)
        if c:
            specials_taken |= {e.episode for d in c.discs if d.job_id != job.id for e in d.episodes if e.season == 0}
        next_special = max(specials_taken, default=0) + 1
        named = self.named_extras(job) if self.s.extras_emby_folders else {}
        bonus = {i: x.bonus for i, x in self.named_extras(job).items() if x.bonus}
        placed: set[Path] = set()
        cut = {sp.title_index for sp in plan.splits}  # copied as its parts, never whole
        for m in plan.mapping:
            if m.title_index in cut:
                continue
            if m.role == TitleRole.episode:
                eps = [known.get(episode_key(e)) or Episode(season=e.season, episode=e.episode) for e in m.episodes]
                out.append((m.title_index, naming.episode_path(self.s.tv, show, eps), eps))
            elif m.role == TitleRole.extra:
                if (b := bonus.get(m.title_index)):
                    # A bonus episode of another show: in that show's season, or as an extra
                    # of this one named after it ("Guess with Jess - Where's My Feather Gone").
                    if self.s.tv_bonus_episodes == "own_show":
                        dest = naming.episode_path(self.s.tv, b.show, [b.episode])
                    else:
                        dest = library.free_path(naming.named_extra_path(self.s.tv, show, "Extra", b.name), placed)
                    placed.add(dest)
                    out.append((m.title_index, dest, None))
                elif (x := named.get(m.title_index)) and self.s.tv_unmatched != "skip":
                    dest = library.free_path(naming.named_extra_path(self.s.tv, show, x.extra_type, x.name), placed)
                    placed.add(dest)
                    out.append((m.title_index, dest, None))
                elif self.s.tv_unmatched == "extras":
                    out.append((m.title_index, naming.extra_path(self.s.tv, show, job.disc.label,
                                                                 m.title_index), None))
                elif self.s.tv_unmatched == "specials":
                    ep = Episode(season=0, episode=next_special,
                                 name=f"{job.disc.label or 'Disc'} extra (title {m.title_index})")
                    next_special += 1
                    out.append((m.title_index, naming.episode_path(self.s.tv, show, [ep]), [ep]))
        return out

    async def _finalise_tv(self, job: JobRecord) -> None:
        self.rematch(job.id)  # the extras the box names, as the listing is now
        job = self.db.get_job(job.id)
        plan = job.tv
        targets = self._tv_targets(job)
        self._set_state(job, JobState.finalising, f"Copying {len(targets)} titles to "
                        f"{naming.show_folder(self.s.tv, plan.show)}")
        self._set_step(job, "Copying to library",
                       self._copy_detail(job, f"{len(targets)} titles to {plan.show.name}"))
        try:
            library.check_library(*self.s.output_root("tv"))
        except library.LibraryError as e:
            self._fail(job, Problem(code="library_missing", message=str(e), actions=["retry"]))
            return
        missing = [i for i, _, _ in targets if i not in job.rip.files or not Path(job.rip.files[i]).exists()]
        if missing:
            self._fail(job, Problem(code="rip_incomplete", message="ripped files missing for titles "
                                    + ", ".join(map(str, missing)), actions=["retry"]))
            return
        overwrite = {f.path for f in job.clash.files} if job.clash and job.clash.decision == "overwrite" else set()
        if self._check_clash(job):  # appeared since the check at identification
            return
        total = sum(Path(job.rip.files[i]).stat().st_size for i, _, _ in targets) or 1
        report = self._copy_progress(job.id, "Copying to library")
        done_before = 0
        copied: dict[int, dict] = {}
        for idx, dest, eps in targets:
            src = Path(job.rip.files[idx])

            def on_copy(done: int, _size: int, verifying: bool, base=done_before) -> None:
                report(2 * base + done, 2 * total, verifying)

            try:
                sha = await asyncio.to_thread(library.copy_verified, src, dest, on_copy, str(dest) in overwrite)
            except FileExistsError:  # appeared during the copy
                self._check_clash(self.db.get_job(job.id))
                return
            except (OSError, library.VerifyError) as e:
                self._fail(job, Problem(code="copy_failed", message=f"Copy to library failed: {e}",
                                        actions=["retry"]))
                return
            done_before += src.stat().st_size
            copied[idx] = {"file": dest.relative_to(naming.show_folder(self.s.tv, plan.show)).as_posix(),
                           "sha256": sha,
                           "episodes": [f"S{e.season:02d}E{e.episode:02d}" for e in eps] if eps else [],
                           "names": [e.name for e in eps] if eps else []}
        job = self.db.get_job(job.id)
        self._event(job, "finalise", f"Copied and verified {len(copied)} titles")
        self._spawn_notify(job, "copied", f"{len(copied)} title{'s' if len(copied) != 1 else ''} to "
                           f"{naming.show_folder(self.s.tv, plan.show)}")
        originals = self._originals(job)
        if originals:
            self._set_step(job, "Copying the kept originals", "Transcoding was set to keep the rips as well")
            await self._copy_originals(job, [(originals[i], p) for i, p, _ in targets if i in originals])
        folder = naming.show_folder(self.s.tv, plan.show)
        meta = naming.disc_meta_folder(self.s.tv, plan.show, job.disc.label, job.id)
        work = self.work_dir(job)
        try:
            library.write_disc_folder(folder, self._tv_disc_json(job, copied), work / "scan.log",
                                      work / "structure", meta=meta)
        except OSError as e:
            self._event(job, "warning", f"Could not write .disc folder: {e}")
        shutil.rmtree(self.scratch_dir(job), ignore_errors=True)
        job.rip.files = {}
        job.destination = str(folder)
        await self._refresh_servers(job, [str(p) for i, p, _ in targets if i in copied])
        job.problem = None
        self._set_state(job, JobState.done, f"Done: {plan.show.name}, {len(copied)} titles")
        self._spawn_notify(job, "done")
        await self.file_scans(self.db.get_job(job.id))

    def _tv_disc_json(self, job: JobRecord, copied: dict[int, dict]) -> dict:
        plan = job.tv
        d = self.drives.get(job.drive_id)
        return {
            "schema": 1, "media": "tv", "label": job.disc.label, "disc_name": job.disc.name,
            "kind": job.disc.kind, "volume_uuid": job.disc.volume_uuid, "size_bytes": job.disc.size_bytes,
            "fingerprints": {"content": job.content_fp, "scan": job.scan_fp, "quick": job.quick_fp},
            "drive": {"id": job.drive_id, "device": job.device,
                      "model": f"{d.hw.vendor} {d.hw.model}" if d else None,
                      "name": self.s.drive_name(job.drive_id), "serial": d.hw.serial if d else None},
            "ripped_at": job.updated_at.isoformat(), "makemkv_version": job.makemkv_version,
            "ripshelf_version": __version__,
            "show": {"tmdb_id": plan.show.tmdb_id, "name": plan.show.name, "year": plan.show.year,
                     **({} if plan.show.source == "tmdb" else {"key": plan.show.key})},
            "season": plan.season, "disc_number": plan.disc_number, "compilation": plan.compilation,
            "order_verified": plan.order_verified, "order_note": plan.order_note,
            "transcode": job.transcode.model_dump() if job.transcode else None,
            "titles": {str(m.title_index): {
                "role": m.role.value, "duration_s": m.duration_s,
                "episodes": [f"S{e.season:02d}E{e.episode:02d}" for e in m.episodes],
                **({"part_of": m.part_of, "start_s": m.start_s} if m.part_of is not None else {}),
                **copied.get(m.title_index, {})} for m in plan.mapping},
            "split": {"title": plan.split.title_index, "chapters": plan.split.chapters,
                      "cuts": plan.split.cuts, "proposed": plan.split.proposed} if plan.split else None,
            "splits": [{"title": sp.title_index, "first_part": sp.base, "chapters": sp.chapters, "cuts": sp.cuts,
                        "proposed": sp.proposed} for sp in plan.splits],
        }

    # --- collections ----------------------------------------------------------------------

    def _add_to_collection(self, job: JobRecord) -> None:
        plan = job.tv
        c = self.db.collection_for_show(plan.show.key) or Collection(id=0, show=plan.show, updated_at=job.updated_at)
        c.show = plan.show
        c.discs = [d for d in c.discs if d.job_id != job.id]
        c.discs.append(CollectionDisc(
            job_id=job.id, season=plan.season, disc_number=plan.disc_number, label=job.disc.label,
            episodes=[e for m in plan.mapping for e in m.episodes], compilation=plan.compilation))
        c.discs.sort(key=lambda d: (d.season if d.season is not None else 999, d.disc_number or 0, d.job_id))
        known = {episode_key(e): e for e in c.known_episodes}
        for e in plan.episodes:
            known[episode_key(e)] = e
        c.known_episodes = sorted(known.values(), key=lambda e: (e.season, e.episode))
        c = self.db.save_collection(c)
        plan.collection_id = c.id

    def _episodes_on_disk(self, show: Show) -> set[tuple[int, int]] | None:
        """The episodes whose files are in the show's folder (by their SxxEyy), or None when
        the TV folder itself can't be seen (a library that's offline isn't empty)."""
        if not self.s.tv.is_dir():
            return None
        folder = naming.show_folder(self.s.tv, show)
        out: set[tuple[int, int]] = set()
        if not folder.is_dir():
            return out
        for p in folder.rglob("*"):
            if p.suffix.lower() not in VIDEO_EXTS or ".disc" in p.parts:
                continue
            for m in EPISODE_CODE.finditer(p.name):
                first, last = int(m.group(2)), int(m.group(3) or m.group(2))
                out |= {(int(m.group(1)), n) for n in range(first, max(first, last) + 1)}
        return out

    def forget_discs(self, job_ids: list[int]) -> None:
        """Take these jobs' discs out of the collections (a collection left with none goes)."""
        ids = set(job_ids)
        for c in self.db.collections():
            keep = [d for d in c.discs if d.job_id not in ids]
            if len(keep) == len(c.discs):
                continue
            if keep:
                c.discs = keep
                self.db.save_collection(c)
            else:
                self.db.delete_collection(c.id)

    def remove_disc(self, collection_id: int, job_id: int) -> CollectionDetail | None:
        """The user's "Remove from the collection" for one disc. None when that was the last."""
        c = self.db.collection(collection_id)
        if not c:
            raise KeyError(collection_id)
        if not any(d.job_id == job_id for d in c.discs):
            raise KeyError(job_id)
        self.forget_discs([job_id])
        return self.collection_detail(collection_id)

    def collection_detail(self, collection_id: int) -> CollectionDetail | None:
        c = self.db.collection(collection_id)
        if not c:
            return None
        here = self._episodes_on_disk(c.show)
        where: dict[tuple[int, int], list[int]] = {}
        gone: set[tuple[int, int]] = set()
        for d in c.discs:
            for e in d.episodes:
                if here is not None and (e.season, e.episode) not in here:
                    gone.add(episode_key(e))
                    continue
                where.setdefault(episode_key(e), []).append(d.job_id)
        for k in gone:
            where.setdefault(k, [])
        seasons: dict[int, list[Episode]] = {}
        for e in c.known_episodes:
            seasons.setdefault(e.season, []).append(e)
        for (s, n) in where:  # mapped but not in TMDB's list (or the list was not fetched)
            if not any(e.episode == n for e in seasons.get(s, [])):
                seasons.setdefault(s, []).append(Episode(season=s, episode=n))
        grids = []
        for s in sorted(seasons, key=lambda s: (s == 0, s)):
            cells = []
            for e in sorted(seasons[s], key=lambda e: e.episode):
                jobs = where.get(episode_key(e), [])
                status = ("gone" if episode_key(e) in gone else "missing") if not jobs else \
                    ("duplicate" if len(jobs) > 1 else "present")
                cells.append(SeasonCell(episode=e, status=status, jobs=jobs))
            grids.append(SeasonGrid(season=s, cells=cells, present=sum(1 for x in cells if x.jobs),
                                    total=len(cells)))
        return CollectionDetail(**c.model_dump(), seasons=grids)


VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts"}
EPISODE_CODE = re.compile(r"S(\d{1,3})E(\d{1,4})(?:-?E(\d{1,4}))?", re.I)


def _same_show(show: Show, match: DiscMatch) -> bool:
    """Is the disc database's show this one? By TMDB or TheTVDB id, or for a show found
    without TMDB, by IMDb id too."""
    if show.tmdb_id is not None and show.tmdb_id == match.tmdb_id:
        return True
    if match.tvdb_id and show.tvdb_id == match.tvdb_id:
        return True
    return bool(show.source != "tmdb" and match.imdb_id and show.imdb_id == match.imdb_id)


def _label_remainder(label_show: str, show_name: str) -> str | None:
    """What a label says beyond the show's name, so "Pippa & Goose Kite Tram" -> "Kite Tram"."""
    if not label_show or not show_name:
        return None
    words = label_show.split()
    n = len(show_name.split())
    if len(words) > n and title_similarity(" ".join(words[:n]), show_name) >= 0.9:
        return " ".join(words[n:])
    return None


def _disc_numbers(job: JobRecord) -> TvLabel:
    """Season, disc and volume from the label, or from the disc name when the label has none."""
    tvl = parse_tv_label(job.disc.label)
    if tvl.season is None and job.disc.name:
        named = split_show_name(job.disc.name)
        if named.season is not None:
            return named
    return tvl


def _clock(s: float) -> str:
    s = int(s)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _contiguous(eps: list[Episode]) -> bool:
    return all(b.season == a.season and b.episode == a.episode + 1 for a, b in zip(eps, eps[1:], strict=False))
