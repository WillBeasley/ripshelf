"""A TV disc's box names the show: its episode names pick the show and season over a
guess from the label, and the episodes it matched go into the mapping for review."""

from __future__ import annotations

from ripshelf import matchjobs
from ripshelf.db import JobRecord, now
from ripshelf.models import (
    DiscKind,
    Episode,
    EpisodeRef,
    JobState,
    Listing,
    ListingDetails,
    ListingItem,
    MediaKind,
    ScanListing,
    Show,
    TitleInfo,
    TitleMap,
    TitleRole,
    TvPlan,
)
from ripshelf.scanner import match

from .test_contribute import put

GOOSE = Show(tmdb_id=61990, name="Pippa & Goose", year=2013, seasons={1: 4}, score=0.8)
KITE = Show(tmdb_id=70001, name="Pippa & Goose: Kite Post", year=2019, seasons={1: 6}, score=0.4)
EPISODES = {
    GOOSE.tmdb_id: [Episode(season=1, episode=n, name=f"The {w} Morning", runtime=15)
                    for n, w in enumerate(["Windy", "Rainy", "Snowy", "Sunny"], 1)],
    KITE.tmdb_id: [Episode(season=1, episode=n, name=f"Pippa and the {w}", runtime=15)
                   for n, w in enumerate(["Lost Kite", "Paper Boat", "Tall Hat", "Big Parcel",
                                          "Muddy Lane", "Silver Bell"], 1)],
}
BOX = ["Kite Post: Big Parcel", "Kite Post: Lost Kite", "Kite Post: The Silver Bell"]


def disc_job(box=BOX) -> JobRecord:
    titles = [TitleInfo(index=i, duration_s=s, size_bytes=1, chapters=4, audio=["DD Stereo English"])
              for i, s in enumerate([890, 890, 890, 2670])]
    mapping = [TitleMap(title_index=i, duration_s=890, role=TitleRole.episode,
                        episodes=[EpisodeRef(season=1, episode=i + 1)]) for i in range(3)]
    mapping.append(TitleMap(title_index=3, duration_s=2670, role=TitleRole.play_all))
    job = JobRecord(id=7, drive_id="fake-ihas122", state=JobState.review, created_at=now(), updated_at=now(),
                    titles=titles, media=MediaKind.tv,
                    tv=TvPlan(show=GOOSE, season=1, mapping=mapping, candidates=[GOOSE, KITE],
                              episodes=EPISODES[GOOSE.tmdb_id]))
    job.disc.label, job.disc.kind = "PG_KP", DiscKind.dvd
    job.listing = ScanListing(state="done", found=Listing(
        details=ListingDetails(title="Pippa & Goose: Kite Post"),
        items=[ListingItem(kind="episode", title=t, runtime_min=15) for t in box]))
    return job


def test_shared_prefixes():
    assert match.shared_prefix(BOX) == "kite post:"
    assert match.shared_prefix([e.name for e in EPISODES[KITE.tmdb_id]]) == "pippa and the"
    assert match.core_name("Pippa and the Big Parcel", "pippa and the") == "Big Parcel"
    assert match.core_name("Kite Post: The Silver Bell", "kite post:") == "Silver Bell"


def test_names_match_past_the_show_and_the_article():
    eps = EPISODES[KITE.tmdb_id]
    got = [match.episode_for(n, eps, None, "kite post:", "pippa and the")[0] for n in BOX]
    assert [e.episode for e in got] == [4, 1, 6]


def fake_tmdb(monkeypatch, h):
    proposed = []

    class Client:
        async def aclose(self):
            pass

    async def all_episodes(tmdb, show):
        return EPISODES[show.tmdb_id]

    async def find_shows(tmdb, name, runtime, *a, **k):
        return [KITE] if "kite" in name.lower() else []

    async def propose(job_id, show, season, disc, start, compilation, tmdb):
        proposed.append((show.tmdb_id, season))
        job = h.db.get_job(job_id)
        job.tv.show, job.tv.season, job.tv.episodes = show, season, EPISODES[show.tmdb_id]
        h.db.save_job(job)

    monkeypatch.setattr(h.m, "_tmdb", lambda: Client())
    monkeypatch.setattr(matchjobs.tmdb_tv, "all_episodes", all_episodes)
    monkeypatch.setattr(matchjobs.tmdb_tv, "find_shows", find_shows)
    monkeypatch.setattr(h.m, "_propose", propose)
    return proposed


async def test_the_box_picks_the_show_and_its_episodes(harness, settings, monkeypatch):
    async with harness() as h:
        proposed = fake_tmdb(monkeypatch, h)
        job = put(h, disc_job())
        assert await h.m.fix_show_from_scan(job.id)
        assert proposed == [(KITE.tmdb_id, 1)]
        job = h.db.get_job(job.id)
        assert job.tv.show.tmdb_id == KITE.tmdb_id and job.tv.from_scan
        assert any("cover scan" in e.text for e in job.tv.show.evidence)
        eps = {m.title_index: [e.episode for e in m.episodes] for m in job.tv.mapping if m.role == TitleRole.episode}
        assert eps == {0: [4], 1: [1], 2: [6]}
        assert job.identity is None and job.state == JobState.review  # reviewed, not confirmed
        assert h.m._auto_check(job)[0] is False
        assert not await h.m.fix_show_from_scan(job.id)  # nothing more to change


async def test_too_few_names_leave_the_show(harness, settings, monkeypatch):
    async with harness() as h:
        proposed = fake_tmdb(monkeypatch, h)
        job = put(h, disc_job(["Kite Post: Big Parcel", "Something Else Entirely", "Another One"]))
        await h.m.fix_show_from_scan(job.id)
        assert proposed == [] and h.db.get_job(job.id).tv.show.tmdb_id == GOOSE.tmdb_id


async def test_a_mapping_changed_by_hand_is_left_alone(harness, settings, monkeypatch):
    async with harness() as h:
        proposed = fake_tmdb(monkeypatch, h)
        job = disc_job()
        job.tv.user_edited = True
        job = put(h, job)
        assert not await h.m.fix_show_from_scan(job.id)
        assert proposed == []


async def test_a_shorter_bonus_title_is_not_an_episode(harness, settings, monkeypatch):
    """Four alike titles and a shorter one at the end; the box lists the four episodes
    and then a bonus. The bonus gets the shorter title, whatever the box's order says,
    and the title mapped as an episode before is an extra now."""
    async with harness() as h:
        fake_tmdb(monkeypatch, h)
        box = [*BOX, "Kite Post: Muddy Lane"]
        job = disc_job(box)
        job.titles = [*job.titles[:3], job.titles[0].model_copy(update={"index": 4}),
                      job.titles[0].model_copy(update={"index": 5, "duration_s": 600}), job.titles[3]]
        job.titles[-1].index = 6
        job.tv.mapping = [TitleMap(title_index=i, duration_s=t.duration_s, role=TitleRole.episode,
                                   episodes=[EpisodeRef(season=1, episode=n)])
                          for n, (i, t) in enumerate(((t.index, t) for t in job.titles[:5]), 1)]
        job.listing.found.items = [ListingItem(kind="episode", title=t) for t in box] + \
            [ListingItem(kind="extra", title="Bonus: Goose Tales", extra_type="Other")]
        job = put(h, job)
        assert await h.m.fix_show_from_scan(job.id)
        job = h.db.get_job(job.id)
        eps = {m.title_index: [e.episode for e in m.episodes] for m in job.tv.mapping if m.role == TitleRole.episode}
        assert eps == {0: [4], 1: [1], 2: [6], 4: [5]}
        assert next(m for m in job.tv.mapping if m.title_index == 5).role == TitleRole.extra
