"""MakeMKV support files kept by the controller for its workers.

``_private_data.tar`` (per drive, with LibreDrive data and SDF files) and firmware dumps
(``dump_*.tgz``, named after the firmware's hash, so one set can be offered to
every drive and MakeMKV picks the one it needs). Imports on the controller are
copied into its drives' homes as before, and also kept here, in
``<data>/makemkv-files``. Workers fetch the files (and the key) when they
register and install them into their own drives' homes.

Files are stored by sha256 (``<sha256>.bin``, mode 0600) with an ``index.json``
naming each one and the drives it is for (``None`` for every drive, including
drives seen later).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .runner import PRIVATE_DATA, is_dump_name

MAX_BYTES = 64 * 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{64}")


def valid_name(name: str) -> bool:
    return name == PRIVATE_DATA or is_dump_name(name)


def valid_sha(sha: str) -> bool:
    return bool(_SHA.fullmatch(sha))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def write_atomic(dest: Path, data: bytes | None = None, source: Path | None = None) -> None:
    """Write ``data`` (or copy ``source``) to ``dest`` via a temporary file, mode 0600."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            if source is not None:
                with source.open("rb") as src:
                    while chunk := src.read(1024 * 1024):
                        f.write(chunk)
            else:
                f.write(data or b"")
        os.chmod(tmp, 0o600)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@dataclass
class SupportFile:
    name: str
    sha256: str
    size: int
    drives: list[str] | None  # None means every drive
    added_at: str

    def covers(self, drive_id: str) -> bool:
        return self.drives is None or drive_id in self.drives


class SupportFiles:
    def __init__(self, root: Path):
        self.root = root

    @property
    def _index(self) -> Path:
        return self.root / "index.json"

    def list(self) -> list[SupportFile]:
        try:
            rows = json.loads(self._index.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return []
        return [SupportFile(**r) for r in rows]

    def _save(self, files: list[SupportFile]) -> None:
        write_atomic(self._index, json.dumps([asdict(f) for f in files], indent=2).encode())

    def path(self, sha: str) -> Path:
        if not valid_sha(sha):
            raise ValueError("bad sha256")
        return self.root / f"{sha}.bin"

    def add(self, source: Path, name: str, drives: list[str] | None) -> SupportFile:
        """Keep a copy of ``source`` as ``name`` for ``drives`` (None for all). It replaces what the
        same drives had under that name; a firmware dump replaces an older one of the same name."""
        if not valid_name(name):
            raise ValueError(f"not a MakeMKV support file name: {name}")
        size = source.stat().st_size
        if size > MAX_BYTES:
            raise ValueError(f"{name} is too big ({size} bytes)")
        sha = sha256_file(source)
        if not self.path(sha).exists():
            write_atomic(self.path(sha), source=source)
        files = []
        for f in self.list():
            if f.name == name and f.sha256 != sha:
                if drives is None or f.drives is None or is_dump_name(name):
                    continue  # replaced
                f.drives = [d for d in f.drives if d not in drives]
                if not f.drives:
                    continue
            files.append(f)
        same = next((f for f in files if f.name == name and f.sha256 == sha), None)
        if same:
            same.drives = None if drives is None or same.drives is None else sorted({*same.drives, *drives})
            entry = same
        else:
            entry = SupportFile(name=name, sha256=sha, size=size, drives=sorted(drives) if drives is not None
                                else None, added_at=datetime.now(UTC).isoformat(timespec="seconds"))
            files.append(entry)
        self._save(files)
        self._prune(files)
        return entry

    def remove(self, sha: str, name: str | None = None) -> bool:
        files = self.list()
        keep = [f for f in files if not (f.sha256 == sha and (name is None or f.name == name))]
        if len(keep) == len(files):
            return False
        self._save(keep)
        self._prune(keep)
        return True

    def rename_drive(self, old: str, new: str) -> None:
        files = self.list()
        changed = False
        for f in files:
            if f.drives and old in f.drives:
                f.drives = sorted({new if d == old else d for d in f.drives})
                changed = True
        if changed:
            self._save(files)

    def _prune(self, files: list[SupportFile]) -> None:
        used = {f.sha256 for f in files}
        for p in self.root.glob("*.bin"):
            if p.stem not in used:
                p.unlink(missing_ok=True)


# --- on a worker, installing the files into a drive's MakeMKV home -----------------------

# What ripshelf last installed in a home, name -> sha256. MakeMKV rewrites its own
# _private_data.tar, so a file is only installed again when the controller's copy changes.
SYNC_STATE = ".ripshelf-synced.json"


def wanted_for(entries: list[dict], drive_id: str | None) -> dict[str, str]:
    """name -> sha256 a drive should have; a file for that drive wins over one for every drive.
    With ``drive_id`` None, only the files for every drive."""
    out: dict[str, str] = {}
    for e in entries:
        if e["drives"] is None:
            out.setdefault(e["name"], e["sha256"])
    for e in entries:
        if drive_id is not None and e["drives"] is not None and drive_id in e["drives"]:
            out[e["name"]] = e["sha256"]
    return out


def _state(conf_dir: Path) -> dict[str, str]:
    try:
        return json.loads((conf_dir / SYNC_STATE).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _record(conf_dir: Path, name: str, sha: str) -> None:
    state = _state(conf_dir) | {name: sha}
    write_atomic(conf_dir / SYNC_STATE, json.dumps(state, indent=2).encode())


def needs(conf_dir: Path, name: str, sha: str) -> bool:
    """Whether ``name`` with this sha256 still has to be installed in ``conf_dir``. A file
    put there before (by hand, or by the controller in a shared makemkv folder) is adopted
    rather than overwritten. That's an imported tar, a dump of the same name, or the same bytes."""
    from .runner import IMPORTED_MARKER

    state = _state(conf_dir)
    if state.get(name) == sha:
        return False
    target = conf_dir / name
    if not target.exists() or name in state:
        return True  # missing, or we installed an older copy and the controller has a new one
    adopt = is_dump_name(name) or (conf_dir / IMPORTED_MARKER).exists() or sha256_file(target) == sha
    if adopt:
        _record(conf_dir, name, sha)
    return not adopt


def install(conf_dir: Path, name: str, sha: str, source: Path) -> None:
    from .runner import IMPORTED_MARKER

    write_atomic(conf_dir / name, source=source)
    if name == PRIVATE_DATA:
        write_atomic(conf_dir / IMPORTED_MARKER, b"imported from the controller\n")
    _record(conf_dir, name, sha)
