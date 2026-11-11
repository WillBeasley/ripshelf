"""Matching the listing read from the box to the disc's titles."""

from __future__ import annotations

import itertools
import random

import pytest

from ripshelf.models import (
    AudioTrack,
    Episode,
    EpisodeRef,
    Listing,
    ListingDetails,
    ListingItem,
    TitleInfo,
    TitleMap,
    TitleRole,
)
from ripshelf.scanner import match


def title(index: int, minutes: float, audio=("DD Stereo English",), source: str | None = None,
          segments: str | None = None) -> TitleInfo:
    return TitleInfo(index=index, duration_s=round(minutes * 60), size_bytes=1, chapters=1, audio=list(audio),
                     source_file=source, segments_map=segments)


def ep(title_: str, runtime: float | None = None, number: int | None = None, disc: int | None = None) -> ListingItem:
    return ListingItem(kind="episode", title=title_, runtime_min=runtime, number=number, disc=disc)


def extra(title_: str, kind: str = "Extra", runtime: float | None = None) -> ListingItem:
    return ListingItem(kind="extra", title=title_, extra_type=kind, runtime_min=runtime)


SEASON = [Episode(season=2, episode=n, name=name, runtime=rt) for n, name, rt in [
    (1, "The Gull Who Stayed", 22), (2, "Low Tide", 30), (3, "A Storm in a Teacup", 45),
    (4, "The Lighthouse Keeper's Cat", 22), (5, "Captain Comet, Part One", 24), (6, "Captain Comet, Part Two", 24)]]


def by_title(m):
    return {x.title_index: x for x in m.matches}


def test_episodes_by_name_on_a_scrambled_disc():
    """The box lists them in broadcast order; the disc has them in another. Each name
    says which episode it is, and its runtime which title."""
    titles = [title(1, 45.2), title(2, 22.1), title(3, 30.3)]
    listing = Listing(items=[ep("The Gull Who Stayed"), ep("Low Tide"), ep("A Storm in a Teacup")])
    m = by_title(match.match(titles, listing, media="tv", episodes=SEASON, season=2))
    assert [(i, m[i].episodes) for i in (1, 2, 3)] == [
        (1, [EpisodeRef(season=2, episode=3)]), (2, [EpisodeRef(season=2, episode=1)]),
        (3, [EpisodeRef(season=2, episode=2)])]
    assert all(x.confidence == "high" for x in m.values())
    assert "S02E03" in m[1].reason and "45 min" in m[1].reason


def test_part_one_and_part_two():
    listing = Listing(items=[ep("Captain Comet (Pt. 2)"), ep("Captain Comet Part 1")])
    e, _ = match.episode_for("Captain Comet Part 1", SEASON, 2)
    assert e.episode == 5
    m = match.match([title(1, 24), title(2, 24)], listing, media="tv", episodes=SEASON, season=2)
    assert [x.episodes[0].episode for x in m.matches] == [6, 5]  # in the listing's order


def test_runtimes_alone():
    titles = [title(0, 95.0), title(1, 23.6), title(2, 2.1), title(3, 11.8)]
    listing = Listing(items=[extra("Making the Orchard", "Featurette", 24), extra("Blossom reel", "Other", 12),
                             extra("Theatrical trailer", "Trailer")],
                      details=ListingDetails(feature_runtime_min=95))
    m = by_title(match.match(titles, listing))
    assert m[0].kind == "feature"
    assert (m[1].name, m[3].name, m[2].name) == ("Making the Orchard", "Blossom reel", "Theatrical trailer")
    assert m[1].confidence == "high" and "23:36 against 24 min printed" in m[1].reason


def test_the_main_feature_among_long_titles():
    """Two cuts of the film: the printed runtime picks one; when that can't, the
    printed audio languages and a commentary track do."""
    langs = ("DD Surround 5.1 English", "DD Surround 5.1 French", "DD Surround 5.1 German", "DD Stereo English Commentary")
    titles = [title(0, 118, audio=("DD Stereo English",)), title(1, 131, audio=langs), title(2, 3)]
    d = ListingDetails(feature_runtime_min=131, audio=[AudioTrack(language=x) for x in ("English", "French", "German")])
    feat = match.match(titles, Listing(details=d), main_title=0).matches[0]
    assert (feat.title_index, feat.agrees, feat.current) == (1, False, "title 0 is ripped as the film")
    d.feature_runtime_min = None
    feat = match.match([title(0, 125, audio=("DD Stereo English",)), title(1, 125, audio=langs)],
                       Listing(details=d), main_title=1).matches[0]
    assert (feat.title_index, feat.agrees) == (1, True)
    assert "3 of the 3 audio languages" in feat.reason and "commentary" in feat.reason


def test_a_box_set_across_discs():
    """The set's listing names every disc's episodes; lines other discs took, or for
    another disc, are left to them."""
    listing = Listing(items=[ep("The Gull Who Stayed", disc=1), ep("Low Tide", disc=1),
                             ep("A Storm in a Teacup", disc=2), ep("The Lighthouse Keeper's Cat", disc=2)])
    m = match.match([title(1, 45), title(2, 22)], listing, media="tv", episodes=SEASON, season=2, disc=2)
    assert [(x.title_index, x.episodes[0].episode) for x in m.matches] == [(1, 3), (2, 4)]
    no_numbers = Listing(items=[ep(it.title) for it in listing.items])
    m = match.match([title(1, 45), title(2, 22)], no_numbers, media="tv", episodes=SEASON, season=2,
                    claimed={0, 1})
    assert [(x.title_index, x.item) for x in m.matches] == [(1, 2), (2, 3)] and m.claimed == [0, 1]


def test_disagreeing_with_the_tv_mapping():
    titles = [title(1, 22), title(2, 30)]
    listing = Listing(items=[ep("The Gull Who Stayed"), ep("Low Tide")])
    mapping = [TitleMap(title_index=1, duration_s=1320, role=TitleRole.episode, episodes=[EpisodeRef(season=2, episode=1)]),
               TitleMap(title_index=2, duration_s=1800, role=TitleRole.episode, episodes=[EpisodeRef(season=2, episode=4)])]
    m = match.match(titles, listing, media="tv", episodes=SEASON, season=2, mapping=mapping)
    first, second = m.matches
    assert (first.agrees, first.confidence) == (True, "high")
    assert (second.agrees, second.current) == (False, "mapped as S02E04")
    assert second.confidence != "high" and m.confidence == "medium"
    assert "disagree" in m.notes[-1]


def test_play_alls_and_duplicates_are_left_out():
    titles = [title(0, 66.3), title(1, 22.1), title(2, 22.1), title(3, 22.1),
              title(4, 22.1, source="00004.mpls", segments="7,8"), title(5, 22.1, source="00005.mpls", segments="7,8")]
    mapping = [TitleMap(title_index=3, duration_s=1326, role=TitleRole.duplicate)]
    cands = [t.index for t in match._candidates(titles, mapping)]
    assert cands == [1, 2, 4]  # 0 is 1-3 back to back; 5 plays 4's clips


def test_a_poor_fit_stays_unmatched():
    m = match.match([title(0, 100), title(1, 50)], Listing(items=[extra("Theatrical trailer", "Trailer", 2)]))
    assert m.unmatched_items == [0] and m.unmatched_titles == [1]


@pytest.mark.parametrize("seed", range(5))
def test_hungarian_is_optimal(seed):
    rnd = random.Random(seed)
    n, k = rnd.randint(1, 5), rnd.randint(0, 3)
    cost = [[rnd.random() for _ in range(n + k)] for _ in range(n)]
    pick = match.hungarian(cost)
    best = min(sum(cost[r][c] for r, c in enumerate(p)) for p in itertools.permutations(range(n + k), n))
    assert len(set(pick)) == n and abs(sum(cost[r][c] for r, c in enumerate(pick)) - best) < 1e-9


@pytest.mark.parametrize("minutes, printed, fit", [(22.1, 22, 1.0), (24, 22, 1.0), (40, 22, 0.0), (165, 165, 1.0),
                                                   (158.2, 165, 1.0)])
def test_runtime_fit(minutes, printed, fit):
    assert match.runtime_fit(minutes * 60, printed, pal=True) == pytest.approx(fit, abs=0.01)


def test_tracks_from_makemkv():
    t = title(0, 100, audio=("TrueHD Surround 7.1 English", "DD Surround 5.1 Spanish", "DD Stereo English"))
    tr = match.tracks(t)
    assert tr.languages == {"English", "Spanish"} and tr.count == 3 and not tr.commentary


def test_alike_titles_placed_by_the_box_alone_are_not_sure():
    """Three names listed for ten titles of the same length: the names say which
    episodes, but only their places on the box which titles. (No runtimes are known:
    a 7-minute title for a 22-minute episode would be low.)"""
    titles = [title(i, 7) for i in range(1, 11)]
    listing = Listing(items=[ep("Low Tide"), ep("The Gull Who Stayed"), ep("A Storm in a Teacup")])
    unknown = [e.model_copy(update={"runtime": None}) for e in SEASON]
    m = match.match(titles, listing, media="tv", episodes=unknown, season=2)
    assert len(m.matches) == 3 and {x.confidence for x in m.matches} == {"medium"}
    assert "its place on the box decided" in m.matches[0].reason
    m = match.match(titles, listing, media="tv", episodes=SEASON, season=2)
    assert {x.confidence for x in m.matches} == {"low"}


# A season of thirteen alike ten-minute episodes, and a disc holding ten of them in its
# own order (not the season's): nine in one long title split into parts, and one title.
TUGBOAT = [Episode(season=1, episode=n, name=name, runtime=10) for n, name in enumerate([
    "Tilly Takes the Tow", "Rusty's Rope", "Fog on the Estuary", "Captain Pim's Pennant", "Barnacle Bay",
    "The Lost Lifebuoy", "Tilly's Horn", "Night Lights", "The Big Regatta", "Pim's Birthday",
    "Mudflat Morning", "The Gull Thief", "Home to Harbour"], 1)]
DISC_ORDER = [4, 5, 1, 8, 6, 3, 7, 10, 2, 9]  # the box lists these first, then 11, 12, 13


def tugboat_box() -> Listing:
    order = [*DISC_ORDER, 11, 12, 13]
    return Listing(items=[ep(TUGBOAT[n - 1].name) for n in order], details=ListingDetails(episode_count=13))


def test_a_box_listing_more_episodes_than_the_disc_places_them_from_the_start():
    """Ten alike titles in the disc's order, and a box listing thirteen: the disc's ten
    are the box's first ten, not ten spread over the whole list."""
    titles = [title(1000 + k, 10.2) for k in range(9)] + [title(1, 10.2)]
    m = match.match(titles, tugboat_box(), media="tv", episodes=TUGBOAT, season=1, in_order=True)
    got = {x.title_index: x.episodes[0].episode for x in m.matches}
    assert [got[t.index] for t in titles] == DISC_ORDER
    assert m.unmatched_items == [10, 11, 12]


def test_titles_by_number_unless_in_the_discs_order():
    titles = [title(1000, 10), title(1001, 10), title(1, 10)]
    assert [t.index for t in match._candidates(titles, [])] == [1, 1000, 1001]
    assert [t.index for t in match._candidates(titles, [], in_order=True)] == [1000, 1001, 1]


def test_guesses_for_a_name_the_episode_list_spells_otherwise():
    """A misread name is closest to one episode by name, but its runtime doesn't fit the
    title: the match stays unsure, and the guesses offer the close names, the one whose
    runtime fits first."""
    eps = [Episode(season=1, episode=3, name="Seagull Snow Day", runtime=11),
           Episode(season=3, episode=8, name="Snow Day Surprise", runtime=22),
           Episode(season=2, episode=9, name="Snowy Day Surprise", runtime=11),
           Episode(season=2, episode=1, name="Puddle Dash", runtime=11)]
    titles = [title(1, 11.1), title(2, 11.0)]
    listing = Listing(items=[ep("Puddle Dash"), ep("Snow Days Suprise")])
    m = by_title(match.match(titles, listing, media="tv", episodes=eps))
    assert m[2].confidence != "high" and m[2].name == "Snow Days Suprise"
    codes = [(g.season, g.episode) for g in m[2].guesses]
    assert codes[0] == (2, 9) and (3, 8) in codes and (2, 1) not in codes
    assert len(m[2].guesses) <= 3 and m[2].guesses[0].name == "Snowy Day Surprise"
    # A name that matched still lists itself first.
    assert (m[1].guesses[0].season, m[1].guesses[0].episode) == (2, 1)
