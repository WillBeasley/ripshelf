"""Plain-word causes for MakeMKV failures, from real logs."""

from __future__ import annotations

import pytest

from ripshelf.makemkv.diagnose import diagnose
from ripshelf.makemkv.parse import Message, parse_info
from ripshelf.models import JobState

from .conftest import FIXTURES

PM = "/dev/fake-sr1"


def msgs(name: str) -> list[Message]:
    return parse_info((FIXTURES / name).read_text()).messages


def read_error(err: str, where: str = "/BDMV/STREAM/00001.m2ts") -> Message:
    return Message(code=2003, flags=0, text=f"Error '{err}' occurred while reading '{where}' at offset '0'",
                   params=[err, where, "0"])


def test_medium_errors_name_the_file_and_count():
    d = diagnose(msgs("scan-bd-medium-errors.log"))
    assert d.code == "disc_unreadable"
    assert "/BDMV/STREAM/01064.m2ts" in d.cause and "25 errors" in d.cause
    assert "Clean the disc" in d.hint
    assert "MEDIUM ERROR" in d.detail


def test_probe_errors_on_other_drives_are_ignored():
    # Every scan has "SCRAMBLED SECTOR" errors from probing the other drives; a good scan has no cause.
    assert diagnose(msgs("scan-dvd-moonlit-orchard.log")) is None


def test_scrambled_sector_on_this_disc_is_an_auth_problem():
    d = diagnose([read_error("Scsi error - ILLEGAL REQUEST:READ OF SCRAMBLED SECTOR WITHOUT AUTHENTICATION",
                             "/VIDEO_TS/VTS_01_1.VOB")])
    assert d.code == "disc_auth" and "region" in d.hint


def test_hardware_fault_beats_a_later_summary():
    d = diagnose([read_error("Scsi error - HARDWARE ERROR:TIMEOUT ON LOGICAL UNIT"),
                  Message(code=5004, flags=0, text="0 titles saved, 1 failed")])
    assert d.code == "drive_hardware"


@pytest.mark.parametrize("err, code", [  # the strings in the MakeMKV forum's read-error FAQ
    ("Scsi error - MEDIUM ERROR:POSITIONING ERROR DETECTED BY READ OF MEDIUM", "disc_unreadable"),
    ("Scsi error - MEDIUM ERROR:READ RETRIES EXHAUSTED", "disc_unreadable"),
    ("OS error - STATUS_DEVICE_DATA_ERROR", "disc_unreadable"),
    ("Scsi error - MEDIUM ERROR:UNABLE TO RECOVER TABLE-OF-CONTENTS", "disc_toc"),
    ("Scsi error - ILLEGAL REQUEST:ILLEGAL MODE FOR THIS TRACK", "aacs_too_new"),
    ("Scsi error - NOT READY:CANNOT READ MEDIUM - INCOMPATIBLE FORMAT", "disc_format"),
    ("Scsi error - NOT READY:MEDIUM NOT PRESENT", "disc_not_ready"),
])
def test_forum_read_errors(err, code):
    assert diagnose([read_error(err)]).code == code


def test_unknown_output_has_no_diagnosis():
    assert diagnose([Message(code=5004, flags=0, text="0 titles saved, 1 failed")]) is None


async def test_scan_timeout_says_why(harness, settings, mkv_script):
    settings.scan_timeout_s = 1
    settings.makemkvcon = mkv_script(info="replay('scan-bd-medium-errors.log'); time.sleep(60)")
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.failed)
        p = job.problem
        assert p.code == "scan_timeout"
        assert "couldn't read /BDMV/STREAM/01064.m2ts" in p.message and "1 s" in p.message
        assert p.hint and "MEDIUM ERROR" in p.detail


async def test_rip_failure_says_why(harness, settings, mkv_script):
    settings.makemkvcon = mkv_script(rip="""
        out('PRGT:5024,0,"Saving all titles to MKV files"')
        out('MSG:2003,0,3,"Error \\'Scsi error - MEDIUM ERROR:L-EC UNCORRECTABLE ERROR\\' occurred while reading \\'/VIDEO_TS/VTS_02_1.VOB\\' at offset \\'1234\\'","x","Scsi error - MEDIUM ERROR:L-EC UNCORRECTABLE ERROR","/VIDEO_TS/VTS_02_1.VOB","1234"')
        out('MSG:5004,0,0,"0 titles saved, 1 failed","%1"')
        sys.exit(1)
    """)
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.failed)
        assert job.problem.code == "rip_failed"
        assert "/VIDEO_TS/VTS_02_1.VOB" in job.problem.message
        assert job.problem.hint
