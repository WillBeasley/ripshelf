"""Room for rips (mixed into ``jobs.Manager``).

A failed job keeps its rip in ``<scratch>/job-<id>``, so that Retry carries on from
it instead of ripping the disc again. With ``failed_scratch_days`` set, the rips of
jobs that have sat failed that long are deleted; Retry then rips the disc again.

Before a finished rip is copied, the folder it goes to is checked for room, so that
a full library disk fails the job at once rather than part way through the copy.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

from . import library
from .library import gib
from .models import JobState, Problem

if TYPE_CHECKING:
    from .db import JobRecord

log = logging.getLogger(__name__)

# A rip being deleted is renamed to this first, so a job retried meanwhile never sees half of it.
DELETING_PREFIX = ".deleting-"
# list_jobs' limit, high enough for every failed job.
ALL_JOBS = 100_000


def newest_change(folder: Path) -> float:
    """When anything in ``folder`` last changed (its own mtime if it's empty). Blocking."""
    times = [folder.stat().st_mtime]
    times += [p.stat().st_mtime for p in folder.rglob("*")]
    return max(times)


class ScratchMixin:
    def _library_full(self, job: JobRecord, media: str, sources: list[Path]) -> bool:
        """Fails the job when ``sources`` won't fit in the output root for ``media``."""
        root, what = self.s.output_root(media)
        short = library.space_shortfall(root, sources, what)
        if short:
            self._fail(job, Problem(code="library_full", message=short, actions=["retry"]))
        return bool(short)

    async def clear_failed_scratch(self) -> int:
        """Deletes the scratch files of jobs failed for ``failed_scratch_days`` or more, with
        nothing in them changed since. Returns how many jobs' files went."""
        scratch = self.s.scratch_dir
        for old in scratch.glob(f"{DELETING_PREFIX}*"):  # left by a restart part way through
            await asyncio.to_thread(shutil.rmtree, old, True)
        days = self.s.failed_scratch_days
        if days <= 0:
            return 0
        cutoff = time.time() - days * 86400
        cleared = 0
        for job in self.db.list_jobs([JobState.failed.value], limit=ALL_JOBS):
            folder = self.scratch_dir(job)
            if not folder.is_dir() or job.updated_at.timestamp() > cutoff:
                continue
            try:
                if await asyncio.to_thread(newest_change, folder) > cutoff:
                    continue
                size = await asyncio.to_thread(library.tree_size, folder)
            except OSError:
                continue
            fresh = self.db.get_job(job.id)  # retried or deleted while that was read
            if fresh is None or fresh.state != JobState.failed or fresh.updated_at != job.updated_at:
                continue
            doomed = scratch / f"{DELETING_PREFIX}{folder.name}"
            folder.rename(doomed)
            await asyncio.to_thread(shutil.rmtree, doomed, True)
            self._event(fresh, "info", f"Deleted its rip from scratch ({gib(size)}), {days} day"
                        f"{'s' if days != 1 else ''} after it failed. Retry rips the disc again.")
            cleared += 1
        return cleared
