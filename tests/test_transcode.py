"""Transcode step. Probe, plan, crop, command, run, check, and the pipeline."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ripshelf import transcode
from ripshelf.config import Settings
from ripshelf.models import Candidate, JobState

from .conftest import posix_only, until

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")


def _vaapi_encodes(device: str = "/dev/dri/renderD128") -> bool:
    """Whether this host can encode HEVC with VAAPI. A render node alone isn't enough:
    a Raspberry Pi 5's (v3d) or a GPU without an HEVC encoder has one too."""
    if not (HAS_FFMPEG and os.access(device, os.R_OK | os.W_OK)):
        return False
    try:
        return subprocess.run(
            ["ffmpeg", "-v", "error", "-init_hw_device", f"vaapi=va:{device}", "-filter_hw_device", "va",
             "-f", "lavfi", "-i", "color=black:s=256x144:d=0.2", "-vf", "format=nv12,hwupload",
             "-c:v", "hevc_vaapi", "-f", "null", "-"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


HAS_VAAPI = _vaapi_encodes()
PM = "/dev/fake-sr1"


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def probe_json(codec="vc1", pix="yuv420p", transfer="bt709", side=None, streams=3, duration="5400.5"):
    v = {"index": 0, "codec_type": "video", "codec_name": codec, "width": 1920, "height": 1080,
         "pix_fmt": pix, "color_transfer": transfer, "side_data_list": side or []}
    rest = [{"index": i, "codec_type": "audio", "codec_name": "ac3"} for i in range(1, streams)]
    return {"streams": [v, *rest], "format": {"duration": duration}}


# --- probe and plan ----------------------------------------------------------------------


def test_parse_probe():
    info = transcode.parse_probe(probe_json())
    assert info.video.codec == "vc1" and not info.video.hdr and not info.video.ten_bit
    assert info.streams == 3 and info.duration == 5400.5
    hdr = transcode.parse_probe(probe_json(codec="hevc", pix="yuv420p10le", transfer="smpte2084"))
    assert hdr.video.hdr and hdr.video.ten_bit
    dv = transcode.parse_probe(probe_json(codec="hevc", side=[{"side_data_type": "DOVI configuration record"}]))
    assert dv.video.dolby_vision and dv.video.hdr


def test_parse_probe_edges():
    cover = {"streams": [{"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
                         {"index": 1, "codec_type": "video", "codec_name": "h264", "width": 10, "height": 10}],
             "format": {"duration": "x"}}
    info = transcode.parse_probe(cover)
    assert info.video.codec == "h264" and info.duration == 0
    assert transcode.parse_probe({}).video is None


@pytest.mark.parametrize("kw, data, kind, want, why", [
    ({}, probe_json(), "bluray", True, "vc1 → H.265"),
    ({"transcode_enabled": False}, probe_json(), "bluray", False, "off"),
    ({}, probe_json(codec="h264"), "bluray", False, "not on the transcode list"),
    ({}, probe_json(codec="hevc", transfer="smpte2084"), "bluray", False, "HDR source left untouched"),
    ({}, probe_json(codec="hevc", side=[{"side_data_type": "DOVI configuration record"}]), "uhd", False,
     "Dolby Vision"),
    ({}, probe_json(), "uhd", False, "4K UHD discs are not transcoded"),
    ({"transcode_hdr": True}, probe_json(), "uhd", True, "vc1"),
    ({"transcode_sources": {"uhd": {"transcode": True}}}, probe_json(), "uhd", True, "vc1"),
    ({"transcode_hdr": True, "transcode_sources": {"uhd": {"transcode": False}}}, probe_json(), "uhd", False,
     "4K UHD discs are not transcoded"),
    ({"transcode_sources": {"dvd": {"transcode": False}}}, probe_json(), "dvd", False, "DVD discs"),
    ({"transcode_sources": {"dvd": {"transcode": False}}}, probe_json(), "bluray", True, "vc1"),
    ({"transcode_sources": {"bluray": {"transcode": True}}}, probe_json(codec="hevc", transfer="smpte2084"),
     "bluray", False, "HDR source left untouched"),
    ({"transcode_crop": "always", "transcode_sources": {"bluray": {"crop": False}}}, probe_json(codec="h264"),
     "bluray", False, "not on the transcode list"),
    ({"transcode_crop": "always"}, probe_json(codec="h264"), "bluray", True, "re-encoded to crop"),
    ({"transcode_codecs": ["VC1", "mpeg2video"]}, probe_json(codec="mpeg2video"), "dvd", True, "mpeg2video"),
])
def test_plan(kw, data, kind, want, why):
    p = transcode.plan(transcode.parse_probe(data), settings(**kw), kind, "vaapi")
    assert p.transcode is want and why in p.summary


def test_plan_crop_per_disc_kind():
    s = settings(transcode_sources={"bluray": {"crop": False}})
    assert transcode.plan(transcode.parse_probe(probe_json()), s, "bluray", "x265").crop_mode == "off"
    assert transcode.plan(transcode.parse_probe(probe_json()), s, "dvd", "x265").crop_mode == "when_transcoding"
    assert transcode.plan(transcode.parse_probe(probe_json()), s, None, "x265").crop_mode == "when_transcoding"


def test_transcode_source_defaults():
    s = settings()
    assert s.transcode_source("dvd") == s.transcode_source("bluray") == {"transcode": True, "crop": True}
    assert s.transcode_source("uhd") == {"transcode": False, "crop": True}
    assert settings(transcode_hdr=True).transcode_source("uhd")["transcode"]
    assert settings(transcode_sources={"bluray": {"crop": False}}).transcode_source("bluray") == \
        {"transcode": True, "crop": False}


def test_plan_without_video():
    assert not transcode.plan(transcode.parse_probe({"streams": []}), settings(), "dvd", "x265").transcode


# --- crop -----------------------------------------------------------------------------------


def test_crop_takes_the_largest_picture_and_ignores_dark_scenes():
    samples = [(1920, 800, 0, 140)] * 7 + [(1900, 800, 0, 140), (1868, 800, 0, 140), (1830, 760, 40, 160)]
    assert transcode.choose_crop(samples, 1920, 1080) == transcode.Crop(1920, 800, 0, 140)


@pytest.mark.parametrize("samples, want", [
    ([], None),
    ([(1920, 1080, 0, 0)] * 5, None),  # no bars
    ([(1920, 1064, 0, 8)] * 5, None),  # bars too thin to bother
    ([(1920, 800, 0, 140), (1920, 600, 0, 240), (1920, 500, 0, 290), (1920, 400, 0, 340)], None),  # no agreement
    ([(1440, 1080, 240, 0)] * 6, transcode.Crop(1440, 1080, 240, 0)),  # pillarbox
    ([(1919, 801, 1, 139)] * 4, transcode.Crop(1918, 800, 0, 138)),  # made even
    ([(4000, 800, 0, 0), (0, 0, 0, 0)], None),  # nonsense values
])
def test_choose_crop_cases(samples, want):
    assert transcode.choose_crop(samples, 1920, 1080) == want


# --- command and checks ----------------------------------------------------------------------


def test_vaapi_command_ten_bit_with_crop():
    v = transcode.parse_probe(probe_json(pix="yuv420p10le")).video
    cmd = transcode.command("ffmpeg", Path("in.mkv"), Path("out.mkv"), v, transcode.Crop(1920, 800, 0, 140),
                            "vaapi", 21, "/dev/dri/renderD128")
    s = " ".join(cmd)
    assert "-map 0 -c copy -map_metadata 0 -map_chapters 0" in s
    assert "crop=1920:800:0:140,format=p010,hwupload" in s and "hevc_vaapi" in s and "main10" in s
    assert "-qp 21" in s and cmd[-1] == "out.mkv"


def test_x265_command():
    v = transcode.parse_probe(probe_json()).video
    s = " ".join(transcode.command("ffmpeg", Path("i"), Path("o"), v, None, "x265", 20, "/dev/x"))
    assert "libx265" in s and "-crf 20" in s and "yuv420p " in s and "crop" not in s and "vaapi" not in s


def test_output_ok():
    src = transcode.parse_probe(probe_json(streams=4, duration="100"))
    good = transcode.parse_probe(probe_json(codec="hevc", streams=4, duration="100.5"))
    assert transcode.output_ok(src, good) is None
    assert "could not be read" in transcode.output_ok(src, None)
    assert "no H.265" in transcode.output_ok(src, transcode.parse_probe(probe_json(codec="h264", streams=4)))
    assert "streams" in transcode.output_ok(src, transcode.parse_probe(probe_json(codec="hevc", streams=2,
                                                                                   duration="100")))
    assert "long" in transcode.output_ok(src, transcode.parse_probe(probe_json(codec="hevc", streams=4,
                                                                                duration="60")))


# --- real ffmpeg -------------------------------------------------------------------------------


def letterboxed(path: Path, seconds: int = 6, ten_bit: bool = False) -> Path:
    """MPEG-2 video with black bars top and bottom, stereo audio, a subtitle and two chapters."""
    meta = path.with_suffix(".meta")
    meta.write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=3000\ntitle=One\n"
                    "[CHAPTER]\nTIMEBASE=1/1000\nSTART=3000\nEND=6000\ntitle=Two\n")
    srt = path.with_suffix(".srt")
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
    vcodec = ["-c:v", "libx264", "-pix_fmt", "yuv420p10le"] if ten_bit else ["-c:v", "mpeg2video", "-q:v", "4"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size=640x272:rate=25:duration={seconds},pad=640:360:0:44:black",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-i", str(srt),
                    "-i", str(meta), "-map", "0:v", "-map", "1:a", "-map", "2:s", "-map_metadata", "3",
                    "-map_chapters", "3", *vcodec, "-c:a", "ac3", "-c:s", "srt", str(path)], check=True)
    return path


@needs_ffmpeg
async def test_crop_detection_on_a_real_clip(tmp_path):
    src = letterboxed(tmp_path / "in.mkv")
    info = await transcode.probe("ffprobe", src)
    crop = await transcode.detect_crop("ffmpeg", src, info.duration, 640, 360)
    assert crop == transcode.Crop(640, 272, 0, 44)


async def transcode_clip(tmp_path, encoder: str, ten_bit=False):
    src = letterboxed(tmp_path / "in.mkv", ten_bit=ten_bit)
    info = await transcode.probe("ffprobe", src)
    crop = await transcode.detect_crop("ffmpeg", src, info.duration, 640, 360)
    dst = tmp_path / "out.mkv"
    seen = []
    rc = await transcode.Transcoder().run(
        transcode.command("ffmpeg", src, dst, info.video, crop, encoder, 24, "/dev/dri/renderD128"),
        info.duration, lambda f, sp, fps: seen.append(f), tmp_path / "log")
    out = await transcode.probe("ffprobe", dst)
    return rc, info, out, seen, dst


@needs_ffmpeg
async def test_x265_transcode_keeps_audio_subs_chapters(tmp_path):
    rc, info, out, seen, dst = await transcode_clip(tmp_path, "x265")
    assert rc == 0 and transcode.output_ok(info, out) is None
    assert (out.video.width, out.video.height) == (640, 272)
    assert seen and seen[-1] > 0.9
    chapters = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_chapters", "-of", "json", str(dst)],
                                         capture_output=True, text=True).stdout)["chapters"]
    assert [c["tags"]["title"] for c in chapters] == ["One", "Two"]


@needs_ffmpeg
@pytest.mark.skipif(not HAS_VAAPI, reason="no VAAPI device")
async def test_vaapi_transcode(tmp_path):
    rc, info, out, _, _ = await transcode_clip(tmp_path, "vaapi")
    assert rc == 0 and transcode.output_ok(info, out) is None and out.video.codec == "hevc"


@needs_ffmpeg
@pytest.mark.skipif(not HAS_VAAPI, reason="no VAAPI device")
async def test_vaapi_ten_bit_is_main10(tmp_path):
    rc, info, out, _, _ = await transcode_clip(tmp_path, "vaapi", ten_bit=True)
    assert rc == 0 and out.video.ten_bit


@needs_ffmpeg
async def test_pick_encoder(tmp_path):
    assert await transcode.pick_encoder("ffmpeg", settings(transcode_encoder="x265")) == "x265"
    assert await transcode.pick_encoder("ffmpeg", settings(vaapi_device=str(tmp_path / "nope"))) == "x265"
    if HAS_VAAPI:
        assert await transcode.pick_encoder("ffmpeg", settings()) == "vaapi"


@posix_only
async def test_transcoder_failures(tmp_path):
    t = transcode.Transcoder()
    assert await t.run(["/nonexistent/ffmpeg"], 10, lambda *a: None, tmp_path / "l") is None
    rc = await transcode.Transcoder().run(["sh", "-c", "echo boom >&2; exit 3"], 10, lambda *a: None,
                                          tmp_path / "l2")
    assert rc == 3 and "boom" in (tmp_path / "l2").read_text()
    assert "could not start ffmpeg" in (tmp_path / "l").read_text()
    assert await transcode.probe("/nonexistent/ffprobe", tmp_path / "x") is None
    t.kill()  # nothing running, so harmless


@posix_only
async def test_transcoder_progress_parsing(tmp_path):
    script = "printf 'fps=0.00\\nspeed=2.5x\\nout_time_us=5000000\\nout_time_us=-1\\nout_time_us=x\\nspeed=N/A\\n" \
             "fps=59.5\\nout_time_us=20000000\\n'"
    seen = []
    await transcode.Transcoder().run(["sh", "-c", script], 10, lambda f, sp, fps: seen.append((f, sp, fps)), tmp_path / "l")
    assert seen == [(0.5, 2.5, None), (0.5, 2.5, None), (1.0, 2.5, 59.5)], "never backwards"
    log = (tmp_path / "l").read_text().splitlines()
    assert log[0].startswith("$ sh -c")
    assert [line.split("] ")[1] for line in log[1:]] == ["50% · 2.5×", "100% · 59.5 fps · 2.5×", "ffmpeg exited with code 0"]


@posix_only
async def test_transcoder_progress_ffmpeg7_end(tmp_path):
    # ffmpeg 7.1's last report gave a subtitle's end time (2 s of a 6 s clip), after 4.68 s.
    script = "printf 'out_time_us=1160000\\nprogress=continue\\nout_time_us=4680000\\nprogress=continue\\n" \
             "out_time_us=2006000\\nprogress=end\\n'"
    seen = []
    await transcode.Transcoder().run(["sh", "-c", script], 6, lambda f, sp, fps: seen.append(f), tmp_path / "l")
    assert seen == sorted(seen) and seen[-1] == 1.0
    assert "] finished" in (tmp_path / "l").read_text()


@posix_only
async def test_transcoder_cancel(tmp_path):
    import asyncio

    t = transcode.Transcoder()
    task = asyncio.create_task(t.run(["sleep", "30"], 30, lambda *a: None, tmp_path / "l"))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# --- in the pipeline ------------------------------------------------------------------------------


@pytest.fixture
def tc_settings(settings):
    settings.transcode_enabled = True
    settings.transcode_codecs = ["mpeg2video"]  # ffmpeg can't make VC-1; the fake rips are MPEG-2
    settings.transcode_encoder = "x265"
    settings.transcode_crop = "off"
    return settings


MANUAL = Candidate(source="manual", title="Practical Magic", year=1998)


@needs_ffmpeg
async def test_pipeline_transcodes_before_finalise(harness, tc_settings):
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done")
        await h.m.confirm(job.id, MANUAL)
        await until(lambda: h.job(job.id).state == JobState.transcoding or h.job(job.id).state == JobState.done)
        done = await h.wait_job(lambda j: j.state == JobState.done, timeout=60)
        events = [e.message for e in h.db.events(job.id)]
    dest = Path(done.destination)
    info = await transcode.probe("ffprobe", dest)
    assert info.video.codec == "hevc"
    assert done.transcode.files[0].action == "transcoded" and done.transcode.files[0].source_codec == "mpeg2video"
    assert any("mpeg2video → H.265" in m for m in events)
    meta = json.loads((dest.parent / ".disc/disc.json").read_text())
    assert meta["transcode"]["files"][0]["action"] == "transcoded"
    assert not (dest.parent / f"{dest.stem} - original.mkv").exists()
    log = h.m.transcode_log(job.id, 500)
    assert log[0].startswith("── Title ") and log[1].startswith("$ ") and "libx265" in log[1]
    assert any("% · " in line for line in log) and any("finished · " in line for line in log)
    assert log[-1].endswith("ffmpeg exited with code 0")


@needs_ffmpeg
async def test_keep_original(harness, tc_settings):
    tc_settings.transcode_keep_original = True
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done")
        await h.m.confirm(job.id, MANUAL)
        done = await h.wait_job(lambda j: j.state == JobState.done, timeout=60)
    dest = Path(done.destination)
    original = dest.parent / f"{dest.stem} - original.mkv"
    assert original.exists()
    assert (await transcode.probe("ffprobe", original)).video.codec == "mpeg2video"


@needs_ffmpeg
async def test_failed_transcode_keeps_the_rip(harness, tc_settings):
    tc_settings.transcode_encoder = "vaapi"
    tc_settings.vaapi_device = "/dev/nonexistent"
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done")
        await h.m.confirm(job.id, MANUAL)
        done = await h.wait_job(lambda j: j.state == JobState.done, timeout=60)
        events = [e.message for e in h.db.events(job.id)]
    assert done.transcode.files[0].action == "failed"
    assert any("transcode failed" in m for m in events)
    assert (await transcode.probe("ffprobe", Path(done.destination))).video.codec == "mpeg2video"


@needs_ffmpeg
async def test_codec_not_listed_is_copied_as_is(harness, tc_settings):
    tc_settings.transcode_codecs = ["vc1"]
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done")
        await h.m.confirm(job.id, MANUAL)
        done = await h.wait_job(lambda j: j.state == JobState.done, timeout=60)
    assert done.transcode.files[0].action == "skipped" and "not on the transcode list" in done.transcode.files[0].note


async def test_disc_kind_switched_off_is_copied_as_is(harness, tc_settings):
    tc_settings.transcode_sources = {"dvd": {"transcode": False}}
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done")
        await h.m.confirm(job.id, MANUAL)
        done = await h.wait_job(lambda j: j.state == JobState.done, timeout=60)
    assert done.transcode.files[0].action == "skipped"
    assert done.transcode.files[0].note == "DVD discs are not transcoded"


def test_transcoding_on_by_default_for_vc1_only():
    s = Settings(_env_file=None)
    assert s.transcode_enabled and s.transcode_codecs == ["vc1"]
    assert s.transcode_crop == "when_transcoding" and not s.transcode_hdr and not s.transcode_keep_original


async def test_transcoding_switched_off(harness, settings):
    settings.transcode_enabled = False
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done")
        await h.m.confirm(job.id, MANUAL)
        done = await h.wait_job(lambda j: j.state == JobState.done)
    assert done.transcode is None


@needs_ffmpeg
async def test_cancel_during_transcode(harness, tc_settings, monkeypatch):
    import asyncio

    async def slow_run(self, cmd, duration, on_progress, log_path):
        self._proc = await asyncio.create_subprocess_exec("sleep", "30", start_new_session=True)
        try:
            await self._proc.wait()
        except asyncio.CancelledError:
            self.kill()
            raise
        return self._proc.returncode

    monkeypatch.setattr(transcode.Transcoder, "run", slow_run)
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done")
        await h.m.confirm(job.id, MANUAL)
        await h.wait_job(lambda j: j.state == JobState.transcoding)
        await asyncio.sleep(0.5)
        await h.m.cancel(job.id)
        cancelled = h.job(job.id)
        assert cancelled.state == JobState.cancelled
        await asyncio.sleep(0.5)
        assert h.job(job.id).state == JobState.cancelled  # not finalised afterwards


async def test_interrupted_transcode_is_retried(settings, harness, tmp_path):
    from ripshelf.db import Database
    from ripshelf.models import RipState, TitleInfo, TranscodeInfo

    rip = settings.scratch_dir / "job-1" / "t.mkv"
    rip.parent.mkdir(parents=True)
    rip.write_bytes(b"x" * 100)
    db = Database(settings.db_path)
    job = db.create_job("fake-ihas122", PM)
    job.state, job.identity = JobState.transcoding, MANUAL
    job.rip.state, job.rip.file = RipState.done, str(rip)
    job.transcode = TranscodeInfo(state="running")
    job.main_title = TitleInfo(index=0, duration_s=100, size_bytes=100, chapters=1)
    db.save_job(job)
    db.close()
    settings.transcode_enabled = True
    async with harness() as h:
        assert h.job(job.id).state == JobState.failed and h.job(job.id).problem.code == "interrupted"
        await h.m.retry(job.id)
        done = await h.wait_job(lambda j: j.state == JobState.done, timeout=30)
    # Not a video, so the transcode step notes it and the rip goes through as it is.
    assert done.transcode.state == "done" and done.transcode.files[0].action == "skipped"


# --- the step shown on the Overview -----------------------------------------------------------------


@pytest.fixture
def steps(monkeypatch):
    """Every (state, label, detail) a job is shown with while off the drive."""
    from ripshelf.jobs import Manager

    seen: list[tuple[str, str, str]] = []
    real = Manager._set_step

    def record(self, job, label, detail=""):
        real(self, job, label, detail)
        saved = self.db.get_job(job.id)
        if saved.step:
            seen.append((saved.state.value, saved.step.label, saved.step.detail))

    monkeypatch.setattr(Manager, "_set_step", record)
    return seen


@needs_ffmpeg
async def test_steps_while_transcoding(harness, tc_settings, steps):
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done")
        await h.m.confirm(job.id, MANUAL)
        done = await h.wait_job(lambda j: j.state == JobState.done, timeout=60)
    labels = [label for _, label, _ in steps]
    i = labels.index("Checking whether title 0 needs transcoding")
    assert labels[i:i + 3] == ["Checking whether title 0 needs transcoding", "Transcoding title 0",
                               "Checking the transcoded title 0"]
    assert steps[i + 1][2].startswith("mpeg2video → H.265 (x265)")
    assert steps[-1] == ("finalising", "Copying to library", f"{Path(done.destination).name} · transcoded to H.265")
    assert all(state == "transcoding" for state, _, _ in steps[:-1])
    assert done.step is None


@needs_ffmpeg
async def test_skipped_transcode_says_why(harness, tc_settings, steps):
    tc_settings.transcode_codecs = ["vc1"]
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done")
        await h.m.confirm(job.id, MANUAL)
        await h.wait_job(lambda j: j.state == JobState.done, timeout=60)
    assert not any(label.startswith("Transcoding") for _, label, _ in steps)
    assert steps[-1][1] == "Copying to library"
    assert steps[-1][2].endswith("· not transcoded (mpeg2video is not on the transcode list)")


async def test_copy_step_when_transcoding_is_off(harness, settings, steps):
    settings.transcode_enabled = False
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done")
        await h.m.confirm(job.id, MANUAL)
        await h.wait_job(lambda j: j.state == JobState.done)
    assert [(s, label) for s, label, _ in steps] == [("finalising", "Copying to library")]
    assert steps[0][2].endswith("· not transcoded (transcoding is off)")


async def test_checking_is_shown_before_probing(harness, tc_settings, monkeypatch):
    """The Overview never says "Transcoding" while ffprobe is only looking at the file."""
    seen = []

    async def probe(ffprobe, path):
        job = h.m.db.get_job(job_id)
        seen.append((job.state, job.step.label, job.rip.progress))

    monkeypatch.setattr(transcode, "probe", probe)
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done")
        job_id = job.id
        await h.m.confirm(job.id, MANUAL)
        done = await h.wait_job(lambda j: j.state == JobState.done, timeout=30)
    assert seen == [(JobState.transcoding, "Checking whether title 0 needs transcoding", None)]
    assert done.transcode.files[0].note.startswith("could not read")


async def test_a_cancelled_job_keeps_no_step(harness, settings):
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review)
        await h.m.cancel(job.id)
        h.m._set_step(h.job(job.id), "Copying to library")
        assert h.job(job.id).state == JobState.cancelled and h.job(job.id).step is None
