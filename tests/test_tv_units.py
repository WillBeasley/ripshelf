"""TV building blocks, such as labels, disc shape, alignment, name matching, shows and naming."""

from __future__ import annotations

from pathlib import Path

import pytest

from ripshelf.makemkv.parse import parse_info
from ripshelf.models import Episode, EpisodeRef, Show, TitleInfo, TitleMap, TitleRole
from ripshelf.tv import mapping as tvmap
from ripshelf.tv import naming, shape, tmdb_tv
from ripshelf.tv.labels import parse_tv_label

from .tvfix import load

FIX = Path(__file__).parent / "fixtures"


def title(i: int, minutes: float, size: int | None = None, segs: str | None = None) -> TitleInfo:
    return TitleInfo(index=i, duration_s=int(minutes * 60), size_bytes=size or int(minutes * 1e7) + i,
                     chapters=1, segments_map=segs)


def season(name: str, n: int) -> list[Episode]:
    return tmdb_tv.to_episodes(load(name)["seasons"][str(n)])


# --- labels ----------------------------------------------------------------------------


@pytest.mark.parametrize("label, show, season_, disc, vol, tv", [
    ("FAWLTY_TOWERS_S1_D2", "Fawlty Towers", 1, 2, None, True),
    ("FRIENDS_S03D04", "Friends", 3, 4, None, True),
    ("THE_OFFICE_SEASON_2_DISC_1", "The Office", 2, 1, None, True),
    ("DOCTOR_WHO_SERIES_1_VOL_3", "Doctor Who", 1, None, 3, True),
    ("24_SEASON_4_DISC_2", "24", 4, 2, None, True),
    ("BAND_OF_BROTHERS_D3", "Band of Brothers", None, 3, None, False),
    ("SHERLOCK_COMPLETE_BOX_SET", "Sherlock", None, None, None, True),
    ("SARAH_&_DUCK_BROLLY_BUS", "Sarah & Duck Brolly Bus", None, None, None, False),
    ("SPIDERMAN", "Spiderman", None, None, None, False),
    ("SEVEN", "Seven", None, None, None, False),
    ("", "", None, None, None, False),
    (None, "", None, None, None, False),
])
def test_tv_labels(label, show, season_, disc, vol, tv):
    t = parse_tv_label(label)
    assert (t.show, t.season, t.disc, t.volume, t.looks_like_tv) == (show, season_, disc, vol, tv)


# --- disc shape --------------------------------------------------------------------------


def test_real_dvd_compilation_shape():
    from ripshelf.jobs import to_title_info

    scan = parse_info((FIX / "scan-dvd-sarah-duck.log").read_text())
    sh = shape.analyse([to_title_info(t) for t in scan.titles], bluray=False)
    assert sh.looks_like_tv and sh.play_all == [0] and sh.episodes == list(range(1, 11))
    assert not sh.same_as, "same-length DVD episodes are not duplicates"


def test_real_movie_with_featurettes_is_not_tv():
    from ripshelf.jobs import to_title_info

    scan = parse_info((FIX / "scan-uhd-wild-robot.log").read_text())
    sh = shape.analyse([to_title_info(t) for t in scan.titles], bluray=True)
    assert not sh.looks_like_tv and sh.feature == 0


def test_box_set_disc_with_play_all_and_bluray_order():
    # Play-all lists the episodes' clips in broadcast order, which differs from title order.
    ts = [title(0, 4 * 30, segs="10,11,12,13"), title(1, 30, segs="12"), title(2, 30.2, segs="10"),
          title(3, 29.8, segs="13"), title(4, 30.1, segs="11"), title(5, 3, segs="20")]
    sh = shape.analyse(ts, bluray=True)
    assert sh.play_all == [0] and sh.episodes == [1, 2, 3, 4] and sh.roles[5] == TitleRole.extra
    order = shape.play_all_order(ts[0], [t for t in ts if t.index in sh.episodes], bluray=True)
    assert order == [2, 4, 1, 3]


def test_play_all_needs_the_sum_to_match():
    near = [title(0, 4 * 30 + 0.1), *[title(i, 30) for i in range(1, 5)]]
    assert shape.analyse(near, bluray=False).play_all == [0]
    off = [title(0, 4 * 30 + 9), *[title(i, 30) for i in range(1, 5)]]
    assert shape.analyse(off, bluray=False).play_all == []


def test_play_all_with_only_one_episode_is_not_a_play_all():
    ts = [title(0, 60), title(1, 30)]
    assert shape.analyse(ts, bluray=False).play_all == []


def test_bluray_duplicates_share_clips_and_two_part_titles():
    ts = [title(1, 45, segs="1"), title(2, 45, segs="2"), title(3, 45.1, segs="1"), title(4, 90, segs="3,4"),
          title(5, 44, segs="5")]
    sh = shape.analyse(ts, bluray=True)
    assert sh.roles[3] == TitleRole.duplicate and sh.same_as[3] == 1
    assert sh.roles[4] == TitleRole.episode and "two-part" in sh.notes[4]


def test_dvd_exact_size_duplicates():
    ts = [title(1, 22, size=1000), title(2, 22, size=1000), title(3, 22, size=1001), title(4, 23)]
    sh = shape.analyse(ts, bluray=False)
    assert sh.roles[2] == TitleRole.duplicate and sh.roles[3] == TitleRole.episode


def test_shape_with_no_episode_length_titles():
    sh = shape.analyse([title(0, 1), title(1, 2)], bluray=False)
    assert not sh.looks_like_tv and set(sh.roles.values()) == {TitleRole.extra}
    assert shape.analyse([], bluray=False).roles == {}


def test_shape_with_tmdb_window():
    ts = [title(1, 7), title(2, 7), title(3, 20)]
    sh = shape.analyse(ts, bluray=False, episode_window=(6 * 60, 8 * 60))
    assert sh.episodes == [1, 2] and sh.roles[3] == TitleRole.extra



@pytest.mark.parametrize("rip_extras, want", [(True, TitleRole.extra), (False, TitleRole.skip)])
def test_extras_start_skipped_when_settings_say_so(rip_extras, want):
    from types import SimpleNamespace

    from ripshelf.tvjobs import TvMixin

    m = TvMixin()
    m.s = SimpleNamespace(tv_rip_extras=rip_extras)
    ts = [title(1, 7), title(2, 7), title(3, 20)]
    job = SimpleNamespace(titles=ts)
    rows = {r.title_index: r for r in m._shape_mapping(job, shape.analyse(ts, bluray=False, episode_window=(6 * 60, 8 * 60)))}
    assert rows[1].role == TitleRole.episode and rows[3].role == want
    assert ("not ripped" in (rows[3].note or "")) == (not rip_extras)

# --- alignment ---------------------------------------------------------------------------


def test_fawlty_towers_disc_1_aligns_with_runtimes():
    eps = season("fawlty-towers", 1)
    ts = [title(i, e.runtime + 0.5) for i, e in enumerate(eps[:3], 1)]
    got = tvmap.propose(ts, {}, eps, 1, pal=False)
    assert [m.episodes[0].episode for m in got] == [1, 2, 3]
    assert all("matches" in m.note for m in got)


def test_disc_two_starts_from_the_collection_point():
    eps = season("fawlty-towers", 1)
    ts = [title(i, eps[i + 2].runtime) for i in range(1, 4)]
    got = tvmap.propose(ts, {}, eps, 4, pal=False)
    assert [m.episodes[0].episode for m in got] == [4, 5, 6]


def test_pal_speed_up_runtimes():
    eps = season("fawlty-towers", 1)
    ts = [title(i, e.runtime * 23.976 / 25) for i, e in enumerate(eps, 1)]
    got = tvmap.propose(ts, {}, eps, 1, pal=True)
    assert [m.episodes[0].episode for m in got] == [1, 2, 3, 4, 5, 6]
    assert any("PAL" in m.note for m in got)


def test_two_part_story_on_one_title():
    eps = season("doctor-who", 1)  # 4 "Aliens of London (1)" 45 + 5 "World War Three (2)" 43
    ts = [title(1, 47), title(2, 47), title(3, 47), title(4, 88), title(5, 48)]
    got = tvmap.propose(ts, {}, eps, 1, pal=False)
    assert [[e.episode for e in m.episodes] for m in got] == [[1], [2], [3], [4, 5], [6]]


def test_more_titles_than_episodes_left_become_extras():
    eps = season("fawlty-towers", 1)
    ts = [title(i, 30) for i in range(1, 5)]
    got = tvmap.propose(ts, {}, eps, 5, pal=False)
    roles = [m.role for m in got]
    assert roles.count(TitleRole.episode) == 2 and roles.count(TitleRole.extra) == 2


def test_missing_runtimes_map_in_order():
    eps = [Episode(season=1, episode=n, name=f"E{n}") for n in range(1, 5)]
    ts = [title(i, 25) for i in range(1, 5)]
    got = tvmap.propose(ts, {}, eps, 1, pal=False)
    assert [m.episodes[0].episode for m in got] == [1, 2, 3, 4]
    assert "no runtime" in got[0].note


def test_obviously_wrong_lengths_are_left_as_extras():
    eps = season("fawlty-towers", 1)
    got = tvmap.propose([title(1, 30), title(2, 3)], {}, eps, 1, pal=False)
    assert got[0].role == TitleRole.episode and got[1].role == TitleRole.extra


def test_others_are_kept_in_title_order():
    eps = season("fawlty-towers", 1)
    pa = TitleMap(title_index=0, duration_s=5400, role=TitleRole.play_all)
    got = tvmap.propose([title(1, 30)], {0: pa}, eps, 1, pal=False)
    assert [m.title_index for m in got] == [0, 1]


def test_align_edge_cases():
    assert tvmap.align([], [], False) == []
    assert tvmap.align([title(1, 30)], [], False) == [None]
    assert tvmap.runtime_cost(1800, None, False) == tvmap.UNKNOWN_RUNTIME_COST
    assert tvmap.runtime_cost(1800, 30, False) == 0
    assert tvmap.runtime_cost(600, 30, False) == 1.0


# --- name matching (compilations) -----------------------------------------------------------


@pytest.mark.parametrize("text, want", [
    ("Brolly Bus, Rainbow Race, Hair Cut", ["Brolly Bus", "Rainbow Race", "Hair Cut"]),
    ("1. Brolly Bus\n2) Rainbow Race\n- Hair Cut\n\n", ["Brolly Bus", "Rainbow Race", "Hair Cut"]),
    ("Brolly Bus; Rainbow Race | Hair Cut", ["Brolly Bus", "Rainbow Race", "Hair Cut"]),
    ("“Brolly Bus”\n'Hair Cut'", ["Brolly Bus", "Hair Cut"]),
    ("   ", []),
])
def test_split_names(text, want):
    assert tvmap.split_names(text) == want


def test_match_names_prefers_real_seasons_and_never_reuses():
    eps = [Episode(season=0, episode=1, name="Brolly Bus"), Episode(season=3, episode=34, name="Brolly Bus"),
           Episode(season=1, episode=1, name="Lots of Shallots")]
    got = tvmap.match_names(["Brolly Bus", "Brolly Bus", "Nonsense Words Here"], eps)
    assert (got[0][1].season, got[0][1].episode) == (3, 34)
    assert (got[1][1].season, got[1][1].episode) == (0, 1)  # the remaining one
    assert got[2][1] is None


def test_match_unicode_and_typos():
    eps = [Episode(season=1, episode=2, name="If You’re Happy and You Know It")]
    assert tvmap.match_names(["If You're Happy and You Know It"], eps)[0][1] is not None


# --- consistency ---------------------------------------------------------------------------


def ref(s, e):
    return EpisodeRef(season=s, episode=e)


@pytest.mark.parametrize("rows, problem", [
    ([TitleMap(title_index=1, duration_s=1, role=TitleRole.episode)], "has none"),
    ([TitleMap(title_index=1, duration_s=1, role=TitleRole.extra, episodes=[ref(1, 1)])], "marked extra"),
    ([TitleMap(title_index=1, duration_s=1, role=TitleRole.episode, episodes=[ref(1, 1)]),
      TitleMap(title_index=2, duration_s=1, role=TitleRole.episode, episodes=[ref(1, 1)])], "on both"),
    ([TitleMap(title_index=1, duration_s=1, role=TitleRole.episode, episodes=[ref(1, 1), ref(1, 3)])],
     "consecutive"),
    ([TitleMap(title_index=1, duration_s=1, role=TitleRole.episode, episodes=[ref(1, 1), ref(1, 2), ref(1, 3)])],
     "at most two"),
    ([TitleMap(title_index=1, duration_s=1, role=TitleRole.episode, episodes=[ref(7, 1)])], "not an episode"),
    ([TitleMap(title_index=1, duration_s=1, role=TitleRole.skip)], "no title is mapped"),
])
def test_problems(rows, problem):
    known = {(1, n): Episode(season=1, episode=n) for n in range(1, 4)}
    assert any(problem in p for p in tvmap.problems(rows, known))


def test_no_problems_for_a_good_mapping():
    rows = [TitleMap(title_index=1, duration_s=1, role=TitleRole.episode, episodes=[ref(1, 1), ref(1, 2)]),
            TitleMap(title_index=2, duration_s=1, role=TitleRole.play_all)]
    assert tvmap.problems(rows, None) == []


# --- shows --------------------------------------------------------------------------------


def shows(name: str) -> list[Show]:
    return [tmdb_tv.to_show(r) for r in load(name)["search"]["results"]]


def test_doctor_who_confidential_never_wins():
    ranked = tmdb_tv.rank_shows(shows("doctor-who"), ["Doctor Who"], 45 * 60)
    names = {s.name: s for s in ranked}
    assert names["Doctor Who Confidential"].excluded and names["Totally Doctor Who"].excluded
    assert ranked[0].name == "Doctor Who" and not ranked[0].excluded


def test_year_breaks_doctor_who_tie():
    ranked = tmdb_tv.rank_shows(shows("doctor-who"), ["Doctor Who"], None, year=2005)
    assert ranked[0].year == 2005


def test_label_with_episode_name_finds_the_show():
    ranked = tmdb_tv.rank_shows(shows("sarah-duck"), ["Sarah & Duck Brolly Bus"], 421)
    assert ranked[0].name == "Sarah & Duck" and ranked[0].score >= 0.9


def test_runtime_disagreement_is_evidence():
    s = tmdb_tv.to_show({**load("fawlty-towers")["show"]})
    scored = tmdb_tv.score_show(s, ["Fawlty Towers"], 90 * 60)
    assert any(e.ok is False and "TMDB says 30" in e.text for e in scored.evidence)


def test_companion_series_without_genre_is_excluded():
    s = Show(tmdb_id=1, name="Sherlock: Unscripted")
    assert tmdb_tv.score_show(s, ["Sherlock"], None).excluded


def test_queries_for():
    assert tmdb_tv.queries_for("Sarah & Duck Brolly Bus") == [
        "Sarah & Duck Brolly Bus", "Sarah & Duck Brolly", "Sarah & Duck", "Sarah &"]
    assert tmdb_tv.queries_for("") == []
    assert tmdb_tv.queries_for("Seinfeld") == ["Seinfeld"]


def test_to_show_and_episodes_tolerate_junk():
    s = tmdb_tv.to_show({"id": 5, "name": "  ", "original_name": "原题", "first_air_date": "x",
                         "episode_run_time": "30", "seasons": [None, {"season_number": "1"},
                                                                {"season_number": 2, "episode_count": "x"}],
                         "genres": [{"id": 18}, "bad"], "poster_path": 3, "vote_count": "many"})
    assert s.name == "原题" and s.year is None and s.episode_runtime is None
    assert s.seasons == {2: 0} and s.genre_ids == [18] and s.poster_url is None and s.vote_count is None
    eps = tmdb_tv.to_episodes({"episodes": [None, {"season_number": 1}, {"season_number": 1, "episode_number": -1},
                                            {"season_number": 1, "episode_number": 2, "name": 7, "runtime": 0}]})
    assert len(eps) == 1 and eps[0].name == "" and eps[0].runtime is None
    assert tmdb_tv.to_episodes({}) == []


# --- naming -------------------------------------------------------------------------------


def test_episode_naming(tmp_path):
    show = Show(tmdb_id=1, name="Doctor Who", year=2005)
    e = [Episode(season=1, episode=4, name="Aliens of London (1)"),
         Episode(season=1, episode=5, name="World War Three (2)")]
    p = naming.episode_path(tmp_path / "TV", show, e[:1])
    assert p == tmp_path / "TV/Doctor Who (2005)/Season 01/Doctor Who - S01E04 - Aliens of London (1).mkv"
    p2 = naming.episode_path(tmp_path, show, e)
    assert p2.name == "Doctor Who - S01E04-E05 - Aliens of London (1) & World War Three (2).mkv"
    p3 = naming.episode_path(tmp_path, Show(tmdb_id=2, name="Who? What: Where/"), [Episode(season=0, episode=1)])
    assert p3.parent.name == "Season 00" and p3.name == "Who What - Where - S00E01.mkv"
    long = naming.episode_path(tmp_path, show, [Episode(season=1, episode=1, name="x" * 400)])
    assert len(long.name.encode()) <= 255 and "S01E01" in long.name
    assert naming.extra_path(tmp_path, show, None, 7).name == "Disc - title 7.mkv"
    assert naming.disc_meta_folder(tmp_path, show, "../EVIL", 3).parent.name == ".disc"


# --- order check (needs ffmpeg) -------------------------------------------------------------

import shutil  # noqa: E402
import subprocess  # noqa: E402

from ripshelf.tv import verify  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def clip(path: Path, colours: list[str]) -> Path:
    inputs = []
    for c in colours:
        inputs += ["-f", "lavfi", "-i", f"color=c={c}:s=160x90:d=2:r=5"]
    filt = "".join(f"[{i}:v]" for i in range(len(colours))) + f"concat=n={len(colours)}:v=1[v]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", filt, "-map", "[v]",
                    "-c:v", "mpeg2video", str(path)], check=True)
    return path


@needs_ffmpeg
async def test_order_check(tmp_path):
    cols = ["red", "green", "blue"]
    eps = [(i, clip(tmp_path / f"e{i}.mkv", [c])) for i, c in enumerate(cols, 1)]
    pa = clip(tmp_path / "all.mkv", cols)
    ok = await verify.verify_order("ffmpeg", "ffprobe", pa, eps)
    assert ok.verified is True and ok.per_title == {1: True, 2: True, 3: True}
    swapped = await verify.verify_order("ffmpeg", "ffprobe", pa, [eps[1], eps[0], eps[2]])
    assert swapped.verified is False and "2, 1" in swapped.note
    short = await verify.verify_order("ffmpeg", "ffprobe", eps[0][1], eps)
    assert short.verified is None and "shorter" in short.note
    assert (await verify.verify_order("ffmpeg", "ffprobe", pa, eps[:1])).verified is None
    assert await verify.thumbnail("ffmpeg", eps[0][1], tmp_path / "t" / "1.jpg", length=2)


async def test_order_check_without_ffmpeg(tmp_path):
    a = tmp_path / "a.mkv"
    a.write_bytes(b"x")
    res = await verify.verify_order("/nonexistent/ffmpeg", "/nonexistent/ffprobe", a, [(1, a), (2, a)])
    assert res.verified is None and "ffprobe" in res.note
    assert await verify.duration("/nonexistent/ffprobe", a) is None
    assert not await verify.thumbnail("/nonexistent/ffmpeg", a, tmp_path / "t.jpg")


def test_frames_match_tolerance():
    a = bytes([100] * 10)
    assert verify.frames_match(a, a)
    assert verify.frames_match(a, bytes([101] * 10))
    assert not verify.frames_match(a, bytes([150] * 10))
