"""Pydantic models, which are the API contract.

``scripts/gen-types.sh`` exports these as JSON Schema and generates the UI's
TypeScript types from them, so change them here first.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

from .makemkv.parse import RipProgress


class JobState(StrEnum):
    detected = "detected"
    scanning = "scanning"
    identifying = "identifying"
    review = "review"
    clash = "clash"  # the library already has this film or these episodes, so it waits for a decision
    checking = "checking"  # ejected so the user can look at the disc; resumes if the same disc goes back
    ripping = "ripping"
    transcoding = "transcoding"
    finalising = "finalising"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


ACTIVE_STATES = {
    JobState.detected, JobState.scanning, JobState.identifying, JobState.review, JobState.clash, JobState.checking,
    JobState.ripping, JobState.transcoding, JobState.finalising,
}
TERMINAL_STATES = {JobState.done, JobState.failed, JobState.cancelled}

# Allowed transitions. review and ripping overlap, because the rip starts as soon as
# the scan is done, and the job shows "review" while it waits for a human.
TRANSITIONS: dict[JobState, set[JobState]] = {
    JobState.detected: {JobState.scanning},
    JobState.scanning: {JobState.identifying},
    JobState.identifying: {JobState.review, JobState.ripping, JobState.transcoding, JobState.finalising,
                           JobState.clash},
    JobState.review: {JobState.ripping, JobState.transcoding, JobState.finalising, JobState.identifying,
                      JobState.clash, JobState.checking},
    JobState.clash: {JobState.review, JobState.ripping, JobState.transcoding, JobState.finalising,
                     JobState.identifying, JobState.checking},
    JobState.checking: {JobState.review, JobState.clash},
    JobState.ripping: {JobState.transcoding, JobState.finalising, JobState.review, JobState.identifying,
                       JobState.clash},
    JobState.transcoding: {JobState.finalising, JobState.clash},
    JobState.finalising: {JobState.done, JobState.clash},
    JobState.done: {JobState.identifying},
    JobState.failed: {JobState.detected, JobState.scanning, JobState.identifying, JobState.transcoding,
                      JobState.finalising},
    JobState.cancelled: set(),
}
for _s in ACTIVE_STATES:
    TRANSITIONS[_s] |= {JobState.failed, JobState.cancelled}


class DiscKind(StrEnum):
    dvd = "dvd"
    bluray = "bluray"
    uhd = "uhd"
    other = "other"


class RipState(StrEnum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class Evidence(BaseModel):
    ok: bool | None = Field(description="true = supports, false = contradicts, null = neutral")
    text: str


class Candidate(BaseModel):
    source: Literal["tmdb", "cache", "manual", "omdb", "thediscdb"] = "tmdb"
    tmdb_id: int | None = None
    imdb_id: str | None = None
    providers: list[str] = Field(default=[], description="metadata providers that found it, best first")
    title: str
    original_title: str | None = None
    year: int | None = None
    runtime: int | None = Field(None, description="minutes")
    poster_url: str | None = None
    overview: str | None = None
    genre_ids: list[int] = Field(default=[], description="TMDB genre ids (99 = documentary)")
    vote_count: int | None = None
    popularity: float | None = None
    excluded: bool = Field(False, description="a making-of, short or other extra; never auto-confirms")
    score: float = 0.0
    evidence: list[Evidence] = []


class DiscFacts(BaseModel):
    """What identification works from."""

    extended: bool = Field(False, description="the label says extended/director's cut, so the runtime may be longer")
    label: str | None = None
    names: list[str] = Field(default=[], description="names to compare against, best first")
    label_year: int | None = None
    main_duration_s: int | None = None
    pal: bool = False
    kind: DiscKind | None = None


class TitleInfo(BaseModel):
    index: int
    duration_s: int
    size_bytes: int
    chapters: int
    source_file: str | None = None
    video: str | None = None
    resolution: str | None = None
    framerate: str | None = None
    audio: list[str] = []
    subtitles: list[str] = []
    segments_map: str | None = Field(None, description="clip numbers on a BD, cells local to the title on a DVD")
    output_file: str | None = Field(None, description="file name MakeMKV writes for this title")


class DiscInfo(BaseModel):
    label: str | None = None
    name: str | None = None
    kind: DiscKind | None = None
    volume_uuid: str | None = None
    size_bytes: int | None = None
    title_count: int | None = None


class MediaKind(StrEnum):
    movie = "movie"
    tv = "tv"


class EpisodeRef(BaseModel):
    season: int = Field(ge=0)
    episode: int = Field(ge=0)


class Episode(EpisodeRef):
    name: str = ""
    runtime: int | None = Field(None, description="minutes")
    air_date: str | None = None


class Show(BaseModel):
    tmdb_id: int
    tvdb_id: int | None = None
    imdb_id: str | None = None
    name: str
    original_name: str | None = None
    year: int | None = None
    poster_url: str | None = None
    episode_runtime: int | None = Field(None, description="typical minutes, from TMDB")
    seasons: dict[int, int] = Field(default={}, description="season number -> episode count")
    genre_ids: list[int] = []
    vote_count: int | None = None
    providers: list[str] = Field(default=[], description="metadata providers that found it, best first")
    excluded: bool = False
    score: float = 0.0
    evidence: list[Evidence] = []


class DiscMatchTitle(BaseModel):
    """One title of a disc as a disc database lists it."""

    index: int
    source_file: str | None = None
    segment_map: str | None = None
    duration_s: int | None = None
    size_bytes: int | None = None
    kind: Literal["main", "episode", "extra"] | None = Field(None, description="null if not described")
    name: str | None = None
    season: int | None = None
    episode: int | None = None
    scan_index: int | None = Field(None, description="the MakeMKV title it is, on this disc")


class DiscMatch(BaseModel):
    """A disc database's record of this very disc (found by its fingerprint)."""

    provider: str = "thediscdb"
    by: Literal["disc_id", "content_hash"] = "disc_id"
    media: MediaKind
    title: str
    year: int | None = None
    tmdb_id: int | None = None
    imdb_id: str | None = None
    tvdb_id: int | None = None
    release: str | None = Field(None, description="the release the disc belongs to (\"Criterion Blu-ray\")")
    disc_name: str | None = None
    titles: list[DiscMatchTitle] = []


class TitleRole(StrEnum):
    episode = "episode"
    play_all = "play_all"  # all episodes back to back, skipped (used to verify order)
    duplicate = "duplicate"  # same content as another title (angle, language)
    extra = "extra"  # not an episode, so the extras folder or Season 00, per settings
    skip = "skip"  # not copied


class TitleMap(BaseModel):
    title_index: int
    duration_s: int
    role: TitleRole
    episodes: list[EpisodeRef] = Field(default=[], description="two for a double-length (two-part) title")
    note: str | None = Field(None, description="why this role/episode, for the review table")
    match_cost: float | None = Field(None, description="runtime disagreement, 0 = exact (proposals only)")
    same_as: int | None = Field(None, description="duplicate of this title")


class TvPlan(BaseModel):
    show: Show | None = None
    candidates: list[Show] = []
    season: int | None = None
    disc_number: int | None = None
    compilation: bool = Field(False, description="episodes from anywhere in the show, not a run")
    start_episode: int | None = Field(None, description="first episode of this disc in the season")
    mapping: list[TitleMap] = []
    episodes: list[Episode] = Field(default=[], description="episodes offered in the review dropdowns")
    collection_id: int | None = None
    order_verified: bool | None = Field(None, description="frames matched the play-all; null = not checked")
    order_note: str | None = None
    consistent: bool = False
    problems: list[str] = Field(default=[], description="why the mapping cannot be confirmed yet")
    season_poster_url: str | None = None
    user_edited: bool = Field(False, description="changed by hand, so never auto-confirmed")
    continues_collection: bool = Field(False, description="carries on from a confirmed earlier disc")
    label_placed: bool = Field(False, description="the label gave the season and disc number")
    disc_db: bool = Field(False, description="the mapping comes from a disc database's record of this disc")
    auto_confirmed: bool = False
    auto_evidence: list[str] = Field(default=[], description="why it was (or wasn't) confirmed without review")


class Problem(BaseModel):
    code: str
    message: str
    hint: str | None = Field(None, description="what to try, in plain words")
    detail: str | None = Field(None, description="MakeMKV's own message behind it")
    actions: list[Literal["retry_scan", "eject", "retry", "identify_again", "setup"]] = []


class TranscodeFile(BaseModel):
    title_index: int
    source_codec: str | None = None
    action: Literal["transcoded", "skipped", "failed"] = "skipped"
    encoder: str | None = None
    crop: str | None = Field(None, description="ffmpeg crop=w:h:x:y, if bars were removed")
    note: str = ""
    original: str | None = Field(None, description="the untouched rip, when kept")
    size_before: int | None = None
    size_after: int | None = None


class JobStep(BaseModel):
    """What a ripped job is doing right now, off the drive (transcode / copy)."""
    label: str = Field(description="e.g. \"Checking whether title 2 needs transcoding\"")
    detail: str = Field("", description="why, or what exactly, such as the plan, the skip reason or the destination")


class TranscodeInfo(BaseModel):
    state: Literal["pending", "running", "done"] = "pending"
    files: list[TranscodeFile] = []


RipMode = Literal["titles", "backup"]


class RipInfo(BaseModel):
    state: RipState = RipState.pending
    progress: RipProgress | None = None
    file: str | None = None
    all_titles: bool = Field(False, description="every title was ripped (TV), not just the main one")
    files: dict[int, str] = Field(default={}, description="title index -> ripped file (TV)")
    size_bytes: int | None = Field(None, description="what the finished rip wrote")
    seconds: float | None = Field(None, description="how long the finished rip took")
    paused: bool = Field(False, description="stopped by Eject to check. The titles in files are finished, "
                                            "the rest are ripped when the disc is back")


class ClashFile(BaseModel):
    path: str
    size_bytes: int
    modified: datetime


ClashAction = Literal["overwrite", "keep_both", "skip", "skip_episodes"]


class ClashInfo(BaseModel):
    """Files the job would write that are already in the library."""

    files: list[ClashFile]
    proposed: bool = Field(False, description="found for the best guess, before the identity is confirmed")
    decision: ClashAction | None = None
    version: str | None = Field(None, description="with keep both chosen, the new film's file name (an Emby version)")
    keep_both_name: str | None = Field(None, description="the file name 'keep both' would use (films)")


class ClashRequest(BaseModel):
    action: ClashAction


class Preset(BaseModel):
    """What the user says the disc is, given before (or while) it is scanned. Used
    in place of auto-identification once the scan is done."""

    media: MediaKind
    candidate: Candidate | None = Field(None, description="the film (when media is movie)")
    show: Show | None = Field(None, description="the series (when media is tv)")
    season: int | None = Field(None, ge=0)
    disc_number: int | None = Field(None, ge=1)
    start_episode: int | None = Field(None, ge=1)

    @model_validator(mode="after")
    def _picked(self) -> Preset:
        if self.media == MediaKind.movie and not self.candidate:
            raise ValueError("pick the film")
        if self.media == MediaKind.tv and not self.show:
            raise ValueError("pick the show")
        return self


ContributeMode = Literal["off", "ask", "auto"]
# TheDiscDB's item types for what isn't the film or an episode.
ExtraKind = Literal["Extra", "Featurette", "DeletedScene", "Trailer"]


class ContributionExtra(BaseModel):
    """A title sent to TheDiscDB as an extra, under the name the user gave it."""

    title_index: int
    kind: ExtraKind = "Extra"
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class Contribution(BaseModel):
    """Sending this disc to TheDiscDB (the contribute setting in Settings → Metadata providers)."""

    status: Literal["not_sent", "sending", "sent", "failed"] = "not_sent"
    release_id: str | None = Field(None, description="TheDiscDB release this disc goes into; discs posted with "
                                                     "the same one become one release (box sets, seasons)")
    disc_number: int | None = Field(None, description="the disc's number within the release")
    new_release: bool = Field(False, description="the user asked for a release of its own")
    contribute_url: str | None = Field(None, description="where to finish the release on TheDiscDB's site")
    sent_at: datetime | None = None
    updated: bool | None = Field(None, description="TheDiscDB already had this disc from an earlier send")
    error: str | None = None
    items_confirmed: bool = Field(False, description="the user went through the disc's titles and named the "
                                                     "extras to send; nothing is sent before")
    extras: list[ContributionExtra] = Field(default=[], description="titles sent as extras, with their names; "
                                                                    "titles that aren't the film, an episode "
                                                                    "or one of these are left out")


class Job(BaseModel):
    id: int
    drive_id: str
    device: str | None = None
    state: JobState
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    disc: DiscInfo = DiscInfo()
    main_title: TitleInfo | None = None
    candidates: list[Candidate] = []
    identity: Candidate | None = None
    identity_source: Literal["auto", "user", "cache"] | None = None
    media: MediaKind = MediaKind.movie
    media_evidence: list[str] = Field(default=[], description="why the disc looks like TV or a movie")
    tv: TvPlan | None = None
    art: str | None = Field(None, description="poster key for /api/jobs/{id}/art?k=...; changes with the picture")
    art_proposed: bool = Field(False, description="the poster is of a proposed, not confirmed, identity")
    mode: RipMode = Field("titles", description="titles ripped to MKV, or a full decrypted disc backup")
    rip: RipInfo = RipInfo()
    transcode: TranscodeInfo | None = None
    step: JobStep | None = Field(None, description="current sub-step while transcoding or finalising")
    destination: str | None = Field(None, description="library file (backup folder for a backup); planned "
                                                      "until the job is done")
    problem: Problem | None = None
    clash: ClashInfo | None = None
    checking_from: JobState | None = Field(None, description="ejected to check the disc, and this is the state to go back to")
    preset: Preset | None = Field(None, description="identified by hand before the scan finished; used once it does")
    disc_match: DiscMatch | None = Field(None, description="the disc as a disc database (TheDiscDB) knows it")
    contribution: Contribution | None = Field(None, description="this disc sent (or being sent) to TheDiscDB")


class ContributionTitle(BaseModel):
    """One of the disc's titles, as the contribution's naming list shows it."""

    title_index: int
    source: str | None = Field(None, description="playlist or source file")
    duration_s: int
    size_bytes: int
    chapters: int
    fixed: Literal["MainMovie", "Episode"] | None = Field(None, description="sent as the film or an episode, "
                                                                            "named after it")
    name: str | None = Field(None, description="the name it is sent with, for the film and episodes")
    suggested: bool = Field(False, description="the episode mapping made it an extra, so it starts ticked")


class ContributionView(BaseModel):
    """A job's TheDiscDB contribution, whether it can be sent, and exactly what would be."""

    mode: ContributeMode
    eligible: bool = Field(description="it can be sent now")
    reason: str | None = Field(None, description="why it can't be sent (or why it isn't needed)")
    known: bool = Field(False, description="TheDiscDB already knows this disc (it identified it)")
    contribution: Contribution | None = None
    payload: dict | None = Field(None, description="the JSON that is posted, exactly")
    scan_log_lines: int = Field(0, description="lines of the MakeMKV scan sent with it (drive lines and "
                                               "messages left out)")
    base_url: str
    todo: str | None = Field(None, description="what the user has to do before it can be sent")
    titles: list[ContributionTitle] = Field(default=[], description="the disc's titles, for naming the extras")


class ContributionExtrasRequest(BaseModel):
    extras: list[ContributionExtra] = Field(description="titles to send as extras; the rest are left out")


class ContributionReleaseRequest(BaseModel):
    new_release: bool = Field(description="true for a release of its own, false to join a recent one for the "
                                          "same title, if any")


class JobEvent(BaseModel):
    id: int
    job_id: int
    ts: datetime
    kind: str
    message: str


class JobDetail(Job):
    events: list[JobEvent] = []
    titles: list[TitleInfo] = []


class Capability(StrEnum):
    dvd = "dvd"
    bluray = "bluray"
    uhd = "uhd"


class DriveStatus(StrEnum):
    empty = "empty"
    tray_open = "tray_open"
    not_ready = "not_ready"
    disc = "disc"
    error = "error"


class Drive(BaseModel):
    id: str
    device: str
    sg_device: str | None = None
    vendor: str
    model: str
    revision: str | None = None
    serial: str | None = None
    capabilities: list[Capability] = []
    status: DriveStatus = DriveStatus.empty
    enabled: bool = True
    job_id: int | None = None
    problem: Problem | None = None
    worker: str | None = Field(None, description="the remote worker this drive belongs to (None for this host)")
    disc_label: str | None = Field(None, description="the disc's label, for a disabled drive (no job is made)")
    name: str | None = Field(None, description="the name given to the drive in the UI, if any")
    display_name: str = Field("", description="the name, else vendor and model")


class DriveNameUpdate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] = Field(
        description="the drive's new name (trimmed); empty clears it")


class SetupProblem(BaseModel):
    code: str
    message: str
    drive_id: str | None = None
    command: str | None = Field(None, description="a command that fixes it (shown on its own line, to copy)")


TvUnmatched = Literal["extras", "specials", "skip"]
SettingsCheck = Literal["tmdb", "tvdb", "tvmaze", "omdb", "thediscdb", "pushover", "emby", "jellyfin", "plex",
                        "catalogue", "makemkv", "paths"]
MediaServer = Literal["emby", "jellyfin", "plex"]
TvEpisodeOrder = Literal["tmdb", "tvdb_aired", "tvdb_dvd"]
NotifyEvent = Literal["disc", "review", "exists", "done", "failed", "setup"]
PushoverPriority = Annotated[int, Field(ge=-2, le=1)]
TranscodeCrop = Literal["off", "when_transcoding", "always"]
CatalogueSearch = Literal["off", "searxng"]
TranscodeEncoder = Literal["auto", "vaapi", "x265"]
TranscodeSourceKind = Literal["dvd", "bluray", "uhd"]


class TranscodeSource(BaseModel):
    """What the transcode step may do to rips from one kind of disc."""
    transcode: bool = True
    crop: bool = Field(True, description="crop black bars (as the crop setting says)")


ProviderList = Literal["movie", "tv", "episodes"]
ProviderName = Literal["thediscdb", "tmdb", "tvdb", "tvmaze", "omdb"]
TvdbEpisodeOrder = Literal["aired", "dvd"]


class ProviderSlot(BaseModel):
    name: ProviderName
    enabled: bool = True


class ProviderInfo(BaseModel):
    name: ProviderName
    label: str
    description: str
    lists: list[ProviderList] = Field(description="the lists it can be in")
    needs_key: bool = Field(description="it has a key to set (see keys)")
    keys: list[str] = Field(default=[], description="settings that must be set before it is used")
    configured: bool = Field(description="every key it needs is set")
    site: str = Field("", description="where to get a key, or the provider's home page")
    check: SettingsCheck | None = Field(None, description="the Settings test for it")


class SettingsView(BaseModel):
    setup_complete: bool = Field(True, description="the first-start setup page was finished or skipped")
    tmdb_api_key_set: bool
    tmdb_language: str
    tvdb_api_key_set: bool = False
    tvdb_pin_set: bool = False
    tv_episode_order: TvEpisodeOrder = "tvdb_aired"
    tvdb_episode_order: TvdbEpisodeOrder = "aired"
    omdb_api_key_set: bool = False
    thediscdb_contribute: ContributeMode = "off"
    thediscdb_contribute_url: str = "https://thediscdb.com"
    thediscdb_api_key_set: bool = False
    metadata_providers: dict[ProviderList, list[ProviderSlot]] = Field(
        {}, description="each list's providers, in the order they are asked")
    providers: list[ProviderInfo] = []
    auto_confirm_threshold: float
    auto_eject: bool
    scan_timeout_s: int
    scan_max_s: int = 7200
    poll_interval_s: float
    uhd_models: list[str]
    disabled_drives: list[str]
    rip_mode: RipMode = "titles"
    drive_modes: dict[str, RipMode] = Field({}, description="drive id -> its own rip mode, overriding rip_mode")
    drive_names: dict[str, str] = Field({}, description="drive id -> its name (set with PUT /drives/{id}/name)")
    pushover_user_key_set: bool = False
    pushover_app_token_set: bool = False
    pushover_device: str | None = None
    notify_events: dict[NotifyEvent, PushoverPriority | None] = Field(
        {}, description="event -> Pushover priority (-2..1), or null for off")
    notify_batch_s: float = 120
    backup_keep: int = Field(7, description="daily database backups kept, 0 for none")
    backups_dir: str = Field("", description="where the database backups go")
    public_url: str | None
    emby_url: str | None
    emby_api_key_set: bool
    emby_movies_library: str | None = None
    emby_movies_path: str | None = None
    emby_tv_library: str | None = None
    emby_tv_path: str | None = None
    jellyfin_url: str | None = None
    jellyfin_api_key_set: bool = False
    jellyfin_movies_library: str | None = None
    jellyfin_movies_path: str | None = None
    jellyfin_tv_library: str | None = None
    jellyfin_tv_path: str | None = None
    plex_url: str | None = None
    plex_token_set: bool = False
    plex_movies_library: str | None = None
    plex_movies_path: str | None = None
    plex_tv_library: str | None = None
    plex_tv_path: str | None = None
    makemkv_key_set: bool
    library_dir: str
    movies_dir: str = Field("", description="where films go (a separate folder, or <library>/Movies)")
    tv_dir: str = Field("", description="where TV goes (a separate folder, or <library>/TV)")
    disc_backups_dir: str = Field("", description="where full disc backups go")
    scratch_dir: str
    custom_movies_dir: str = Field("", description="the films folder as set, or empty for <library>/Movies")
    custom_tv_dir: str = Field("", description="the TV folder as set, or empty for <library>/TV")
    custom_disc_backups_dir: str = Field("", description="the disc backups folder as set, or empty for <library>/Disc backups")
    makemkv_drive_data: dict[str, bool] = Field(description="drive id -> has imported private data")
    makemkv_drive_dumps: dict[str, bool] = Field({}, description="drive id -> has a firmware dump (dump_*.tgz)")
    tv_unmatched: TvUnmatched = "extras"
    tv_rip_extras: bool = True
    verify_order: bool = True
    tv_auto_confirm: bool = True
    tv_auto_threshold: float = 0.9
    catalogue_search: CatalogueSearch = "off"
    searxng_url: str | None = None
    transcode_enabled: bool = True
    transcode_codecs: list[str] = ["vc1"]
    transcode_crop: TranscodeCrop = "when_transcoding"
    transcode_hdr: bool = False
    transcode_sources: dict[TranscodeSourceKind, TranscodeSource] = Field(
        {}, description="per kind of disc, with the defaults filled in")
    transcode_encoder: TranscodeEncoder = "auto"
    transcode_quality: int = 21
    transcode_keep_original: bool = False
    vaapi_device: str = "/dev/dri/renderD128"


class SettingsUpdate(BaseModel):
    setup_complete: bool | None = None
    catalogue_search: CatalogueSearch | None = None
    searxng_url: str | None = None
    tv_auto_confirm: bool | None = None
    tv_auto_threshold: float | None = Field(None, ge=0.5, le=1)
    transcode_enabled: bool | None = None
    transcode_codecs: list[str] | None = None
    transcode_crop: TranscodeCrop | None = None
    transcode_hdr: bool | None = None
    transcode_sources: dict[TranscodeSourceKind, TranscodeSource] | None = None
    transcode_encoder: TranscodeEncoder | None = None
    transcode_quality: int | None = Field(None, ge=10, le=40)
    transcode_keep_original: bool | None = None
    vaapi_device: str | None = None
    tv_unmatched: TvUnmatched | None = None
    tv_rip_extras: bool | None = None
    verify_order: bool | None = None
    tmdb_api_key: str | None = None
    tmdb_language: str | None = None
    tvdb_api_key: str | None = None
    tvdb_pin: str | None = None
    tv_episode_order: TvEpisodeOrder | None = None
    tvdb_episode_order: TvdbEpisodeOrder | None = None
    omdb_api_key: str | None = None
    thediscdb_contribute: ContributeMode | None = None
    thediscdb_contribute_url: str | None = Field(None, max_length=300)
    thediscdb_api_key: str | None = None
    metadata_providers: dict[ProviderList, list[ProviderSlot]] | None = None
    auto_confirm_threshold: float | None = Field(None, ge=0, le=1)
    auto_eject: bool | None = None
    scan_timeout_s: int | None = Field(None, ge=30, le=3600)
    scan_max_s: int | None = Field(None, ge=300, le=43200)
    poll_interval_s: float | None = Field(None, ge=1, le=60)
    uhd_models: list[str] | None = None
    disabled_drives: list[str] | None = None
    rip_mode: RipMode | None = None
    drive_modes: dict[str, RipMode] | None = None
    pushover_user_key: str | None = None
    pushover_app_token: str | None = None
    pushover_device: str | None = None
    notify_events: dict[NotifyEvent, PushoverPriority | None] | None = None
    notify_batch_s: float | None = Field(None, ge=0, le=3600)
    backup_keep: int | None = Field(None, ge=0, le=365)
    public_url: str | None = None
    emby_url: str | None = None
    emby_api_key: str | None = None
    emby_movies_library: str | None = None
    emby_movies_path: str | None = None
    emby_tv_library: str | None = None
    emby_tv_path: str | None = None
    jellyfin_url: str | None = None
    jellyfin_api_key: str | None = None
    jellyfin_movies_library: str | None = None
    jellyfin_movies_path: str | None = None
    jellyfin_tv_library: str | None = None
    jellyfin_tv_path: str | None = None
    plex_url: str | None = None
    plex_token: str | None = None
    plex_movies_library: str | None = None
    plex_movies_path: str | None = None
    plex_tv_library: str | None = None
    plex_tv_path: str | None = None
    makemkv_key: str | None = None
    library_dir: str | None = None
    movies_dir: str | None = Field(None, description="empty for <library>/Movies")
    tv_dir: str | None = Field(None, description="empty for <library>/TV")
    disc_backups_dir: str | None = Field(None, description="empty for <library>/Disc backups")
    scratch_dir: str | None = None




class SettingsTestRequest(BaseModel):
    check: SettingsCheck
    values: SettingsUpdate = Field(default_factory=SettingsUpdate,
                                   description="the form as typed; blank secrets mean the saved ones")


class SettingsTestResult(BaseModel):
    ok: bool
    message: str
    details: list[str] = []


class ServerLibrary(BaseModel):
    """A library on a media server (Emby, Jellyfin or Plex)."""
    id: str
    name: str
    type: str | None = Field(None, description="movies, tvshows, mixed, … (Emby's CollectionType, Plex's types mapped onto it)")
    locations: list[str] = Field(description="its folders, as the server sees them")


class ServerLibrariesRequest(BaseModel):
    url: str | None = Field(None, description="blank for the saved URL")
    api_key: str | None = Field(None, description="blank for the saved key")


class TvProposeRequest(BaseModel):
    show_tmdb_id: int
    season: int | None = Field(None, ge=0)
    disc_number: int | None = Field(None, ge=1)
    start_episode: int | None = Field(None, ge=1)
    compilation: bool = False


class TvMatchTextRequest(BaseModel):
    text: str = Field(max_length=20000, description="episode names in disc order, one per line or comma separated")


class TitleMapUpdate(BaseModel):
    title_index: int
    role: TitleRole
    episodes: list[EpisodeRef] = []


class TvConfirmRequest(BaseModel):
    mapping: list[TitleMapUpdate]


class MediaRequest(BaseModel):
    media: MediaKind


class SeasonCell(BaseModel):
    episode: Episode
    status: Literal["present", "missing", "duplicate"]
    jobs: list[int] = []


class SeasonGrid(BaseModel):
    season: int
    cells: list[SeasonCell]
    present: int
    total: int


class CollectionDisc(BaseModel):
    job_id: int
    season: int | None
    disc_number: int | None
    label: str | None
    episodes: list[EpisodeRef] = []
    compilation: bool = False


class Collection(BaseModel):
    id: int
    show: Show
    discs: list[CollectionDisc] = []
    known_episodes: list[Episode] = Field(default=[], description="TMDB episodes seen for this show")
    updated_at: datetime


class CollectionDetail(Collection):
    seasons: list[SeasonGrid] = []


class ConfirmRequest(BaseModel):
    candidate: Candidate


class SearchRequest(BaseModel):
    query: str
    year: int | None = None


class ImportPrivateDataResult(BaseModel):
    drive_ids: list[str]
    files: int


class MakeMKVFile(BaseModel):
    name: str = Field(description="_private_data.tar or dump_*.tgz")
    sha256: str
    size: int
    drives: list[str] | None = Field(description="drive ids it is for, or null for every drive, including later ones")
    added_at: str


class RemoteWorker(BaseModel):
    id: int
    name: str
    worker_id: str = Field(description="the RIPSHELF_WORKER_ID, because the token works for this worker only")
    created_at: str
    last_seen_at: str | None = Field(description="last request with this token (to within 30 s)")
    online: bool


class RemoteWorkers(BaseModel):
    workers: list[RemoteWorker]
    shared_token: bool = Field(description="the shared RIPSHELF_WORKER_TOKEN (or token file) is set")


class NewRemoteWorkerRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    controller_url: str | None = Field(None, max_length=300,
                                       description="as the worker reaches the controller, or blank for the public URL "
                                                   "setting, else the address this page was loaded from")


class NewRemoteWorker(BaseModel):
    worker: RemoteWorker
    token: str = Field(description="shown once; only its hash is kept")
    compose: str = Field(description="a compose file for the worker, token included")


class StorageFolder(BaseModel):
    name: str
    path: str
    exists: bool = Field(description="false if not made yet (sizes are for the folder it would be made in)")


class Filesystem(BaseModel):
    """One disk (filesystem) and the ripshelf folders on it. Sizes in bytes; none when not found."""
    folders: list[StorageFolder]
    total: int | None = None
    used: int | None = None
    free: int | None = Field(None, description="free for ripshelf (excludes space reserved for root)")
    warning: str | None = None


class DriveStats(BaseModel):
    """All-time totals for one drive. Kept when job history is cleared."""

    drive_id: str
    model: str | None = None
    name: str | None = Field(None, description="the name given to the drive in the UI, if any")
    connected: bool = Field(description="the drive is attached now")
    discs_ripped: int = Field(description="jobs whose rip finished (retries of one job count once)")
    data_bytes: int = Field(description="what those rips wrote")
    rip_seconds: float = Field(description="time spent ripping (timed rips only)")
    timed_bytes: int = Field(description="data from rips with a known time, for the average speed")
    failed: int = Field(description="jobs that failed before the rip finished")
    cancelled: int = Field(description="jobs cancelled before the rip finished")
    skipped: int = Field(description="finished without ripping, e.g. already in the library")
    by_kind: dict[str, int] = Field(description="discs ripped by disc kind (dvd, bluray, uhd, other)")
    movies: int
    tv: int
    backups: int
    first_at: datetime | None = None
    last_at: datetime | None = None


class HwDevice(BaseModel):
    path: str
    vendor: str | None = None
    driver: str | None = None
    model: str | None = None
    accessible: bool = Field(description="ripshelf may open it (read and write)")


class HwEncoder(BaseModel):
    name: str = Field(description="the ffmpeg encoder, e.g. hevc_vaapi")
    codec: str
    method: str
    device: str | None = None
    description: str
    works: bool = Field(description="a test encode succeeded")
    tested: bool = Field(True, description="false if not tried, because there is no GPU for it")
    ten_bit: bool | None = Field(None, description="10-bit (Main10) test encode succeeded, where tested")
    error: str | None = None
    used_by_ripshelf: bool = False


class Hardware(BaseModel):
    devices: list[HwDevice]
    encoders: list[HwEncoder]
    ffmpeg_found: bool
    encoder: Literal["vaapi", "x265"] = Field(description="what ripshelf transcodes with now")
    encoder_note: str
    checked_at: float


class Status(BaseModel):
    version: str
    makemkv_version: str | None = None
    fake_hardware: bool
    problems: list[SetupProblem]
    review_count: int


class Tool(BaseModel):
    found: bool
    path: str | None = Field(None, description="where it is, if found")


class SetupTools(BaseModel):
    """The programs ripshelf runs, for the setup page."""
    makemkv: Tool
    makemkv_registered: bool = Field(description="a MakeMKV key is set here (or, on Windows, in MakeMKV itself)")
    ffmpeg: Tool
    ffprobe: Tool
    java: Tool = Field(description="a Java runtime, which MakeMKV needs for some Blu-ray menus (BD-J)")
    makemkv_eula_pending: bool = Field(False, description="MakeMKV came with ripshelf and its licence isn't accepted yet")
    windows: bool = False


class FolderRequest(BaseModel):
    path: str = Field(max_length=1000)
    create: bool = Field(False, description="make it (and its parents) if it doesn't exist")


class FolderState(BaseModel):
    path: str
    exists: bool
    created: bool = False
    free: int | None = Field(None, description="bytes free where it is (or would be)")
    error: str | None = None


class LogEntry(BaseModel):
    seq: int = Field(description="increasing number; ask for entries after the last one you have")
    ts: datetime
    level: str
    logger: str
    message: str


class LogPage(BaseModel):
    entries: list[LogEntry]
    last_seq: int
    file: str | None = Field(None, description="the log file on the server, if there is one")


class DeleteJobsRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=1000)


class DeleteJobsResult(BaseModel):
    deleted: list[int]


# Server-sent events. ``type`` discriminates.
class JobUpdated(BaseModel):
    type: Literal["job"] = "job"
    job: Job


class DriveUpdated(BaseModel):
    type: Literal["drive"] = "drive"
    drive: Drive


class RipProgressEvent(BaseModel):
    type: Literal["progress"] = "progress"
    job_id: int
    progress: RipProgress


class JobEventAdded(BaseModel):
    type: Literal["event"] = "event"
    event: JobEvent


class StatusChanged(BaseModel):
    type: Literal["status"] = "status"
    status: Status


class JobsDeleted(BaseModel):
    type: Literal["jobs_deleted"] = "jobs_deleted"
    ids: list[int]


ServerEvent = JobUpdated | DriveUpdated | RipProgressEvent | JobEventAdded | StatusChanged | JobsDeleted
