"""ripshelf's own log, kept in a rotating file in <data>/logs and a recent-lines buffer for the Logs page."""

from __future__ import annotations

import logging
import logging.handlers
import threading
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

from .models import LogEntry

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
FILE_NAME = "ripshelf.log"
FILE_BYTES = 5 * 1024 * 1024
FILE_COUNT = 5  # ripshelf.log plus 4 rotated copies
BUFFER_LINES = 5000


class BufferHandler(logging.Handler):
    """Keeps the newest records in memory, numbered, so a page can ask for "everything after N"."""

    def __init__(self, size: int = BUFFER_LINES):
        super().__init__(logging.DEBUG)
        self._lines: deque[LogEntry] = deque(maxlen=size)
        self._seq = 0
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info:
                message += "\n" + logging.Formatter().formatException(record.exc_info)
        except Exception:  # a bad format string must not break logging
            message = str(record.msg)
        with self._lock:
            self._seq += 1
            self._lines.append(LogEntry(
                seq=self._seq, ts=datetime.fromtimestamp(record.created, UTC), level=record.levelname,
                logger=record.name, message=message))

    def since(self, after: int = 0, level: str = "DEBUG", limit: int = 1000) -> tuple[list[LogEntry], int]:
        floor = logging.getLevelName(level.upper())
        floor = floor if isinstance(floor, int) else logging.DEBUG
        with self._lock:
            out = [e for e in self._lines if e.seq > after and logging.getLevelName(e.level) >= floor]
            return out[-limit:], self._seq


BUFFER = BufferHandler()


def install_buffer() -> None:
    root = logging.getLogger()
    if BUFFER not in root.handlers:
        root.addHandler(BUFFER)


def log_file(data_dir: Path) -> Path:
    return data_dir / "logs" / FILE_NAME


def install_file(data_dir: Path) -> Path | None:
    """Also log to <data>/logs/ripshelf.log, rotated at 5 MB (5 files kept)."""
    path = log_file(data_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=FILE_BYTES, backupCount=FILE_COUNT - 1,
                                                       encoding="utf-8")
    except OSError as e:
        logging.getLogger(__name__).warning("cannot write the log file %s: %s", path, e)
        return None
    handler.setFormatter(logging.Formatter(FORMAT))
    logging.getLogger().addHandler(handler)
    return path
