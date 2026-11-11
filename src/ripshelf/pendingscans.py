"""Scans of a box made before its disc goes in (mixed into ``jobs.Manager``).

A drive keeps the pictures added for "the next disc" until a disc goes into it: the new
job takes them, and they're read and used to identify the disc like any other scan.
They're kept in ``<data>/pending-scans`` and forgotten after a day if no disc comes.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import timedelta
from pathlib import Path

from pydantic import BaseModel

from . import library
from .db import now
from .models import PendingScan, PendingScans, ScanKind, ScannerRun, ScannerScanRequest, ScanSource
from .providers import release_info
from .scanner import images

log = logging.getLogger(__name__)

KEEP = timedelta(hours=24)
MAX_PENDING = 30
EXTS = release_info.COVER_TYPES


class _Index(BaseModel):
    scans: list[PendingScan] = []


class PendingMixin:
    def _init_pending(self) -> None:
        self._pending_runs: dict[str, ScannerRun] = {}
        self._pending_lock = asyncio.Lock()

    @property
    def _pending_dir(self) -> Path:
        return self.s.data_dir / "pending-scans"

    def _pending_index(self) -> _Index:
        try:
            idx = _Index.model_validate_json((self._pending_dir / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            idx = _Index()
        fresh = [p for p in idx.scans if p.expires_at > now()]
        for p in idx.scans:
            if p not in fresh:
                (self._pending_dir / f"{p.id}.{EXTS[p.content_type]}").unlink(missing_ok=True)
        idx.scans = fresh
        return idx

    def _save_pending(self, idx: _Index) -> None:
        self._pending_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._pending_dir / "index.json.tmp"
        tmp.write_text(idx.model_dump_json(), encoding="utf-8")
        library.replace(tmp, self._pending_dir / "index.json")

    def _drive_known(self, drive_id: str) -> None:
        if drive_id not in self.drives:
            raise KeyError(drive_id)

    def pending_scans(self, drive_id: str) -> PendingScans:
        self._drive_known(drive_id)
        return PendingScans(drive_id=drive_id, scans=[p for p in self._pending_index().scans if p.drive_id == drive_id],
                            scanner=self._pending_runs.get(drive_id))

    def pending_file(self, scan_id: str) -> tuple[Path, str] | None:
        p = next((x for x in self._pending_index().scans if x.id == scan_id), None)
        f = self._pending_dir / f"{p.id}.{EXTS[p.content_type]}" if p else None
        return (f, p.content_type) if f and f.is_file() else None

    async def add_pending(self, drive_id: str, data: bytes, kind: ScanKind, source: ScanSource) -> PendingScan:
        """Keep a picture for the next disc in this drive."""
        self._drive_known(drive_id)
        if len(data) > release_info.MAX_COVER_BYTES:
            raise ValueError("the picture is larger than 20 MB")
        ctype = release_info.sniff(data)
        if not ctype:
            raise ValueError("the picture must be a JPEG, PNG or WebP")
        trim = source != "camera" and self.s.scanner_trim

        def measure():
            img = images.open_image(data)
            return img.width, img.height, images.trim_box(img) if trim else None

        width, height, crop = await asyncio.to_thread(measure)
        async with self._pending_lock:
            idx = self._pending_index()
            if sum(p.drive_id == drive_id for p in idx.scans) >= MAX_PENDING:
                raise ValueError(f"a drive keeps at most {MAX_PENDING} pictures for its next disc")
            page = max((p.page or 0 for p in idx.scans if p.drive_id == drive_id and p.kind == kind), default=0) + 1 \
                if kind in ("insert", "other") else None
            p = PendingScan(id=uuid.uuid4().hex[:12], drive_id=drive_id, kind=kind, source=source, page=page,
                            created_at=now(), expires_at=now() + KEEP, content_type=ctype, width=width,
                            height=height, sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), crop=crop)
            self._pending_dir.mkdir(parents=True, exist_ok=True)
            (self._pending_dir / f"{p.id}.{EXTS[ctype]}").write_bytes(data)
            idx.scans.append(p)
            self._save_pending(idx)
        return p

    async def delete_pending(self, drive_id: str, scan_id: str) -> None:
        async with self._pending_lock:
            idx = self._pending_index()
            p = next((x for x in idx.scans if x.id == scan_id and x.drive_id == drive_id), None)
            if not p:
                raise KeyError(scan_id)
            (self._pending_dir / f"{p.id}.{EXTS[p.content_type]}").unlink(missing_ok=True)
            idx.scans.remove(p)
            self._save_pending(idx)

    async def attach_pending(self, job_id: int) -> int:
        """Give a new job the pictures waiting for its drive's next disc."""
        job = self.db.get_job(job_id)
        if not job:
            return 0
        async with self._pending_lock:
            idx = self._pending_index()
            mine = [p for p in idx.scans if p.drive_id == job.drive_id]
            idx.scans = [p for p in idx.scans if p.drive_id != job.drive_id]
            self._save_pending(idx)
        n = 0
        for p in mine:
            f = self._pending_dir / f"{p.id}.{EXTS[p.content_type]}"
            try:
                await self.add_scan(job_id, f.read_bytes(), p.kind, p.source, p.page)
                n += 1
            except (OSError, ValueError, KeyError) as e:
                log.info("could not give job %s the waiting scan %s: %s", job_id, p.id, e)
            f.unlink(missing_ok=True)
        if n:
            self._event(self.db.get_job(job_id), "info",
                        f"Took the {n} picture{'s' if n != 1 else ''} of the box added before the disc went in")
        return n

    async def scan_pending(self, drive_id: str, body: ScannerScanRequest) -> ScannerRun:
        """Scan from the network scanner for the drive's next disc."""
        self._drive_known(drive_id)
        client = self._escl()
        if self._scanner_busy():
            raise ValueError("the scanner is busy; wait for it to finish")
        source = body.source or self.s.scanner_source
        run = ScannerRun(state="scanning", kind=body.kind, source=source,
                         duplex=body.duplex and source == "feeder", started_at=now())
        self._pending_runs[drive_id] = run

        def show(r: ScannerRun | None) -> None:
            if r is None:
                self._pending_runs.pop(drive_id, None)
            else:
                self._pending_runs[drive_id] = r

        async def keep(data: bytes) -> None:
            await self.add_pending(drive_id, data, run.kind, "scanner")

        self._scanner_task = asyncio.create_task(
            self._run_scanner(f"drive {drive_id}", client, run, body.dpi or self.s.scanner_dpi, keep, show))
        return run

    def clear_pending_run(self, drive_id: str) -> None:
        if self._scanner_busy() and drive_id in self._pending_runs:
            self._scanner_task.cancel()
        self._pending_runs.pop(drive_id, None)
