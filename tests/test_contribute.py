"""Contributing discs to TheDiscDB. The payload, what is never sent, eligibility,
releases, sending (against a mocked server, never the real one) and the API."""

from __future__ import annotations

import getpass
import json
import socket
import time
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from ripshelf.db import JobRecord, now
from ripshelf.dev.fake import FakeDrives
from ripshelf.jobs import to_title_info
from ripshelf.main import create_app
from ripshelf.makemkv.parse import parse_info
from ripshelf.models import (
    Candidate,
    Contribution,
    ContributionExtra,
    DiscKind,
    DiscMatch,
    Episode,
    EpisodeRef,
    MediaKind,
    Show,
    TitleMap,
    TitleRole,
    TvPlan,
)
from ripshelf.providers import contribute, thediscdb

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://discdb.test"
HASH = "0123456789abcdef0123456789abcdef"
RELEASE = "6f1c0b8e-1111-4222-8333-944455556666"


def scan_titles(name: str):
    return [to_title_info(t) for t in parse_info((FIXTURES / name).read_text()).titles]


def robot_job(job_id: int = 1, **kw) -> JobRecord:
    titles = scan_titles("scan-uhd-wild-robot.log")
    job = JobRecord(id=job_id, drive_id="fake-pioneer", device="/dev/sr2", state="done", created_at=now(),
                    updated_at=now(), titles=titles, main_title=titles[0],
                    identity=Candidate(tmdb_id=1184918, title="The Wild Robot", year=2024, score=0.97),
                    identity_source="user", disc_ids={"content_hash": HASH, "disc_id": "AB" * 20})
    job.disc.label, job.disc.kind = "THE_WILD_ROBOT", DiscKind.uhd
    return job.model_copy(update=kw)


def sarah_job(job_id: int = 2, disc_number: int | None = 2) -> JobRecord:
    titles = scan_titles("scan-dvd-sarah-duck.log")
    show = Show(tmdb_id=61990, name="Sarah & Duck", year=2013)
    mapping = [TitleMap(title_index=0, duration_s=titles[0].duration_s, role=TitleRole.play_all)]
    mapping += [TitleMap(title_index=t.index, duration_s=t.duration_s, role=TitleRole.episode,
                         episodes=[EpisodeRef(season=3, episode=t.index)]) for t in titles[1:3]]
    mapping += [TitleMap(title_index=titles[3].index, duration_s=titles[3].duration_s, role=TitleRole.extra)]
    job = JobRecord(id=job_id, drive_id="fake-ihas122", state="done", created_at=now(), updated_at=now(),
                    titles=titles, media=MediaKind.tv, identity_source="user",
                    identity=Candidate(tmdb_id=61990, title="Sarah & Duck", year=2013),
                    tv=TvPlan(show=show, season=3, disc_number=disc_number, mapping=mapping,
                              episodes=[Episode(season=3, episode=1, name="The Brolly Bus")]),
                    disc_ids={"content_hash": HASH.upper()})
    job.disc.label, job.disc.kind = "SARAH_&_DUCK_BROLLY_BUS", DiscKind.dvd
    return job


def named(job: JobRecord, *extras: ContributionExtra) -> JobRecord:
    """The user went through the titles, naming these extras (or none)."""
    job.contribution = Contribution(items_confirmed=True, extras=list(extras))
    return job


@pytest.fixture
def scan_log(tmp_path):
    p = tmp_path / "scan.log"
    p.write_text((FIXTURES / "scan-uhd-wild-robot.log").read_text())
    return p


# --- payload ------------------------------------------------------------------------


def test_film_payload_shape():
    job = robot_job()
    body = contribute.payload(job, RELEASE, 1)
    assert body["engram_version"].startswith("ripshelf/") and body["export_version"] == "1"
    assert body["contribution_tier"] == 2 and body["exported_at"].endswith("+00:00")
    assert body["disc"] == {"content_hash": HASH.upper(), "volume_label": "THE_WILD_ROBOT", "content_type": "4k",
                            "disc_number": 1, "release_id": RELEASE}
    assert body["identification"] == {"tmdb_id": 1184918, "detected_title": "The Wild Robot",
                                      "classification_source": "ripshelf", "classification_confidence": 1.0}
    # TheDiscDB names each item after its source_filename, so the film goes as its title.
    main = body["titles"][0]
    assert main["title_type"] == "MainMovie" and main["source_filename"] == "The Wild Robot"
    assert main["segment_map"] == "294" and main["segment_count"] == 1
    assert main["match_source"] == "user" and main["match_confidence"] == 1.0
    assert main["duration_seconds"] == job.titles[0].duration_s and main["size_bytes"] == job.titles[0].size_bytes
    # And it makes an item of every title listed, so nothing else goes until the user names it.
    assert len(body["titles"]) == 1 and len(job.titles) > 1


def test_named_extras_are_sent_under_their_names():
    job = robot_job(identity_source="auto")
    extra, trailer = job.titles[1].index, job.titles[2].index
    named(job, ContributionExtra(title_index=trailer, kind="Trailer", name="Theatrical Trailer"),
          ContributionExtra(title_index=extra, name="Making Of"),
          ContributionExtra(title_index=job.main_title.index, name="Not the film"),  # the film is the film
          ContributionExtra(title_index=999, name="Gone"))
    body = contribute.payload(job, RELEASE, 1)
    got = [(t["index"], t["title_type"], t["source_filename"], t["match_source"]) for t in body["titles"]]
    assert got == [(job.main_title.index, "MainMovie", "The Wild Robot", "ripshelf"),
                   (extra, "Extra", "Making Of", "user"), (trailer, "Trailer", "Theatrical Trailer", "user")]


def test_auto_confirmed_film_carries_its_score():
    body = contribute.payload(robot_job(identity_source="auto"), RELEASE, 1)
    assert body["identification"]["classification_confidence"] == 0.97
    assert body["titles"][0]["match_source"] == "ripshelf"


def test_tv_payload_episodes_and_extras():
    job = sarah_job()
    body = contribute.payload(job, RELEASE, 2)
    assert body["disc"]["content_type"] == "dvd" and body["identification"]["detected_season"] == 3
    by_index = {t["index"]: t for t in body["titles"]}
    # The play-all, and the extra nobody has named yet, are left out.
    assert sorted(by_index) == [1, 2]
    assert by_index[1] | {"title_type": "Episode", "season": "3", "episode": "1",
                          "source_filename": "The Brolly Bus"} == by_index[1]
    assert by_index[2]["source_filename"] == "Episode 2"  # no name from TMDB
    named(job, ContributionExtra(title_index=3, name="Sing-along"))
    by_index = {t["index"]: t for t in contribute.payload(job, RELEASE, 2)["titles"]}
    assert by_index[3]["title_type"] == "Extra" and by_index[3]["source_filename"] == "Sing-along"
    assert "episode" not in by_index[3]


def test_naming_list():
    job = sarah_job()
    rows = {t.title_index: t for t in contribute.naming_titles(job)}
    assert (rows[1].fixed, rows[1].name) == ("Episode", "The Brolly Bus")
    assert rows[3].fixed is None and rows[3].suggested and not rows[0].suggested  # the mapping's extra
    film = {t.title_index: t for t in contribute.naming_titles(robot_job())}
    assert [(i, t.name) for i, t in film.items() if t.fixed] == [(0, "The Wild Robot")]
    assert contribute.todo(robot_job()) and contribute.todo(named(robot_job())) is None


@pytest.mark.parametrize("value, n", [("294", 1), ("1,2,3", 3), ("1-4", 4), ("1-3,7", 4), ("", None),
                                      (None, None), ("x", None)])
def test_segment_count(value, n):
    assert contribute.segment_count(value) == n


def test_nothing_private_is_sent(scan_log, tmp_path):
    job = robot_job()
    sent = json.dumps(contribute.payload(job, RELEASE, 1)) + contribute.scan_log(scan_log)
    raw = scan_log.read_text()
    # The fixture has all of these, in drive lines and messages.
    for private in ("/dev/sr", "SN0000000001", "LibreDrive", "PIONEER", "BDR-S11JX", "makemkv.com"):
        assert private in raw
        assert private not in sent, private
    for local in (str(tmp_path), socket.gethostname(), job.drive_id, job.device, f"/home/{getpass.getuser()}"):
        assert local not in sent, local
    lines = contribute.scan_log(scan_log).splitlines()
    assert lines and all(ln.startswith(("CINFO:", "TINFO:", "SINFO:", "TCOUNT:")) for ln in lines)
    assert sum(ln.startswith("TINFO:") for ln in lines) == sum(ln.startswith("TINFO:") for ln in raw.splitlines())


def test_scan_log_missing_or_without_titles(tmp_path):
    assert contribute.scan_log(tmp_path / "nope.log") is None
    (tmp_path / "s.log").write_text('MSG:1005,0,1,"x"\nDRV:0,2,999,1,"a","b","/dev/sr0"\n')
    assert contribute.scan_log(tmp_path / "s.log") is None


# --- eligibility -----------------------------------------------------------------------


@pytest.mark.parametrize("change, reason", [
    ({}, None),
    ({"mode": "off"}, "Contributing is off"),
    ({"disc_match": DiscMatch(media=MediaKind.movie, title="The Wild Robot")}, "TheDiscDB already knows"),
    ({"identity": None}, "Confirm what the disc is"),
    ({"disc_ids": {}}, "content hash is missing"),
    ({"disc_ids": {"content_hash": "not-hex"}}, "content hash is missing"),
    ({"main_title": None}, "no main feature"),
    ({"kind": DiscKind.other}, "Only DVDs"),
    ({"log": False}, "scan of the disc is missing"),
])
def test_eligibility(settings, scan_log, change, reason):
    known_disc = "disc_match" in change
    settings.thediscdb_contribute = change.pop("mode", "ask")
    kind = change.pop("kind", None)
    log = scan_log if change.pop("log", True) else scan_log.with_name("missing.log")
    job = robot_job(**change)
    if kind:
        job.disc.kind = kind
    ok, why, known = contribute.eligibility(settings, job, log)
    assert ok is (reason is None)
    assert (reason in why) if reason else why is None
    assert known is known_disc


def test_tv_needs_its_episodes(settings, scan_log):
    settings.thediscdb_contribute = "ask"
    job = sarah_job()
    assert contribute.eligibility(settings, job, scan_log)[0]
    job.tv.mapping = []
    assert contribute.eligibility(settings, job, scan_log)[1] == "Confirm the episodes first"


@pytest.mark.parametrize("mode, source, sent", [("auto", "user", True), ("auto", "auto", False),
                                                ("auto", "cache", False), ("ask", "user", False),
                                                ("off", "user", False)])
def test_only_user_confirmed_discs_go_automatically(settings, mode, source, sent):
    settings.thediscdb_contribute = mode
    assert contribute.auto_send(settings, robot_job(identity_source=source)) is sent


# --- sending ----------------------------------------------------------------------------


def contributor(settings, **kw):
    s = settings.model_copy(update={"thediscdb_contribute_url": BASE + "/", **kw})
    return contribute.Contributor(s)


@respx.mock
async def test_disc_then_scan_log(settings, scan_log):
    disc = respx.post(f"{BASE}/api/engram/disc").mock(
        return_value=httpx.Response(200, json={"id": 7, "contentHash": HASH.upper(), "updated": False}))
    log = respx.post(f"{BASE}/api/engram/disc/{HASH.upper()}/logs/scan").mock(
        return_value=httpx.Response(200, json={"path": "x"}))
    body = contribute.payload(robot_job(), RELEASE, 1)
    c = contributor(settings)
    reply = await c.send(body, contribute.scan_log(scan_log))
    await c.aclose()
    assert reply["id"] == 7
    assert [call.request.url.path for call in respx.calls] == ["/api/engram/disc",
                                                                f"/api/engram/disc/{HASH.upper()}/logs/scan"]
    assert json.loads(disc.calls[0].request.content) == body
    sent_log = log.calls[0].request
    assert sent_log.headers["content-type"].startswith("text/plain")
    assert sent_log.content.decode().startswith(("CINFO:", "TCOUNT:"))
    assert "authorization" not in disc.calls[0].request.headers
    assert "authorization" not in sent_log.headers


@respx.mock
async def test_api_key_only_when_set(settings):
    from pydantic import SecretStr

    respx.post(f"{BASE}/api/engram/disc").mock(return_value=httpx.Response(200, json={}))
    respx.post(url__regex=rf"{BASE}/api/engram/disc/.*/logs/scan").mock(return_value=httpx.Response(200))
    c = contributor(settings, thediscdb_api_key=SecretStr("k3y"))
    await c.send(contribute.payload(robot_job(), RELEASE, 1), "TINFO:0,2,0,\"x\"\n")
    await c.aclose()
    assert {call.request.headers["authorization"] for call in respx.calls} == {"ApiKey k3y"}


@pytest.mark.parametrize("disc, log, message", [
    (httpx.Response(500), None, "refused the disc: HTTP 500"),
    (httpx.Response(400, text='"Invalid JSON payload"'), None, "HTTP 400 (Invalid JSON payload)"),
    (httpx.ReadTimeout("slow"), None, "did not answer in time (disc)"),
    (httpx.ConnectError("down"), None, "could not reach TheDiscDB (ConnectError, disc)"),
    (httpx.Response(200, json={}), httpx.Response(404, text="No disc found"), "refused the scan log: HTTP 404"),
    (httpx.Response(200, json={}), httpx.ReadTimeout("slow"), "did not answer in time (scan log)"),
])
async def test_send_errors(settings, disc, log, message):
    with respx.mock(assert_all_called=False) as router:
        d = router.post(f"{BASE}/api/engram/disc")
        d.mock(side_effect=disc) if isinstance(disc, Exception) else d.mock(return_value=disc)
        lr = router.post(url__regex=rf"{BASE}/api/engram/disc/.*/logs/scan")
        if log is None:
            lr.mock(return_value=httpx.Response(200))
        else:
            lr.mock(side_effect=log) if isinstance(log, Exception) else lr.mock(return_value=log)
        c = contributor(settings)
        with pytest.raises(contribute.ContributeError, match=message.replace("(", r"\(").replace(")", r"\)")):
            await c.send(contribute.payload(robot_job(), RELEASE, 1), "TINFO:0,2,0,\"x\"\n")
        await c.aclose()
        if log is None:
            assert not lr.called  # nothing after a refused disc


async def test_malformed_ids_are_not_sent(settings):
    body = contribute.payload(robot_job(), "not a release id!", 1)
    with respx.mock(assert_all_called=False) as router:
        route = router.post(f"{BASE}/api/engram/disc")
        c = contributor(settings)
        with pytest.raises(contribute.ContributeError, match="malformed"):
            await c.send(body, "")
        await c.aclose()
    assert not route.called


async def test_the_real_site_is_never_called_from_tests(settings):
    c = contribute.Contributor(settings.model_copy(update={"thediscdb_contribute_url": "https://thediscdb.com"}))
    with pytest.raises(AssertionError, match="real TheDiscDB"):
        await c.send(contribute.payload(robot_job(), RELEASE, 1), "")
    await c.aclose()


# --- the manager, releases, sending in the background ---------------------------------------


def put(h, job: JobRecord) -> JobRecord:
    """Store a finished job (made by hand) with its scan log."""
    rec = h.db.create_job(job.drive_id, "/dev/sr0")
    job = job.model_copy(update={"id": rec.id})
    h.db.save_job(job)
    work = h.m.work_dir(job)
    work.mkdir(parents=True, exist_ok=True)
    name = "scan-dvd-sarah-duck.log" if job.media == MediaKind.tv else "scan-uhd-wild-robot.log"
    (work / "scan.log").write_text((FIXTURES / name).read_text())
    return job


async def test_releases_group_discs_of_one_title(harness, settings):
    settings.thediscdb_contribute = "ask"
    async with harness() as h:
        a, b = put(h, robot_job()), put(h, robot_job())
        va, vb = h.m.contribution_view(a.id), h.m.contribution_view(b.id)
        assert va.payload["disc"]["release_id"] == vb.payload["disc"]["release_id"]
        assert (va.contribution.disc_number, vb.contribution.disc_number) == (1, 2)
        assert va.contribution.contribute_url == f"{BASE}/contribute/engram/{va.contribution.release_id}"
        # Kept once given.
        assert h.m.contribution_view(a.id).contribution.release_id == va.contribution.release_id
        # A release of its own, and back.
        own = await h.m.contribution_release(b.id, True)
        assert own.contribution.release_id != va.contribution.release_id and own.contribution.disc_number == 1
        assert own.contribution.new_release and own.payload["disc"]["release_id"] == own.contribution.release_id
        back = await h.m.contribution_release(b.id, False)
        assert back.contribution.release_id == va.contribution.release_id and back.contribution.disc_number == 2
        # Another disc type, or long after, makes a new release.
        c = robot_job()
        c.disc.kind = DiscKind.bluray
        vc = h.m.contribution_view(put(h, c).id)
        assert vc.contribution.release_id != va.contribution.release_id
        old = (now() - timedelta(days=contribute.RELEASE_WINDOW_DAYS + 1)).isoformat()
        h.db.conn.execute("UPDATE contributions SET assigned_at=?", (old,))
        assert h.m.contribution_view(put(h, robot_job()).id).contribution.release_id != va.contribution.release_id


async def test_tv_discs_keep_their_numbers(harness, settings):
    settings.thediscdb_contribute = "ask"
    async with harness() as h:
        d2, d1 = put(h, sarah_job(disc_number=2)), put(h, sarah_job(disc_number=1))
        other_season = sarah_job(disc_number=1)
        other_season.tv.season = 4
        v2, v1, v4 = (h.m.contribution_view(j.id) for j in (d2, d1, put(h, other_season)))
    assert v1.contribution.release_id == v2.contribution.release_id != v4.contribution.release_id
    assert (v2.contribution.disc_number, v1.contribution.disc_number, v4.contribution.disc_number) == (2, 1, 1)


async def wait_status(h, job_id, *statuses):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        c = h.db.get_job(job_id).contribution
        if c and c.status in statuses:
            return c
        await __import__("asyncio").sleep(0.02)
    raise AssertionError("contribution did not finish")


async def test_send_in_the_background_retry_and_events(harness, settings):
    settings.thediscdb_contribute = "ask"
    with respx.mock(assert_all_called=False) as router:
        disc = router.post(f"{BASE}/api/engram/disc")
        disc.mock(return_value=httpx.Response(503))
        router.post(f"{BASE}/api/engram/disc/{HASH.upper()}/logs/scan").mock(return_value=httpx.Response(200))
        async with harness() as h:
            job = put(h, named(robot_job()))
            view = await h.m.contribute(job.id)
            assert view.contribution.status == "sending" and not view.eligible
            with pytest.raises(ValueError, match="already"):
                await h.m.contribute(job.id)
            c = await wait_status(h, job.id, "sent", "failed")
            assert c.status == "failed" and "HTTP 503" in c.error
            disc.mock(return_value=httpx.Response(200, json={"id": 1, "updated": True}))
            await h.m.contribute(job.id)
            c = await wait_status(h, job.id, "sent", "failed")
            events = [e.message for e in h.db.events(job.id)]
            state = h.db.get_job(job.id).state
    assert c.status == "sent" and c.updated is True and c.error is None and c.sent_at
    assert any(e.startswith("Not sent to TheDiscDB (TheDiscDB refused the disc: HTTP 503") for e in events)
    assert any(e.startswith("Sent to TheDiscDB (disc 1 of release") and c.contribute_url in e for e in events)
    assert state == "done"  # the job itself is untouched


async def test_content_hash_recovered_from_the_stored_structure(harness, settings):
    # Jobs read through a worker before it sent the file sizes have no content hash;
    # a Blu-ray's comes back from the clip info files kept with the job.
    settings.thediscdb_contribute = "ask"
    async with harness() as h:
        job = put(h, robot_job(disc_ids={"disc_id": "AB" * 20}))
        view = h.m.contribution_view(job.id)
        assert not view.eligible and "Insert the disc again" in view.reason
        ci = h.m.work_dir(job) / "structure" / "BDMV" / "CLIPINF"
        ci.mkdir(parents=True)
        (ci / "00001.clpi").write_bytes(b"HDMV0300" + bytes(48) + (1000).to_bytes(4, "big") + bytes(20))
        view = h.m.contribution_view(job.id)
        want = thediscdb.content_hash([("BDMV/STREAM/00001.m2ts", 192000)])
        assert view.payload["disc"]["content_hash"] == want and view.reason is None
        assert h.db.get_job(job.id).disc_ids == {"disc_id": "AB" * 20, "content_hash": want}  # kept


async def test_not_eligible_raises(harness, settings):
    settings.thediscdb_contribute = "ask"
    async with harness() as h:
        job = put(h, robot_job(disc_match=DiscMatch(media=MediaKind.movie, title="The Wild Robot")))
        view = h.m.contribution_view(job.id)
        assert view.known and not view.eligible and view.payload is None
        with pytest.raises(ValueError, match="already knows"):
            await h.m.contribute(job.id)
        with pytest.raises(ValueError, match="already knows"):
            await h.m.contribution_release(job.id, True)
        with pytest.raises(KeyError):
            await h.m.contribute(999)


async def test_restart_while_sending(harness, settings):
    async with harness() as h:
        job = put(h, robot_job())
        job = h.db.get_job(job.id)
        job.contribution = contribute_state("sending")
        h.db.save_job(job)
    async with harness() as h:
        c = h.db.get_job(job.id).contribution
    assert c.status == "failed" and "restarted" in c.error


def contribute_state(status):
    from ripshelf.models import Contribution

    return Contribution(status=status, release_id=RELEASE, disc_number=1)


async def test_auto_mode_sends_once_the_user_confirms(harness, settings):
    settings.thediscdb_contribute = "auto"
    with respx.mock(assert_all_called=False) as router:
        disc = router.post(f"{BASE}/api/engram/disc").mock(return_value=httpx.Response(200, json={"id": 3}))
        router.post(url__regex=rf"{BASE}/api/engram/disc/.*/logs/scan").mock(return_value=httpx.Response(200))
        async with harness("/dev/fake-sr1") as h:
            job = await h.wait_job(lambda j: j.state == "review")
            assert job.disc_ids.get("content_hash") and job.contribution is None
            await h.m.confirm(job.id, Candidate(tmdb_id=9819, title="Practical Magic", year=1998))
            # Not before the user has been through the titles.
            assert h.db.get_job(job.id).contribution is None and not h.m._contrib_tasks
            await h.m.contribution_extras(job.id, [])
            c = await wait_status(h, job.id, "sent", "failed")
            await h.wait_job(lambda j: j.state == "done")
    assert c.status == "sent", c.error
    body = json.loads(disc.calls[0].request.content)
    assert body["identification"]["tmdb_id"] == 9819 and body["disc"]["content_type"] == "dvd"
    assert [t["source_filename"] for t in body["titles"]] == ["Practical Magic"]
    assert body["disc"]["content_hash"] == job.disc_ids["content_hash"].upper()


async def test_auto_mode_leaves_auto_confirmed_discs(harness, settings, discdb):
    settings.thediscdb_contribute = "auto"
    async with harness("/dev/fake-sr1") as h:
        job = await h.wait_job(lambda j: j.state == "review")
        h.m._maybe_contribute(job.id)  # not confirmed, so nothing
        await h.m._settle_identity(h.db.get_job(job.id), Candidate(tmdb_id=9819, title="Practical Magic"), "auto")
        assert h.db.get_job(job.id).contribution is None and not h.m._contrib_tasks
        await h.wait_job(lambda j: j.state == "done")


# --- API ---------------------------------------------------------------------------------


@pytest.fixture
def client(settings):
    app = create_app(settings, backend=FakeDrives(present=set()))
    with TestClient(app) as c:
        yield c


def test_settings_round_trip(client):
    s = client.get("/api/settings").json()
    assert s["thediscdb_contribute"] == "off" and s["thediscdb_api_key_set"] is False
    r = client.put("/api/settings", json={"thediscdb_contribute": "ask", "thediscdb_api_key": "secret-key",
                                          "thediscdb_contribute_url": " https://discdb.example/ "})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["thediscdb_contribute"] == "ask" and s["thediscdb_api_key_set"] is True
    assert s["thediscdb_contribute_url"] == "https://discdb.example" and "secret-key" not in r.text
    assert client.put("/api/settings", json={"thediscdb_contribute_url": "discdb.example"}).status_code == 400
    assert client.put("/api/settings", json={"thediscdb_contribute": "sometimes"}).status_code == 422
    s = client.put("/api/settings", json={"thediscdb_api_key": "", "thediscdb_contribute_url": ""}).json()
    assert s["thediscdb_api_key_set"] is False and s["thediscdb_contribute_url"] == "https://thediscdb.com"


def test_api_preview_send_and_release(client, settings):
    m = client.app.state.manager
    rec = m.db.create_job("fake-pioneer", "/dev/sr0")
    job = robot_job(rec.id)
    m.db.save_job(job)
    m.work_dir(job).mkdir(parents=True)
    (m.work_dir(job) / "scan.log").write_text((FIXTURES / "scan-uhd-wild-robot.log").read_text())

    off = client.get(f"/api/jobs/{job.id}/thediscdb").json()
    assert off["mode"] == "off" and not off["eligible"] and off["payload"] is None
    assert client.post(f"/api/jobs/{job.id}/thediscdb/send").status_code == 409
    client.put("/api/settings", json={"thediscdb_contribute": "ask", "thediscdb_contribute_url": BASE})

    view = client.get(f"/api/jobs/{job.id}/thediscdb").json()
    assert not view["eligible"] and view["todo"].startswith("Name the extras") and view["reason"] is None
    assert view["scan_log_lines"] > 100 and view["base_url"] == BASE and len(view["titles"]) == len(job.titles)
    # Nothing goes before the extras are named.
    r = client.post(f"/api/jobs/{job.id}/thediscdb/send")
    assert r.status_code == 409 and "Name the extras" in r.text
    url = f"/api/jobs/{job.id}/thediscdb/extras"
    assert client.put(url, json={"extras": [{"title_index": 1, "name": "  "}]}).status_code == 422
    assert client.put(url, json={"extras": [{"title_index": 1, "kind": "Poster", "name": "x"}]}).status_code == 422
    for bad, why in (([0], "sent as the film"), ([1, 1], "twice"), ([999], "no title 999")):
        r = client.put(url, json={"extras": [{"title_index": i, "name": "x"} for i in bad]})
        assert r.status_code == 409 and why in r.text, r.text
    view = client.put(url, json={"extras": [{"title_index": 1, "kind": "Featurette", "name": " Making Of "}]}).json()
    assert view["eligible"] and view["todo"] is None
    assert view["contribution"]["extras"] == [{"title_index": 1, "kind": "Featurette", "name": "Making Of"}]
    assert [t["source_filename"] for t in view["payload"]["titles"]] == ["The Wild Robot", "Making Of"]
    assert view["payload"]["disc"]["release_id"] == view["contribution"]["release_id"]
    new = client.post(f"/api/jobs/{job.id}/thediscdb/release", json={"new_release": True}).json()
    assert new["contribution"]["new_release"] is True
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{BASE}/api/engram/disc").mock(return_value=httpx.Response(200, json={"id": 1}))
        router.post(url__regex=rf"{BASE}/api/engram/disc/.*/logs/scan").mock(return_value=httpx.Response(200))
        r = client.post(f"/api/jobs/{job.id}/thediscdb/send")
        assert r.status_code == 200 and r.json()["contribution"]["status"] == "sending"
        end = time.monotonic() + 10
        while client.get(f"/api/jobs/{job.id}").json()["contribution"]["status"] == "sending":
            assert time.monotonic() < end
            time.sleep(0.05)
    sent = client.get(f"/api/jobs/{job.id}").json()["contribution"]
    assert sent["status"] == "sent" and sent["contribute_url"].startswith(f"{BASE}/contribute/engram/")
    # Renaming after it was sent asks for it to be sent again.
    again = client.put(url, json={"extras": []}).json()
    assert again["contribution"]["status"] == "not_sent" and [t["index"] for t in again["payload"]["titles"]] == [0]
    assert client.put("/api/jobs/999/thediscdb/extras", json={"extras": []}).status_code == 404
    for path in ("/api/jobs/999/thediscdb",):
        assert client.get(path).status_code == 404
    assert client.post("/api/jobs/999/thediscdb/send").status_code == 404
    assert client.post("/api/jobs/999/thediscdb/release", json={"new_release": True}).status_code == 404
