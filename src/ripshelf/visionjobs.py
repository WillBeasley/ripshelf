"""Reading a job's scans with a vision model, beside Tesseract (mixed into
``jobs.Manager``).

When it runs is a setting: never, only for scans Tesseract struggled with, for every
scan, or only when asked (the scan's "Read with the vision model" button). A reading is
kept beside the scan as ``<id>.vision.json`` and merged into the listing with
Tesseract's (``listing.merge``); like Tesseract's, it never replaces the user's edits.

A local model keeps a desktop CPU busy for a minute or more a picture, so readings take
turns across jobs, and wait while a transcode runs. Cloud readings take seconds and
don't wait.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from .db import now
from .models import CoverScan, Listing, ScanText
from .scanner import images, vision
from .scanner import listing as parse

if TYPE_CHECKING:
    from .db import JobRecord

log = logging.getLogger(__name__)

LISTING_KINDS = ("back", "outside_full", "inside_full", "insert")
FEW_LINES = 8  # fewer confident lines than this is struggling
LOW_CONFIDENCE = 75
TRANSCODE_WAIT_S = 15


class VisionReading(BaseModel):
    key: str
    read_at: datetime
    provider: str
    model: str
    seconds: float
    listing: Listing


def struggles(sc: CoverScan, text: ScanText | None) -> list[str]:
    """Why Tesseract's reading of a scan is worth a second opinion, or nothing. Only
    scans that carry a listing (the back, the whole sheet, its inside, inserts)."""
    if sc.kind not in LISTING_KINDS:
        return []
    if text is None:
        return ["Tesseract couldn't read it"]
    why = []
    if sc.source == "camera":
        why.append("it's a photo")
    sure = [ln for ln in text.lines if ln.conf >= 70]
    if len(sure) < FEW_LINES:
        why.append(f"Tesseract read only {len(sure)} lines with confidence")
    elif sum(ln.conf for ln in text.lines) / len(text.lines) < LOW_CONFIDENCE:
        why.append("Tesseract wasn't confident")
    own = parse.build([text])
    if not own.items:
        why.append("no episodes or extras were found")
    if sc.kind in ("back", "outside_full") and not (own.details.region and not own.details.region_guessed) \
            and not own.details.video_system:
        why.append("the region and video system weren't read")
    return why


class VisionMixin:
    def _init_vision(self) -> None:
        self._vision_lock = asyncio.Lock()  # one reading at a time, across jobs
        self._vision_tasks: dict[int, asyncio.Task] = {}
        self._vision_again: set[int] = set()
        self._vision_asked: dict[int, set[str]] = {}  # job -> scans asked for by hand
        self.vision_transport = None  # tests put a fake server here

    def vision_provider(self) -> vision.Provider | None:
        """The provider set up in Settings, or None when it's off or not set up."""
        if self.s.vision_mode == "off":
            return None
        return self._model_provider(self.s.vision_model)

    def label_provider(self) -> vision.Provider | None:
        """The model that reads cryptic labels: the vision model's connection, with the
        label model if one is set. None when it's off or not set up."""
        if self.s.label_ai == "off":
            return None
        return self._model_provider((self.s.label_ai_model or "").strip() or self.s.vision_model)

    def _model_provider(self, model: str | None) -> vision.Provider | None:
        s = self.s
        if not model or (s.vision_provider != "anthropic" and not s.vision_url):
            return None
        # Anthropic's API has its own address: an address left from a local model (the
        # Settings page hides it for Anthropic) would send the request there, as the Test
        # button doesn't.
        url = None if s.vision_provider == "anthropic" else s.vision_url
        return vision.Provider(kind=s.vision_provider, url=url, model=model,
                               key=s.secret("vision_api_key"), transport=self.vision_transport)

    def _vision_path(self, job: JobRecord, scan_id: str) -> Path:
        return self._scans_dir(job) / f"{scan_id}.vision.json"

    def _vision_key(self, sc: CoverScan, provider: vision.Provider) -> str:
        return f"{self._read_key(sc)}:{provider.kind}:{provider.model}"

    def vision_reading(self, job: JobRecord, sc: CoverScan) -> VisionReading | None:
        """The vision model's reading of the scan, if it's for the scan as it is now."""
        try:
            r = VisionReading.model_validate_json(self._vision_path(job, sc.id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return r if r.key.startswith(self._read_key(sc) + ":") else None

    def _wanted(self, job: JobRecord, provider: vision.Provider) -> list[CoverScan]:
        """Scans to read with the vision model now, as Settings say."""
        mode = self.s.vision_mode
        asked = self._vision_asked.get(job.id, set())
        out = []
        for sc in job.scans:
            r = self.vision_reading(job, sc)
            if r and r.key == self._vision_key(sc, provider) and sc.id not in asked:
                continue
            if sc.id in asked or mode == "every" and sc.kind in (*LISTING_KINDS, "front") or \
                    mode == "struggles" and struggles(sc, self.scan_text(job.id, sc.id)):
                out.append(sc)
        return out

    def ask_vision(self, job_id: int, scan_id: str) -> None:
        """Read this scan with the vision model now (the scan's button)."""
        job = self._job(job_id)
        self._find(job, scan_id)
        if not self.vision_provider():
            raise ValueError("set up a vision model in Settings → Scanner and covers first")
        self._vision_asked.setdefault(job_id, set()).add(scan_id)
        self.queue_vision(job_id)

    def queue_vision(self, job_id: int) -> None:
        if not self.vision_provider():
            return
        task = self._vision_tasks.get(job_id)
        if task and not task.done():
            self._vision_again.add(job_id)
            return
        self._vision_tasks[job_id] = asyncio.create_task(self._vision_job(job_id))

    def _set_vision(self, job_id: int, scan_id: str, state: str | None, message: str | None = None) -> None:
        job = self.db.get_job(job_id)
        sc = next((x for x in job.scans if x.id == scan_id), None) if job else None
        if sc:
            sc.vision, sc.vision_message = state, message
            self._save_scans(job)

    async def _vision_job(self, job_id: int) -> None:
        try:
            while True:
                self._vision_again.discard(job_id)
                await self._vision_once(job_id)
                if job_id not in self._vision_again:
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("reading the scans of job %s with the vision model failed", job_id)
        finally:
            self._vision_tasks.pop(job_id, None)

    async def _vision_once(self, job_id: int) -> None:
        provider = self.vision_provider()
        job = self.db.get_job(job_id)
        if not provider or not job:
            return
        todo = self._wanted(job, provider)
        for sc in todo:
            self._set_vision(job_id, sc.id, "queued", "Waiting for another reading to finish")
        for sc in todo:
            async with self._vision_lock:
                while not provider.cloud and self._transcoders:
                    self._set_vision(job_id, sc.id, "queued", "Waiting for the transcode to finish")
                    await asyncio.sleep(TRANSCODE_WAIT_S)
                self._set_vision(job_id, sc.id, "reading", f"Reading with {provider.model}")
                await self._vision_scan(job_id, sc.id, provider)
            self._vision_asked.get(job_id, set()).discard(sc.id)
        if todo:
            self._rebuild_listing(job_id)

    async def _vision_scan(self, job_id: int, scan_id: str, provider: vision.Provider) -> None:
        job = self.db.get_job(job_id)
        sc = next((x for x in job.scans if x.id == scan_id), None) if job else None
        found = self.scan_file(job_id, scan_id) if sc else None
        if not found:
            return
        try:
            img = await asyncio.to_thread(images.render, found[0].read_bytes(), sc.crop, sc.rotate)
            listing, secs = await vision.read_listing(provider, img)
        except (vision.VisionError, OSError, images.ImageError) as e:
            self._set_vision(job_id, scan_id, "failed", str(e)[:1].upper() + str(e)[1:])
            return
        job = self.db.get_job(job_id)
        sc = next((x for x in job.scans if x.id == scan_id), None) if job else None
        if not sc:
            return
        reading = VisionReading(key=self._vision_key(sc, provider), read_at=now(), provider=provider.kind,
                                model=provider.model, seconds=round(secs, 1), listing=listing)
        self._vision_path(job, scan_id).write_text(reading.model_dump_json(), encoding="utf-8")
        n = len(listing.items)
        self._set_vision(job_id, scan_id, "done", f"{provider.model} read {n} item{'s' if n != 1 else ''} "
                                                  f"in {secs:.0f} s")
