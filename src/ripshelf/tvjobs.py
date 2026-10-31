"""The TV half of the job pipeline (mixed into ``jobs.Manager``).

    scan -> media guess (shape + label) -> rip all titles
         -> identify show, season, disc; propose a title -> episode mapping
         -> review (always, for TV) with the mapping table, confirm
         -> thumbnails + order check against the play-all, once ripped
         -> finalise every mapped title into TV/Show (Year)/Season NN/
"""

from __future__ import annotations

import asyncio
import logging
import shutil
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
    TitleMap,
    TitleMapUpdate,
    TitleRole,
    TvPlan,
)
from .providers import thediscdb
from .providers.tvmaze import Tvmaze, TvmazeError
from .tv import mapping as tvmap
from .tv import naming, shape, tmdb_tv, verify
from .tv.labels import TvLabel, parse_tv_label, split_show_name
from .tv.tvdb import Tvdb, TvdbError

if TYPE_CHECKING:
    from .db import JobRecord

log = logging.getLogger(__name__)

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
        is_tv = sh.looks_like_tv or (tvl.looks_like_tv and len(sh.episodes) >= 2)
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

    async def _find_shows(self, tmdb: Tmdb, name: str, episode_s: float | None) -> list[Show]:
        """Shows for ``name`` from the providers in Settings' "TV shows" list, in order."""
        sources = [n for n in providers.enabled(self.s, "tv") if n != "thediscdb"]
        tvdb = self._tvdb() if "tvdb" in sources else None
        maze = Tvmaze() if "tvmaze" in sources else None
        try:
            return await tmdb_tv.find_shows(tmdb, name, episode_s, tvdb=tvdb, sources=sources, tvmaze=maze) \
                if sources else []
        finally:
            for c in (tvdb, maze):
                if c:
                    await c.aclose()

    async def _disc_match_show(self, tmdb: Tmdb, match: DiscMatch, episode_s: float | None) -> Show | None:
        """The show a disc database says this disc is, from TMDB, scored as certain."""
        tmdb_id = match.tmdb_id or await tmdb.find_id("tv", match.tvdb_id, match.imdb_id)
        if not tmdb_id:
            return None
        show = tmdb_tv.score_show(tmdb_tv.to_show({**await tmdb.tv_show(tmdb_id), "id": tmdb_id}),
                                  [match.title], episode_s)
        show.excluded, show.score, show.providers = False, 1.0, ["thediscdb", "tmdb"]
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
        found = await self._catalogue_lookup(job) if tmdb else None
        if found and found.names:
            name = found.names[0]
        match = job.disc_match if job.disc_match and job.disc_match.media == MediaKind.tv \
            and "thediscdb" in providers.enabled(self.s, "tv") else None
        if not tmdb:
            self._event(job, "warning", "No TMDB API key set, so pick the show by hand")
        elif not name and not match:
            self._event(job, "warning", "The disc label is generic; search for the show by hand")
        else:
            try:
                eps = [t.duration_s for t in self._ordered_episode_titles(job)]
                typical = sorted(eps)[len(eps) // 2] if eps else None
                shows = await self._find_shows(tmdb, name, typical) if name else []
                if name:
                    self._event(job, "info", f"{self._show_sources()}: {len(shows)} shows for “{name}”")
                known = None
                if match:
                    try:
                        known = await self._disc_match_show(tmdb, match, typical)
                    except TmdbError as e:
                        self._event(job, "warning", f"TheDiscDB's show could not be looked up on TMDB: {e}")
                job = self.db.get_job(job_id)  # the rip may have saved while TMDB answered
                plan = job.tv or plan
                if known:
                    shows = [known, *(x for x in shows if x.tmdb_id != known.tmdb_id)]
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
            except TmdbError as e:
                self._event(job, "warning", str(e))
            finally:
                await tmdb.aclose()
        job = self.db.get_job(job_id)
        job.tv = plan if job.tv is None else job.tv
        self._save(job)
        self._set_state(job, JobState.review, "Waiting for review of the episode mapping")
        await self._maybe_auto_confirm(job_id)

    def _next_for(self, show: Show, season: int | None, disc_number: int | None = None,
                  job_id: int | None = None) -> tuple[int | None, int | None, int | None]:
        """(season, next disc number, next episode) continuing the show's collection.

        With the disc number known, only the disc just before it counts. Disc 1 ripped
        after disc 2 starts at episode 1, not after disc 2's episodes."""
        c = self.db.collection_for_show(show.tmdb_id)
        if not c:
            return season, None, None
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
                       start_episode: int | None, compilation: bool | None, tmdb: Tmdb) -> None:
        """Fill the job's TV plan for ``show``, with episodes and a proposed mapping."""
        job = self.db.get_job(job_id)
        plan = job.tv or TvPlan()
        ordered = self._ordered_episode_titles(job)
        others = {m.title_index: m.model_copy(update={"episodes": []}) for m in plan.mapping
                  if m.role != TitleRole.episode}
        pal = any(t.framerate == "25" for t in ordered)
        notes: list[str] = []

        tvl = parse_tv_label(job.disc.label)
        remainder = _label_remainder(tvl.show, show.name)
        if compilation is None:
            compilation = False
            if season is None and remainder:
                every = await tmdb_tv.all_episodes(tmdb, show)
                hit = max(every, key=lambda e: title_similarity(remainder, e.name), default=None)
                if hit and title_similarity(remainder, hit.name) >= COMPILATION_NAME_MIN:
                    compilation = True
                    notes.append(f"Disc is named after “{hit.name}” (S{hit.season:02d}E{hit.episode:02d}), "
                                 "with no season in the label, so a compilation")
        if compilation:
            episodes = await tmdb_tv.all_episodes(tmdb, show)
            plan.compilation, plan.season, plan.start_episode = True, None, None
            plan.season_poster_url = None
            plan.episodes = episodes
            mapping = [TitleMap(title_index=t.index, duration_s=t.duration_s, role=TitleRole.episode,
                                note="pick the episode, or paste the run order from the case")
                       for t in ordered]
            if remainder and ordered:
                hit = max(episodes, key=lambda e: title_similarity(remainder, e.name), default=None)
                if hit and title_similarity(remainder, hit.name) >= COMPILATION_NAME_MIN:
                    mapping[0].episodes = [EpisodeRef(season=hit.season, episode=hit.episode)]
                    mapping[0].note = f"the disc is named “{hit.name}”"
            plan.mapping = sorted([*mapping, *others.values()], key=lambda m: m.title_index)
        else:
            s_guess, disc_guess, start_guess = self._next_for(show, season, disc_number, job_id)
            season = season if season is not None else (s_guess or 1)
            disc_number = disc_number or disc_guess
            episodes, season_poster = await tmdb_tv.season_details(tmdb, show.tmdb_id, season)
            episodes, source = await self._episode_list(show, season, episodes, tmdb)
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
            plan.mapping = tvmap.propose(ordered, others, episodes, plan.start_episode, pal)
            plan.disc_db = False
            mapped = self._disc_match_mapping(job, plan, show)
            if mapped:
                notes.append(mapped)
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
        if job.rip.state == RipState.done and job.rip.all_titles:
            self._spawn(self.drives[job.drive_id], self._after_tv_rip(job.id), job.id)

    def _disc_match_mapping(self, job: JobRecord, plan: TvPlan, show: Show) -> str | None:
        """Map titles to episodes as the disc database's record of this disc says, when
        it is this show and season. Titles it lists as something else stop being episodes."""
        match = job.disc_match
        if not (match and match.media == MediaKind.tv and "thediscdb" in providers.enabled(self.s, "tv")):
            return None
        if show.tmdb_id != match.tmdb_id and not (match.tvdb_id and show.tvdb_id == match.tvdb_id):
            return None
        said = {t.scan_index: t for t in match.titles if t.scan_index is not None}
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

    async def _episode_list(self, show: Show, season: int, episodes: list[Episode],
                            tmdb: Tmdb) -> tuple[list[Episode], str | None]:
        """The season's episodes from the first provider in Settings' "TV episodes" list
        that has them; TMDB's ``episodes`` when none does. Returns the episodes and a note
        on where they came from (None for TMDB, as asked)."""
        notes: list[str] = []
        # TMDB is in hand here (it found the show), whatever Settings says about its key.
        order = [r["name"] for r in providers.lists(self.s)["episodes"]
                 if r["enabled"] and (r["name"] == "tmdb" or providers.configured(self.s, r["name"]))]
        for name in order:
            if name == "tmdb":
                break
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
        return episodes, "; ".join([*notes, "using TMDB's episodes"]) if notes else None

    async def _tvdb_episodes(self, show: Show, season: int, tmdb: Tmdb) -> tuple[list[Episode], str]:
        tvdb = self._tvdb()
        try:
            await self._external_ids(show, tmdb)
            if show.tvdb_id is None:
                return [], "TheTVDB not used, because TMDB has no TheTVDB id for this show"
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

    async def _tvmaze_episodes(self, show: Show, season: int, tmdb: Tmdb) -> tuple[list[Episode], str]:
        maze = Tvmaze()
        try:
            await self._external_ids(show, tmdb)
            found = await maze.lookup(show.tvdb_id, show.imdb_id)
            if not found:
                return [], "TVmaze doesn't have this show"
            got = await maze.episodes(found["id"], season)
            return got, ("Episodes from TVmaze, aired order" if got else f"TVmaze has no season {season}")
        finally:
            await maze.aclose()

    async def _external_ids(self, show: Show, tmdb: Tmdb) -> None:
        """Fill in the show's TheTVDB and IMDb ids from TMDB, when there is no TheTVDB id."""
        if show.tvdb_id is None:
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

    async def tv_propose(self, job_id: int, show_tmdb_id: int, season: int | None, disc_number: int | None,
                         start_episode: int | None, compilation: bool) -> JobRecord:
        job = self._tv_job(job_id)
        if job.media != MediaKind.tv:
            await self.set_media(job_id, MediaKind.tv)
        self._mark_edited(job_id)
        tmdb = self._tmdb()
        if not tmdb:
            raise ValueError("No TMDB API key set")
        try:
            known = next((s for s in (job.tv.candidates if job.tv else []) if s.tmdb_id == show_tmdb_id), None)
            show = tmdb_tv.to_show(await tmdb.tv_show(show_tmdb_id))
            if known:
                show.score, show.evidence, show.providers = known.score, known.evidence, known.providers
            await self._propose(job_id, show, season, disc_number, start_episode, compilation, tmdb)
        except TmdbError as e:
            raise ValueError(str(e)) from None
        finally:
            await tmdb.aclose()
        return self.db.get_job(job_id)

    async def tv_search(self, query: str) -> list[Show]:
        tmdb = self._tmdb()
        if not tmdb:
            raise ValueError("No TMDB API key set")
        try:
            return await self._find_shows(tmdb, query, None)
        except TmdbError as e:
            raise ValueError(str(e)) from None
        finally:
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
            if not tmdb:
                raise ValueError("No TMDB API key set")
            try:
                episodes = await tmdb_tv.all_episodes(tmdb, plan.show)
            except TmdbError as e:
                raise ValueError(str(e)) from None
            finally:
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
            else:
                m.episodes = []
                m.note = f"no episode called “{name}”"
        seasons = {e.season for _, e, _ in matches if e}
        contiguous = _contiguous([e for _, e, _ in matches if e])
        plan.compilation = not contiguous or len(seasons) > 1
        plan.episodes = episodes if plan.compilation else [e for e in episodes if e.season in seasons]
        if not plan.compilation and seasons:
            plan.season = seasons.pop()
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
        if plan.user_edited:
            return False, ["changed by hand"]
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
        if not job or job.media != MediaKind.tv or not job.tv or job.identity or job.tv.auto_confirmed:
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
        job.identity = Candidate(source="tmdb", tmdb_id=show.tmdb_id, title=show.name, year=show.year,
                                 poster_url=show.poster_url, score=show.score)
        job.identity_source = source
        plan.auto_confirmed = source == "auto"
        job.problem = None
        job.destination = str(naming.show_folder(self.s.tv, show))
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
        needed = [m.title_index for m in job.tv.mapping if m.role in (TitleRole.episode, TitleRole.extra)]
        return [i for i in needed if i not in job.rip.files or not Path(job.rip.files[i]).exists()]

    async def set_media(self, job_id: int, media: MediaKind) -> JobRecord:
        """The user says this disc is TV (or a film) after all."""
        job = self._tv_job(job_id)
        if job.media == media:
            return job
        if job.mode == "backup":
            raise ValueError("a full disc backup keeps the whole disc, so it has no episodes to map")
        d = self.drives[job.drive_id]
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
        if not tmdb:
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
        except TmdbError as e:
            self._event(job, "warning", str(e))
        finally:
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
        if d.rip:
            d.rip.cancel()
            for _ in range(100):
                if d.rip is None:
                    break
                await asyncio.sleep(0.1)
        if await self._drive_status(d) != "disc":
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
        if not self.s.verify_order:
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
        p = self.work_dir(job) / "thumbs" / f"{int(title_index)}.jpg"
        return p if p.exists() else None

    # --- finalise -----------------------------------------------------------------------

    def _tv_targets(self, job: JobRecord) -> list[tuple[int, Path, list[Episode] | None]]:
        """(title, destination, episodes or None for an extra) for every title to copy."""
        plan = job.tv
        show = plan.show
        known = {(e.season, e.episode): e for e in plan.episodes}
        out = []
        specials_taken = {e.episode for e in plan.episodes if e.season == 0}
        c = self.db.collection_for_show(show.tmdb_id)
        if c:
            specials_taken |= {e.episode for d in c.discs if d.job_id != job.id for e in d.episodes if e.season == 0}
        next_special = max(specials_taken, default=0) + 1
        for m in plan.mapping:
            if m.role == TitleRole.episode:
                eps = [known.get(episode_key(e)) or Episode(season=e.season, episode=e.episode) for e in m.episodes]
                out.append((m.title_index, naming.episode_path(self.s.tv, show, eps), eps))
            elif m.role == TitleRole.extra:
                if self.s.tv_unmatched == "extras":
                    out.append((m.title_index, naming.extra_path(self.s.tv, show, job.disc.label,
                                                                 m.title_index), None))
                elif self.s.tv_unmatched == "specials":
                    ep = Episode(season=0, episode=next_special,
                                 name=f"{job.disc.label or 'Disc'} extra (title {m.title_index})")
                    next_special += 1
                    out.append((m.title_index, naming.episode_path(self.s.tv, show, [ep]), [ep]))
        return out

    async def _finalise_tv(self, job: JobRecord) -> None:
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
            copied[idx] = {"file": str(dest.relative_to(naming.show_folder(self.s.tv, plan.show))),
                           "sha256": sha,
                           "episodes": [f"S{e.season:02d}E{e.episode:02d}" for e in eps] if eps else [],
                           "names": [e.name for e in eps] if eps else []}
        job = self.db.get_job(job.id)
        self._event(job, "finalise", f"Copied and verified {len(copied)} titles")
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
            "show": {"tmdb_id": plan.show.tmdb_id, "name": plan.show.name, "year": plan.show.year},
            "season": plan.season, "disc_number": plan.disc_number, "compilation": plan.compilation,
            "order_verified": plan.order_verified, "order_note": plan.order_note,
            "transcode": job.transcode.model_dump() if job.transcode else None,
            "titles": {str(m.title_index): {
                "role": m.role.value, "duration_s": m.duration_s,
                "episodes": [f"S{e.season:02d}E{e.episode:02d}" for e in m.episodes],
                **copied.get(m.title_index, {})} for m in plan.mapping},
        }

    # --- collections ----------------------------------------------------------------------

    def _add_to_collection(self, job: JobRecord) -> None:
        plan = job.tv
        c = self.db.collection_for_show(plan.show.tmdb_id) or Collection(id=0, show=plan.show, updated_at=job.updated_at)
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

    def collection_detail(self, collection_id: int) -> CollectionDetail | None:
        c = self.db.collection(collection_id)
        if not c:
            return None
        where: dict[tuple[int, int], list[int]] = {}
        for d in c.discs:
            for e in d.episodes:
                where.setdefault(episode_key(e), []).append(d.job_id)
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
                status = "missing" if not jobs else ("duplicate" if len(jobs) > 1 else "present")
                cells.append(SeasonCell(episode=e, status=status, jobs=jobs))
            grids.append(SeasonGrid(season=s, cells=cells, present=sum(1 for x in cells if x.jobs),
                                    total=len(cells)))
        return CollectionDetail(**c.model_dump(), seasons=grids)


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


def _contiguous(eps: list[Episode]) -> bool:
    return all(b.season == a.season and b.episode == a.episode + 1 for a, b in zip(eps, eps[1:], strict=False))
