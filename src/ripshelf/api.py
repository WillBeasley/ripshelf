"""HTTP API, server-sent events and the static UI."""

from __future__ import annotations

import asyncio
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Annotated, Any, get_args

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from . import applog, art, checks, integrations, proc, providers, scanjobs, system
from .bus import next_item
from .config import EDITABLE, FOLDERS, REQUIRED_FOLDERS, SECRETS, Settings
from .identify import catalogue
from .makemkv import files as mkfiles
from .makemkv import runner
from .models import (
    Candidate,
    ClashRequest,
    Collection,
    CollectionDetail,
    ConfirmRequest,
    ContributionExtrasRequest,
    ContributionReleaseRequest,
    ContributionView,
    CoverFromUrlRequest,
    CoverScan,
    CoverSide,
    DeleteJobsRequest,
    DeleteJobsResult,
    Drive,
    DriveNameUpdate,
    DriveStats,
    Filesystem,
    FolderRequest,
    FolderState,
    Hardware,
    ImportPrivateDataResult,
    Job,
    JobDetail,
    JobState,
    Listing,
    ListingView,
    LogPage,
    MakeMKVFile,
    MediaRequest,
    MediaServer,
    PendingScan,
    PendingScans,
    Preset,
    ProviderInfo,
    ReleaseDetails,
    ReleaseLookup,
    ReleaseLookupRequest,
    ScanKind,
    ScanMatch,
    ScannerInfo,
    ScannerRun,
    ScannerScanRequest,
    ScanSource,
    ScanSplit,
    ScanText,
    ScanUpdate,
    SearchRequest,
    ServerLibrariesRequest,
    ServerLibrary,
    SettingField,
    SettingsTestRequest,
    SettingsTestResult,
    SettingsUpdate,
    SettingsView,
    SetupTools,
    Show,
    Snapshots,
    Status,
    Tool,
    TvConfirmRequest,
    TvMatchTextRequest,
    TvProposeRequest,
    TvSplitRequest,
    WebResult,
    WebSearchRequest,
    setting_fields,
)
from .providers import contribute as contribute_provider
from .providers import release_info
from .scanner import escl, images
from .tv import split as tvsplit

SSE_HEARTBEAT_S = 15


def manager(request: Request):
    return request.app.state.manager


def settings(request: Request) -> Settings:
    return request.app.state.settings


def bad_request(e: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(e))


api = APIRouter(prefix="/api")


@api.get("/status", response_model=Status)
async def get_status(request: Request):
    return manager(request).status()


@api.get("/system/storage", response_model=list[Filesystem])
async def get_storage(request: Request):
    return await asyncio.to_thread(system.storage, settings(request))


@api.get("/system/hardware", response_model=Hardware)
async def get_hardware(request: Request, refresh: bool = False):
    return await request.app.state.hardware.get(settings(request), refresh)


@api.get("/drives", response_model=list[Drive])
async def list_drives(request: Request):
    m = manager(request)
    return [m.drive_view(d) for d in m.drives.values()]


@api.get("/drives/stats", response_model=list[DriveStats])
async def drive_stats(request: Request):
    """All-time totals per drive, including drives no longer attached."""
    return manager(request).drive_stats()


@api.post("/drives/redetect", response_model=list[Drive])
async def redetect_drives(request: Request):
    """Look for drives again, e.g. one plugged in after start-up."""
    m = manager(request)
    await asyncio.to_thread(m.redetect)
    return [m.drive_view(d) for d in m.drives.values()]


@api.put("/drives/{drive_id}/name", response_model=Drive | None)
async def set_drive_name(drive_id: str, body: DriveNameUpdate, request: Request):
    """Name a drive ("Top bay"); an empty name goes back to the model. Drives no
    longer attached (in the statistics) can be renamed too."""
    m = manager(request)
    if drive_id not in m.drives and not m.db.latest_job_for_drive(drive_id):
        raise HTTPException(404, "no such drive")
    m.set_drive_name(drive_id, body.name)
    return m.drive_view(m.drives[drive_id]) if drive_id in m.drives else None


@api.post("/drives/{drive_id}/eject", status_code=204)
async def eject(drive_id: str, request: Request):
    m = manager(request)
    if drive_id not in m.drives:
        raise HTTPException(404)
    try:
        await m.eject(drive_id)
    except ValueError as e:
        raise bad_request(e) from None


@api.post("/drives/{drive_id}/close", status_code=204)
async def close_tray(drive_id: str, request: Request):
    m = manager(request)
    if drive_id not in m.drives:
        raise HTTPException(404)
    try:
        await m.close_tray(drive_id)
    except ValueError as e:
        raise bad_request(e) from None


@api.post("/drives/{drive_id}/scan", response_model=Job)
async def rescan(drive_id: str, request: Request):
    m = manager(request)
    if drive_id not in m.drives:
        raise HTTPException(404)
    try:
        return (await m.rescan_drive(drive_id)).public()
    except ValueError as e:
        raise bad_request(e) from None


@api.get("/jobs", response_model=list[Job])
async def list_jobs(request: Request, state: Annotated[list[JobState] | None, Query()] = None,
                    limit: int = 100):
    jobs = manager(request).db.list_jobs([s.value for s in state] if state else None, limit)
    return [j.public() for j in jobs]


@api.post("/jobs/delete", response_model=DeleteJobsResult)
async def delete_jobs(body: DeleteJobsRequest, request: Request):
    """Remove finished, failed or cancelled jobs from the history, and their discs from the
    TV collections; their library files too only with ``delete_files``."""
    try:
        deleted, files = manager(request).delete_jobs(body.ids, body.delete_files)
        return DeleteJobsResult(deleted=deleted, files_deleted=files)
    except KeyError as e:
        raise HTTPException(404, e.args[0]) from None
    except ValueError as e:
        raise bad_request(e) from None


@api.delete("/jobs/{job_id}", status_code=204)
async def delete_job(job_id: int, request: Request):
    await delete_jobs(DeleteJobsRequest(ids=[job_id]), request)


@api.get("/logs", response_model=LogPage)
async def app_log(request: Request, after: int = 0, level: str = "INFO",
                  limit: Annotated[int, Query(ge=1, le=5000)] = 1000):
    """ripshelf's own log, newest last. Returns entries after ``after`` at ``level`` or above."""
    entries, last = applog.BUFFER.since(after, level, limit)
    path = applog.log_file(settings(request).data_dir)
    return LogPage(entries=entries, last_seq=last, file=str(path) if path.exists() else None)


@api.get("/logs/file", include_in_schema=False)
async def app_log_file(request: Request):
    path = applog.log_file(settings(request).data_dir)
    if not path.exists():
        raise HTTPException(404, "no log file")
    return FileResponse(path, media_type="text/plain", filename="ripshelf.log")


@api.get("/jobs/{job_id}", response_model=JobDetail)
async def get_job(job_id: int, request: Request):
    detail = manager(request).job_detail(job_id)
    if not detail:
        raise HTTPException(404)
    return detail


@api.get("/jobs/{job_id}/log", response_model=list[str])
async def job_log(job_id: int, request: Request, kind: str = "rip", lines: int = 40):
    if kind not in ("rip", "scan", "transcode"):
        raise HTTPException(400, "kind must be rip, scan or transcode")
    m = manager(request)
    if kind == "transcode":
        return m.transcode_log(job_id, min(lines, 500))
    out = m.log_tail(job_id, kind, min(lines, 500))
    if not out and kind == "rip":
        out = m.log_tail(job_id, "scan", min(lines, 500))
    return out


async def _job_action(request: Request, job_id: int, coro_fn) -> Job:
    try:
        job = await coro_fn(job_id)
    except ValueError as e:
        raise bad_request(e) from None
    return manager(request).db.get_job(job.id).public()


@api.post("/jobs/{job_id}/confirm", response_model=Job)
async def confirm(job_id: int, body: ConfirmRequest, request: Request):
    m = manager(request)
    return await _job_action(request, job_id, lambda i: m.confirm(i, body.candidate))


@api.post("/jobs/{job_id}/search", response_model=list[Candidate])
async def search(job_id: int, body: SearchRequest, request: Request):
    m = manager(request)
    if not m.db.get_job(job_id):
        raise HTTPException(404)
    try:
        return await m.search(job_id, body.query, body.year)
    except ValueError as e:
        raise bad_request(e) from None


@api.post("/jobs/{job_id}/clash", response_model=Job)
async def resolve_clash(job_id: int, body: ClashRequest, request: Request):
    """The disc is already in the library. Overwrite, keep both (films), skip the clashing episodes (TV), or skip."""
    m = manager(request)
    return await _job_action(request, job_id, lambda i: m.resolve_clash(i, body.action))


@api.post("/jobs/{job_id}/check-disc", response_model=Job)
async def check_disc(job_id: int, request: Request, pause_rip: bool = False):
    """Eject to check. Opens the tray without losing the job. ``pause_rip`` allows it during a
    TV rip, which stops and keeps the titles already ripped."""
    m = manager(request)
    return await _job_action(request, job_id, lambda i: m.check_disc(i, pause_rip))


@api.post("/jobs/{job_id}/retry", response_model=Job)
async def retry(job_id: int, request: Request):
    return await _job_action(request, job_id, manager(request).retry)


@api.post("/jobs/{job_id}/identify-again", response_model=Job)
async def identify_again(job_id: int, request: Request):
    return await _job_action(request, job_id, manager(request).identify_again)


@api.post("/jobs/{job_id}/cancel", response_model=Job)
async def cancel(job_id: int, request: Request):
    return await _job_action(request, job_id, manager(request).cancel)


@api.post("/jobs/{job_id}/media", response_model=Job)
async def set_media(job_id: int, body: MediaRequest, request: Request):
    m = manager(request)
    return await _job_action(request, job_id, lambda i: m.set_media(i, body.media))


@api.put("/jobs/{job_id}/preset", response_model=Job)
async def set_preset(job_id: int, body: Preset, request: Request):
    m = manager(request)
    return await _job_action(request, job_id, lambda i: m.set_preset(i, body))


@api.delete("/jobs/{job_id}/preset", response_model=Job)
async def clear_preset(job_id: int, request: Request):
    m = manager(request)
    return await _job_action(request, job_id, lambda i: m.set_preset(i, None))


@api.get("/jobs/{job_id}/thediscdb", response_model=ContributionView)
async def contribution(job_id: int, request: Request):
    """Sending this disc to TheDiscDB, whether it can be sent, and the exact JSON that would be posted."""
    view = manager(request).contribution_view(job_id)
    if not view:
        raise HTTPException(404)
    return view


@api.post("/jobs/{job_id}/thediscdb/reread", response_model=ContributionView)
async def contribution_reread(job_id: int, request: Request):
    """Read the disc again (it must be back in its drive) for the content hash TheDiscDB
    keys it by, when it couldn't be read as the disc went in."""
    return await _contribution_call(lambda: manager(request).reread_disc(job_id))


@api.post("/jobs/{job_id}/thediscdb/send", response_model=ContributionView)
async def contribution_send(job_id: int, request: Request):
    """Send (or send again) in the background; the job's ``contribution`` says how it went."""
    try:
        return await manager(request).contribute(job_id)
    except KeyError:
        raise HTTPException(404) from None
    except ValueError as e:
        raise bad_request(e) from None


@api.put("/jobs/{job_id}/thediscdb/extras", response_model=ContributionView)
async def contribution_extras(job_id: int, body: ContributionExtrasRequest, request: Request):
    """The extras to send, with their names. Nothing is sent before these are given."""
    try:
        return await manager(request).contribution_extras(job_id, body.extras)
    except KeyError:
        raise HTTPException(404) from None
    except ValueError as e:
        raise bad_request(e) from None


@api.post("/jobs/{job_id}/thediscdb/release", response_model=ContributionView)
async def contribution_release(job_id: int, body: ContributionReleaseRequest, request: Request):
    """Put the disc in a release of its own, or back with a recent one of the same title."""
    try:
        return await manager(request).contribution_release(job_id, body.new_release)
    except KeyError:
        raise HTTPException(404) from None
    except ValueError as e:
        raise bad_request(e) from None


async def _contribution_call(fn):
    try:
        return await fn()
    except KeyError:
        raise HTTPException(404) from None
    except ValueError as e:
        raise bad_request(e) from None


@api.put("/jobs/{job_id}/thediscdb/details", response_model=ContributionView)
async def contribution_details(job_id: int, body: ReleaseDetails, request: Request):
    """The release's UPC, ASIN, date, locale and region (shown when finishing on TheDiscDB)."""
    return await _contribution_call(lambda: manager(request).contribution_details(job_id, body))


@api.post("/jobs/{job_id}/thediscdb/lookup", response_model=ReleaseLookup)
async def contribution_lookup(job_id: int, body: ReleaseLookupRequest, request: Request):
    """Suggestions for the release's details, from the barcode or the title. Nothing is saved."""
    return await _contribution_call(lambda: manager(request).release_lookup(job_id, body.upc))


@api.get("/jobs/{job_id}/thediscdb/cover/{side}")
async def contribution_cover(job_id: int, side: CoverSide, request: Request):
    p = manager(request).cover_file(job_id, side)
    if not p:
        raise HTTPException(404)
    return FileResponse(p, media_type=contribute_provider.COVER_EXTS.get(p.suffix.lstrip("."), "image/jpeg"),
                        headers={"Cache-Control": "no-cache"})


@api.put("/jobs/{job_id}/thediscdb/cover/{side}", response_model=ContributionView)
async def contribution_cover_upload(job_id: int, side: CoverSide, request: Request,
                                    file: Annotated[UploadFile, File()]):
    """A photo or scan of the cover (JPEG, PNG or WebP, at most 20 MB), sent with the disc."""
    data = await file.read(release_info.MAX_COVER_BYTES + 1)
    return await _contribution_call(lambda: manager(request).set_cover(job_id, side, data))


@api.post("/jobs/{job_id}/thediscdb/cover/{side}/from-url", response_model=ContributionView)
async def contribution_cover_from_url(job_id: int, side: CoverSide, body: CoverFromUrlRequest, request: Request):
    """Use a product picture a lookup found as the cover."""
    return await _contribution_call(lambda: manager(request).cover_from_url(job_id, side, body.url))


@api.delete("/jobs/{job_id}/thediscdb/cover/{side}", response_model=ContributionView)
async def contribution_cover_delete(job_id: int, side: CoverSide, request: Request):
    return await _contribution_call(lambda: manager(request).delete_cover(job_id, side))


# --- scans of the box ------------------------------------------------------------------


@api.get("/jobs/{job_id}/scans", response_model=list[CoverScan])
async def scans(job_id: int, request: Request):
    job = manager(request).db.get_job(job_id)
    if not job:
        raise HTTPException(404)
    return job.scans


@api.post("/jobs/{job_id}/scans", response_model=CoverScan)
async def scan_upload(job_id: int, request: Request, file: Annotated[UploadFile, File()],
                      kind: Annotated[ScanKind, Form()], source: Annotated[ScanSource, Form()] = "upload",
                      page: Annotated[int | None, Form(ge=1, le=999)] = None):
    """A picture of the box (JPEG, PNG or WebP, at most 20 MB): uploaded, or a phone's photo."""
    if source == "scanner":
        raise HTTPException(400, "Use POST /jobs/{id}/scans/scanner to scan")
    data = await file.read(scanjobs.MAX_SCAN_BYTES + 1)
    return await _contribution_call(lambda: manager(request).add_scan(job_id, data, kind, source, page))


@api.post("/jobs/{job_id}/scans/scanner", response_model=ScannerRun)
async def scan_from_scanner(job_id: int, body: ScannerScanRequest, request: Request):
    """Scan from the network scanner, in the background: the job's ``scanner`` says how
    it's going, and the pages join ``scans`` as they arrive."""
    return await _contribution_call(lambda: manager(request).scanner_scan(job_id, body))


@api.delete("/jobs/{job_id}/scans/scanner", status_code=204)
async def scan_cancel(job_id: int, request: Request):
    """Stop the scan under way for the job, or clear the message of one that failed."""
    await _contribution_call(lambda: manager(request).cancel_scanner(job_id))


@api.get("/jobs/{job_id}/scans/{scan_id}")
async def scan_image(job_id: int, scan_id: str, request: Request):
    """The original file, as scanned or uploaded."""
    found = manager(request).scan_file(job_id, scan_id)
    if not found:
        raise HTTPException(404)
    return FileResponse(found[0], media_type=found[1], headers={"Cache-Control": "private, max-age=86400"})


@api.get("/jobs/{job_id}/scans/{scan_id}/thumb")
async def scan_thumb(job_id: int, scan_id: str, request: Request, large: bool = False):
    """A JPEG trimmed and turned as the scan says: small, or up to 1600 pixels with ``large``."""
    p = await manager(request).scan_thumb(job_id, scan_id, large)
    if not p:
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@api.patch("/jobs/{job_id}/scans/{scan_id}", response_model=CoverScan)
async def scan_update(job_id: int, scan_id: str, body: ScanUpdate, request: Request):
    """Change what the picture is of, its page, its turn or its trim."""
    return await _contribution_call(lambda: manager(request).update_scan(job_id, scan_id, body))


@api.delete("/jobs/{job_id}/scans/{scan_id}", status_code=204)
async def scan_delete(job_id: int, scan_id: str, request: Request):
    """Forget the picture (its copy in the library, if filed, stays)."""
    await _contribution_call(lambda: manager(request).delete_scan(job_id, scan_id))


@api.post("/jobs/{job_id}/scans/{scan_id}/covers", response_model=ContributionView)
async def scan_covers(job_id: int, scan_id: str, request: Request, body: ScanSplit | None = None):
    """Use the scan as TheDiscDB's covers. A whole outer sheet is cut where ``body`` says
    (the back on the left, the front on the right, unless swapped)."""
    return await _contribution_call(lambda: manager(request).scan_covers(job_id, scan_id, body))


@api.post("/jobs/{job_id}/scans/{scan_id}/vision", status_code=202)
async def scan_vision(job_id: int, scan_id: str, request: Request):
    """Read the scan with the vision model now, in the background (the scan's ``vision``
    says how it goes)."""
    _sync_call(lambda: manager(request).ask_vision(job_id, scan_id))
    return {"queued": True}


@api.get("/jobs/{job_id}/scans/{scan_id}/text", response_model=ScanText)
async def scan_text(job_id: int, scan_id: str, request: Request):
    """What was read on the scan: its lines of text, with their boxes, and its barcodes."""
    t = manager(request).scan_text(job_id, scan_id)
    if not t:
        raise HTTPException(404)
    return t


@api.get("/jobs/{job_id}/scan-match", response_model=ScanMatch | None)
async def scan_match(job_id: int, request: Request):
    """The box's listing matched to the disc's titles, made again from the job as it is now."""
    return _sync_call(lambda: manager(request).rematch(job_id))


@api.post("/jobs/{job_id}/extras/rip", response_model=Job)
async def rip_extras(job_id: int, request: Request):
    """Rip the film's extras the box names, while the disc is still in its drive. A
    finished job files them straight away; otherwise they're filed with the film."""
    return await _contribution_call(lambda: _public(manager(request).rip_film_extras(job_id)))


@api.post("/jobs/{job_id}/scan-match/use", response_model=Job)
async def use_scan(job_id: int, request: Request):
    """A TV disc in review: take the show, season and episodes the cover scan gives. It
    still needs confirming."""
    return await _contribution_call(lambda: _public(manager(request).use_scan(job_id)))


async def _public(coro):
    return (await coro).public()


@api.get("/jobs/{job_id}/listing", response_model=ListingView)
async def listing(job_id: int, request: Request):
    """What's on the disc, as read from the box: the reading, and the user's version of it."""
    return _sync_call(lambda: manager(request).listing_view(job_id))


@api.put("/jobs/{job_id}/listing", response_model=ListingView)
async def listing_save(job_id: int, body: Listing, request: Request):
    """Keep the user's listing. Reading the scans again never replaces it."""
    return _sync_call(lambda: manager(request).save_listing(job_id, body))


@api.delete("/jobs/{job_id}/listing/edits", response_model=ListingView)
async def listing_drop_edits(job_id: int, request: Request):
    """Drop the user's listing and go back to what was read."""
    return _sync_call(lambda: manager(request).drop_listing_edits(job_id))


@api.post("/jobs/{job_id}/listing/read", response_model=ListingView)
async def listing_read(job_id: int, request: Request):
    """Read every scan again, in the background (the job's ``listing`` says how it goes)."""
    m = manager(request)

    def go():
        if not settings(request).scan_read_text:
            raise ValueError("reading the text on scans is off (Settings → Scanner and covers)")
        view = m.listing_view(job_id)
        m.read_scans(job_id, again=True)
        return view
    return _sync_call(go)


def _sync_call(fn):
    try:
        return fn()
    except KeyError:
        raise HTTPException(404) from None
    except ValueError as e:
        raise bad_request(e) from None


# --- scans for a drive's next disc ------------------------------------------------------


@api.get("/drives/{drive_id}/pending-scans", response_model=PendingScans)
async def pending_scans(drive_id: str, request: Request):
    """Pictures of a box kept for the next disc in this drive (forgotten after a day)."""
    return _sync_call(lambda: manager(request).pending_scans(drive_id))


@api.post("/drives/{drive_id}/pending-scans", response_model=PendingScan)
async def pending_upload(drive_id: str, request: Request, file: Annotated[UploadFile, File()],
                         kind: Annotated[ScanKind, Form()], source: Annotated[ScanSource, Form()] = "upload"):
    if source == "scanner":
        raise HTTPException(400, "Use POST /drives/{id}/pending-scans/scanner to scan")
    data = await file.read(scanjobs.MAX_SCAN_BYTES + 1)
    return await _contribution_call(lambda: manager(request).add_pending(drive_id, data, kind, source))


@api.post("/drives/{drive_id}/pending-scans/scanner", response_model=ScannerRun)
async def pending_scan(drive_id: str, body: ScannerScanRequest, request: Request):
    """Scan from the network scanner for the drive's next disc."""
    return await _contribution_call(lambda: manager(request).scan_pending(drive_id, body))


@api.delete("/drives/{drive_id}/pending-scans/scanner", status_code=204)
async def pending_scan_cancel(drive_id: str, request: Request):
    _sync_call(lambda: manager(request).clear_pending_run(drive_id))


@api.delete("/drives/{drive_id}/pending-scans/{scan_id}", status_code=204)
async def pending_delete(drive_id: str, scan_id: str, request: Request):
    await _contribution_call(lambda: manager(request).delete_pending(drive_id, scan_id))


@api.get("/pending-scans/{scan_id}")
async def pending_image(scan_id: str, request: Request, thumb: bool = False):
    """The picture (or a small JPEG of it, trimmed, with ``thumb``)."""
    m = manager(request)
    found = m.pending_file(scan_id)
    if not found:
        raise HTTPException(404)
    if not thumb:
        return FileResponse(found[0], media_type=found[1])
    p = found[0].with_suffix(".thumb.jpg")
    if not p.is_file():
        sc = next(x for x in m._pending_index().scans if x.id == scan_id)
        data = found[0].read_bytes()
        p.write_bytes(await asyncio.to_thread(lambda: images.thumbnail(images.render(data, sc.crop, sc.rotate))))
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@api.get("/scanner", response_model=ScannerInfo)
async def scanner_info(request: Request):
    """The network scanner's model and state, and whether it's scanning for a job."""
    return await manager(request).scanner_info()


@api.get("/movies/search", response_model=list[Candidate])
async def movie_search(request: Request, q: Annotated[str, Query(min_length=1, max_length=200)],
                       year: int | None = None):
    try:
        return await manager(request).movie_search(q, year)
    except ValueError as e:
        raise bad_request(e) from None


@api.post("/web-search", response_model=list[WebResult])
async def web_search(body: WebSearchRequest, request: Request):
    """Search the web (SearXNG) by hand, for a disc nothing identified."""
    try:
        return await catalogue.web_search(settings(request), body.query.strip())
    except catalogue.CatalogueError as e:
        msg = str(e)
        raise HTTPException(400, msg[:1].upper() + msg[1:]) from None


@api.get("/jobs/{job_id}/snapshots", response_model=Snapshots)
async def job_snapshots(job_id: int, request: Request):
    """Frames from the disc's ripped titles (and the part of a title being ripped)."""
    try:
        return await manager(request).snapshots(job_id)
    except ValueError:
        raise HTTPException(404) from None


@api.get("/jobs/{job_id}/snapshots/{title_index}/{frame}.jpg", include_in_schema=False)
async def job_snapshot(job_id: int, title_index: int, frame: int, request: Request):
    p = await manager(request).snapshot(job_id, title_index, frame)
    if not p:
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


@api.get("/tv/search", response_model=list[Show])
async def tv_search(request: Request, q: Annotated[str, Query(min_length=1, max_length=200)]):
    try:
        return await manager(request).tv_search(q)
    except ValueError as e:
        raise bad_request(e) from None


@api.post("/jobs/{job_id}/tv/propose", response_model=Job)
async def tv_propose(job_id: int, body: TvProposeRequest, request: Request):
    m = manager(request)
    return await _job_action(request, job_id, lambda i: m.tv_propose(
        i, body.show_tmdb_id, body.season, body.disc_number, body.start_episode, body.compilation))


@api.post("/jobs/{job_id}/tv/match-text", response_model=Job)
async def tv_match_text(job_id: int, body: TvMatchTextRequest, request: Request):
    m = manager(request)
    return await _job_action(request, job_id, lambda i: m.tv_match_text(i, body.text))


@api.post("/jobs/{job_id}/tv/confirm", response_model=Job)
async def tv_confirm(job_id: int, body: TvConfirmRequest, request: Request):
    m = manager(request)
    return await _job_action(request, job_id, lambda i: m.tv_confirm(i, body.mapping))


@api.put("/jobs/{job_id}/tv/mapping", response_model=Job)
async def tv_set_mapping(job_id: int, body: TvConfirmRequest, request: Request):
    """Titles changed by hand in the review, kept (unconfirmed) so the cover scan's picks
    leave them alone."""
    m = manager(request)
    return await _job_action(request, job_id, lambda i: m.tv_set_mapping(i, body.mapping))


@api.post("/jobs/{job_id}/tv/split", response_model=Job)
async def tv_split(job_id: int, body: TvSplitRequest, request: Request):
    """Where the long title holding the episodes is cut (chapters), or keep it whole."""
    m = manager(request)
    return await _job_action(request, job_id, lambda i: m.tv_split(i, body.cuts, body.off, body.cut_at))


@api.get("/jobs/{job_id}/split-thumbs/{title_index}/{chapter}.jpg", include_in_schema=False)
async def split_thumbnail(job_id: int, title_index: int, chapter: int, request: Request):
    p = await manager(request).split_thumb(job_id, title_index, chapter)
    if not p:
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


@api.get("/jobs/{job_id}/thumbs/{title_index}.jpg", include_in_schema=False)
async def thumbnail(job_id: int, title_index: int, request: Request):
    p = await manager(request).title_thumb(job_id, title_index)
    if not p:
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


@api.get("/jobs/{job_id}/art", include_in_schema=False)
async def job_art(job_id: int, request: Request):
    """The disc's poster (``?k=`` is the job's ``art`` key, only there for cache busting)."""
    m = manager(request)
    job = m.db.get_job(job_id)
    url = job.art_url()[0] if job else None
    path = await art.cached(settings(request).data_dir / "art", url) if url else None
    if not path:
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


@api.get("/collections", response_model=list[Collection])
async def list_collections(request: Request):
    return manager(request).db.collections()


@api.delete("/collections/{collection_id}/discs/{job_id}", response_model=CollectionDetail | None)
async def remove_collection_disc(collection_id: int, job_id: int, request: Request):
    """Take a disc out of the collection (its files in the library are kept). None when it
    was the last, so the collection is gone."""
    return _sync_call(lambda: manager(request).remove_disc(collection_id, job_id))


@api.get("/collections/{collection_id}", response_model=CollectionDetail)
async def get_collection(collection_id: int, request: Request):
    c = manager(request).collection_detail(collection_id)
    if not c:
        raise HTTPException(404)
    return c


def settings_view(s: Settings) -> SettingsView:
    m_dir = s.makemkv_dir
    drive_ids = [p.name for p in m_dir.iterdir() if p.is_dir() and not p.name.startswith("_")] \
        if m_dir.exists() else []
    values: dict[str, Any] = {}
    for name in EDITABLE - set(FOLDERS):
        v = getattr(s, name)
        if name in SECRETS:
            values[f"{name}_set"] = bool(v)
        else:
            values[name] = v
    # With the defaults filled in.
    values |= {
        "metadata_providers": providers.lists(s),
        "transcode_sources": {k: s.transcode_source(k) for k in ("dvd", "bluray", "uhd")},
        "notify_events": {e: integrations.Notifier(s).priority(e) for e in integrations.EVENTS},
    }
    return SettingsView(
        **values,
        providers=[ProviderInfo(name=p.name, label=p.label, description=p.description, lists=list(p.lists),
                                needs_key=bool(p.keys), keys=list(p.keys), configured=providers.configured(s, p.name),
                                site=p.site, check=p.check) for p in providers.PROVIDERS.values()],
        library_dir=str(s.library_dir), scratch_dir=str(s.scratch_dir),
        movies_dir=str(s.movies), tv_dir=str(s.tv), disc_backups_dir=str(s.disc_backups),
        custom_movies_dir=str(s.movies_dir or ""), custom_tv_dir=str(s.tv_dir or ""),
        custom_disc_backups_dir=str(s.disc_backups_dir or ""),
        makemkv_drive_data={d: runner.has_private_data(m_dir, d) for d in drive_ids},
        makemkv_drive_dumps={d: runner.has_dump(m_dir, d) for d in drive_ids},
        backups_dir=str(s.backups),
    )


@api.get("/settings", response_model=SettingsView)
async def get_settings(request: Request):
    return settings_view(settings(request))


@api.get("/settings/fields", response_model=list[SettingField])
async def get_setting_fields():
    """Every editable setting: its section, label, limits and default (config.Settings)."""
    return setting_fields()


@api.put("/settings", response_model=SettingsView)
async def put_settings(body: SettingsUpdate, request: Request):
    s = settings(request)
    plain = _plain_update(body)
    if "metadata_providers" in plain:
        plain["metadata_providers"] = providers.normalise(plain["metadata_providers"], s.tv_episode_order)
    if "transcode_codecs" in plain:
        plain["transcode_codecs"] = [c.lower() for c in plain["transcode_codecs"]]
    if "thediscdb_contribute_url" in plain:
        url = plain["thediscdb_contribute_url"].rstrip("/") or "https://thediscdb.com"
        if not url.startswith(("https://", "http://")):
            raise HTTPException(400, "TheDiscDB's address must start with https://")
        plain["thediscdb_contribute_url"] = url
    if plain.get("scanner_url"):
        try:
            plain["scanner_url"] = escl.base_url(plain["scanner_url"])
        except ValueError as e:
            raise bad_request(e) from None
    elif "scanner_url" in plain:
        plain["scanner_url"] = None
    folders = _folders(s, plain, manager(request))
    s.save_overrides(plain)
    if folders:
        s.scratch_dir.mkdir(parents=True, exist_ok=True)
    if "makemkv_key" in plain:
        manager(request).write_default_key()
    if "disabled_drives" in plain or "setup_complete" in plain:
        manager(request).drives_toggled()
    if any(k.startswith("scanner_") for k in plain):
        manager(request)._scanner_caps.clear()
    if "transcode_parallel" in plain:
        await manager(request).transcode_limit_changed()
    manager(request).publish_status()
    return settings_view(s)


def _plain_update(body: SettingsUpdate) -> dict[str, Any]:
    """What was sent, as settings.json keeps it. A secret sent as "" is cleared (secrets are
    stored, never echoed back). Text sent empty is unset, or back to its default when it
    can't be unset; anything else sent as null is ignored. Lists lose their blank lines."""
    out: dict[str, Any] = {}
    for k, v in body.model_dump(exclude_unset=True).items():
        f = Settings.model_fields[k]
        if k in SECRETS:
            out[k] = v or None
        elif v is None and k not in FOLDERS and type(None) not in get_args(f.annotation):
            if f.annotation is str:
                out[k] = f.default
        elif isinstance(v, list) and all(isinstance(x, str) for x in v):
            out[k] = [x.strip() for x in v if x.strip()]
        else:
            out[k] = v
    return out


def _folders(s, plain: dict, m) -> bool:
    """Check the folders in a settings update (in place, and "" unsets an optional one).
    True if any was given."""
    given = [k for k in FOLDERS if plain.get(k) is not None]
    for k in given:
        v = plain[k].strip()
        if not v:
            if k in REQUIRED_FOLDERS:
                raise HTTPException(400, f"The {FOLDER_NAMES[k]} can't be empty")
            plain[k] = None
        elif not Path(v).is_absolute():
            raise HTTPException(400, f"The {FOLDER_NAMES[k]} must be a full path, not {v}")
        else:
            plain[k] = v
    for k in set(FOLDERS) - set(given):
        plain.pop(k, None)
    if plain.get("scratch_dir") and Path(plain["scratch_dir"]) != s.scratch_dir and m.has_active_jobs():
        # Jobs find their rips in the scratch folder, so it can't move under them.
        raise HTTPException(409, "Finish or cancel the jobs in progress before moving the scratch folder")
    return bool(given)


FOLDER_NAMES = {"library_dir": "library folder", "movies_dir": "films folder", "tv_dir": "TV folder",
                "disc_backups_dir": "disc backups folder", "scratch_dir": "scratch folder"}


@api.post("/settings/test", response_model=SettingsTestResult)
async def test_settings(body: SettingsTestRequest, request: Request):
    """Try a connection with the values typed into the form, without saving them."""
    return await checks.run(body.check, settings(request), body.values)


@api.post("/media-servers/{server}/libraries", response_model=list[ServerLibrary])
async def server_libraries(server: MediaServer, body: ServerLibrariesRequest, request: Request):
    s = settings(request)
    url, key = body.url or getattr(s, f"{server}_url"), body.api_key or s.secret(integrations.server_key(server))
    if not (url and key):
        what = "token" if server == "plex" else "API key"
        raise HTTPException(400, f"set the {integrations.SERVERS[server]} URL and {what} first")
    try:
        return await integrations.server_libraries(server, url, key)
    except integrations.ServerError as e:
        raise HTTPException(502, str(e)) from None


@api.post("/setup/makemkv/private-data", response_model=ImportPrivateDataResult)
async def import_private_data(
    request: Request,
    drive_ids: Annotated[str, Form(description="comma-separated drive ids, or 'all'")] = "all",
    file: Annotated[UploadFile | None, File()] = None,
    path: Annotated[str | None, Form(description="a _private_data.tar or dump_*.tgz on the server")] = None,
):
    """Copy into this host's drive homes, and keep it for workers (they fetch it when they register)."""
    m = manager(request)
    s = settings(request)
    every = drive_ids == "all"
    ids = list(m.drives) if every else [d for d in drive_ids.split(",") if d]
    unknown = [d for d in ids if d not in m.drives]
    if unknown:
        raise HTTPException(400, f"unknown drives: {', '.join(unknown)}")
    name = Path(file.filename or "").name if file is not None else Path(path or "").name
    dump = runner.is_dump_name(name)
    with tempfile.TemporaryDirectory(dir=s.data_dir) as tmp:
        src = Path(tmp) / (name if dump else runner.PRIVATE_DATA)
        if file is not None:
            with src.open("wb") as fh:
                while chunk := file.file.read(1024 * 1024):
                    fh.write(chunk)
                    if fh.tell() > mkfiles.MAX_BYTES:
                        raise HTTPException(413, f"too big, the limit is {mkfiles.MAX_BYTES // 2**20} MB")
        elif path:
            p = Path(path)
            if not p.is_file():
                raise HTTPException(400, f"{path} is not a file")
            if p.stat().st_size > mkfiles.MAX_BYTES:
                raise HTTPException(413, f"too big, the limit is {mkfiles.MAX_BYTES // 2**20} MB")
            shutil.copy2(p, src)
        else:
            raise HTTPException(400, "upload a file or give a path")
        if not tarfile.is_tarfile(src):
            raise HTTPException(400, "that is not a tar file")
        if dump:
            n = runner.import_dump(s.makemkv_dir, src, name, ids)
        else:
            n = runner.import_private_data(s.makemkv_dir, src, ids)
        m.support.add(src, src.name, None if every else ids)
    m.publish_status()
    return ImportPrivateDataResult(drive_ids=ids, files=n)


@api.get("/setup/tools", response_model=SetupTools)
async def setup_tools(request: Request):
    """Whether MakeMKV, ffmpeg, mkvmerge and Java are where ripshelf will look for them."""
    s = settings(request)

    def tool(command: str) -> Tool:
        args = proc.split_command(command)
        path = args[0] if Path(args[0]).is_file() else shutil.which(args[0])
        return Tool(found=bool(path or s.fake_hardware and command == s.makemkvcon), path=path)

    registered = bool(s.makemkv_key) or (proc.WINDOWS and runner.registry_key() is not None)
    return SetupTools(makemkv=tool(s.makemkvcon), makemkv_registered=registered or s.fake_hardware,
                      ffmpeg=tool(s.ffmpeg), ffprobe=tool(s.ffprobe), mkvmerge=tool(tvsplit.default_mkvmerge(s.mkvmerge)), java=tool("java"), windows=proc.WINDOWS,
                      makemkv_eula_pending=s.makemkv_eula_pending())


@api.get("/setup/makemkv/eula", include_in_schema=False)
async def makemkv_eula(request: Request):
    """The licence of the MakeMKV that came with ripshelf, to read before accepting it."""
    f = settings(request).makemkv_eula_file
    if f is None or not f.is_file():
        raise HTTPException(404, "MakeMKV didn't come with ripshelf here, so its licence is wherever it was installed from")
    return FileResponse(f, media_type="text/plain; charset=utf-8")


@api.post("/setup/folder", response_model=FolderState)
async def setup_folder(body: FolderRequest):
    """Whether a folder typed into Settings exists (and its free space), and make it if asked."""
    p = Path(body.path.strip())
    if not p.is_absolute():
        raise HTTPException(400, f"{body.path} is not a full path")
    created, error = False, None
    if body.create and not p.is_dir():
        try:
            p.mkdir(parents=True, exist_ok=True)
            created = True
        except OSError as e:
            error = f"could not create it: {e.strerror or e}"
    here = system._existing(p)
    free = None
    if here is not None:
        try:
            free = shutil.disk_usage(here).free
        except OSError:
            pass
    return FolderState(path=str(p), exists=p.is_dir(), created=created, free=free, error=error)


@api.get("/setup/makemkv/files", response_model=list[MakeMKVFile])
async def makemkv_files(request: Request):
    """The private data and firmware dumps kept for workers."""
    return [MakeMKVFile(**vars(f)) for f in manager(request).support.list()]


@api.delete("/setup/makemkv/files/{sha256}", status_code=204)
async def delete_makemkv_file(sha256: str, request: Request, name: str | None = None):
    """Stop offering a file to workers. Drives that have it keep their copy."""
    if not mkfiles.valid_sha(sha256) or not manager(request).support.remove(sha256, name):
        raise HTTPException(404, "no such file")


@api.get("/events")
async def events(request: Request):
    bus = request.app.state.bus
    closing = getattr(request.app.state, "closing", None)

    async def stream():
        with bus.subscribe() as q:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected() or (closing and closing.is_set()):
                    break
                data = await next_item(q, SSE_HEARTBEAT_S, closing)
                if data is not None:
                    yield f"data: {data}\n\n"
                elif not (closing and closing.is_set()):
                    yield ": ping\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@api.post("/dev/insert/{drive_id}", status_code=204)
async def dev_insert(drive_id: str, request: Request):
    """Put the disc back in a simulated drive (fake hardware only)."""
    m = manager(request)
    if not settings(request).fake_hardware or drive_id not in m.drives:
        raise HTTPException(404)
    m.backend.insert(m.drives[drive_id].hw.device)


def mount_ui(app: FastAPI, static_dir: Path | None) -> None:
    if not static_dir or not (static_dir / "index.html").exists():
        return
    index = static_dir / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        candidate = (static_dir / path).resolve()
        if path and candidate.is_file() and static_dir.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
