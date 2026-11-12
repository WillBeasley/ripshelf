"""The files that go with the daily database backup.

The database alone doesn't bring a ripshelf back: settings.json holds the settings and
secrets (API keys, the MakeMKV key, worker tokens), and the MakeMKV folders hold the
LibreDrive data and firmware dumps each drive needs, which can be hard to get again.
They go in ``ripshelf-<date>-files.tar.gz`` next to ``ripshelf-<date>.db``, laid out as
in the data folder, so restoring is extracting the archive into it.
"""

from __future__ import annotations

import os
import tarfile
from pathlib import Path

from .config import Settings
from .library import replace


def files_name(date: str) -> str:
    return f"ripshelf-{date}-files.tar.gz"


FILES_GLOB = files_name("*")


def _skip(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """Leaves out half-written files (*.tmp) and anything that isn't a file or folder."""
    if info.name.endswith(".tmp") or not (info.isfile() or info.isdir()):
        return None
    return info


def write_files(s: Settings, dest: Path) -> None:
    """Archive settings.json, the MakeMKV homes and the files kept for remote workers,
    written atomically and readable only by this user (it holds the secrets)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f, tarfile.open(fileobj=f, mode="w:gz") as tar:
            # Named as in the data folder, also when makemkv_path puts the homes elsewhere.
            for src, name in ((s.settings_file, "settings.json"), (s.makemkv_dir, "makemkv"),
                              (s.makemkv_files_dir, "makemkv-files")):
                if src.exists():
                    tar.add(src, arcname=name, filter=_skip)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    replace(tmp, dest)  # retried on Windows while a scanner has the new file open
