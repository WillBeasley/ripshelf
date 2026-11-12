"""Choosing which audio and subtitle tracks MakeMKV keeps (its default selection rule)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ripshelf.config import Settings
from ripshelf.makemkv import runner, selection

from .conftest import until
from .test_pipeline import run_manager
from .test_runner import script


def tracks(**kw) -> Settings:
    return Settings(_env_file=None, track_selection=True, **kw)


def test_off_leaves_makemkvs_own_rule():
    s = Settings(_env_file=None, track_rule="-sel:all")
    assert selection.rule(s) is None
    assert selection.conf(s) == {selection.RULE_KEY: None}


def test_defaults_keep_english_audio_and_subtitles():
    assert selection.rule(tracks()) == (
        "+sel:all,-sel:(audio|subtitle),-sel:mvcvideo,"
        "+sel:audio&(eng|nolang|single),-sel:audio&(havemulti|havecore),+sel:subtitle&(eng)")
    assert selection.conf(tracks())["app_ExpertMode"] == "1"


def test_choices_shape_the_rule():
    r = selection.rule(tracks(track_languages=["fre", "eng"], track_audio="all", track_commentary=False,
                              track_subtitles="forced"))
    assert r == ("+sel:all,-sel:(audio|subtitle),-sel:mvcvideo,+sel:audio,-sel:audio&special,"
                 "+sel:subtitle&forced&(fre|fra|eng)")
    r = selection.rule(tracks(track_subtitles="none"))
    assert "subtitle&" not in r and r.startswith("+sel:all,-sel:(audio|subtitle)")
    assert selection.rule(tracks(track_subtitles="all")).endswith(",+sel:subtitle")
    # No languages: audio falls back to untagged or the only track, and no subtitles by language.
    r = selection.rule(tracks(track_languages=[]))
    assert "+sel:audio&(nolang|single)" in r and "subtitle&" not in r


def test_own_rule_wins():
    s = tracks(track_rule="  -sel:all,+sel:(favlang|nolang)  ")
    assert selection.rule(s) == "-sel:all,+sel:(favlang|nolang)"


def test_languages_are_checked_and_tidied():
    assert tracks(track_languages=[" ENG", "jpn", "eng", ""]).track_languages == ["eng", "jpn"]
    with pytest.raises(ValidationError, match="three-letter"):
        tracks(track_languages=["english"])
    with pytest.raises(ValidationError):
        tracks(track_rule='bad"quote')


def test_set_conf_keeps_the_other_lines(tmp_path):
    conf = tmp_path / ".MakeMKV"
    conf.mkdir()
    (conf / "settings.conf").write_text('app_Key = "T-1"\napp_DefaultSelectionString = "old"\n')
    runner.set_conf(conf, {"app_DefaultSelectionString": "-sel:all", "app_ExpertMode": "1"})
    assert (conf / "settings.conf").read_text() == (
        'app_DefaultSelectionString = "-sel:all"\napp_ExpertMode = "1"\napp_Key = "T-1"\n')
    runner.set_conf(conf, {"app_DefaultSelectionString": None})
    assert (conf / "settings.conf").read_text() == 'app_ExpertMode = "1"\napp_Key = "T-1"\n'


async def test_rule_is_in_place_before_makemkvcon_starts(tmp_path):
    cmd = script(tmp_path, """
        import os, pathlib
        print(pathlib.Path(os.environ["HOME"], ".MakeMKV", "settings.conf").read_text())
    """)
    lines: list[str] = []
    res = await runner.MakeMKV(cmd).run([], tmp_path / "home", tmp_path / "log", on_line=lines.append,
                                         conf={"app_DefaultSelectionString": "-sel:all"})
    assert res.returncode == 0
    assert 'app_DefaultSelectionString = "-sel:all"\n' in lines


async def test_rip_writes_the_rule_into_the_drives_home(settings: Settings):
    settings.track_selection, settings.track_subtitles = True, "none"
    m, db, _ = await run_manager(settings, {"/dev/fake-sr1"})
    try:
        await until(lambda: any(j.rip.state == "done" for j in db.list_jobs()))
        conf = (settings.makemkv_dir / "fake-ihas122" / ".MakeMKV" / "settings.conf").read_text()
        assert f'app_DefaultSelectionString = "{selection.rule(settings)}"' in conf
    finally:
        await m.stop()
        db.close()
