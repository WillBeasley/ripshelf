"""A job's debug bundle: what's in it, what's noted as missing, and secrets taken out."""

from __future__ import annotations

import io
import json
import logging
import zipfile

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from ripshelf import debugbundle
from ripshelf.debugbundle import REDACTED, redact, redact_text, secret_key
from ripshelf.dev.fake import FakeDrives
from ripshelf.main import create_app
from ripshelf.models import Candidate, CoverScan, JobState, Problem, TitleInfo

PM = "/dev/fake-sr1"
TMDB_KEY = "0123456789abcdef0123456789abcdef"
MAKEMKV_KEY = "T-" + "Ab3@_" * 14


def unzip(body: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        return {n: z.read(n) for n in z.namelist()}


# --- redaction --------------------------------------------------------------------


@pytest.mark.parametrize("name", ["tmdb_api_key", "apikey", "api-key", "X-Api-Key", "token", "plex_token",
                                  "pushover_user_key", "makemkv_key", "tvdb_pin", "client_secret", "password",
                                  "passwd", "Authorization", "Cookie", "set-cookie", "key", "access_token",
                                  "serial"])
def test_secret_key_names(name):
    assert secret_key(name)


@pytest.mark.parametrize("name", ["show_key", "group_key", "token_id", "keywords", "title", "disc_ids",
                                  "monkey", "tokens_used", "author", "pinned"])
def test_ordinary_key_names(name):
    assert not secret_key(name)


def test_redact_nested_keys_and_leaves_the_rest():
    data = {"tmdb_api_key": "abc123", "title": "Moonlit Orchard",
            "providers": [{"name": "tmdb", "headers": {"Authorization": "Bearer xyz", "Accept": "json"}}],
            "emby_api_key": None, "show": {"key": "tmdb:1399", "name": "Seagull Manor"},
            "scan": {"key": "abcdef123456"}}
    out = redact(data)
    assert out["tmdb_api_key"] == REDACTED
    assert out["title"] == "Moonlit Orchard"
    assert out["providers"][0]["headers"] == {"Authorization": REDACTED, "Accept": "json"}
    assert out["emby_api_key"] is None, "an unset secret still shows as unset"
    assert out["show"]["key"] == "tmdb:1399", "a show's key is an id"
    assert out["scan"]["key"] == REDACTED
    assert data["tmdb_api_key"] == "abc123", "the original is unchanged"


@pytest.mark.parametrize(("text", "gone"), [
    ("GET https://api.themoviedb.org/3/search/movie?query=brass&api_key=s3cr3tvalue&year=2001", "s3cr3tvalue"),
    ("GET https://example.test/x?apikey=s3cr3tvalue", "s3cr3tvalue"),
    ("GET https://plex.test/library?X-Plex-Token=s3cr3tvalue", "s3cr3tvalue"),
    ("http://ripper:hunter22@emby.local:8096/", "hunter22"),
    ("Authorization: Bearer eyJhbGciOi.s3cr3tvalue", "s3cr3tvalue"),
    ("headers={'authorization': 'Basic dXNlcjpwYXNz'}", "dXNlcjpwYXNz"),
    ('{"vision_api_key": "sk-s3cr3tvalue"}', "s3cr3tvalue"),
    ("Cookie: session=s3cr3tvalue; theme=dark", "s3cr3tvalue"),
    ("token=s3cr3tvalue other=fine", "s3cr3tvalue"),
    ("password: s3cr3tvalue", "s3cr3tvalue"),
    (f"app_Key = {MAKEMKV_KEY}", MAKEMKV_KEY),
    (f"MakeMKV registration key {MAKEMKV_KEY} rejected", MAKEMKV_KEY),
])
def test_redact_text(text, gone):
    out = redact_text(text)
    assert gone not in out and REDACTED in out


def test_redact_text_keeps_ordinary_lines():
    line = "2026-11-11 18:05:16,123 INFO ripshelf.jobs: job 3: title 2 is 01:42:13, 27.1 GB (token count 4)"
    assert redact_text(line) == line


def test_redact_known_values_anywhere():
    out = redact_text(f"tried {TMDB_KEY} and x{TMDB_KEY}y", [TMDB_KEY, "", "ab"])
    assert TMDB_KEY not in out and out.count(REDACTED) == 2


# --- the bundle --------------------------------------------------------------------


@pytest.fixture
def client(settings):
    settings.tmdb_api_key = SecretStr(TMDB_KEY)
    settings.makemkv_key = SecretStr(MAKEMKV_KEY)
    with TestClient(create_app(settings, backend=FakeDrives(present=set()))) as c:
        yield c


def get_bundle(c: TestClient, job_id: int) -> tuple[dict[str, bytes], dict]:
    r = c.get(f"/api/jobs/{job_id}/debug-bundle")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    disp = r.headers["content-disposition"]
    assert disp.startswith(f'attachment; filename="ripshelf-job-{job_id}-') and disp.endswith('.zip"')
    files = unzip(r.content)
    return files, json.loads(files["manifest.json"])


def assert_no_secrets(files: dict[str, bytes]):
    for name, data in files.items():
        for secret in (TMDB_KEY, MAKEMKV_KEY, "s3cr3tvalue"):
            assert secret.encode() not in data, f"{secret} in {name}"


def test_bundle_of_a_job_with_little_data(client):
    m = client.app.state.manager
    job = m.db.create_job("fake-ihas122", PM)
    files, manifest = get_bundle(client, job.id)
    assert {"job.json", "timeline.json", "disc/titles.json", "disc/identity.json", "settings.json",
            "versions.json", "README.txt", "manifest.json", "logs/ripshelf-job.log"} <= set(files)
    assert {f["path"] for f in manifest["files"]} == set(files)
    s = manifest["sections"]
    assert s["job"] == "included"
    assert s["titles"].startswith("missing")
    assert s["job folder"].startswith("missing")
    assert s["scan.log"].startswith("missing") and s["box scans"].startswith("missing")
    assert manifest["not_stored_by_ripshelf"]
    readme = files["README.txt"].decode()
    assert f"job {job.id}" in readme and "State:    detected" in readme
    settings = json.loads(files["settings.json"])
    assert settings["tmdb_api_key"] == REDACTED and settings["omdb_api_key"] is None
    assert json.loads(files["versions.json"])["ripshelf"]
    assert_no_secrets(files)


def test_bundle_of_a_job_with_everything(client, settings):
    m = client.app.state.manager
    job = m.db.create_job("fake-ihas122", PM)
    job.state = JobState.failed
    job.disc.label = "BRASS_HERON"
    job.titles = [TitleInfo(index=0, duration_s=6000, size_bytes=30_000_000_000, chapters=12)]
    job.candidates = [Candidate(title="The Brass Heron", year=2001, tmdb_id=1234, score=0.82)]
    job.problem = Problem(code="rip_failed", message="MakeMKV stopped at 41%")
    m.db.save_job(job)
    m.db.add_event(job.id, "state", "Scanning the disc")
    m.db.add_event(job.id, "error", "MakeMKV stopped at 41%")
    work = m.work_dir(job)
    (work / "scans").mkdir(parents=True)
    (work / "scan.log").write_text(f"MSG:5021,0,0,\"key {MAKEMKV_KEY}\"\nTINFO:0,2,0,\"The Brass Heron\"\n")
    (work / "rip.log").write_text("GET https://api.test/movie?api_key=s3cr3tvalue\nfailed at 41%\n")
    (work / "transcode-0.log").write_text(f"ffmpeg -i in.mkv out.mkv tmdb {TMDB_KEY}\n")
    (work / "scans" / "a1.vision.json").write_text(json.dumps({"title": "The Brass Heron",
                                                               "api_key": "s3cr3tvalue"}))
    (work / "scans" / "a1.jpg").write_bytes(b"\xff\xd8small")
    (work / "scans" / "big.jpg").write_bytes(b"\xff\xd8" + b"0" * (debugbundle.IMAGE_MAX + 1))
    job.scans = [CoverScan(id="a1", kind="front", source="upload", created_at=job.created_at,
                           content_type="image/jpeg", width=10, height=10, sha256="0" * 64, size_bytes=7)]
    m.db.save_job(job)
    logging.getLogger("ripshelf.jobs").warning("job %s: rip failed (token=s3cr3tvalue)", job.id)

    files, manifest = get_bundle(client, job.id)
    assert files["work/scan.log"].decode().endswith("\"The Brass Heron\"\n")
    assert "failed at 41%" in files["work/rip.log"].decode()
    assert "work/transcode-0.log" in files
    assert json.loads(files["work/scans/a1.vision.json"]) == {"title": "The Brass Heron", "api_key": REDACTED}
    assert files["work/scans/a1.jpg"] == b"\xff\xd8small"
    assert "work/scans/big.jpg" not in files
    assert [x["path"].endswith("big.jpg") for x in manifest["listed_not_included"]] == [True]
    timeline = json.loads(files["timeline.json"])
    assert [e["message"] for e in timeline] == ["Scanning the disc", "MakeMKV stopped at 41%"]
    ident = json.loads(files["disc/identity.json"])
    assert ident["candidates"][0]["title"] == "The Brass Heron" and ident["disc"]["label"] == "BRASS_HERON"
    assert json.loads(files["disc/titles.json"])[0]["duration_s"] == 6000
    assert len(json.loads(files["disc/scans.json"])["scans"]) == 1
    assert f"job {job.id}: rip failed" in files["logs/ripshelf-job.log"].decode()
    assert json.loads(files["drive.json"])["attached"] is True
    s = manifest["sections"]
    assert s["titles"] == s["identification"] == s["box scans"] == s["job folder"] == s["app log"] == "included"
    assert s["worker"] == "missing: the drive is on this machine"
    assert "MakeMKV stopped at 41%" in files["README.txt"].decode()
    assert_no_secrets(files)


def test_bundle_of_no_such_job(client):
    assert client.get("/api/jobs/999/debug-bundle").status_code == 404


def test_job_log_keeps_tracebacks_and_the_window(settings):
    from datetime import UTC, datetime, timedelta

    path = settings.data_dir / "logs" / "ripshelf.log"
    path.parent.mkdir(parents=True)
    t = datetime(2026, 11, 11, 12, 0, 0).astimezone()

    def ts(minutes):
        return (t + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S,000")

    path.write_text("\n".join([
        f"{ts(-30)} INFO ripshelf.jobs: job 7: old line",
        f"{ts(0)} INFO ripshelf.jobs: job 7 started",
        f"{ts(1)} ERROR ripshelf.jobs: job 7 crashed",
        "Traceback (most recent call last):",
        "  ValueError: boom",
        f"{ts(2)} INFO ripshelf.jobs: job 71 started",
        f"{ts(3)} INFO ripshelf.hw: drive polled",
        f"{ts(60)} INFO ripshelf.hw: much later",
    ]) + "\n")
    about, during = debugbundle.job_log(settings.data_dir, 7, t.astimezone(UTC), (t + timedelta(minutes=5)).astimezone(UTC))
    assert len(about) == 3 and "ValueError: boom" in about[2]
    assert not any("job 71" in a for a in about)
    assert [d.split(": ", 1)[1].splitlines()[0] for d in during] == [
        "job 7 started", "job 7 crashed", "job 71 started", "drive polled"]


@pytest.mark.slow
async def test_bundle_of_a_finished_rip(harness):
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review)
        await h.m.confirm(job.id, Candidate(source="manual", title="Moonlit Orchard", year=1998))
        done = await h.wait_job(lambda j: j.state == JobState.done)
        name, fh = await debugbundle.build(h.m, done.id)
        files = unzip(b"".join(debugbundle.chunks(fh)))
    assert name.startswith(f"ripshelf-job-{done.id}-")
    assert "work/scan.log" in files and "work/rip.log" in files
    assert json.loads(files["disc/titles.json"])
    out = json.loads(files["output.json"])
    assert out["rip"]["state"] == "done" and out["destination_exists"] is True
    assert json.loads(files["job.json"])["identity"]["title"] == "Moonlit Orchard"
    assert any(e["kind"] == "state" for e in json.loads(files["timeline.json"]))
