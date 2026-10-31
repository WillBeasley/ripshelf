import pytest

from ripshelf.identify.label import clean_bdmt_title, name_differs_from_label, parse_label


@pytest.mark.parametrize(
    "label,title,year,disc",
    [
        ("MOONLIT_ORCHARD", "Moonlit Orchard", None, None),
        ("THE_BRASS_HERON", "The Brass Heron", None, None),
        ("PIPPA_&_GOOSE_KITE_TRAM", "Pippa & Goose Kite Tram", None, None),
        ("STOWAWAY_1979_DISC_1", "Stowaway", 1979, 1),
        ("IRONHELM_SPECIAL_EDITION_D2", "Ironhelm", None, 2),
        ("GLASS_HUNTER_2061", "Glass Hunter 2061", None, None),
        ("THE_BRICK_MOVIE", "The Brick Movie", None, None),
        ("BRAWLER_II", "Brawler II", None, None),
        ("UNDERTOW_UHD", "Undertow", None, None),
        ("VANTAGE_WS_PAL", "Vantage", None, None),
        ("KEEPER_OF_THE_LANTERNS", "Keeper of the Lanterns", None, None),
        ("DUSTY_HAL_WW", "Dusty Hal", None, None),
        ("EMBER_INTL_NORDIC", "Ember", None, None),
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
    p = parse_label("FELIX_HOLLOW_AND_THE_ALCHEMIST")
    assert p.queries == ["Felix Hollow and the Alchemist", "Felix Hollow"]


def test_bdmt_title_cleanup():
    assert clean_bdmt_title("The Brass Heron - 4K Ultra HD") == "The Brass Heron"
    assert clean_bdmt_title("Drift (Disc 1)") == "Drift"
    assert clean_bdmt_title("The Brass Heron") == "The Brass Heron"


def test_name_differs_from_label():
    assert name_differs_from_label("The Brass Heron", "THE_BRASS_HERON")
    assert not name_differs_from_label("MOONLIT_ORCHARD", "MOONLIT_ORCHARD")
