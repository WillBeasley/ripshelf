"""The eSCL client when the scanner misbehaves: jams, open covers, busy, gone, odd replies.

A scripted scanner (httpx.MockTransport) answers each request; tests/test_scans.py has
the well-behaved one (dev/fake_escl.py) and the scans it makes.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import httpx
import pytest

from ripshelf.scanner import escl

FIXTURES = Path(__file__).parent / "fixtures"
CAPS = escl.parse_capabilities((FIXTURES / "escl-capabilities-mfc-l8690cdw.xml").read_bytes())
JOB = "http://scanner.test/eSCL/ScanJobs/7"
NS = 'xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm"'


def status(state: str = "Idle", adf: str = "ScannerAdfLoaded") -> httpx.Response:
    return httpx.Response(200, text=f"<scan:ScannerStatus {NS}><pwg:State>{state}</pwg:State>"
                                    f"<scan:AdfState>{adf}</scan:AdfState></scan:ScannerStatus>")


def scanner(*pages: httpx.Response | Exception, start: list | None = None,
            st: httpx.Response | Exception | None = None, cancel: Exception | None = None):
    """An Escl client whose scanner starts a job (or answers ``start`` in turn), then
    answers each NextDocument with ``pages`` in turn, and its status with ``st``."""
    starts = list(start or [httpx.Response(201, headers={"Location": "/eSCL/ScanJobs/7"})])
    nexts = list(pages)
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(f"{request.method} {path}")
        if request.method == "DELETE":
            if cancel:
                raise cancel
            return httpx.Response(200)
        reply = (starts.pop(0) if path.endswith("/ScanJobs") else nexts.pop(0) if path.endswith("/NextDocument")
                 else st or status())
        if isinstance(reply, Exception):
            raise reply
        return reply

    return escl.Escl("http://scanner.test", transport=httpx.MockTransport(handle), busy_wait_s=0), seen


JPEG = httpx.Response(200, content=b"\xff\xd8page")
DONE = httpx.Response(404)


async def test_the_pages_then_the_end():
    e, seen = scanner(JPEG, JPEG, DONE)
    got = []

    async def on_page(n, data):
        got.append(n)

    assert await e.scan(CAPS, "platen", 300, on_page=on_page) == [b"\xff\xd8page"] * 2
    assert got == [1, 2] and "DELETE /eSCL/ScanJobs/7" not in seen


@pytest.mark.parametrize(("adf", "message", "code"), [
    ("ScannerAdfJam", "The paper is jammed in the feeder", "problem"),
    ("ScannerAdfHatchOpen", "The feeder's cover is open", "problem"),
    ("ScannerAdfEmpty", "The feeder is empty: put the pages in it face up", "empty"),
])
async def test_a_feeder_that_stops_says_why(adf, message, code):
    # Loaded at the start, then the page request fails and the status says why.
    states = [status(adf="ScannerAdfLoaded"), status(adf=adf)]
    e, _ = scanner(httpx.Response(500))
    e._transport = _with_status(e._transport, states)
    with pytest.raises(escl.ScannerError) as err:
        await e.scan(CAPS, "feeder", 300)
    assert str(err.value) == message and err.value.code == code


def _with_status(transport, states):
    inner = transport.handler

    def handle(request):
        if request.url.path.endswith("/ScannerStatus"):
            return states.pop(0) if len(states) > 1 else states[0]
        return inner(request)

    return httpx.MockTransport(handle)


async def test_a_stopped_scanner_suggests_an_open_cover():
    e, _ = scanner(httpx.Response(500), st=status(state="Stopped"))
    with pytest.raises(escl.ScannerError, match="HTTP 500 while scanning; the scanner has stopped .*cover may be open"):
        await e.scan(CAPS, "platen", 300)


async def test_without_a_status_the_plain_reason():
    e, seen = scanner(httpx.Response(500), st=httpx.ConnectError("gone"))
    with pytest.raises(escl.ScannerError, match="^The scanner answered HTTP 500 while scanning$"):
        await e.scan(CAPS, "platen", 300)
    assert "DELETE /eSCL/ScanJobs/7" in seen, "the job is cancelled, not left on the scanner"


async def test_no_pages_at_all():
    e, _ = scanner(DONE)
    with pytest.raises(escl.ScannerError, match="finished without sending a page"):
        await e.scan(CAPS, "platen", 300)


async def test_the_scanner_going_away_mid_scan_cancels_the_job():
    e, seen = scanner(JPEG, httpx.ReadTimeout("slow"))
    with pytest.raises(escl.ScannerError, match="didn't answer [(]ReadTimeout[)]") as err:
        await e.scan(CAPS, "platen", 300)
    assert err.value.code == "offline" and seen[-1] == "DELETE /eSCL/ScanJobs/7"


async def test_a_failed_cancel_is_only_logged(caplog):
    e, _ = scanner(httpx.Response(500), st=status(), cancel=httpx.ConnectError("gone"))
    with caplog.at_level(logging.INFO, logger="ripshelf.scanner.escl"), pytest.raises(escl.ScannerError):
        await e.scan(CAPS, "platen", 300)
    assert "could not cancel scan job" in caplog.text


async def test_busy_pages_are_waited_for():
    e, _ = scanner(httpx.Response(503), httpx.Response(503), JPEG, DONE)
    assert len(await e.scan(CAPS, "platen", 300)) == 1


async def test_an_empty_feeder_is_refused_before_starting():
    e, seen = scanner(st=status(adf="ScannerAdfEmpty"))
    with pytest.raises(escl.ScannerError) as err:
        await e.scan(CAPS, "feeder", 300)
    assert err.value.code == "empty" and not any("ScanJobs" in s for s in seen)


async def test_a_feeder_scan_goes_ahead_when_the_status_cant_be_read():
    e, _ = scanner(JPEG, DONE, st=httpx.Response(500))
    assert len(await e.scan(CAPS, "feeder", 300)) == 1


@pytest.mark.parametrize(("start", "message", "code"), [
    ([httpx.Response(409)], "The scanner is busy (in use from its panel or another computer)", "busy"),
    ([httpx.Response(503)] * escl.BUSY_RETRIES, "The scanner is busy (in use from its panel or another computer)",
     "busy"),
    ([httpx.Response(400)], "The scanner refused the scan (HTTP 400)", "bad_reply"),
    ([httpx.Response(201)], "The scanner refused the scan (HTTP 201)", "bad_reply"),  # no Location
    ([httpx.ConnectError("refused")], "The scanner didn't answer (ConnectError): is it on, and is the address "
                                      "right?", "offline"),
])
async def test_a_scan_that_cant_start(start, message, code):
    e, _ = scanner(start=start)
    with pytest.raises(escl.ScannerError) as err:
        await e.scan(CAPS, "platen", 300)
    assert str(err.value) == message and err.value.code == code


async def test_busy_then_free_starts():
    e, _ = scanner(JPEG, DONE, start=[httpx.Response(503), httpx.Response(201, headers={"Location": JOB})])
    assert len(await e.scan(CAPS, "platen", 300)) == 1


@pytest.mark.parametrize(("reply", "message"), [
    (httpx.Response(401), "The scanner answered HTTP 401 to ScannerCapabilities"),
    (httpx.ConnectError("refused"), "The scanner didn't answer (ConnectError)"),
])
async def test_capabilities_that_cant_be_had(reply, message):
    def handle(request):
        if isinstance(reply, Exception):
            raise reply
        return reply

    with pytest.raises(escl.ScannerError, match=re.escape(message)):
        await escl.Escl("http://scanner.test", transport=httpx.MockTransport(handle)).capabilities()


def test_a_status_that_isnt_xml():
    with pytest.raises(escl.ScannerError, match="status isn't readable XML") as err:
        escl.parse_status(b"<html>login")
    assert err.value.code == "bad_reply"


def test_resolutions_given_as_a_range_and_odd_numbers():
    caps = escl.parse_capabilities(f"""<scan:ScannerCapabilities {NS}><scan:Platen><scan:PlatenInputCaps>
        <scan:MaxWidth>wide</scan:MaxWidth>
        <scan:SettingProfiles><scan:SettingProfile><scan:SupportedResolutions><scan:XResolutionRange>
        <scan:Min>100</scan:Min><scan:Max>300</scan:Max></scan:XResolutionRange>
        </scan:SupportedResolutions></scan:SettingProfile></scan:SettingProfiles>
        </scan:PlatenInputCaps></scan:Platen></scan:ScannerCapabilities>""".encode())
    assert caps.platen.resolutions == [100, 150, 200, 300]
    assert caps.platen.max_width == 2550  # not a number, so the default (A4/Letter)
    assert caps.feeder is None and caps.version == "2.0"


def test_a_scanner_with_only_a_feeder():
    caps = escl.parse_capabilities(f"""<scan:ScannerCapabilities {NS}><scan:Adf><scan:AdfSimplexInputCaps>
        <scan:MaxHeight>4200</scan:MaxHeight></scan:AdfSimplexInputCaps></scan:Adf>
        </scan:ScannerCapabilities>""".encode())
    assert caps.platen is None and caps.feeder.max_height == 4200 and not caps.duplex
    with pytest.raises(escl.ScannerError, match="no glass"):
        escl.scan_settings(caps, "platen", 300)


def test_a_colour_mode_the_scanner_lacks_falls_back_to_one_it_has():
    caps = escl.parse_capabilities(f"""<scan:ScannerCapabilities {NS}><scan:Platen><scan:PlatenInputCaps>
        <scan:ColorModes><scan:ColorMode>Grayscale8</scan:ColorMode></scan:ColorModes>
        </scan:PlatenInputCaps></scan:Platen></scan:ScannerCapabilities>""".encode())
    assert "<scan:ColorMode>Grayscale8</scan:ColorMode>" in escl.scan_settings(caps, "platen", 300)
