"""Writes a database with a release's own code: a TV and a movie disc done, a failed
scan and a disc waiting for review. Not part of the suite: see ../README.md."""

import os
import sqlite3

import respx
from pydantic import SecretStr

from ripshelf.models import Candidate, JobState

from .conftest import until
from .test_tv_pipeline import CASE_ORDER, SD
from .tvfix import mock_show


async def test_make_fixture(harness, settings):
    settings.tmdb_api_key = SecretStr("k")
    settings.scan_timeout_s = 2
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "pippa-goose")
        router.get(url__regex=r".*/search/movie.*").respond(
            200, json={"results": [], "page": 1, "total_results": 0, "total_pages": 0})
        async with harness(SD) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done", timeout=60)
            job = await h.m.tv_match_text(job.id, CASE_ORDER)
            await h.m.tv_confirm(job.id, [])
            await h.wait_job(lambda j: j.id == job.id and j.state == JobState.done)

            h.backend.insert("/dev/fake-sr1")
            movie = await until(lambda: next(iter(h.db.list_jobs([JobState.review.value])), None), timeout=60)
            await h.m.confirm(movie.id, Candidate(source="manual", title="Moonlit Orchard", year=1998))
            await until(lambda: h.db.get_job(movie.id).state == JobState.done, timeout=60)

            h.backend.insert("/dev/fake-sr3")
            await until(lambda: next(iter(h.db.list_jobs([JobState.failed.value])), None), timeout=30)
            h.backend.insert("/dev/fake-sr2")
            await until(lambda: next(iter(h.db.list_jobs([JobState.review.value])), None), timeout=60)
            out = sqlite3.connect(os.environ["FIXTURE_OUT"])
            h.db.conn.backup(out)
            out.close()
