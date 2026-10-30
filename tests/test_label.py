import pytest

from ripshelf.identify.label import clean_bdmt_title, name_differs_from_label, parse_label


@pytest.mark.parametrize(
    "label,title,year,disc",
    [
        ("PRACTICAL_MAGIC", "Practical Magic", None, None),
        ("THE_WILD_ROBOT", "The Wild Robot", None, None),
        ("SARAH_&_DUCK_BROLLY_BUS", "Sarah & Duck Brolly Bus", None, None),
        ("ALIEN_1979_DISC_1", "Alien", 1979, 1),
        ("GLADIATOR_SPECIAL_EDITION_D2", "Gladiator", None, 2),
        ("BLADE_RUNNER_2049", "Blade Runner 2049", None, None),
        ("THE_LEGO_MOVIE", "The Lego Movie", None, None),
        ("ROCKY_II", "Rocky II", None, None),
        ("INCEPTION_UHD", "Inception", None, None),
        ("AVATAR_WS_PAL", "Avatar", None, None),
        ("LORD_OF_THE_RINGS", "Lord of the Rings", None, None),
        ("DIRTY_HARRY_WW", "Dirty Harry", None, None),
        ("HEAT_INTL_NORDIC", "Heat", None, None),
    ],
)
def test_parse_label(label, title, year, disc):
    p = parse_label(label)
    assert p.title == title
    assert p.year == year
    assert p.disc_number == disc


def test_generic_labels_have_no_queries():
    assert parse_label("DVD_VIDEO").queries == []
    assert parse_label("").queries == []


def test_truncated_label_adds_shorter_query():
    p = parse_label("HARRY_POTTER_AND_THE_PHILOSOPHER")
    assert p.queries == ["Harry Potter and the Philosopher", "Harry Potter"]


def test_bdmt_title_cleanup():
    assert clean_bdmt_title("The Wild Robot - 4K Ultra HD") == "The Wild Robot"
    assert clean_bdmt_title("Dune (Disc 1)") == "Dune"
    assert clean_bdmt_title("The Wild Robot") == "The Wild Robot"


def test_name_differs_from_label():
    assert name_differs_from_label("The Wild Robot", "THE_WILD_ROBOT")
    assert not name_differs_from_label("PRACTICAL_MAGIC", "PRACTICAL_MAGIC")
