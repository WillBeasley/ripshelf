from ripshelf.makemkv.parse import (
    ProgressTracker,
    choose_main_feature,
    parse_duration,
    parse_info,
    split_fields,
)


def test_split_fields_handles_escaped_quotes():
    f = split_fields('3348,16908288,2,"The AACS dump file \\"file:///x.tgz\\" was sent","fmt","AACS"')
    assert f[3] == 'The AACS dump file "file:///x.tgz" was sent'
    assert f[5] == "AACS"


def test_parse_duration():
    assert parse_duration("1:41:41") == 6101
    assert parse_duration("0:07:01") == 421


def test_uhd_scan(fixture_text):
    scan = parse_info(fixture_text("scan-uhd-wild-robot.log"))
    assert scan.makemkv_version == "2.0.0"
    assert scan.kind == "uhd"
    assert scan.disc_name == "The Wild Robot"
    assert scan.volume_name == "THE_WILD_ROBOT"
    assert scan.title_count == 24 and len(scan.titles) == 24
    assert scan.libredrive and "LibreDrive" in scan.libredrive
    assert scan.drive_serial("/dev/sr2") == "SN0000000001"
    t0 = scan.titles[0]
    assert t0.duration_s == 6101
    assert t0.size_bytes == 62492817408
    assert t0.chapters == 20
    assert t0.source_file == "00801.mpls"
    v = t0.video
    assert v.resolution == "3840x2160" and v.framerate == "23.976"
    assert "dvhe.07" in v.codec
    audio = [s for s in t0.streams if s.kind == "audio"]
    assert audio[0].codec == "TrueHD Atmos" and audio[0].lang == "eng"


def test_uhd_main_feature_skips_duplicate_playlist(fixture_text):
    main = choose_main_feature(parse_info(fixture_text("scan-uhd-wild-robot.log")))
    assert main.title.index == 0
    assert main.duplicates == [13]  # 00294.m2ts, same 1:41:41 content
    assert main.rivals == []


def test_pal_dvd_scan(fixture_text):
    scan = parse_info(fixture_text("scan-dvd-practical-magic.log"))
    assert scan.makemkv_version == "2.0.0"
    assert scan.kind == "dvd"
    assert scan.disc_name == "PRACTICAL_MAGIC"
    main = choose_main_feature(scan)
    assert main.title.duration_s == 5983
    assert main.title.video.framerate == "25"
    assert main.title.video.resolution == "720x576"


def test_dvd_play_all_is_longest(fixture_text):
    scan = parse_info(fixture_text("scan-dvd-sarah-duck.log"))
    assert len(scan.titles) == 11
    main = choose_main_feature(scan)
    assert main.title.duration_s == 4210
    assert sum(1 for t in scan.titles if t.duration_s == 421) == 10


def test_volume_key_unknown(fixture_text):
    scan = parse_info(fixture_text("scan-uhd-volume-key-unknown.log"))
    assert scan.volume_key_unknown
    assert scan.failed_open
    assert scan.title_count == 0
    assert choose_main_feature(scan) is None


def test_invalid_key(fixture_text):
    assert parse_info(fixture_text("scan-bad-key.log")).key_invalid


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_progress_ignores_scan_phase_and_reports_bytes(fixture_text):
    clock = FakeClock()
    tr = ProgressTracker(bytes_total=1_000_000_000, clock=clock)
    lines = fixture_text("rip-dvd-episode.log").splitlines()
    saw_saving = False
    for line in lines:
        clock.t += 0.1
        tr.feed(line)
        if line.startswith("PRGT:5024"):
            saw_saving = True
            assert tr.fraction == 0.0
    assert saw_saving
    snap = tr.snapshot()
    assert snap.fraction == 1.0
    assert snap.bytes_done == 1_000_000_000
    assert snap.bytes_per_s and snap.bytes_per_s > 0


def test_progress_uhd_partial(fixture_text):
    clock = FakeClock()
    tr = ProgressTracker(bytes_total=62492817408, clock=clock)
    for line in fixture_text("rip-uhd-partial.log").splitlines():
        clock.t += 0.5
        tr.feed(line)
    snap = tr.snapshot()
    assert 0 < snap.fraction < 1
    assert snap.phase == "Saving to MKV file"
    assert snap.eta_s and snap.eta_s > 0


def test_progress_max_is_third_field():
    tr = ProgressTracker(bytes_total=100)
    tr.feed('PRGT:5024,0,"Saving all titles to MKV files"')
    assert tr.feed("PRGV:100,25,100")
    assert tr.fraction == 0.25
    assert not tr.feed("PRGV:0,0,0")  # max 0 must not divide
