"""The transcode step of the pipeline (mixed into ``jobs.Manager``).

Runs after the rip and the identity are settled, before finalise, one file at
a time per job. Up to ``transcode_parallel`` jobs transcode at once (one by
default, as they share the GPU). It runs as a job task that does not belong to
the drive, so the drive takes the next disc meanwhile.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from . import library, transcode
from .models import (
    JobState,
    MediaKind,
    Problem,
    RipProgress,
    TitleRole,
    TranscodeFile,
    TranscodeInfo,
)

if TYPE_CHECKING:
    from .db import JobRecord

log = logging.getLogger(__name__)


class Slots:
    """A semaphore whose size is read on each acquire, so a new limit applies to
    the jobs already waiting (call ``resized``). Lowering it lets the transcodes
    already running finish."""

    def __init__(self, size: Callable[[], int]) -> None:
        self._size = size
        self._busy = 0
        self._cond = asyncio.Condition()

    def full(self) -> bool:
        return self._busy >= self._size()

    async def resized(self) -> None:
        async with self._cond:
            self._cond.notify_all()

    async def __aenter__(self) -> None:
        async with self._cond:
            await self._cond.wait_for(lambda: not self.full())
            self._busy += 1

    async def __aexit__(self, *exc) -> None:
        async with self._cond:
            self._busy -= 1
            self._cond.notify_all()


class TranscodeMixin:
    def _init_transcode(self) -> None:
        self._gpu = Slots(lambda: self.s.transcode_parallel)
        self._bg: dict[int, set[asyncio.Task]] = {}
        self._transcoders: dict[int, transcode.Transcoder] = {}

    async def transcode_limit_changed(self) -> None:
        await self._gpu.resized()

    def _spawn_bg(self, coro, job_id: int) -> asyncio.Task:
        """A job task not tied to a drive (transcode + finalise)."""
        async def guarded() -> None:
            try:
                await coro
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("job %s finish crashed", job_id)
                self._fail(self.db.get_job(job_id), Problem(
                    code="internal", message=f"Internal error: {type(e).__name__}: {e}", actions=["retry"]))

        task = asyncio.create_task(guarded())
        self._bg.setdefault(job_id, set()).add(task)
        task.add_done_callback(lambda t: self._bg.get(job_id, set()).discard(t))
        return task

    def _cancel_bg(self, job_id: int) -> None:
        t = self._transcoders.get(job_id)
        if t:
            t.cancelled = True
            t.kill()
        for task in list(self._bg.get(job_id, ())):
            task.cancel()

    def _transcode_targets(self, job: JobRecord) -> dict[int, str]:
        """Ripped files that go to the library (title -> path)."""
        if job.media == MediaKind.tv and job.tv:
            wanted = {m.title_index for m in job.tv.mapping if m.role in (TitleRole.episode, TitleRole.extra)}
            return {i: f for i, f in job.rip.files.items() if i in wanted}
        if job.rip.file and job.main_title:
            return {job.main_title.index: job.rip.file}
        return {}

    async def _finish(self, job_id: int) -> None:
        job = self.db.get_job(job_id)
        if job.mode == "backup":  # kept exactly as on the disc
            await self._finalise_backup(job)
            return
        if self.s.transcode_enabled and not (job.transcode and job.transcode.state == "done"):
            await self._transcode(job_id)
            job = self.db.get_job(job_id)
            if job.state in (JobState.failed, JobState.cancelled):
                return
        if job.media == MediaKind.tv:
            await self._finalise_tv(job)
        else:
            await self._finalise(job)

    async def _transcode(self, job_id: int) -> None:
        job = self.db.get_job(job_id)
        targets = self._transcode_targets(job)
        waiting = self._gpu.full()
        if waiting:
            self._event(job, "info", "Waiting for another job's transcode to finish")
        self._set_state(job, JobState.transcoding, f"Transcode check for {len(targets)} file(s)")
        if waiting:
            n = self.s.transcode_parallel
            self._set_step(job, "Waiting to transcode",
                           f"Other jobs are using the encoder; {n} transcode{'s' if n > 1 else ''} at a time")
        async with self._gpu:
            job = self.db.get_job(job_id)
            self._set_step(job, "Checking whether to transcode", "Choosing the encoder")
            encoder = await transcode.pick_encoder(self.s.ffmpeg, self.s)
            job = self.db.get_job(job_id)
            job.transcode = TranscodeInfo(state="running")
            self._save(job)
            for n, (idx, path) in enumerate(targets.items(), 1):
                of = f" ({n} of {len(targets)})" if len(targets) > 1 else ""
                result = await self._transcode_file(job_id, idx, Path(path), encoder, of)
                job = self.db.get_job(job_id)
                if job.state == JobState.cancelled:
                    return
                job.transcode.files.append(result)
                if result.action == "transcoded":
                    out = Path(path).with_suffix(".h265.mkv")
                    if job.media == MediaKind.tv:
                        job.rip.files[idx] = str(out)
                    else:
                        job.rip.file = str(out)
                self._save(job)
            job = self.db.get_job(job_id)
            job.transcode.state = "done"
            self._save(job)

    async def _transcode_file(self, job_id: int, idx: int, src: Path, encoder: str, of: str = "") -> TranscodeFile:
        job = self.db.get_job(job_id)
        rec = TranscodeFile(title_index=idx, size_before=src.stat().st_size if src.exists() else None)
        self._set_step(job, f"Checking whether title {idx} needs transcoding{of}",
                       "Reading the video format (ffprobe)")
        info = await transcode.probe(self.s.ffprobe, src)
        if info is None or info.video is None:
            rec.note = "could not read the ripped file (is ffprobe installed?)"
            self._event(job, "warning", f"Title {idx}: {rec.note}; copied as ripped")
            return rec
        rec.source_codec = info.video.codec
        plan = transcode.plan(info, self.s, job.disc.kind.value if job.disc.kind else None, encoder)
        if not plan.transcode:
            rec.note = plan.summary
            self._event(job, "info", f"Title {idx}: no transcode ({plan.summary})")
            return rec
        crop = None
        if plan.crop_mode != "off":
            self._set_step(job, f"Looking for black bars in title {idx}{of}",
                           f"{plan.summary}; sampling frames to decide the crop")
            crop = await transcode.detect_crop(self.s.ffmpeg, src, info.duration, info.video.width,
                                               info.video.height)
        if not plan.codec_wanted and crop is None:
            rec.note = "no black bars to crop"
            self._event(job, "info", f"Title {idx}: no transcode ({rec.note})")
            return rec
        rec.encoder, rec.crop = encoder, crop.filter() if crop else None
        dst = src.with_suffix(".h265.mkv")
        cmd = transcode.command(self.s.ffmpeg, src, dst, info.video, crop, encoder, self.s.transcode_quality,
                                self.s.vaapi_device)
        what = f"{info.video.codec} → H.265 ({transcode.ENCODER_NAMES.get(encoder, encoder)})" + \
            (f", cropping to {crop.w}×{crop.h}" if crop else "")
        self._event(job, "transcode", f"Title {idx}: {what}")
        self._set_step(job, f"Transcoding title {idx}{of}", what)
        tc = transcode.Transcoder()
        self._transcoders[job_id] = tc
        size = rec.size_before or 0
        loop = asyncio.get_running_loop()
        started = loop.time()

        def on_progress(frac: float, speed: float | None, fps: float | None) -> None:
            elapsed = max(loop.time() - started, 1e-6)
            eta = (1 - frac) * info.duration / speed if speed else (elapsed / frac * (1 - frac) if frac else None)
            rate = " · ".join(x for x in (fps and f"{fps:.0f} fps", speed and f"{speed:.1f}×") if x)
            self._progress(job_id, RipProgress(
                phase=f"Transcoding title {idx}" + (f" ({rate})" if rate else ""), fraction=frac,
                bytes_done=int(frac * size), bytes_total=size, bytes_per_s=frac * size / elapsed, eta_s=eta),
                [0.0])

        try:
            rc = await tc.run(cmd, info.duration, on_progress, self.work_dir(job) / f"transcode-{idx}.log")
        finally:
            self._transcoders.pop(job_id, None)
        if tc.cancelled:
            dst.unlink(missing_ok=True)
            rec.action, rec.note = "failed", "cancelled"
            return rec
        if rc == 0:
            self._set_step(job, f"Checking the transcoded title {idx}{of}",
                           "Comparing its length and streams with the rip")
        problem = "ffmpeg failed" if rc != 0 else transcode.output_ok(info, await transcode.probe(self.s.ffprobe, dst))
        if problem:
            tail = [e for e in tc.errors if e.strip()][-1:]
            rec.action, rec.note = "failed", problem + (f": {tail[0][:200]}" if tail else "")
            dst.unlink(missing_ok=True)
            self._event(job, "warning", f"Title {idx}: transcode failed ({rec.note}); the rip is used as it is")
            return rec
        rec.action, rec.size_after = "transcoded", dst.stat().st_size
        if self.s.transcode_keep_original:
            rec.original = str(src)
        else:
            src.unlink(missing_ok=True)
        pct = round(100 * rec.size_after / size) if size else 0
        self._event(job, "transcode", f"Title {idx}: transcoded, {pct}% of the original size")
        return rec

    def _transcode_summary(self, job: JobRecord) -> str:
        """How the transcode went, for the copy step, with what was skipped and why."""
        if not job.transcode:
            return "not transcoded (transcoding is off)" if not self.s.transcode_enabled else ""
        files = job.transcode.files
        done = [f for f in files if f.action == "transcoded"]
        left = [f for f in files if f.action != "transcoded"]
        if not left:
            return "transcoded to H.265" if done else ""
        why = "; ".join(dict.fromkeys(f.note for f in left if f.note))
        if not done:
            return "not transcoded" + (f" ({why})" if why else "")
        return f"{len(done)} transcoded, {len(left)} as ripped" + (f" ({why})" if why else "")

    def _copy_detail(self, job: JobRecord, what: str) -> str:
        summary = self._transcode_summary(job)
        return f"{what} · {summary}" if summary else what

    async def _copy_originals(self, job: JobRecord, pairs: list[tuple[str, Path]]) -> None:
        """Kept originals go next to their transcoded file as "<name> - original.mkv"."""
        for n, (original, dest) in enumerate(pairs, 1):
            target = dest.with_name(f"{dest.stem} - original{dest.suffix}")
            of = f" {n} of {len(pairs)}" if len(pairs) > 1 else ""
            on_copy = self._copy_progress(job.id, f"Copying the kept original{of}")
            try:
                await asyncio.to_thread(library.copy_verified, Path(original), target, on_copy)
                Path(original).unlink(missing_ok=True)
            except (OSError, library.VerifyError) as e:
                self._event(job, "warning", f"Could not keep the original as {target.name}: {e}")

    def _originals(self, job: JobRecord) -> dict[int, str]:
        if not job.transcode:
            return {}
        return {f.title_index: f.original for f in job.transcode.files if f.original}

