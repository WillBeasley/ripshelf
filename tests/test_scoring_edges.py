"""Scoring boundaries, such as the PAL speed-up, the auto-confirm line and ties."""

from __future__ import annotations

import pytest

from ripshelf.identify.scoring import (
    PAL_SPEEDUP,
    fmt_duration,
    normalise,
    rank,
    runtime_score,
    score_candidate,
    title_similarity,
)
from ripshelf.models import Candidate, DiscFacts

THRESHOLD = 0.90


@pytest.mark.parametrize("diff, want", [(0, 1.0), (2, 1.0), (2.01, pytest.approx(1 - 0.01 / 13)),
                                        (8.5, 0.5), (14.99, pytest.approx(0.0, abs=0.01)), (15, 0.0), (60, 0.0)])
def test_runtime_score_bands(diff, want):
    score, d, _ = runtime_score(100, round(100 + diff) if diff == int(diff) else 100, pal=False) \
        if False else runtime_score(100 - diff, 100, pal=False)
    assert score == want
    assert d == pytest.approx(diff)


def test_pal_speedup_edges():
    disc_min = 96  # 96 min at 25 fps
    film = round(disc_min * PAL_SPEEDUP)  # 100 min at 24 fps
    s, diff, used = runtime_score(disc_min, film, pal=True)
    assert s == 1.0 and used and diff < 0.5
    # PAL flagged, but the runtime matches without the speed-up, so don't claim it.
    s, diff, used = runtime_score(100, 100, pal=True)
    assert s == 1.0 and not used and diff == 0
    # Not PAL, so the 4 minute gap counts.
    s, _, used = runtime_score(disc_min, film, pal=False)
    assert s < 1.0 and not used


def test_pal_evidence_mentions_speedup():
    disc = DiscFacts(names=["Heat"], main_duration_s=96 * 60, pal=True)
    c = score_candidate(Candidate(title="Heat", runtime=100), disc)
    assert any("PAL" in e.text for e in c.evidence)


def test_scores_are_rounded_to_three_places():
    disc = DiscFacts(names=["Heat"], main_duration_s=100 * 60)
    for rt in range(80, 121):
        c = score_candidate(Candidate(title="Heat", runtime=rt), disc)
        assert c.score == round(c.score, 3)


def test_near_threshold():
    # Find a runtime gap that lands just under and just over the line.
    disc = DiscFacts(names=["Heat"], main_duration_s=6000)
    scores = {rt: score_candidate(Candidate(title="Heat", runtime=rt), disc).score for rt in range(100, 120)}
    below = [s for s in scores.values() if s < THRESHOLD]
    above = [s for s in scores.values() if s >= THRESHOLD]
    assert below and above
    assert max(below) < THRESHOLD <= min(above)
    assert 0.899 < THRESHOLD and not (0.899 >= THRESHOLD) and 0.900 >= THRESHOLD


def test_exact_tie_never_auto_confirms():
    disc = DiscFacts(names=["Heat"], main_duration_s=100 * 60)
    ranked = rank([Candidate(title="Heat", runtime=100, vote_count=10),
                   Candidate(title="Heat", runtime=100, vote_count=12)], disc)
    assert all(c.score < THRESHOLD for c in ranked)
    assert ranked[0].vote_count == 12  # ties ordered by votes


def test_tie_with_far_better_known_film_auto_confirms():
    disc = DiscFacts(names=["Heat"], main_duration_s=170 * 60)
    ranked = rank([Candidate(title="Heat", year=1986, runtime=101, vote_count=60),
                   Candidate(title="Heat", year=1995, runtime=170, vote_count=7000),
                   Candidate(title="Heat", year=2013, runtime=170, vote_count=40)], disc)
    assert ranked[0].year == 1995 and ranked[0].score >= THRESHOLD
    assert any("better known" in e.text for e in ranked[0].evidence)


def test_tie_without_votes_flags_both():
    disc = DiscFacts(names=["Heat"], main_duration_s=None)
    ranked = rank([Candidate(title="Heat", year=1995), Candidate(title="Heat", year=2013)], disc)
    assert all(any("almost as high" in e.text for e in c.evidence) for c in ranked)


def test_rank_empty_and_single():
    assert rank([], DiscFacts()) == []
    one = rank([Candidate(title="Heat", runtime=170)], DiscFacts(names=["Heat"], main_duration_s=170 * 60))
    assert one[0].score >= THRESHOLD


def test_no_disc_names():
    c = score_candidate(Candidate(title="Heat", runtime=170), DiscFacts(main_duration_s=170 * 60))
    assert c.score < THRESHOLD
    assert c.evidence[0].ok is False


def test_original_title_matches():
    disc = DiscFacts(names=["Amelie"], main_duration_s=122 * 60)
    c = score_candidate(Candidate(title="Le Fabuleux Destin d'Amélie Poulain", original_title="Amélie",
                                  runtime=122), disc)
    assert c.score >= THRESHOLD


def test_year_one_off_is_partial():
    disc = DiscFacts(names=["Heat"], main_duration_s=170 * 60, label_year=1996)
    c = score_candidate(Candidate(title="Heat", year=1995, runtime=170), disc)
    assert any("one off" in e.text for e in c.evidence)


def test_normalise_and_similarity_edges():
    assert normalise("The  Wild—Robot!") == "wild robot"
    assert title_similarity("", "Heat") == 0.0
    assert title_similarity("!!!", "???") == 0.0
    assert title_similarity("Amélie", "Amelie") == 1.0


def test_fmt_duration():
    assert fmt_duration(59) == "0:59"
    assert fmt_duration(3600) == "1:00:00"


def test_non_latin_titles_match_themselves():
    assert title_similarity("千と千尋の神隠し", "千と千尋の神隠し") == 1.0
    assert title_similarity("Léon", "Leon") == 1.0
    disc = DiscFacts(names=["千と千尋の神隠し"], main_duration_s=125 * 60)
    assert score_candidate(Candidate(title="Spirited Away", original_title="千と千尋の神隠し",
                                     runtime=125), disc).score >= THRESHOLD


def test_extended_cut_runtime():
    from ripshelf.identify.label import parse_label

    assert parse_label("LOTR_FOTR_EXT_D1").extended and parse_label("ALIEN_DIRECTORS_CUT").extended
    assert not parse_label("PRACTICAL_MAGIC").extended
    disc = DiscFacts(names=["The Lord of the Rings: The Fellowship of the Ring"], main_duration_s=208 * 60)
    fotr = Candidate(title="The Lord of the Rings: The Fellowship of the Ring", runtime=179)
    rotk = Candidate(title="The Lord of the Rings: The Return of the King", runtime=201)
    ranked = rank([rotk, fotr], disc)
    assert ranked[0].title.endswith("Fellowship of the Ring")
    assert any("probably an extended cut" in e.text for e in ranked[0].evidence)
    labelled = score_candidate(fotr.model_copy(), disc.model_copy(update={"extended": True}))
    plain = score_candidate(fotr.model_copy(), disc)
    assert labelled.score > plain.score and "label says extended" in labelled.evidence[1].text
    # Far too long (a different film), or shorter, so no extended-cut excuse.
    assert not any("extended" in e.text for e in score_candidate(fotr.model_copy(), DiscFacts(
        names=[fotr.title], main_duration_s=300 * 60)).evidence)
