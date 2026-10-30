"""In-process fan-out of server events to SSE subscribers."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager

from pydantic import BaseModel


async def next_item(q: asyncio.Queue, timeout: float, stop: asyncio.Event | None = None):
    """The queue's next item, or None after ``timeout`` seconds or once ``stop`` is set
    (the server is shutting down, so long-polls and streams can end on their own)."""
    get = asyncio.ensure_future(q.get())
    waiters = {get} if stop is None else {get, asyncio.ensure_future(stop.wait())}
    try:
        await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in waiters:
            if not t.done():
                t.cancel()
    return get.result() if get.done() and not get.cancelled() else None


class EventBus:
    def __init__(self, queue_size: int = 500):
        self._subs: set[asyncio.Queue[str]] = set()
        self._size = queue_size

    def publish(self, event: BaseModel) -> None:
        data = event.model_dump_json()
        for q in list(self._subs):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                # Drop a stalled client. The browser's EventSource reconnects.
                self._subs.discard(q)

    @contextmanager
    def subscribe(self):
        q: asyncio.Queue[str] = asyncio.Queue(self._size)
        self._subs.add(q)
        try:
            yield q
        finally:
            self._subs.discard(q)
