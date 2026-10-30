"""Labels that are empty, generic, numeric, noisy, long or not ASCII."""

from __future__ import annotations

import pytest

from ripshelf.identify.label import clean_bdmt_title, name_differs_from_label, parse_label


@pytest.mark.parametrize("label", [None, "", "   ", "DVD_VIDEO", "dvd_video", "BDROM", "NO_LABEL", "LOGICAL_VOLUME_ID"])
def test_generic_or_empty(label):
    p = parse_label(label)
    assert p.generic and p.queries == []


@pytest.mark.parametrize("label", ["DISC_1", "BD", "DISC", "D1", "1", "12345678", "DVD9_PAL"])
def test_noise_only_labels_give_no_useful_query(label):
    p = parse_label(label)
    # The first token is kept (a film may be called "1"), but nothing beyond it.
    assert len(p.title.split()) <= 1


def test_numeric_titles_survive():
    assert parse_label("1917").title == "1917"
    assert parse_label("2001_A_SPACE_ODYSSEY").title == "2001 a Space Odyssey"
    assert parse_label("BLADE_RUNNER_2049").year is None


def test_year_is_split_off():
    p = parse_label("DUNE_2021")
    assert p.title == "Dune" and p.year == 2021


def test_disc_numbers():
    assert parse_label("LOTR_FELLOWSHIP_DISC_2").disc_number == 2
    assert parse_label("GLADIATOR_D1").disc_number == 1
    assert parse_label("MATRIX_1").disc_number == 1


def test_very_long_label_is_handled_and_truncation_query_added():
    long = "THE_LORD_OF_THE_RINGS_THE_RETURN_OF_THE_KING_EXTENDED_EDITION_DISC_1" * 3
    p = parse_label(long)
    assert p.title.startswith("The Lord of the Rings")
    assert len(p.queries) == 2


def test_punctuation_and_ampersand():
    assert parse_label("FAST_&_FURIOUS").title == "Fast & Furious"
    assert parse_label("FAST&FURIOUS").title == "Fast & Furious"
    assert parse_label("MR.SMITH-GOES").title == "Mr Smith Goes"
    assert parse_label("WHO'S_AFRAID").title == "Who's Afraid"


def test_unicode_labels():
    assert parse_label("AMÉLIE").title == "Amélie"
    assert parse_label("千と千尋の神隠し").title == "千と千尋の神隠し"


def test_editions_are_dropped():
    p = parse_label("ALIEN_DIRECTORS_CUT_WS")
    assert p.title == "Alien" and "Directors Cut" in p.dropped


def test_bdmt_cleanup_edges():
    assert clean_bdmt_title(None) is None
    assert clean_bdmt_title("") is None
    assert clean_bdmt_title(" - Blu-ray") is None
    assert clean_bdmt_title("Dune: Part Two – 4K Ultra HD") == "Dune: Part Two"
    assert clean_bdmt_title("Alien (Disk 2)") == "Alien"


def test_name_differs_edges():
    assert not name_differs_from_label(None, "X")
    assert not name_differs_from_label("", None)
    assert name_differs_from_label("Dune", None)
    assert not name_differs_from_label(" dune ", "DUNE")
