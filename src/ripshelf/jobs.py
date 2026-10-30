"""Job orchestration, meaning drive polling and the per-disc pipeline.

    detected -> scanning -> identifying -> ripping -> [transcoding] -> finalising -> done
                                       \\-> review --/

The rip starts as soon as the scan is done and runs alongside identification
and human review; the job shows ``review`` while it waits for a decision and
``ripping`` once the identity is settled. Transcoding (optional) and
finalising start when both the rip and the identity are done, apart from the
drive, which takes the next disc. One rip per drive, drives in parallel; drives
can be local or on remote workers. TV discs are handled in ``tvjobs`` and
transcoding in ``transcodejobs``.

In backup mode (Settings, per drive or for all) a Blu-ray or UHD disc is copied
whole with ``makemkvcon backup --decrypt``, as a decrypted BDMV folder named after
the identified film, in the disc backups folder. It is never transcoded.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from . import __version__, art, discfs, integrations, library, providers
from .bus import EventBus
from .config import EULA_PENDING, Settings
from .contributejobs import ContributeMixin
from .db import Database, JobRecord, now
from .hw.drives import DiscIdentity, DriveHw
from .identify import catalogue, scoring
from .identify.label import clean_bdmt_title, name_differs_from_label, parse_label
from .makemkv import runner
from .makemkv.diagnose import diagnose
from .makemkv.files import SupportFiles
from .makemkv.parse import (
    MSG_COPY_COMPLETE,
    ProgressTracker,
    RipProgress,
    ScanResult,
    choose_main_feature,
    parse_info,
)
from .models import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    TRANSITIONS,
    Candidate,
    Capability,
    ClashFile,
    ClashInfo,
    DiscFacts,
    DiscKind,
    Drive,
    DriveStats,
    DriveStatus,
    DriveUpdated,
    Evidence,
    JobDetail,
    JobEventAdded,
    JobsDeleted,
    JobState,
    JobStep,
    JobUpdated,
    MediaKind,
    Preset,
    Problem,
    RipProgressEvent,
    RipState,
    SetupProblem,
    Status,
    StatusChanged,
    TitleInfo,
    TitleRole,
)
from .proc import WINDOWS
from .providers import films, thediscdb
from .transcodejobs import TranscodeMixin
from .tvjobs import TvMixin
from .workers.hub import RemoteBackend, RemoteMakeMKV, Worker, WorkerHub

log = logging.getLogger(__name__)

PROGRESS_PUBLISH_S = 1.0
PROGRESS_SAVE_S = 10.0
STATUS_TIMEOUT_S = 10.0
# Scratch needed for a rip is its size plus a margin, plus room for a transcoded copy.
SCRATCH_MARGIN = 1.05
BACKUP_INTERVAL_S = 24 * 3600
BACKUP_CHECK_S = 3600
TRANSCODE_ROOM = 0.6
# A best guess this strong is checked against the library before the rip starts.
PROPOSED_CLASH_SCORE = 0.6
# After an eject, a "disc" status is ignored until the tray is seen open, for at most
# this long (a remote drive's status is a few seconds old).
EJECT_SETTLE_S = 20.0
# Job problems that are fixed in Settings, not by retrying.
SETUP_CODES = {"volume_key_unknown", "decrypt_failed", "makemkv_key", "makemkv_missing"}
# Setup problems shown in the UI but not worth a notification (they are there from the first start).
QUIET_SETUP_CODES = {"tmdb_key", "makemkv_key_unset", "public_url", "private_data_missing"}


class DriveBackend(Protocol):
    def discover(self, uhd_models: list[str]) -> list[DriveHw]: ...
    def status(self, device: str) -> DriveStatus: ...
    def eject(self, device: str) -> None: ...
    def close_tray(self, device: str) -> None: ...
    async def disc_identity(self, device: str) -> DiscIdentity: ...
    async def extract_structure(self, device: str, out_dir: Path) -> list[str]: ...


class JobError(Exception):
    def __init__(self, problem: Problem):
        super().__init__(problem.message)
        self.problem = problem


@dataclass
class DriveRuntime:
    hw: DriveHw
    status: DriveStatus = DriveStatus.empty
    job_id: int | None = None
    # Quick fingerprint of the disc we already handled, so a disc left in the
    # drive (or present at startup) is not ripped twice.
    handled_fp: str | None = None
    # The disc in the drive has been read (blkid) since it went in, so don't read it
    # again until the tray opens. Polling only asks the drive for its status.
    identified: bool = False
    label: str | None = None  # the disc's label, for a disabled drive (which gets no job)
    ejecting_until: float = 0.0  # ejected, and the tray hasn't been seen open yet
    status_pending: bool = False
    scan: runner.MakeMKV | RemoteMakeMKV | None = None
    rip: runner.MakeMKV | RemoteMakeMKV | None = None
    # For remote drives, their worker's backend, and (worker id, drive id on the worker).
    backend: object = None
    remote: tuple[str, str] | None = None
    tasks: set[asyncio.Task] = field(default_factory=set)


class Manager(TvMixin, TranscodeMixin, ContributeMixin):
    def __init__(self, settings: Settings, db: Database, backend: DriveBackend, bus: EventBus):
        self.s = settings
        self.db = db
        self.backend = backend
        self.bus = bus
        self.drives: dict[str, DriveRuntime] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._poller: asyncio.Task | None = None
        self._last_progress: dict[int, float] = {}
        # Presets set (or cleared to None) by hand. They win over a job copy saved meanwhile.
        self._presets: dict[int, Preset | None] = {}
        self.makemkv_version: str | None = None
        self.java_missing = False  # a scan said MakeMKV has no Java runtime
        self.open_gate = runner.OpenGate()
        self.notifier = integrations.Notifier(settings)
        self._setup_notified: set[str] = set()
        self._init_transcode()
        self._init_contribute()
        self.support = SupportFiles(settings.makemkv_files_dir)
        self.hub = WorkerHub(on_drives_changed=self._remote_drives,
                             local_ids=lambda: {i for i, d in self.drives.items() if not d.remote})

    # --- lifecycle ------------------------------------------------------------

    async def start(self) -> None:
        for job in self.db.list_jobs([s.value for s in ACTIVE_STATES]):
            if job.state == JobState.review and job.rip.state == RipState.done:
                continue  # waiting for a human, nothing in flight
            job.rip.state = RipState.failed if job.rip.state == RipState.running else job.rip.state
            self._fail(job, Problem(code="interrupted", message="ripshelf restarted while this job was running",
                                    actions=["retry", "eject"]), notify=False)
        for job_id in self.db.jobs_sending_contribution():
            job = self.db.get_job(job_id)
            job.contribution.status, job.contribution.error = "failed", "ripshelf restarted while it was being sent"
            self.db.save_job(job)
        self.write_default_key()
        if WINDOWS and runner.seed_template(self.s.makemkv_dir, Path.home()):
            log.info("using MakeMKV's own private data (%s) for new drives", Path.home() / ".MakeMKV")
        self.discover()
        log.info("ripshelf %s started with %d drive(s): %s", __version__, len(self.drives),
                 ", ".join(f"{self.short_drive_label(d.hw.id)} ({d.hw.device})"
                           for d in self.drives.values()) or "none")
        self._poller = asyncio.create_task(self._poll_loop())
        self._backups = asyncio.create_task(self._backup_loop())

    async def stop(self) -> None:
        self.notifier.cancel()
        if self._poller:
            self._poller.cancel()
        if getattr(self, "_backups", None):
            self._backups.cancel()
        for job_id in list(self._bg):
            self._cancel_bg(job_id)
        for t in list(self._contrib_tasks.values()):
            t.cancel()
        for d in self.drives.values():
            for proc in (d.scan, d.rip):
                if proc:
                    proc.kill()
            for t in list(d.tasks):
                t.cancel()

    def discover(self) -> None:
        for hw in self.backend.discover(self.s.uhd_models):
            self._migrate_drive(hw, home=True)
            known = self.db.drive_data(hw.id)
            if not hw.serial and known.get("serial"):
                hw.serial = known["serial"]
            if hw.virtual and not known and hw.id not in self.s.disabled_drives:
                # A virtual drive (a VM's, a mounted image) starts disabled, and Settings can enable it.
                log.info("%s (%s): a virtual drive, so it starts disabled", hw.device, hw.label)
                self.s.save_overrides({"disabled_drives": [*self.s.disabled_drives, hw.id]})
            rt = self.drives.get(hw.id) or DriveRuntime(hw=hw)
            rt.hw = hw
            self.drives[hw.id] = rt
            latest = self.db.latest_job_for_drive(hw.id)
            if latest:
                rt.job_id = latest.id
                rt.handled_fp = latest.quick_fp
            self.db.save_drive_data(hw.id, {**known, "device": hw.device, "model": hw.model,
                                            "serial": hw.serial})

    def _migrate_drive(self, hw: DriveHw, home: bool) -> None:
        """Drive ids used to come from the port; now from vendor, model and serial. Carry
        what was kept under the drive's old id over to the new one. That's jobs, statistics,
        settings and (``home``) its MakeMKV home. Nothing to do once that's done."""
        old, new = hw.legacy_id, hw.id
        if not old or old == new or old in self.drives:
            return
        moved = self.db.rename_drive(old, new)
        s = self.s
        changes: dict = {}
        if old in s.drive_modes:
            changes["drive_modes"] = {k: v for k, v in s.drive_modes.items() if k != old} | \
                {new: s.drive_modes.get(new, s.drive_modes[old])}
        if old in s.disabled_drives:
            changes["disabled_drives"] = list(dict.fromkeys(new if d == old else d for d in s.disabled_drives))
        if old in s.drive_names:
            changes["drive_names"] = {k: v for k, v in s.drive_names.items() if k != old} | \
                {new: s.drive_names.get(new, s.drive_names[old])}
        if changes:
            s.save_overrides(changes)
        self.support.rename_drive(old, new)
        homed = False
        if home:
            try:
                homed = runner.migrate_home(s.makemkv_dir, old, new)
            except OSError as e:
                log.warning("could not move the MakeMKV home of %s to %s: %s", old, new, e)
        if moved or changes or homed:
            log.info("drive %s (%s %s) is now %s: moved %d job/statistics rows, settings %s, "
                     "MakeMKV home %s", old, hw.vendor, hw.model, new, moved,
                     ", ".join(changes) or "none", "moved" if homed else "unchanged")

    # --- backups --------------------------------------------------------------

    def backup_problem(self) -> str | None:
        """Why backups can't be written, or None. A configured folder must already exist."""
        if self.s.backup_keep <= 0 or self.s.backup_dir is None:
            return None
        d = self.s.backup_dir
        if not d.is_dir():
            return f"Backup folder {d} does not exist. Mount it (see the README) or set RIPSHELF_BACKUP_DIR"
        if not os.access(d, os.W_OK | os.X_OK):
            return f"Backup folder {d} is not writable"
        return None

    def backup_now(self) -> Path | None:
        """Write today's database backup and prune old ones. Returns the file."""
        if self.s.backup_keep <= 0 or self.backup_problem():
            return None
        folder = self.s.backups
        dest = folder / f"ripshelf-{now().strftime('%Y-%m-%d')}.db"
        self.db.backup(dest)
        for old in sorted(folder.glob("ripshelf-*.db"))[:-self.s.backup_keep]:
            old.unlink(missing_ok=True)
        return dest

    def _backup_due(self) -> bool:
        files = sorted(self.s.backups.glob("ripshelf-*.db"))
        return not files or time.time() - files[-1].stat().st_mtime > BACKUP_INTERVAL_S

    async def _backup_loop(self) -> None:
        while True:
            try:
                if self.s.backup_keep > 0 and not self.backup_problem() and self._backup_due():
                    path = await asyncio.to_thread(self.backup_now)
                    log.info("database backed up to %s", path)
            except Exception:
                log.exception("database backup failed")
            try:
                if n := await asyncio.to_thread(art.prune, self.s.data_dir / "art"):
                    log.info("deleted %d poster(s) cached more than %d days ago", n, art.MAX_AGE_S // 86400)
            except OSError:
                log.exception("poster clean-up failed")
            await asyncio.sleep(BACKUP_CHECK_S)

    # --- remote workers -------------------------------------------------------

    def _be(self, d: DriveRuntime):
        return d.backend or self.backend

    def _runner(self, d: DriveRuntime):
        if d.remote:
            return RemoteMakeMKV(self.hub, *d.remote)
        return runner.MakeMKV(self.s.makemkvcon, self.open_gate, label=d.hw.device)

    def write_default_key(self) -> None:
        """Write the key (or remove it, once cleared) in every MakeMKV home. That's each drive's,
        including those per-drive workers use from the shared makemkv folder, and
        <data>/.MakeMKV (HOME in the image), so a makemkvcon run by hand is registered too."""
        key = self.s.secret("makemkv_key")
        homes = [self.s.data_dir / ".MakeMKV"]
        if self.s.makemkv_dir.is_dir():
            homes += [p / ".MakeMKV" for p in self.s.makemkv_dir.iterdir() if p.is_dir() and not p.name.startswith("_")]
        for conf_dir in homes:
            if key or (conf_dir / "settings.conf").exists():
                try:
                    runner.set_key(conf_dir, key)
                except OSError as e:
                    log.warning("could not write the MakeMKV key to %s: %s", conf_dir, e)

    def _home(self, d: DriveRuntime) -> Path:
        # A worker keeps its drives' MakeMKV homes (and private data) itself.
        if d.remote:
            return self.s.makemkv_dir / "_remote"
        return runner.prepare_home(self.s.makemkv_dir, d.hw.id, self.s.secret("makemkv_key"))

    def _remote_drives(self, w: Worker) -> None:
        """A worker (re)registered, so its drives join (or replace) ours."""
        backend = RemoteBackend(self.hub, w.id)
        for rd in w.drives.values():
            hw = rd.hw
            # The worker moves its own MakeMKV homes.
            self._migrate_drive(hw, home=False)
            if w.shared_scratch:
                # A worker on this host shares the makemkv folder, so give its drive's home
                # the key (and template data) before its first scan.
                try:
                    runner.prepare_home(self.s.makemkv_dir, hw.id, self.s.secret("makemkv_key"))
                except OSError as e:
                    log.warning("could not prepare the MakeMKV home for %s: %s", hw.id, e)
            rt = self.drives.get(hw.id) or DriveRuntime(hw=hw)
            rt.hw, rt.backend, rt.remote = hw, backend, (w.id, rd.local_id)
            if hw.id not in self.drives:
                latest = self.db.latest_job_for_drive(hw.id)
                if latest:
                    rt.job_id, rt.handled_fp = latest.id, latest.quick_fp
            self.drives[hw.id] = rt
            self.publish_drive(rt)
        # The worker may have come back with MakeMKV's licence accepted (or not), so its drives
        # are held or let go.
        self.drives_toggled()
        self.publish_status()

    # --- drive polling --------------------------------------------------------

    async def _poll_loop(self) -> None:
        while True:
            try:
                self.hub.check_timeouts()
                await asyncio.gather(*(self._poll(d) for d in list(self.drives.values())))
            except Exception:
                log.exception("drive poll failed")
            await asyncio.sleep(self.s.poll_interval_s)

    async def _poll(self, d: DriveRuntime) -> None:
        if d.status_pending:
            return  # a previous status call is stuck (hung drive); don't pile up threads
        status = await self._drive_status(d)
        changed = status != d.status
        d.status = status
        job = self.db.get_job(d.job_id) if d.job_id else None

        if status != DriveStatus.disc:
            d.identified = False
            d.label = None
            d.ejecting_until = 0.0
        elif time.monotonic() < d.ejecting_until:
            return
        if status in (DriveStatus.empty, DriveStatus.tray_open):
            d.handled_fp = None
            if job and job.state in ACTIVE_STATES and job.state != JobState.checking and self._needs_disc(job):
                # Stop MakeMKV and the pipeline first, or they carry on against an empty drive.
                self._stop_drive_work(d)
                job = self.db.get_job(job.id)
                if job.rip.state == RipState.running:
                    job.rip.state = RipState.failed
                shutil.rmtree(self.scratch_dir(job), ignore_errors=True)
                self._fail(job, Problem(code="disc_removed", message="The disc was removed before ripping finished",
                                        actions=["retry"]))
        elif status == DriveStatus.disc and not d.identified and job and job.state == JobState.checking:
            await self._disc_back(d, job)
            changed = True
        elif status == DriveStatus.disc and not d.identified and self._held(d):
            # A disabled drive (or any, until the setup is finished) gets no job, and just shows what's in it.
            d.label = (await self._be(d).disc_identity(d.hw.device)).label or ""
            d.identified = changed = True
        elif status == DriveStatus.disc and not d.identified and not self._busy(d):
            ident = await self._be(d).disc_identity(d.hw.device)
            d.identified = True
            qfp = discfs.quick_fingerprint(ident.label, ident.uuid, ident.size_bytes)
            if qfp is None or qfp != d.handled_fp:
                d.handled_fp = qfp
                if job and qfp and job.quick_fp == qfp and job.media == MediaKind.tv \
                        and not job.rip.all_titles and job.state in (JobState.review, JobState.failed):
                    # The disc is back so its episodes can be ripped, in the same job.
                    self._event(job, "info", "Disc is back, so ripping every title")
                    await self._rerip_all(job, d)
                else:
                    self._start_job(d, ident, qfp)
                changed = True
        if changed:
            self.publish_drive(d)

    async def _drive_status(self, d: DriveRuntime) -> DriveStatus:
        """The drive's status, or ``error`` if it does not answer in time.

        The ioctl runs in a thread that cannot be cancelled; while it is stuck no
        new one is started (``status_pending`` clears only when it returns).
        """
        if d.status_pending:
            return DriveStatus.error
        d.status_pending = True
        fut = asyncio.ensure_future(asyncio.to_thread(self._be(d).status, d.hw.device))

        def finished(f: asyncio.Future) -> None:
            d.status_pending = False
            if not f.cancelled():
                f.exception()  # retrieved, so it is not logged as unhandled

        fut.add_done_callback(finished)
        try:
            return await asyncio.wait_for(asyncio.shield(fut), STATUS_TIMEOUT_S)
        except TimeoutError:
            return DriveStatus.error
        except Exception as e:
            log.warning("status of %s failed: %s", d.hw.device, e)
            return DriveStatus.error

    @staticmethod
    def _stop_drive_work(d: DriveRuntime) -> None:
        for proc in (d.scan, d.rip):
            if proc:
                proc.cancel()
        for t in list(d.tasks):
            t.cancel()

    def _busy(self, d: DriveRuntime) -> bool:
        if d.tasks:
            return True
        job = self.db.get_job(d.job_id) if d.job_id else None
        return bool(job and job.state in ACTIVE_STATES and self._needs_disc(job))

    @staticmethod
    def _needs_disc(job: JobRecord) -> bool:
        return job.rip.state in (RipState.pending, RipState.running)

    # --- job helpers ----------------------------------------------------------

    def _lock(self, job_id: int) -> asyncio.Lock:
        return self._locks.setdefault(job_id, asyncio.Lock())

    def _event(self, job: JobRecord, kind: str, message: str) -> None:
        ev = self.db.add_event(job.id, kind, message)
        self.bus.publish(JobEventAdded(event=ev))
        level = {"error": logging.ERROR, "warning": logging.WARNING}.get(kind, logging.INFO)
        # The drive's device now: /dev/srN can change after a restart (e.g. a retried job).
        d = self.drives.get(job.drive_id)
        where = (d.hw.device if d else job.device) or job.drive_id
        if name := self.s.drive_name(job.drive_id):
            where = f"{name}, {where}"
        log.log(level, "job %s (%s): %s", job.id, where, message)

    def _set_step(self, job: JobRecord, label: str, detail: str = "") -> None:
        """Show what a job off the drive is doing; the last progress bar belongs to the step before.
        Saved on a fresh copy, so a job cancelled meanwhile stays cancelled."""
        fresh = self.db.get_job(job.id)
        if fresh is None or fresh.state not in (JobState.transcoding, JobState.finalising):
            return
        for j in (job, fresh):
            j.step = JobStep(label=label, detail=detail)
            j.rip.progress = None
        self._save(fresh)

    def _save(self, job: JobRecord) -> None:
        if job.id in self._presets:
            job.preset = self._presets[job.id]
        self.db.save_job(job)
        self.bus.publish(JobUpdated(job=job.public()))
        d = self.drives.get(job.drive_id)
        if d and d.job_id == job.id:
            self.publish_drive(d)

    def _set_state(self, job: JobRecord, state: JobState, message: str | None = None) -> None:
        if state == job.state:
            return
        if state not in TRANSITIONS[job.state]:
            log.warning("job %s: illegal transition %s -> %s", job.id, job.state, state)
            return
        job.state = state
        if state not in (JobState.transcoding, JobState.finalising):
            job.step = None
        if state in TERMINAL_STATES:
            job.finished_at = now()
        else:
            job.finished_at = None
        self._event(job, "state", message or f"State: {state.value}")
        self._save(job)
        self.db.record_stats(job)
        # A TV disc that may still confirm itself notifies once that is decided.
        if state == JobState.review and not (job.media == MediaKind.tv and self.s.tv_auto_confirm):
            self._spawn_notify(job, "review")
        self.publish_status()

    def _fail(self, job: JobRecord, problem: Problem, notify: bool = True) -> None:
        job.problem = problem
        self._event(job, "error", problem.message)
        if job.state != JobState.failed:
            if JobState.failed in TRANSITIONS[job.state]:
                self._set_state(job, JobState.failed, f"Failed: {problem.message}")
            else:
                self._save(job)
        else:
            self._save(job)
        if notify:
            self._spawn_notify(job, "failed")

    def _spawn(self, d: DriveRuntime, coro, job_id: int) -> asyncio.Task:
        async def guarded() -> None:
            # Whatever goes wrong in a job task, the job must not be left stuck mid-state.
            try:
                await coro
            except asyncio.CancelledError:
                raise
            except JobError as e:
                self._fail(self.db.get_job(job_id), e.problem)
            except Exception as e:
                log.exception("job %s task crashed", job_id)
                self._fail(self.db.get_job(job_id), Problem(
                    code="internal", message=f"Internal error: {type(e).__name__}: {e}", actions=["retry"]))

        task = asyncio.create_task(guarded())
        d.tasks.add(task)

        def done(t: asyncio.Task) -> None:
            d.tasks.discard(t)
            if not t.cancelled() and t.exception():
                log.error("job task failed", exc_info=t.exception())

        task.add_done_callback(done)
        return task

    def has_active_jobs(self) -> bool:
        return bool(self.db.list_jobs([st.value for st in ACTIVE_STATES], limit=1))

    def work_dir(self, job: JobRecord) -> Path:
        return self.s.jobs_dir / str(job.id)

    def scratch_dir(self, job: JobRecord) -> Path:
        return self.s.scratch_dir / f"job-{job.id}"

    # --- pipeline -------------------------------------------------------------

    def _start_job(self, d: DriveRuntime, ident: DiscIdentity, qfp: str | None) -> JobRecord:
        job = self.db.create_job(d.hw.id, d.hw.device)
        job.disc.label = ident.label
        job.disc.volume_uuid = ident.uuid
        job.disc.size_bytes = ident.size_bytes
        job.quick_fp = qfp
        d.job_id = job.id
        self._event(job, "state", f"Disc detected in {d.hw.device}: {ident.label or 'no label'}")
        self._save(job)
        self._spawn_notify(job, "disc")
        self._spawn(d, self._pipeline(job.id), job.id)
        self.publish_status()
        return job

    async def _pipeline(self, job_id: int, from_scan: bool = False) -> None:
        job = self.db.get_job(job_id)
        d = self.drives[job.drive_id]
        try:
            cached = None
            if not from_scan:
                cached = await self._read_structure(job, d)
            scan = await self._scan(job, d)
            self._apply_scan(job, d, scan)
            job = self.db.get_job(job_id)
            # Identify first, so a disc that is already in the library is caught before
            # anything is ripped; then rip (during review too, unless that clashes).
            if not (job.preset and await self._use_preset(job.id)):
                recalled = None if job.media == MediaKind.tv else (cached or self._recall(job))
                await self._identify(job.id, cached=recalled)
            job = self.db.get_job(job_id)
            if job.state not in TERMINAL_STATES and not self._clash_before_rip(job):
                self._spawn(d, self._rip(job.id), job.id)
        except JobError as e:
            job = self.db.get_job(job_id)
            self._fail(job, e.problem)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("job %s crashed", job_id)
            job = self.db.get_job(job_id)
            self._fail(job, Problem(code="internal", message=f"Internal error: {e}", actions=["retry"]))

    async def _read_structure(self, job: JobRecord, d: DriveRuntime) -> tuple[Candidate, str] | None:
        out = self.work_dir(job) / "structure"
        files = await self._be(d).extract_structure(d.hw.device, out)
        job.content_fp = discfs.content_fingerprint(out, files)
        title = discfs.bdmt_title(out)
        if title:
            job.disc.name = title
        self._event(job, "info", f"Read {len(files)} structure files from the disc" if files
                    else "No structure files could be read from the disc")
        hit = self._recall(job)
        job.disc_ids = thediscdb.fingerprints(out)  # also what a contribution to TheDiscDB is keyed by
        if hit:
            self._event(job, "info", f"Recognised from the local disc cache: {hit[0].title}")
        else:
            await self._disc_db_lookup(job)
        self._save(job)
        return hit

    async def _disc_db_lookup(self, job: JobRecord) -> None:
        """Ask TheDiscDB about the disc by its fingerprints (when it's on in either list in Settings)."""
        if not job.disc_ids or not any("thediscdb" in providers.enabled(self.s, k) for k in ("movie", "tv")):
            return
        db = thediscdb.TheDiscDb()
        try:
            job.disc_match = await db.lookup(job.disc_ids)
        except thediscdb.DiscDbError as e:
            self._event(job, "warning", f"TheDiscDB: {e}")
            return
        finally:
            await db.aclose()
        if job.disc_match:
            self._event(job, "info", f"TheDiscDB knows this disc as {thediscdb.summary(job.disc_match)}")
        else:
            self._event(job, "info", "TheDiscDB doesn't know this disc yet")

    def _recall(self, job: JobRecord) -> tuple[Candidate, str] | None:
        for fp in (job.content_fp, job.scan_fp, job.quick_fp):
            hit = self.db.recall(fp)
            if hit:
                return hit
        return None

    async def _scan(self, job: JobRecord, d: DriveRuntime) -> ScanResult:
        self._set_state(job, JobState.scanning, f"Scanning disc with MakeMKV on {d.hw.device}")
        home = self._home(d)
        if self.open_gate.busy and not d.remote:
            held = int(asyncio.get_running_loop().time() - self.open_gate.since) if self.open_gate.since else 0
            self._event(job, "info", f"Waiting for {self.open_gate.holder or 'another drive'} to open its disc "
                                     f"(for {held} s so far). MakeMKV opens one disc at a time, at most "
                                     f"{int(self.open_gate.max_hold_s)} s each, then the scans run side by side")
        d.scan = self._runner(d)
        log_path = self.work_dir(job) / "scan.log"
        try:
            res = await d.scan.run(["-r", "--progress=-same", "--cache=1", "info", f"dev:{d.hw.device}"],
                                   home, log_path, timeout=self.s.scan_max_s, idle_timeout=self.s.scan_timeout_s)
        finally:
            d.scan = None
        # Progress lines are asked for only to tell a slow scan from a hung one.
        text = "".join(ln for ln in log_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
                       if not ln.startswith(("PRGV:", "PRGT:", "PRGC:")))
        log_path.write_text(text, encoding="utf-8")
        scan = parse_info(text)
        if scan.java_missing and not d.remote:
            self.java_missing = True
            self._event(job, "warning", "MakeMKV found no Java runtime, so Blu-ray menus (BD-J) can't be read. "
                                        "Some discs need them to find the right playlist")
        if scan.makemkv_version:
            job.makemkv_version = scan.makemkv_version
            self.makemkv_version = scan.makemkv_version
        if res.cancelled:
            raise asyncio.CancelledError
        if res.error:
            raise JobError(Problem(code="makemkv_missing", message=f"MakeMKV {res.error}",
                                   actions=["setup", "retry_scan"]))
        if res.timed_out:
            if res.stalled:
                what = f"MakeMKV stopped responding while scanning (no output for {_span(self.s.scan_timeout_s)})"
            else:
                what = f"MakeMKV was still scanning after {_span(self.s.scan_max_s)}"
            diag = diagnose(scan.messages)
            if diag:  # a drive stuck retrying bad reads, most often
                raise JobError(Problem(
                    code="scan_timeout", message=f"{diag.cause} {what}.",
                    hint=diag.hint, detail=diag.detail, actions=["retry_scan", "eject"]))
            raise JobError(Problem(
                code="scan_timeout",
                message=f"{what}. The drive may be hanging, the disc may be badly damaged, or MakeMKV is stuck "
                        f"making a firmware dump for it (for older drives, import a dump_*.tgz in Settings, see "
                        f"docs/troubleshooting.md). The limits are in Settings → Drives.",
                actions=["retry_scan", "eject"]))
        if scan.key_invalid:
            raise JobError(Problem(code="makemkv_key", message="MakeMKV rejected the registration key",
                                   actions=["setup"]))
        if scan.volume_key_unknown:
            raise JobError(Problem(
                code="volume_key_unknown",
                message="MakeMKV does not know the volume key for this disc. Import the drive's "
                        "_private_data.tar in Settings, then retry.",
                actions=["setup", "retry_scan", "eject"]))
        if scan.cannot_decrypt and not scan.titles:
            raise JobError(Problem(
                code="decrypt_failed",
                message="MakeMKV could not decrypt this disc (it needs LibreDrive data for this drive). "
                        "Import the drive's _private_data.tar in Settings, then retry.",
                actions=["setup", "retry_scan", "eject"]))
        if not scan.titles:
            last = next((m.text for m in reversed(scan.messages)), "no output")
            diag = diagnose(scan.messages)
            raise JobError(Problem(code="no_titles",
                                   message=f"MakeMKV found no titles. {diag.cause}" if diag else
                                   f"MakeMKV found no titles ({last})",
                                   hint=diag and diag.hint, detail=diag.detail if diag else last,
                                   actions=["retry_scan", "eject"]))
        serial = scan.drive_serial(d.hw.device)
        if serial and not d.hw.serial:
            d.hw.serial = serial
            data = self.db.drive_data(d.hw.id)
            self.db.save_drive_data(d.hw.id, {**data, "serial": serial})
        if scan.libredrive:
            self._event(job, "info", scan.libredrive)
        self._event(job, "info", f"Scan found {len(scan.titles)} titles")
        return scan

    def _apply_scan(self, job: JobRecord, d: DriveRuntime, scan: ScanResult) -> None:
        main = choose_main_feature(scan)
        job.titles = [to_title_info(t) for t in scan.titles]
        job.main_title = to_title_info(main.title)
        job.duplicates = main.duplicates
        job.disc.kind = DiscKind(scan.kind) if scan.kind else None
        job.disc.title_count = len(scan.titles)
        job.scan_fp = discfs.scan_fingerprint(scan)
        if not job.disc.name and name_differs_from_label(scan.disc_name, job.disc.label):
            job.disc.name = scan.disc_name
        if not job.disc.label and scan.volume_name:
            job.disc.label = scan.volume_name
        dur = scoring.fmt_duration(main.title.duration_s)
        msg = f"Main feature is title {main.title.index} ({dur}, {gib(main.title.size_bytes)})"
        if main.duplicates:
            msg += f"; skipped duplicate playlist(s) {', '.join(map(str, main.duplicates))}"
        self._event(job, "info", msg)
        if main.rivals:
            self._event(job, "warning", "Other titles have the same length but different content, "
                        + ", ".join(map(str, main.rivals)))
        if job.disc.kind == DiscKind.uhd and Capability.uhd not in d.hw.capabilities:
            self._event(job, "warning", "UHD disc in a drive not marked as UHD-capable")
        if job.disc_match:
            self._apply_disc_match(job, scan)
        job.mode = self._choose_mode(job, d, scan)
        if job.mode == "backup":
            # The whole disc is kept, so there are no episodes to map. It is named like a film.
            job.media, job.tv = MediaKind.movie, None
            self._event(job, "info", "Full disc backup, so the whole disc is copied, decrypted, as a BDMV folder")
        else:
            self._classify_media(job)
        self._save(job)

    def _apply_disc_match(self, job: JobRecord, scan: ScanResult) -> None:
        """Place the disc database's titles on the scan; for a film, its main title wins."""
        match = job.disc_match
        if not thediscdb.place_titles(match, job.titles):
            self._event(job, "warning", "TheDiscDB's titles don't match this scan, so its record isn't used")
            job.disc_match = None
            return
        main = next((t for t in match.titles if t.kind == "main" and t.scan_index is not None), None)
        if match.media == MediaKind.movie and main and job.main_title and main.scan_index != job.main_title.index:
            pick = next((t for t in scan.titles if t.index == main.scan_index), None)
            if pick:
                job.main_title = to_title_info(pick)
                self._event(job, "info", f"TheDiscDB says the film is title {pick.index} "
                                         f"({scoring.fmt_duration(pick.duration_s)}), so ripping that one")

    def _choose_mode(self, job: JobRecord, d: DriveRuntime, scan: ScanResult) -> str:
        """The drive's rip mode, unless this disc can't be backed up (then titles, saying why)."""
        if self.s.rip_mode_for(d.hw.id) != "backup":
            return "titles"
        worker = self.hub.workers.get(d.remote[0]) if d.remote else None
        why = None
        if job.disc.kind not in (DiscKind.bluray, DiscKind.uhd):
            why = "MakeMKV backs up Blu-ray and UHD discs only"
        elif d.remote and not (worker and worker.shared_scratch):
            why = "the drive's worker doesn't share the scratch folder (a backup is a whole folder, not one file)"
        else:
            job.disc_index = scan.disc_index(d.hw.device, job.disc.label or scan.volume_name)
            if job.disc_index is None:
                why = "MakeMKV's number for the drive isn't in the scan"
        if why:
            self._event(job, "warning", f"Ripping titles instead of a full backup: {why}")
            return "titles"
        return "backup"

    def disc_facts(self, job: JobRecord) -> DiscFacts:
        label = parse_label(job.disc.label)
        names = []
        bd = clean_bdmt_title(job.disc.name)
        if bd and bd == bd.upper() and any(c.isalpha() for c in bd):
            # A label-style name (DVDs report the volume label), parsed like one.
            bd = parse_label(bd).title or None
        if bd:
            names.append(bd)
        if label.title and label.title not in names:
            names.append(label.title)
        mt = job.main_title
        return DiscFacts(
            label=job.disc.label, names=names, label_year=label.year, extended=label.extended,
            main_duration_s=mt.duration_s if mt else None,
            pal=bool(mt and mt.framerate == "25"), kind=job.disc.kind,
        )

    async def _identify(self, job_id: int, cached: tuple[Candidate, str] | None = None,
                        use_cache: bool = True) -> None:
        job = self.db.get_job(job_id)
        if job.media == MediaKind.tv:
            await self._identify_tv(job_id)
            return
        self._set_state(job, JobState.identifying, "Identifying disc")
        facts = self.disc_facts(job)
        if cached and use_cache:
            cand, when = cached
            cand = cand.model_copy(update={
                "source": "cache", "score": 1.0,
                "evidence": [Evidence(ok=True, text=f"Recognised from the local disc cache (confirmed {when[:10]})")],
            })
            job.candidates = [cand]
            await self._settle_identity(job, cand, "cache")
            return
        candidates: list[Candidate] = []
        queries = facts.names + [q for q in parse_label(job.disc.label).queries if q not in facts.names]
        searches = [n for n in providers.enabled(self.s, "movie") if n != "thediscdb"]
        found = await self._catalogue_lookup(job) if searches else None
        if found and found.names:
            # The web's titles go first; the label's own reading stays as a fallback.
            facts.names = found.names + [n for n in facts.names if n not in found.names]
            queries = found.names[:2] + [q for q in queries if q not in found.names][:1]
        match = job.disc_match if job.disc_match and job.disc_match.media == MediaKind.movie \
            and "thediscdb" in providers.enabled(self.s, "movie") else None
        if match:
            candidates.append(await films.from_disc_match(self.s, match))
        if not searches:
            if not match:
                self._event(job, "warning", "No TMDB API key is set, so identification needs a human"
                            if not self.s.secret("tmdb_api_key") else
                            "No film search is switched on (Settings → Metadata providers)")
        elif not queries:
            self._event(job, "warning", "The disc label is generic; nothing to search for")
        else:
            got, notes = await films.search(self.s, queries, facts.label_year)
            films.merge(candidates, got)
            for level, note in notes:
                self._event(job, level, note)
        candidates = scoring.rank(candidates, facts)
        if match:
            candidates = films.vouch(candidates, match, thediscdb.summary(match))
        films.cross_check(candidates)
        if found and found.names:
            candidates = catalogue.weigh(candidates, found)
        job = self.db.get_job(job_id)  # the rip task may have saved meanwhile
        job.candidates = candidates[:8]
        best = candidates[0] if candidates else None
        if best and not best.excluded and best.score >= self.s.auto_confirm_threshold:
            await self._settle_identity(job, best, "auto")
        else:
            if best:
                self._event(job, "info", f"Best match {best.title} ({best.year}) scored {best.score:.2f}, "
                                         f"below {self.s.auto_confirm_threshold:.2f}: needs review")
            self._set_state(job, JobState.review, "Waiting for review")

    async def _use_preset(self, job_id: int) -> bool:
        """Identify from what the user said before the scan finished (False to identify as usual)."""
        job = self.db.get_job(job_id)
        p = job.preset
        self._presets[job.id] = job.preset = None
        if p.media == MediaKind.tv:
            if job.mode == "backup":
                self._event(job, "warning", f"You said this is {p.show.name}, but a full disc backup has no "
                                            "episodes to map, so it's identified as usual")
                self._save(job)
                return False
            return await self._use_tv_preset(job, p)
        if job.media == MediaKind.tv:
            job.media, job.tv = MediaKind.movie, None
        job.candidates = [p.candidate]
        self._set_state(job, JobState.identifying, "Identifying from what you said before the scan finished")
        await self._settle_identity(job, p.candidate, "user")
        return True

    async def set_preset(self, job_id: int, preset: Preset | None) -> JobRecord:
        """The user says what the disc is before its scan is done (None forgets it again)."""
        job = self.db.get_job(job_id)
        if job is None:
            raise ValueError("no such job")
        early = job.state in (JobState.detected, JobState.scanning) or (
            job.state == JobState.failed and not job.titles)  # a failed scan, kept for the retry
        if not early:
            # Scanned meanwhile, so the same as picking it in Review, when it is the same kind of disc.
            if preset and preset.media == job.media and job.state in (
                    JobState.identifying, JobState.review, JobState.ripping, JobState.clash):
                if preset.media == MediaKind.movie:
                    return await self.confirm(job_id, preset.candidate)
                return await self.tv_propose(job_id, preset.show.tmdb_id, preset.season, preset.disc_number,
                                             preset.start_episode, False)
            raise ValueError("the scan has finished, so identify the disc in Review")
        self._presets[job_id] = job.preset = preset
        if preset:
            self._event(job, "info", f"You said this disc is {preset_label(preset)}: used once the scan is done")
        else:
            self._event(job, "info", "Forgot what you said this disc is, so it will be identified automatically")
        self._save(job)
        return job

    async def _catalogue_lookup(self, job: JobRecord) -> catalogue.Lookup | None:
        """Look a catalogue-number label ("EO51710") up on the web, when it is one."""
        number = catalogue.number(job.disc.label)
        if not number or (job.disc.name and catalogue.number(job.disc.name) is None):
            return None  # a real title on the disc (bdmt) beats a web search
        if self.s.catalogue_search == "off":
            self._event(job, "info", f"The label looks like a catalogue number ({number}). Turn on catalogue "
                                     "lookup in Settings → Identification to look it up")
            return None
        try:
            found = await catalogue.lookup(self.s, number, job.disc.kind.value if job.disc.kind else None)
        except catalogue.CatalogueError as e:
            self._event(job, "warning", f"Catalogue number {number}: {e}")
            return None
        if found.names:
            named = ", ".join(f"“{t}” ({n})" for t, n in found.titles)
            self._event(job, "info", f"Catalogue number {number}: {found.pages} web page(s) mention it; "
                                     f"titles named: {named}")
        else:
            self._event(job, "info", f"Catalogue number {number}: no web page names a title "
                                     f"({found.pages} mention it)")
        return found

    async def _settle_identity(self, job: JobRecord, cand: Candidate, source: str) -> None:
        job.identity = cand
        job.identity_source = source
        job.problem = None
        # Planned path, shown while the rip runs; finalise writes the same one.
        job.destination = str(self._movie_dest(job))
        year = f" ({cand.year})" if cand.year else ""
        how = {"auto": f"Auto-confirmed (score {cand.score:.2f})", "user": "Confirmed",
               "cache": "Recognised"}[source]
        self._event(job, "identity", f"{how}: {cand.title}{year}")
        self._remember(job, cand)
        self._save(job)
        if source == "user":
            self._maybe_contribute(job.id)
        if self._check_clash(job):
            return
        if job.state == JobState.clash:  # the confirmed film isn't the one in the library
            job.clash = None
            await self._leave_clash(job, self.drives.get(job.drive_id))
            return
        await self._advance(job.id)

    # --- already in the library ----------------------------------------------------

    def _movie_destination(self, job: JobRecord, cand: Candidate) -> Path:
        return self._movie_dest(job, cand)

    def _movie_dest(self, job: JobRecord, cand: Candidate | None = None) -> Path:
        """The film's library file (a kept-both version if chosen), or its backup folder."""
        cand = cand or job.identity
        uhd = job.disc.kind == DiscKind.uhd
        if job.mode == "backup":
            return library.backup_path(self.s.disc_backups, cand.title, cand.year, uhd)
        folder, dest = library.movie_paths(self.s.movies, cand.title, cand.year, uhd)
        if job.clash and job.clash.decision == "keep_both" and cand is job.identity:
            return folder / job.clash.version if job.clash.version else self._version_path(job)
        return dest

    def _version_path(self, job: JobRecord, cand: Candidate | None = None) -> Path:
        """Keep both, as a second file in the film's folder, which Emby shows as another version
        ("Title (Year) - <label>.mkv"), labelled by edition, else source, else the disc label."""
        cand = cand or job.identity
        folder, _ = library.movie_paths(self.s.movies, cand.title, cand.year, job.disc.kind == DiscKind.uhd)
        tag = library.safe_name(version_label(job))
        n = 1
        while (path := folder / f"{folder.name} - {tag}{'' if n == 1 else f' {n}'}.mkv").exists():
            n += 1
        return path

    def _targets(self, job: JobRecord, proposed: bool = False) -> list[Path]:
        """Library files the job will write; with ``proposed``, those of the likely identity
        (a strong best guess, or a confident TV mapping) before anyone confirmed it."""
        if job.mode == "backup":
            return []  # a disc backup folder keeps its own "already exists" check at finalise
        if job.media == MediaKind.tv:
            plan = job.tv
            if not plan or not plan.show or not plan.mapping:
                return []
            if proposed and not job.identity and not (plan.label_placed or plan.continues_collection):
                return []
            return [p for _, p, _ in self._tv_targets(job)]
        if job.identity:
            return [self._movie_dest(job)]
        best = next((c for c in job.candidates if not c.excluded), None)
        if proposed and best and best.score >= PROPOSED_CLASH_SCORE:
            return [self._movie_dest(job, best)]
        return []

    def _clashing(self, job: JobRecord, proposed: bool = False) -> list[ClashFile]:
        decided = {f.path for f in job.clash.files} if job.clash and job.clash.decision else set()
        out = []
        for p in self._targets(job, proposed):
            if p.exists() and str(p) not in decided:
                st = p.stat()
                out.append(ClashFile(path=str(p), size_bytes=st.st_size,
                                     modified=datetime.fromtimestamp(st.st_mtime, UTC)))
        return out

    def _check_clash(self, job: JobRecord, proposed: bool = False) -> bool:
        """Put the job on hold if what it would write is already in the library."""
        files = self._clashing(job, proposed)
        if not files:
            return False
        job.clash = ClashInfo(files=files, proposed=proposed and job.identity is None)
        if job.media == MediaKind.movie:
            cand = job.identity or next((c for c in job.candidates if not c.excluded), None)
            if cand:
                job.clash.version = None
                job.clash.keep_both_name = self._version_path(job, cand).name
        names = ", ".join(Path(f.path).name for f in files[:3]) + (f" and {len(files) - 3} more" if len(files) > 3
                                                                    else "")
        self._set_state(job, JobState.clash, f"Already in the library: {names}")
        self._spawn_notify(job, "exists")
        return True

    def _clash_before_rip(self, job: JobRecord) -> bool:
        """True if the rip must wait for a decision (the job is, or now goes, on hold)."""
        return job.state == JobState.clash or self._check_clash(job, proposed=True)

    async def resolve_clash(self, job_id: int, action: str) -> JobRecord:
        job = self.db.get_job(job_id)
        if job is None or job.state != JobState.clash or not job.clash:
            raise ValueError("job is not waiting for an already-in-the-library decision")
        d = self.drives.get(job.drive_id)
        tv = job.media == MediaKind.tv
        if action == "skip":
            self._event(job, "info", "Already in the library, so skipped")
            job = await self.cancel(job_id)
            if self.s.auto_eject and d and d.job_id == job_id and not d.rip:
                await self.eject(d.hw.id, quiet=True)
            return self.db.get_job(job_id)
        if action == "keep_both" and tv or action == "skip_episodes" and not tv:
            raise ValueError(f"“{action}” is for {'films' if action == 'keep_both' else 'TV discs'}")
        if action == "skip_episodes":
            clashing = {f.path for f in job.clash.files}
            skipped = [i for i, p, _ in self._tv_targets(job) if str(p) in clashing]
            for m in job.tv.mapping:
                if m.title_index in skipped:
                    m.role, m.episodes = TitleRole.skip, []
            job.clash = None
            self._event(job, "info", f"Already in the library, so skipping {len(skipped)} title(s), copying the rest")
        else:
            job.clash.decision = action
            if action == "keep_both" and job.identity:
                job.clash.version = self._version_path(job).name
                job.destination = str(Path(job.destination).parent / job.clash.version)
            self._event(job, "info", "Already in the library, so " + (
                "overwrite it" if action == "overwrite" else f"keep both, as {job.clash.version or 'a new version'}"))
        self._save(job)
        await self._leave_clash(job, d)
        return self.db.get_job(job_id)

    async def _leave_clash(self, job: JobRecord, d: DriveRuntime | None) -> None:
        if job.identity is None:
            self._set_state(job, JobState.review, "Waiting for review")
        elif job.rip.state == RipState.done:
            self._spawn_bg(self._finish(job.id), job.id)  # sets transcoding / finalising itself
        else:
            self._set_state(job, JobState.ripping,
                            "Ripping episodes" if job.media == MediaKind.tv else "Ripping main feature")
        if job.rip.state == RipState.pending and d and d.job_id == job.id and not d.rip:
            self._spawn(d, self._rip(job.id), job.id)
        if job.media == MediaKind.tv and not job.identity:
            await self._maybe_auto_confirm(job.id)

    def _remember(self, job: JobRecord, cand: Candidate) -> None:
        if job.media == MediaKind.tv:
            return  # a TV disc's identity is its episode mapping, not one title
        stored = cand.model_copy(update={"evidence": [], "score": 1.0})
        for fp, kind in ((job.content_fp, "content"), (job.scan_fp, "scan"),
                         (job.quick_fp, "quick")):
            if fp:
                self.db.remember(fp, kind, stored, job.id)

    async def _advance(self, job_id: int) -> None:
        """Move on once identity and rip are both settled."""
        async with self._lock(job_id):
            job = self.db.get_job(job_id)
            if job.state in TERMINAL_STATES and job.state != JobState.failed:
                return
            if job.state == JobState.clash:
                self._save(job)  # waiting for a decision; the rip (if any) carries on
                return
            if job.state == JobState.checking:
                return  # ejected to check (maybe while the rip finished), and the disc coming back decides
            if job.identity is None:
                if job.state != JobState.review:
                    self._set_state(job, JobState.review)
                return
            if job.rip.state == RipState.done:
                # Transcode (if on) and copy run apart from the drive, so it can take the next disc.
                if job.state not in (JobState.transcoding, JobState.finalising) and not self._bg.get(job.id):
                    self._spawn_bg(self._finish(job.id), job.id)
            elif job.rip.state in (RipState.pending, RipState.running):
                self._set_state(job, JobState.ripping,
                                "Backing up the disc" if job.mode == "backup" else
                                "Ripping episodes" if job.media == MediaKind.tv else "Ripping main feature")
            else:
                self._save(job)

    async def _rip(self, job_id: int) -> None:
        job = self.db.get_job(job_id)
        d = self.drives[job.drive_id]
        out_dir = self.scratch_dir(job)
        backup = job.mode == "backup"
        if backup:
            # The disc's size from blkid; the titles' total if blkid had none.
            expected = job.disc.size_bytes or sum(t.size_bytes for t in job.titles)
            what = "Backing up the whole disc to scratch (decrypted)"
            args = ["-r", "--progress=-same", "--decrypt", "backup", f"disc:{job.disc_index}", str(out_dir)]
        else:
            title_arg, expected, what = self._rip_args(job)
            args = ["-r", "--progress=-same", "mkv", f"dev:{d.hw.device}", title_arg, str(out_dir)]
        # A paused TV rip (Eject to check) carries on with the titles it hadn't finished.
        resume = [t for t in job.titles if t.index not in job.rip.files] if job.rip.paused else None
        runs = [args]
        if resume is not None:
            expected = sum(t.size_bytes for t in resume)
            what = f"Ripping the {len(resume)} unfinished titles to scratch ({len(job.rip.files)} kept)"
            runs = [[*args[:4], str(t.index), str(out_dir)] for t in resume]
        short = self._scratch_shortfall(job, expected)
        if short:
            job.rip.state = RipState.failed
            self._fail(job, Problem(code="scratch_full", message=short, actions=["retry", "eject"]))
            return
        if resume is None:
            shutil.rmtree(out_dir, ignore_errors=True)
            out_dir.mkdir(parents=True)
        job.rip.paused = False
        job.rip.state = RipState.running
        job.rip.all_titles = not backup and (title_arg == "all" or resume is not None)
        job.rip.progress = RipProgress(bytes_total=expected)
        self._event(job, "rip", what)
        self._save(job)
        tracker = ProgressTracker(bytes_total=expected, backup=backup)
        last_save = [0.0]

        def on_line(line: str) -> None:
            if tracker.feed(line):
                self._progress(job_id, tracker.snapshot(), last_save)

        home = self._home(d)
        d.rip = self._runner(d)
        log_path = self.work_dir(job) / "rip.log"
        started = time.monotonic()
        saved_ok = True
        try:
            for run_args in runs:  # one run, or one per unfinished title after a pause
                res = await d.rip.run(run_args, home, log_path, on_line=on_line)
                job = self.db.get_job(job_id)
                if job.state == JobState.cancelled or res.cancelled or job.rip.paused:
                    return
                rip_log = parse_info(log_path.read_text(encoding="utf-8", errors="replace"))
                if res.returncode != 0:
                    break
                if not backup:
                    saved_ok = saved_ok and any(m.code == MSG_COPY_COMPLETE for m in rip_log.messages)
        finally:
            d.rip = None
        job = self.db.get_job(job_id)
        if job.state == JobState.failed:  # disc removed, already reported
            return
        if backup:
            saved_ok = rip_log.backup_done
            files = [out_dir] if (out_dir / "BDMV").is_dir() else []
        else:
            files = sorted(out_dir.glob("*.mkv"), key=lambda p: p.stat().st_size, reverse=True)
        if res.returncode != 0 or not files or not saved_ok:
            job.rip.state = RipState.failed
            last = next((m.text for m in reversed(rip_log.messages)), "no output")
            # Why the run ended, when it wasn't MakeMKV's doing (its worker stopped answering,
            # its token was revoked), because MakeMKV's last line would only say what it was doing.
            if res.error:
                last = res.error
            diag = diagnose(rip_log.messages)
            what = "backup" if backup else "rip"
            self._fail(job, Problem(code="rip_failed",
                                    message=f"MakeMKV {what} failed. {diag.cause}" if diag else
                                    f"MakeMKV {what} failed: {last}",
                                    hint=diag and diag.hint, detail=diag.detail if diag else last,
                                    actions=["retry", "eject"]))
            return
        job.rip.state = RipState.done
        job.rip.seconds = time.monotonic() - started
        if backup:
            job.rip.file = str(out_dir)
            job.rip.size_bytes = library.tree_size(out_dir)
            self._event(job, "rip", f"Backup complete ({gib(job.rip.size_bytes)})")
        elif job.rip.all_titles:
            job.rip.files = self._collect_files(job, out_dir)
            main = job.rip.files.get(job.main_title.index) if job.main_title else None
            job.rip.file = main
            job.rip.size_bytes = sum(p.stat().st_size for p in files)
            total = sum(Path(f).stat().st_size for f in job.rip.files.values())
            self._event(job, "rip", f"Rip complete: {len(job.rip.files)} titles ({gib(total)})")
        else:
            job.rip.file = str(files[0])
            job.rip.size_bytes = files[0].stat().st_size
            self._event(job, "rip", f"Rip complete: {files[0].name} ({gib(job.rip.size_bytes)})")
        job.rip.progress = tracker.snapshot().model_copy(update={"fraction": 1.0, "bytes_done": expected,
                                                                 "phase": "Ripped", "eta_s": 0})
        if job.tv:
            self._check_plan(job)
        self._save(job)
        if self.s.auto_eject:
            await self.eject(d.hw.id, quiet=True)
        if job.media == MediaKind.tv:
            await self._after_tv_rip(job_id)
        await self._advance(job_id)

    def _scratch_shortfall(self, job: JobRecord, expected: int) -> str | None:
        """Why this rip won't fit in scratch, or None.

        Needs the rip plus a margin, plus room for a transcoded copy when
        transcoding is on, after what rips already running will still write.
        """
        try:
            free = shutil.disk_usage(self.s.scratch_dir).free
        except OSError:
            return None
        transcoded = self.s.transcode_enabled and job.mode != "backup"
        need = int(expected * SCRATCH_MARGIN) + (int(expected * TRANSCODE_ROOM) if transcoded else 0)
        promised = 0
        for other in self.db.list_jobs([JobState.review.value, JobState.ripping.value, JobState.identifying.value]):
            if other.id != job.id and other.rip.state == RipState.running and other.rip.progress:
                p = other.rip.progress
                promised += max(0, p.bytes_total - p.bytes_done)
        if free - promised >= need:
            return None
        return (f"Not enough scratch space for this rip. It needs about {gib(need)}, and "
                f"{gib(max(free - promised, 0))} is free" + (" after the rips already running" if promised else "")
                + f" ({self.s.scratch_dir}). Free some space, then retry.")

    def _progress(self, job_id: int, snap: RipProgress, last_save: list[float]) -> None:
        loop = asyncio.get_running_loop()
        t = loop.time()
        if t - self._last_progress.get(job_id, 0) < PROGRESS_PUBLISH_S and snap.fraction < 1:
            return
        self._last_progress[job_id] = t
        self.bus.publish(RipProgressEvent(job_id=job_id, progress=snap))
        if t - last_save[0] >= PROGRESS_SAVE_S:
            last_save[0] = t
            job = self.db.get_job(job_id)
            if job:
                job.rip.progress = snap
                self.db.save_job(job)

    def _copy_progress(self, job_id: int, label: str) -> library.CopyProgress:
        """A ``library`` copy callback that puts the copy, then the check that reads it back,
        on the job's progress bar. Called from the copying thread."""
        loop = asyncio.get_running_loop()
        started = loop.time()

        def on_copy(done: int, total: int, verifying: bool) -> None:
            rate = done / max(loop.time() - started, 1e-6)
            snap = RipProgress(phase="Verifying the copy" if verifying else label, fraction=done / total,
                               bytes_done=done // 2, bytes_total=total // 2, bytes_per_s=rate,
                               eta_s=(total - done) / rate if rate else None)
            loop.call_soon_threadsafe(self._progress, job_id, snap, [0.0])
        return on_copy

    async def _finalise(self, job: JobRecord) -> None:
        dest = self._movie_dest(job)
        folder = dest.parent
        overwrite = bool(job.clash and job.clash.decision == "overwrite"
                         and str(dest) in {f.path for f in job.clash.files})
        # The check at identification may be long ago, so never overwrite a file that appeared since.
        if not overwrite and self._check_clash(job):
            return
        self._set_state(job, JobState.finalising, f"Copying to {dest}")
        self._set_step(job, "Copying to library", self._copy_detail(job, dest.name))
        src = Path(job.rip.file)
        try:
            library.check_library(*self.s.output_root("movie"))
        except library.LibraryError as e:
            self._fail(job, Problem(code="library_missing", message=str(e), actions=["retry"]))
            return
        total = src.stat().st_size
        on_copy = self._copy_progress(job.id, "Copying to library")
        try:
            sha = await asyncio.to_thread(library.copy_verified, src, dest, on_copy, overwrite)
        except FileExistsError:  # appeared during the copy
            self._check_clash(self.db.get_job(job.id))
            return
        except (OSError, library.VerifyError) as e:
            self._fail(job, Problem(code="copy_failed", message=f"Copy to library failed: {e}",
                                    actions=["retry"]))
            return
        job = self.db.get_job(job.id)
        self._event(job, "finalise", f"Copied and verified ({gib(total)}, sha256 {sha[:12]}…)")
        originals = self._originals(job)
        if originals:
            self._set_step(job, "Copying the kept original", "Transcoding was set to keep the rip as well")
            await self._copy_originals(job, [(o, dest) for o in originals.values()])
        job.destination = str(dest)
        work = self.work_dir(job)
        try:
            library.write_disc_folder(folder, self._disc_json(job, dest, sha), work / "scan.log",
                                      work / "structure")
        except OSError as e:
            self._event(job, "warning", f"Could not write .disc folder: {e}")
        shutil.rmtree(self.scratch_dir(job), ignore_errors=True)
        job.rip.file = None
        await self._refresh_servers(job, [str(dest)])
        job.problem = None
        self._set_state(job, JobState.done, f"Done: {dest.name}")
        self._spawn_notify(job, "done")

    async def _refresh_servers(self, job: JobRecord, paths: list[str]) -> None:
        """Tell Emby, Jellyfin and Plex (those set up) about the new files. A failure is
        only a warning, because the files are already in the library."""
        servers = [integrations.SERVERS[s] for s in integrations.configured_servers(self.s)]
        if not servers:
            return
        files = "file" if len(paths) == 1 else "files"
        self._set_step(job, f"Refreshing {integrations.names(servers)}",
                       f"Asking {integrations.names(servers)} to scan the new {files}")
        for name, err in await integrations.refresh_servers(self.s, paths):
            if err:
                self._event(job, "warning", err)
            else:
                self._event(job, "finalise", f"{name} library refresh requested")

    async def _finalise_backup(self, job: JobRecord) -> None:
        cand = job.identity
        dest = self._movie_destination(job, cand)
        self._set_state(job, JobState.finalising, f"Copying the backup to {dest}")
        self._set_step(job, "Copying backup to library", f"{dest.name} (full disc backup, not transcoded)")
        src = Path(job.rip.file)
        try:
            library.check_library(*self.s.output_root("backup"))
        except library.LibraryError as e:
            self._fail(job, Problem(code="library_missing", message=str(e), actions=["retry"]))
            return
        exists = Problem(code="destination_exists",
                         message=f"{dest} already exists; remove it or identify the disc differently",
                         actions=["retry", "identify_again"])
        if dest.exists():
            self._fail(job, exists)
            return
        on_copy = self._copy_progress(job.id, "Copying backup to library")
        try:
            hashes = await asyncio.to_thread(library.copy_tree_verified, src, dest, on_copy)
        except FileExistsError:
            self._fail(job, exists)
            return
        except (OSError, library.VerifyError) as e:
            self._fail(job, Problem(code="copy_failed", message=f"Copy to library failed: {e}", actions=["retry"]))
            return
        job = self.db.get_job(job.id)
        total = library.tree_size(dest)
        self._event(job, "finalise", f"Copied and verified {len(hashes)} files ({gib(total)})")
        job.destination = str(dest)
        work = self.work_dir(job)
        info = self._disc_json(job, dest, "")
        info["titles"] = None
        info["backup"] = {"decrypted": True, "files": hashes}
        try:
            library.write_disc_folder(dest, info, work / "scan.log", None)
        except OSError as e:
            self._event(job, "warning", f"Could not write .disc folder: {e}")
        shutil.rmtree(self.scratch_dir(job), ignore_errors=True)
        job.rip.file = None
        job.problem = None
        self._set_state(job, JobState.done, f"Done: {dest.name}")
        self._spawn_notify(job, "done")

    def _disc_json(self, job: JobRecord, dest: Path, sha: str) -> dict:
        d = self.drives.get(job.drive_id)
        c = job.identity
        mt = job.main_title
        return {
            "schema": 1,
            "label": job.disc.label,
            "disc_name": job.disc.name,
            "kind": job.disc.kind,
            "volume_uuid": job.disc.volume_uuid,
            "size_bytes": job.disc.size_bytes,
            "fingerprints": {"content": job.content_fp, "scan": job.scan_fp,
                             "quick": job.quick_fp},
            "drive": {"id": job.drive_id, "device": job.device,
                      "model": f"{d.hw.vendor} {d.hw.model}" if d else None,
                      "name": self.s.drive_name(job.drive_id),
                      "serial": d.hw.serial if d else None},
            "ripped_at": now().isoformat(),
            "makemkv_version": job.makemkv_version,
            "ripshelf_version": __version__,
            "tmdb_id": c.tmdb_id,
            "title": c.title,
            "year": c.year,
            "identity_source": job.identity_source,
            "titles": {str(mt.index) if mt else "main": {
                "file": dest.name, "sha256": sha, "duration_s": mt.duration_s if mt else None,
                "role": "main_feature"}},
            "skipped_duplicates": job.duplicates,
            "transcode": job.transcode.model_dump() if job.transcode else None,
        }

    # --- notifications ----------------------------------------------------------

    def _spawn_notify(self, job: JobRecord, kind: str) -> None:
        if not integrations.notifications_enabled(self.s) or kind in job.notified:
            return
        job.notified.append(kind)
        self.db.save_job(job)
        name = job.identity.title if job.identity else (job.disc.name or job.disc.label or "Disc")
        where = f"/?job={job.id}"
        if kind == "disc":
            note = integrations.Note(kind, f"Disc inserted: {job.disc.label or 'no label'}",
                                     f"In {self.short_drive_label(job.drive_id, job.device)}", where)
        elif kind == "review":
            best = job.candidates[0] if job.candidates else None
            if job.media == MediaKind.tv and job.tv:
                eps = sum(1 for m in job.tv.mapping if m.role == TitleRole.episode)
                show = job.tv.show.name if job.tv.show else "show not found"
                msg = f"TV disc: {show}, {eps} episode titles to check"
            else:
                msg = f"Best guess: {best.title} ({best.year}), score {best.score:.2f}" if best else "No candidates found"
            note = integrations.Note(kind, f"Needs review: {name}", msg, f"/review?job={job.id}", "Review it")
        elif kind == "exists":
            files = job.clash.files if job.clash else []
            note = integrations.Note(kind, f"Already in library: {name}",
                                     "\n".join(Path(f.path).name for f in files[:5]) or "",
                                     f"/review?job={job.id}", "Decide")
        elif kind == "done":
            note = integrations.Note(kind, f"Ripped: {name}", job.destination or "", f"/finished?job={job.id}")
        else:
            msg = job.problem.message if job.problem else "Failed"
            note = integrations.Note(kind, f"Failed: {name}", msg, where)
        self.notifier.send(note)

    def _notify_setup(self, problems: list[SetupProblem]) -> None:
        """One notification per setup problem when it appears (again after it was fixed)."""
        keys = {f"{p.code}:{p.drive_id or ''}" for p in problems}
        for p in problems:
            key = f"{p.code}:{p.drive_id or ''}"
            if key not in self._setup_notified and p.code not in QUIET_SETUP_CODES:
                self.notifier.send(integrations.Note("setup", "Setup problem", p.message, "/settings",
                                                     "Open Settings"))
        self._setup_notified = keys

    # --- user actions -------------------------------------------------------------

    def delete_jobs(self, ids: list[int]) -> list[int]:
        """Remove finished, failed or cancelled jobs from the history.

        Their logs and leftover scratch files go too. Library files are never touched,
        and the disc cache keeps the identity, so the same disc is still recognised.
        """
        jobs = [self.db.get_job(i) for i in dict.fromkeys(ids)]
        missing = [i for i, j in zip(dict.fromkeys(ids), jobs, strict=True) if j is None]
        if missing:
            raise KeyError(f"no job {missing[0]}")
        busy = [j.id for j in jobs if j.state not in TERMINAL_STATES
                or self._bg.get(j.id)]
        if busy:
            raise ValueError(f"job {busy[0]} is still running; cancel it first")
        gone = [j.id for j in jobs]
        self.db.delete_jobs(gone)
        for j in jobs:
            shutil.rmtree(self.work_dir(j), ignore_errors=True)
            shutil.rmtree(self.scratch_dir(j), ignore_errors=True)
            self._locks.pop(j.id, None)
        for d in self.drives.values():
            if d.job_id in gone:
                d.job_id = None
                self.publish_drive(d)
        self.bus.publish(JobsDeleted(ids=gone))
        self.publish_status()
        return gone

    def redetect(self) -> None:
        """Look for drives again (one plugged in since start-up), without touching running jobs."""
        self.discover()
        for d in self.drives.values():
            self.publish_drive(d)
        self.publish_status()

    async def confirm(self, job_id: int, cand: Candidate) -> JobRecord:
        job = self.db.get_job(job_id)
        if job is None or job.state not in (JobState.review, JobState.identifying, JobState.ripping,
                                            JobState.failed, JobState.clash):
            raise ValueError("job is not waiting for an identity")
        if job.state == JobState.failed and job.problem and job.problem.code not in (
                "destination_exists", "copy_failed"):
            raise ValueError("job has failed; retry it first")
        cand = cand.model_copy(update={"source": cand.source if cand.source != "cache" else "tmdb"})
        if job.state == JobState.failed:
            job.problem = None
            self._set_state(job, JobState.identifying, "Re-identified by hand")
        await self._settle_identity(job, cand, "user")
        return self.db.get_job(job_id)

    async def movie_search(self, query: str, year: int | None) -> list[Candidate]:
        """Films for ``query`` from the film searches in Settings, unranked (no disc to compare against)."""
        if not any(n != "thediscdb" for n in providers.enabled(self.s, "movie")):
            raise ValueError("No TMDB API key set" if not self.s.secret("tmdb_api_key")
                             else "No film search is switched on (Settings → Metadata providers)")
        found, notes = await films.search(self.s, [query], year)
        if not found and any(level == "warning" for level, _ in notes):
            raise ValueError(next(n for level, n in notes if level == "warning"))
        return found

    async def search(self, job_id: int, query: str, year: int | None) -> list[Candidate]:
        job = self.db.get_job(job_id)
        if job is None:
            raise ValueError("no such job")
        found = await self.movie_search(query, year)
        facts = self.disc_facts(job)
        facts.names = [query, *facts.names]
        if year:
            facts.label_year = year
        ranked = scoring.rank(found, facts)
        job.candidates = ranked[:8]
        films.cross_check(ranked)
        self._event(job, "info", f"Searched for “{query}”{f' ({year})' if year else ''}: {len(ranked)} results")
        self._save(job)
        return job.candidates

    async def identify_again(self, job_id: int) -> JobRecord:
        job = self.db.get_job(job_id)
        if job is None or job.state in (JobState.done, JobState.cancelled, JobState.finalising, JobState.checking):
            raise ValueError("cannot re-identify this job now")
        if job.state == JobState.failed and job.rip.state != RipState.done:
            raise ValueError("retry the job first")
        job.identity = None
        job.identity_source = None
        job.destination = None
        job.problem = None
        job.clash = None
        if job.state == JobState.failed:
            job.state = JobState.review  # rip is done; only the identity is open
        self._save(job)
        d = self.drives[job.drive_id]
        self._spawn(d, self._identify_then_rip(job_id), job_id)
        return job

    async def _identify_then_rip(self, job_id: int) -> None:
        """Identify (again); a rip held back by a clash starts once nothing clashes."""
        await self._identify(job_id, use_cache=False)
        job = self.db.get_job(job_id)
        d = self.drives.get(job.drive_id)
        if job.rip.state == RipState.pending and job.state not in TERMINAL_STATES and d and not d.rip \
                and not self._clash_before_rip(job):
            self._spawn(d, self._rip(job.id), job.id)

    async def cancel(self, job_id: int) -> JobRecord:
        job = self.db.get_job(job_id)
        if job is None or job.state not in ACTIVE_STATES:
            raise ValueError("job is not active")
        if job.state == JobState.finalising:
            # The copy runs in a thread that cannot be interrupted safely.
            raise ValueError("the job is copying to the library; wait for it to finish")
        d = self.drives.get(job.drive_id)
        if d and d.job_id == job_id:
            self._stop_drive_work(d)
        self._cancel_bg(job_id)
        if job.rip.state in (RipState.pending, RipState.running):
            job.rip.state = RipState.cancelled
        self._set_state(job, JobState.cancelled, "Cancelled")
        shutil.rmtree(self.scratch_dir(job), ignore_errors=True)
        return job

    async def retry(self, job_id: int) -> JobRecord:
        job = self.db.get_job(job_id)
        if job is None or job.state != JobState.failed:
            raise ValueError("only failed jobs can be retried")
        d = self.drives[job.drive_id]
        job.problem = None
        ripped = job.rip.files.values() if job.rip.all_titles else ([job.rip.file] if job.rip.file else [])
        if job.rip.state == RipState.done and ripped and all(Path(f).exists() for f in ripped):
            if job.identity:
                if job.transcode and job.transcode.state == "running":
                    job.transcode.state = "pending"  # interrupted, so start the transcode again
                self._save(job)
                self._spawn_bg(self._finish(job.id), job.id)
            else:
                self._set_state(job, JobState.identifying, "Retrying identification")
                self._spawn(d, self._identify(job_id), job_id)
            return job
        if self._busy_other(d, job_id):
            raise ValueError("the drive is busy with another job")
        self._check_enabled(d)
        if await self._drive_status(d) != DriveStatus.disc:
            raise ValueError("put the disc back in the drive first")
        job.rip = type(job.rip)()
        d.job_id = job.id
        self._set_state(job, JobState.detected, "Retrying")
        self._spawn(d, self._pipeline(job_id), job_id)
        return job

    def _busy_other(self, d: DriveRuntime, job_id: int) -> bool:
        return bool(d.tasks) or (d.job_id not in (None, job_id) and self._busy(d))

    def _held(self, d: DriveRuntime) -> bool:
        """No jobs on this drive, because it's disabled, the setup isn't finished yet (a fresh
        install would otherwise rip the discs already in its drives before it's set up), or
        MakeMKV's licence hasn't been accepted (here, or on the drive's worker)."""
        return (d.hw.id in self.s.disabled_drives or not self.s.setup_complete or self.s.makemkv_eula_pending()
                or self._worker_eula_pending(d))

    def _worker_eula_pending(self, d: DriveRuntime) -> bool:
        w = self.hub.workers.get(d.remote[0]) if d.remote else None
        return bool(w and w.makemkv_eula_pending)

    def drives_toggled(self) -> None:
        """Settings changed which drives are enabled (or the setup was finished). A drive
        that's no longer held has its disc read again (it was only looked at)."""
        for d in self.drives.values():
            if not self._held(d) and d.label is not None:
                d.identified, d.label = False, None
            elif self._held(d) and d.label is None and not self._busy(d):
                d.identified = False  # read its label to show on the card (no job is made)
            self.publish_drive(d)

    async def check_disc(self, job_id: int, pause_rip: bool = False) -> JobRecord:
        """"Eject to check" opens the tray without losing the job. The same disc going back
        carries on where it was; another disc (or none) ends the job.

        A TV disc can be checked mid-rip (``pause_rip``). The rip stops, the titles already
        ripped are kept, and only the unfinished ones are ripped once the disc is back.
        """
        job = self.db.get_job(job_id)
        if job is None or job.state not in (JobState.review, JobState.clash):
            raise ValueError("only a job waiting for review or a decision can be ejected to check")
        d = self.drives.get(job.drive_id)
        if not d or d.job_id != job.id:
            raise ValueError("this job's disc is no longer in its drive")
        if d.scan:
            raise ValueError("MakeMKV is scanning the disc, so wait for it")
        if d.rip:
            if job.media != MediaKind.tv or job.mode == "backup" or not job.rip.all_titles:
                raise ValueError("MakeMKV is using the drive, so wait for it or cancel the job")
            if not pause_rip:
                raise ValueError("the rip is running. It pauses, keeping the titles already ripped")
        self._check_enabled(d)
        if d.rip:
            await self._pause_rip(job, d)
            job = self.db.get_job(job_id)
        before = job.state
        job.checking_from = before
        self._set_state(job, JobState.checking, "Ejected to check the disc. Put it back to carry on")
        try:
            await self.eject(d.hw.id)
        except ValueError:
            job = self.db.get_job(job_id)
            job.checking_from = None
            self._set_state(job, before, "The tray didn't open")
            raise
        return self.db.get_job(job_id)

    async def _pause_rip(self, job: JobRecord, d: DriveRuntime) -> None:
        """Stop a TV rip, keeping its finished titles. MakeMKV rips the titles one after
        another, so the newest file is the one it was writing, and that one is redone."""
        d.rip.cancel()
        for _ in range(200):
            if d.rip is None:
                break
            await asyncio.sleep(0.05)
        job = self.db.get_job(job.id)
        out_dir = self.scratch_dir(job)
        files = self._collect_files(job, out_dir)
        if files:
            writing = max(files, key=lambda i: Path(files[i]).stat().st_mtime)
            Path(files.pop(writing)).unlink(missing_ok=True)
        for p in out_dir.glob("*.mkv"):  # anything else half-written
            if str(p) not in files.values():
                p.unlink(missing_ok=True)
        job.rip.files = files
        job.rip.paused = True
        job.rip.state = RipState.pending
        job.rip.all_titles = True
        self._event(job, "rip", f"Rip paused: {len(files)} of {len(job.titles)} titles kept")
        self._save(job)

    async def _disc_back(self, d: DriveRuntime, job: JobRecord) -> None:
        ident = await self._be(d).disc_identity(d.hw.device)
        d.identified = True
        qfp = discfs.quick_fingerprint(ident.label, ident.uuid, ident.size_bytes)
        d.handled_fp = qfp
        back = job.checking_from or JobState.review
        job.checking_from = None
        if qfp and qfp == job.quick_fp:
            self._set_state(job, back, "Same disc back, so carrying on")
            if job.rip.paused and job.rip.state == RipState.pending:
                self._spawn(d, self._rip(job.id), job.id)
            return
        # Another disc, so the old job ends, and this one is handled like any new disc.
        job.rip.state = RipState.cancelled if job.rip.state in (RipState.pending, RipState.running) else job.rip.state
        self._set_state(job, JobState.cancelled, "Cancelled because a different disc went in")
        shutil.rmtree(self.scratch_dir(job), ignore_errors=True)
        self._start_job(d, ident, qfp)

    def _check_enabled(self, d: DriveRuntime) -> None:
        if d.hw.id in self.s.disabled_drives:
            raise ValueError("this drive is disabled in Settings, so ripshelf leaves it alone")
        if not self.s.setup_complete:
            raise ValueError("finish the setup first (or skip it), because discs wait until then")
        if self.s.makemkv_eula_pending():
            raise ValueError(EULA_PENDING)

    async def rescan_drive(self, drive_id: str) -> JobRecord:
        """The drive card's "Retry scan". Retries its failed job, or starts a fresh one."""
        d = self.drives[drive_id]
        self._check_enabled(d)
        job = self.db.get_job(d.job_id) if d.job_id else None
        if job and job.state == JobState.failed:
            return await self.retry(job.id)
        if self._busy(d):
            raise ValueError("the drive is busy")
        if await self._drive_status(d) != DriveStatus.disc:
            raise ValueError("there is no disc in the drive")
        ident = await self._be(d).disc_identity(d.hw.device)
        qfp = discfs.quick_fingerprint(ident.label, ident.uuid, ident.size_bytes)
        d.handled_fp = qfp
        return self._start_job(d, ident, qfp)

    async def close_tray(self, drive_id: str) -> None:
        d = self.drives[drive_id]
        self._check_enabled(d)
        if d.scan or d.rip:
            raise ValueError("the drive is busy")
        try:
            await asyncio.to_thread(self._be(d).close_tray, d.hw.device)
        except OSError as e:
            raise ValueError(f"the tray didn't close ({e.strerror or e}). Slot-load and laptop drives "
                             "can't close themselves, so push it shut") from None

    async def eject(self, drive_id: str, quiet: bool = False) -> None:
        d = self.drives[drive_id]
        if not quiet:
            self._check_enabled(d)
        if d.scan or d.rip:
            raise ValueError("the drive is busy; cancel the job first")
        try:
            await asyncio.to_thread(self._be(d).eject, d.hw.device)
        except OSError as e:
            if not quiet:
                raise ValueError(f"eject failed: {e}") from None
            log.warning("eject %s failed: %s", d.hw.device, e)
            return
        # Whatever goes in next is a new disc, even if it is reinserted before
        # the poller has seen the tray empty.
        d.handled_fp = None
        d.identified = False
        # Until the drive reports the tray open, a "disc" status is the one from before
        # the eject (a worker's last heartbeat), so don't start a job on it.
        d.ejecting_until = time.monotonic() + EJECT_SETTLE_S

    # --- views ------------------------------------------------------------------

    def drive_view(self, d: DriveRuntime) -> Drive:
        job = self.db.get_job(d.job_id) if d.job_id else None
        problem = job.problem if job and job.state == JobState.failed and d.status == DriveStatus.disc else None
        return Drive(
            id=d.hw.id, device=d.hw.device, sg_device=d.hw.sg_device, vendor=d.hw.vendor,
            model=d.hw.model, revision=d.hw.revision, serial=d.hw.serial,
            capabilities=d.hw.capabilities, status=d.status,
            enabled=d.hw.id not in self.s.disabled_drives, job_id=d.job_id, problem=problem, disc_label=d.label,
            worker=self.hub.workers[d.remote[0]].name if d.remote and d.remote[0] in self.hub.workers else None,
            name=self.s.drive_name(d.hw.id), display_name=self.drive_label(d.hw.id),
        )

    def drive_label(self, drive_id: str) -> str:
        """What to call a drive. Its name, else vendor and model."""
        if name := self.s.drive_name(drive_id):
            return name
        d = self.drives.get(drive_id)
        return d.hw.label if d else (self.db.drive_data(drive_id).get("model") or drive_id)

    def short_drive_label(self, drive_id: str, fallback: str | None = None) -> str:
        """Its name, else just the model number ("BDR-S11JX"), for one-line messages."""
        if name := self.s.drive_name(drive_id):
            return name
        d = self.drives.get(drive_id)
        return d.hw.model.split()[-1] if d and d.hw.model.split() else (fallback or drive_id)

    def set_drive_name(self, drive_id: str, name: str) -> None:
        names = {k: v for k, v in self.s.drive_names.items() if k != drive_id}
        if name:
            names[drive_id] = name
        self.s.save_overrides({"drive_names": names})
        if d := self.drives.get(drive_id):
            self.publish_drive(d)
        self.publish_status()  # setup problems name the drive

    def drive_stats(self) -> list[DriveStats]:
        """All-time totals, with attached drives first (zeros if new), then drives no longer attached."""
        known = {st.drive_id: st for st in self.db.drive_stats()}
        out = []
        for d in self.drives.values():
            st = known.pop(d.hw.id, None) or DriveStats(
                drive_id=d.hw.id, connected=True, discs_ripped=0, data_bytes=0, rip_seconds=0, timed_bytes=0,
                failed=0, cancelled=0, skipped=0, by_kind={}, movies=0, tv=0, backups=0)
            st.connected = True
            st.model = f"{d.hw.vendor} {d.hw.model}".strip()
            out.append(st)
        out += known.values()
        for st in out:
            st.name = self.s.drive_name(st.drive_id)
        return out

    def publish_drive(self, d: DriveRuntime) -> None:
        self.bus.publish(DriveUpdated(drive=self.drive_view(d)))

    def job_detail(self, job_id: int) -> JobDetail | None:
        job = self.db.get_job(job_id)
        if not job:
            return None
        return JobDetail(**job.public().model_dump(), events=self.db.events(job_id), titles=job.titles)

    def log_tail(self, job_id: int, kind: str, lines: int = 40) -> list[str]:
        path = self.s.jobs_dir / str(job_id) / f"{kind}.log"
        if not path.exists():
            return []
        tail: deque[str] = deque(maxlen=lines)
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.startswith(("PRGV:", "PRGC:", "PRGT:", "DRV:")):
                    tail.append(humanise_log_line(line.rstrip("\n")))
        return list(tail)

    def transcode_log(self, job_id: int, lines: int = 40) -> list[str]:
        """The ffmpeg logs of a job's titles, in title order, each under a heading."""
        def idx(p: Path) -> int:
            n = p.stem.removeprefix("transcode-")
            return int(n) if n.isdigit() else -1

        out: list[str] = []
        for path in sorted((self.s.jobs_dir / str(job_id)).glob("transcode-*.log"), key=idx):
            out.append(f"── Title {idx(path)} ──")
            out += path.read_text(encoding="utf-8", errors="replace").splitlines()
        return out[-lines:]

    def problems(self) -> list[SetupProblem]:
        out = []
        if self.s.makemkv_eula_pending():
            out.append(SetupProblem(code="makemkv_eula", message=EULA_PENDING + ". "
                                    "Read it at /api/setup/makemkv/eula. If you accept it, add this under environment: "
                                    "in compose.yaml (or RIPSHELF_ACCEPT_MAKEMKV_EULA=yes in an env file) and restart",
                                    command='RIPSHELF_ACCEPT_MAKEMKV_EULA: "yes"'))
        if names := sorted(w.name for w in self.hub.workers.values() if w.online and w.makemkv_eula_pending):
            out.append(SetupProblem(
                code="worker_makemkv_eula",
                message=f"MakeMKV's licence (EULA) hasn't been accepted on {', '.join(names)}, so "
                        f"{'its' if len(names) == 1 else 'their'} drives wait. A worker runs its own MakeMKV, so "
                        "if you accept it, add this under environment: in each worker's compose.yaml "
                        "(or RIPSHELF_ACCEPT_MAKEMKV_EULA=yes in an env file) and restart it",
                command='RIPSHELF_ACCEPT_MAKEMKV_EULA: "yes"'))
        if not self.s.secret("tmdb_api_key"):
            out.append(SetupProblem(code="tmdb_key", message="No TMDB API key, so TV discs can't be identified, and "
                                                              "films only when TheDiscDB knows the disc"))
        for root, what in self.s.output_roots():
            try:
                library.check_library(root, what)
            except library.LibraryError as e:
                msg = str(e)
                out.append(SetupProblem(code="library_missing", message=msg[:1].upper() + msg[1:]))
        backup = self.backup_problem()
        if backup:
            out.append(SetupProblem(code="backup_dir", message=backup))
        if integrations.notifications_enabled(self.s) and not self.s.public_url:
            out.append(SetupProblem(code="public_url", message="No UI address set (Settings → Notifications), so "
                                                                "notifications can't link to the job"))
        if self.java_missing:
            out.append(SetupProblem(code="java_missing", message="MakeMKV has no Java runtime, needed for some " + (
                "Blu-rays (BD-J). Install one, then restart ripshelf"
                if WINDOWS else "Blu-rays (BD-J). The image should include one, so rebuild it"),
                command="winget install -e --id Microsoft.OpenJDK.21" if WINDOWS else None))
        # On Windows the MakeMKV app's own registration counts, because makemkvcon uses it.
        if not self.s.secret("makemkv_key") and not (WINDOWS and runner.registry_key()):
            out.append(SetupProblem(code="makemkv_key_unset",
                                    message="No MakeMKV key set, so MakeMKV uses whatever key it already has"))
        # The same problem on several drives (a bad key fails every scan) is listed once.
        failed: dict[tuple[str, str], list[str]] = {}
        for d in self.drives.values():
            job = self.db.get_job(d.job_id) if d.job_id else None
            if job and job.problem and job.problem.code in SETUP_CODES \
                    and job.state == JobState.failed:
                failed.setdefault((job.problem.code, job.problem.message), []).append(d.hw.id)
        for (code, message), ids in failed.items():
            where = ", ".join(self.short_drive_label(i) for i in ids) if len(self.drives) > 1 else ""
            out.append(SetupProblem(code=code, message=f"{message} ({where})" if where else message, drive_id=ids[0]))
        if any(code == "makemkv_key" for code, _ in failed):
            # "rejected the key" says more than "no key set here".
            out = [p for p in out if p.code != "makemkv_key_unset"]
        for d in self.drives.values():
            if Capability.uhd in d.hw.capabilities and not runner.has_private_data(self.s.makemkv_dir, d.hw.id):
                out.append(SetupProblem(
                    code="private_data_missing", drive_id=d.hw.id,
                    message=f"{self.drive_label(d.hw.id)}: no MakeMKV private data imported; UHD discs may "
                            "fail with an unknown volume key"))
        return out

    def status(self) -> Status:
        return Status(version=__version__, makemkv_version=self.makemkv_version,
                      fake_hardware=self.s.fake_hardware, problems=self.problems(),
                      review_count=self.db.count_state(JobState.review))

    def publish_status(self) -> None:
        st = self.status()
        if integrations.notifications_enabled(self.s):
            self._notify_setup(st.problems)
        self.bus.publish(StatusChanged(status=st))


def version_label(job: JobRecord) -> str:
    """An Emby version label for a second copy of a film."""
    p = parse_label(job.disc.label)
    words = " ".join([*p.dropped, job.disc.name or ""]).lower()
    if "director" in words or " dc" in f" {words}":
        return "Director's Cut"
    if "theatrical" in words:
        return "Theatrical"
    if p.extended or "extended" in words:
        return "Extended"
    source = {DiscKind.uhd: "2160p", DiscKind.bluray: "1080p", DiscKind.dvd: "DVD"}.get(job.disc.kind)
    return source or p.title or "Copy"


def preset_label(p: Preset) -> str:
    if p.media == MediaKind.movie:
        c = p.candidate
        return f"{c.title} ({c.year})" if c.year else c.title
    parts = [p.show.name]
    if p.season is not None:
        parts.append(f"season {p.season}")
    if p.disc_number:
        parts.append(f"disc {p.disc_number}")
    if p.start_episode:
        parts.append(f"from episode {p.start_episode}")
    return ", ".join(parts)


def to_title_info(t) -> TitleInfo:
    v = t.video
    return TitleInfo(
        index=t.index, duration_s=t.duration_s, size_bytes=t.size_bytes, chapters=t.chapters,
        source_file=t.source_file, video=v.codec if v else None,
        resolution=v.resolution if v else None, framerate=v.framerate if v else None,
        audio=[s.description or s.codec or "" for s in t.streams if s.kind == "audio"],
        subtitles=[s.lang_name or s.lang or "" for s in t.streams if s.kind == "subtitles"],
        segments_map=t.segments_map, output_file=t.output_file,
    )


def _span(seconds: int) -> str:
    if seconds % 3600 == 0:
        return f"{seconds // 3600} h"
    return f"{seconds // 60} min" if seconds % 60 == 0 else f"{seconds} s"


def gib(n: int) -> str:
    """Sizes as MakeMKV prints them (binary units, labelled GB/MB)."""
    if n >= 1024**3:
        return f"{n / 1024**3:.1f} GB"
    return f"{n / 1024**2:.0f} MB"


def humanise_log_line(line: str) -> str:
    from .makemkv.parse import split_line

    parsed = split_line(line)
    if parsed and parsed[0] == "MSG" and len(parsed[1]) > 3:
        return parsed[1][3]
    return line
