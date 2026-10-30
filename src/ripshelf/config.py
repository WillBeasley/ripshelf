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
import os
import secrets
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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

# Fields the Settings screen may change. Everything else is deployment config.
EDITABLE = {
    *FOLDERS,
    "setup_complete",
    "tmdb_api_key",
    "tmdb_language",
    "tvdb_api_key",
    "tvdb_pin",
    "tv_episode_order",
    "tvdb_episode_order",
    "omdb_api_key",
    "thediscdb_contribute",
    "thediscdb_contribute_url",
    "thediscdb_api_key",
    "metadata_providers",
    "auto_confirm_threshold",
    "auto_eject",
    "scan_timeout_s",
    "scan_max_s",
    "poll_interval_s",
    "uhd_models",
    "public_url",
    "emby_url",
    "emby_api_key",
    "emby_movies_library",
    "emby_tv_library",
    "emby_movies_path",
    "emby_tv_path",
    "jellyfin_url",
    "jellyfin_api_key",
    "jellyfin_movies_library",
    "jellyfin_tv_library",
    "jellyfin_movies_path",
    "jellyfin_tv_path",
    "plex_url",
    "plex_token",
    "plex_movies_library",
    "plex_tv_library",
    "plex_movies_path",
    "plex_tv_path",
    "makemkv_key",
    "disabled_drives",
    "rip_mode",
    "drive_modes",
    "drive_names",
    "tv_unmatched",
    "catalogue_search",
    "searxng_url",
    "tv_auto_confirm",
    "tv_rip_extras",
    "tv_auto_threshold",
    "transcode_enabled",
    "transcode_codecs",
    "transcode_crop",
    "transcode_hdr",
    "transcode_sources",
    "transcode_encoder",
    "transcode_quality",
    "transcode_keep_original",
    "vaapi_device",
    "pushover_user_key",
    "pushover_app_token",
    "pushover_device",
    "notify_events",
    "notify_batch_s",
    "verify_order",
    "backup_keep",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RIPSHELF_", env_file=ENV_FILE, extra="ignore", validate_assignment=True
    )

    # Deployment (env only), except the folders (FOLDERS), which Settings can change too
    data_dir: Path = DEFAULT_BASE / "data"
    scratch_dir: Path = DEFAULT_BASE / "scratch"
    library_dir: Path = DEFAULT_LIBRARY
    # Separate roots for films and TV (e.g. on different disks). When unset they are the
    # Movies/ and TV/ folders inside library_dir. When set, the folder must exist.
    movies_dir: Path | None = None
    tv_dir: Path | None = None
    # Full disc backups (rip mode "backup"). When unset they go in <library_dir>/Disc backups.
    disc_backups_dir: Path | None = None
    host: str = "0.0.0.0"
    port: int = 8380
    makemkvcon: str = "makemkvcon"
    # MakeMKV's licence, where ripshelf installed MakeMKV itself (the image sets the file). Until
    # accept_makemkv_eula is set (RIPSHELF_ACCEPT_MAKEMKV_EULA=yes), MakeMKV is not run and discs wait.
    makemkv_eula_file: Path | None = None
    accept_makemkv_eula: bool = False
    sevenzip: str = "7z"
    blkid: str = "blkid"
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    # Labels that are a catalogue number ("EO51710") are looked up on the web:
    # a self-hosted SearXNG.
    catalogue_search: Literal["off", "searxng"] = "off"
    searxng_url: str | None = None
    # Daily database backups. The newest backup_keep are kept (0 turns them off), and Settings can
    # change how many.
    # In the container this is /backups, a bind mount that must exist; unset
    # (development) they go to <data>/backups.
    backup_dir: Path | None = None
    backup_keep: int = Field(7, ge=0, le=365)
    # Remote workers. Each worker added in the UI (Settings → Remote workers) gets its
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

    # Editable
    # The first-start setup page was finished (or skipped). Installs from before it
    # count as set up (load_settings).
    setup_complete: bool = False
    tmdb_api_key: SecretStr | None = None
    tmdb_language: str = "en-GB"
    # TheTVDB, a second source for shows and episodes (off until a key is set). The pin
    # is only for user-supported keys. Episode lists come from TheTVDB in aired or DVD
    # order when it has the show, else from TMDB. DVD order follows the discs, but Emby
    # numbers episodes in aired order unless its library is set to DVD order.
    tvdb_api_key: SecretStr | None = None
    tvdb_pin: SecretStr | None = None
    # Legacy setting for which source episode lists came from. Only read to set up the
    # provider lists (and tvdb_episode_order) from settings saved without them.
    tv_episode_order: Literal["tmdb", "tvdb_aired", "tvdb_dvd"] = "tvdb_aired"
    tvdb_episode_order: Literal["aired", "dvd"] = "aired"
    # OMDb (IMDb data), a second film search and cross-check, off until a key is set.
    omdb_api_key: SecretStr | None = None
    # Sending confirmed discs back to TheDiscDB. Off, a button per job ("ask"), or on
    # its own once the user confirms the identity ("auto"). The key is sent only if set.
    thediscdb_contribute: Literal["off", "ask", "auto"] = "off"
    thediscdb_contribute_url: str = "https://thediscdb.com"
    thediscdb_api_key: SecretStr | None = None
    # Which metadata providers each kind of lookup asks, in order, each switched on
    # or off, as {"movie": [{"name": "tmdb", "enabled": true}, ...], "tv": ..., "episodes": ...}.
    # Empty means the defaults (providers.lists fills in anything missing).
    metadata_providers: dict[str, list[dict[str, Any]]] = {}
    auto_confirm_threshold: float = Field(0.90, ge=0, le=1)
    auto_eject: bool = True
    # A scan is stopped once MakeMKV has written nothing for scan_timeout_s, and in any
    # case after scan_max_s. A damaged disc keeps MakeMKV busy (read retries, slow
    # AACS) for far longer than a healthy one, but it keeps reporting while it does.
    scan_timeout_s: int = 300
    scan_max_s: int = 7200
    poll_interval_s: float = 3.0
    # Drives that can read UHD discs (LibreDrive). Matched against "VENDOR MODEL".
    uhd_models: list[str] = ["PIONEER BD-RW  BDR-S11JX"]
    disabled_drives: list[str] = []
    # "titles" rips the main feature (or every title, for TV) to MKV. "backup" makes a
    # decrypted copy of the whole Blu-ray or UHD disc (BDMV folder), as MakeMKV's
    # backup does; DVDs are ripped as titles either way. drive_modes overrides it per drive.
    rip_mode: Literal["titles", "backup"] = "titles"
    drive_modes: dict[str, Literal["titles", "backup"]] = {}
    # drive id -> a name given in the UI ("Top bay"), shown instead of the model.
    drive_names: dict[str, str] = {}
    # Transcoding (on by default). Sources whose video codec is listed are
    # re-encoded to H.265 before they go to the library.
    transcode_enabled: bool = True
    transcode_codecs: list[str] = ["vc1"]
    # Crop black bars never, only when transcoding anyway, or always (which
    # re-encodes any letterboxed source).
    transcode_crop: Literal["off", "when_transcoding", "always"] = "when_transcoding"
    # HDR and Dolby Vision sources are left alone unless this is on.
    transcode_hdr: bool = False
    # Per kind of disc, whether its rips are transcoded at all and whether their black
    # bars are cropped, as {"bluray": {"transcode": true, "crop": false}}. Missing is
    # on, except transcoding UHD discs, which follows transcode_hdr (see transcode_source).
    transcode_sources: dict[Literal["dvd", "bluray", "uhd"], dict[Literal["transcode", "crop"], bool]] = {}
    transcode_encoder: Literal["auto", "vaapi", "x265"] = "auto"
    # VAAPI constant QP or x265 CRF. Lower is better and bigger.
    transcode_quality: int = Field(21, ge=10, le=40)
    # Keep the untouched rip next to the transcoded file (" - original").
    transcode_keep_original: bool = False
    vaapi_device: str = "/dev/dri/renderD128"
    # Pushover is off until both are set.
    pushover_user_key: SecretStr | None = None
    pushover_app_token: SecretStr | None = None
    pushover_device: str | None = None
    # Which events notify, with their Pushover priority (-2..1). None or missing is off.
    notify_events: dict[str, int | None] = {"disc": None, "review": 1, "exists": 1, "done": 0, "failed": 1,
                                            "setup": 0}
    # More notifications of one kind within this many seconds of the first are
    # sent as one summary at the end of the window (0 sends every one at once).
    notify_batch_s: float = Field(120, ge=0, le=3600)
    # Base URL of this UI as seen from a phone, for notification links.
    public_url: str | None = None
    emby_url: str | None = None
    emby_api_key: SecretStr | None = None
    # The Emby libraries films and TV go into (picked in Settings), and the folder
    # of each as Emby sees it. Our movies/TV folder maps onto it for refreshes.
    emby_movies_library: str | None = None
    emby_movies_path: str | None = None
    emby_tv_library: str | None = None
    emby_tv_path: str | None = None
    # The same for Jellyfin and Plex (whose key is called a token).
    jellyfin_url: str | None = None
    jellyfin_api_key: SecretStr | None = None
    jellyfin_movies_library: str | None = None
    jellyfin_movies_path: str | None = None
    jellyfin_tv_library: str | None = None
    jellyfin_tv_path: str | None = None
    plex_url: str | None = None
    plex_token: SecretStr | None = None
    plex_movies_library: str | None = None
    plex_movies_path: str | None = None
    plex_tv_library: str | None = None
    plex_tv_path: str | None = None
    makemkv_key: SecretStr | None = None
    # TV titles that are not episodes go in an extras folder or Season 00, or are left out.
    tv_unmatched: Literal["extras", "specials", "skip"] = "extras"
    # Titles that look like extras start as "extra" (ripped); off, they start as "skip".
    tv_rip_extras: bool = True
    # Confirm a TV disc without review when everything agrees (label places it,
    # show score >= threshold, runtimes match, order verified or collection continues).
    tv_auto_confirm: bool = True
    tv_auto_threshold: float = Field(0.9, ge=0.5, le=1)
    # Check episode order against the play-all after ripping (needs ffmpeg).
    verify_order: bool = True

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


def load_settings() -> Settings:
    s = Settings()
    overrides = s._read_overrides()
    s.apply(overrides)
    if overrides and "setup_complete" not in overrides:
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
