"""TV discs with every episode in one long title: detection, cuts, the split and its review."""

from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from ripshelf.models import Episode, EpisodeRef, JobState, MediaKind, TitleInfo, TitleRole
from ripshelf.tv import split

from .conftest import until
from .tvfix import mock_show

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg and ffprobe")


def title(i: int, minutes: float, chapters: int = 0) -> TitleInfo:
    return TitleInfo(index=i, duration_s=round(minutes * 60), size_bytes=1000 + i, chapters=chapters)


def eps(*runtimes: int | None, season: int = 1, first: int = 1) -> list[Episode]:
    return [Episode(season=season, episode=first + n, name=f"Episode {first + n}", runtime=r)
            for n, r in enumerate(runtimes)]


# --- detection ----------------------------------------------------------------------


def test_detects_one_long_title_with_chapters():
    titles = [title(0, 91, chapters=12), title(1, 3), title(2, 1)]
    got = split.detect(titles, {0: TitleRole.extra, 1: TitleRole.extra, 2: TitleRole.skip}, eps(30, 28, 33))
    assert got.title_index == 0 and got.episodes == 3
    assert "91 min with 12 chapters" in got.reason


@pytest.mark.parametrize("why, titles, roles", [
    ("too short", [title(0, 50, 12)], {}),
    ("too few chapters", [title(0, 91, 2)], {}),
    ("fewer chapters than episodes", [title(0, 150, 4)], {}),
    ("a play-all", [title(0, 91, 12), title(1, 30), title(2, 31)], {0: TitleRole.play_all}),
    ("a title per episode too", [title(0, 91, 12), title(1, 30), title(2, 31)], {}),
])
def test_not_a_single_title_disc(why, titles, roles):
    assert split.detect(titles, roles, eps(30, 28, 33)) is None, why


def test_detection_needs_runtimes_and_picks_the_longest():
    titles = [title(0, 70, 8), title(1, 120, 16)]
    assert split.detect(titles, {}, eps(None, None)) is None
    assert split.detect(titles, {}, eps(30, None)).title_index == 1
    # One episode-length title beside it (a bonus episode) still counts as a single-title disc.
    assert split.detect([title(0, 91, 12), title(1, 30)], {}, eps(30, 28, 33)).title_index == 0


# --- where to cut -------------------------------------------------------------------

# Seagull Manor S01E01-03 run 30, 28 and 33 min; two chapters each.
CHAPTERS = [0, 900, 1800, 2640, 3480, 4440]
TOTAL = 5460


def test_cuts_fit_the_runtimes():
    got = split.propose_cuts(CHAPTERS, TOTAL, [None] * 6, eps(30, 28, 33, 29, 29, 31))
    assert got.cuts == [2, 4] and got.costs == [0, 0, 0]
    assert got.confidence == 1.0 and got.note == "3 of 3 runtimes match, breaks not checked"


def test_breaks_settle_a_close_call():
    # Episodes of 20 min with chapters every 10 min, and one 2 min chapter that could go either way.
    chapters = [0, 600, 1200, 1800, 1920, 2520]
    runtimes = eps(21, 21)
    before = split.propose_cuts(chapters, 3240, [None, 0, 0, 1.0, 0, 0], runtimes)
    after = split.propose_cuts(chapters, 3240, [None, 0, 0, 0, 1.0, 0], runtimes)
    assert before.cuts == [3] and after.cuts == [4]
    assert "1 of 1 cuts at black and silence" in before.note


def test_confidence_drops_when_nothing_fits():
    good = split.propose_cuts(CHAPTERS, TOTAL, [None, 0, 1, 0, 1, 0], eps(30, 28, 33))
    bad = split.propose_cuts([0, 2000, 4000], TOTAL, [None, 0, 0], eps(30, 28, 33))
    assert good.confidence == 1.0 and bad.confidence < 0.5


def test_cuts_without_enough_episodes_or_runtimes():
    assert split.propose_cuts([0], TOTAL, [None], eps(30)).cuts == []
    assert split.propose_cuts(CHAPTERS, TOTAL, [], eps(None, None)).note == "no episode runtimes to fit"
    # The season ends after two episodes, so the third part fits the typical runtime, with a warning.
    short = split.propose_cuts(CHAPTERS, TOTAL, [], eps(30, 28))
    assert short.cuts == [2, 4] and "the season has only 2 more" in short.note
    # A missing runtime borrows the season's typical one.
    assert split.propose_cuts(CHAPTERS, TOTAL, [], eps(30, None, 33)).cuts == [2, 4]


def test_pal_runtimes_are_sped_up():
    # 25 fps runs 4% fast, so 30 min episodes take 28.8 min on the disc.
    chapters = [0, 864, 1728, 2592]
    assert split.propose_cuts(chapters, 3456, [], eps(30, 30), pal=True).cuts == [2]


def test_parts():
    assert split.parts(CHAPTERS, TOTAL, [4, 2]) == [(0.0, 1800, 0), (1800, 3480, 2), (3480, TOTAL, 4)]
    assert split.parts([0.4, 900], 1800, []) == [(0.0, 1800, 0)]
    assert split.parts(CHAPTERS, TOTAL, [0, 9, 2]) == [(0.0, 1800, 0), (1800, TOTAL, 2)]  # out of range dropped
    assert split.is_part(split.part_index(0)) and not split.is_part(99)


# --- ffmpeg and mkvmerge --------------------------------------------------------------


def test_split_command():
    cmd = split.split_command("mkvmerge", Path("/s/t.mkv"), Path("/o"), [4, 2, 2])
    assert cmd == ["mkvmerge", "--quiet", "-o", "/o/part.mkv", "--split", "chapters:3,5", "/s/t.mkv"]


def test_mkvmerge_found_in_program_files_on_windows(tmp_path, monkeypatch):
    from ripshelf import proc

    exe = tmp_path / "MKVToolNix" / "mkvmerge.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    monkeypatch.setattr(proc, "WINDOWS", True)
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    assert split.default_mkvmerge("mkvmerge") == str(exe)
    assert split.default_mkvmerge("/opt/mkvmerge") == "/opt/mkvmerge"
    monkeypatch.setattr(proc, "WINDOWS", False)
    assert split.default_mkvmerge("mkvmerge") == "mkvmerge"


def test_break_command_and_score():
    cmd, mark = split.break_command("ffmpeg", Path("t.mkv"), 1800)
    assert cmd[cmd.index("-ss") + 1] == "1796.000" and mark == 4.0
    assert split.break_command("ffmpeg", Path("t.mkv"), 2)[1] == 2.0  # can't start before 0
    log = ("[blackdetect @ 0x1] black_start:3.5 black_end:4.5 black_duration:1\n"
           "[silencedetect @ 0x2] silence_start: 3.6\n[silencedetect @ 0x2] silence_end: 4.4 | silence_duration: 0.8\n")
    assert split.score_break(log, 4.0) == 1.0
    assert split.score_break(log, 7.0) == 0.0
    assert split.score_break("black_start:3 black_end:3.5", 4.0) == 0.5
    assert split.score_break("silence_start: 6.0", 7.0) == 0.5  # silent to the end of the window


def test_parse_chapters():
    out = b'{"chapters": [{"start_time": "900.0"}, {"start_time": "0.000000"}]}'
    assert split.parse_chapters(out) == [0.0, 900.0]
    assert split.parse_chapters(b"{}") == [] and split.parse_chapters(b"nope") is None


def fake_mkvmerge(where: Path, exit_code: int = 0) -> str:
    """A stand-in mkvmerge, copying its source once per part, as one executable path."""
    where.mkdir(parents=True, exist_ok=True)
    script = where / "mkvmerge_stub.py"
    script.write_text(textwrap.dedent(f"""\
        import shutil, sys
        from pathlib import Path
        a = sys.argv[1:]
        out, src = Path(a[a.index("-o") + 1]), a[-1]
        n = len(a[a.index("--split") + 1].split(":")[1].split(",")) + 1
        for k in range(1, n + 1):
            shutil.copy(src, out.with_name(f"{{out.stem}}-{{k:03d}}.mkv"))
        print("Error: no space" if {exit_code} else "ok")
        sys.exit({exit_code})
    """))
    exe = where / "mkvmerge"
    exe.write_text(f"#!/bin/sh\nexec {sys.executable} {script} \"$@\"\n")
    exe.chmod(0o755)
    return str(exe)


async def test_split_file(tmp_path):
    src = tmp_path / "t.mkv"
    src.write_bytes(b"x" * 10)
    good, out = fake_mkvmerge(tmp_path / "good"), tmp_path / "out"
    assert await split.split_file(good, src, out, []) == [src]
    files = await split.split_file(good, src, out, [2, 4])
    assert [f.name for f in files] == ["part-001.mkv", "part-002.mkv", "part-003.mkv"]
    with pytest.raises(split.SplitError, match="could not be run"):
        await split.split_file(str(tmp_path / "missing"), src, out, [2])
    with pytest.raises(split.SplitError, match="mkvmerge failed: Error: no space"):
        await split.split_file(fake_mkvmerge(tmp_path / "bad", exit_code=2), src, out, [2])
    # Fewer files than parts (here the stub only ever writes two).
    short = fake_mkvmerge(tmp_path / "short")
    Path(short).write_text(Path(short).read_text().replace('"$@"', '-o "$3" --split chapters:2 "$6"'))
    with pytest.raises(split.SplitError, match="made 2 parts, not 3"):
        await split.split_file(short, src, out, [2, 4])


def long_video(path: Path, chapters: list[int], total: int, breaks: list[int]) -> Path:
    """A tiny real video with chapters, black frames and silence at ``breaks`` (seconds)."""
    meta = path.with_suffix(".txt")
    meta.write_text(";FFMETADATA1\n" + "".join(
        f"[CHAPTER]\nTIMEBASE=1/1\nSTART={a}\nEND={b}\n" for a, b in zip(chapters, [*chapters[1:], total],
                                                                       strict=True)))
    quiet = "+".join(f"between(t,{b - 2},{b + 2})" for b in breaks)
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", f"color=gray:s=64x36:r=2:d={total},drawbox=enable='{quiet}':c=black:t=fill",
                    "-f", "lavfi", "-i", f"sine=f=440:r=8000:d={total},volume=enable='{quiet}':volume=0",
                    "-i", str(meta), "-map", "0:v", "-map", "1:a", "-map_metadata", "2", "-c:v", "libx264",
                    "-preset", "ultrafast", "-g", "2", "-c:a", "aac", "-b:a", "16k", "-ac", "1", "-shortest",
                    str(path)], check=True)
    return path


@pytest.fixture(scope="module")
def seagull_rip(tmp_path_factory) -> Path:
    if not HAS_FFMPEG:
        pytest.skip("needs ffmpeg")
    return long_video(tmp_path_factory.mktemp("rip") / "long.mkv", CHAPTERS, TOTAL, [1800, 3480])


@needs_ffmpeg
@pytest.mark.slow
async def test_chapters_and_breaks_from_a_real_file(seagull_rip):
    assert await split.read_chapters("ffprobe", seagull_rip) == [float(c) for c in CHAPTERS]
    scores = [await split.break_score("ffmpeg", seagull_rip, c) for c in CHAPTERS[1:]]
    assert scores == [0.0, 1.0, 0.0, 1.0, 0.0]
    assert await split.read_chapters("ffprobe", seagull_rip.with_name("missing.mkv")) is None


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("mkvmerge") is None, reason="needs mkvmerge (MKVToolNix)")
async def test_real_mkvmerge_splits_at_chapters(seagull_rip, tmp_path):
    files = await split.split_file("mkvmerge", seagull_rip, tmp_path, [2, 4])
    from ripshelf.tv import verify
    lengths = [round(await verify.duration("ffprobe", f)) for f in files]
    assert lengths == [1800, 1680, 1980]


# --- the pipeline ---------------------------------------------------------------------

DRIVE = "/dev/fake-sr0"
LABEL = "SEAGULL_MANOR_S1_D1"
SCAN = """
out('CINFO:1,6206,"DVD disc"')
out('TCOUNT:2')
out('TINFO:0,8,0,"6"')
out('TINFO:0,9,0,"1:31:00"')
out('TINFO:0,11,0,"12000000"')
out('TINFO:0,27,0,"title_t00.mkv"')
out('TINFO:1,8,0,"1"')
out('TINFO:1,9,0,"0:02:10"')
out('TINFO:1,11,0,"300000"')
out('TINFO:1,27,0,"title_t01.mkv"')
"""


def rip_from(video: Path) -> str:
    return f"""
out('PRGT:5024,0,"Saving all titles to MKV files"')
import shutil
shutil.copy({str(video)!r}, Path(args[3], "title_t00.mkv"))
shutil.copy({str(video)!r}, Path(args[3], "title_t01.mkv"))
out('MSG:5036,260,1,"Copy complete.","%1","2"')
"""


@pytest.fixture
def keyed(settings, tmp_path):
    settings.tmdb_api_key = SecretStr("k")
    settings.verify_order = False
    settings.mkvmerge = fake_mkvmerge(tmp_path / "mkvmerge")
    return settings


async def single_title_job(h, monkeypatch, settings, mkv_script, video: Path):
    from ripshelf.dev import fake

    monkeypatch.setattr(fake.DRIVES[DRIVE][1], "label", LABEL)
    settings.makemkvcon = mkv_script(info=SCAN, rip=rip_from(video))
    h.backend.insert(DRIVE)
    return await until(lambda: next((j for j in h.db.list_jobs() if j.state == JobState.review
                                     and j.tv and j.tv.split and j.tv.split.state != "waiting"), None), 40,
                       "the split to be proposed")


@needs_ffmpeg
@pytest.mark.slow
async def test_single_title_disc_end_to_end(harness, keyed, settings, mkv_script, monkeypatch, seagull_rip):
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "seagull-manor")
        async with harness() as h:
            job = await single_title_job(h, monkeypatch, settings, mkv_script, seagull_rip)
            assert job.media == MediaKind.tv and job.tv.season == 1
            sp = job.tv.split
            assert sp.state == "ready" and sp.title_index == 0 and sp.cuts == [2, 4] == sp.proposed
            assert sp.breaks[2] == 1.0 and sp.breaks[4] == 1.0 and sp.confidence == 1.0
            assert sp.note == "3 of 3 runtimes match, 2 of 2 cuts at black and silence"
            # One long title says nothing about episode length, so the show isn't marked down for it.
            assert not any("TMDB says" in e.text for e in job.tv.show.evidence)
            rows = {m.title_index: m for m in job.tv.mapping}
            assert rows[0].role == TitleRole.skip and rows[1].role != TitleRole.episode
            parts = [rows[split.part_index(k)] for k in range(3)]
            assert [m.episodes for m in parts] == [[EpisodeRef(season=1, episode=n)] for n in (1, 2, 3)]
            assert [m.start_s for m in parts] == [0, 1800, 3480] and {m.part_of for m in parts} == {0}
            assert job.tv.consistent and h.m.thumbnail_path(job.id, 1001) == h.m.split_thumb_path(job.id, 0, 2)
            assert h.m.split_thumb_path(job.id, 0, 5)
            ok, why = h.m._auto_check(job)
            assert not ok and "needs a review" in why[0]

            # Cut at every chapter instead, and back.
            job = await h.m.tv_split(job.id, [1, 2, 3, 4, 5], None)
            assert len([m for m in job.tv.mapping if m.part_of == 0]) == 6 and job.tv.split.edited
            with pytest.raises(ValueError, match="chapter 6 can't start an episode"):
                await h.m.tv_split(job.id, [6], None)
            job = await h.m.tv_split(job.id, [2, 4], None)
            assert not job.tv.split.edited  # back to the proposal

            await h.m.tv_confirm(job.id, [])
            done = await h.wait_job(lambda j: j.state in (JobState.done, JobState.failed))
    assert done.state == JobState.done, done.problem
    season = settings.library_dir / "TV/Seagull Manor (1975)/Season 01"
    assert sorted(p.name for p in season.iterdir()) == [
        "Seagull Manor - S01E01 - A Hint of Style.mkv", "Seagull Manor - S01E02 - The Plumbers.mkv",
        "Seagull Manor - S01E03 - The Birthday Picnic.mkv"]


@needs_ffmpeg
@pytest.mark.slow
async def test_keep_whole_and_split_failures(harness, keyed, settings, mkv_script, monkeypatch, seagull_rip,
                                              tmp_path):
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "seagull-manor")
        async with harness() as h:
            job = await single_title_job(h, monkeypatch, settings, mkv_script, seagull_rip)
            job = await h.m.tv_split(job.id, None, True)
            assert job.tv.split is None and job.tv.split_off
            assert not any(m.part_of is not None for m in job.tv.mapping)
            # Re-proposing leaves it whole, as asked.
            job = await h.m.tv_propose(job.id, 2207, season=1, disc_number=1, start_episode=None, compilation=False)
            assert job.tv.split is None
            job = await h.m.tv_split(job.id, None, False)
            assert job.tv.split.state == "waiting"  # its chapters are read again
            job = await until(lambda: (j := h.job(job.id)).tv.split.state == "ready" and j)
            assert job.tv.split.cuts == [2, 4]
            # From episode 4 the cuts fit 29, 29 and 31 min instead.
            job = await h.m.tv_propose(job.id, 2207, season=1, disc_number=1, start_episode=4, compilation=False)
            got = [m.episodes[0].episode for m in job.tv.mapping if m.part_of == 0 and m.episodes]
            assert got[0] == 4 and job.tv.split.state == "ready"

            settings.mkvmerge = str(tmp_path / "no-such-mkvmerge")
            await h.m.tv_confirm(job.id, [])
            failed = await h.wait_job(lambda j: j.state == JobState.failed)
    assert failed.problem.code == "split_failed" and "MKVToolNix" in failed.problem.message


async def test_split_api(settings, keyed, monkeypatch, mkv_script, tmp_path):
    """The API on a split that is waiting for its chapters (no ffmpeg needed)."""
    from ripshelf.dev import fake
    from ripshelf.dev.fake import FakeDrives
    from ripshelf.main import create_app

    monkeypatch.setattr(fake.DRIVES[DRIVE][1], "label", LABEL)
    settings.makemkvcon = mkv_script(info=SCAN, rip="import time; time.sleep(30)")
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "seagull-manor")
        router.route(host="testserver").pass_through()
        app = create_app(settings, backend=FakeDrives(present={DRIVE}))
        with TestClient(app) as c:
            import time
            for _ in range(300):
                jobs = c.get("/api/jobs").json()
                if jobs and jobs[0]["tv"] and jobs[0]["tv"]["show"] and jobs[0]["tv"]["split"]:
                    break
                time.sleep(0.1)
            jid, plan = jobs[0]["id"], jobs[0]["tv"]
            assert plan["split"]["state"] == "waiting" and plan["split"]["expected"] == 3
            assert any("chapters are read once it's ripped" in p for p in plan["problems"])
            r = c.post(f"/api/jobs/{jid}/tv/split", json={"cuts": [2]})
            assert r.status_code == 409 and "no chapters" in r.json()["detail"]
            assert c.post(f"/api/jobs/{jid}/tv/split", json={"cuts": "x"}).status_code == 422
            assert c.get(f"/api/jobs/{jid}/split-thumbs/0/2.jpg").status_code == 404
            r = c.post(f"/api/jobs/{jid}/tv/split", json={"off": True})
            assert r.status_code == 200 and r.json()["tv"]["split"] is None and r.json()["tv"]["split_off"]
            assert c.post("/api/jobs/999/tv/split", json={"off": True}).status_code == 409
