"""The box's listing matched to a job's titles, and what it's used for (mixed into
``jobs.Manager``): the matches shown in Review, the TV picks offered from them, the
extras' names and types for TheDiscDB and the library.

The match is made again whenever the listing or what it's compared with changes, and
kept on the job (``scan_match``). It only ever suggests: the TV mapping changes when
the user takes its picks, and an extra is named from it only when the match isn't low.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from . import library
from .db import now
from .identify.scoring import title_similarity
from .models import DiscKind, JobState, MediaKind, ScanMatch, TitleMatch
from .providers import release_info
from .scanner import match as matcher

if TYPE_CHECKING:
    from .db import JobRecord

log = logging.getLogger(__name__)


class MatchMixin:
    def _listing_for(self, job: JobRecord):
        sl = job.listing
        return (sl.edited or sl.found) if sl else None

    def _claimed(self, job: JobRecord, listing) -> set[int]:
        """Listing lines other discs of the same release matched already."""
        c = job.contribution
        if not c or not c.release_id:
            return set()
        taken: set[tuple[str, str]] = set()
        for other_id in self.db.release_jobs(c.release_id):
            other = self.db.get_job(other_id) if other_id != job.id else None
            if other and other.scan_match:
                taken |= {(m.kind, m.name.lower()) for m in other.scan_match.matches if m.item is not None}
        return {i for i, it in enumerate(listing.items) if (it.kind, it.title.lower()) in taken}

    def compute_match(self, job: JobRecord) -> ScanMatch | None:
        listing = self._listing_for(job)
        if not listing or not job.titles or not (listing.items or listing.details.feature_runtime_min):
            return None
        tv = job.tv if job.media == MediaKind.tv else None
        pal = job.disc.kind == DiscKind.dvd and any((t.resolution or "").endswith("576") for t in job.titles)
        disc = (tv.disc_number if tv else None) or (job.contribution.disc_number if job.contribution else None)
        m = matcher.match(job.titles, listing, media=job.media.value, episodes=tv.episodes if tv else [],
                          season=tv.season if tv else None, mapping=tv.mapping if tv else [],
                          main_title=job.main_title.index if job.main_title and job.media == MediaKind.movie
                          else None, disc=disc, claimed=self._claimed(job, listing), pal=pal)
        m.made_at = now()
        return m

    def rematch(self, job_id: int) -> ScanMatch | None:
        """Match again and keep it on the job, if it changed."""
        job = self.db.get_job(job_id)
        if not job:
            raise KeyError(job_id)
        m = self.compute_match(job)
        old = job.scan_match.model_dump(exclude={"made_at"}) if job.scan_match else None
        if (m.model_dump(exclude={"made_at"}) if m else None) != old:
            job.scan_match = m
            self._save_scans(job)
        return job.scan_match

    # --- using it ---------------------------------------------------------------------

    @staticmethod
    def named_extras(job: JobRecord) -> dict[int, TitleMatch]:
        """Titles the box names as extras (title index -> match), when the match is good
        enough to name a file after."""
        m = job.scan_match
        if not m:
            return {}
        return {x.title_index: x for x in m.matches if x.kind == "extra" and x.confidence != "low" and x.name}

    # --- a film's extras ---------------------------------------------------------------

    def film_extra_titles(self, job: JobRecord) -> list[int]:
        """A film's titles to rip besides the main one: those the box names (when they go
        into Emby's folders), and the rest too when unnamed extras are filed."""
        if job.media != MediaKind.movie or job.mode == "backup" or not job.main_title:
            return []
        out = set(self.named_extras(job)) if self.s.extras_emby_folders else set()
        if self.s.film_unnamed_extras == "extras":
            out |= {t.index for t in matcher._candidates(job.titles, []) if t.duration_s >= 30}
        out.discard(job.main_title.index)
        return sorted(out)

    async def rip_film_extras(self, job_id: int) -> JobRecord:
        """Rip the film's extras the box names, now (the disc must still be in its drive);
        a finished job files them straight away."""
        job = self.db.get_job(job_id)
        if not job:
            raise KeyError(job_id)
        self.rematch(job_id)
        job = self.db.get_job(job_id)
        todo = [i for i in self.film_extra_titles(job) if i not in job.rip.files]
        if not todo:
            raise ValueError("there are no extras to rip: the box names none, or they're ripped already")
        d = self.drives.get(job.drive_id)
        if not d or d.job_id != job_id or d.rip or job.rip.state != "done":
            raise ValueError("the extras can be ripped once the film is, while the disc is still in its drive")
        if await self._drive_status(d) != "disc":
            raise ValueError("put the disc back in its drive first")
        self._spawn(d, self._rip_extras(job_id, todo), job_id)
        return job

    async def _rip_extras(self, job_id: int, indices: list[int]) -> None:
        job = self.db.get_job(job_id)
        d = self.drives[job.drive_id]
        out_dir = self.scratch_dir(job)
        out_dir.mkdir(parents=True, exist_ok=True)
        self._event(job, "rip", f"Ripping {len(indices)} extra{'s' if len(indices) != 1 else ''} the box names")
        home = self._home(d)
        d.rip = self._runner(d)
        log_path = self.work_dir(job) / "rip-extras.log"
        try:
            for idx in indices:
                res = await d.rip.run(["-r", "--progress=-same", "mkv", f"dev:{d.hw.device}", str(idx), str(out_dir)],
                                      home, log_path)
                if res.cancelled:
                    return
                if res.returncode != 0:
                    self._event(self.db.get_job(job_id), "warning", f"Title {idx} couldn't be ripped")
        finally:
            d.rip = None
        job = self.db.get_job(job_id)
        got = {i: f for i, f in self._collect_files(job, out_dir).items() if i in indices}
        job.rip.files = {**job.rip.files, **got}
        self._save(job)
        self._event(job, "rip", f"Ripped {len(got)} of {len(indices)} extras")
        if job.state == "done" and job.destination:
            await self.file_film_extras(job, Path(job.destination).parent)
            shutil.rmtree(out_dir, ignore_errors=True)

    async def file_film_extras(self, job: JobRecord, folder: Path) -> None:
        """Copy a film's ripped extras into Emby's extras folders beside it: a named one
        under its name in the folder for its type, an unnamed one as extras/Title N. A
        file already there is never replaced. A failure is a warning: the film is in."""
        main = job.main_title.index if job.main_title else None
        named = self.named_extras(job) if self.s.extras_emby_folders else {}
        placed: set[Path] = set()
        filed: dict[int, str] = {}
        for idx, src in sorted(job.rip.files.items()):
            if idx == main or idx in job.filed_extras or not Path(src).is_file():
                continue
            if x := named.get(idx):
                dest = folder / library.extras_folder(x.extra_type, x.name) / f"{library.safe_name(x.name)}.mkv"
            elif self.s.film_unnamed_extras == "extras":
                dest = folder / "extras" / f"Title {idx}.mkv"
            else:
                continue
            dest = library.free_path(dest, placed)
            placed.add(dest)
            try:
                await asyncio.to_thread(library.copy_verified, Path(src), dest)
            except (OSError, library.VerifyError) as e:
                self._event(job, "warning", f"Could not copy title {idx} to {dest}: {e}")
                continue
            filed[idx] = str(dest)
        if filed:
            job = self.db.get_job(job.id)
            job.filed_extras = {**job.filed_extras, **filed}
            self._save(job)
            self._event(job, "finalise", f"Filed {len(filed)} extra{'s' if len(filed) != 1 else ''}: "
                        + ", ".join(Path(p).relative_to(folder).as_posix() for p in filed.values()))

    # --- identifying the disc from its box ------------------------------------------------

    async def scan_hints(self, job: JobRecord) -> ScanHints:
        """What the box says the disc is: the title and year read off it, and the product
        its barcode names (UPCitemdb, looked up once and kept)."""
        listing = self._listing_for(job)
        hints = ScanHints()
        if not listing:
            return hints
        d = listing.details
        if d.title:
            hints.names.append(d.title)
            hints.evidence.append(f"The cover scan reads “{d.title}”" + (f" ({d.year})" if d.year else ""))
        hints.year = d.year
        if d.barcode and job.listing:
            if job.listing.upc_title is None:
                job.listing.upc_title = await self._upc_title(d.barcode)
                self._save_scans(job)
            if job.listing.upc_title:
                name = product_name(job.listing.upc_title)
                if name and all(title_similarity(name, n) < 0.9 for n in hints.names):
                    hints.names.append(name)
                    hints.evidence.append(f"The barcode on the box, {d.barcode}, is “{job.listing.upc_title}”")
        return hints

    async def _upc_title(self, upc: str) -> str:
        """The product's title for a barcode, or "" when nothing is known (so it isn't
        asked again: UPCitemdb's free lookups are limited)."""
        products = release_info.Products()
        try:
            found = await products.lookup(upc)
        except release_info.ReleaseLookupError as e:
            log.info("no product for barcode %s: %s", upc, e)
            return ""
        finally:
            await products.aclose()
        return found[0].title if found else ""

    def identify_from_scan(self, job_id: int) -> None:
        """Identify again when the box's reading may help: the job waits for review with
        nothing confirmed, and the scan says something new about what it is."""
        job = self.db.get_job(job_id)
        listing = self._listing_for(job) if job else None
        if not job or not listing or job.state != JobState.review or job.identity:
            return
        if job.media == MediaKind.tv and job.tv and job.tv.show:
            return
        d = listing.details
        key = f"{d.title}|{d.year}|{d.barcode}"
        if not (d.title or d.barcode) or job.listing.identified_from == key:
            return
        job.listing.identified_from = key
        self._save_scans(job)
        self._event(job, "info", "Identifying again with what the cover scan says")
        d_rt = self.drives.get(job.drive_id)
        if d_rt:
            self._spawn(d_rt, self._identify_then_rip(job_id), job_id)


@dataclass
class ScanHints:
    names: list[str] = field(default_factory=list)
    year: int | None = None
    evidence: list[str] = field(default_factory=list)


def product_name(title: str) -> str:
    """"The Great Escape (DVD, 2006) [Region 2]" -> "The Great Escape"."""
    t = re.sub(r"[\(\[].*?[\)\]]", " ", title)
    t = re.sub(r"(?i)\b(dvd|blu-?ray|4k|ultra hd|uhd|region\s*\w+|special edition|collector'?s edition|"
               r"widescreen|box set|\d+-disc)\b.*$", " ", t)
    return re.sub(r"\s+", " ", t).strip(" -–:,") or ""
