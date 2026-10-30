"""Identifying a disc by hand before its scan is done (a preset)."""

from __future__ import annotations

import pytest
import respx
from pydantic import SecretStr, ValidationError

from ripshelf.models import Candidate, JobState, MediaKind, Preset, Show

from .tvfix import load, mock_show

PM = "/dev/fake-sr1"  # PRACTICAL_MAGIC, a film
SD = "/dev/fake-sr0"  # SARAH_&_DUCK_BROLLY_BUS, a TV disc
FILM = Candidate(source="tmdb", tmdb_id=6435, title="Practical Magic", year=1998)


async def test_film_preset_during_scan_skips_identification(harness):
    # No TMDB key, so without the preset this disc would wait for review.
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.scanning)
        await h.m.set_preset(job.id, Preset(media=MediaKind.movie, candidate=FILM))
        assert h.job(job.id).preset.candidate.title == "Practical Magic"  # outlives the scan's own saves
        done = await h.wait_job(lambda j: j.state == JobState.done, timeout=60)
        states = [e.message for e in h.db.events(done.id) if e.kind == "state"]
    assert done.identity.title == "Practical Magic" and done.identity_source == "user"
    assert done.preset is None
    assert not any("review" in s.lower() for s in states)
    assert done.destination.endswith("Practical Magic (1998).mkv")


async def test_cleared_preset_identifies_as_usual(harness):
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.scanning)
        await h.m.set_preset(job.id, Preset(media=MediaKind.movie, candidate=FILM))
        await h.m.set_preset(job.id, None)
        job = await h.wait_job(lambda j: j.state == JobState.review, timeout=60)
    assert job.identity is None and job.preset is None


async def test_film_preset_after_the_scan_confirms(harness):
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review, timeout=60)
        await h.m.set_preset(job.id, Preset(media=MediaKind.movie, candidate=FILM))
        done = await h.wait_job(lambda j: j.state == JobState.done, timeout=60)
    assert done.identity.title == "Practical Magic"


async def test_tv_preset_after_the_scan_of_a_film_is_refused(harness):
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review, timeout=60)
        with pytest.raises(ValueError, match="Review"):
            await h.m.set_preset(job.id, Preset(media=MediaKind.tv, show=Show(tmdb_id=1, name="X")))


async def test_tv_preset_maps_the_named_season(harness, settings):
    settings.tmdb_api_key = SecretStr("k")
    f = load("sarah-duck")
    show = Show(tmdb_id=f["show"]["id"], name="Sarah & Duck", year=2013)
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "sarah-duck")
        async with harness(SD) as h:
            job = await h.wait_job(lambda j: j.state == JobState.scanning)
            await h.m.set_preset(job.id, Preset(media=MediaKind.tv, show=show, season=3, disc_number=1,
                                                start_episode=21))
            job = await h.wait_job(lambda j: j.state == JobState.review and j.tv and j.tv.show, timeout=60)
    plan = job.tv
    assert job.media == MediaKind.tv and job.preset is None
    assert plan.show.name == "Sarah & Duck" and plan.show.score == 1.0
    assert plan.season == 3 and not plan.compilation and plan.start_episode == 21
    assert plan.label_placed  # placed by hand, so auto-confirm may take it from here
    first = min((m for m in plan.mapping if m.episodes), key=lambda m: m.title_index)
    assert first.episodes[0].episode == 21


async def test_film_preset_on_a_tv_looking_disc(harness):
    async with harness(SD) as h:
        job = await h.wait_job(lambda j: j.state == JobState.scanning)
        await h.m.set_preset(job.id, Preset(media=MediaKind.movie, candidate=FILM))
        done = await h.wait_job(lambda j: j.state == JobState.done, timeout=60)
    assert done.media == MediaKind.movie and done.tv is None and not done.rip.all_titles


def test_preset_needs_a_pick():
    with pytest.raises(ValidationError):
        Preset(media=MediaKind.movie)
    with pytest.raises(ValidationError):
        Preset(media=MediaKind.tv, candidate=FILM)
