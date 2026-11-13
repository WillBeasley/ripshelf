"""The covers shelf: case sheets scanned ahead of their discs (mixed into
``jobs.Manager``).

A stack of sheets goes through the scanner's feeder (or is uploaded, pictures or PDFs)
and each sheet becomes a cover on the shelf: its outside (back, spine and front) and
its inside (``scanner.stack`` pairs the pages, drops blank sides and tells the outside
from the inside). Each cover is read in the background, one at a time, like a job's
scans: Tesseract and zbar, the vision model as Settings say, and the barcode lookups.

When its disc comes, the cover is used for that job (its sides become the job's scans,
read and filed like any other) or kept for a drive's next disc, and leaves the shelf.
A job in review is offered the covers that look like it (``suggestions``).

The shelf lives in ``<data>/covers``: an ``index.json`` and the pictures, kept until
used or deleted. A two-pass stack's first pass waits in ``covers/stack`` for the second.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from . import library, providers, scanjobs
from .db import now
from .identify import barcode, catalogue
from .identify.label import parse_label
from .identify.scoring import title_similarity
from .matchjobs import product_name
from .models import (
    DiscKind,
    JobState,
    Listing,
    MediaKind,
    PendingScans,
    ScannerRun,
    ScanSource,
    ScanText,
    ShelfCover,
    ShelfReading,
    ShelfScanRequest,
    ShelfSide,
    ShelfSideName,
    ShelfStack,
    ShelfSuggestion,
    ShelfUploadResult,
    ShelfView,
    StackMode,
    StackPage,
    sentence,
)
from .providers import release_info
from .scanner import images, ocr, stack, vision
from .scanner import listing as parse
from .visionjobs import TRANSCODE_WAIT_S

if TYPE_CHECKING:
    from .db import JobRecord

log = logging.getLogger(__name__)

MAX_COVERS = 500
MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # a PDF of a whole stack
EXTS = release_info.COVER_TYPES
KINDS = {"outside": "outside_full", "inside": "inside_full"}
SUGGEST_AT = 0.7  # a cover scoring less isn't offered
SUGGESTIONS = 3
FORMAT_WORDS = [(re.compile(r"(?i)\b(4k|ultra\s*hd|uhd)\b"), DiscKind.uhd),
                (re.compile(r"(?i)\bblu-?\s?ray\b"), DiscKind.bluray),
                (re.compile(r"(?i)\bdvd\b"), DiscKind.dvd)]


class _Index(BaseModel):
    covers: list[ShelfCover] = []
    stack: ShelfStack | None = None


class _Texts(BaseModel):
    key: str
    texts: list[ScanText]


class ShelfMixin:
    def _init_shelf(self) -> None:
        self._shelf_lock = asyncio.Lock()
        self._shelf_task: asyncio.Task | None = None  # reading the covers
        self._shelf_again = False
        self._shelf_vision_asked: set[str] = set()
        self._stack_task: asyncio.Task | None = None  # scanning a stack

    @property
    def _shelf_dir(self) -> Path:
        return self.s.data_dir / "covers"

    def _shelf_index(self) -> _Index:
        try:
            idx = _Index.model_validate_json((self._shelf_dir / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            idx = _Index()
        if idx.stack and idx.stack.state == "scanning" and not self._stack_scanning():
            idx.stack.state, idx.stack.message = "failed", "The scan stopped (ripshelf restarted)"
            if idx.stack.mode == "two_pass" and idx.stack.first and idx.stack.pass_no == 1:
                idx.stack.message += "; scan the first pass again"
        return idx

    def _save_shelf(self, idx: _Index) -> None:
        self._shelf_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._shelf_dir / "index.json.tmp"
        tmp.write_text(idx.model_dump_json(), encoding="utf-8")
        library.replace(tmp, self._shelf_dir / "index.json")

    def _side_path(self, cover_id: str, side: ShelfSideName, s: ShelfSide) -> Path:
        return self._shelf_dir / f"{cover_id}.{side}.{EXTS[s.content_type]}"

    def _stack_path(self, p: StackPage) -> Path:
        return self._shelf_dir / "stack" / f"{p.id}.{EXTS[p.content_type]}"

    def _forget_shelf_thumbs(self, cover_id: str) -> None:
        for f in self._shelf_dir.glob(f"{cover_id}.*.*.jpg"):
            f.unlink(missing_ok=True)

    # --- looking ---------------------------------------------------------------------------

    def shelf(self, query: str | None = None) -> ShelfView:
        covers = self._shelf_index().covers
        if query and query.strip():
            covers = [c for c in covers if _matches(c, query)]
        return ShelfView(covers=sorted(covers, key=lambda c: c.created_at, reverse=True),
                         stack=self._shelf_index().stack, limit=MAX_COVERS)

    def shelf_cover(self, cover_id: str) -> ShelfCover:
        c = next((c for c in self._shelf_index().covers if c.id == cover_id), None)
        if not c:
            raise KeyError(cover_id)
        return c

    def shelf_file(self, cover_id: str, side: ShelfSideName) -> tuple[Path, str] | None:
        c = next((c for c in self._shelf_index().covers if c.id == cover_id), None)
        s = getattr(c, side) if c else None
        p = self._side_path(cover_id, side, s) if s else None
        return (p, s.content_type) if p and p.is_file() else None

    async def shelf_thumb(self, cover_id: str, side: ShelfSideName, large: bool = False) -> Path | None:
        """A JPEG of the side, trimmed and turned: a thumbnail, or a large one to look at."""
        found = self.shelf_file(cover_id, side)
        if not found:
            return None
        s = getattr(self.shelf_cover(cover_id), side)
        out = self._shelf_dir / f"{cover_id}.{side}.{'view' if large else 'thumb'}.jpg"
        if not out.is_file():
            def write():
                img = images.render(found[0].read_bytes(), s.crop, s.rotate)
                out.write_bytes(images.thumbnail(img, images.VIEW_PX if large else images.THUMB_PX))
            await asyncio.to_thread(write)
        return out

    def stack_page_file(self, page_id: str) -> tuple[Path, str] | None:
        st = self._shelf_index().stack
        p = next((p for p in [*st.first, *st.second] if p.id == page_id), None) if st else None
        f = self._stack_path(p) if p else None
        return (f, p.content_type) if f and f.is_file() else None

    # --- adding ----------------------------------------------------------------------------

    async def _make_cover(self, source: ScanSource, a: bytes | None, b: bytes | None) -> tuple[ShelfCover | None, str | None]:
        """A cover from a sheet's two sides as they came (either may be None). Returns it,
        or None and why not (both sides blank, already on the shelf, the shelf full)."""
        trim = self.s.scanner_trim

        def measure(data: bytes | None):
            if data is None:
                return None
            img, box, w, h = stack.render_trimmed(data, trim)
            return img, box, w, h, stack.look(img)

        try:
            ma, mb = await asyncio.to_thread(measure, a), await asyncio.to_thread(measure, b)
        except images.ImageError as e:
            return None, f"a page couldn't be read: {e}"
        sides = [(d, m) for d, m in ((a, ma), (b, mb)) if d is not None and not m[4].blank]
        blanks = sum(1 for d, m in ((a, ma), (b, mb)) if d is not None and m[4].blank)
        if not sides:
            return None, "a blank sheet was left out"
        if len(sides) == 2 and not stack.outside_first(sides[0][1][4], sides[1][1][4]):
            sides.reverse()
        made: dict[str, ShelfSide] = {}
        for name, (data, (img, box, w, h, _)) in zip(("outside", "inside"), sides, strict=False):
            made[name] = ShelfSide(content_type=release_info.sniff(data), width=w, height=h,
                                   sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), crop=box,
                                   split=await asyncio.to_thread(images.find_spine, img) if name == "outside" else None)
        note = None
        if blanks:
            note = "The other side was blank, so it was left out"
        elif len(sides) == 2 and sides[0][0] is not a:
            note = "The second side scanned looked like the outside, so the sides were swapped"
        async with self._shelf_lock:
            idx = self._shelf_index()
            # The same outside is the same sheet; an inside alone may be a plain one many
            # sheets share.
            if any(c.outside and c.outside.sha256 == made["outside"].sha256 for c in idx.covers):
                return None, "the same sheet is on the shelf already"
            if len(idx.covers) >= MAX_COVERS:
                return None, f"the shelf is full ({MAX_COVERS} covers); use or delete some first"
            cover = ShelfCover(id=uuid.uuid4().hex[:12], created_at=now(), source=source, note=note, **made)
            self._shelf_dir.mkdir(parents=True, exist_ok=True)
            for name, (data, _) in zip(("outside", "inside"), sides, strict=False):
                self._side_path(cover.id, name, made[name]).write_bytes(data)
            idx.covers.append(cover)
            self._save_shelf(idx)
        self.read_shelf()
        return cover, None

    async def _make_covers(self, source: ScanSource, pages: list[bytes], sheets: list[stack.Sheet],
                           out: ShelfUploadResult) -> None:
        for sh in sheets:
            cover, why = await self._make_cover(source, pages[sh.a] if sh.a is not None else None,
                                                pages[sh.b] if sh.b is not None else None)
            if cover:
                out.covers.append(cover)
            elif why:
                out.notes.append(why[0].upper() + why[1:])

    async def add_shelf_upload(self, files: list[bytes], insides: list[bytes], both_sides: bool = True,
                               layout: str = "alternate", reverse_insides: bool = True) -> ShelfUploadResult:
        """Covers from uploaded pictures and PDFs. With both sides, the pages pair as
        ``layout`` says: one after the other (a duplex scan), or as two passes (the
        first half, then the second half reversed). ``insides`` is a second pass of its
        own. Two passes that don't pair up are held, to drop the extra pages."""
        pages = await self._expand(files)
        second = await self._expand(insides)
        out = ShelfUploadResult()
        if not pages and not second:
            raise ValueError("no pages were uploaded")
        if second or (both_sides and layout == "passes"):
            if not second:
                n, _ = stack.split_passes(len(pages))
                pages, second = pages[:n], pages[n:]
            elif not reverse_insides:
                second = second[::-1]  # held as a turned stack comes out
            if len(pages) != len(second):
                out.stack = await self._hold_upload(pages, second)
                return out
            await self._make_covers("upload", pages + second, stack.pair_passes(len(pages), len(second)), out)
        elif both_sides:
            if len(pages) % 2:
                raise ValueError(f"{len(pages)} pages can't be paired into both sides of each sheet; untick "
                                 "\"both sides\", or add the missing page")
            await self._make_covers("upload", pages, stack.pair_duplex(len(pages)), out)
        else:
            await self._make_covers("upload", pages, stack.pair_one_side(len(pages)), out)
        return out

    async def _expand(self, files: list[bytes]) -> list[bytes]:
        """Each file's pages: a picture is one, a PDF one per page."""
        out: list[bytes] = []
        for data in files:
            if stack.is_pdf(data):
                out += await asyncio.to_thread(stack.pdf_pages, data)
            else:
                if len(data) > release_info.MAX_COVER_BYTES:
                    raise ValueError("a picture is larger than 20 MB")
                out.append(await asyncio.to_thread(stack.to_jpeg_if_needed, data))
        return out

    async def _hold_upload(self, first: list[bytes], second: list[bytes]) -> ShelfStack:
        async with self._shelf_lock:
            idx = self._shelf_index()
            if idx.stack and idx.stack.state != "failed":
                raise ValueError("a stack is waiting already; finish or cancel it first")
            self._drop_stack_files(idx.stack)
            idx.stack = ShelfStack(mode="two_pass", source="upload", state="mismatch", pass_no=2, started_at=now())
            self._save_shelf(idx)
        for n, group in ((1, first), (2, second)):
            for data in group:
                await self._hold_page(n, data)
        async with self._shelf_lock:
            idx = self._shelf_index()
            idx.stack.state = "mismatch"
            idx.stack.message = _mismatch(idx.stack)
            self._save_shelf(idx)
            return idx.stack

    async def _hold_page(self, pass_no: int, data: bytes) -> None:
        trim = self.s.scanner_trim
        blank = await asyncio.to_thread(lambda: stack.is_blank(stack.render_trimmed(data, trim)[0]))
        p = StackPage(id=uuid.uuid4().hex[:12], content_type=release_info.sniff(data) or "image/jpeg",
                      size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), blank=blank)
        f = self._stack_path(p)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
        async with self._shelf_lock:
            idx = self._shelf_index()
            if idx.stack:
                (idx.stack.first if pass_no == 1 else idx.stack.second).append(p)
                idx.stack.pages += 1
                self._save_shelf(idx)

    def _drop_stack_files(self, st: ShelfStack | None, which: tuple[str, ...] = ("first", "second")) -> None:
        for name in which:
            for p in getattr(st, name) if st else []:
                self._stack_path(p).unlink(missing_ok=True)

    # --- changing and removing ----------------------------------------------------------------

    async def delete_shelf_cover(self, cover_id: str) -> None:
        async with self._shelf_lock:
            idx = self._shelf_index()
            c = next((c for c in idx.covers if c.id == cover_id), None)
            if not c:
                raise KeyError(cover_id)
            idx.covers.remove(c)
            self._save_shelf(idx)
        self._drop_cover_files(c)

    def _drop_cover_files(self, c: ShelfCover) -> None:
        for name in ("outside", "inside"):
            if s := getattr(c, name):
                self._side_path(c.id, name, s).unlink(missing_ok=True)
        self._texts_path(c.id).unlink(missing_ok=True)
        self._forget_shelf_thumbs(c.id)

    async def swap_shelf_sides(self, cover_id: str) -> ShelfCover:
        """The outside was taken for the inside: swap them, and read it again."""
        async with self._shelf_lock:
            idx = self._shelf_index()
            c = next((c for c in idx.covers if c.id == cover_id), None)
            if not c:
                raise KeyError(cover_id)
            files = {name: self._side_path(c.id, name, s) for name in ("outside", "inside")
                     if (s := getattr(c, name))}
            tmp = {name: f.with_name(f.name + ".swap") for name, f in files.items()}
            for name, f in files.items():
                f.rename(tmp[name])
            c.outside, c.inside = c.inside, c.outside
            for old, t in tmp.items():
                new = "inside" if old == "outside" else "outside"
                t.rename(self._side_path(c.id, new, getattr(c, new)))
            if c.inside:
                c.inside.split = None
            c.reading = ShelfReading()
            c.note = None
            self._forget_shelf_thumbs(c.id)
            self._save_shelf(idx)
        if c.outside:
            data = self._side_path(c.id, "outside", c.outside).read_bytes()
            s = c.outside
            split = await asyncio.to_thread(lambda: images.find_spine(images.render(data, s.crop, s.rotate)))
            await self._update_cover(cover_id, lambda x: setattr(x.outside, "split", split) if x.outside else None)
        self.read_shelf()
        return self.shelf_cover(cover_id)

    async def read_shelf_again(self, cover_id: str) -> ShelfCover:
        await self._update_cover(cover_id, lambda c: setattr(c, "reading", ShelfReading()))
        self.read_shelf()
        return self.shelf_cover(cover_id)

    async def _update_cover(self, cover_id: str, change: Callable[[ShelfCover], object]) -> ShelfCover | None:
        async with self._shelf_lock:
            idx = self._shelf_index()
            c = next((c for c in idx.covers if c.id == cover_id), None)
            if c:
                change(c)
                self._save_shelf(idx)
            return c

    # --- using a cover -------------------------------------------------------------------------

    async def _take_cover(self, cover_id: str) -> tuple[ShelfCover, dict[str, bytes]]:
        """Take a cover off the shelf, with its pictures."""
        async with self._shelf_lock:
            idx = self._shelf_index()
            c = next((c for c in idx.covers if c.id == cover_id), None)
            if not c:
                raise KeyError(cover_id)
            data = {name: self._side_path(c.id, name, s).read_bytes() for name in ("outside", "inside")
                    if (s := getattr(c, name))}
            idx.covers.remove(c)
            self._save_shelf(idx)
        return c, data

    async def _put_back(self, c: ShelfCover) -> None:
        async with self._shelf_lock:
            idx = self._shelf_index()
            idx.covers.append(c)
            self._save_shelf(idx)

    async def use_shelf_cover(self, cover_id: str, job_id: int) -> JobRecord:
        """The cover's sides become the job's scans (read, used to identify the disc,
        and filed with it like any other), and it leaves the shelf."""
        job = self._job(job_id)
        self.shelf_cover(cover_id)
        if len(job.scans) + 2 > scanjobs.MAX_SCANS:
            raise ValueError(f"a job keeps at most {scanjobs.MAX_SCANS} pictures; remove some first")
        c, data = await self._take_cover(cover_id)
        source = "upload" if c.source == "camera" else c.source
        try:
            for name, d in data.items():
                await self.add_scan(job_id, d, KINDS[name], source)
        except (OSError, ValueError, KeyError):
            await self._put_back(c)
            raise
        self._drop_cover_files(c)
        title = c.reading.title or c.reading.barcode_name
        self._event(self._job(job_id), "info", "Took the cover from the shelf" + (f" (“{title}”)" if title else ""))
        return self._job(job_id)

    async def shelf_cover_for_drive(self, cover_id: str, drive_id: str) -> PendingScans:
        """Keep the cover's sides for the drive's next disc instead."""
        self._drive_known(drive_id)
        self.shelf_cover(cover_id)
        c, data = await self._take_cover(cover_id)
        source = "upload" if c.source == "camera" else c.source
        added = []
        try:
            for name, d in data.items():
                added.append(await self.add_pending(drive_id, d, KINDS[name], source))
        except (OSError, ValueError, KeyError):
            for p in added:
                await self.delete_pending(drive_id, p.id)
            await self._put_back(c)
            raise
        self._drop_cover_files(c)
        return self.pending_scans(drive_id)

    # --- the scanner ---------------------------------------------------------------------------

    def _stack_scanning(self) -> bool:
        return bool(self._stack_task and not self._stack_task.done())

    async def scan_stack(self, body: ShelfScanRequest) -> ShelfStack:
        """Scan a stack of sheets from the feeder. Both sides at once and one side make
        covers as the pages come; two passes holds the first until the second."""
        client = self._escl()
        if self._scanner_busy():
            raise ValueError(self._scanner_busy_message())
        async with self._shelf_lock:
            idx = self._shelf_index()
            if idx.stack and idx.stack.state in ("flip", "mismatch"):
                raise ValueError("a stack is waiting for its other side; finish or cancel it first")
            self._drop_stack_files(idx.stack)
            idx.stack = ShelfStack(mode=body.mode, source="scanner", state="scanning", started_at=now())
            self._save_shelf(idx)
            st = idx.stack
        self._start_stack_scan(client, body.mode, 1, body.dpi)
        return st

    async def scan_second_pass(self, dpi: int | None = None) -> ShelfStack:
        """Scan the turned-over stack (again, if its pages didn't pair up)."""
        client = self._escl()
        if self._scanner_busy():
            raise ValueError(self._scanner_busy_message())
        async with self._shelf_lock:
            idx = self._shelf_index()
            st = idx.stack
            if not st or st.mode != "two_pass" or not st.first or st.source != "scanner" or not (
                    st.state in ("flip", "mismatch") or st.state == "failed" and st.pass_no == 2):
                raise ValueError("there's no first pass waiting for its other side")
            self._drop_stack_files(st, ("second",))
            st.second, st.pages, st.pass_no, st.state, st.message = [], 0, 2, "scanning", None
            self._save_shelf(idx)
        self._start_stack_scan(client, "two_pass", 2, dpi)
        return st

    def _start_stack_scan(self, client, mode: StackMode, pass_no: int, dpi: int | None) -> None:
        run = ScannerRun(state="scanning", kind="outside_full", source="feeder", duplex=mode == "duplex",
                         started_at=now())
        waiting: list[bytes] = []  # a duplex sheet's first side, until its second comes

        async def keep(data: bytes) -> None:
            if mode == "two_pass":
                await self._hold_page(pass_no, data)
                return
            if mode == "duplex" and not waiting:
                waiting.append(data)
                return
            a = waiting.pop() if waiting else data
            await self._stack_cover(a, data if a is not data else None)

        def show(r: ScannerRun | None) -> None:
            if r and r.state == "failed":
                failed.append(r.message or "The scan failed")

        failed: list[str] = []

        async def go() -> None:
            await self._run_scanner("the covers shelf", client, run, dpi or self.s.scanner_dpi, keep, show)
            if waiting:  # an odd last page: a sheet with one side
                await self._stack_cover(waiting.pop(), None)
            await self._stack_done(pass_no, failed[0] if failed else None)

        self._scanner_task = self._stack_task = asyncio.create_task(go())

    async def _stack_cover(self, a: bytes, b: bytes | None) -> None:
        cover, why = await self._make_cover("scanner", a, b)
        async with self._shelf_lock:
            idx = self._shelf_index()
            if idx.stack:
                idx.stack.pages += 1 if b is None else 2
                idx.stack.covers += 1 if cover else 0
                if why:
                    idx.stack.message = why[0].upper() + why[1:]
                self._save_shelf(idx)

    async def _stack_done(self, pass_no: int, failed: str | None) -> None:
        """The scanner stopped: say what's next."""
        async with self._shelf_lock:
            idx = self._shelf_index()
            st = idx.stack
            if not st:
                return
            if failed:
                st.state, st.message = "failed", failed
                if st.mode == "two_pass" and pass_no == 2:
                    st.message += "; scan the second pass again"
            elif st.mode != "two_pass":
                idx.stack = None  # all in
            elif pass_no == 1:
                st.state, st.message = "flip", "Now turn the stack over and scan the other side"
            elif len(st.first) != len(st.second):
                st.state, st.message = "mismatch", _mismatch(st)
            else:
                st.state = "scanning"
            self._save_shelf(idx)
            pair = st.mode == "two_pass" and pass_no == 2 and not failed and len(st.first) == len(st.second)
        if pair:
            await self.pair_stack(scanned=True)

    async def pair_stack(self, scanned: bool = False) -> ShelfUploadResult:
        """Make the held passes into covers, once their pages pair up."""
        async with self._shelf_lock:
            idx = self._shelf_index()
            st = idx.stack
            if not st or st.mode != "two_pass" or not st.first:
                raise ValueError("there's no stack waiting")
            if self._stack_scanning() and not scanned:
                raise ValueError("the stack is still being scanned")
            if len(st.first) != len(st.second):
                raise ValueError(_mismatch(st))
            held = [*st.first, *st.second]
            idx.stack = None
            self._save_shelf(idx)
        pages = [self._stack_path(p).read_bytes() for p in held]
        out = ShelfUploadResult()
        await self._make_covers(st.source, pages, stack.pair_passes(len(st.first), len(st.second)), out)
        self._drop_stack_files(st)
        return out

    async def drop_stack_page(self, page_id: str) -> ShelfStack:
        """Leave out a page of a held stack (a sheet fed twice, a blank page)."""
        async with self._shelf_lock:
            idx = self._shelf_index()
            st = idx.stack
            p = next((p for p in [*st.first, *st.second] if p.id == page_id), None) if st else None
            if not p:
                raise KeyError(page_id)
            if st.state == "scanning":
                raise ValueError("the stack is still being scanned")
            (st.first if p in st.first else st.second).remove(p)
            self._stack_path(p).unlink(missing_ok=True)
            if st.state == "mismatch":
                st.message = _mismatch(st) if len(st.first) != len(st.second) else \
                    "The passes pair up now: make the covers"
            self._save_shelf(idx)
            return st

    async def cancel_stack(self) -> None:
        """Stop a stack being scanned, or let go of one held: its held pages go (covers
        already made stay on the shelf)."""
        if self._stack_scanning():
            self._stack_task.cancel()
            try:
                await self._stack_task
            except asyncio.CancelledError:
                pass
        async with self._shelf_lock:
            idx = self._shelf_index()
            self._drop_stack_files(idx.stack)
            idx.stack = None
            self._save_shelf(idx)

    # --- reading ---------------------------------------------------------------------------------

    def _texts_path(self, cover_id: str) -> Path:
        return self._shelf_dir / f"{cover_id}.text.json"

    @staticmethod
    def _texts_key(c: ShelfCover) -> str:
        """What the text read depends on: the sides' files and trims (not the turn, which
        the reading itself finds)."""
        return "|".join(f"{n}:{s.sha256}:{s.crop}" for n in ("outside", "inside") if (s := getattr(c, n)))

    def _saved_texts(self, c: ShelfCover) -> list[ScanText] | None:
        try:
            saved = _Texts.model_validate_json(self._texts_path(c.id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return saved.texts if saved.key == self._texts_key(c) else None

    def read_shelf(self) -> None:
        """Read the covers waiting to be read, one at a time, in the background."""
        if self._shelf_task and not self._shelf_task.done():
            self._shelf_again = True
            return
        self._shelf_task = asyncio.create_task(self._read_shelf_loop())

    async def ask_shelf_vision(self, cover_id: str) -> ShelfCover:
        """Read the cover with the vision model now (its button), whatever Settings say
        about when it runs."""
        self.shelf_cover(cover_id)
        if not self.vision_provider():
            raise ValueError("set up a vision model in Settings → Scanner and covers first")
        self._shelf_vision_asked.add(cover_id)
        c = await self._update_cover(cover_id, lambda c: setattr(c.reading, "vision_state", "queued"))
        self.read_shelf()
        return c

    async def _read_shelf_loop(self) -> None:
        try:
            while True:
                self._shelf_again = False
                todo = [c.id for c in self._shelf_index().covers
                        if c.reading.state in ("waiting", "reading") or c.id in self._shelf_vision_asked]
                for cover_id in todo:
                    if not await self._read_cover(cover_id):
                        return
                if not self._shelf_again:
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("reading the covers on the shelf failed")
        finally:
            self._shelf_task = None

    async def _read_cover(self, cover_id: str) -> bool:
        """Read one cover: its text (unless read already), the vision model's reading
        when Settings say or it was asked for, and what its barcode names. False when
        reading can't go on (no Tesseract)."""
        asked = cover_id in self._shelf_vision_asked
        self._shelf_vision_asked.discard(cover_id)
        c = next((c for c in self._shelf_index().covers if c.id == cover_id), None)
        if not c:
            return True
        texts = self._saved_texts(c)
        if texts is None:
            if not self.s.scan_read_text:
                await self._set_reading(cover_id, ShelfReading(
                    state="unavailable", message="Reading the text on scans is off (Settings → Scanner and covers)"))
                return True
            texts = await self._read_text(cover_id)
            if texts is None:
                return False
            if not texts:
                return True
        found = parse.build(texts)
        provider = self.vision_provider()
        wanted = asked or self.s.vision_mode == "every" or (
            self.s.vision_mode == "struggles" and not (found.details.title and found.details.year))
        used, vision_state, vision_message = False, None, None
        if provider and wanted and (c := self.shelf_cover(cover_id)).outside:
            seen, vision_message = await self._shelf_vision(c, provider)
            vision_state = "done" if seen is not None else "failed"
            if seen is not None:
                found = parse.merge(found, [(f"{cover_id}.outside", seen)], texts)
                used = True
        d = found.details
        reading = ShelfReading(state="done", title=d.title, year=d.year, barcode=d.barcode,
                               catalogue_number=d.catalogue_number, read_at=now(), vision=used,
                               vision_state=vision_state, vision_message=vision_message,
                               format=_format(found, texts))
        if not used and (old := self.shelf_cover(cover_id).reading).vision and not wanted:
            reading.vision, reading.vision_state = old.vision, old.vision_state
        if d.barcode:
            await self._shelf_barcode(reading, d.barcode)
        await self._set_reading(cover_id, reading)
        return True

    async def _set_reading(self, cover_id: str, reading: ShelfReading) -> None:
        await self._update_cover(cover_id, lambda c: setattr(c, "reading", reading))

    async def _read_text(self, cover_id: str) -> list[ScanText] | None:
        """Tesseract and zbar on each side, kept beside the cover. None when there's no
        Tesseract; an empty list when the cover went or a side couldn't be read."""
        reader = self._reader()
        try:
            engine = await reader.version()
        except ocr.OcrUnavailable as e:
            await self._set_reading(cover_id, ShelfReading(state="unavailable", message=str(e)))
            return None
        texts: list[ScanText] = []
        async with self._read_lock:
            c = await self._update_cover(cover_id, lambda c: setattr(c.reading, "state", "reading"))
            if not c:
                return []
            key = self._texts_key(c)
            for name in ("outside", "inside"):
                s: ShelfSide | None = getattr(c, name)
                if not s:
                    continue
                try:
                    data = self._side_path(c.id, name, s).read_bytes()
                    img = await asyncio.to_thread(images.render, data, s.crop, s.rotate)
                    got = await ocr.read_picture(reader, img, KINDS[name], s.split)
                except (OSError, images.ImageError) as e:
                    await self._set_reading(cover_id, ShelfReading(state="failed",
                                                                   message=f"Couldn't read the {name}: {e}"))
                    return []
                texts.append(ScanText(scan_id=f"{c.id}.{name}", key=key, read_at=now(), engine=engine,
                                      turned=got.turned, lines=got.lines, more=got.more, barcodes=got.barcodes))
                if got.turned or name == "outside":
                    def turn(x: ShelfCover, name=name, got=got, sha=s.sha256) -> None:
                        side = getattr(x, name)
                        if side and side.sha256 == sha:
                            side.rotate = (side.rotate + got.turned) % 360
                            if name == "outside" and (side.split is None or side.split.auto):
                                side.split = got.split
                    await self._update_cover(cover_id, turn)
                    self._forget_shelf_thumbs(cover_id)
        if self._texts_key(self.shelf_cover(cover_id)) != key:
            return []  # swapped meanwhile: it's read again
        self._texts_path(cover_id).write_text(_Texts(key=key, texts=texts).model_dump_json(), encoding="utf-8")
        return texts

    async def _shelf_vision(self, c: ShelfCover, provider: vision.Provider) -> tuple[Listing | None, str]:
        """The vision model's reading of the outside, taking turns with the jobs' readings."""
        s = c.outside
        async with self._vision_lock:
            while not provider.cloud and self._transcoders:
                await self._update_cover(c.id, lambda x: setattr(x.reading, "vision_state", "queued"))
                await asyncio.sleep(TRANSCODE_WAIT_S)
            await self._update_cover(c.id, lambda x: setattr(x.reading, "vision_state", "reading"))
            try:
                data = self._side_path(c.id, "outside", s).read_bytes()
                img = await asyncio.to_thread(images.render, data, s.crop, s.rotate)
                listing, secs = await vision.read_listing(provider, img)
            except (vision.VisionError, OSError, images.ImageError) as e:
                log.info("the vision model couldn't read shelf cover %s: %s", c.id, e)
                return None, sentence(str(e))
        return listing, f"{provider.model} read it in {secs:.0f} s"

    async def _shelf_barcode(self, reading: ShelfReading, code: str) -> None:
        thediscdb = any("thediscdb" in providers.enabled(self.s, k) for k in ("movie", "tv"))
        try:
            answer = await barcode.name(self.s, code, reading.format.value if reading.format else None,
                                        thediscdb=thediscdb)
        except Exception as e:  # a lookup is a nicety; the reading stands without it
            log.info("barcode %s on the shelf: %s", code, e)
            return
        if answer.found:
            reading.barcode_name, reading.barcode_source = answer.found.title, answer.found.source

    # --- suggestions -----------------------------------------------------------------------------

    def shelf_suggestions(self, job_id: int) -> list[ShelfSuggestion]:
        """Covers on the shelf that look like the job's disc, best first. Nothing is
        used without the user."""
        job = self._job(job_id)
        codes, names = job_clues(job)
        out = []
        for c in self._shelf_index().covers:
            score, reason = score_cover(c, codes, names)
            if score >= SUGGEST_AT:
                out.append(ShelfSuggestion(cover=c, score=round(score, 3), reason=reason))
        return sorted(out, key=lambda s: -s.score)[:SUGGESTIONS]

    def shelf_picks(self, job_id: int) -> list[ShelfSuggestion]:
        """Every cover, scored against the job (for picking one by hand), best first."""
        job = self._job(job_id)
        codes, names = job_clues(job)
        out = [ShelfSuggestion(cover=c, score=round(s, 3), reason=r)
               for c in self._shelf_index().covers for s, r in [score_cover(c, codes, names)]]
        return sorted(out, key=lambda s: (-s.score, -s.cover.created_at.timestamp()))


# --- pure helpers -------------------------------------------------------------------------------


def _mismatch(st: ShelfStack) -> str:
    a, b = len(st.first), len(st.second)
    return (f"The first pass has {a} page{'s' if a != 1 else ''} and the second {b}, so they don't pair up. "
            "Leave out the extra pages (a sheet fed twice, or two fed together), or scan the second pass again.")


def _format(found: Listing, texts: list[ScanText]) -> DiscKind | None:
    if any("4K" in v or "UHD" in v for v in found.details.video):
        return DiscKind.uhd
    words = " ".join(ln.text for t in texts for ln in t.lines if ln.region in ("front", "spine", "page"))
    for pat, kind in FORMAT_WORDS:
        if pat.search(words):
            return kind
    return None


def _matches(c: ShelfCover, query: str) -> bool:
    r = c.reading
    hay = " ".join(str(x) for x in (r.title, r.year, r.barcode, r.barcode_name, r.catalogue_number,
                                    r.format.value if r.format else None) if x).lower()
    return all(w in hay for w in query.lower().split())


def cover_names(c: ShelfCover) -> list[str]:
    r = c.reading
    out = [r.title] if r.title else []
    if r.barcode_name:
        name = r.barcode_name if r.barcode_source in barcode.CLEAN_SOURCES else product_name(r.barcode_name)
        if name and all(title_similarity(name, n) < 0.95 for n in out):
            out.append(name)
    return out


def job_clues(job: JobRecord) -> tuple[list[str], list[tuple[str, int | None, str]]]:
    """The job's barcodes, and the names it may be: (name, year, what it is)."""
    codes: list[str] = []
    sl = job.listing
    found = (sl.edited or sl.found) if sl else None
    if found and found.details.barcode:
        codes.append(found.details.barcode)
    if job.contribution and job.contribution.details.upc:
        codes.append(job.contribution.details.upc)
    if (n := catalogue.number(job.disc.label)) and release_info.valid_upc(n):
        codes.append(n)
    names: list[tuple[str, int | None, str]] = []
    if job.identity:
        names.append((job.identity.title, job.identity.year, "the disc's title"))
    if job.media == MediaKind.tv and job.tv and job.tv.show:
        names.append((job.tv.show.name, job.tv.show.year, "the show"))
    if job.disc_match:
        names.append((job.disc_match.title, job.disc_match.year, "TheDiscDB's title"))
    if job.state in (JobState.review, JobState.clash) or not job.identity:
        names += [(c.title, c.year, "a candidate") for c in job.candidates[:5] if not c.excluded]
    if job.preset:
        names.append((job.preset.title, job.preset.year, "the title set by hand"))
    label = parse_label(job.disc.label or job.disc.name)
    if label.title and not label.generic:
        names.append((label.title, label.year, "the disc's label"))
    return codes, names


def score_cover(c: ShelfCover, codes: list[str], names: list[tuple[str, int | None, str]]) -> tuple[float, str]:
    """How well a shelf cover fits a job: 1 for the same barcode, else how alike its
    title is to the job's names (a year that agrees helps, one that doesn't hurts)."""
    r = c.reading
    if r.barcode and any(barcode.same_code(r.barcode, code) for code in codes):
        return 1.0, f"The same barcode, {r.barcode}"
    best, reason = 0.0, ""
    for mine in cover_names(c):
        for name, year, what in names:
            sim = title_similarity(mine, name)
            score = sim * 0.85
            if r.year and year:
                score += 0.1 if abs(r.year - year) <= 1 else -0.25
            if score > best:
                best = score
                reason = f"The cover reads “{mine}”" + (f" ({r.year})" if r.year else "") + \
                    (f", like {what}" if sim >= 0.999 else f", close to {what} “{name}”") + \
                    (f" ({year})" if year and sim < 0.999 else "")
    return min(best, 0.99), reason
