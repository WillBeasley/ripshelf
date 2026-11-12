"""A save writes only what its copy of the job changed, so copies held by several tasks
(the pipeline across a rip, the user's picks, the scans) can't undo one another."""

from __future__ import annotations

import json

from ripshelf.db import Database, merge, now
from ripshelf.models import Candidate, CoverScan, JobState, MediaKind


def test_merge_keeps_both_sides_changes():
    base = {"a": 1, "b": {"x": 1, "y": 1}, "c": [1], "d": 1}
    mine = {"a": 2, "b": {"x": 2, "y": 1}, "c": [1], "d": 1}
    stored = {"a": 1, "b": {"x": 1, "y": 3}, "c": [1, 2], "d": 5}
    assert merge(base, mine, stored) == {"a": 2, "b": {"x": 2, "y": 3}, "c": [1, 2], "d": 5}


def test_merge_same_value_changed_twice_takes_the_later_save():
    assert merge({"a": 1, "l": [1]}, {"a": 2, "l": [1, 2]}, {"a": 3, "l": [1, 3]}) == {"a": 2, "l": [1, 2]}


def test_merge_adds_and_removes_keys():
    base, mine, stored = {"m": {"1": "a", "2": "b"}}, {"m": {"2": "b", "3": "c"}}, {"m": {"1": "a", "2": "b", "4": "d"}}
    assert merge(base, mine, stored) == {"m": {"2": "b", "3": "c", "4": "d"}}
    assert merge({"k": 1}, {}, {"k": 1, "other": 2}) == {"other": 2}
    # An object replaced by something else (or the other way) is taken whole.
    assert merge({"v": {"x": 1}}, {"v": None}, {"v": {"x": 1, "y": 2}}) == {"v": None}
    assert merge({"v": None}, {"v": {"x": 1}}, {"v": None}) == {"v": {"x": 1}}


def scan(i: str) -> CoverScan:
    return CoverScan(id=i, kind="front", source="upload", created_at=now(), content_type="image/jpeg",
                     width=1, height=1, sha256="0" * 64, size_bytes=1)


def test_a_save_from_an_old_copy_keeps_what_was_saved_meanwhile(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    made = db.create_job("d1", "/dev/sr0")
    pipeline = db.get_job(made.id)  # held across a rip

    user = db.get_job(made.id)
    user.scans.append(scan("s1"))
    user.media = MediaKind.tv
    db.save_job(user)

    pipeline.rip.file = "a.mkv"
    pipeline.quick_fp = "abc"
    db.save_job(pipeline)

    now = db.get_job(made.id)
    assert [s.id for s in now.scans] == ["s1"] and now.media == MediaKind.tv, "the user's changes stand"
    assert now.rip.file == "a.mkv" and now.quick_fp == "abc", "and so do the pipeline's"
    assert [s.id for s in pipeline.scans] == ["s1"], "the old copy is brought up to date by its save"

    # The copy that saved last carries on from the merged job, not from what it first read.
    pipeline.rip.file = "b.mkv"
    db.save_job(pipeline)
    assert [s.id for s in db.get_job(made.id).scans] == ["s1"]
    db.close()


def test_state_column_follows_the_merged_job(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    made = db.create_job("d1", "/dev/sr0")
    old = db.get_job(made.id)
    newer = db.get_job(made.id)
    newer.state = JobState.review
    db.save_job(newer)
    old.identity = Candidate(title="Moonlit Orchard", year=1998, source="manual")
    db.save_job(old)
    assert [j.id for j in db.list_jobs([JobState.review.value])] == [made.id]
    assert db.get_job(made.id).identity.title == "Moonlit Orchard"
    db.close()


def test_a_job_saved_by_an_older_version_loads_and_saves(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    made = db.create_job("d1", "/dev/sr0")
    row = json.loads(db.conn.execute("SELECT data FROM jobs WHERE id=?", (made.id,)).fetchone()["data"])
    row.pop("scans"), row.update(scans_rev=4)  # a field added since, and one that's gone
    db.conn.execute("UPDATE jobs SET data=? WHERE id=?", (json.dumps(row), made.id))
    a, b = db.get_job(made.id), db.get_job(made.id)
    a.scans.append(scan("s1"))
    db.save_job(a)
    b.quick_fp = "abc"
    db.save_job(b)
    now = db.get_job(made.id)
    assert [s.id for s in now.scans] == ["s1"] and now.quick_fp == "abc"
    db.close()
