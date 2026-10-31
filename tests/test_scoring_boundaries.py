"""Exact scores, thresholds and evidence flags in film scoring and ranking.

These pin the number, the boundary and whether an evidence line counts for or
against, not just who wins.
"""

from __future__ import annotations

import pytest

from ripshelf.identify import scoring
from ripshelf.identify.scoring import rank, runtime_score, score_candidate
from ripshelf.models import Candidate, DiscFacts


def cand(title="Moonlit Orchard", year=1998, runtime=104, **kw) -> Candidate:
    return Candidate(source="tmdb", tmdb_id=kw.pop("tmdb_id", 1), title=title, year=year, runtime=runtime, **kw)


def disc(**kw) -> DiscFacts:
    return DiscFacts(**{"names": ["Moonlit Orchard"], "main_duration_s": 104 * 60, **kw})


def flags(c: Candidate) -> list:
    return [e.ok for e in c.evidence]


def test_perfect_match_is_exactly_one_with_two_ok_lines():
    c = score_candidate(cand(), disc())
    assert c.score == 1.0 and flags(c) == [True, True] and not c.excluded


@pytest.mark.parametrize("diff, want", [(0, 1.0), (2, 1.0), (2.5, 1 - 0.5 / 13), (15, 0.0), (20, 0.0)])
def test_runtime_score_edges(diff, want):
    score, d, pal = runtime_score(100 + diff, 100, pal=False)
    assert score == pytest.approx(want) and d == pytest.approx(diff) and pal is False


def test_runtime_score_uses_pal_only_when_closer():
    score, diff, pal = runtime_score(96, 100, pal=True)  # 96 * 25/23.976 = 100.1
    assert pal is True and score == 1.0 and diff < 0.2
    assert runtime_score(100, 100, pal=True)[2] is False


def test_title_close_but_not_exact_is_neutral_evidence():
    c = score_candidate(cand(title="Moonlit Mayhem"), disc())  # 77% similar
    assert c.evidence[0].ok is None and c.evidence[0].text.startswith("Title is close")
    far = score_candidate(cand(title="Something Else"), disc())
    assert far.evidence[0].ok is False


def test_runtime_off_by_some_is_neutral_and_way_off_is_against():
    near = score_candidate(cand(runtime=110), disc())
    assert near.evidence[1].ok is None and "6 min off" in near.evidence[1].text
    far = score_candidate(cand(runtime=60), disc(main_duration_s=104 * 60))
    assert far.evidence[1].ok is False


def test_extended_cut_scores_partial_or_high_when_labelled():
    plain = score_candidate(cand(runtime=100), disc(main_duration_s=130 * 60))  # 30 min longer
    labelled = score_candidate(cand(runtime=100), disc(main_duration_s=130 * 60, extended=True))
    assert plain.evidence[1].ok is None and "extended cut" in plain.evidence[1].text
    assert labelled.evidence[1].ok is True and "(the label says extended)" in labelled.evidence[1].text
    w = scoring.W_TITLE + scoring.W_RUNTIME
    assert plain.score == pytest.approx(round((scoring.W_TITLE + scoring.W_RUNTIME * 0.6) / w, 3))
    assert labelled.score == pytest.approx(round((scoring.W_TITLE + scoring.W_RUNTIME * 0.9) / w, 3))
    # More than 35% longer is not an extended cut.
    assert "extended" not in score_candidate(cand(runtime=100), disc(main_duration_s=140 * 60)).evidence[1].text
    # Shorter than TMDB's runtime is not one either.
    assert "extended" not in score_candidate(cand(runtime=130), disc(main_duration_s=100 * 60)).evidence[1].text


def test_year_lines_and_weights():
    exact = score_candidate(cand(), disc(label_year=1998))
    one_off = score_candidate(cand(year=1999), disc(label_year=1998))
    wrong = score_candidate(cand(year=2005), disc(label_year=1998))
    missing = score_candidate(cand(year=None), disc(label_year=1998))
    assert [exact.evidence[2].ok, one_off.evidence[2].ok, wrong.evidence[2].ok] == [True, None, False]
    w = scoring.W_TITLE + scoring.W_RUNTIME + scoring.W_YEAR
    assert one_off.score == pytest.approx(round((scoring.W_TITLE + scoring.W_RUNTIME + scoring.W_YEAR * 0.7) / w, 3))
    assert wrong.score == pytest.approx(round((scoring.W_TITLE + scoring.W_RUNTIME) / w, 3))
    assert "Year ? differs" in missing.evidence[2].text


def test_no_runtime_caps_and_says_so():
    c = score_candidate(cand(runtime=None), disc())
    assert c.score == scoring.TITLE_ONLY_CAP and c.evidence[1].text == "TMDB has no runtime for this title"
    assert score_candidate(cand(runtime=None), disc(main_duration_s=None)).score == scoring.TITLE_ONLY_CAP


def test_excluded_extra_is_capped_and_first_line_says_why():
    c = score_candidate(cand(title="The Making of Moonlit Orchard", runtime=20), disc())
    assert c.excluded and c.score <= scoring.EXCLUDED_CAP
    assert c.evidence[0].ok is False and c.evidence[0].text.startswith("Excluded (making-of")


def test_short_film_against_a_feature_is_excluded_at_the_half_line():
    feature = disc(main_duration_s=100 * 60)
    assert score_candidate(cand(runtime=49), feature).excluded
    assert not score_candidate(cand(runtime=50), feature).excluded


def test_ambiguous_pair_is_capped_and_both_say_so():
    a = cand(title="Nadira", year=1972, runtime=104, tmdb_id=1, vote_count=40)
    b = cand(title="Nadira", year=2002, runtime=104, tmdb_id=2, vote_count=30)
    out = rank([a, b], disc(names=["Nadira"]))
    assert all(c.score <= scoring.AMBIGUOUS_CAP for c in out)
    assert [c.evidence[-1].ok for c in out] == [False, False]
    assert "scores almost as high" in out[0].evidence[-1].text


def test_far_better_known_film_wins_a_tie_on_votes():
    film = cand(title="Ix", year=2009, runtime=96, tmdb_id=1, vote_count=500)
    short = cand(title="Ix", year=1984, runtime=96, tmdb_id=2, vote_count=50)
    out = rank([short, film], disc(names=["Ix"], main_duration_s=96 * 60))
    assert out[0].tmdb_id == 1 and out[0].score == 1.0
    assert out[0].evidence[-1].ok is True and "(500 vs 50 votes)" in out[0].evidence[-1].text
    assert out[1].score == round(min(1.0 * scoring.AMBIGUITY_FACTOR, scoring.AMBIGUOUS_CAP), 3)


def test_popularity_needs_both_a_minimum_and_a_factor():
    # 10x but under the minimum is still ambiguous.
    out = rank([cand(title="Ix", tmdb_id=1, vote_count=49), cand(title="Ix", tmdb_id=2, vote_count=4)],
               disc(names=["Ix"], main_duration_s=104 * 60))
    assert all(c.score <= scoring.AMBIGUOUS_CAP for c in out)
    # Over the minimum but not 10x is still ambiguous.
    out = rank([cand(title="Ix", tmdb_id=1, vote_count=900), cand(title="Ix", tmdb_id=2, vote_count=91)],
               disc(names=["Ix"], main_duration_s=104 * 60))
    assert all(c.score <= scoring.AMBIGUOUS_CAP for c in out)
    # Exactly 10x and at the minimum is settled.
    out = rank([cand(title="Ix", tmdb_id=1, vote_count=50), cand(title="Ix", tmdb_id=2, vote_count=5)],
               disc(names=["Ix"], main_duration_s=104 * 60))
    assert out[0].tmdb_id == 1 and out[0].score == 1.0


def test_runtime_favoured_rival_loses_to_the_exact_title():
    # "Tin Parade 2" matches the runtime; "Tin Parade" matches the label. A human decides.
    sequel = cand(title="Tin Parade 2", year=1999, runtime=92, tmdb_id=2)
    original = cand(title="Tin Parade", year=1995, runtime=81, tmdb_id=1)
    out = rank([sequel, original], disc(names=["Tin Parade"], main_duration_s=92 * 60))
    top = next(c for c in out if c.tmdb_id == 2)
    assert top.score <= round(0.89 * scoring.AMBIGUITY_FACTOR, 3)
    assert any(e.ok is False and "“Tin Parade” (1995) matches the disc name exactly" in e.text for e in top.evidence)


def test_documentary_loses_to_the_same_named_film():
    film = cand(title="Vasco", year=2010, runtime=104, tmdb_id=1)
    doc = cand(title="Vasco", year=2010, runtime=104, tmdb_id=2, genre_ids=[scoring.DOCUMENTARY])
    out = rank([doc, film], disc(names=["Vasco"]))
    d = next(c for c in out if c.tmdb_id == 2)
    assert any("TMDB lists this as a documentary" in e.text and e.ok is False for e in d.evidence)
    assert out[0].tmdb_id == 1
