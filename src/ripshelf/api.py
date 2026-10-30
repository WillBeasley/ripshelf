"""HTTP API, server-sent events and the static UI."""

from __future__ import annotations

import asyncio
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import SecretStr

from . import applog, art, checks, integrations, proc, providers, system
from .bus import next_item
from .config import FOLDERS, REQUIRED_FOLDERS, Settings
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
    LogPage,
    MakeMKVFile,
    MediaRequest,
    MediaServer,
    Preset,
    ProviderInfo,
    SearchRequest,
    ServerLibrariesRequest,
    ServerLibrary,
    SettingsTestRequest,
    SettingsTestResult,
    SettingsUpdate,
    SettingsView,
    SetupTools,
    Show,
    Status,
    Tool,
    TvConfirmRequest,
    TvMatchTextRequest,
    TvProposeRequest,
)

SSE_HEARTBEAT_S = 15
SECRETS = {"tmdb_api_key", "tvdb_api_key", "tvdb_pin", "omdb_api_key", "thediscdb_api_key", "emby_api_key", "jellyfin_api_key", "plex_token", "makemkv_key", "pushover_user_key",
           "pushover_app_token"}


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
    """Remove finished, failed or cancelled jobs from the history (never library files)."""
    try:
        return DeleteJobsResult(deleted=manager(request).delete_jobs(body.ids))
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


@api.get("/movies/search", response_model=list[Candidate])
async def movie_search(request: Request, q: Annotated[str, Query(min_length=1, max_length=200)],
                       year: int | None = None):
    try:
        return await manager(request).movie_search(q, year)
    except ValueError as e:
        raise bad_request(e) from None


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


@api.get("/jobs/{job_id}/thumbs/{title_index}.jpg", include_in_schema=False)
async def thumbnail(job_id: int, title_index: int, request: Request):
    p = manager(request).thumbnail_path(job_id, title_index)
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
    return SettingsView(
        tmdb_api_key_set=bool(s.tmdb_api_key), tmdb_language=s.tmdb_language,
        tvdb_api_key_set=bool(s.tvdb_api_key), tvdb_pin_set=bool(s.tvdb_pin), tv_episode_order=s.tv_episode_order,
        tvdb_episode_order=s.tvdb_episode_order, omdb_api_key_set=bool(s.omdb_api_key),
        setup_complete=s.setup_complete,
        thediscdb_contribute=s.thediscdb_contribute, thediscdb_contribute_url=s.thediscdb_contribute_url,
        thediscdb_api_key_set=bool(s.thediscdb_api_key),
        metadata_providers=providers.lists(s),
        providers=[ProviderInfo(name=p.name, label=p.label, description=p.description, lists=list(p.lists),
                                needs_key=bool(p.keys), keys=list(p.keys), configured=providers.configured(s, p.name),
                                site=p.site, check=p.check) for p in providers.PROVIDERS.values()],
        auto_confirm_threshold=s.auto_confirm_threshold, auto_eject=s.auto_eject,
        scan_timeout_s=s.scan_timeout_s, scan_max_s=s.scan_max_s, poll_interval_s=s.poll_interval_s, uhd_models=s.uhd_models,
        disabled_drives=s.disabled_drives, rip_mode=s.rip_mode, drive_modes=s.drive_modes, drive_names=s.drive_names, public_url=s.public_url, emby_url=s.emby_url,
        emby_api_key_set=bool(s.emby_api_key),
        emby_movies_library=s.emby_movies_library, emby_movies_path=s.emby_movies_path,
        emby_tv_library=s.emby_tv_library, emby_tv_path=s.emby_tv_path,
        jellyfin_url=s.jellyfin_url, jellyfin_api_key_set=bool(s.jellyfin_api_key),
        jellyfin_movies_library=s.jellyfin_movies_library, jellyfin_movies_path=s.jellyfin_movies_path,
        jellyfin_tv_library=s.jellyfin_tv_library, jellyfin_tv_path=s.jellyfin_tv_path,
        plex_url=s.plex_url, plex_token_set=bool(s.plex_token),
        plex_movies_library=s.plex_movies_library, plex_movies_path=s.plex_movies_path,
        plex_tv_library=s.plex_tv_library, plex_tv_path=s.plex_tv_path,
        makemkv_key_set=bool(s.makemkv_key), library_dir=str(s.library_dir),
        movies_dir=str(s.movies), tv_dir=str(s.tv), disc_backups_dir=str(s.disc_backups),
        scratch_dir=str(s.scratch_dir),
        custom_movies_dir=str(s.movies_dir or ""), custom_tv_dir=str(s.tv_dir or ""),
        custom_disc_backups_dir=str(s.disc_backups_dir or ""),
        makemkv_drive_data={d: runner.has_private_data(m_dir, d) for d in drive_ids},
        makemkv_drive_dumps={d: runner.has_dump(m_dir, d) for d in drive_ids},
        tv_unmatched=s.tv_unmatched, tv_rip_extras=s.tv_rip_extras, verify_order=s.verify_order,
        tv_auto_confirm=s.tv_auto_confirm, tv_auto_threshold=s.tv_auto_threshold,
        catalogue_search=s.catalogue_search, searxng_url=s.searxng_url,
        transcode_enabled=s.transcode_enabled, transcode_codecs=s.transcode_codecs,
        transcode_crop=s.transcode_crop, transcode_hdr=s.transcode_hdr,
        transcode_sources={k: s.transcode_source(k) for k in ("dvd", "bluray", "uhd")},
        transcode_encoder=s.transcode_encoder, transcode_quality=s.transcode_quality,
        transcode_keep_original=s.transcode_keep_original, vaapi_device=s.vaapi_device,
        pushover_user_key_set=bool(s.pushover_user_key), pushover_app_token_set=bool(s.pushover_app_token),
        pushover_device=s.pushover_device,
        notify_events={e: integrations.Notifier(s).priority(e) for e in integrations.EVENTS},
        notify_batch_s=s.notify_batch_s, backup_keep=s.backup_keep, backups_dir=str(s.backups),
    )


@api.get("/settings", response_model=SettingsView)
async def get_settings(request: Request):
    return settings_view(settings(request))


@api.put("/settings", response_model=SettingsView)
async def put_settings(body: SettingsUpdate, request: Request):
    s = settings(request)
    values = body.model_dump(exclude_unset=True)
    for k in list(values):
        if k in SECRETS:
            v = values[k]
            # "" clears a secret; values are stored, never echoed back.
            values[k] = SecretStr(v) if v else None
    # settings.json needs plain values
    plain = {k: (v.get_secret_value() if isinstance(v, SecretStr) else v) for k, v in values.items()}
    if "metadata_providers" in plain:
        given = plain.pop("metadata_providers")
        if given is not None:
            plain["metadata_providers"] = providers.normalise(given, s.tv_episode_order)
    if "thediscdb_contribute_url" in plain:
        url = (plain["thediscdb_contribute_url"] or "").strip().rstrip("/") or "https://thediscdb.com"
        if not url.startswith(("https://", "http://")):
            raise HTTPException(400, "TheDiscDB's address must start with https://")
        plain["thediscdb_contribute_url"] = url
    folders = _folders(s, plain, manager(request))
    s.save_overrides(plain)
    if folders:
        s.scratch_dir.mkdir(parents=True, exist_ok=True)
    if "makemkv_key" in plain:
        manager(request).write_default_key()
    if "disabled_drives" in plain or "setup_complete" in plain:
        manager(request).drives_toggled()
    manager(request).publish_status()
    return settings_view(s)


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
    """Whether MakeMKV, ffmpeg and Java are where ripshelf will look for them."""
    s = settings(request)

    def tool(command: str) -> Tool:
        args = proc.split_command(command)
        path = args[0] if Path(args[0]).is_file() else shutil.which(args[0])
        return Tool(found=bool(path or s.fake_hardware and command == s.makemkvcon), path=path)

    registered = bool(s.makemkv_key) or (proc.WINDOWS and runner.registry_key() is not None)
    return SetupTools(makemkv=tool(s.makemkvcon), makemkv_registered=registered or s.fake_hardware,
                      ffmpeg=tool(s.ffmpeg), ffprobe=tool(s.ffprobe), java=tool("java"), windows=proc.WINDOWS,
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
