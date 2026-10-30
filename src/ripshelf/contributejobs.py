"""Sending confirmed discs to TheDiscDB (mixed into ``jobs.Manager``).

It's opt-in, in Settings → Metadata providers. Before anything is sent, the user goes
through the disc's titles and names the extras worth keeping (the film and episodes are
named after themselves, and the rest are left out). In "ask" mode each eligible job then
has a send button, and in "auto" mode a disc is sent once the user has confirmed what it
is and named its extras. Sending
runs as a task of its own, so a network problem marks the contribution failed (with a
Retry), never the job.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from .db import now
from .models import Contribution, ContributionExtra, ContributionView, MediaKind
from .providers import contribute, thediscdb

if TYPE_CHECKING:
    from .db import JobRecord

log = logging.getLogger(__name__)


class ContributeMixin:
    def _init_contribute(self) -> None:
        self._contrib_tasks: dict[int, asyncio.Task] = {}

    def _contrib_check(self, job: JobRecord) -> tuple[bool, str | None, bool]:
        self._recover_content_hash(job)
        return contribute.eligibility(self.s, job, self.work_dir(job) / "scan.log")

    def _recover_content_hash(self, job: JobRecord) -> None:
        """A job read through a worker before the disc's file sizes reached the
        controller has no content hash; a Blu-ray's can be worked out again from the
        clip info files kept with the job."""
        if contribute.content_hash(job):
            return
        structure = self.work_dir(job) / "structure"
        if not structure.is_dir():
            return
        ch = thediscdb.fingerprints(structure).get("content_hash")
        if ch:
            job.disc_ids = {**(job.disc_ids or {}), "content_hash": ch}
            self._save(job)

    def _assign_release(self, job: JobRecord, new_release: bool | None = None) -> Contribution:
        """The release the disc goes into, kept once given unless asked again. By default
        the newest recent release of the same title (and season, and disc type)."""
        c = job.contribution or Contribution()
        if c.release_id and new_release is None:
            return c
        if new_release is not None:
            c.new_release = new_release
        key = json.dumps(contribute.group_key(job))
        found = None if c.new_release else self.db.recent_release(
            key, now() - timedelta(days=contribute.RELEASE_WINDOW_DAYS), job.id)
        c.release_id = found[0] if found else contribute.new_release_id()
        taken = self.db.release_discs(c.release_id) if found else []
        tv_disc = job.tv.disc_number if job.media == MediaKind.tv and job.tv else None
        c.disc_number = tv_disc if tv_disc and tv_disc not in taken else max(taken, default=0) + 1
        c.contribute_url = contribute.contribute_url(self.s, c.release_id)
        self.db.assign_release(job.id, c.release_id, key, c.disc_number)
        job.contribution = c
        return c

    def contribution_view(self, job_id: int) -> ContributionView | None:
        job = self.db.get_job(job_id)
        if not job:
            return None
        ok, reason, known = self._contrib_check(job)
        todo = contribute.todo(job) if ok else None
        view = ContributionView(mode=self.s.thediscdb_contribute,
                                eligible=ok and not todo and job.id not in self._contrib_tasks,
                                reason=reason, known=known, contribution=job.contribution,
                                base_url=contribute.base_url(self.s), todo=todo)
        if ok:
            if not (job.contribution and job.contribution.release_id):
                self._assign_release(job)
                self._save(job)
            c = job.contribution
            view.contribution = c
            view.payload = contribute.payload(job, c.release_id, c.disc_number)
            scan = contribute.scan_log(self.work_dir(job) / "scan.log") or ""
            view.scan_log_lines = scan.count("\n")
            view.titles = contribute.naming_titles(job)
        return view

    async def contribution_extras(self, job_id: int, extras: list[ContributionExtra]) -> ContributionView:
        """The extras to send and their names (none is an answer too). Until this is given,
        nothing is sent."""
        job = self.db.get_job(job_id)
        if not job:
            raise KeyError(job_id)
        ok, reason, _ = self._contrib_check(job)
        if not ok:
            raise ValueError(reason)
        if job_id in self._contrib_tasks:
            raise ValueError("it is being sent right now")
        fixed = {t.title_index for t in contribute.naming_titles(job) if t.fixed}
        indexes = {t.index for t in job.titles}
        seen: set[int] = set()
        for x in extras:
            if x.title_index not in indexes:
                raise ValueError(f"the disc has no title {x.title_index}")
            if x.title_index in fixed:
                raise ValueError(f"title {x.title_index} is sent as the film or an episode already")
            if x.title_index in seen:
                raise ValueError(f"title {x.title_index} is listed twice")
            seen.add(x.title_index)
        c = self._assign_release(job)
        c.extras = sorted(extras, key=lambda x: x.title_index)
        c.items_confirmed = True
        if c.status == "sent":
            c.status = "not_sent"  # TheDiscDB has the old names, so send again to replace them
        self._save(job)
        self._maybe_contribute(job_id)
        return self.contribution_view(job_id)

    async def contribution_release(self, job_id: int, new_release: bool) -> ContributionView:
        job = self.db.get_job(job_id)
        if not job:
            raise KeyError(job_id)
        ok, reason, _ = self._contrib_check(job)
        if not ok:
            raise ValueError(reason)
        if job_id in self._contrib_tasks:
            raise ValueError("it is being sent right now")
        c = self._assign_release(job, new_release)
        if c.status == "sent":
            c.status = "not_sent"  # sent to another release, so send again to move it
        self._save(job)
        return self.contribution_view(job_id)

    async def contribute(self, job_id: int) -> ContributionView:
        """Send the disc now (the button, or Retry)."""
        job = self.db.get_job(job_id)
        if not job:
            raise KeyError(job_id)
        ok, reason, _ = self._contrib_check(job)
        if not ok:
            raise ValueError(reason)
        if todo := contribute.todo(job):
            raise ValueError(todo)
        if job_id in self._contrib_tasks:
            raise ValueError("it is being sent already")
        self._start_contribution(job)
        return self.contribution_view(job_id)

    def _maybe_contribute(self, job_id: int) -> None:
        """Send the disc after the user confirms an identity, in "auto" mode."""
        job = self.db.get_job(job_id)
        if not job or not contribute.auto_send(self.s, job) or job_id in self._contrib_tasks:
            return
        if job.contribution and job.contribution.status == "sent":
            return
        if self._contrib_check(job)[0] and not contribute.todo(job):
            self._start_contribution(job)

    def _start_contribution(self, job: JobRecord) -> None:
        c = self._assign_release(job)
        c.status, c.error = "sending", None
        self._save(job)
        task = asyncio.create_task(self._send_contribution(job.id))
        self._contrib_tasks[job.id] = task
        task.add_done_callback(lambda t: self._contrib_tasks.pop(job.id, None))

    async def _send_contribution(self, job_id: int) -> None:
        job = self.db.get_job(job_id)
        c = job.contribution
        body = contribute.payload(job, c.release_id, c.disc_number)
        scan = contribute.scan_log(self.work_dir(job) / "scan.log") or ""
        client = contribute.Contributor(self.s)
        error = None
        reply: dict = {}
        try:
            reply = await client.send(body, scan)
        except contribute.ContributeError as e:
            error = str(e)
        except asyncio.CancelledError:
            error = "stopped before it finished"
            raise
        except Exception as e:  # never take the job down with it
            log.exception("job %s: sending to TheDiscDB crashed", job_id)
            error = f"internal error: {type(e).__name__}"
        finally:
            await client.aclose()
            job = self.db.get_job(job_id)
            if job and job.contribution:
                c = job.contribution
                if error:
                    c.status, c.error = "failed", error
                    self._event(job, "warning", f"Not sent to TheDiscDB ({error})")
                else:
                    c.status, c.error, c.sent_at = "sent", None, now()
                    c.updated = bool(reply.get("updated")) if reply else None
                    self._event(job, "info", f"Sent to TheDiscDB (disc {c.disc_number} of release "
                                             f"{c.release_id}). Finish it at {c.contribute_url}")
                self._save(job)
