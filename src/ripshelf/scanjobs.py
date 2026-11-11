"""Scans and photos of a disc's box (mixed into ``jobs.Manager``).

Each job keeps its pictures in ``<jobs>/<id>/scans``: the original file as it came
(from the network scanner, an upload or a phone's camera), and a thumbnail. Front and
back scans become the TheDiscDB covers too, and a whole outer sheet can be cut into
both. Once the job is filed, the originals are copied into the film's or show's
folder, in ``scans/``, under readable names; a file already there is never replaced.

The network scanner is one device, so it scans for one job at a time.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from . import library
from .db import now
from .models import (
    ContributionView,
    CoverScan,
    JobState,
    MediaKind,
    ScanKind,
    ScannerInfo,
    ScannerRun,
    ScannerScanRequest,
    ScanSource,
    ScanSplit,
    ScanUpdate,
)
from .providers import release_info
from .scanner import images
from .scanner.escl import Capabilities, Escl, ScannerError

if TYPE_CHECKING:
    from .db import JobRecord

log = logging.getLogger(__name__)

MAX_SCAN_BYTES = release_info.MAX_COVER_BYTES
MAX_SCANS = 60
EXTS = release_info.COVER_TYPES
COVER_KINDS = ("front", "back")
# File names in the library's scans/ folder. None of them is a name Emby, Jellyfin or
# Plex reads as artwork (poster, folder, cover, fanart, backdrop...).
LIBRARY_NAMES: dict[str, str] = {"front": "front", "back": "back", "outside_full": "outside-full",
                                 "inside_full": "inside-full", "insert": "insert", "other": "other"}
NUMBERED = ("insert", "other")


class ScansMixin:
    def _init_scans(self) -> None:
        self._scanner_task: asyncio.Task | None = None
        self._scanner_job: int | None = None
        self._scanner_url: str | None = None  # the job being scanned's address, to cancel it
        self._scanner_caps: dict[str, Capabilities] = {}
        # Tests point these at a fake scanner.
        self.escl_transport: httpx.AsyncBaseTransport | None = None
        self.escl_busy_wait_s: float | None = None

    def _scans_dir(self, job: JobRecord) -> Path:
        return self.work_dir(job) / "scans"

    def _job(self, job_id: int) -> JobRecord:
        job = self.db.get_job(job_id)
        if not job:
            raise KeyError(job_id)
        return job

    @staticmethod
    def _find(job: JobRecord, scan_id: str) -> CoverScan:
        for sc in job.scans:
            if sc.id == scan_id:
                return sc
        raise KeyError(scan_id)

    def scan_file(self, job_id: int, scan_id: str) -> tuple[Path, str] | None:
        job = self.db.get_job(job_id)
        sc = next((x for x in job.scans if x.id == scan_id), None) if job else None
        if not sc:
            return None
        p = self._scans_dir(job) / f"{sc.id}.{EXTS[sc.content_type]}"
        return (p, sc.content_type) if p.is_file() else None

    async def scan_thumb(self, job_id: int, scan_id: str, large: bool = False, whole: bool = False) -> Path | None:
        """A JPEG of the scan as it's used (trimmed and turned): a thumbnail, or a large
        copy for looking at, or with ``whole`` a large one not trimmed (to crop it by
        hand). Made when first asked for."""
        found = self.scan_file(job_id, scan_id)
        if not found:
            return None
        job = self.db.get_job(job_id)
        sc = self._find(job, scan_id)
        size = "whole" if whole else "view" if large else "thumb"
        out = self._scans_dir(job) / f"{sc.id}.{size}.jpg"
        if not out.is_file():
            await asyncio.to_thread(self._write_thumb, found[0], sc, out, large or whole, whole)
        return out

    @staticmethod
    def _write_thumb(src: Path, sc: CoverScan, out: Path, large: bool, whole: bool) -> None:
        img = images.render(src.read_bytes(), None if whole else sc.crop, sc.rotate)
        out.write_bytes(images.thumbnail(img, images.VIEW_PX if large else images.THUMB_PX))

    def _forget_thumbs(self, job: JobRecord, sc: CoverScan) -> None:
        """The thumbnails made from the scan as it was trimmed and turned."""
        for size in ("thumb", "view", "whole"):
            (self._scans_dir(job) / f"{sc.id}.{size}.jpg").unlink(missing_ok=True)

    # --- adding, changing and removing ----------------------------------------------

    async def add_scan(self, job_id: int, data: bytes, kind: ScanKind, source: ScanSource,
                       page: int | None = None) -> CoverScan:
        """Keep a picture of the box (JPEG, PNG or WebP, at most 20 MB)."""
        if len(data) > MAX_SCAN_BYTES:
            raise ValueError("the picture is larger than 20 MB")
        ctype = release_info.sniff(data)
        if not ctype:
            raise ValueError("the picture must be a JPEG, PNG or WebP")
        job = self._job(job_id)
        if len(job.scans) >= MAX_SCANS:
            raise ValueError(f"a job keeps at most {MAX_SCANS} pictures; remove some first")
        # Scans have the empty glass around them; a photo is all picture.
        trim = source != "camera" and self.s.scanner_trim

        def measure() -> tuple[int, int, list[int] | None, ScanSplit | None]:
            img = images.open_image(data)
            box = images.trim_box(img) if trim else None
            # A whole sheet the right way up: where its spine is (a sideways one is
            # turned by the reading, and its spine found then).
            split = images.find_spine(img.crop(box) if box else img) if kind == "outside_full" else None
            return img.width, img.height, box, split

        width, height, crop, split = await asyncio.to_thread(measure)
        if kind in NUMBERED and page is None:
            page = max((s.page or 0 for s in job.scans if s.kind == kind), default=0) + 1
        sc = CoverScan(id=uuid.uuid4().hex[:12], kind=kind, source=source, page=page, created_at=now(),
                       content_type=ctype, width=width, height=height,
                       sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), crop=crop, split=split)
        d = self._scans_dir(job)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{sc.id}.{EXTS[ctype]}").write_bytes(data)
        job = self._job(job_id)  # the picture took a while to read
        job.scans.append(sc)
        self._save_scans(job)
        what = {"scanner": "Scanned", "upload": "Added a picture of", "camera": "Took a photo of"}[source]
        self._event(job, "info", f"{what} the {self._kind_name(sc)}")
        if kind in COVER_KINDS:
            await self._scan_cover_quietly(job_id, sc.id)
        await self._file_scans_if_done(job_id)
        self.read_scans(job_id)
        return self._find(self._job(job_id), sc.id)

    @staticmethod
    def _kind_name(sc: CoverScan) -> str:
        name = {"front": "front cover", "back": "back cover", "outside_full": "whole outside of the cover",
                "inside_full": "inside of the cover", "insert": "insert", "other": "box"}[sc.kind]
        return f"{name} (page {sc.page})" if sc.page and sc.kind in NUMBERED else name

    async def update_scan(self, job_id: int, scan_id: str, body: ScanUpdate) -> CoverScan:
        job = self._job(job_id)
        sc = self._find(job, scan_id)
        if body.kind is not None and body.kind != sc.kind:
            sc.kind = body.kind
            if sc.kind not in NUMBERED:
                sc.page = None
            elif not sc.page:
                sc.page = max((s.page or 0 for s in job.scans if s.kind == sc.kind), default=0) + 1
            sc.split = None
        if body.page is not None and sc.kind in NUMBERED:
            sc.page = body.page
        if body.rotate is not None:
            sc.rotate = body.rotate
        if body.crop is not None:
            sc.crop = images.crop_from_shown(body.crop, sc.width, sc.height, sc.rotate)
            sc.crop_by_hand = True
        elif body.trim is not None:
            if body.trim:
                found = self.scan_file(job_id, scan_id)
                if found:
                    data = found[0].read_bytes()
                    sc.crop = await asyncio.to_thread(lambda: images.trim_box(images.open_image(data)))
            else:
                sc.crop = None
            sc.crop_by_hand = False
        if sc.kind == "outside_full" and (sc.split is None or sc.split.auto) and (
                body.kind is not None or body.rotate is not None or body.trim is not None or body.crop is not None):
            sc.split = await self._find_spine(job_id, sc)
        self._forget_thumbs(job, sc)
        self._save_scans(job)
        self.read_scans(job_id)  # read again if what the reading depends on changed
        return sc

    async def delete_scan(self, job_id: int, scan_id: str) -> None:
        """Forget a picture. Its copy in the library, if filed, stays."""
        job = self._job(job_id)
        sc = self._find(job, scan_id)
        d = self._scans_dir(job)
        (d / f"{sc.id}.{EXTS[sc.content_type]}").unlink(missing_ok=True)
        self._forget_thumbs(job, sc)
        self._text_path(job, sc.id).unlink(missing_ok=True)
        self._vision_path(job, sc.id).unlink(missing_ok=True)
        job.scans = [x for x in job.scans if x.id != scan_id]
        self._save_scans(job)
        if job.listing:
            self._rebuild_listing(job_id)

    # --- covers ---------------------------------------------------------------------

    async def scan_covers(self, job_id: int, scan_id: str, split: ScanSplit | None = None) -> ContributionView:
        """Use the scan as TheDiscDB's covers: a front or back scan as that cover, a
        whole outer sheet cut in two for both."""
        job = self._job(job_id)
        sc = self._find(job, scan_id)
        if sc.kind not in (*COVER_KINDS, "outside_full"):
            raise ValueError("only the front, the back or the whole outside of the cover can be the covers")
        self._editable(job_id)  # say why not before cutting anything
        found = self.scan_file(job_id, scan_id)
        if not found:
            raise KeyError(scan_id)
        data = found[0].read_bytes()
        img = await asyncio.to_thread(images.render, data, sc.crop, sc.rotate)
        if sc.kind in COVER_KINDS:
            return await self.set_cover(job_id, sc.kind, await asyncio.to_thread(images.jpeg, img))
        # A cut given here is the user's, so it's kept (never found again over it).
        split = split.model_copy(update={"auto": False}) if split else sc.split or images.find_spine(img) or ScanSplit()
        front, back = await asyncio.to_thread(images.halves, img, split)
        await self.set_cover(job_id, "front", await asyncio.to_thread(images.jpeg, front))
        view = await self.set_cover(job_id, "back", await asyncio.to_thread(images.jpeg, back))
        job = self._job(job_id)
        self._find(job, scan_id).split = split
        self._save_scans(job)
        self.read_scans(job_id)  # the parts are read where the sheet is cut
        return view

    async def _find_spine(self, job_id: int, sc: CoverScan) -> ScanSplit | None:
        """Where the spine of a whole sheet is, as it's trimmed and turned now."""
        found = self.scan_file(job_id, sc.id)
        if not found:
            return None
        data = found[0].read_bytes()
        try:
            return await asyncio.to_thread(lambda: images.find_spine(images.render(data, sc.crop, sc.rotate)))
        except images.ImageError:
            return None

    def _covers_from_scans(self, job: JobRecord) -> bool:
        """Covers the disc has no picture for yet, from the box's scans: scans taken
        before the disc could be sent (while it waited for review) count once it can.
        True if any was added."""
        c = job.contribution
        if not c or not job.scans or c.status == "sending":
            return False
        latest = {sc.kind: sc for sc in job.scans}
        todo = [side for side in COVER_KINDS if side not in c.covers]
        sheet = latest.get("outside_full")
        added = False
        for side in todo:
            sc = latest.get(side) or sheet
            found = self.scan_file(job.id, sc.id) if sc else None
            if not found:
                continue
            try:
                img = images.render(found[0].read_bytes(), sc.crop, sc.rotate)
                if sc.kind == "outside_full":
                    # Cut where the spine is: as set, or found. Not found, it waits for
                    # "Cut into front and back covers" rather than being cut on a guess.
                    split = sc.split or images.find_spine(img)
                    if not split:
                        continue
                    front, back = images.halves(img, split)
                    img = front if side == "front" else back
                self._write_cover(job, side, images.jpeg(img), "image/jpeg")
                added = True
            except (OSError, images.ImageError) as e:
                log.info("no %s cover from scan %s: %s", side, sc.id, e)
        if added and c.status == "sent":
            c.status = "not_sent"
        return added

    async def _scan_cover_quietly(self, job_id: int, scan_id: str) -> None:
        """A new front or back scan replaces that cover, when the disc can be sent."""
        try:
            await self.scan_covers(job_id, scan_id)
        except (ValueError, KeyError, images.ImageError) as e:
            log.debug("scan %s not used as a cover: %s", scan_id, e)

    def _inherit_scans(self, job: JobRecord, other: JobRecord) -> bool:
        """A disc joining a release takes the box's pictures from another disc in it,
        when it has none of its own."""
        if job.scans or not other.scans:
            return False
        src, dest = self._scans_dir(other), self._scans_dir(job)
        for sc in other.scans:
            p = src / f"{sc.id}.{EXTS[sc.content_type]}"
            if p.is_file():
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, dest / p.name)
                job.scans.append(sc.model_copy(update={"library_path": None}))
        self._inherit_reading(job, other)
        cur = self.db.get_job(job.id)
        job.scans_rev = max(job.scans_rev, cur.scans_rev if cur else 0) + 1  # the caller saves it
        return bool(job.scans)

    # --- the library ------------------------------------------------------------------

    def _scans_folder(self, job: JobRecord) -> Path | None:
        """``scans/`` in the film's folder (or backup's, or show's), once it's filed."""
        if job.state != JobState.done or not job.destination:
            return None
        dest = Path(job.destination)
        return dest / "scans" if dest.is_dir() else dest.parent / "scans"

    def _scan_prefix(self, job: JobRecord) -> str:
        """The disc's label (or number) before the names, when other discs file their
        scans in the same folder: every TV disc, and a film's box set."""
        many = job.media == MediaKind.tv
        c = job.contribution
        if not many and c and c.release_id:
            many = len(self.db.release_jobs(c.release_id)) > 1
        if not many:
            return ""
        number = (job.tv.disc_number if job.tv else None) or (c.disc_number if c else None)
        name = job.disc.label or (f"Disc {number}" if number else f"Job {job.id}")
        return library.safe_name(name) + " - "

    async def _file_scans_if_done(self, job_id: int) -> None:
        job = self.db.get_job(job_id)
        if job and job.state == JobState.done:
            await self.file_scans(job)

    async def file_scans(self, job: JobRecord) -> None:
        """Copy the pictures not filed yet into the library. A failure is a warning."""
        if not self.s.scans_to_library or not job.scans:
            return
        folder = self._scans_folder(job)
        todo = [sc for sc in job.scans if not sc.library_path]
        if not folder or not todo:
            return
        prefix = self._scan_prefix(job)
        src = self._scans_dir(job)
        try:
            filed = await asyncio.to_thread(file_into, folder, prefix, src, todo)
        except OSError as e:
            self._event(job, "warning", f"Could not copy the scans to the library: {e}")
            return
        job = self.db.get_job(job.id)
        for sc in job.scans:
            if sc.id in filed:
                sc.library_path = str(filed[sc.id])
        self._save_scans(job)
        if filed:
            n = len(filed)
            self._event(job, "finalise", f"Copied {n} scan{'s' if n != 1 else ''} to {folder}")

    # --- the network scanner -------------------------------------------------------

    def _escl(self) -> Escl:
        if not self.s.scanner_enabled or not self.s.scanner_url:
            raise ValueError("set up the scanner in Settings → Scanner and covers first")
        kw = {} if self.escl_busy_wait_s is None else {"busy_wait_s": self.escl_busy_wait_s}
        return Escl(self.s.scanner_url, transport=self.escl_transport, **kw)

    async def _caps(self, client: Escl, fresh: bool = False) -> Capabilities:
        if fresh or client.base not in self._scanner_caps:
            self._scanner_caps[client.base] = await client.capabilities()
        return self._scanner_caps[client.base]

    def _scanner_busy(self) -> bool:
        return bool(self._scanner_task and not self._scanner_task.done())

    async def scanner_info(self) -> ScannerInfo:
        info = ScannerInfo(enabled=self.s.scanner_enabled, url=self.s.scanner_url,
                           busy_job=self._scanner_job if self._scanner_busy() else None)
        if p := self.vision_provider():
            info.vision, info.vision_cloud = p.describe(), p.cloud
        if not (self.s.scanner_enabled and self.s.scanner_url):
            info.message = "The scanner isn't set up (Settings → Scanner and covers)"
            return info
        try:
            client = self._escl()
            caps = await self._caps(client, fresh=True)
            st = await client.status()
        except (ScannerError, ValueError) as e:
            info.message = str(e)
            return info
        info.ok, info.model, info.state, info.adf_state = True, caps.model, st.state, st.adf_state
        info.sources = caps.sources
        info.duplex = caps.duplex
        info.resolutions = (caps.platen or caps.feeder).resolutions if caps.sources else []
        return info

    async def scanner_scan(self, job_id: int, body: ScannerScanRequest) -> ScannerRun:
        """Start scanning for the job. It runs in the background, and the job's
        ``scanner`` says how it's going (then clears once the pages are in)."""
        job = self._job(job_id)
        client = self._escl()
        if self._scanner_busy():
            raise ValueError(f"the scanner is scanning for job {self._scanner_job}; wait for it to finish")
        source = body.source or self.s.scanner_source
        run = ScannerRun(state="scanning", kind=body.kind, source=source,
                         duplex=body.duplex and source == "feeder", started_at=now())
        job.scanner = run
        self._save_scans(job)
        self._scanner_job = job_id

        def show(r: ScannerRun | None) -> None:
            job = self.db.get_job(job_id)
            if job:
                job.scanner = r
                self._save_scans(job)

        async def keep(data: bytes) -> None:
            await self.add_scan(job_id, data, run.kind, "scanner")

        self._scanner_task = asyncio.create_task(
            self._run_scanner(f"job {job_id}", client, run, body.dpi or self.s.scanner_dpi, keep, show))
        return run

    async def _run_scanner(self, owner: str, client: Escl, run: ScannerRun, dpi: int,
                           keep: Callable[[bytes], Awaitable[None]],
                           show: Callable[[ScannerRun | None], None]) -> None:
        """Scan, giving each page to ``keep`` and the run's progress to ``show`` (None when
        it's over and the pages are in)."""
        def update(done: bool = False, **kw) -> None:
            for k, v in kw.items():
                setattr(run, k, v)
            show(None if done else run.model_copy())

        async def got_page(n: int, data: bytes) -> None:
            await keep(data)
            update(pages=n)

        def got_job(url: str) -> None:
            self._scanner_url = url

        try:
            caps = await self._caps(client)
            await client.scan(caps, run.source, dpi, run.duplex, on_page=got_page, on_job=got_job)
            update(done=True)
        except asyncio.CancelledError:
            update(done=True)
            raise
        except (ScannerError, ValueError, KeyError) as e:
            log.info("scan for %s failed: %s", owner, e)
            update(state="failed", message=str(e))
        except Exception as e:  # never leave the job saying it's scanning
            log.exception("scan for %s failed", owner)
            update(state="failed", message=f"The scan failed: {e}")
        finally:
            self._scanner_job = self._scanner_url = None

    async def cancel_scanner(self, job_id: int) -> None:
        """Stop a scan under way for the job, or clear one that failed."""
        if self._scanner_busy() and self._scanner_job == job_id:
            self._scanner_task.cancel()
            try:
                await self._scanner_task
            except asyncio.CancelledError:
                pass
            return
        job = self._job(job_id)
        if job.scanner:
            job.scanner = None
            self._save_scans(job)


def file_into(folder: Path, prefix: str, src: Path, scans: list[CoverScan]) -> dict[str, Path]:
    """Copy each scan's original into ``folder`` as ``<prefix><name>.<ext>``. A name
    taken by a different file gets " (2)", " (3)"...; the same file already there
    (same SHA-256) is taken as filed."""
    folder.mkdir(parents=True, exist_ok=True)
    filed: dict[str, Path] = {}
    for sc in scans:
        p = src / f"{sc.id}.{EXTS[sc.content_type]}"
        if not p.is_file():
            continue
        stem = prefix + LIBRARY_NAMES[sc.kind]
        if sc.kind in NUMBERED:
            stem += f"-{sc.page or 1:02d}"
        ext = EXTS[sc.content_type]
        n = 1
        while True:
            target = folder / (f"{stem}.{ext}" if n == 1 else f"{stem} ({n}).{ext}")
            if not target.exists():
                tmp = target.with_name(target.name + ".partial")
                shutil.copyfile(p, tmp)
                if target.exists():  # appeared meanwhile
                    tmp.unlink()
                    n += 1
                    continue
                library.replace(tmp, target)
                break
            if target.is_file() and _sha256(target) == sc.sha256:
                break
            n += 1
        filed[sc.id] = target
    return filed


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
