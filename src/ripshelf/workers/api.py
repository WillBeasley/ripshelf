"""HTTP API for remote workers (controller side).

Workers authenticate with a Bearer token, either their own (added in Settings → Remote
workers; only its sha256 is stored, and it works for that worker id only) or the
shared ``worker_token``. Off until either exists.
"""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import re
import secrets
import time
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..models import NewRemoteWorker, NewRemoteWorkerRequest, RemoteWorker, RemoteWorkers

router = APIRouter(prefix="/api/workers")

TOUCH_EVERY_S = 30.0


def hash_token(token: str) -> str:
    # Tokens are 256 random bits, so a plain hash is enough (no salt or stretching needed).
    return hashlib.sha256(token.encode()).hexdigest()


def _hub(request: Request):
    return request.app.state.manager.hub


@dataclass
class Caller:
    token_id: int | None  # None for the shared token
    worker_id: str | None  # the only worker this token is for (None for any)


# Async, so FastAPI runs it on the event loop. A sync dependency runs in a worker thread, and
# the database connection is shared with the loop with no lock. During a burst of job writes
# (a cancel) a lookup there came back empty, and a good token got a 401.
async def authorised(request: Request, authorization: Annotated[str | None, Header()] = None) -> Caller:
    shared = request.app.state.settings.secret("worker_token")
    given = (authorization or "").removeprefix("Bearer ").strip()
    if shared and given and hmac.compare_digest(given.encode(), shared.encode()):
        return Caller(None, None)
    db = request.app.state.manager.db
    if given:
        h = hash_token(given)
        row = db.worker_token_by_hash(h)
        if row and hmac.compare_digest(row["token_hash"], h):
            touched = getattr(request.app.state, "token_touched", None)
            if touched is None:
                touched = request.app.state.token_touched = {}
            if time.monotonic() - touched.get(row["id"], -TOUCH_EVERY_S) >= TOUCH_EVERY_S:
                touched[row["id"]] = time.monotonic()
                db.touch_worker_token(row["id"])
            return Caller(row["id"], row["worker_id"])
    if not shared and not db.worker_tokens():
        raise HTTPException(403, "remote workers are off (add one in Settings → Remote workers)")
    raise HTTPException(401, "wrong worker token")


def allowed(caller: Caller, worker_id: str) -> None:
    if caller.worker_id is not None and caller.worker_id != worker_id:
        raise HTTPException(403, f"this token is for worker {caller.worker_id}, not {worker_id}")


async def for_worker(worker_id: str, caller: Annotated[Caller, Depends(authorised)]) -> Caller:
    allowed(caller, worker_id)
    return caller


Auth = Depends(for_worker)


class RegisterBody(BaseModel):
    id: str
    name: str
    encoders: list[str] = []
    shared_scratch: bool = False
    makemkv_eula_pending: bool = False
    drives: list[dict] = []


class HeartbeatBody(BaseModel):
    drives: list[dict] = []
    runs: list[int] = []  # what the worker is working on


class LinesBody(BaseModel):
    lines: list[str]


class FinishBody(BaseModel):
    returncode: int | None = None
    timed_out: bool = False
    stalled: bool = False
    cancelled: bool = False
    error: str | None = None
    files: list[str] = []


class WorkerView(BaseModel):
    id: str
    name: str
    online: bool
    encoders: list[str]
    shared_scratch: bool
    makemkv_eula_pending: bool
    drives: list[str]


def _known(request: Request, worker_id: str):
    hub = _hub(request)
    if worker_id not in hub.workers:
        raise HTTPException(404, "unknown worker, so register first")
    return hub


@router.get("", response_model=list[WorkerView])
async def list_workers(request: Request):
    return [WorkerView(id=w.id, name=w.name, online=w.online, encoders=w.encoders,
                       shared_scratch=w.shared_scratch, makemkv_eula_pending=w.makemkv_eula_pending,
                       drives=[d.hw.id for d in w.drives.values()])
            for w in _hub(request).workers.values()]


@router.post("/register")
async def register(body: RegisterBody, request: Request, caller: Annotated[Caller, Depends(authorised)]):
    if not body.id or "/" in body.id or ":" in body.id:
        raise HTTPException(422, "bad worker id")
    allowed(caller, body.id)
    for d in body.drives:
        if not isinstance(d.get("id"), str) or not isinstance(d.get("device"), str):
            raise HTTPException(422, "each drive needs an id and a device")
        if not isinstance(d.get("legacy_id"), str | None):
            raise HTTPException(422, "legacy_id must be a string")
    w = _hub(request).register(body.id, body.name, body.drives, body.encoders, body.shared_scratch,
                                 body.makemkv_eula_pending)
    return {"ok": True, "drives": [d.hw.id for d in w.drives.values()]}


@router.post("/{worker_id}/heartbeat", dependencies=[Auth])
async def heartbeat(worker_id: str, body: HeartbeatBody, request: Request):
    hub = _known(request, worker_id)
    hub.heartbeat(worker_id, body.drives)
    return {"ok": True, "cancel": hub.given_up(worker_id, body.runs)}


@router.post("/{worker_id}/poll", dependencies=[Auth])
async def poll(worker_id: str, request: Request):
    hub = _known(request, worker_id)
    hub.heartbeat(worker_id, [])
    closing = getattr(request.app.state, "closing", None)
    return {"commands": await hub.next_commands(worker_id, stop=closing)}


@router.post("/{worker_id}/runs/{run_id}/lines", dependencies=[Auth])
async def lines(worker_id: str, run_id: int, body: LinesBody, request: Request):
    _known(request, worker_id).lines(run_id, body.lines)
    return {"ok": True}


@router.put("/{worker_id}/runs/{run_id}/files/{name}", dependencies=[Auth])
async def upload(worker_id: str, run_id: int, name: str, request: Request):
    hub = _known(request, worker_id)
    try:
        target, run = hub.upload_target(run_id, name)
    except KeyError:
        raise HTTPException(404, "no such run") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(f".{target.name}.upload")
    try:
        with part.open("wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
    except Exception:
        part.unlink(missing_ok=True)
        raise
    part.replace(target)
    if run.kind == "structure" and name == "structure.tar":
        try:
            hub.unpack_structure(target, run.out_dir)
        except Exception as e:
            raise HTTPException(400, f"bad structure archive: {e}") from None
    return {"ok": True, "bytes": target.stat().st_size if target.exists() else 0}


@router.post("/{worker_id}/runs/{run_id}/finish", dependencies=[Auth])
async def finish(worker_id: str, run_id: int, body: FinishBody, request: Request):
    _known(request, worker_id).finish(run_id, body.returncode, body.timed_out, body.cancelled, body.error,
                                      body.files, body.stalled)
    return {"ok": True}




@router.get("/{worker_id}/makemkv", dependencies=[Auth])
async def makemkv_bootstrap(worker_id: str, request: Request):
    """What a worker needs for MakeMKV, which is the registration key and the support files for its
    drives (drive ids as the worker knows them; ``drives`` null for every drive)."""
    hub = _known(request, worker_id)
    s = request.app.state.settings
    return {"key": s.secret("makemkv_key"), "files": _manifest(request, hub.workers[worker_id])}


def _manifest(request: Request, worker) -> list[dict]:
    local = {rd.hw.id: rd.local_id for rd in worker.drives.values()}
    out = []
    for f in request.app.state.manager.support.list():
        drives = None if f.drives is None else sorted(local[d] for d in f.drives if d in local)
        if drives == []:
            continue
        out.append({"name": f.name, "sha256": f.sha256, "size": f.size, "drives": drives})
    return out


@router.get("/{worker_id}/makemkv/files/{sha256}", dependencies=[Auth])
async def makemkv_file(worker_id: str, sha256: str, request: Request):
    hub = _known(request, worker_id)
    if not any(f["sha256"] == sha256 for f in _manifest(request, hub.workers[worker_id])):
        raise HTTPException(404, "no such file for this worker")
    path = request.app.state.manager.support.path(sha256)
    if not path.is_file():
        raise HTTPException(404, "no such file")
    return FileResponse(path, media_type="application/octet-stream")


# --- Settings → Remote workers (the UI) --------------------------------------------------

admin = APIRouter(prefix="/api/remote-workers")


def _view(request: Request, row: dict):
    w = _hub(request).workers.get(row["worker_id"])
    return RemoteWorker(id=row["id"], name=row["name"], worker_id=row["worker_id"], created_at=row["created_at"],
                        last_seen_at=row["last_seen_at"], online=bool(w and w.online))


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-") or "worker"


def compose_snippet(name: str, worker_id: str, token: str, controller_url: str) -> str:
    q = json.dumps  # a JSON string is a valid YAML string, whatever it holds
    return f"""\
# ripshelf worker "{worker_id}". Change the lines marked  <-- , then run docker compose up -d
# Use the controller's image, either pulled (docs/installation.md) or built here (docs/building.md).
# The MakeMKV key, _private_data.tar and firmware dumps come from the controller.
services:
  ripshelf-worker:
    image: ${{RIPSHELF_IMAGE:-ghcr.io/willbeasley/ripshelf:latest}}   # <-- the controller's image
    command: ["ripshelf", "worker"]
    restart: unless-stopped
    user: "1000:1000"                    # <-- your user and group ids (id -u, id -g)
    group_add: ["24"]                    # <-- the cdrom group id on this machine (getent group cdrom)
    environment:
      TZ: Etc/UTC                        # <-- your time zone
      RIPSHELF_ACCEPT_MAKEMKV_EULA: "no"  # <-- yes once you accept MakeMKV's licence, as on the controller
      RIPSHELF_CONTROLLER_URL: {q(controller_url)}
      RIPSHELF_WORKER_TOKEN: {q(token)}
      RIPSHELF_WORKER_ID: {q(worker_id)}
      RIPSHELF_WORKER_NAME: {q(name)}
    devices:                             # <-- this machine's drives, each /dev/srN with its /dev/sgN
      - /dev/sr0                         #     (ls /sys/class/block/sr*/device/scsi_generic)
      - /dev/sg0
    volumes:
      - ./worker-data:/data              # MakeMKV data per drive
      - ./worker-scratch:/scratch        # rips in progress: ~100 GB free for a UHD disc
"""


@admin.get("", response_model=RemoteWorkers)
async def remote_workers(request: Request):
    return RemoteWorkers(workers=[_view(request, r) for r in request.app.state.manager.db.worker_tokens()],
                         shared_token=bool(request.app.state.settings.secret("worker_token")))


@admin.post("", response_model=NewRemoteWorker)
async def add_remote_worker(body: NewRemoteWorkerRequest, request: Request):
    db = request.app.state.manager.db
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "give the worker a name")
    # A revoked worker's id is free again (unless it's still connected, with the shared token),
    # so adding it back keeps its id, and its drives keep theirs.
    taken = {r["worker_id"] for r in db.worker_tokens()} | {w.id for w in _hub(request).workers.values() if w.online}
    base = slug(name)
    worker_id = next(c for c in (base if i == 1 else f"{base}-{i}" for i in itertools.count(1)) if c not in taken)
    token = secrets.token_urlsafe(32)
    row = db.add_worker_token(name, worker_id, hash_token(token))
    s = request.app.state.settings
    url = (body.controller_url or "").strip() or s.public_url or str(request.base_url)
    return NewRemoteWorker(worker=_view(request, row), token=token,
                           compose=compose_snippet(name, worker_id, token, url.rstrip("/")))


@admin.delete("/{token_id}", status_code=204)
async def revoke_remote_worker(token_id: int, request: Request):
    db = request.app.state.manager.db
    row = db.worker_token(token_id)
    if not row or not db.delete_worker_token(token_id):
        raise HTTPException(404, "no such worker")
    request.app.state.token_touched = {}
    _hub(request).disconnect(row["worker_id"], "its token was revoked")
