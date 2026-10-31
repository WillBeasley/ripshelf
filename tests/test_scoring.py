from ripshelf.identify.scoring import rank, runtime_score, score_candidate, title_similarity
from ripshelf.models import Candidate, DiscFacts, DiscKind

WILD_ROBOT = DiscFacts(
    label="THE_BRASS_HERON", names=["The Brass Heron"], main_duration_s=6101, kind=DiscKind.uhd
)
PRACTICAL_MAGIC = DiscFacts(
    label="MOONLIT_ORCHARD", names=["Moonlit Orchard"], main_duration_s=5983, pal=True,
    kind=DiscKind.dvd,
)


def test_title_similarity():
    assert title_similarity("The Brass Heron", "Brass Heron") == 1.0
    assert title_similarity("Pippa & Goose", "Pippa and Goose") == 1.0
    assert title_similarity("Moonlit Orchard", "Moonlit Orchard 2") < 0.95
    assert title_similarity("Moonlit Orchard", "Undertow") < 0.5


def test_runtime_pal_speedup():
    # 99:43 at 25 fps is a 104 minute film at 24 fps.
    score, diff, used_pal = runtime_score(5983 / 60, 104, pal=True)
    assert score == 1.0 and used_pal and diff < 1
    score, _, used_pal = runtime_score(5983 / 60, 104, pal=False)
    assert score < 1.0 and not used_pal


def test_exact_match_auto_confirms():
    c = score_candidate(Candidate(title="The Brass Heron", year=2024, runtime=102), WILD_ROBOT)
    assert c.score >= 0.9
    assert all(e.ok for e in c.evidence)


def test_pal_dvd_auto_confirms():
    c = score_candidate(Candidate(title="Moonlit Orchard", year=1998, runtime=104), PRACTICAL_MAGIC)
    assert c.score >= 0.9
    assert any("PAL" in e.text for e in c.evidence)


def test_title_only_never_auto_confirms():
    c = score_candidate(Candidate(title="The Brass Heron", year=2024, runtime=None), WILD_ROBOT)
    assert c.score < 0.9


def test_wrong_runtime_goes_to_review():
    c = score_candidate(Candidate(title="The Brass Heron", runtime=45), WILD_ROBOT)
    assert c.score < 0.9
    assert any(e.ok is False for e in c.evidence)


def test_label_year_mismatch_penalised():
    disc = DiscFacts(names=["Stowaway"], label_year=1979, main_duration_s=117 * 60)
    right = score_candidate(Candidate(title="Stowaway", year=1979, runtime=117), disc)
    wrong = score_candidate(Candidate(title="Stowaway", year=2019, runtime=117), disc)
    assert right.score > 0.9 > wrong.score


def test_rank_orders_and_flags_ambiguity():
    disc = DiscFacts(names=["The Sky Queen"], main_duration_s=None)
    ranked = rank(
        [Candidate(title="The Sky Queen", year=1994), Candidate(title="The Sky Queen", year=2019)],
        disc,
    )
    assert ranked[0].score < 0.9
    assert any("almost as high" in e.text for e in ranked[0].evidence)


def test_rank_runtime_disambiguates_remake():
    disc = DiscFacts(names=["The Sky Queen"], main_duration_s=88 * 60)
    ranked = rank(
        [Candidate(title="The Sky Queen", year=2019, runtime=118),
         Candidate(title="The Sky Queen", year=1994, runtime=89)],
        disc,
    )
    assert ranked[0].year == 1994 and ranked[0].score >= 0.9
