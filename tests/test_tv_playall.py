"""Working the episodes out from the play-all: its chapters, frames, and "Wait for the play-all"."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import respx
from pydantic import SecretStr

from ripshelf.models import Episode, JobState, TitleInfo, TitleMapUpdate, TitleRole
from ripshelf.tv import mapping as tvmap
from ripshelf.tv import playall, verify

from .conftest import until
from .tvfix import mock_show

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg not installed")
SD = "/dev/fake-sr0"  # PIPPA_&_GOOSE_KITE_TRAM: a play-all + 10 episodes, the play-all in title order


def chapters_of(*lengths: float, at: float = 0.0) -> list[tuple[float, float]]:
    out = []
    for n in lengths:
        out.append((at, at + n))
        at += n
    return out


# --- chapters ---------------------------------------------------------------------------------


def test_chapters_find_the_play_alls_order():
    """A DVD play-all leaves out each episode's 0.4 s closing cell, so it's a little shorter
    than its episodes together, and the chapters still say where each one is."""
    pilot, two, three = (812.5, 903.1, 777.0), (441.2, 318.7, 405.4), (380.9, 549.2, 397.6)
    eps = {3: chapters_of(*pilot, 0.4), 4: chapters_of(*two, 0.4), 1: chapters_of(*three, 0.4),
           2: chapters_of(176.0, 0.4)}  # a trailer: one chapter, so not matched
    pa = chapters_of(*pilot, *two, *three, 0.4)
    order, starts = playall.match_chapters(pa, eps)
    assert order == [3, 4, 1]
    assert starts[3] == 0 and starts[4] == pytest.approx(sum(pilot))
    assert starts[1] == pytest.approx(sum(pilot) + sum(two))


def test_chapters_tolerate_a_little_drift_but_not_another_episode():
    eps = {1: chapters_of(300.0, 600.0), 2: chapters_of(450.0, 451.0)}
    assert playall.match_chapters(chapters_of(300.8, 599.4, 450.3, 450.6), eps)[0] == [1, 2]
    # Title 2's chapters are not in this play-all.
    assert playall.match_chapters(chapters_of(300.0, 600.0, 200.0, 700.0), eps)[0] == [1]
    # The same chapters twice: each title takes its own place.
    same = {1: chapters_of(300.0, 600.0), 5: chapters_of(300.0, 600.0)}
    order, starts = playall.match_chapters(chapters_of(300.0, 600.0, 300.0, 600.0), same)
    assert order == [1, 5] and starts[5] == 900.0


def test_a_dropped_opening_cell_is_placed_by_the_inner_chapters():
    """Some DVD episodes open with a short cell the play-all leaves out, so the first chapter
    is a little shorter in the play-all. The marks between chapters still say where it is,
    and when they run to the frame they prove the order without looking at frames."""
    one, two = [267.12, 328.08, 292.08, 241.32], [228.36, 273.48, 261.6, 333.96]
    pa = chapters_of(*one, 227.88, *two[1:], at=0.08)
    eps = {1: chapters_of(*one, 0.4, at=0.08), 2: chapters_of(*two, 0.4, at=0.08)}
    order, starts, exact = playall._match_chapters(pa, eps)
    assert order == [1, 2] and exact == {1, 2}
    assert starts[1] == pytest.approx(0.0)
    assert starts[2] == pytest.approx(sum(one) + 227.88 - 228.36)  # 0.48 s before its first chapter mark
    # About the same lengths, but not the same chapters: found, not proven.
    near = chapters_of(*one, 227.88, *(x + 0.3 for x in two[1:]), at=0.08)
    assert playall._match_chapters(near, eps)[2] == {1}
    read = playall.PlayAllRead(order, starts, "chapters", exact=exact)
    assert read.proves([1, 2]) and not read.proves([2, 1])
    assert not playall.PlayAllRead(order, starts, "chapters", exact={1}).proves([1, 2])


# --- the mapping takes every title the play-all plays ------------------------------------------


def test_play_all_titles_are_all_episodes():
    """A 40 min title with a long pilot first used to come out as an extra (the alignment
    found it cheaper to skip than to put it on episode 2)."""
    titles = [TitleInfo(index=3, duration_s=4670, size_bytes=1, chapters=7), TitleInfo(index=4, duration_s=2467, size_bytes=1, chapters=7),
              TitleInfo(index=1, duration_s=2420, size_bytes=1, chapters=7)]
    eps = [Episode(season=1, episode=1, name="Kettle Day", runtime=81),
           Episode(season=1, episode=2, name="Brass Tacks", runtime=43),
           Episode(season=1, episode=3, name="Gull Watch", runtime=42),
           Episode(season=1, episode=4, name="Low Tide", runtime=43)]
    mapped = tvmap.propose(titles, {}, eps, 1, pal=True, all_episodes=True)
    got = {m.title_index: [e.episode for e in m.episodes] for m in mapped}
    assert got == {3: [1], 4: [2], 1: [3]}
    assert all(m.role == TitleRole.episode for m in mapped)
    # More titles than episodes left: the usual alignment, with extras.
    assert tvmap.align(titles, eps[:2], all_episodes=True)[2] is None


# --- real files (ffmpeg) -------------------------------------------------------------------------


def moving(path: Path, parts: list[tuple[int, list[float]]], tail: float = 0.0, rate: int = 10,
           spin: int = 0) -> Path:
    """A video of moving test pictures, one hue per part, with a chapter per given length
    (and a short closing chapter of ``tail`` s, like a DVD's last cell). ``spin`` turns the
    hue this many degrees a second, so each frame looks unlike the next."""
    inputs, chapters, at = [], [], 0.0
    for hue, lengths in parts:
        for n in lengths:
            chapters.append((at, at + n))
            at += n
        inputs.append((hue, sum(lengths)))
    if tail:
        chapters.append((at, at + tail))
        inputs.append((None, tail))
    args = []
    for hue, d in inputs:
        src = f"testsrc=s=160x90:r={rate}:d={d}" if hue is not None else f"color=c=black:s=160x90:r={rate}:d={d}"
        args += ["-f", "lavfi", "-i", src]
    filt = "".join(f"[{i}:v]" + (f"hue=h='{hue}+{spin}*t'[h{i}];" if hue is not None else f"null[h{i}];")
                   for i, (hue, _) in enumerate(inputs))
    filt += "".join(f"[h{i}]" for i in range(len(inputs))) + f"concat=n={len(inputs)}:v=1[v]"
    meta = path.with_suffix(".txt")
    meta.write_text(";FFMETADATA1\n" + "".join(
        f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={round(a * 1000)}\nEND={round(b * 1000)}\n" for a, b in chapters),
        encoding="utf-8")
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, "-i", str(meta), "-filter_complex", filt,
                    "-map", "[v]", "-map_metadata", str(len(inputs)), "-map_chapters", str(len(inputs)),
                    "-c:v", "mpeg2video", "-q:v", "2", str(path)], check=True)
    return path


@needs_ffmpeg
async def test_dvd_play_all_is_read_and_checked(tmp_path):
    """Each episode ends in a 0.4 s cell the play-all leaves out, so where an episode sits in
    the play-all drifts from the running sum. The chapters find the order, and the frame
    check looks a little either side of where it expects each one."""
    a, b, c = (0, [1.5, 2.5]), (120, [3.0, 1.2]), (240, [2.2, 1.6])
    files = {3: moving(tmp_path / "t3.mkv", [a], tail=0.4), 4: moving(tmp_path / "t4.mkv", [b], tail=0.4),
             1: moving(tmp_path / "t1.mkv", [c], tail=0.4)}
    pa = moving(tmp_path / "t0.mkv", [a, b, c])
    titles = sorted(files.items())
    read = await playall.read("ffmpeg", "ffprobe", pa, titles, 12)
    assert read.found and read.order == [3, 4, 1] and read.how == "chapters"
    ordered = [(i, files[i]) for i in read.order]
    check = await verify.verify_order("ffmpeg", "ffprobe", pa, ordered, read.starts)
    assert check.verified is True, check.note
    wrong = await verify.verify_order("ffmpeg", "ffprobe", pa, [ordered[0], ordered[2], ordered[1]], None)
    assert wrong.verified is False


@needs_ffmpeg
async def test_the_frame_check_finds_an_episode_between_its_steps(tmp_path, monkeypatch):
    """A 0.44 s closing cell puts the next episode a frame off every 0.1 s step, and on a
    moving 25 fps picture the frame next to it is too different to pass (job 238's false
    alarm). Looking frame by frame near the closest step finds it."""
    a, b, c = (0, [2.0, 2.0]), (120, [2.5, 1.5]), (240, [2.0, 2.0])
    files = [(i, moving(tmp_path / f"t{i}.mkv", [p], tail=0.44, rate=25, spin=900))
             for i, p in ((1, a), (2, b), (3, c))]
    pa = moving(tmp_path / "t0.mkv", [a, b, c], rate=25, spin=900)
    check = await verify.verify_order("ffmpeg", "ffprobe", pa, files, None)
    assert check.verified is True, check.note
    monkeypatch.setattr(verify, "FINE_SHIFTS", ())
    coarse = await verify.verify_order("ffmpeg", "ffprobe", pa, files, None)
    assert coarse.verified is False and coarse.note.startswith("couldn't confirm titles")


@needs_ffmpeg
async def test_frames_find_the_order_without_chapters(tmp_path):
    def clip(path: Path, colours: list[str]) -> Path:
        args = []
        for col in colours:
            args += ["-f", "lavfi", "-i", f"color=c={col}:s=160x90:d=2:r=5"]
        filt = "".join(f"[{i}:v]" for i in range(len(colours))) + f"concat=n={len(colours)}:v=1[v]"
        subprocess.run(["ffmpeg", "-v", "error", "-y", *args, "-filter_complex", filt, "-map", "[v]",
                        "-c:v", "mpeg2video", str(path)], check=True)
        return path

    eps = [(1, clip(tmp_path / "e1.mkv", ["red"])), (2, clip(tmp_path / "e2.mkv", ["green"])),
           (3, clip(tmp_path / "e3.mkv", ["blue"])), (4, clip(tmp_path / "e4.mkv", ["white"]))]
    pa = clip(tmp_path / "all.mkv", ["blue", "red", "green"])
    read = await playall.read("ffmpeg", "ffprobe", pa, eps, 6)
    assert read.order == [3, 1, 2] and read.how == "frames"
    assert not (await playall.read("ffmpeg", "ffprobe", pa, eps, 6, frames=False)).found


async def test_read_without_ffprobe(tmp_path):
    a = tmp_path / "a.mkv"
    a.write_bytes(b"x")
    res = await playall.read("/nonexistent/ffmpeg", "/nonexistent/ffprobe", a, [(1, a), (2, a)], 100)
    assert not res.found and "ffprobe" in res.note


# --- the button, end to end ---------------------------------------------------------------------


@pytest.fixture
def keyed(settings):
    settings.tmdb_api_key = SecretStr("k")
    return settings


@needs_ffmpeg
@pytest.mark.slow
async def test_wait_for_the_play_all(harness, keyed):
    """Asked to wait before the rip is done, the play-all is read once it is: a title marked as
    an extra by mistake becomes an episode again, and the episodes run in the play-all's order."""
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "pippa-goose")
        async with harness(SD) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review and j.tv and j.tv.show, timeout=60)
            job = await h.m.tv_propose(job.id, job.tv.show.key, season=3, disc_number=None, start_episode=21,
                                       compilation=False)
            await h.m.tv_set_mapping(job.id, [TitleMapUpdate(title_index=4, role=TitleRole.extra)])
            job = await h.m.tv_play_all(job.id, True)
            await until(lambda: h.job(job.id).tv.play_all_order, timeout=60)
            job = h.job(job.id)
            plan = job.tv
            assert not plan.wait_play_all and plan.order_from == "play_all"
            assert plan.play_all_order == list(range(1, 11)) and plan.order_verified is True
            roles = {m.title_index: m.role for m in plan.mapping}
            assert roles[0] == TitleRole.play_all and roles[4] == TitleRole.episode
            eps = [m.episodes[0].episode for m in sorted(plan.mapping, key=lambda m: m.title_index)
                   if m.role == TitleRole.episode]
            assert eps == list(range(21, 31)) and plan.consistent
            # Re-picking the show keeps the play-all's order.
            job = await h.m.tv_propose(job.id, plan.show.key, season=3, disc_number=None, start_episode=21,
                                       compilation=False)
            assert job.tv.order_from == "play_all" and job.tv.order_verified is True
            await h.m.tv_confirm(job.id, [])
            await h.wait_job(lambda j: j.state == JobState.done, timeout=60)


async def test_stop_waiting(harness, keyed):
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "pippa-goose")
        async with harness(SD) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review and j.tv and j.tv.show, timeout=60)
            job = h.job(job.id)
            job.tv.wait_play_all = True
            h.m._save(job)
            job = await h.m.tv_play_all(job.id, False)
            assert not job.tv.wait_play_all and not job.tv.play_all_order
