"""Settings.

Two layers:
  * Environment / env file (RIPSHELF_* variables, file named by RIPSHELF_ENV_FILE,
    default ``ripshelf.env`` in the install folder). Paths and secrets normally live here.
  * ``<data_dir>/settings.json``, written by the Settings screen. Values there
    override the environment.

Secrets are ``SecretStr`` so they never appear in reprs or logs, and the API only
ever returns whether a secret is set.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, get_args

from pydantic import AfterValidator, Field, SecretStr, ValidationError
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)


def install_dir() -> Path:
    """Where ripshelf is. That's RIPSHELF_HOME, else the source checkout it runs from, else the
    current folder. Folders that aren't set go next to it (a container sets them all)."""
    if home := os.environ.get("RIPSHELF_HOME"):
        return Path(home)
    root = Path(__file__).resolve().parents[2]
    return root if (root / "pyproject.toml").is_file() else Path.cwd()


def _default_base() -> tuple[Path, str]:
    """The folder the defaults live in, and the library's name in it. A checkout
    that already has state in var/ keeps using it."""
    root = install_dir()
    if (root / "var" / "data").is_dir() and not (root / "data").exists():
        return root / "var", "library-test"
    return root, "library"


DEFAULT_BASE, _LIBRARY_NAME = _default_base()
ENV_FILE = os.environ.get("RIPSHELF_ENV_FILE", str(DEFAULT_BASE / "ripshelf.env"))
DEFAULT_LIBRARY = DEFAULT_BASE / _LIBRARY_NAME

EULA_PENDING = "MakeMKV's licence (EULA) hasn't been accepted, so MakeMKV isn't run and discs wait"

# Folders the Settings screen may change. Unset movies/TV/backup folders are inside the library.
FOLDERS = ("library_dir", "movies_dir", "tv_dir", "disc_backups_dir", "scratch_dir")
REQUIRED_FOLDERS = {"library_dir", "scratch_dir"}

# The sections of the Settings screen, in order (web/src/lib/settings/sections.ts has their names).
# The model each vision provider uses when none is set. A hosted OpenAI-compatible
# service has none: its model names are its own.
VISION_MODELS = {"local": "qwen3-vl:4b-instruct", "anthropic": "claude-opus-5-5"}


def vision_model_for(provider: str, model: str | None) -> str | None:
    """The model to ask: the one set, or the provider's default. Another provider's
    default is never kept (an Ollama model name left when switching to Anthropic)."""
    model = (model or "").strip()
    if not model or (model in VISION_MODELS.values() and model != VISION_MODELS.get(provider)):
        return VISION_MODELS.get(provider)
    return model


SECTIONS = ("identify", "drives", "scanner", "library", "media-servers", "transcoding", "notifications",
            "workers", "system")


@dataclass(frozen=True)
class UI:
    """A setting the Settings screen may change, and how it shows there. Settings
    without one are deployment config (environment only).

    section is None for a setting the UI saves elsewhere (a drive's name, setup done).
    Field limits (ge, le, a pattern) go on the field itself, so the server keeps them.
    """
    section: str | None
    title: str = ""
    help: str = ""  # one line under the field
    advanced: bool = False  # behind "Advanced" in its section
    controller_only: bool = False  # used by this machine only, not by remote workers
    kind: str | None = None  # the widget, when not the type's own (e.g. "range")
    choices: tuple[tuple[Any, str], ...] = ()  # (value, label), in order
    placeholder: str = ""
    keywords: str = ""  # more words the settings search matches
    step: float | None = None
    update: bool = True  # in SettingsUpdate (False: saved by its own endpoint)
    # Limits for what the screen saves only, where the environment may go further (tests
    # poll faster). Limits that always hold go on the field (Field(ge=...)).
    ge: float | None = None
    le: float | None = None


def ui_of(field: FieldInfo) -> UI | None:
    return next((m for m in field.metadata if isinstance(m, UI)), None)


def is_secret(field: FieldInfo) -> bool:
    return SecretStr in get_args(field.annotation) or field.annotation is SecretStr


def _languages(codes: list[str]) -> list[str]:
    """ISO 639-2 codes, as MakeMKV names a track's language ("eng")."""
    out = [c.strip().lower() for c in codes if c.strip()]
    if bad := [c for c in out if not (len(c) == 3 and c.isascii() and c.isalpha())]:
        raise ValueError(f"not a three-letter language code: {', '.join(bad)}")
    return list(dict.fromkeys(out))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RIPSHELF_", env_file=ENV_FILE, extra="ignore", validate_assignment=True
    )

    # Deployment (env only), except the folders (FOLDERS), which Settings can change too
    data_dir: Path = DEFAULT_BASE / "data"
    scratch_dir: Annotated[Path, UI(
        "library", "Scratch", "Rips in progress; a UHD disc needs about 100 GB.", advanced=True,
        keywords="temp working space")] = DEFAULT_BASE / "scratch"
    library_dir: Annotated[Path, UI(
        "library", "Library", "Films go in its Movies folder and TV in TV, unless set below.",
        keywords="folder path share")] = DEFAULT_LIBRARY
    # Separate roots for films and TV (e.g. on different disks). When unset they are the
    # Movies/ and TV/ folders inside library_dir. When set, the folder must exist.
    movies_dir: Annotated[Path | None, UI(
        "library", "Films", "Leave empty for the Movies folder in the library.", keywords="movies folder")] = None
    tv_dir: Annotated[Path | None, UI(
        "library", "TV", "Leave empty for the TV folder in the library.", keywords="shows folder")] = None
    # Full disc backups (rip mode "backup"). When unset they go in <library_dir>/Disc backups.
    disc_backups_dir: Annotated[Path | None, UI(
        "library", "Full disc backups", "Leave empty for the Disc backups folder in the library.",
        advanced=True, keywords="bdmv folder")] = None
    host: str = "0.0.0.0"
    port: int = 8380
    # More names the UI is reached by, besides addresses, plain and local names and the
    # public URL's (origins.py): comma separated, ".example.com" for any name under it.
    allowed_hosts: str = ""
    makemkvcon: str = "makemkvcon"
    # MakeMKV's licence, where ripshelf installed MakeMKV itself (the image sets the file). Until
    # accept_makemkv_eula is set (RIPSHELF_ACCEPT_MAKEMKV_EULA=yes), MakeMKV is not run and discs wait.
    makemkv_eula_file: Path | None = None
    accept_makemkv_eula: bool = False
    sevenzip: str = "7z"
    blkid: str = "blkid"
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    # Splits a TV title holding several episodes at its chapters (MKVToolNix).
    mkvmerge: str = "mkvmerge"
    # Reads the text on scans of the box.
    tesseract: str = "tesseract"
    # Where the daily backups (database, settings and MakeMKV data) go (backup_keep says how many). In the container
    # this is /backups, a bind mount that must exist; unset (development) they go to
    # <data>/backups.
    backup_dir: Path | None = None
    # Remote workers. Each worker added in the UI (Settings → Workers) gets its
    # own token. worker_token is a shared token for any worker, accepted by the
    # controller when set. A worker (`ripshelf worker`) needs controller_url and its token.
    worker_token: SecretStr | None = None
    # Instead of a token typed into both, the controller writes a random one to
    # this file on first start and workers that can see the file read it.
    worker_token_file: Path | None = None
    # Where the per-drive MakeMKV homes are, if not <data>/makemkv. A per-drive worker
    # mounts the controller's makemkv folder here.
    makemkv_path: Path | None = None
    controller_url: str = "http://localhost:8380"
    # On a worker, extra CA certificates (PEM) for an https controller_url behind a
    # reverse proxy with a private CA.
    controller_ca_file: Path | None = None
    worker_id: str | None = None  # defaults to the host name
    worker_name: str | None = None
    # The controller's scratch folder is mounted at the same path on the worker:
    # rips are written there directly instead of being uploaded.
    worker_shared_scratch: bool = False
    # Simulated drives replaying recorded MakeMKV logs (UI development, tests).
    fake_hardware: bool = False
    static_dir: Path | None = None

    # Editable: the fields with a UI(...). Each is described once, here, and the API's
    # SettingsUpdate and SettingsView, and the Settings screen's labels and search, follow.
    # The first-start setup page was finished (or skipped). Installs from before it
    # count as set up (load_settings).
    setup_complete: Annotated[bool, UI(None)] = False

    # Identify
    # Which metadata providers each kind of lookup asks, in order, each switched on
    # or off, as {"movie": [{"name": "tmdb", "enabled": true}, ...], "tv": ..., "episodes": ...}.
    # Empty means the defaults (providers.lists fills in anything missing).
    metadata_providers: Annotated[dict[str, list[dict[str, Any]]], UI(
        "identify", "Metadata providers", "Where discs, films and shows are looked up, in order.",
        keywords="tmdb tvdb omdb tvmaze thediscdb order")] = {}
    tmdb_api_key: Annotated[SecretStr | None, UI("identify", "TMDB API key (v3)", keywords="themoviedb")] = None
    tmdb_language: Annotated[str, Field(max_length=20), UI(
        "identify", "TMDB language", "Names and overviews in this language, such as en-GB.",
        advanced=True, placeholder="en-GB")] = "en-GB"
    # TheTVDB, a second source for shows and episodes (off until a key is set). The pin
    # is only for user-supported keys. Episode lists come from TheTVDB in aired or DVD
    # order when it has the show, else from TMDB. DVD order follows the discs, but Emby
    # numbers episodes in aired order unless its library is set to DVD order.
    tvdb_api_key: Annotated[SecretStr | None, UI("identify", "TheTVDB API key (v4)", keywords="tvdb")] = None
    tvdb_pin: Annotated[SecretStr | None, UI(
        "identify", "TheTVDB subscriber PIN", "Only for user-supported keys.", advanced=True)] = None
    # Legacy setting for which source episode lists came from, replaced by the provider
    # lists and tvdb_episode_order. Only read (LEGACY) from settings saved without them.
    tv_episode_order: Literal["tmdb", "tvdb_aired", "tvdb_dvd"] = "tvdb_aired"
    tvdb_episode_order: Annotated[Literal["aired", "dvd"], UI(
        "identify", "Episode order", "DVD order follows the discs; Emby needs its library set to DVD order too.",
        choices=(("aired", "Aired order"), ("dvd", "DVD order (falls back to aired)")),
        keywords="tvdb season numbering")] = "aired"
    # OMDb (IMDb data), a second film search and cross-check, off until a key is set.
    omdb_api_key: Annotated[SecretStr | None, UI("identify", "OMDb API key", keywords="imdb")] = None
    auto_confirm_threshold: Annotated[float, Field(ge=0.5, le=1), UI(
        "identify", "Auto-confirm films at score", "Lower confirms more discs without asking.",
        kind="range", step=0.01, keywords="threshold review confidence")] = 0.90
    # Confirm a TV disc without review when everything agrees (label places it,
    # show score >= threshold, runtimes match, order verified or collection continues).
    tv_auto_confirm: Annotated[bool, UI(
        "identify", "Confirm TV discs without review when everything agrees")] = True
    tv_auto_threshold: Annotated[float, Field(ge=0.5, le=1), UI(
        "identify", "TV show match needed", kind="range", step=0.01, keywords="threshold confidence")] = 0.9
    # Check episode order against the play-all after ripping (needs ffmpeg).
    verify_order: Annotated[bool, UI(
        "identify", "Check the episode order after ripping a TV disc",
        "Compares each episode with the play-all title, when there is one. Needs ffmpeg.")] = True
    # Labels that are a catalogue number ("EO51710") are looked up on the web:
    # a self-hosted SearXNG.
    searxng_url: Annotated[str | None, Field(max_length=300), UI(
        "identify", "SearXNG address", "A self-hosted SearXNG, for web searches from Review.",
        placeholder="http://searxng:8080", keywords="web search")] = None
    catalogue_search: Annotated[Literal["off", "searxng"], UI(
        "identify", "Look catalogue numbers up with", "For DVDs labelled with the publisher's number.",
        choices=(("off", "Off"), ("searxng", "SearXNG (self-hosted)")))] = "off"
    # A language model reading cryptic labels ("BTTF2"): off, only from the button in
    # Review, or also on its own when nothing the label's own reading finds fits. It
    # runs on the vision model's connection, with label_ai_model if set.
    label_ai: Annotated[Literal["off", "manual", "auto"], UI(
        "identify", "Read cryptic labels with an AI model",
        "BTTF2 is Back to the Future Part II. Only the label and running times are sent.",
        choices=(("off", "Never"), ("manual", "Only when I press the button in Review"),
                 ("auto", "When nothing found fits the disc, and with the button")),
        keywords="ai llm ollama claude decode abbreviation")] = "off"
    label_ai_model: Annotated[str | None, Field(max_length=200), UI(
        "identify", "Model for labels", "Blank uses the vision model. Any chat model will do.",
        placeholder="the vision model", keywords="ai llm ollama")] = None
    # Sending confirmed discs back to TheDiscDB. Off, a button per job ("ask"), or on
    # its own once the user confirms the identity ("auto"). The key is sent only if set.
    thediscdb_contribute: Annotated[Literal["off", "ask", "auto"], UI(
        "identify", "Contribute confirmed discs to TheDiscDB",
        choices=(("off", "Off"), ("ask", "Ask, with a Send button on each job"),
                 ("auto", "Automatically, once I confirm a disc")), keywords="share upload")] = "off"
    thediscdb_contribute_url: Annotated[str, Field(max_length=300), UI(
        "identify", "TheDiscDB address", advanced=True, placeholder="https://thediscdb.com")] = "https://thediscdb.com"
    thediscdb_api_key: Annotated[SecretStr | None, UI(
        "identify", "TheDiscDB API key", "Optional; only if TheDiscDB gave you one.", advanced=True)] = None

    # Drives and ripping
    makemkv_key: Annotated[SecretStr | None, UI(
        "drives", "MakeMKV registration key", keywords="licence beta")] = None
    # "titles" rips the main feature (or every title, for TV) to MKV. "backup" makes a
    # decrypted copy of the whole Blu-ray or UHD disc (BDMV folder), as MakeMKV's
    # backup does; DVDs are ripped as titles either way. drive_modes overrides it per drive.
    rip_mode: Annotated[Literal["titles", "backup"], UI(
        "drives", "What to rip", "DVDs are ripped as titles either way. Each drive can have its own.",
        choices=(("titles", "Titles, the film (or every episode) as MKV"),
                 ("backup", "Full disc backup, the whole Blu-ray or UHD, decrypted")),
        keywords="bdmv backup mode")] = "titles"
    drive_modes: Annotated[dict[str, Literal["titles", "backup"]], UI("drives", "Rip mode per drive")] = {}
    disabled_drives: Annotated[list[str], UI(
        "drives", "Enabled drives", "Untick a drive for ripshelf to leave it alone.", keywords="disable")] = []
    # drive id -> a name given in the UI ("Top bay"), shown instead of the model.
    drive_names: Annotated[dict[str, str], UI(None, update=False)] = {}
    auto_eject: Annotated[bool, UI("drives", "Eject when the rip finishes")] = True
    # Which audio and subtitle tracks MakeMKV keeps, as its default selection rule
    # (makemkv.selection). Off leaves MakeMKV's own rule. A full backup keeps everything.
    track_selection: Annotated[bool, UI(
        "drives", "Choose which audio and subtitle tracks are kept",
        "Off keeps MakeMKV's own choice. Full disc backups keep every track.",
        keywords="audio subtitles language commentary streams")] = False
    track_languages: Annotated[list[str], AfterValidator(_languages), UI(
        "drives", "Languages", "Three-letter codes, comma separated, such as eng, fre.",
        placeholder="eng", keywords="audio subtitles")] = ["eng"]
    track_audio: Annotated[Literal["languages", "all"], UI(
        "drives", "Audio", "A disc's only audio track is always kept.",
        choices=(("languages", "In these languages"), ("all", "Every audio track")))] = "languages"
    track_commentary: Annotated[bool, UI("drives", "Keep commentary tracks")] = True
    track_subtitles: Annotated[Literal["none", "forced", "languages", "all"], UI(
        "drives", "Subtitles", choices=(("none", "None"), ("forced", "Forced only, in these languages"),
                                        ("languages", "In these languages"), ("all", "Every subtitle track")))
    ] = "languages"
    # MakeMKV's own rule syntax, used instead of the choices above when set.
    track_rule: Annotated[str | None, Field(max_length=500, pattern=r'^[^"\n]*$'), UI(
        "drives", "MakeMKV selection rule", "Used instead of the choices above. MakeMKV's own syntax.",
        advanced=True, placeholder="-sel:all,+sel:(favlang|nolang|single),-sel:mvcvideo",
        keywords="app_DefaultSelectionString")] = None
    # A scan is stopped once MakeMKV has written nothing for scan_timeout_s, and in any
    # case after scan_max_s. A damaged disc keeps MakeMKV busy (read retries, slow
    # AACS) for far longer than a healthy one, but it keeps reporting while it does.
    # After a complex multiplex message it may stay quiet longer (runner.COMPLEX_IDLE_S).
    scan_timeout_s: Annotated[int, Field(gt=0), UI(
        "drives", "Scan stall timeout (s)", "Stop a scan once MakeMKV has said nothing for this long.",
        advanced=True, ge=30, le=3600)] = 300
    scan_max_s: Annotated[int, Field(gt=0), UI(
        "drives", "Scan time limit (s)", "A damaged disc being read slowly keeps going up to this.",
        advanced=True, ge=300, le=43200)] = 7200
    poll_interval_s: Annotated[float, Field(gt=0), UI(
        "drives", "Poll interval (s)", "How often drives are checked for a disc.", advanced=True,
        step=0.5, ge=1, le=60)] = 3.0
    # Drives that can read UHD discs (LibreDrive). Matched against "VENDOR MODEL".
    # A remote worker uses its own (RIPSHELF_UHD_MODELS).
    uhd_models: Annotated[list[str], UI(
        "drives", "UHD-capable drive models", 'One per line, "VENDOR MODEL". Workers set RIPSHELF_UHD_MODELS.',
        advanced=True, controller_only=True, keywords="4k libredrive")] = ["PIONEER BD-RW  BDR-S11JX"]

    # Scanner and covers
    # A network scanner (eSCL, also called AirScan) for scanning the box, as
    # http://printer/eSCL. Off until set up in Settings.
    scanner_enabled: Annotated[bool, UI(
        "scanner", "Scan from a network scanner", keywords="escl airscan printer cover box")] = False
    scanner_url: Annotated[str | None, Field(max_length=300), UI(
        "scanner", "Scanner address", "The printer's address is enough; /eSCL is added. Use http://.",
        placeholder="http://192.168.1.20/eSCL")] = None
    scanner_dpi: Annotated[Literal[100, 150, 200, 300, 400, 600], UI(
        "scanner", "Resolution", choices=tuple((d, f"{d} dpi" + (" (recommended)" if d == 300 else ""))
                                               for d in (100, 150, 200, 300, 400, 600)), keywords="dpi")] = 300
    scanner_source: Annotated[Literal["platen", "feeder"], UI(
        "scanner", "Scan from", "Insert pages come from the feeder when there is one.",
        choices=(("platen", "The glass"), ("feeder", "The feeder")))] = "platen"
    # Cut away the empty glass around what was scanned (the original is kept).
    scanner_trim: Annotated[bool, UI("scanner", "Cut away the empty glass around a scan", keywords="crop")] = True
    # Read the text on scans (Tesseract) and make a listing of what's on the disc.
    scan_read_text: Annotated[bool, UI(
        "scanner", "Read the text on scans and list what's on the disc", keywords="ocr tesseract")] = True
    # Tesseract's languages, joined with +, such as "eng+fra" (each needs its traineddata).
    ocr_languages: Annotated[str, Field(pattern=r"^[a-z_]{3,10}(\+[a-z_]{3,10})*$"), UI(
        "scanner", "Text languages", "Tesseract's codes joined with +, such as eng+fra.",
        advanced=True, placeholder="eng", keywords="ocr tesseract")] = "eng"
    # A vision model reading the scans beside Tesseract: never, only scans Tesseract
    # struggled with, every scan, or only when asked. "local" is an OpenAI-compatible
    # server on the LAN (Ollama), "openai" a hosted OpenAI-compatible service, and
    # "anthropic" Anthropic's API (no address needed). A model left blank is the
    # provider's default (VISION_MODELS).
    vision_mode: Annotated[Literal["off", "struggles", "every", "manual"], UI(
        "scanner", "Read scans with a vision model",
        choices=(("off", "Never"), ("struggles", "Only when Tesseract struggles (recommended)"),
                 ("every", "Every scan"), ("manual", "Only when I press the button on a scan")),
        keywords="ai llm ollama")] = "off"
    vision_provider: Annotated[Literal["local", "openai", "anthropic"], UI(
        "scanner", "Where the vision model runs",
        choices=(("local", "On my network (Ollama or another OpenAI-compatible server)"),
                 ("openai", "A cloud service (OpenAI-compatible)"), ("anthropic", "Anthropic's API")),
        keywords="ollama openai anthropic claude")] = "local"
    vision_url: Annotated[str | None, Field(max_length=300), UI(
        "scanner", "Vision model address", placeholder="http://ollama:11434")] = None
    vision_model: Annotated[str | None, Field(max_length=200), UI(
        "scanner", "Vision model", "Blank uses the default for where it runs.")] = None
    vision_api_key: Annotated[SecretStr | None, UI("scanner", "Vision model API key")] = None
    # Copy a job's scans into the film's or show's folder, in scans/.
    scans_to_library: Annotated[bool, UI(
        "scanner", "Copy scans to the library", "Into a scans folder beside the film or show, once it's filed.")] = True

    # Library and filing (the folders are with the deployment ones above)
    # Titles that look like extras start as "extra" (ripped); off, they start as "skip".
    tv_rip_extras: Annotated[bool, UI(
        "library", "Rip TV extras by default", "Off, titles that look like extras start as Skip in review.")] = True
    # TV titles kept as extras that the box doesn't name: in the show's extras folder,
    # in Season 00 as specials, or not copied to the library.
    tv_unmatched: Annotated[Literal["extras", "specials", "skip"], UI(
        "library", "TV extras go", "For extras the cover scan doesn't name.",
        choices=(("extras", "In the show's extras folder"), ("specials", "In Season 00, as specials"),
                 ("skip", "Nowhere (not copied to the library)")), keywords="season 0 specials")] = "extras"
    # A bonus episode of another show on a TV disc: an extra of this show, named after
    # the other show and the episode, or an episode in the other show's own folder.
    tv_bonus_episodes: Annotated[Literal["extra", "own_show"], UI(
        "library", "Bonus episodes of other shows",
        choices=(("extra", 'An extra of this show, named "Other show - Episode"'),
                 ("own_show", "An episode in the other show's own folder")))] = "extra"
    # Extras the box names (matched with confidence) are filed in Emby's extras folder
    # for their type, under their names: featurettes/, trailers/...
    extras_emby_folders: Annotated[bool, UI(
        "library", "File named extras into Emby's folders",
        "featurettes, trailers, deleted scenes… as the cover scan names them.", keywords="jellyfin plex")] = True
    # A film's other titles the box doesn't name: left out, or ripped and filed in
    # extras/ as "Title N".
    film_unnamed_extras: Annotated[Literal["skip", "extras"], UI(
        "library", "A film's other titles",
        choices=(("skip", "Leave them out"), ("extras", 'Rip them into extras/ as "Title N"')))] = "skip"

    # Media servers. The libraries films and TV go into (picked in Settings), and the
    # folder of each as the server sees it. Our movies/TV folder maps onto it for refreshes.
    emby_url: Annotated[str | None, UI("media-servers", "Emby URL", placeholder="http://emby.lan:8096")] = None
    emby_api_key: Annotated[SecretStr | None, UI("media-servers", "Emby API key")] = None
    emby_movies_library: Annotated[str | None, UI("media-servers", "Emby movies library")] = None
    emby_movies_path: Annotated[str | None, UI("media-servers")] = None
    emby_tv_library: Annotated[str | None, UI("media-servers", "Emby TV library")] = None
    emby_tv_path: Annotated[str | None, UI("media-servers")] = None
    # The same for Jellyfin and Plex (whose key is called a token).
    jellyfin_url: Annotated[str | None, UI(
        "media-servers", "Jellyfin URL", placeholder="http://jellyfin.lan:8096")] = None
    jellyfin_api_key: Annotated[SecretStr | None, UI("media-servers", "Jellyfin API key")] = None
    jellyfin_movies_library: Annotated[str | None, UI("media-servers", "Jellyfin movies library")] = None
    jellyfin_movies_path: Annotated[str | None, UI("media-servers")] = None
    jellyfin_tv_library: Annotated[str | None, UI("media-servers", "Jellyfin TV library")] = None
    jellyfin_tv_path: Annotated[str | None, UI("media-servers")] = None
    plex_url: Annotated[str | None, UI("media-servers", "Plex URL", placeholder="http://plex.lan:32400")] = None
    plex_token: Annotated[SecretStr | None, UI("media-servers", "Plex token")] = None
    plex_movies_library: Annotated[str | None, UI("media-servers", "Plex movies library")] = None
    plex_movies_path: Annotated[str | None, UI("media-servers")] = None
    plex_tv_library: Annotated[str | None, UI("media-servers", "Plex TV library")] = None
    plex_tv_path: Annotated[str | None, UI("media-servers")] = None

    # Transcoding (on by default). Sources whose video codec is listed are
    # re-encoded to H.265 before they go to the library.
    transcode_enabled: Annotated[bool, UI(
        "transcoding", "Transcode after ripping", keywords="h265 hevc encode")] = True
    # Per kind of disc, whether its rips are transcoded at all and whether their black
    # bars are cropped, as {"bluray": {"transcode": true, "crop": false}}. Missing is
    # on, except transcoding UHD discs, which follows transcode_hdr (see transcode_source).
    transcode_sources: Annotated[dict[Literal["dvd", "bluray", "uhd"], dict[Literal["transcode", "crop"], bool]], UI(
        "transcoding", "Which discs", keywords="dvd blu-ray uhd crop")] = {}
    # auto tries Quick Sync, VAAPI, then NVENC (transcode.AUTO_ORDER) and falls back to x265.
    transcode_encoder: Annotated[Literal["auto", "qsv", "vaapi", "nvenc", "x265"], UI(
        "transcoding", "Encoder", controller_only=True,
        choices=(("auto", "Auto (Quick Sync, VAAPI, NVENC, else CPU)"), ("qsv", "GPU (Intel Quick Sync)"),
                 ("vaapi", "GPU (VAAPI)"), ("nvenc", "GPU (NVIDIA NVENC)"), ("x265", "CPU (x265)")),
        keywords="gpu cpu hardware")] = "auto"
    # Constant QP on a GPU or x265 CRF. Lower is better and bigger.
    transcode_quality: Annotated[int, Field(ge=10, le=40), UI(
        "transcoding", "Quality (QP/CRF)", "Lower is better and bigger.", keywords="crf qp")] = 21
    # HDR and Dolby Vision sources are left alone unless this is on.
    transcode_hdr: Annotated[bool, UI(
        "transcoding", "Also touch HDR and Dolby Vision sources", keywords="dv 4k")] = False
    # Keep the untouched rip next to the transcoded file (" - original").
    transcode_keep_original: Annotated[bool, UI(
        "transcoding", "Keep the original rip too", '"… - original.mkv" next to the transcode.')] = False
    transcode_codecs: Annotated[list[str], UI(
        "transcoding", "Video codecs to re-encode", "ffprobe names, comma separated.", advanced=True,
        placeholder="vc1", keywords="vc1 mpeg2 h264")] = ["vc1"]
    # Crop black bars never, only when transcoding anyway, or always (which
    # re-encodes any letterboxed source).
    transcode_crop: Annotated[Literal["off", "when_transcoding", "always"], UI(
        "transcoding", "Crop black bars", advanced=True,
        choices=(("off", "Never"), ("when_transcoding", "When transcoding anyway"),
                 ("always", "Always (re-encodes any letterboxed film)")), keywords="letterbox")] = "when_transcoding"
    # How many jobs transcode at once, across every drive and worker (workers
    # never transcode; the controller does). Each job does its files in turn.
    transcode_parallel: Annotated[int, Field(ge=1, le=8), UI(
        "transcoding", "Transcodes at once", "One is best for x265; a GPU can often take two or three.",
        advanced=True, controller_only=True, keywords="parallel concurrent")] = 1
    # The render node Quick Sync and VAAPI use, when there is more than one GPU.
    vaapi_device: Annotated[str, Field(max_length=200), UI(
        "transcoding", "GPU for Quick Sync and VAAPI", advanced=True, controller_only=True,
        placeholder="/dev/dri/renderD128", keywords="render node dri")] = "/dev/dri/renderD128"

    # Notifications. Pushover is off until both keys are set.
    pushover_user_key: Annotated[SecretStr | None, UI("notifications", "Pushover user key")] = None
    pushover_app_token: Annotated[SecretStr | None, UI("notifications", "Pushover application API token")] = None
    pushover_device: Annotated[str | None, UI(
        "notifications", "Pushover device", "Optional; else all your devices.")] = None
    # Which events notify, with their Pushover priority (-2..1). None or missing is off.
    notify_events: Annotated[dict[str, int | None], UI(
        "notifications", "Events", "Which events notify, and how loudly.", keywords="priority alert")] = {
        "disc": None, "review": 1, "exists": 1, "ripped": 0, "transcoded": None, "copied": None, "done": 0,
        "failed": 1, "setup": 0}
    # More notifications of one kind within this many seconds of the first are
    # sent as one summary at the end of the window (0 sends every one at once).
    notify_batch_s: Annotated[float, Field(ge=0, le=3600), UI(
        "notifications", "Batch window (s)", "More of one kind within this time come as one summary.",
        keywords="summary group")] = 120

    # System
    # Base URL of this UI as seen from a phone, for notification links.
    public_url: Annotated[str | None, Field(max_length=300), UI(
        "system", "This UI's address from a phone", "So every notification links to its job.",
        placeholder="http://192.168.1.10:8380", keywords="url link")] = None
    # Daily backups of the database, settings.json and the MakeMKV data (backups.py).
    # The newest backup_keep are kept (0 turns them off).
    backup_keep: Annotated[int, Field(ge=0, le=365), UI(
        "system", "Daily backups to keep", "The database, settings and MakeMKV data. 0 turns them off.",
        keywords="database settings makemkv")] = 7

    @property
    def settings_file(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ripshelf.db"

    @property
    def backups(self) -> Path:
        return self.backup_dir or self.data_dir / "backups"

    @property
    def movies(self) -> Path:
        return self.movies_dir or self.library_dir / "Movies"

    @property
    def tv(self) -> Path:
        return self.tv_dir or self.library_dir / "TV"

    @property
    def disc_backups(self) -> Path:
        return self.disc_backups_dir or self.library_dir / "Disc backups"

    def rip_mode_for(self, drive_id: str) -> str:
        return self.drive_modes.get(drive_id, self.rip_mode)

    @property
    def vision_model_name(self) -> str | None:
        """The vision model to ask: the one set, or the provider's default."""
        return vision_model_for(self.vision_provider, self.vision_model)

    def transcode_source(self, kind: str | None) -> dict[str, bool]:
        """Whether rips from this kind of disc are transcoded, and cropped. A disc
        of unknown kind follows the other transcode settings only."""
        rule = {"transcode": kind != "uhd" or self.transcode_hdr, "crop": True}
        return rule | self.transcode_sources.get(kind, {}) if kind else rule

    def drive_name(self, drive_id: str) -> str | None:
        return self.drive_names.get(drive_id) or None

    @property
    def backups_used(self) -> bool:
        return self.rip_mode == "backup" or "backup" in self.drive_modes.values()

    def output_roots(self) -> list[tuple[Path, str]]:
        """The folders that must already exist for finished rips. These are the separate movies/TV
        folders if set, else the library root (Movies/ and TV/ are made inside it).
        The disc backups folder too, once any drive makes backups."""
        roots = [self.output_root("movie"), self.output_root("tv")]
        if self.backups_used:
            roots.append(self.output_root("backup"))
        return list(dict.fromkeys(roots))

    def output_root(self, media: str) -> tuple[Path, str]:
        """The folder that must exist before a film ("movie"), TV disc or disc backup is copied, and its name."""
        if media == "backup":
            return ((self.disc_backups_dir, "disc backups folder") if self.disc_backups_dir
                    else (self.library_dir, "library folder"))
        if media == "tv":
            return (self.tv_dir, "TV folder") if self.tv_dir else (self.library_dir, "library folder")
        return (self.movies_dir, "movies folder") if self.movies_dir else (self.library_dir, "library folder")

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def makemkv_files_dir(self) -> Path:
        """Private data and firmware dumps kept for remote workers (makemkv.files)."""
        return self.data_dir / "makemkv-files"

    @property
    def makemkv_dir(self) -> Path:
        return self.makemkv_path or self.data_dir / "makemkv"

    def with_folders(self, values: dict[str, Any]) -> Settings:
        """A copy with these folders (as typed, where "" is unset, or unchanged for a required one)."""
        update = {}
        for k in FOLDERS:
            if values.get(k) is not None:
                v = str(values[k]).strip()
                update[k] = Path(v) if v else (getattr(self, k) if k in REQUIRED_FOLDERS else None)
        return self.model_copy(update=update)

    def apply(self, values: dict[str, Any]) -> None:
        for k, v in values.items():
            if k in EDITABLE:
                setattr(self, k, v)

    def save_overrides(self, values: dict[str, Any]) -> None:
        current = self._read_overrides()
        for k, v in values.items():
            if k in EDITABLE:
                current[k] = v
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.settings_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.settings_file)
        self.apply(values)

    def _read_overrides(self) -> dict[str, Any]:
        try:
            return json.loads(self.settings_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}

    def makemkv_eula_pending(self) -> bool:
        """MakeMKV came with ripshelf and its licence hasn't been accepted yet."""
        return self.makemkv_eula_file is not None and not self.accept_makemkv_eula

    def secret(self, name: str) -> str | None:
        v = getattr(self, name)
        return v.get_secret_value() if v else None


# Settings the Settings screen may change (described with a UI), and which are secrets.
EDITABLE = frozenset(n for n, f in Settings.model_fields.items() if ui_of(f))
SECRETS = frozenset(n for n in EDITABLE if is_secret(Settings.model_fields[n]))
# Saved by older versions, and still read from settings.json, but no longer changed.
LEGACY = frozenset({"tv_episode_order"})


# Settings ripshelf saves by itself, before anyone has seen the setup page: a virtual
# drive starts disabled. A settings.json with only these is a new install, not one set
# up before there was a setup page.
AUTO_WRITTEN = {"disabled_drives"}


def load_settings() -> Settings:
    s = Settings()
    overrides = s._read_overrides()
    for k, v in overrides.items():
        if k in EDITABLE or k in LEGACY:
            try:
                setattr(s, k, v)
            except ValidationError as e:
                # Saved by an older version with looser limits, say. The default is used.
                log.warning("settings.json: ignoring %s=%r (%s)", k, v, e.errors()[0]["msg"])
    if set(overrides) - AUTO_WRITTEN and "setup_complete" not in overrides:
        s.setup_complete = True  # set up before there was a setup page
    if "tvdb_episode_order" not in overrides and s.tv_episode_order == "tvdb_dvd":
        s.tvdb_episode_order = "dvd"
    # Not a library that was set, because if that is missing (share not mounted) jobs stop at
    # finalise. The default one, next to ripshelf, is made.
    for d in (s.data_dir, s.scratch_dir, s.jobs_dir, s.makemkv_dir):
        d.mkdir(parents=True, exist_ok=True)
    if s.library_dir == DEFAULT_LIBRARY:
        s.library_dir.mkdir(parents=True, exist_ok=True)
    return s


def load_worker_token(s: Settings, create: bool) -> str | None:
    """The worker token. That's RIPSHELF_WORKER_TOKEN, else the token file (made if ``create``)."""
    if s.secret("worker_token") or not s.worker_token_file:
        return s.secret("worker_token")
    f = s.worker_token_file
    if not f.exists() and create:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(secrets.token_hex(24) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(f)
    if f.exists() and (token := f.read_text(encoding="utf-8").strip()):
        s.worker_token = SecretStr(token)
    return s.secret("worker_token")
