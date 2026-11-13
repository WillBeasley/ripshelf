"""Identifying a finished job again (mixed into ``jobs.Manager``).

A disc confirmed as the wrong film or show (auto-confirmed, most likely) goes back to
Review with its files left where they are in the library. Once the right one is
confirmed, the files move to its names. Nothing is ripped or transcoded again.

    done -> review (Re-identify) -> finalising (moving the files) -> done
                                 -> done (kept as it was)

Every move is planned before the first one is made, nothing is moved over a file that's
already there, and each move is saved as made, so a move that fails half way (a share
gone) is retried from where it stopped, or the moves made are put back.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from . import library
from .db import now
from .models import Candidate, JobState, MediaKind, Problem, Refile, RefileMove, TitleRole
from .tv import naming

if TYPE_CHECKING:
    from .db import JobRecord

log = logging.getLogger(__name__)


def filed_as(job: JobRecord) -> str:
    """The job's identity as the history says it: "Moonlit Orchard (2004)", or the show and season."""
    if job.media == MediaKind.tv and job.tv and job.tv.show:
        show = job.tv.show
        name = f"{show.name} ({show.year})" if show.year else show.name
        return name if job.tv.compilation or job.tv.season is None else f"{name}, season {job.tv.season}"
    c = job.identity
    if not c:
        return "nothing"
    return f"{c.title} ({c.year})" if c.year else c.title


def _same(a: Candidate, b: Candidate | None) -> bool:
    if b is None:
        return False
    if a.tmdb_id and b.tmdb_id:
        return a.tmdb_id == b.tmdb_id
    return (a.title, a.year) == (b.title, b.year)


def _original(p: Path) -> Path:
    """Where transcoding's kept original of a library file is ("<name> - original.mkv")."""
    return p.with_name(f"{p.stem} - original{p.suffix}")


class RefileMixin:
    # --- starting --------------------------------------------------------------------

    async def reidentify(self, job_id: int) -> JobRecord:
        """The user's "Wrong title?": back to Review, its files left where they are until
        the right identity is confirmed."""
        job = self.db.get_job(job_id)
        if job is None:
            raise ValueError("no such job")
        if job.state in (JobState.transcoding, JobState.finalising):
            raise ValueError("the job is still transcoding or copying, so wait for it to finish")
        if job.state != JobState.done or self._bg_running(job.id):
            raise ValueError("only a finished job can be identified again")
        if not job.identity or not job.destination:
            raise ValueError("this job didn't put anything in the library")
        if job.mode == "backup":
            if not Path(job.destination).is_dir():
                raise ValueError(f"{job.destination} isn't in the library any more")
        files = self._filed_titles(job)
        if job.mode != "backup" and not files:
            raise ValueError("none of this job's files are in the library any more")
        wrong = job.identity
        job.refile = Refile(title=filed_as(job), identity=wrong, identity_source=job.identity_source,
                            destination=job.destination, tv=job.tv.model_copy(deep=True) if job.tv else None,
                            candidates=job.candidates, finished_at=job.finished_at, files=files)
        job.identity = job.identity_source = None
        job.clash = job.problem = None
        # The wrong film goes last, so the card offers the others first.
        job.candidates = sorted(job.candidates, key=lambda c: _same(c, wrong))
        if job.tv:
            job.tv.user_edited = True  # never confirmed again without a review
            for sp in job.tv.splits:
                sp.edited = True  # its parts are the files in the library, so the cuts stay
        if "review" not in job.notified:
            job.notified.append("review")  # asked for, so no "needs review" notification
        n = len(files) or 1
        self._event(job, "info", f"Identifying again. It was filed as {job.refile.title}; its "
                                 f"{'file stays' if n == 1 else f'{n} files stay'} where "
                                 f"{'it is' if n == 1 else 'they are'} until the right one is confirmed")
        self._set_state(job, JobState.review, "Waiting for review (identifying a finished job again)")
        return job

    def _filed_titles(self, job: JobRecord) -> dict[int, str]:
        """What the job put in the library and is still there: title -> file."""
        out: dict[int, str] = {}
        if job.mode == "backup":
            return out
        if job.media == MediaKind.tv:
            if not job.tv or not job.tv.show:
                return out
            folder = Path(job.destination)
            meta = naming.disc_meta_folder(self.s.tv, job.tv.show, job.disc.label, job.id)
            try:
                titles = json.loads((meta / "disc.json").read_text(encoding="utf-8")).get("titles") or {}
                out = {int(i): str(folder / t["file"]) for i, t in titles.items()
                       if isinstance(t, dict) and t.get("file")}
            except (OSError, ValueError, KeyError):
                # No record of what was copied, so where the mapping says they went.
                out = {i: str(p) for i, p, _ in self._tv_targets(job)}
        else:
            main = job.main_title.index if job.main_title else 0
            out = {main: job.destination, **job.filed_extras}
        return {i: p for i, p in out.items() if Path(p).is_file()}

    def _refiling(self, job: JobRecord | None, what: str) -> None:
        """Refuse what a job being identified again can't do: it has no disc or rip any more."""
        if job and job.refile:
            raise ValueError(f"this job is finished and is being identified again, so it can't {what}")

    # --- confirmed: moving the files -------------------------------------------------

    async def _refile(self, job: JobRecord) -> JobRecord:
        """Called by confirm and tv_confirm once the new identity is set on ``job`` (not saved
        yet). Plans the moves, refusing (nothing changed) if one can't be made, then moves."""
        if job.media == MediaKind.movie:
            job.destination = str(self._movie_dest(job))
        root, what = self.s.output_root("backup" if job.mode == "backup" else job.media.value)
        try:
            library.check_library(root, what)
        except library.LibraryError as e:
            raise ValueError(str(e)) from None
        moves = self._refile_moves(job)
        job.refile.moves = moves
        job.problem = None
        self._event(job, "identity", f"Re-identified: {job.refile.title} → {filed_as(job)}")
        n = len(moves)
        self._set_state(job, JobState.finalising, (f"Moving {n} file{'s' if n != 1 else ''} to {job.destination}"
                                                   if n else "Nothing to move"))
        self._spawn_bg(self._refile_run(job.id), job.id)
        return job

    def _refile_moves(self, job: JobRecord) -> list[RefileMove]:
        """Every file (or, for a disc backup, the folder) to move, in the order to move them."""
        r = job.refile
        pairs: list[tuple[Path, Path]] = []
        scans_from = scans_to = None
        if job.mode == "backup":
            pairs.append((Path(r.destination), self._movie_dest(job)))  # its scans/ and .disc/ go with it
        elif job.media == MediaKind.tv:
            old_folder, new_folder = Path(r.destination), naming.show_folder(self.s.tv, job.tv.show)
            targets = self._tv_targets(job)
            missing = [i for i, _, _ in targets if i not in r.files]
            if missing:
                raise ValueError(f"title{'s' if len(missing) > 1 else ''} {', '.join(map(str, missing))} "
                                 f"{'weren' if len(missing) > 1 else 'wasn'}'t put in the library the first "
                                 "time, so make it Skip (ripping the disc again is the way to add it)")
            for i, dst, eps in targets:
                src = Path(r.files[i])
                if eps is None:
                    # An extra keeps its name, which comes from the disc, not the show (and is
                    # elsewhere, an episode of another show the box gave as a bonus, stays).
                    try:
                        dst = new_folder / src.relative_to(old_folder)
                    except ValueError:
                        dst = src
                pairs += [(src, dst), (_original(src), _original(dst))]
            old_meta = naming.disc_meta_folder(self.s.tv, r.tv.show, job.disc.label, job.id)
            new_meta = naming.disc_meta_folder(self.s.tv, job.tv.show, job.disc.label, job.id)
            pairs += [(p, new_meta / p.relative_to(old_meta)) for p in sorted(old_meta.rglob("*")) if p.is_file()]
            scans_from, scans_to = old_folder / "scans", new_folder / "scans"
        else:
            old_main, new_main = Path(r.destination), Path(job.destination)
            old_folder, new_folder = old_main.parent, new_main.parent
            pairs += [(old_main, new_main), (_original(old_main), _original(new_main))]
            for p in sorted(r.files.values()):
                if p != r.destination:
                    try:
                        pairs.append((Path(p), new_folder / Path(p).relative_to(old_folder)))
                    except ValueError:
                        pass  # filed somewhere else, so left there
            meta = old_folder / ".disc"
            pairs += [(p, new_folder / ".disc" / p.relative_to(meta)) for p in sorted(meta.rglob("*")) if p.is_file()]
            scans_from, scans_to = old_folder / "scans", new_folder / "scans"
        if scans_from and scans_from != scans_to:
            taken: set[Path] = set()
            for sc in job.scans:
                p = Path(sc.library_path) if sc.library_path else None
                if p and p.parent == scans_from and p.is_file():
                    dst = library.free_path(scans_to / p.name, taken)  # a picture may take a "(2)"
                    taken.add(dst)
                    pairs.append((p, dst))
        pairs = [(a, b) for a, b in pairs if a != b and a.exists()]
        dsts = [b for _, b in pairs]
        if len(set(dsts)) != len(dsts):
            twice = next(b for b in dsts if dsts.count(b) > 1)
            raise ValueError(f"two titles would both be {twice.name}")
        srcs = {a for a, _ in pairs}
        taken = [b for b in dsts if b.exists() and b not in srcs]
        if taken:
            names = ", ".join(str(b) for b in taken[:3]) + (f" and {len(taken) - 3} more" if len(taken) > 3 else "")
            raise ValueError(f"already in the library: {names}. Move or delete "
                             f"{'it' if len(taken) == 1 else 'them'} first, or pick another title")
        # A file going where another of these is now (episodes swapped) goes by a
        # temporary name, once the one there has moved.
        first, direct, last = [], [], []
        for a, b in pairs:
            if b in srcs:
                tmp = a.with_name(f".{a.name}.refile-{job.id}")
                first.append(RefileMove(src=str(a), dst=str(tmp)))
                last.append(RefileMove(src=str(tmp), dst=str(b)))
            else:
                direct.append(RefileMove(src=str(a), dst=str(b)))
        return first + direct + last

    async def _refile_run(self, job_id: int) -> None:
        job = self.db.get_job(job_id)
        n = len(job.refile.moves)
        self._set_step(job, "Moving files", f"{n} file{'s' if n != 1 else ''} to {job.destination}")
        for mv in job.refile.moves:
            if mv.done:
                continue
            src, dst = Path(mv.src), Path(mv.dst)
            if not src.exists() and dst.exists():
                pass  # moved before ripshelf stopped, but not saved as moved
            else:
                try:
                    await asyncio.to_thread(library.move_path, src, dst)
                except OSError as e:
                    self._fail(job, Problem(
                        code="refile_failed", message=f"Moving {src} to {dst} failed: {e}",
                        hint=f"Retry carries on from this file. Keep it as {job.refile.title} puts back the "
                             "files already moved.",
                        actions=["retry"]))
                    return
            mv.done = True
            self._save(job)
        await self._refiled(job_id)

    async def _refiled(self, job_id: int) -> None:
        """Every file has moved: the job's records, the disc's .disc record, the collections
        and the disc cache follow, and the old folders go if nothing else is in them."""
        job = self.db.get_job(job_id)
        r = job.refile
        moved = {mv.src: mv.dst for mv in r.moves}

        def new(p: str) -> str:
            while p in moved:
                p = moved[p]
            return p

        if job.media == MediaKind.movie and job.mode != "backup":
            job.filed_extras = {i: new(p) for i, p in job.filed_extras.items()}
        self._save(job)
        job = self.db.get_job(job_id)
        for sc in job.scans:
            if sc.library_path:
                sc.library_path = new(sc.library_path)
        self._save(job)
        job = self.db.get_job(job_id)
        try:
            await asyncio.to_thread(self._rewrite_disc_record, job, new)
        except OSError as e:
            self._event(job, "warning", f"Could not update the .disc record: {e}")
        if job.media == MediaKind.tv:
            self.forget_discs([job.id])
            self._add_to_collection(job)
            left_out = sorted(i for i in r.files if i not in {m.title_index for m in job.tv.mapping
                                                               if m.role in (TitleRole.episode, TitleRole.extra)})
            for i in left_out:
                self._event(job, "warning", f"Title {i} isn't mapped any more, so its file stays at {r.files[i]}")
        else:
            self._remember(job, job.identity)  # so the same disc is recognised as this from now on
        whole = job.mode == "backup" or job.media == MediaKind.tv  # the destination is the folder
        old_home = Path(r.destination) if whole else Path(r.destination).parent
        new_home = Path(job.destination) if whole else Path(job.destination).parent
        for folder in sorted({old_home} | {Path(mv.src).parent for mv in r.moves},
                             key=lambda p: len(p.parts), reverse=True):
            root = self._root_of(folder)
            left = await asyncio.to_thread(library.prune_folder, folder, root) if root else []
            if left and folder == old_home != new_home:
                self._event(job, "warning", f"{folder} still has {len(left)} file{'s' if len(left) != 1 else ''} "
                                            "that aren't this job's, so it stays")
        n = sum(1 for mv in r.moves if not Path(mv.dst).name.startswith("."))
        self._event(job, "finalise", f"Moved {n} file{'s' if n != 1 else ''} from {r.destination} to "
                                     f"{job.destination}")
        c = job.contribution
        if c and c.status == "sent":
            self._event(job, "warning", f"TheDiscDB was sent this disc as {r.title}, so correct it there"
                        + (f": {c.contribute_url}" if c.contribute_url else ""))
        elif c and c.release_id and c.status != "sending":
            self._assign_release(job, c.new_release)  # the new title's release
        await self._refresh_servers(job, [job.destination, r.destination])
        job.refile = None
        job.problem = None
        self._save(job)
        await self.file_scans(job, filing=True)  # added while it was in review
        job = self.db.get_job(job.id)
        self._set_state(job, JobState.done, f"Done: {filed_as(job)}")
        job.finished_at = r.finished_at or job.finished_at  # it finished when it was ripped
        self._save(job)
        self.db.record_stats(job)

    def _rewrite_disc_record(self, job: JobRecord, new) -> None:
        """The .disc/disc.json beside the files says what the disc is, so it says the new title."""
        r = job.refile
        if job.media == MediaKind.tv:
            path = naming.disc_meta_folder(self.s.tv, job.tv.show, job.disc.label, job.id) / "disc.json"
            old = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            folder = naming.show_folder(self.s.tv, job.tv.show)
            sums = {int(i): t.get("sha256") for i, t in (old.get("titles") or {}).items() if isinstance(t, dict)}
            copied = {}
            for i, _, eps in self._tv_targets(job):
                if i not in r.files:
                    continue
                p = Path(new(r.files[i]))
                try:
                    file = p.relative_to(folder).as_posix()  # as the first copy wrote it
                except ValueError:
                    file = str(p)
                copied[i] = {"file": file, "sha256": sums.get(i),
                             "episodes": [f"S{e.season:02d}E{e.episode:02d}" for e in eps] if eps else [],
                             "names": [e.name for e in eps] if eps else []}
            record = {**self._tv_disc_json(job, copied), "ripped_at": old.get("ripped_at")}
        else:
            dest = Path(job.destination)
            path = (dest if job.mode == "backup" else dest.parent) / ".disc" / "disc.json"
            if not path.is_file():
                return
            record = json.loads(path.read_text(encoding="utf-8"))
            c = job.identity
            record.update(tmdb_id=c.tmdb_id, title=c.title, year=c.year, identity_source=job.identity_source)
            for t in (record.get("titles") or {}).values():
                if isinstance(t, dict) and t.get("role") == "main_feature":
                    t["file"] = dest.name
            old = record
        record["reidentified"] = [*(old.get("reidentified") or []), {"at": now().isoformat(), "was": r.title}]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(".disc.json.partial")
        tmp.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        library.replace(tmp, path)

    def _root_of(self, folder: Path) -> Path | None:
        """The output root (films, TV, disc backups) a library folder is in."""
        for root in (self.s.movies, self.s.tv, self.s.disc_backups):
            try:
                folder.resolve().relative_to(root.resolve())
                return root
            except ValueError:
                continue
        return None

    # --- retrying, or keeping it as it was ----------------------------------------------

    async def _refile_retry(self, job: JobRecord) -> JobRecord:
        job.problem = None
        self._set_state(job, JobState.finalising, "Retrying the move")
        self._spawn_bg(self._refile_run(job.id), job.id)
        return job

    async def keep_identity(self, job_id: int) -> JobRecord:
        """Not identified again after all: the files already moved go back, and the job is
        as it was."""
        job = self.db.get_job(job_id)
        if job is None or not job.refile:
            raise ValueError("this job isn't being identified again")
        if job.state not in (JobState.review, JobState.failed) or self._bg_running(job.id):
            raise ValueError("the files are being moved, so wait for that to finish")
        r = job.refile
        back = [mv for mv in r.moves if mv.done]
        for mv in reversed(back):
            src, dst = Path(mv.src), Path(mv.dst)
            try:
                await asyncio.to_thread(library.move_path, dst, src)
            except OSError as e:
                raise ValueError(f"could not move {dst} back to {src}: {e}") from None
            mv.done = False
            self._save(job)
        for folder in sorted({Path(mv.dst).parent for mv in back}, key=lambda p: len(p.parts), reverse=True):
            if root := self._root_of(folder):
                await asyncio.to_thread(library.prune_folder, folder, root)
        job.identity, job.identity_source, job.destination = r.identity, r.identity_source, r.destination
        job.candidates, job.tv = r.candidates, r.tv
        job.refile = job.problem = None
        self._event(job, "info", f"Kept as {r.title}" + (f"; {len(back)} moved files put back" if back else ""))
        self._set_state(job, JobState.done, f"Done: {r.title}")
        job.finished_at = r.finished_at or job.finished_at
        self._save(job)
        self.db.record_stats(job)
        return job
