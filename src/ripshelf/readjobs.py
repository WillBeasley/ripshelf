"""Reading the text on a job's scans, and the listing of what's on the disc made from
it (mixed into ``jobs.Manager``).

A scan is read in the background when it's added, and again when something the
reading depends on changes (its kind, trim, turn or cut). What was read is kept beside
the scan as ``<id>.text.json``; the listing is made again from all the job's readings
each time, into ``listing.found``. The user's own version, ``listing.edited``, is never
replaced by a reading: when the scans are read again after it was edited, the job
says so, and the user picks.

Reading is slow (several seconds a picture, most of a CPU), so jobs take turns.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .db import now
from .models import CoverScan, Listing, ListingView, ScanListing, ScanText
from .scanner import images, ocr
from .scanner import listing as parse

if TYPE_CHECKING:
    from .db import JobRecord

log = logging.getLogger(__name__)

READER_VERSION = 1  # bump when reading changes enough to be worth doing again
PART_NAMES = {"front": "front cover", "back": "back cover", "outside_full": "whole outside",
              "inside_full": "inside of the cover", "insert": "insert", "other": "picture"}


class ReadingMixin:
    def _init_reading(self) -> None:
        self._read_tasks: dict[int, asyncio.Task] = {}
        self._read_again: set[int] = set()
        self._read_lock = asyncio.Lock()  # one reading at a time, across jobs
        self.text_reader: ocr.Tesseract | None = None  # tests put a fake here

    def _reader(self) -> ocr.Tesseract:
        if self.text_reader:
            return self.text_reader
        return ocr.Tesseract(self.s.tesseract, self.s.ocr_languages)

    def _text_path(self, job: JobRecord, scan_id: str) -> Path:
        return self._scans_dir(job) / f"{scan_id}.text.json"

    def _read_key(self, sc: CoverScan) -> str:
        split = sc.split.model_dump_json() if sc.split and sc.kind == "outside_full" else ""
        return f"{READER_VERSION}:{sc.sha256}:{sc.kind}:{sc.rotate}:{sc.crop}:{split}:{self.s.ocr_languages}"

    def scan_text(self, job_id: int, scan_id: str) -> ScanText | None:
        job = self.db.get_job(job_id)
        if not job:
            return None
        p = self._text_path(job, scan_id)
        try:
            return ScanText.model_validate_json(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    # --- the listing ------------------------------------------------------------------

    def listing_view(self, job_id: int) -> ListingView:
        job = self._job(job_id)
        sl = job.listing or ScanListing()
        return ListingView(listing=sl, current=sl.edited or sl.found, newer_reading=sl.stale_edits,
                           available=self.s.scan_read_text and sl.state != "unavailable")

    def save_listing(self, job_id: int, edited: Listing) -> ListingView:
        """Keep the user's listing. Readings never replace it."""
        job = self._job(job_id)
        sl = job.listing or ScanListing()
        # What the user changed or added is theirs; what they kept keeps its reader.
        kept = {(i.kind, i.title, i.number, i.runtime_min, i.extra_type, i.disc): i.source
                for i in (sl.found.items if sl.found else [])}
        for i in edited.items:
            i.source = kept.get((i.kind, i.title, i.number, i.runtime_min, i.extra_type, i.disc), "you")
        sl.edited = edited
        sl.edited_from = sl.read_at or now()
        job.listing = sl
        self._save_scans(job)
        self.rematch(job_id)
        return self.listing_view(job_id)

    def drop_listing_edits(self, job_id: int) -> ListingView:
        """Go back to what was read."""
        job = self._job(job_id)
        if job.listing:
            job.listing.edited = job.listing.edited_from = None
            self._save_scans(job)
            self.rematch(job_id)
        return self.listing_view(job_id)

    # --- reading ----------------------------------------------------------------------

    def read_scans(self, job_id: int, again: bool = False) -> None:
        """Read the job's scans that need it, in the background (``again`` reads them all).
        A request while a reading runs makes it go round once more."""
        if not self.s.scan_read_text:
            return
        if again:
            job = self.db.get_job(job_id)
            for sc in job.scans if job else []:
                self._text_path(job, sc.id).unlink(missing_ok=True)
        task = self._read_tasks.get(job_id)
        if task and not task.done():
            self._read_again.add(job_id)
            return
        self._read_tasks[job_id] = asyncio.create_task(self._read_job(job_id))

    def _set_listing(self, job_id: int, **kw) -> None:
        job = self.db.get_job(job_id)
        if not job:
            return
        sl = job.listing or ScanListing()
        for k, v in kw.items():
            setattr(sl, k, v)
        job.listing = sl
        self._save_scans(job)

    async def _read_job(self, job_id: int) -> None:
        try:
            while True:
                self._read_again.discard(job_id)
                await self._read_once(job_id)
                if job_id not in self._read_again:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:  # never leave the job saying it's reading
            log.exception("reading the scans of job %s failed", job_id)
            self._set_listing(job_id, state="failed", progress=None, message=f"Reading failed: {e}")
        finally:
            self._read_tasks.pop(job_id, None)

    async def _read_once(self, job_id: int) -> None:
        job = self.db.get_job(job_id)
        if not job:
            return
        reader = self._reader()
        try:
            engine = await reader.version()
        except ocr.OcrUnavailable as e:
            self._set_listing(job_id, state="unavailable", progress=None, message=str(e))
            return
        todo = [sc for sc in job.scans if (t := self.scan_text(job_id, sc.id)) is None
                or t.key != self._read_key(sc)]
        if todo:
            self._set_listing(job_id, state="reading", message=None, progress="Waiting for another job's reading")
            async with self._read_lock:
                for n, sc in enumerate(todo, 1):
                    self._set_listing(job_id, state="reading",
                                      progress=f"Reading the {PART_NAMES[sc.kind]} ({n} of {len(todo)})")
                    try:
                        await self._read_scan(job_id, sc.id, reader, engine)
                    except ocr.OcrUnavailable as e:
                        self._set_listing(job_id, state="unavailable", progress=None, message=str(e))
                        return
                    except (OSError, images.ImageError) as e:
                        log.info("could not read scan %s of job %s: %s", sc.id, job_id, e)
        self._rebuild_listing(job_id, engine)

    async def _read_scan(self, job_id: int, scan_id: str, reader: ocr.Tesseract, engine: str) -> None:
        job = self.db.get_job(job_id)
        sc = next((x for x in job.scans if x.id == scan_id), None) if job else None
        found = self.scan_file(job_id, scan_id) if sc else None
        if not found:
            return
        data = found[0].read_bytes()
        img = await asyncio.to_thread(images.render, data, sc.crop, sc.rotate)
        started = time.monotonic()
        reading = await ocr.read_picture(reader, img, sc.kind, sc.split)
        job = self.db.get_job(job_id)
        sc = next((x for x in job.scans if x.id == scan_id), None) if job else None
        if not sc:
            return
        if reading.turned:
            # The text says which way up the picture is; show it that way.
            sc.rotate = (sc.rotate + reading.turned) % 360
            sc.split = None
            self._forget_thumbs(job, sc)
        if sc.kind == "outside_full" and (sc.split is None or sc.split.auto):
            sc.split = reading.split  # where the spine is, as found (none: cut by hand)
        if reading.turned or sc.kind == "outside_full":
            self._save_scans(job)
        text = ScanText(scan_id=scan_id, key=self._read_key(sc), read_at=now(), engine=engine,
                        seconds=round(time.monotonic() - started, 1), turned=reading.turned,
                        lines=reading.lines, more=reading.more, barcodes=reading.barcodes)
        p = self._text_path(job, scan_id)
        p.write_text(text.model_dump_json(), encoding="utf-8")

    def _rebuild_listing(self, job_id: int, engine: str | None = None) -> None:
        job = self.db.get_job(job_id)
        if not job:
            return
        texts = [t for sc in job.scans if (t := self.scan_text(job_id, sc.id))]
        found = parse.build(texts) if texts else None
        seen = [(sc.id, r.listing) for sc in job.scans if (r := self.vision_reading(job, sc))]
        if seen:
            found = parse.merge(found or Listing(), seen, texts)
        sl = job.listing or ScanListing()
        changed = (found.model_dump() if found else None) != (sl.found.model_dump() if sl.found else None)
        sl.found = found
        sl.state = "done" if texts or seen else "idle"
        sl.progress = sl.message = None
        sl.engine = engine or sl.engine
        if changed:
            sl.read_at = now()
        job.listing = sl
        self._save_scans(job)
        self.rematch(job_id)
        self.identify_from_scan(job_id)
        if texts and engine:  # a Tesseract reading: the vision model's turn, if Settings say
            self.queue_vision(job_id)

    def _inherit_reading(self, job: JobRecord, other: JobRecord) -> None:
        """With the scans of another disc in the release, its readings and listing."""
        for sc in job.scans:
            src = self._text_path(other, sc.id)
            if src.is_file():
                shutil.copyfile(src, self._text_path(job, sc.id))
            if (vsrc := self._vision_path(other, sc.id)).is_file():
                shutil.copyfile(vsrc, self._vision_path(job, sc.id))
        if other.listing and not job.listing:
            job.listing = other.listing.model_copy(deep=True)
            if job.listing.state == "reading":
                job.listing.state, job.listing.progress = "done", None

    async def cancel_reading(self) -> None:
        for t in list(self._read_tasks.values()):
            t.cancel()


def dump(listing: Listing) -> str:
    return json.dumps(listing.model_dump(mode="json"), indent=1)
