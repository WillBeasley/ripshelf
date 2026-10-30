"""Optional transcode step between rip and library (ffmpeg).

What it does, per ripped file:
  * probe the first video stream (codec, bit depth, HDR / Dolby Vision);
  * decide: transcode when the codec is on the list (default VC-1), crop
    black bars when asked (cropdetect sampled across the film, and only when
    the samples agree), and leave HDR/Dolby Vision/UHD alone unless allowed,
    with transcoding and cropping each switchable per kind of disc;
  * encode the video to H.265 on the GPU (VAAPI, Main10 for 10-bit sources)
    or with x265, copying every audio and subtitle track, chapters and
    metadata;
  * check the result (same length, same number of streams) before it
    replaces the rip. Any failure keeps the original.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import proc as procs

log = logging.getLogger(__name__)

HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
CROP_SAMPLES = 10
CROP_FRAMES = 6
# After a seek, some decoders (VC-1) output grey until the next keyframe:
# seek this much earlier and only look at frames from the wanted time on.
SETTLE_S = 2.0
# Most samples must see the chosen size, and the bars must be worth removing.
CROP_AGREEMENT = 0.5
CROP_MIN_FRACTION = 0.03
DURATION_TOLERANCE_S = 2.0
PROBE_TIMEOUT_S = 60
LOG_STEP = 0.05  # a progress line in the transcode log every 5%
DISC_NAMES = {"dvd": "DVD", "bluray": "Blu-ray", "uhd": "4K UHD"}


@dataclass
class VideoInfo:
    index: int
    codec: str
    width: int
    height: int
    pix_fmt: str
    transfer: str | None = None
    dolby_vision: bool = False

    @property
    def ten_bit(self) -> bool:
        return "10" in self.pix_fmt or "12" in self.pix_fmt

    @property
    def hdr(self) -> bool:
        return self.transfer in HDR_TRANSFERS or self.dolby_vision


@dataclass
class MediaInfo:
    duration: float
    streams: int
    video: VideoInfo | None


@dataclass
class Crop:
    w: int
    h: int
    x: int
    y: int

    def filter(self) -> str:
        return f"crop={self.w}:{self.h}:{self.x}:{self.y}"


@dataclass
class Plan:
    transcode: bool
    crop: Crop | None = None
    encoder: str | None = None  # "vaapi" | "x265"
    reasons: list[str] = field(default_factory=list)
    crop_mode: str = "off"  # transcode_crop, or "off" where this kind of disc isn't cropped
    codec_wanted: bool = False  # the codec is on the list (else it's only re-encoded to crop)

    @property
    def summary(self) -> str:
        return "; ".join(self.reasons)


async def _run(cmd: list[str], timeout: float = PROBE_TIMEOUT_S) -> tuple[int | None, bytes, bytes]:
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE)
    except OSError as e:
        return None, b"", str(e).encode()
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return None, b"", b"timed out"
    return proc.returncode, out, err


def parse_probe(data: dict) -> MediaInfo:
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    try:
        duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    video = None
    for s in streams:
        if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic"):
            side = s.get("side_data_list") or []
            video = VideoInfo(
                index=int(s.get("index", 0)), codec=str(s.get("codec_name") or "?"),
                width=int(s.get("width") or 0), height=int(s.get("height") or 0),
                pix_fmt=str(s.get("pix_fmt") or ""), transfer=s.get("color_transfer"),
                dolby_vision=any("DOVI" in str(x.get("side_data_type", "")) or "Dolby Vision" in
                                 str(x.get("side_data_type", "")) for x in side if isinstance(x, dict)),
            )
            break
    return MediaInfo(duration=duration, streams=len(streams), video=video)


async def probe(ffprobe: str, path: Path) -> MediaInfo | None:
    rc, out, _ = await _run([ffprobe, "-v", "error", "-print_format", "json", "-show_format",
                             "-show_streams", str(path)])
    if rc != 0:
        return None
    try:
        return parse_probe(json.loads(out))
    except (ValueError, TypeError):
        return None


_CROP_RE = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")


def choose_crop(found: list[tuple[int, int, int, int]], width: int, height: int) -> Crop | None:
    """The crop to apply, from cropdetect samples (w, h, x, y) across the film.

    Black bars are constant; dark scenes only make the picture *look* smaller.
    So take the largest picture area any sample saw (never cut into the
    picture) and insist that most samples see that same height or width, so
    one odd frame can't decide.
    """
    found = [f for f in found if 0 < f[0] <= width and 0 < f[1] <= height]
    if not found or not width or not height:
        return None
    w, h = max(f[0] for f in found), max(f[1] for f in found)
    x = min(f[2] for f in found if f[0] == w)
    y = min(f[3] for f in found if f[1] == h)
    agree_h = sum(1 for f in found if f[1] == h) / len(found)
    agree_w = sum(1 for f in found if f[0] == w) / len(found)
    if h < height and agree_h < CROP_AGREEMENT or w < width and agree_w < CROP_AGREEMENT:
        return None
    removed = 1 - (w * h) / (width * height)
    if removed < CROP_MIN_FRACTION:
        return None
    # Even sizes and offsets for 4:2:0 encoders.
    return Crop(w - w % 2, h - h % 2, x - x % 2, y - y % 2)


async def detect_crop(ffmpeg: str, path: Path, duration: float, width: int, height: int) -> Crop | None:
    if duration <= 0:
        return None
    found: list[tuple[int, int, int, int]] = []
    for i in range(CROP_SAMPLES):
        t = duration * (0.1 + 0.8 * i / max(CROP_SAMPLES - 1, 1))
        pre = min(SETTLE_S, t)
        rc, _, err = await _run([ffmpeg, "-hide_banner", "-ss", f"{t - pre:.2f}", "-i", str(path), "-frames:v",
                                 str(CROP_FRAMES), "-vf", f"select=gte(t\\,{pre:.2f}),cropdetect=24:2:0",
                                 "-an", "-sn", "-f", "null", "-"])
        hits = _CROP_RE.findall(err.decode(errors="replace"))
        if hits:
            found.append(tuple(int(v) for v in hits[-1]))
    return choose_crop(found, width, height)


def plan(info: MediaInfo, s, disc_kind: str | None, encoder: str) -> Plan:
    """What to do with one file under settings ``s``."""
    v = info.video
    if v is None:
        return Plan(False, reasons=["no video stream"])
    if not s.transcode_enabled:
        return Plan(False, reasons=["transcoding is off"])
    if v.hdr and not s.transcode_hdr:
        return Plan(False, reasons=[f"{'Dolby Vision' if v.dolby_vision else 'HDR'} source left untouched"])
    source = s.transcode_source(disc_kind)
    if not source["transcode"]:
        return Plan(False, reasons=[f"{DISC_NAMES.get(disc_kind, disc_kind)} discs are not transcoded"])
    crop_mode = s.transcode_crop if source["crop"] else "off"
    wanted = v.codec.lower() in {c.lower() for c in s.transcode_codecs}
    if not wanted and crop_mode != "always":
        return Plan(False, reasons=[f"{v.codec} is not on the transcode list"])
    reasons = [f"{v.codec} → H.265"] if wanted else [f"{v.codec} re-encoded to crop black bars"]
    return Plan(True, encoder=encoder, reasons=reasons, crop_mode=crop_mode, codec_wanted=wanted)


async def pick_encoder(ffmpeg: str, s) -> str:
    """'vaapi' when the GPU can encode HEVC, else 'x265'."""
    if s.transcode_encoder in ("vaapi", "x265"):
        return s.transcode_encoder
    if not os.path.exists(s.vaapi_device):
        return "x265"
    rc, _, _ = await _run([ffmpeg, "-v", "error", "-init_hw_device", f"vaapi=va:{s.vaapi_device}",
                           "-filter_hw_device", "va", "-f", "lavfi", "-i", "color=black:s=256x144:d=0.2",
                           "-vf", "format=nv12,hwupload", "-c:v", "hevc_vaapi", "-f", "null", "-"], timeout=30)
    return "vaapi" if rc == 0 else "x265"


def command(ffmpeg: str, src: Path, dst: Path, video: VideoInfo, crop: Crop | None, encoder: str,
            quality: int, vaapi_device: str) -> list[str]:
    """The ffmpeg command. Re-encode the first video stream, copy everything else."""
    filters = [crop.filter()] if crop else []
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-v", "error", "-progress", "pipe:1", "-nostats"]
    if encoder == "vaapi":
        cmd += ["-init_hw_device", f"vaapi=va:{vaapi_device}", "-filter_hw_device", "va"]
    cmd += ["-i", str(src), "-map", "0", "-c", "copy", "-map_metadata", "0", "-map_chapters", "0"]
    vi = "0"  # first video stream in the output
    if encoder == "vaapi":
        filters += ["format=p010" if video.ten_bit else "format=nv12", "hwupload"]
        cmd += [f"-filter:v:{vi}", ",".join(filters), f"-c:v:{vi}", "hevc_vaapi", "-rc_mode", "CQP",
                "-qp", str(quality), f"-profile:v:{vi}", "main10" if video.ten_bit else "main"]
    else:
        if filters:
            cmd += [f"-filter:v:{vi}", ",".join(filters)]
        cmd += [f"-c:v:{vi}", "libx265", "-preset", "medium", "-crf", str(quality),
                "-pix_fmt", "yuv420p10le" if video.ten_bit else "yuv420p", "-x265-params", "log-level=error"]
    return [*cmd, str(dst)]


class Transcoder:
    """One ffmpeg run, killable, reporting progress (0..1), speed (× real time) and fps."""

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._tree: procs.Tree | None = None
        self.cancelled = False
        self.errors: deque[str] = deque(maxlen=20)  # ffmpeg's stderr, for the failure note

    async def run(self, cmd: list[str], duration: float, on_progress: Callable[[float, float | None, float | None], None],
                  log_path: Path) -> int | None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL, **procs.start_options())
        except OSError as e:
            log_path.write_text(f"$ {shlex.join(cmd)}\ncould not start ffmpeg: {e}\n", encoding="utf-8")
            return None
        self._tree = procs.Tree(self._proc.pid)
        speed: float | None = None
        fps: float | None = None
        # The log is what the UI shows, with the command, a progress line every
        # LOG_STEP of the way, ffmpeg's own errors, and how it ended.
        fh = log_path.open("w", encoding="utf-8")
        fh.write(f"$ {shlex.join(cmd)}\n")
        fh.flush()

        def note(text: str) -> None:
            fh.write(f"[{time.strftime('%H:%M:%S')}] {text}\n")
            fh.flush()

        def stats_text(stats: dict[str, str]) -> str:
            def known(k: str) -> str:
                v = stats.get(k, "")
                return "" if v in ("", "N/A") else v
            return "".join(f" · {x}" for x in (
                known("out_time").split(".")[0], known("frame") and f"frame {known('frame')}",
                fps and f"{fps:g} fps", f"{speed:g}×" if speed else "") if x)

        async def read_progress() -> None:
            nonlocal speed, fps
            assert self._proc and self._proc.stdout
            stats: dict[str, str] = {}
            logged = -1
            done = 0.0
            while line := await self._proc.stdout.readline():
                k, _, v = line.decode(errors="replace").strip().partition("=")
                stats[k] = v
                if k == "speed" and v.endswith("x"):
                    try:
                        speed = float(v[:-1])
                    except ValueError:
                        pass
                elif k == "fps":
                    try:
                        fps = float(v) or None  # 0.00 until ffmpeg has a rate
                    except ValueError:
                        pass
                elif k == "out_time_us" and v.lstrip("-").isdigit() and duration > 0:
                    # Never go backwards, because ffmpeg 7's last report can give the time of a stream
                    # that ended early (a subtitle), well before the end.
                    frac = done = max(done, min(1.0, max(0.0, int(v) / 1e6 / duration)))
                    on_progress(frac, speed, fps)
                    if (step := int(frac / LOG_STEP)) > logged:
                        logged = step
                        note(f"{frac:.0%}{stats_text(stats)}")
                elif k == "progress" and v == "end":
                    on_progress(1.0, speed, fps)
                    note(f"finished{stats_text(stats)}")

        async def read_errors() -> None:
            assert self._proc and self._proc.stderr
            while line := await self._proc.stderr.readline():
                text = line.decode(errors="replace").rstrip("\n")
                self.errors.append(text)
                fh.write(text + "\n")
                fh.flush()

        try:
            await asyncio.gather(read_progress(), read_errors())
            await self._proc.wait()
            note("cancelled" if self.cancelled else f"ffmpeg exited with code {self._proc.returncode}")
        except asyncio.CancelledError:
            self.kill()
            note("stopped")
            raise
        finally:
            fh.close()
            if self._tree:
                self._tree.close()
        return self._proc.returncode

    def kill(self) -> None:
        p = self._proc
        if p and p.returncode is None:
            (self._tree or procs.Tree(p.pid)).kill()


def output_ok(src: MediaInfo, out: MediaInfo | None) -> str | None:
    """Why the transcoded file can't replace the rip, or None if it can."""
    if out is None:
        return "the result could not be read"
    if out.video is None or out.video.codec != "hevc":
        return "the result has no H.265 video"
    if out.streams != src.streams:
        return f"the result has {out.streams} streams, the rip {src.streams}"
    if abs(out.duration - src.duration) > DURATION_TOLERANCE_S:
        return f"the result is {out.duration:.0f} s long, the rip {src.duration:.0f} s"
    return None
