"""Edge cases in the label parser, TV alignment and MakeMKV parsing."""

from __future__ import annotations

import pytest

from ripshelf.identify.label import parse_label
from ripshelf.makemkv.parse import parse_info
from ripshelf.models import Episode, TitleInfo
from ripshelf.tv import mapping
from ripshelf.tv.mapping import align, describe, runtime_cost

# --- labels -------------------------------------------------------------------------------


@pytest.mark.parametrize("label, title, disc, dropped, extended", [
    ("LOTR_FOTR_EXT_DISC2", "Lotr Fotr", 2, ["EXT", "DISC2"], True),
    ("TDKR_D2", "Tdkr", 2, ["D2"], False),
    ("ALIEN_DC", "Alien", None, ["DC"], True),
    ("BLADE_RUNNER_DIRECTORS_CUT", "Blade Runner", None, ["Directors Cut"], True),
    ("FILM_2004_UNRATED", "Film", None, ["Unrated", "2004"], True),
    ("THE_MATRIX_1999_WS", "The Matrix", None, ["1999", "WS"], False),
    ("BRAVE_UK", "Brave", None, ["UK"], False),
    ("SAW_X", "Saw X", None, [], False),
])
def test_label_tokens(label, title, disc, dropped, extended):
    p = parse_label(label)
    assert (p.raw, p.title, p.disc_number, p.dropped, p.extended) == (label, title, disc, dropped, extended)


def test_disc_marker_needs_a_title_before_it():
    # A leading token is the title, even if it looks like a disc marker.
    assert parse_label("D2_FILM").title == "D2 Film" and parse_label("D2_FILM").disc_number is None
    assert parse_label("SE_FILM").title.startswith("Se")


def test_a_bare_trailing_digit_is_also_tried_as_a_sequel():
    assert parse_label("SHREK_2").queries == ["Shrek", "Shrek 2"]
    assert parse_label("SHREK_2").disc_number == 2
    # An explicit disc marker is not a sequel number.
    assert parse_label("SHREK_DISC2").queries == ["Shrek"]


def test_generic_label_keeps_its_raw_text():
    assert parse_label("DVD_VIDEO").raw == "DVD_VIDEO"


# --- TV alignment -------------------------------------------------------------------------


def t(i: int, minutes: float) -> TitleInfo:
    return TitleInfo(index=i, duration_s=int(minutes * 60), size_bytes=1, chapters=1)


def ep(n: int, runtime: int | None, name: str | None = None) -> Episode:
    return Episode(season=1, episode=n, name=name or f"E{n}", runtime=runtime)


def test_runtime_cost_slack_pal_and_unknown():
    assert runtime_cost(30 * 60, 30, pal=False) == 0.0
    assert runtime_cost(32 * 60, 30, pal=False) == 0.0  # 2 min slack
    assert runtime_cost(34 * 60, 30, pal=False) == pytest.approx(2 / 30 * 2)
    assert runtime_cost(28.8 * 60, 30, pal=True) == 0.0  # 28.8 x 25/23.976 = 30.03
    assert runtime_cost(28.8 * 60, 34, pal=False) > 0
    assert runtime_cost(60 * 60, None, pal=False) == mapping.UNKNOWN_RUNTIME_COST
    assert runtime_cost(500 * 60, 30, pal=False) == 1.0


def test_align_uses_pal_when_told():
    titles = [t(1, 42.2), t(2, 42.2)]  # PAL, 44 min episodes
    eps = [ep(1, 44), ep(2, 44)]
    with_pal = align(titles, eps, pal=True)
    assert [a.cost for a in with_pal] == [0.0, 0.0]
    assert "(PAL speed-up)" in describe(titles[0], with_pal[0], pal=True)
    assert "(PAL speed-up)" not in describe(titles[0], with_pal[0], pal=False)


def test_align_two_part_episode_and_an_extra():
    titles = [t(1, 30), t(2, 60), t(3, 5), t(4, 30)]
    eps = [ep(1, 30), ep(2, 30), ep(3, 30), ep(4, 30)]
    out = align(titles, eps)
    assert [a.title_index if a else None for a in out] == [1, 2, None, 4]
    assert [e.episode for e in out[1].episodes] == [2, 3]
    assert out[2] is None and describe(titles[2], None, False) == "no episode fits this title's length"
    assert describe(titles[1], out[1], False) == "60 min matches “E2” + “E3” (60 min)"


def test_align_two_part_with_an_unknown_half_costs_the_unknown_rate():
    out = align([t(1, 60)], [ep(1, 30), ep(2, None)])
    # One half has no runtime, so the pair can't be checked, so it costs the "unknown" rate.
    assert [e.episode for e in out[0].episodes] == [1, 2]
    assert out[0].cost == mapping.UNKNOWN_RUNTIME_COST
    assert "TMDB has no runtime" in describe(t(1, 30), mapping.Assignment(1, [ep(9, None, "X")], 0.5), False)


def test_describe_verdicts():
    title = t(1, 30)
    assert "is close to" in describe(title, mapping.Assignment(1, [ep(1, 33)], 0.2), False)
    assert "does not match" in describe(title, mapping.Assignment(1, [ep(1, 50)], 0.9), False)


# --- MakeMKV info parsing -----------------------------------------------------------------


def test_message_flags_params_and_drive_fields():
    res = parse_info("\n".join([
        'MSG:1005,7,1,"MakeMKV v2.0.0 linux(x64-release) started","%1 started","MakeMKV v2.0.0 linux(x64-release)"',
        'DRV:3,2,999,12,"HD-DVD-ROM HL-DT-ST BDDVDRW GGC-H20N 1.01 SN000000004","RENTAL_FAMILY","/dev/sr3"',
        'DRV:4,256,999,0,"","",""',
        'DRV:5,0,999,0,"ignored","",""',
    ]))
    assert res.messages[0].flags == 7 and res.messages[0].params == ["MakeMKV v2.0.0 linux(x64-release)"]
    assert res.makemkv_version == "2.0.0"
    d = res.drives["/dev/sr3"]
    assert (d.index, d.visibility, d.flags, d.disc_name) == (3, 2, 12, "RENTAL_FAMILY")
    assert list(res.drives) == ["/dev/sr3"], "empty slots and drives without a device are skipped"


def test_version_without_a_number_is_kept_as_it_is():
    res = parse_info('MSG:1005,0,1,"MakeMKV beta started","%1 started","MakeMKV beta"')
    assert res.makemkv_version == "MakeMKV beta"


def test_bad_lines_are_skipped_not_fatal():
    res = parse_info('garbage\nMSG:notanumber,0,0,"x"\nTCOUNT:2\nCINFO:2,0,"Name"\n')
    assert res.title_count == 2 and res.disc_name == "Name"
