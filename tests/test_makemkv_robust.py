"""MakeMKV robot output that is broken, truncated or unexpected."""

from __future__ import annotations

import pytest

from ripshelf.makemkv.parse import (
    ProgressTracker,
    choose_main_feature,
    parse_duration,
    parse_info,
    split_fields,
)


@pytest.mark.parametrize("line", [
    "", "\n", "garbage", "msg:1,2,3", "MSG", "MSG:", "TINFO:", "TINFO:x,y,z,w", "TINFO:0,9",
    'MSG:abc,0,1,"text"', "SINFO:0,0", 'CINFO:2,0', "TCOUNT:", "TCOUNT:many", "DRV:0",
    "PRGV:1", "PRGV:a,b,c", ":::", "MSG:\x00,\x00", 'MSG:1005,0,1,"unterminated',
])
def test_malformed_lines_are_skipped(line):
    res = parse_info(line)
    assert res.titles == [] or all(t.duration_s == 0 for t in res.titles)


def test_nul_bytes_do_not_crash_the_field_splitter():
    assert split_fields('1,"a\x00b",2') in ([], ["1", "a\x00b", "2"])
    assert parse_info('MSG:1,"a\x00b"\nTINFO:0,9,0,"1:00:00"').titles[0].duration_s == 3600


def test_unknown_message_codes_are_kept_but_ignored():
    res = parse_info('MSG:99999,0,0,"Something new in MakeMKV 3","%1"\nTCOUNT:0')
    assert [m.code for m in res.messages] == [99999]
    assert not res.volume_key_unknown and not res.key_invalid and not res.failed_open
    assert res.title_count == 0 and res.titles == []


def test_zero_titles(fixture_text):
    res = parse_info(fixture_text("scan-uhd-volume-key-unknown.log"))
    assert res.titles == []
    assert choose_main_feature(res) is None
    assert res.volume_key_unknown


def test_truncated_scan_keeps_complete_titles(fixture_text):
    text = fixture_text("scan-dvd-moonlit-orchard.log")
    cut = text[: len(text) * 2 // 3]
    cut = cut[: cut.rfind("\n") + 25]  # end mid-line
    res = parse_info(cut)
    full = parse_info(text)
    assert 0 < len(res.titles) <= len(full.titles)
    assert res.makemkv_version == full.makemkv_version


def test_non_utf8_replacement_characters_survive():
    text = b'CINFO:2,0,"Caf\xe9 \xff\xfe"\nTINFO:0,9,0,"1:30:00"\n'.decode("utf-8", "replace")
    res = parse_info(text)
    assert res.disc_name.startswith("Caf")
    assert res.titles[0].duration_s == 5400


def test_duplicate_and_out_of_order_attributes():
    res = parse_info('TINFO:3,9,0,"0:10:00"\nTINFO:1,9,0,"2:00:00"\nTINFO:3,9,0,"0:20:00"\nTINFO:1,11,0,"12"')
    assert [t.index for t in res.titles] == [1, 3]
    assert res.titles[1].duration_s == 1200  # last value wins
    assert res.titles[0].size_bytes == 12


def test_size_and_chapters_tolerate_empty_values():
    res = parse_info('TINFO:0,8,0,""\nTINFO:0,11,0,""\nTINFO:0,9,0,"1:00:00"')
    assert res.titles[0].chapters == 0 and res.titles[0].size_bytes == 0


@pytest.mark.parametrize("s, want", [("1:02:03", 3723), ("45:00", 2700), ("7", 7), ("", 0), ("x:y", 0)])
def test_parse_duration_edges(s, want):
    assert parse_duration(s) == want


def test_unknown_disc_type():
    assert parse_info('CINFO:1,6206,"HD-DVD disc"').kind == "other"
    assert parse_info("").kind is None


def test_drive_serial_missing():
    res = parse_info('DRV:0,2,999,12,"BDROM","LABEL","/dev/sr0"')
    assert res.drive_serial() is None
    assert parse_info("").drive_serial() is None


# --- progress ---------------------------------------------------------------------


def tracker(**kw) -> ProgressTracker:
    t = iter(range(0, 10_000, 10))
    return ProgressTracker(bytes_total=1000, clock=lambda: next(t), **kw)


def test_prgv_with_max_zero_is_ignored():
    p = tracker()
    p.feed('PRGT:5024,0,"Saving all titles to MKV files"')
    assert not p.feed("PRGV:0,0,0")
    assert p.fraction == 0


def test_prgv_total_above_max_is_clamped():
    p = tracker()
    p.feed('PRGT:5024,0,"Saving"')
    p.feed("PRGV:10,70000,65536")
    assert p.fraction == 1.0


@pytest.mark.parametrize("line", ["PRGV:", "PRGV:1,2", "PRGV:a,b,c", "PRGT:", "PRGT:x,0,name", "PRGC:", "nonsense"])
def test_progress_survives_garbage(line):
    p = tracker()
    p.feed('PRGT:5024,0,"Saving"')
    assert p.feed(line) in (False, True)
    assert 0 <= p.snapshot().fraction <= 1


def test_progress_before_saving_phase_does_not_count():
    p = tracker()
    p.feed('PRGT:5018,0,"Scanning CD-ROM devices"')
    assert not p.feed("PRGV:65536,65536,65536")
    assert p.snapshot().fraction == 0


def test_progress_rate_and_eta():
    p = tracker()
    p.feed('PRGT:5024,0,"Saving"')
    p.feed("PRGV:0,6553,65536")
    p.feed("PRGV:0,13107,65536")
    snap = p.snapshot()
    assert snap.bytes_per_s and snap.bytes_per_s > 0
    assert snap.eta_s and snap.eta_s > 0
    assert snap.bytes_done == int(snap.fraction * 1000)


def test_repeated_prgv_is_not_a_change():
    p = tracker()
    p.feed('PRGT:5024,0,"Saving"')
    assert p.feed("PRGV:0,100,65536")
    assert not p.feed("PRGV:0,100,65536")


def test_libredrive_required_real_log(fixture_text):
    res = parse_info(fixture_text("scan-uhd-libredrive-required.log"))
    assert res.cannot_decrypt and res.failed_open and not res.titles
    assert not res.volume_key_unknown


def test_serial_comes_from_the_scanned_drive_not_the_last_listed(fixture_text):
    # Real scan of /dev/sr1 in a container that sees all four drives.
    res = parse_info(fixture_text("scan-dvd-moonlit-orchard.log"))
    assert set(res.drives) == {"/dev/sr0", "/dev/sr1", "/dev/sr2", "/dev/sr3"}
    assert res.drive_serial("/dev/sr1") == "SN0000000002"
    assert res.drive_serial("/dev/sr2") == "SN0000000001"
    assert res.drive_serial("/dev/sr9") is None
    assert res.drive_serial() is None  # ambiguous without a device


def test_dvd_cell_maps_are_not_treated_as_duplicates():
    # Two same-length DVD titles both report cells "1,2" (local to each title).
    text = "\n".join([
        'CINFO:1,6206,"DVD disc"',
        'TINFO:0,9,0,"0:45:00"', 'TINFO:0,11,0,"1000"', 'TINFO:0,26,0,"1,2"',
        'TINFO:1,9,0,"0:45:00"', 'TINFO:1,11,0,"2000"', 'TINFO:1,26,0,"1,2"',
    ])
    main = choose_main_feature(parse_info(text))
    assert main.duplicates == [] and main.rivals == [1]
    bd = parse_info(text.replace("DVD disc", "Blu-ray disc"))
    assert choose_main_feature(bd).duplicates == [1]
