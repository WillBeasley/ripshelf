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
    assert parse_label("1747").title == "1747"
    assert parse_label("2087_A_STAR_VOYAGE").title == "2087 a Star Voyage"
    assert parse_label("GLASS_HUNTER_2061").year is None


def test_year_is_split_off():
    p = parse_label("DRIFT_2021")
    assert p.title == "Drift" and p.year == 2021


def test_disc_numbers():
    assert parse_label("KOTL_GUILD_DISC_2").disc_number == 2
    assert parse_label("IRONHELM_D1").disc_number == 1
    assert parse_label("LATTICE_1").disc_number == 1


def test_very_long_label_is_handled_and_truncation_query_added():
    long = "THE_KEEPER_OF_THE_LANTERNS_THE_RETURN_OF_THE_WARDEN_EXTENDED_EDITION_DISC_1" * 3
    p = parse_label(long)
    assert p.title.startswith("The Keeper of the Lanterns")
    assert len(p.queries) == 2


def test_punctuation_and_ampersand():
    assert parse_label("SWIFT_&_FIERCE").title == "Swift & Fierce"
    assert parse_label("SWIFT&FIERCE").title == "Swift & Fierce"
    assert parse_label("MR.JONES-GOES").title == "Mr Jones Goes"
    assert parse_label("WHO'S_COUNTING").title == "Who's Counting"


def test_unicode_labels():
    assert parse_label("ÉLODIE").title == "Élodie"
    assert parse_label("星降る町の灯台").title == "星降る町の灯台"


def test_editions_are_dropped():
    p = parse_label("STOWAWAY_DIRECTORS_CUT_WS")
    assert p.title == "Stowaway" and "Directors Cut" in p.dropped


def test_bdmt_cleanup_edges():
    assert clean_bdmt_title(None) is None
    assert clean_bdmt_title("") is None
    assert clean_bdmt_title(" - Blu-ray") is None
    assert clean_bdmt_title("Drift: Part Two – 4K Ultra HD") == "Drift: Part Two"
    assert clean_bdmt_title("Stowaway (Disk 2)") == "Stowaway"


def test_name_differs_edges():
    assert not name_differs_from_label(None, "X")
    assert not name_differs_from_label("", None)
    assert name_differs_from_label("Drift", None)
    assert not name_differs_from_label(" drift ", "DRIFT")
