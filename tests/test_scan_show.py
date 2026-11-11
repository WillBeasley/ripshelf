"""A TV disc's box names the show: its episode names pick the show and season over a
guess from the label, and the episodes it matched go into the mapping for review."""

from __future__ import annotations

import pytest

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
    TitleSplit,
    TvPlan,
)
from ripshelf.scanner import listing, match, vision
from ripshelf.tv import naming
from ripshelf.tv import split as tvsplit

from .test_contribute import put
from .test_scan_match import DISC_ORDER, TUGBOAT, tugboat_box

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


async def test_use_this_takes_the_box_over_a_hand_edit(harness, settings, monkeypatch):
    async with harness() as h:
        proposed = fake_tmdb(monkeypatch, h)
        job = disc_job()
        job.tv.user_edited = True
        job = put(h, job)
        got = await h.m.use_scan(job.id)
        assert proposed == [(KITE.tmdb_id, 1)] and got.tv.show.tmdb_id == KITE.tmdb_id
        with pytest.raises(ValueError, match="already says the same"):
            await h.m.use_scan(job.id)


async def test_a_job_whose_drive_is_gone(harness, settings):
    """A task for a job whose drive isn't known any more (picking its show after the rip,
    say) still runs."""
    async with harness() as h:
        ran = []

        async def after(job_id):
            ran.append(job_id)

        await h.m._spawn(h.m.drives.get("unplugged"), after(3), 3)
        assert ran == [3] and not h.m._driveless_tasks


# --- a bonus episode of another show ------------------------------------------------------

MOLE = Show(tmdb_id=70002, name="Marigold Mole", year=2010, seasons={1: 26}, episode_runtime=10)
MOLE_EPISODES = [Episode(season=1, episode=n, name=name, runtime=10)
                 for n, name in [(3, "Who Took My Spade?"), (24, "Where's My Hat Gone?")]]


def test_the_box_names_a_bonus_episode():
    from .test_scan_text import lines

    reading = lines("Kite Post: Big Parcel", "Kite Post: Lost Kite", "Plus meet Pippa's friend Marigold",
                    "in an all new series!", "“Wheres my hat gone?”",
                    "“Marigold Mole” TM and © 2010 Mole Pictures Limited", ("front", "Includes Bonus Episode"))
    got = [(it.kind, it.title, it.show) for it in listing.build([reading]).items if it.kind == "extra"]
    assert got == [("extra", "Wheres My Hat Gone?", "Marigold Mole")]
    # Bonus features aren't a bonus episode.
    plain = lines("Bonus features", "“A quiet orchard. A loud summer.”", "“Moonlit Orchard” © 1998")
    assert not any(it.show for it in listing.build([plain]).items)


def test_the_model_names_a_bonus_episode_and_tesseract_its_show():
    raw = {"items": [{"kind": "extra", "title": "“Where's My Hat Gone?”", "number": None, "runtime_min": None,
                      "extra_type": "Other", "disc": None, "show": "Marigold Mole"}],
           "details": {"title": "Pippa & Goose: Kite Post", "year": None, "feature_runtime_min": None,
                       "episode_count": None, "barcode": None, "catalogue_number": None, "region": None,
                       "video_system": None, "aspect_ratio": None, "audio": [], "subtitles": []}}
    it = vision.tidy(raw).items[0]
    assert (it.title, it.show) == ("Where's My Hat Gone?", "Marigold Mole")
    # The model listing the show's name as an extra: folded into Tesseract's bonus item.
    own = Listing(items=[ListingItem(kind="extra", title="Wheres My Hat Gone?", show="Marigold Mole",
                                     extra_type="Other", source="tesseract")])
    theirs = Listing(items=[ListingItem(kind="extra", title="Marigold Mole", extra_type="Other", source="vision")])
    merged = listing.merge(own, [("s1", theirs)], [])
    assert [(i.title, i.show, i.source) for i in merged.items] == [("Wheres My Hat Gone?", "Marigold Mole", "both")]


def bonus_disc() -> JobRecord:
    job = disc_job([*BOX[:2], "Kite Post: The Silver Bell", "Kite Post: Muddy Lane"])
    job.titles = [*job.titles[:3], job.titles[0].model_copy(update={"index": 4}),
                  job.titles[0].model_copy(update={"index": 5, "duration_s": 600})]
    job.tv = TvPlan(show=KITE, season=1, candidates=[KITE], episodes=EPISODES[KITE.tmdb_id],
                    mapping=[TitleMap(title_index=t.index, duration_s=t.duration_s,
                                      role=TitleRole.episode if t.duration_s > 800 else TitleRole.skip,
                                      episodes=[EpisodeRef(season=1, episode=t.index + 1)] if t.duration_s > 800 else [])
                             for t in job.titles])
    job.listing.found.items.append(ListingItem(kind="extra", title="Wheres My Hat Gone?", show="Marigold Mole",
                                               extra_type="Other"))
    return job


@pytest.mark.parametrize("where", ["extra", "own_show"])
async def test_a_bonus_episode_is_ripped_and_filed(harness, settings, monkeypatch, where):
    EPISODES[MOLE.tmdb_id] = MOLE_EPISODES
    settings.tv_bonus_episodes = where
    async with harness() as h:
        fake_tmdb(monkeypatch, h)

        async def find_shows(tmdb, name, runtime, *a, **k):
            return [MOLE] if "mole" in name.lower() else [KITE] if "kite" in name.lower() else []

        monkeypatch.setattr(matchjobs.tmdb_tv, "find_shows", find_shows)
        job = put(h, bonus_disc())
        assert await h.m.fix_show_from_scan(job.id)
        job = h.db.get_job(job.id)
        assert [b.episode.episode for b in job.tv.bonus] == [24]
        x = next(x for x in job.scan_match.matches if x.bonus)
        assert (x.title_index, x.name) == (5, "Marigold Mole - Where's My Hat Gone?")
        row = next(r for r in job.tv.mapping if r.title_index == 5)
        assert row.role == TitleRole.extra and "Marigold Mole S01E24" in row.note
        eps = {r.title_index: [e.episode for e in r.episodes] for r in job.tv.mapping if r.role == TitleRole.episode}
        assert eps == {0: [4], 1: [1], 2: [6], 4: [5]}
        targets = {i: p for i, p, _ in h.m._tv_targets(job)}
        if where == "extra":
            assert targets[5] == (naming.show_folder(settings.tv, KITE) / "extras"
                                  / "Marigold Mole - Where's My Hat Gone.mkv")
        else:
            assert targets[5] == settings.tv / "Marigold Mole (2010)" / "Season 01" / \
                "Marigold Mole - S01E24 - Where's My Hat Gone.mkv"
        assert not await h.m.fix_show_from_scan(job.id)  # looked up once


# --- the box read again: what a second reading of it got wrong ---------------------------


def test_a_symbol_misread_before_a_title_keeps_the_column():
    from .test_scan_text import lines

    reading = lines("Join Pippa and Goose on 4 new Kite Post adventures. Watch them",
                    "deliver parcels across the valley and find out what happens!",
                    "Kite Post: A Big Parcel", "©& Kite Post: A Lost Kite", "Kite Post: Silver Bell",
                    "Kite Post: A Muddy Lane", "© 2011 Mole Pictures Limited")
    got = [it.title for it in listing.build([reading]).items if it.kind == "episode"]
    assert got == ["Kite Post: A Big Parcel", "Kite Post: A Lost Kite", "Kite Post: Silver Bell",
                   "Kite Post: A Muddy Lane"]


def test_episodes_alike_but_for_their_ends_are_all_kept():
    """The model's four episodes, each starting with the same long words, are four, not two."""
    p = "Pippa's Kite Post Special Delivery: "
    names = [p + "A Big Parcel", p + "A Lost Kite", p + "Silver Bells", p + "A Muddy Lane"]
    own = Listing(items=[ListingItem(kind="episode", title=n, source="tesseract") for n in names[:2]])
    theirs = Listing(items=[ListingItem(kind="episode", title=n, source="vision") for n in names])
    merged = listing.merge(own, [("s1", theirs)], [])
    assert [(i.title, i.source) for i in merged.items] == [
        (names[0], "both"), (names[1], "both"), (names[2], "vision"), (names[3], "vision")]


def test_the_model_naming_the_discs_own_show_as_the_bonus_show():
    raw = {"items": [{"kind": "extra", "title": "Marigold Mole", "number": None, "runtime_min": None,
                      "extra_type": "Other", "disc": None, "show": "Pippa & Goose"}],
           "details": {"title": "Pippa & Goose: Kite Post", "year": None, "feature_runtime_min": None,
                       "episode_count": 4, "barcode": None, "catalogue_number": None, "region": None,
                       "video_system": None, "aspect_ratio": None, "audio": [], "subtitles": []}}
    assert vision.tidy(raw).items[0].show is None
    # A reading kept from before still folds into Tesseract's bonus item.
    own = Listing(items=[ListingItem(kind="extra", title="Where's My Hat Gone?", show="Marigold Mole",
                                     extra_type="Other", source="tesseract")])
    theirs = Listing(items=[ListingItem(kind="extra", title="Marigold Mole", show="Pippa & Goose", source="vision")])
    merged = listing.merge(own, [("s1", theirs)], [])
    assert [(i.title, i.show) for i in merged.items] == [("Where's My Hat Gone?", "Marigold Mole")]


async def test_a_box_read_short_of_its_episodes_leaves_the_rest(harness, settings, monkeypatch):
    """The box says it has four episodes and two were read: those two are set, and the
    titles mapped as the other episodes stay episodes, not extras."""
    async with harness() as h:
        fake_tmdb(monkeypatch, h)
        job = disc_job(BOX[:2])
        job.tv = job.tv.model_copy(update={"show": KITE, "episodes": EPISODES[KITE.tmdb_id], "candidates": [KITE]})
        job.listing.found.details.episode_count = 3
        job = put(h, job)
        h.m.rematch(job.id)  # as reading the box does
        await h.m.fix_show_from_scan(job.id)
        job = h.db.get_job(job.id)
        placed = {x.title_index: x.episodes for x in job.scan_match.matches if x.episodes}
        rows = {r.title_index: r for r in job.tv.mapping}
        assert sorted(e.episode for eps in placed.values() for e in eps) == [1, 4]
        assert all(rows[i].episodes == eps for i, eps in placed.items())
        rest = [r for i, r in rows.items() if i not in placed and i < 3]
        assert len(rest) == 1 and rest[0].role == TitleRole.episode  # not read on the box: still an episode
        used = [(e.season, e.episode) for r in rows.values() for e in r.episodes]
        assert len(used) == len(set(used))  # no episode on two titles


def test_what_the_model_copies_onto_every_item_is_dropped():
    """The model put the whole disc's "61 mins" and "disc 1" against each episode of a
    one-disc box: no title is 61 minutes, and the box isn't split by disc."""
    names = ["Muddy Morning", "Windy Kite", "Lost Bell", "Big Parcel"]
    theirs = Listing(details=ListingDetails(feature_runtime_min=61),
                     items=[ListingItem(kind="episode", title=n, runtime_min=61, disc=1, source="vision") for n in names])
    merged = listing.merge(Listing(), [("s1", theirs)], [])
    assert [(i.runtime_min, i.disc) for i in merged.items] == [(None, None)] * 4
    # A box that is split by disc keeps its numbers, and printed runtimes that differ stay.
    theirs.items[0].disc, theirs.items[0].runtime_min = 2, 11
    merged = listing.merge(Listing(), [("s1", theirs)], [])
    assert [i.disc for i in merged.items] == [2, 1, 1, 1] and merged.items[0].runtime_min == 11


def test_an_apostrophe_is_not_a_quote_in_a_models_title():
    raw = {"items": [{"kind": "extra", "title": "Pippa's Kite Day", "number": None, "runtime_min": None,
                      "extra_type": "Other", "disc": None, "show": None}],
           "details": {"title": None, "year": None, "feature_runtime_min": None, "episode_count": None,
                       "barcode": None, "catalogue_number": None, "region": None, "video_system": None,
                       "aspect_ratio": None, "audio": [], "subtitles": []}}
    assert vision.tidy(raw).items[0].title == "Pippa's Kite Day"


def test_scraps_misread_off_a_picture_are_not_extras():
    from .test_scan_text import lines

    reading = lines("Special features", ("back", "ae"), "Making the Kite Tram")
    reading.lines[1].conf = 94
    got = [it.title for it in listing.build([reading]).items]
    assert "ae" not in got and "Making the Kite Tram" in got
    low = lines("Special features", "Making the Kite Tram", "GE woth", conf=49)
    assert listing.build([low]).items == []


# --- a long title split into episodes, in the box's order -----------------------------

TUG = Show(tmdb_id=70002, name="Tilly the Tugboat", year=1998, seasons={1: 13}, score=0.9)
CHAPTERS = [0.0, 611.0, 1222.0, 1833.0, 2444.0, 3055.0, 3666.0, 4277.0, 4887.0]
LENGTH = 5497.5


def split_job(box: bool = True) -> JobRecord:
    """Nine episodes in title 0, cut at its chapters once it's ripped, a tenth in title 1
    and a 30-minute title 2; the box lists the season's thirteen, the disc's ten first."""
    titles = [TitleInfo(index=i, duration_s=s, size_bytes=1, chapters=c)
              for i, s, c in [(0, 5498, 10), (1, 611, 1), (2, 1830, 1)]]
    mapping = [TitleMap(title_index=0, duration_s=5498, role=TitleRole.skip),
               TitleMap(title_index=1, duration_s=611, role=TitleRole.episode),
               TitleMap(title_index=2, duration_s=1830, role=TitleRole.skip)]
    split = TitleSplit(title_index=0, duration_s=5498, expected=9, reason="title 0 is long")
    job = JobRecord(id=7, drive_id="fake-ihas122", state=JobState.review, created_at=now(), updated_at=now(),
                    titles=titles, media=MediaKind.tv,
                    tv=TvPlan(show=TUG, season=1, start_episode=1, mapping=mapping, candidates=[TUG],
                              episodes=TUGBOAT, split=split, order_from="season"))
    job.disc.label, job.disc.kind = "TILLY_SERIES_1", DiscKind.dvd
    job.rip.files = {0: "/nowhere/title_t00.mkv", 1: "/nowhere/title_t01.mkv"}
    if box:
        job.listing = ScanListing(state="done", found=tugboat_box())
    return job


def fake_chapters(monkeypatch, h):
    async def chapters(ffprobe, path):
        return CHAPTERS

    async def break_score(ffmpeg, path, at):
        return 1.0

    async def duration(ffprobe, path):
        return LENGTH

    async def grab(*a):
        return None

    monkeypatch.setattr(tvsplit, "read_chapters", chapters)
    monkeypatch.setattr(tvsplit, "break_score", break_score)
    monkeypatch.setattr("ripshelf.tvjobs.verify.duration", duration)
    monkeypatch.setattr(h.m, "_grab_mark", grab)


def episodes_in_disc_order(job) -> list[int]:
    rows = {m.title_index: m for m in job.tv.mapping}
    order = [tvsplit.part_index(k) for k in range(9)] + [1]
    return [rows[i].episodes[0].episode if rows[i].episodes else None for i in order]


async def test_split_parts_take_the_boxs_order(harness, settings, monkeypatch):
    """The parts are alike, so only the box's order says which episode each is: they get
    the box's list, not the season's order (which put most of them on the wrong part)."""
    async with harness() as h:
        fake_chapters(monkeypatch, h)
        job = put(h, split_job())
        await h.m._analyse_split(job.id)
        job = h.db.get_job(job.id)
        assert job.tv.split.state == "ready" and len(job.tv.split.cuts) == 8
        assert episodes_in_disc_order(job) == DISC_ORDER
        assert job.tv.order_from == "box" and job.tv.from_scan
        assert next(m for m in job.tv.mapping if m.title_index == 2).role == TitleRole.skip
        m = {x.title_index: x for x in job.scan_match.matches}
        assert all(m[tvsplit.part_index(k)].agrees for k in range(9)) and 0 not in m
        assert h.m._auto_check(job)[0] is False  # reviewed, all the same


async def test_split_parts_without_a_box_are_a_guess(harness, settings, monkeypatch):
    async with harness() as h:
        fake_chapters(monkeypatch, h)
        job = put(h, split_job(box=False))
        await h.m._analyse_split(job.id)
        job = h.db.get_job(job.id)
        assert episodes_in_disc_order(job) == list(range(1, 11))  # the season's order
        assert job.tv.order_from == "season" and not job.tv.from_scan


async def test_the_box_read_after_the_split(harness, settings, monkeypatch):
    """The box scanned once the parts are mapped: its order is taken then, and Use this
    takes it again after the cuts were changed by hand."""
    async with harness() as h:
        fake_chapters(monkeypatch, h)
        proposed = fake_tmdb(monkeypatch, h)
        job = put(h, split_job(box=False))
        await h.m._analyse_split(job.id)
        job = h.db.get_job(job.id)
        job.listing = ScanListing(state="done", found=tugboat_box())
        h.db.save_job(job)
        assert await h.m.fix_show_from_scan(job.id)
        assert proposed == [] and episodes_in_disc_order(h.db.get_job(job.id)) == DISC_ORDER

        job = await h.m.tv_split(job.id, list(range(1, 9)), None)  # the same cuts, by hand
        assert job.tv.user_edited and job.tv.order_from == "season"
        assert episodes_in_disc_order(job) == list(range(1, 11))
        assert any(x.agrees is False for x in job.scan_match.matches)  # the box says otherwise
        job = await h.m.use_scan(job.id)
        assert episodes_in_disc_order(job) == DISC_ORDER and job.tv.order_from == "box"
