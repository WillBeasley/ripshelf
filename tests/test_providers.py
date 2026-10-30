"""Metadata providers. The Settings lists, TheDiscDB, TVmaze and OMDb (all HTTP mocked)."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from ripshelf import checks, discfs, providers
from ripshelf.config import Settings, load_settings
from ripshelf.dev.fake import FakeDrives
from ripshelf.identify.tmdb import API as TMDB
from ripshelf.identify.tmdb import Tmdb
from ripshelf.main import create_app
from ripshelf.models import (
    DiscMatch,
    DiscMatchTitle,
    EpisodeRef,
    JobState,
    MediaKind,
    SettingsUpdate,
    Show,
    TitleInfo,
    TitleRole,
)
from ripshelf.providers import films, thediscdb
from ripshelf.providers.omdb import API as OMDB
from ripshelf.providers.omdb import Omdb, OmdbError
from ripshelf.providers.omdb import to_candidate as omdb_candidate
from ripshelf.providers.thediscdb import SITE, DiscDbError, TheDiscDb
from ripshelf.providers.tvmaze import API as MAZE
from ripshelf.providers.tvmaze import Tvmaze, TvmazeError
from ripshelf.tv import tmdb_tv

from .conftest import posix_only
from .test_tv_sources import BWE, Tv, season1
from .tvfix import load, mock_show

ROBOT = "/dev/fake-sr2"  # In THE_WILD_ROBOT UHD, title 0 is 00801.mpls (1:41:41), title 3 00149.mpls
SD = "/dev/fake-sr0"  # In SARAH_&_DUCK_BROLLY_BUS DVD, title 0 is the play-all, 1-10 the episodes (7:01)
SD_EPISODES = [34, 26, 21, 23, 24, 25, 29, 37, 30, 28]

# A trimmed record as /api/dischash answers it (TheDiscDB's 'Round Midnight Criterion disc).
ROUND_MIDNIGHT = {
    "global_disc_id": "BDE5486DBE5FA6E7B9D66485CB9AA774C527D8EE", "format": "Blu-Ray",
    "content_hash": "2D61282D8DA5EAC2CA87B451BCE9A055",
    "media": {"title": "'Round Midnight", "full_title": "'Round Midnight (1986)", "year": 1986, "type": "Movie",
              "external_ids": {"tmdb": "14670", "imdb": "tt0090557"}},
    "release": {"slug": "2022-criterion-blu-ray", "title": "Criterion Blu-ray", "year": 2022},
    "disc": {"slug": "blu-ray", "name": "’ROUND MIDNIGHT", "index": 1},
    "titles": [
        {"index": 0, "source_file": "00001.mpls", "segment_map": "274", "duration": "2:11:34", "size": 36380633088,
         "item": {"title": "'Round Midnight", "type": "MainMovie"}},
        {"index": 1, "source_file": "00230.m2ts", "segment_map": "230", "duration": "0:00:00", "size": 239616},
        {"index": 2, "source_file": "00002.mpls", "segment_map": "300", "duration": "0:25:10", "size": 1,
         "item": {"title": "Bertrand Tavernier on 'Round Midnight", "type": "Extra"}},
        "not a title",
    ],
}


@pytest.fixture
def keyed(settings):
    settings.tmdb_api_key = SecretStr("k")
    return settings


# --- the lists ---------------------------------------------------------------------------


def test_default_lists_and_what_is_used(settings):
    lists = providers.lists(settings)
    assert [r["name"] for r in lists["movie"]] == ["thediscdb", "tmdb", "omdb"]
    assert [r["name"] for r in lists["tv"]] == ["thediscdb", "tmdb", "tvdb", "tvmaze"]
    assert [r["name"] for r in lists["episodes"]] == ["tvdb", "tmdb", "tvmaze"]
    # Keyless providers only, until keys are set; OMDb starts switched off.
    assert providers.enabled(settings, "movie") == ["thediscdb"]
    assert providers.enabled(settings, "tv") == ["thediscdb", "tvmaze"]
    settings.tmdb_api_key, settings.tvdb_api_key, settings.omdb_api_key = (SecretStr("k"),) * 3
    assert providers.enabled(settings, "movie") == ["thediscdb", "tmdb"]
    assert providers.enabled(settings, "episodes") == ["tvdb", "tmdb", "tvmaze"]


def test_normalise_keeps_order_and_fills_gaps():
    got = providers.normalise({"movie": [{"name": "omdb", "enabled": True}, {"name": "tvmaze"}, {"name": "omdb"},
                                         {"name": "nope"}, "junk", {"name": "tmdb", "enabled": False}],
                               "episodes": "junk"})
    assert got["movie"] == [{"name": "omdb", "enabled": True}, {"name": "tmdb", "enabled": False},
                            {"name": "thediscdb", "enabled": True}]
    assert got["episodes"] == providers.defaults()["episodes"]
    assert got["tv"] == providers.defaults()["tv"]


@pytest.mark.parametrize("old, episodes, order", [
    ("tvdb_aired", ["tvdb", "tmdb", "tvmaze"], "aired"),
    ("tvdb_dvd", ["tvdb", "tmdb", "tvmaze"], "dvd"),
    ("tmdb", ["tmdb", "tvmaze"], "aired"),
])
def test_settings_from_before_the_lists(tmp_path, monkeypatch, old, episodes, order):
    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.json").write_text(json.dumps({"tv_episode_order": old, "tvdb_api_key": "k",
                                                    "tmdb_api_key": "t"}))
    monkeypatch.setenv("RIPSHELF_DATA_DIR", str(data))
    for k in ("SCRATCH_DIR", "LIBRARY_DIR"):
        monkeypatch.setenv(f"RIPSHELF_{k}", str(tmp_path / k.lower()))
    monkeypatch.setattr(Settings, "model_config", {**Settings.model_config, "env_file": None})
    s = load_settings()
    assert providers.enabled(s, "episodes") == episodes and s.tvdb_episode_order == order
    assert s.secret("tvdb_api_key") == "k" and s.secret("tmdb_api_key") == "t"  # keys stay where they were


def test_settings_api_lists_and_saves_providers(settings):
    with TestClient(create_app(settings, backend=FakeDrives(present=set()))) as c:
        view = c.get("/api/settings").json()
        assert [r["name"] for r in view["metadata_providers"]["movie"]] == ["thediscdb", "tmdb", "omdb"]
        info = {p["name"]: p for p in view["providers"]}
        assert info["omdb"]["needs_key"] and not info["omdb"]["configured"] and info["omdb"]["check"] == "omdb"
        assert info["thediscdb"]["configured"] and info["tvmaze"]["lists"] == ["tv", "episodes"]
        body = {"metadata_providers": {"movie": [{"name": "omdb", "enabled": True}, {"name": "tmdb"}]},
                "omdb_api_key": "o", "tvdb_episode_order": "dvd"}
        view = c.put("/api/settings", json=body).json()
        assert view["metadata_providers"]["movie"] == [
            {"name": "omdb", "enabled": True}, {"name": "tmdb", "enabled": True},
            {"name": "thediscdb", "enabled": True}]
        assert view["omdb_api_key_set"] and view["tvdb_episode_order"] == "dvd" and "omdb_api_key" not in view
        assert c.put("/api/settings", json={"metadata_providers": {"movie": [{"name": "fanart"}]}}).status_code == 422
        assert c.put("/api/settings", json={"metadata_providers": None}).status_code == 200
    saved = json.loads(settings.settings_file.read_text())
    assert saved["metadata_providers"]["movie"][0]["name"] == "omdb" and saved["omdb_api_key"] == "o"
    assert providers.enabled(settings, "movie") == ["omdb", "thediscdb"]


# --- TheDiscDB fingerprints -------------------------------------------------------------------


def test_aacs_and_dvd_disc_ids():
    # The vectors of TheDiscDB's own tests (AacsDiscIdTests, DvdDiscIdTests).
    assert thediscdb.aacs_disc_id(b"hello world") == "2AAE6C35C94FCFB415DBE95F408B9CE91EE846ED"
    assert thediscdb.aacs_disc_id(b"") == "DA39A3EE5E6B4B0D3255BFEF95601890AFD80709"
    vmg = bytearray(b"\xab" * 0x100)
    vmg[0x3E:0x40] = (20).to_bytes(2, "big")
    vts = [bytes([i, i + 100]) for i in range(12)]
    assert thediscdb.dvd_disc_id(bytes(vmg), vts) == hashlib.md5(bytes(vmg) + b"".join(vts[:9])).hexdigest().upper()
    vmg[0x3E:0x40] = (3).to_bytes(2, "big")
    want = hashlib.md5(bytes(vmg) + b"\x0a\x0b" + b"\x1e\x1f").hexdigest().upper()
    assert thediscdb.dvd_disc_id(bytes(vmg), [b"\x0a\x0b", None, b"\x1e\x1f"]) == want  # a missing IFO is skipped
    assert thediscdb.dvd_disc_id(b"short", []) is None


def test_content_hash_matches_thediscdb():
    # TheDiscDB's /api/hash gave 2DE65E70… for these two sizes (checked once by hand).
    sizes = [("BDMV/STREAM/00002.m2ts", 239616), ("BDMV/STREAM/00001.m2ts", 36380633088),
             ("BDMV/STREAM/SSIF/00001.ssif", 5), ("BDMV/index.bdmv", 7)]
    assert thediscdb.content_hash(sizes) == "2DE65E702D354593205EDFCD39DE5D4D"
    dvd = [("VIDEO_TS/VTS_01_1.VOB", 3), ("VIDEO_TS/VIDEO_TS.IFO", 1)]
    assert thediscdb.content_hash(dvd) == hashlib.md5(struct.pack("<qq", 1, 3)).hexdigest().upper()
    assert thediscdb.content_hash([("AUDIO_TS/x", 1)]) is None


def test_fingerprints_from_a_structure_read(tmp_path):
    (tmp_path / "AACS" / "DUPLICATE").mkdir(parents=True)
    (tmp_path / "AACS" / "DUPLICATE" / "Unit_Key_RO.inf").write_bytes(b"hello world")
    (tmp_path / discfs.SIZES).write_text("BDMV/STREAM/00001.m2ts\t36380633088\nBDMV/STREAM/00002.m2ts\t239616\nbad\n")
    assert thediscdb.fingerprints(tmp_path) == {"disc_id": "2AAE6C35C94FCFB415DBE95F408B9CE91EE846ED",
                                                "content_hash": "2DE65E702D354593205EDFCD39DE5D4D"}
    dvd = tmp_path / "dvd"
    (dvd / "VIDEO_TS").mkdir(parents=True)
    (dvd / "VIDEO_TS" / "VIDEO_TS.IFO").write_bytes(bytes(0x100))
    assert thediscdb.fingerprints(dvd) == {"disc_id": hashlib.md5(bytes(0x100)).hexdigest().upper()}
    assert thediscdb.fingerprints(tmp_path / "missing") == {}


def clpi(packets: int) -> bytes:
    """A clip info file. A header, then the ClipInfo block at byte 40 with its packet count."""
    return b"HDMV0300" + bytes(32) + bytes(16) + packets.to_bytes(4, "big") + bytes(20)


def test_blu_ray_sizes_from_clip_info(tmp_path):
    # A worker that never sent the sizes listing. A Blu-ray's are in its clip info files
    # (packets x 192 bytes, checked against a real UHD's .m2ts files).
    ci = tmp_path / "BDMV" / "CLIPINF"
    ci.mkdir(parents=True)
    (ci / "00001.clpi").write_bytes(clpi(36380633088 // 192))
    (ci / "00002.clpi").write_bytes(clpi(239616 // 192))
    assert discfs.clip_sizes(tmp_path) == [("BDMV/STREAM/00001.m2ts", 36380633088),
                                           ("BDMV/STREAM/00002.m2ts", 239616)]
    assert thediscdb.fingerprints(tmp_path)["content_hash"] == "2DE65E702D354593205EDFCD39DE5D4D"
    # The listing wins when there is one; a damaged clip info file gives nothing.
    (tmp_path / discfs.SIZES).write_text("BDMV/STREAM/00001.m2ts\t5\n")
    assert thediscdb.fingerprints(tmp_path)["content_hash"] == thediscdb.content_hash([("BDMV/STREAM/00001.m2ts", 5)])
    (tmp_path / discfs.SIZES).unlink()
    (ci / "00003.clpi").write_bytes(b"junk")
    assert discfs.clip_sizes(tmp_path) == [] and "content_hash" not in thediscdb.fingerprints(tmp_path)


def test_7zip_listing():
    text = ("Listing archive: /dev/sr0\n\n--\nPath = /dev/sr0\nType = Udf\n\n----------\n"
            "Path = BDMV/STREAM\nFolder = +\nSize = 0\n\n"
            "Path = BDMV/STREAM/00001.m2ts\nFolder = -\nSize = 36380633088\n\n"
            "Path = VIDEO_TS\\VTS_01_1.VOB\nSize = 12\nAttributes = A\n\n"
            "Path = odd\nSize = ?\n")
    assert discfs.parse_listing(text) == [("BDMV/STREAM/00001.m2ts", 36380633088), ("VIDEO_TS/VTS_01_1.VOB", 12)]
    assert discfs.parse_listing("Path = /dev/sr0\nSize = 1\n") == []


@posix_only
async def test_structure_read_lists_sizes(tmp_path):
    script = tmp_path / "7z"
    script.write_text("#!/bin/sh\nif [ \"$1\" = l ]; then printf -- '----------\\nPath = BDMV/STREAM/00001.m2ts\\n"
                      "Size = 5\\n\\n'; else mkdir -p \"${4#-o}/BDMV\"; echo x > \"${4#-o}/BDMV/index.bdmv\"; fi\n")
    script.chmod(0o755)
    files = await discfs.extract_structure(str(script), "/dev/null", tmp_path / "out")
    assert files == ["BDMV/index.bdmv"]
    assert discfs.read_sizes(tmp_path / "out") == [("BDMV/STREAM/00001.m2ts", 5)]
    assert await discfs.list_sizes(str(tmp_path / "missing"), "/dev/null") == []


# --- TheDiscDB lookups ------------------------------------------------------------------------


@respx.mock
async def test_lookup_by_disc_id_then_content_hash(discdb):
    by_id = respx.get(f"{SITE}/api/discid/AAA").mock(return_value=httpx.Response(404))
    by_hash = respx.get(f"{SITE}/api/dischash/BBB").mock(return_value=httpx.Response(200, json=ROUND_MIDNIGHT))
    db = TheDiscDb()
    db.lookup = lambda fps: discdb.real(db, fps)
    try:
        m = await db.lookup({"disc_id": "AAA", "content_hash": "BBB"})
        assert by_id.called and by_hash.called and m.by == "content_hash"
        respx.get(f"{SITE}/api/discid/CCC").mock(return_value=httpx.Response(200, json=[ROUND_MIDNIGHT]))
        assert (await db.lookup({"disc_id": "CCC"})).by == "disc_id"
        assert await db.lookup({}) is None
        respx.get(f"{SITE}/api/dischash/DDD").mock(return_value=httpx.Response(500))
        with pytest.raises(DiscDbError, match="HTTP 500"):
            await db.lookup({"content_hash": "DDD"})
        respx.get(f"{SITE}/api/dischash/EEE").mock(return_value=httpx.Response(200, text="<html>"))
        with pytest.raises(DiscDbError, match="not JSON"):
            await db.lookup({"content_hash": "EEE"})
        respx.get(f"{SITE}/api/dischash/FFF").mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(DiscDbError, match="could not reach"):
            await db.lookup({"content_hash": "FFF"})
        respx.get(f"{SITE}/api/dischash/GGG").mock(side_effect=httpx.ReadTimeout("slow"))
        with pytest.raises(DiscDbError, match="in time"):
            await db.lookup({"content_hash": "GGG"})
        respx.get(f"{SITE}/api/search").mock(return_value=httpx.Response(200, json=[{"title": "x"}, 1]))
        assert await db.search("x") == [{"title": "x"}]
    finally:
        await db.aclose()


def test_record_to_match():
    m = thediscdb.to_match(ROUND_MIDNIGHT, "disc_id")
    assert (m.media, m.title, m.year, m.tmdb_id, m.imdb_id, m.release) == (
        MediaKind.movie, "'Round Midnight", 1986, 14670, "tt0090557", "Criterion Blu-ray")
    assert [(t.index, t.kind, t.duration_s) for t in m.titles] == [(0, "main", 7894), (1, None, 0), (2, "extra", 1510)]
    assert thediscdb.summary(m) == "'Round Midnight (1986), Criterion Blu-ray, ’ROUND MIDNIGHT"
    series = {**ROUND_MIDNIGHT, "media": {"title": "Show", "type": "Series", "external_ids": {"tvdb": "12"}},
              "titles": [{"index": 3, "item": {"title": "Pilot", "type": "Episode", "season": "1", "episode": "x"}}]}
    s = thediscdb.to_match(series, "content_hash")
    assert s.media == MediaKind.tv and s.tvdb_id == 12 and s.titles[0].season == 1 and s.titles[0].episode is None
    assert thediscdb.to_match({**ROUND_MIDNIGHT, "media": {"title": "X", "type": "Boxset"}}, "disc_id") is None
    assert thediscdb.to_match({}, "disc_id") is None


def test_place_titles_on_the_scan():
    m = thediscdb.to_match(ROUND_MIDNIGHT, "disc_id")
    scan = [TitleInfo(index=5, duration_s=7894, size_bytes=1, chapters=1, source_file="00001.mpls", segments_map="274"),
            TitleInfo(index=1, duration_s=1510, size_bytes=1, chapters=1, source_file="00002.mpls", segments_map="9")]
    assert thediscdb.place_titles(m, scan) == 1  # 00002.mpls plays other clips here
    assert [t.scan_index for t in m.titles] == [5, None, None]
    dvd = DiscMatch(media=MediaKind.tv, title="x", titles=[DiscMatchTitle(index=1, duration_s=421),
                                                           DiscMatchTitle(index=2, duration_s=500)])
    scan = [TitleInfo(index=1, duration_s=422, size_bytes=1, chapters=1),
            TitleInfo(index=2, duration_s=421, size_bytes=1, chapters=1)]
    assert thediscdb.place_titles(dvd, scan) == 1 and dvd.titles[0].scan_index == 1


# --- TVmaze ---------------------------------------------------------------------------------


MAZE_BWE = {"id": 112, "name": "Boardwalk Empire", "premiered": "2010-09-19",
            "externals": {"tvrage": 1, "thetvdb": 84947, "imdb": "tt0979432"}}


@respx.mock
async def test_tvmaze_client():
    respx.get(f"{MAZE}/search/shows").mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, json=[{"score": 1, "show": MAZE_BWE}, {"show": {"name": "no id"}}, "junk"])])
    respx.get(f"{MAZE}/lookup/shows", params={"thetvdb": "1"}).mock(return_value=httpx.Response(404))
    respx.get(f"{MAZE}/lookup/shows", params={"imdb": "tt0979432"}).mock(return_value=httpx.Response(200, json=MAZE_BWE))
    eps = [{"season": 1, "number": n, "name": f"E{n}", "airdate": "2010-09-19", "runtime": 55} for n in (2, 1)]
    eps += [{"season": 1, "number": None, "name": "special"}, {"season": 2, "number": 1}, "junk",
            {"season": 1, "number": 1, "name": "dup"}]
    respx.get(f"{MAZE}/shows/112/episodes").mock(return_value=httpx.Response(200, json=eps))
    maze = Tvmaze()
    maze._sleep = lambda s: _nothing()
    try:
        assert [s["id"] for s in await maze.search("bwe")] == [112]
        assert (await maze.lookup(1, "tt0979432"))["id"] == 112
        assert await maze.lookup() is None
        got = await maze.episodes(112, 1)
        assert [(e.episode, e.name, e.runtime) for e in got] == [(1, "E1", 55), (2, "E2", 55)]
        respx.get(f"{MAZE}/shows/9/episodes").mock(return_value=httpx.Response(503))
        with pytest.raises(TvmazeError, match="HTTP 503"):
            await maze.episodes(9, 1)
    finally:
        await maze.aclose()


async def _nothing():
    return None


@respx.mock
async def test_tvmaze_finds_what_tmdb_misses():
    respx.get(f"{TMDB}/search/tv").mock(return_value=httpx.Response(200, json={"results": []}))
    respx.get(f"{MAZE}/search/shows").mock(return_value=httpx.Response(200, json=[{"show": MAZE_BWE}]))
    respx.get(f"{TMDB}/find/84947").mock(return_value=httpx.Response(200, json={"tv_results": [{"id": 1621}]}))
    respx.get(f"{TMDB}/tv/1621").mock(return_value=httpx.Response(200, json=BWE))
    tmdb, maze = Tmdb("k"), Tvmaze()
    try:
        shows = await tmdb_tv.find_shows(tmdb, "Boardwalk Empire", None, sources=["tmdb", "tvmaze"], tvmaze=maze)
        assert [s.tmdb_id for s in shows] == [1621] and shows[0].providers == ["tvmaze", "tmdb"]
        assert any(e.text == "Found through TVmaze (TMDB's search missed it)" for e in shows[0].evidence)
        respx.get(f"{MAZE}/search/shows").mock(return_value=httpx.Response(500))
        assert await tmdb_tv.find_shows(tmdb, "x", None, sources=["tvmaze"], tvmaze=maze) == []
    finally:
        await tmdb.aclose()
        await maze.aclose()


@respx.mock
async def test_episodes_from_tvmaze_without_a_tvdb_key(settings):
    respx.get(f"{TMDB}/tv/1621").mock(return_value=httpx.Response(200, json={**BWE, "external_ids": {
        "tvdb_id": 84947, "imdb_id": "tt0979432"}}))
    respx.get(f"{MAZE}/lookup/shows").mock(return_value=httpx.Response(200, json=MAZE_BWE))
    eps = [{"season": 1, "number": n, "name": f"M{n}"} for n in (1, 2)]
    respx.get(f"{MAZE}/shows/112/episodes").mock(return_value=httpx.Response(200, json=eps))
    settings.metadata_providers = {"episodes": [{"name": "tvmaze"}, {"name": "tmdb"}]}
    tmdb = Tmdb("k")
    show = Show(tmdb_id=1621, name="Boardwalk Empire")
    try:
        got, note = await Tv(settings)._episode_list(show, 1, season1(), tmdb)
        assert [(e.name, e.runtime) for e in got] == [("M1", 73), ("M2", 53)]  # TMDB's runtimes filled in
        assert note == "Episodes from TVmaze, aired order" and show.imdb_id == "tt0979432"
        respx.get(f"{MAZE}/shows/112/episodes").mock(return_value=httpx.Response(200, json=[]))
        got, note = await Tv(settings)._episode_list(show, 1, season1(), tmdb)
        assert len(got) == 12 and note == "TVmaze has no season 1; using TMDB's episodes"
        respx.get(f"{MAZE}/lookup/shows").mock(return_value=httpx.Response(404))
        _, note = await Tv(settings)._episode_list(show, 1, season1(), tmdb)
        assert note == "TVmaze doesn't have this show; using TMDB's episodes"
        respx.get(f"{MAZE}/lookup/shows").mock(return_value=httpx.Response(500))
        _, note = await Tv(settings)._episode_list(show, 1, season1(), tmdb)
        assert note == "TVmaze: TVmaze returned HTTP 500; using TMDB's episodes"
    finally:
        await tmdb.aclose()


# --- OMDb ---------------------------------------------------------------------------------


OMDB_HITS = {"Search": [{"Title": "Practical Magic", "Year": "1998", "imdbID": "tt0120791", "Type": "movie"},
                        {"Title": "Bad", "imdbID": "nope"}], "Response": "True"}
OMDB_PM = {"Title": "Practical Magic", "Year": "1998", "Runtime": "104 min", "Genre": "Comedy, Fantasy",
           "Plot": "Two witch sisters.", "Poster": "https://img/pm.jpg", "imdbVotes": "120,345",
           "imdbID": "tt0120791", "Response": "True"}


def omdb_route(router, by_query=OMDB_HITS, details=OMDB_PM):
    def answer(request):
        q = request.url.params
        if "i" in q:
            return httpx.Response(200, json=details if q["i"] == details["imdbID"] else {"Response": "False"})
        if q.get("y"):
            return httpx.Response(200, json={"Response": "False", "Error": "Movie not found!"})
        return httpx.Response(200, json=by_query)
    return router.get(OMDB).mock(side_effect=answer)


@respx.mock
async def test_omdb_client():
    omdb_route(respx)
    o = Omdb("k")
    try:
        got = await o.candidates(["Practical Magic"], 1999)  # nothing for 1999, so searched again without
        assert [(c.title, c.year, c.runtime, c.imdb_id, c.vote_count, c.source) for c in got] == [
            ("Practical Magic", 1998, 104, "tt0120791", 120345, "omdb")]
        respx.get(OMDB).mock(return_value=httpx.Response(401, json={"Response": "False", "Error": "Invalid API key!"}))
        with pytest.raises(OmdbError, match="refused the API key"):
            await o.search("x")
        respx.get(OMDB).mock(return_value=httpx.Response(401, json={"Error": "Request limit reached!"}))
        with pytest.raises(OmdbError, match="Request limit"):
            await o.search("x")
        respx.get(OMDB).mock(return_value=httpx.Response(200, json=[1]))
        with pytest.raises(OmdbError, match="unexpected"):
            await o.search("x")
        respx.get(OMDB).mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(OmdbError, match="could not reach"):
            await o.search("x")
    finally:
        await o.aclose()
    doc = omdb_candidate({"imdbID": "tt1", "Title": "D", "Genre": "Documentary", "Runtime": "N/A", "Poster": "N/A", "Plot": "N/A"})
    assert doc.genre_ids == [99] and doc.runtime is None and doc.poster_url is None and doc.overview is None


@respx.mock
async def test_films_merges_tmdb_and_omdb(keyed):
    keyed.omdb_api_key = SecretStr("o")
    keyed.metadata_providers = {"movie": [{"name": "tmdb"}, {"name": "omdb", "enabled": True}]}
    respx.get(f"{TMDB}/search/movie").mock(return_value=httpx.Response(200, json={"results": [
        {"id": 6435, "title": "Practical Magic", "release_date": "1998-10-16"}]}))
    respx.get(f"{TMDB}/movie/6435").mock(return_value=httpx.Response(200, json={
        "id": 6435, "runtime": 104, "imdb_id": "tt0120791"}))
    hits = {"Search": [*OMDB_HITS["Search"], {"Title": "Practical Magic 2", "Year": "2026", "imdbID": "tt999"},
                       {"Title": "Only on IMDb", "Year": "2001", "imdbID": "tt555"}]}
    omdb_route(respx, hits)
    respx.get(f"{TMDB}/find/tt999").mock(return_value=httpx.Response(200, json={"movie_results": [{"id": 77}]}))
    respx.get(f"{TMDB}/find/tt555").mock(return_value=httpx.Response(200, json={"movie_results": []}))
    respx.get(f"{TMDB}/movie/77").mock(return_value=httpx.Response(200, json={
        "id": 77, "title": "Practical Magic 2", "release_date": "2026-09-18", "imdb_id": "tt999"}))
    got, notes = await films.search(keyed, ["Practical Magic"], None)
    by = {c.title: c for c in got}
    assert by["Practical Magic"].providers == ["tmdb", "omdb"] and by["Practical Magic"].tmdb_id == 6435
    assert by["Practical Magic 2"].providers == ["omdb", "tmdb"] and by["Practical Magic 2"].source == "tmdb"
    assert by["Only on IMDb"].tmdb_id is None and by["Only on IMDb"].providers == ["omdb"]
    assert notes == [("info", "TMDB: 1 candidates for “Practical Magic”"),
                     ("info", "OMDb: 3 candidates for “Practical Magic”")]
    films.cross_check(got)
    assert by["Practical Magic"].evidence[-1].text == "Found by TMDB and OMDb"
    respx.get(OMDB).mock(return_value=httpx.Response(401, json={"Error": "Invalid API key!"}))
    got, notes = await films.search(keyed, ["Practical Magic"], None)
    assert len(got) == 1 and notes[-1] == ("warning", "OMDb refused the API key")


@respx.mock
async def test_settings_tests_for_new_providers(settings):
    omdb_route(respx)
    assert not (await checks.omdb(settings, SettingsUpdate())).ok
    r = await checks.omdb(settings, SettingsUpdate(omdb_api_key="typed"))
    assert r.ok and "1 titles" in r.details[0]
    respx.get(OMDB).mock(return_value=httpx.Response(401, json={"Error": "Invalid API key!"}))
    assert not (await checks.omdb(settings, SettingsUpdate(omdb_api_key="bad"))).ok
    respx.get(f"{MAZE}/search/shows").mock(return_value=httpx.Response(200, json=[{"show": MAZE_BWE}]))
    assert (await checks.tvmaze(settings, SettingsUpdate())).ok
    respx.get(f"{MAZE}/search/shows").mock(side_effect=httpx.ConnectError("down"))
    assert (await checks.tvmaze(settings, SettingsUpdate())).message.startswith("Could not reach TVmaze")
    respx.get(f"{SITE}/api/search").mock(return_value=httpx.Response(200, json=[{"title": "x"}]))
    assert (await checks.thediscdb(settings, SettingsUpdate())).ok
    respx.get(f"{SITE}/api/search").mock(return_value=httpx.Response(502))
    assert not (await checks.thediscdb(settings, SettingsUpdate())).ok


# --- in the pipeline ------------------------------------------------------------------------


def robot_match(main: str = "00801.mpls") -> DiscMatch:
    return DiscMatch(media=MediaKind.movie, title="The Wild Robot", year=2024, tmdb_id=1184918,
                     release="4K Ultra HD", titles=[DiscMatchTitle(index=0, source_file=main, kind="main"),
                                                    DiscMatchTitle(index=1, source_file="01238.mpls", kind="extra")])


async def test_disc_database_identifies_a_film_without_any_search(harness, settings, discdb):
    discdb.match = robot_match()
    async with harness(ROBOT) as h:
        job = await h.wait_job(lambda j: j.state == JobState.done)
        events = [e.message for e in h.db.events(job.id)]
    assert len(discdb.asked[0]["disc_id"]) == 40
    assert job.identity.title == "The Wild Robot" and job.identity_source == "auto"
    assert job.identity.providers == ["thediscdb"] and job.identity.score == films.DISC_MATCH_SCORE
    assert job.disc_match.titles[0].scan_index == 0
    assert "TheDiscDB knows this disc as The Wild Robot (2024), 4K Ultra HD" in events


async def test_disc_database_picks_the_main_title(harness, keyed, discdb):
    discdb.match = robot_match("00149.mpls")
    f = json.loads((Path(__file__).parent / "fixtures" / "tmdb" / "wild-robot.json").read_text())
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{TMDB}/search/movie").mock(return_value=httpx.Response(200, json={"results": f["results"]}))
        for tid, d in f["details"].items():
            router.get(f"{TMDB}/movie/{tid}").mock(return_value=httpx.Response(200, json={"id": int(tid), **d}))
        async with harness(ROBOT) as h:
            job = await h.wait_job(lambda j: j.state in (JobState.done, JobState.review))
    assert job.main_title.index == 3
    assert job.candidates[0].tmdb_id == 1184918 and job.candidates[0].providers == ["thediscdb", "tmdb"]
    assert job.candidates[0].evidence[0].text.startswith("TheDiscDB knows this disc")


async def test_unknown_disc_and_titles_that_dont_fit(harness, settings, discdb):
    discdb.match = DiscMatch(media=MediaKind.movie, title="Other", titles=[DiscMatchTitle(index=0, source_file="9.mpls")])
    async with harness(ROBOT) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review)
        events = [e.message for e in h.db.events(job.id)]
    assert job.disc_match is None
    assert "TheDiscDB's titles don't match this scan, so its record isn't used" in events


async def test_disc_database_maps_tv_episodes(harness, keyed, settings, discdb):
    sd = load("sarah-duck")
    discdb.match = DiscMatch(media=MediaKind.tv, title="Sarah & Duck", tmdb_id=sd["show"]["id"], titles=[
        DiscMatchTitle(index=0, duration_s=4210),
        *(DiscMatchTitle(index=i, duration_s=421, kind="episode", season=3, episode=n, name=f"Ep {n}")
          for i, n in enumerate(SD_EPISODES, 1))])
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "sarah-duck", search_override=[])
        router.get(f"{MAZE}/search/shows").mock(return_value=httpx.Response(200, json=[]))
        async with harness(SD) as h:
            job = await h.wait_job(lambda j: j.state == JobState.done, timeout=60)
    assert job.media == MediaKind.tv and job.tv.disc_db and not job.tv.compilation
    assert job.tv.show.providers == ["thediscdb", "tmdb"] and job.tv.auto_confirmed
    eps = {m.title_index: m.episodes for m in job.tv.mapping if m.role == TitleRole.episode}
    assert eps[1] == [EpisodeRef(season=3, episode=34)] and len(eps) == 10
    assert "TheDiscDB knows this disc" in job.tv.auto_evidence


def test_disc_match_mapping_only_for_the_same_show(settings):
    tv = Tv(settings)
    job = type("J", (), {})()
    job.titles = [TitleInfo(index=i, duration_s=3000, size_bytes=1, chapters=1) for i in range(3)]
    job.disc_match = DiscMatch(media=MediaKind.tv, title="BWE", tmdb_id=1621, titles=[
        DiscMatchTitle(index=0, kind="episode", season=1, episode=1, scan_index=0),
        DiscMatchTitle(index=1, kind="extra", scan_index=1), DiscMatchTitle(index=2, scan_index=2)])
    from ripshelf.models import TitleMap, TvPlan
    plan = TvPlan(season=1, mapping=[TitleMap(title_index=i, duration_s=3000, role=TitleRole.episode) for i in range(3)])
    assert tv._disc_match_mapping(job, plan, Show(tmdb_id=1, name="other")) is None
    note = tv._disc_match_mapping(job, plan, Show(tmdb_id=1621, name="BWE"))
    assert note == "TheDiscDB knows this disc. It's season 1, episode 1" and plan.disc_db
    assert [m.role for m in plan.mapping] == [TitleRole.episode, TitleRole.extra, TitleRole.skip]
    plan.season = 2
    assert tv._disc_match_mapping(job, plan, Show(tmdb_id=1621, name="BWE")) is None
    settings.metadata_providers = {"tv": [{"name": "thediscdb", "enabled": False}]}
    plan.season = 1
    assert tv._disc_match_mapping(job, plan, Show(tmdb_id=1621, name="BWE")) is None
