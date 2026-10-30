from ripshelf.identify.scoring import rank, runtime_score, score_candidate, title_similarity
from ripshelf.models import Candidate, DiscFacts, DiscKind

WILD_ROBOT = DiscFacts(
    label="THE_WILD_ROBOT", names=["The Wild Robot"], main_duration_s=6101, kind=DiscKind.uhd
)
PRACTICAL_MAGIC = DiscFacts(
    label="PRACTICAL_MAGIC", names=["Practical Magic"], main_duration_s=5983, pal=True,
    kind=DiscKind.dvd,
)


def test_title_similarity():
    assert title_similarity("The Wild Robot", "Wild Robot") == 1.0
    assert title_similarity("Sarah & Duck", "Sarah and Duck") == 1.0
    assert title_similarity("Practical Magic", "Practical Magic 2") < 0.95
    assert title_similarity("Practical Magic", "Inception") < 0.5


def test_runtime_pal_speedup():
    # 99:43 at 25 fps is a 104 minute film at 24 fps.
    score, diff, used_pal = runtime_score(5983 / 60, 104, pal=True)
    assert score == 1.0 and used_pal and diff < 1
    score, _, used_pal = runtime_score(5983 / 60, 104, pal=False)
    assert score < 1.0 and not used_pal


def test_exact_match_auto_confirms():
    c = score_candidate(Candidate(title="The Wild Robot", year=2024, runtime=102), WILD_ROBOT)
    assert c.score >= 0.9
    assert all(e.ok for e in c.evidence)


def test_pal_dvd_auto_confirms():
    c = score_candidate(Candidate(title="Practical Magic", year=1998, runtime=104), PRACTICAL_MAGIC)
    assert c.score >= 0.9
    assert any("PAL" in e.text for e in c.evidence)


def test_title_only_never_auto_confirms():
    c = score_candidate(Candidate(title="The Wild Robot", year=2024, runtime=None), WILD_ROBOT)
    assert c.score < 0.9


def test_wrong_runtime_goes_to_review():
    c = score_candidate(Candidate(title="The Wild Robot", runtime=45), WILD_ROBOT)
    assert c.score < 0.9
    assert any(e.ok is False for e in c.evidence)


def test_label_year_mismatch_penalised():
    disc = DiscFacts(names=["Alien"], label_year=1979, main_duration_s=117 * 60)
    right = score_candidate(Candidate(title="Alien", year=1979, runtime=117), disc)
    wrong = score_candidate(Candidate(title="Alien", year=2019, runtime=117), disc)
    assert right.score > 0.9 > wrong.score


def test_rank_orders_and_flags_ambiguity():
    disc = DiscFacts(names=["The Lion King"], main_duration_s=None)
    ranked = rank(
        [Candidate(title="The Lion King", year=1994), Candidate(title="The Lion King", year=2019)],
        disc,
    )
    assert ranked[0].score < 0.9
    assert any("almost as high" in e.text for e in ranked[0].evidence)


def test_rank_runtime_disambiguates_remake():
    disc = DiscFacts(names=["The Lion King"], main_duration_s=88 * 60)
    ranked = rank(
        [Candidate(title="The Lion King", year=2019, runtime=118),
         Candidate(title="The Lion King", year=1994, runtime=89)],
        disc,
    )
    assert ranked[0].year == 1994 and ranked[0].score >= 0.9
