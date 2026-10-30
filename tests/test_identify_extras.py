"""Making-ofs, shorts, remakes and sequels must not beat (or impersonate) the film."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from ripshelf.identify.scoring import looks_like_extra, rank
from ripshelf.identify.tmdb import API, Tmdb, to_candidate
from ripshelf.models import Candidate, DiscFacts, DiscKind

TMDB = Path(__file__).parent / "fixtures" / "tmdb"
THRESHOLD = 0.90


def load(name: str) -> dict:
    return json.loads((TMDB / f"{name}.json").read_text())


def candidates(name: str) -> list[Candidate]:
    f = load(name)
    return [to_candidate({**r, **f["details"].get(str(r["id"]), {})}) for r in f["results"]]


def disc(name: str, minutes: float, year: int | None = None, kind=DiscKind.bluray) -> DiscFacts:
    return DiscFacts(names=[name], main_duration_s=int(minutes * 60), label_year=year, kind=kind)


def evidence(c: Candidate) -> str:
    return " | ".join(e.text for e in c.evidence)


# --- The Wild Robot and its extras ---------------------------------------------------


def test_wild_robot_beats_its_making_of():
    ranked = rank(candidates("wild-robot"), disc("The Wild Robot", 101.7, kind=DiscKind.uhd))
    best = ranked[0]
    assert (best.tmdb_id, best.year) == (1184918, 2024)
    assert best.score >= THRESHOLD and not best.excluded
    extras = {c.tmdb_id: c for c in ranked[1:]}
    for tid in (9000101, 9000102, 9000103, 9000104):
        assert extras[tid].excluded, evidence(extras[tid])
        assert extras[tid].score <= 0.3


def test_exclusion_reasons_are_shown():
    ranked = {c.tmdb_id: c for c in rank(candidates("wild-robot"), disc("The Wild Robot", 101.7))}
    making_of = ranked[9000101].evidence[0]
    assert making_of.ok is False
    assert making_of.text == "Excluded (making-of, documentary, 24 min against a 1:41:42 main feature)"
    assert "Excluded (" in ranked[9000102].evidence[0].text and "“Inside …”" in ranked[9000102].evidence[0].text
    short = ranked[9000103].evidence[0].text
    assert short == "Excluded (6 min against a 1:41:42 main feature)"
    assert "behind-the-scenes" in ranked[9000104].evidence[0].text


def test_short_namesake_never_wins_even_without_runtime_for_film():
    # TMDB detail lookup failed for the film, so it has no runtime, and the 6-minute short does.
    cands = candidates("wild-robot")
    for c in cands:
        if c.tmdb_id == 1184918:
            c.runtime = None
    ranked = rank(cands, disc("The Wild Robot", 101.7))
    assert ranked[0].tmdb_id == 1184918
    assert ranked[0].score < THRESHOLD  # title only, so review, but the right film on top


def test_disc_that_only_matches_extras_goes_to_review():
    ranked = rank(candidates("jaws-extras-only"), disc("Jaws", 124))
    assert all(c.score < THRESHOLD for c in ranked)
    by_id = {c.tmdb_id: c for c in ranked}
    assert by_id[9000301].excluded  # same runtime, but a making-of
    assert by_id[9000303].excluded
    # "Jaws: The Inside Story" is not caught by a pattern, but its title and runtime keep it low.
    assert by_id[9000302].score < 0.7


def test_bonus_disc_titled_as_a_making_of_identifies_as_itself():
    ranked = rank(candidates("jaws-extras-only"), disc("The Making of Jaws", 123))
    assert ranked[0].tmdb_id == 9000301
    assert not ranked[0].excluded
    assert ranked[0].score >= THRESHOLD


# --- Remakes and sequels ------------------------------------------------------------


def test_remake_picked_by_runtime():
    ranked = rank(candidates("lion-king"), disc("The Lion King", 88.5))
    assert (ranked[0].tmdb_id, ranked[0].year) == (8587, 1994)
    assert ranked[0].score >= THRESHOLD
    ranked = rank(candidates("lion-king"), disc("The Lion King", 117.8))
    assert ranked[0].year == 2019 and ranked[0].score >= THRESHOLD


def test_remake_picked_by_label_year():
    # 1982 and 2011 are 6 minutes apart; the label's year settles it.
    ranked = rank(candidates("the-thing"), disc("The Thing", 106, year=2011))
    assert ranked[0].year == 2011


def test_remake_with_close_runtimes_and_no_year_prefers_the_better_known_original():
    ranked = rank(candidates("the-thing"), disc("The Thing", 108.9))
    assert ranked[0].tmdb_id == 1091
    making_of = next(c for c in ranked if c.tmdb_id == 9000201)
    assert making_of.excluded


def test_sequels_with_similar_names():
    for minutes, want in ((81, 862), (92.5, 863), (103, 10193), (100, 301528)):
        name = {862: "Toy Story", 863: "Toy Story 2", 10193: "Toy Story 3", 301528: "Toy Story 4"}[want]
        ranked = rank(candidates("toy-story"), disc(name, minutes))
        assert ranked[0].tmdb_id == want, (name, [(c.title, c.score) for c in ranked])
        assert ranked[0].score >= THRESHOLD, evidence(ranked[0])


def test_unnumbered_label_with_sequel_runtime_does_not_auto_confirm_the_original():
    # Label says "TOY_STORY" but the feature is 92 min. Toy Story 2 in a plain case?
    ranked = rank(candidates("toy-story"), disc("Toy Story", 92.5))
    assert ranked[0].score < THRESHOLD


def test_tv_special_loses_to_theatrical_film():
    cands = [Candidate(title="Frozen", year=2013, runtime=102, vote_count=15000),
             Candidate(title="Frozen", year=2013, runtime=102, genre_ids=[10770], vote_count=40)]
    ranked = rank(cands, disc("Frozen", 102))
    assert 10770 not in ranked[0].genre_ids


def test_real_documentary_disc_is_not_penalised():
    # A documentary film on disc, with nothing else of that name.
    c = Candidate(title="Free Solo", year=2018, runtime=100, genre_ids=[99], vote_count=1500)
    ranked = rank([c], disc("Free Solo", 100))
    assert ranked[0].score >= THRESHOLD and not ranked[0].excluded


def test_documentary_namesake_loses_to_feature():
    cands = [Candidate(title="Senna", year=2010, runtime=106, genre_ids=[99], vote_count=900),
             Candidate(title="Senna", year=2024, runtime=104, genre_ids=[18], vote_count=50)]
    ranked = rank(cands, disc("Senna", 104))
    assert ranked[0].year == 2024
    doc = ranked[1]
    assert any("documentary" in e.text for e in doc.evidence)


@pytest.mark.parametrize("title, names, want", [
    ("The Making of The Wild Robot", ["The Wild Robot"], "making-of"),
    ("Making-of: Dune", ["Dune"], "making-of"),
    ("Dune: Behind the Scenes", ["Dune"], "behind-the-scenes"),
    ("Dune Featurette", ["Dune"], "featurette"),
    ("Inside Out", ["Inside Out"], None),
    ("Inside Man", ["Inside Out"], None),
    ("Inside The Wild Robot", ["The Wild Robot"], "“Inside …” feature about the film"),
    ("The Story of Toy Story", ["Toy Story"], "“The Story of …” feature about the film"),
    ("The Story of Us", ["Toy Story"], None),
    ("Anatomy of a Fall", ["Anatomy of a Fall"], None),
    ("Anatomy of Alien", ["Alien"], "“Anatomy of …” feature about the film"),
    ("The Making of Jaws", ["The Making of Jaws"], None),
    ("Creating Frozen", [], None),
])
def test_looks_like_extra(title, names, want):
    assert looks_like_extra(title, names) == want


# --- The TMDB client keeps extras out of the detail lookups -------------------------


def mock_fixture(router: respx.Router, name: str) -> None:
    f = load(name)
    router.get(f"{API}/search/movie").mock(return_value=httpx.Response(200, json={"results": f["results"]}))
    for tid, d in f["details"].items():
        router.get(f"{API}/movie/{tid}").mock(return_value=httpx.Response(200, json={"id": int(tid), **d}))


async def test_tmdb_details_prefer_films_over_extras(monkeypatch):
    monkeypatch.setattr("ripshelf.identify.tmdb.DETAILS_FOR_TOP", 2)
    with respx.mock(assert_all_called=False) as router:
        mock_fixture(router, "wild-robot")
        t = Tmdb("k")
        try:
            got = await t.candidates(["The Wild Robot"])
        finally:
            await t.aclose()
    # The film and the same-named short get details; the making-ofs ranked above them do not.
    assert [c.tmdb_id for c in got] == [1184918, 9000103]
    assert got[0].runtime == 102 and got[0].vote_count == 4521
