"""Pydantic models, which are the API contract.

``scripts/gen-types.sh`` exports these as JSON Schema and generates the UI's
TypeScript types from them, so change them here first.
"""

from __future__ import annotations

import operator
from datetime import date, datetime
from enum import StrEnum
from functools import reduce
from pathlib import Path
from types import UnionType
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import (
    BaseModel,
    Field,
    SecretStr,
    StringConstraints,
    create_model,
    field_validator,
    model_validator,
)
from pydantic.fields import FieldInfo
from pydantic_core import to_jsonable_python

from .config import EDITABLE, FOLDERS, SECRETS, UI, Settings, ui_of
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
                      JobState.clash, JobState.checking, JobState.done},
    JobState.clash: {JobState.review, JobState.ripping, JobState.transcoding, JobState.finalising,
                     JobState.identifying, JobState.checking},
    JobState.checking: {JobState.review, JobState.clash},
    JobState.ripping: {JobState.transcoding, JobState.finalising, JobState.review, JobState.identifying,
                       JobState.clash},
    JobState.transcoding: {JobState.finalising, JobState.clash},
    JobState.finalising: {JobState.done, JobState.clash},
    # A finished job identified again goes back to review, and back to done if kept as it was.
    JobState.done: {JobState.identifying, JobState.review},
    JobState.failed: {JobState.detected, JobState.scanning, JobState.identifying, JobState.transcoding,
                      JobState.finalising, JobState.done},
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
    source: Literal["tmdb", "cache", "manual", "omdb", "thediscdb", "tvdb", "tvmaze"] = "tmdb"
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
    from_ai: bool = Field(False, description="only the AI model's reading of the label supports it; never "
                                             "auto-confirms")
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


ShowSource = Literal["tmdb", "tvdb", "tvmaze"]


class Show(BaseModel):
    """A TV series. ``source`` is the provider whose id names it: TMDB when there is a
    TMDB key, else TheTVDB or TVmaze. ``key`` ("tmdb:1399") is that provider and id."""

    source: ShowSource = "tmdb"
    key: str = Field("", description="the source and its id, \"tvmaze:82\"; set from them")
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    imdb_id: str | None = None
    tvmaze_id: int | None = None
    name: str
    original_name: str | None = None
    year: int | None = None
    poster_url: str | None = None
    episode_runtime: int | None = Field(None, description="typical minutes, from the show's source")
    seasons: dict[int, int] = Field(default={}, description="season number -> episode count")
    genre_ids: list[int] = []
    vote_count: int | None = None
    providers: list[str] = Field(default=[], description="metadata providers that found it, best first")
    excluded: bool = False
    from_ai: bool = Field(False, description="found by searching for the AI model's reading of the label; "
                                             "never auto-confirms")
    score: float = 0.0
    evidence: list[Evidence] = []

    @model_validator(mode="after")
    def _keyed(self) -> Show:
        id_ = getattr(self, f"{self.source}_id")
        if not id_:
            raise ValueError(f"a show from {self.source} needs its {self.source}_id")
        self.key = f"{self.source}:{id_}"
        return self


def parse_show_key(key: str) -> tuple[ShowSource, int]:
    """("tvmaze", 82) from "tvmaze:82"; a bare number is a TMDB id."""
    source, _, id_ = key.rpartition(":")
    source = source or "tmdb"
    if source not in get_args(ShowSource) or not id_.isdigit() or int(id_) <= 0:
        raise ValueError(f"not a show: {key!r}")
    return source, int(id_)  # type: ignore[return-value]


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
    release: str | None = Field(None, description="the release the disc belongs to (\"Archive Blu-ray\")")
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
    episodes: list[EpisodeRef] = Field(default=[], description="the episodes it holds: two for a two-part story, or any from the show")
    note: str | None = Field(None, description="why this role/episode, for the review table")
    match_cost: float | None = Field(None, description="runtime disagreement, 0 = exact (proposals only)")
    same_as: int | None = Field(None, description="duplicate of this title")
    part_of: int | None = Field(None, description="a part of this title, split at its chapters")
    start_s: float | None = Field(None, description="where the part starts in that title")
    by_hand: bool = Field(False, description="set in the review by hand, so the cover scan's picks leave it alone")


class TitleSplit(BaseModel):
    """One long title holding several episodes, to be split at its chapter marks, or at
    black frames and silence found in it when it has too few chapters."""

    title_index: int
    duration_s: float
    expected: int = Field(description="about how many episodes it holds, from the season's runtimes")
    reason: str
    was: TitleRole = Field(TitleRole.episode, description="the title's role before it was split")
    state: Literal["waiting", "ready", "failed", "split"] = Field(
        "waiting", description="waiting for the rip, chapters read, chapters unreadable, split into files")
    error: str | None = None
    chapters: list[float] = Field(default=[], description="marks it can be cut at (s): its chapters, or breaks "
                                  "found in it, and cuts put in by hand")
    kinds: list[Literal["chapter", "start", "break", "manual"]] = Field(
        default=[], description="per mark: a chapter, the start, a break found by a scan, a cut put in by hand")
    marks_from: Literal["chapters", "scan"] = Field(
        "chapters", description="the marks are its chapters, or breaks found by searching all of it")
    breaks: list[float | None] = Field(
        default=[], description="per mark, 0..1: black frames and silence at it (null = not checked)")
    cuts: list[int] = Field(default=[], description="marks (0-based) where an episode starts")
    proposed: list[int] = Field(default=[], description="the cuts as first proposed")
    confidence: float | None = Field(None, description="0..1, how well the proposed parts fit the runtimes")
    note: str | None = None
    edited: bool = Field(False, description="cuts changed by hand, so kept when the mapping is re-proposed")
    base: int = Field(1000, description="the title number its first part has in the mapping (1000, 1100, ...)")

    def part(self, k: int) -> int:
        """The title number of its part ``k``."""
        return self.base + k

    def has_part(self, title_index: int) -> bool:
        return self.base <= title_index < self.base + 100


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
    bonus: list[BonusEpisode] = Field(default=[], description="bonus episodes of other shows the box lists, "
                                                         "as found in those shows' episode lists")
    from_scan: bool = Field(False, description="the show or episodes were set from the cover scan, so it's "
                                               "reviewed, not confirmed without review")
    order_from: Literal["season", "box", "names", "disc_db"] | None = Field(
        None, description="what put the episodes on the titles in this order: the season's own order from "
                          "the start episode (a guess), the cover scan's list, a pasted run order, or TheDiscDB")
    auto_confirmed: bool = False
    auto_evidence: list[str] = Field(default=[], description="why it was (or wasn't) confirmed without review")
    splits: list[TitleSplit] = Field(default=[], description="long titles each holding several episodes, split "
                                                             "at chapters (in title order)")
    split_off: bool = Field(False, description="the user keeps every long title whole")
    whole: list[int] = Field(default=[], description="long titles the user keeps whole")

    @model_validator(mode="before")
    @classmethod
    def _one_split(cls, data):
        # Plans saved before a disc could have more than one long title split.
        if isinstance(data, dict) and "split" in data:
            data = dict(data)
            old = data.pop("split")
            if old is not None and not data.get("splits"):
                data["splits"] = [old]
        return data

    @property
    def split(self) -> TitleSplit | None:
        """The first long title split (most discs have one)."""
        return self.splits[0] if self.splits else None

    def split_of(self, title_index: int) -> TitleSplit | None:
        """The split a title (the long one, or one of its parts) belongs to."""
        return next((sp for sp in self.splits if sp.title_index == title_index or sp.has_part(title_index)), None)


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


class RefileMove(BaseModel):
    src: str
    dst: str
    done: bool = False


class Refile(BaseModel):
    """A finished job being identified again. Its files stay where they are in the library
    until the new identity is confirmed, and are then moved (not ripped again) to its names."""

    title: str = Field(description="what it was filed as, \"Moonlit Orchard (2004)\"")
    identity: Candidate | None = Field(None, description="the identity it had, put back if kept as it was")
    identity_source: Literal["auto", "user", "cache"] | None = None
    destination: str | None = None
    tv: TvPlan | None = None
    candidates: list[Candidate] = []
    finished_at: datetime | None = None
    files: dict[int, str] = Field(default={}, description="title index -> its file in the library")
    moves: list[RefileMove] = Field(default=[], description="planned once confirmed, each marked done once moved, "
                                                           "so a move that failed half way can be retried")


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
# TheDiscDB's item types for what isn't the film or an episode: every type its site
# offers. All but Trailer and DeletedScene count as extras when it identifies discs.
ExtraKind = Literal["Extra", "Featurette", "DeletedScene", "Trailer", "Interview", "Scene", "Music",
                    "Short", "Other"]
CoverSide = Literal["front", "back"]


# Pictures of the box: the front and back covers, the whole outer sheet (back, spine
# and front side by side), its inside face, and booklet or insert pages.
ScanKind = Literal["front", "back", "outside_full", "inside_full", "insert", "other"]
ScanSource = Literal["scanner", "upload", "camera"]
ScanRotate = Literal[0, 90, 180, 270]


class ScanSplit(BaseModel):
    """Where a whole outer sheet is cut into its back (left) and front (right) halves."""

    position: float = Field(0.5, ge=0.2, le=0.8, description="the middle of the spine, as a fraction "
                                                             "of the width")
    spine: float = Field(0.05, ge=0, le=0.2, description="the spine's width, left out of both halves")
    swap: bool = Field(False, description="the front is the left half")
    auto: bool = Field(False, description="found by ripshelf (the spine's edges in the picture), not set by hand")


class CoverScan(BaseModel):
    """A scan or photo of the disc's box, kept with the job (and filed in the library)."""

    id: str
    kind: ScanKind
    source: ScanSource
    page: int | None = Field(None, description="the page, for inserts scanned from the feeder")
    created_at: datetime
    content_type: str = Field(description="image/jpeg, image/png or image/webp")
    width: int
    height: int
    sha256: str
    size_bytes: int
    rotate: ScanRotate = Field(0, description="turned clockwise by this much when shown or used")
    crop: list[int] | None = Field(None, description="the picture within the scan, [left, top, right, "
                                                     "bottom] in the original's pixels, when trimmed")
    crop_by_hand: bool = Field(False, description="the crop was set by hand, not found by trimming")
    split: ScanSplit | None = Field(None, description="the cut last used for the covers, for a whole sheet")
    vision: Literal["queued", "reading", "done", "failed"] | None = Field(
        None, description="reading it with the vision model")
    vision_message: str | None = Field(None, description="why the vision model's reading failed, or what it took")
    library_path: str | None = Field(None, description="its copy in the library, once filed")


class TextLine(BaseModel):
    """A line of text read on a scan."""

    text: str
    conf: float = Field(description="Tesseract's confidence, 0 to 100")
    box: list[float] = Field(description="[left, top, right, bottom] as fractions of the picture as shown "
                                         "(trimmed and turned)")
    region: str = Field("page", description="the part of the picture: front, spine or back of a whole outer "
                                            "sheet, left or right of its inside, else page")


class ScanText(BaseModel):
    """What was read on one scan."""

    scan_id: str
    key: str = Field(description="what the reading depends on (the file, kind, trim, turn and cut); a "
                                 "different key means it needs reading again")
    read_at: datetime
    engine: str
    seconds: float = 0
    turned: int = Field(0, description="the quarter turns the text needed, which the scan was given")
    lines: list[TextLine] = []
    more: list[TextLine] = Field(default=[], description="the other reading of each part, used only for the "
                                                         "release details")
    barcodes: list[str] = Field(default=[], description="EAN-13 and UPC-A barcodes decoded from the picture")


ListingKind = Literal["episode", "extra"]
ItemSource = Literal["tesseract", "vision", "both", "you"]
FieldSource = Literal["tesseract", "vision", "barcode", "you"]


class ListingItem(BaseModel):
    """An episode or extra the box lists."""

    kind: ListingKind
    title: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]
    number: int | None = Field(None, description="the episode's number, when printed")
    runtime_min: float | None = Field(None, description="the printed running time, in minutes")
    extra_type: ExtraKind | None = Field(None, description="TheDiscDB's type, for an extra")
    disc: int | None = Field(None, description="the disc it's on, when the box splits them by disc")
    text: str | None = Field(None, description="the line as read, before the title was picked out of it")
    scan_id: str | None = Field(None, description="the scan it was read on")
    box: list[float] | None = Field(None, description="where on that scan, as fractions (see TextLine)")
    source: ItemSource | None = Field(None, description="who read it: Tesseract, the vision model, both, or "
                                                        "the user")
    show: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = Field(
        None, description="for a bonus episode of another show (an extra): that show, the title being the episode's")


AudioFormat = Literal["Dolby Digital", "Dolby Digital Plus", "Dolby TrueHD", "Dolby Atmos", "DTS", "DTS-HD MA",
                      "DTS-HD HR", "DTS:X", "LPCM", "MPEG", "PCM"]


class AudioTrack(BaseModel):
    """An audio track the box lists."""

    language: str
    format: AudioFormat | None = None
    channels: Literal["mono", "2.0", "5.1", "6.1", "7.1"] | None = None
    note: str | None = Field(None, description="such as commentary or audio description")


class SubtitleTrack(BaseModel):
    language: str
    hard_of_hearing: bool = False
    note: str | None = Field(None, description="such as \"movie only\" or \"extras\"")


class ListingDetails(BaseModel):
    """What the box says about the release."""

    title: str | None = None
    year: int | None = Field(None, description="the copyright year of the film or show")
    cast: list[str] = []
    feature_runtime_min: float | None = None
    episode_count: int | None = Field(None, description="from wording such as \"13 episodes\"")
    barcode: str | None = Field(None, description="EAN-13 or UPC-A, check digit verified")
    barcode_source: Literal["barcode", "text"] | None = Field(None, description="decoded from the bars, "
                                                                                 "or read from the digits")
    catalogue_number: str | None = None
    region: str | None = None
    region_guessed: bool = Field(False, description="not printed as text: worked out from the video system "
                                                    "and country")
    video_system: Literal["PAL", "NTSC"] | None = None
    country: str | None = Field(None, description="such as UK or US, from the box")
    locale: str | None = None
    aspect_ratio: str | None = None
    audio: list[AudioTrack] = []
    subtitles: list[SubtitleTrack] = []
    video: list[str] = Field(default=[], description="resolution and HDR the box claims: 4K UHD, 1080p, HDR10, "
                                                     "HDR10+, Dolby Vision")
    sources: dict[str, FieldSource] = Field(default={}, description="field -> who read it")


class Listing(BaseModel):
    """What's on the disc, as the box tells it."""

    items: list[ListingItem] = []
    details: ListingDetails = Field(default_factory=ListingDetails)
    notes: list[str] = Field(default=[], description="what the reader noticed, such as a count that "
                                                     "doesn't match")


ListingState = Literal["idle", "reading", "done", "failed", "unavailable"]


class ScanListing(BaseModel):
    """The listing read from a job's scans, and the user's own version of it."""

    state: ListingState = "idle"
    message: str | None = None
    progress: str | None = Field(None, description="what is being read now")
    read_at: datetime | None = None
    engine: str | None = None
    found: Listing | None = Field(None, description="what reading found, replaced by each reading")
    edited: Listing | None = Field(None, description="the user's listing, kept until they drop it")
    edited_from: datetime | None = Field(None, description="the reading the edits started from")
    upc_title: str | None = Field(None, description="what the box's barcode names (\"\" when no source knows it; "
                                                    "None until every source has answered)")
    upc_code: str | None = Field(None, description="the barcode upc_title is for (it is looked up again when the "
                                                   "box's barcode changes)")
    upc_source: str | None = Field(None, description="where upc_title came from: thediscdb, upcitemdb, "
                                                     "barcodelookup, eansearch or web")
    identified_from: str | None = Field(None, description="what the box said when the disc was last identified "
                                                          "again from it")

    @property
    def stale_edits(self) -> bool:
        return bool(self.edited and self.read_at and self.edited_from and self.read_at > self.edited_from)


class ListingView(BaseModel):
    listing: ScanListing
    current: Listing | None = Field(None, description="the edited listing if there is one, else the found one")
    newer_reading: bool = Field(False, description="the scans were read again after the listing was edited, "
                                                   "and the reading differs")
    available: bool = Field(description="text reading can run here (Tesseract is installed and switched on)")


Confidence = Literal["high", "medium", "low"]


class BonusEpisode(BaseModel):
    """A bonus episode of another show the box lists, found in that show's episode list."""

    box_show: str = Field(description="the show as the box names it")
    box_title: str = Field(description="the episode as the box names it")
    show: Show
    episode: Episode

    @property
    def name(self) -> str:
        """What it's called as an extra: "<show> - <episode>"."""
        return f"{self.show.name} - {self.episode.name or self.box_title}"


class EpisodeGuess(Episode):
    """An episode a box's line may be, for the review to offer."""

    score: float = Field(description="how well the name, and the title's length, fit it (0-1)")


class TitleMatch(BaseModel):
    """A disc title, and the line of the box's listing it was matched to."""

    title_index: int
    duration_s: int = 0
    kind: Literal["feature", "episode", "extra"]
    item: int | None = Field(None, description="the listing item (its index), none for the main feature")
    name: str = Field(description="the title as the box gives it")
    episodes: list[EpisodeRef] = Field(default=[], description="the episode it is, when its name was found in "
                                                               "the season's episode list")
    extra_type: ExtraKind | None = None
    score: float
    confidence: Confidence
    reason: str = Field(description="why, for the user: runtimes, order, names")
    agrees: bool | None = Field(None, description="the existing mapping or main title says the same (none when "
                                                  "there is nothing to compare)")
    current: str | None = Field(None, description="what the existing mapping says instead, when it disagrees")
    bonus: BonusEpisode | None = Field(None, description="a bonus episode of another show, found in its "
                                                         "episode list")
    guesses: list[EpisodeGuess] = Field(default=[], description="for an episode line, the episodes its name "
                                                                "and the title's length fit best, best first")


class ScanMatch(BaseModel):
    """The listing read from the box, matched to the disc's titles."""

    matches: list[TitleMatch] = []
    unmatched_items: list[int] = Field(default=[], description="listing items no title fits")
    unmatched_titles: list[int] = Field(default=[], description="titles (not play-alls or duplicates) no line fits")
    claimed: list[int] = Field(default=[], description="listing items other discs of the release took")
    confidence: Confidence | None = Field(None, description="for the disc as a whole")
    notes: list[str] = []
    made_at: datetime | None = None


class PendingScan(CoverScan):
    """A picture of a box kept for the next disc a drive gets."""

    drive_id: str
    expires_at: datetime = Field(description="forgotten after this if no disc goes in")


class PendingScans(BaseModel):
    drive_id: str
    scans: list[PendingScan] = []
    scanner: ScannerRun | None = Field(None, description="a scan for the next disc, under way or failed")


ScannerSource = Literal["platen", "feeder"]
ScannerState = Literal["scanning", "failed"]


class ScannerRun(BaseModel):
    """The scanner working for this job, or why it couldn't."""

    state: ScannerState
    kind: ScanKind
    source: ScannerSource
    duplex: bool = False
    pages: int = Field(0, description="pages received so far")
    started_at: datetime
    message: str | None = None


class ContributionExtra(BaseModel):
    """A title sent to TheDiscDB as an extra, under the name the user gave it."""

    title_index: int
    kind: ExtraKind = "Extra"
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class ReleaseDetails(BaseModel):
    """What TheDiscDB asks about the release (the box) rather than the disc. Its import
    takes the UPC and the covers, and the rest is typed into its form when finishing, so
    ripshelf keeps them to show there."""

    upc: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^(\d{12,13})?$")] | None = Field(
        None, description="the barcode on the box, 12 (UPC) or 13 (EAN) digits")
    asin: Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True,
                                           pattern=r"^([A-Za-z0-9]{10})?$")] | None = Field(
        None, description="Amazon's product id for this release")
    asin_unavailable: bool = Field(False, description="Amazon doesn't sell this release")
    release_date: date | None = Field(None, description="when this release came out")
    locale: Annotated[str, StringConstraints(strip_whitespace=True, max_length=16,
                                             pattern=r"^([A-Za-z]{2,3}(-[A-Za-z]{2})?)?$")] | None = Field(
        None, description="language and country the release is for, such as en-GB")
    region_code: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] | None = Field(
        None, description="the disc's playback regions, such as 2, B or Region free")

    @field_validator("upc", "asin", "locale", "region_code", mode="after")
    @classmethod
    def _blank_is_none(cls, v: str | None) -> str | None:
        return v or None

    def empty(self) -> bool:
        return not (self.upc or self.asin or self.asin_unavailable or self.release_date or self.locale
                    or self.region_code)


class Contribution(BaseModel):
    """Sending this disc to TheDiscDB (the contribute setting in Settings → Identify)."""

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
    details: ReleaseDetails = Field(default_factory=ReleaseDetails, description="the release's UPC, ASIN, "
                                                                                "date, locale and region")
    covers: list[CoverSide] = Field(default=[], description="cover pictures added, sent with the disc")


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
    scans: list[CoverScan] = Field(default=[], description="scans and photos of the box")
    scanner: ScannerRun | None = Field(None, description="a scan from the network scanner, under way or failed")
    listing: ScanListing | None = Field(None, description="what's on the disc, read from the scans")
    scan_match: ScanMatch | None = Field(None, description="the listing matched to the disc's titles")
    refile: Refile | None = Field(None, description="finished, and being identified again")


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
    reread: bool = Field(False, description="the content hash is missing and the disc is in its drive, so it "
                                            "can be read again for it (POST .../thediscdb/reread)")


class ReleaseProduct(BaseModel):
    """A product listing a lookup found: maybe this release."""

    upc: str | None = None
    title: str
    brand: str | None = None
    asin: str | None = None
    images: list[str] = Field(default=[], description="product pictures, usable as the front cover")


class ReleaseLookupRequest(BaseModel):
    upc: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{12,13}$")] | None = Field(
        None, description="look this barcode up; without one, the release is searched for by title and format")


class ReleaseLookup(BaseModel):
    """Suggestions for the release details, for the user to take or leave."""

    products: list[ReleaseProduct] = Field(default=[], description="listings that may be this release, best "
                                                                   "first")
    suggested: ReleaseDetails = Field(default_factory=ReleaseDetails, description="values worked out without a "
                                                                                  "product: the date from TMDB, "
                                                                                  "the region read from the disc")
    notes: list[str] = Field(default=[], description="where each value came from, and what couldn't be found")


class CoverFromUrlRequest(BaseModel):
    url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=8, max_length=2000)]


class ScanUpdate(BaseModel):
    kind: ScanKind | None = None
    page: int | None = Field(None, ge=1, le=999)
    rotate: ScanRotate | None = None
    trim: bool | None = Field(None, description="cut away the scanner's empty glass around the picture "
                                                "(found again), or with false keep the whole scan")
    crop: list[float] | None = Field(None, min_length=4, max_length=4, description=(
        "crop by hand: [left, top, right, bottom] as fractions of the whole scan as shown (turned, "
        "not trimmed)"))

    @field_validator("crop", mode="after")
    @classmethod
    def _crop_box(cls, v: list[float] | None) -> list[float] | None:
        if v is not None:
            left, top, right, bottom = v
            if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
                raise ValueError("the crop must be [left, top, right, bottom] within 0 to 1")
            if right - left < 0.05 or bottom - top < 0.05:
                raise ValueError("the crop leaves too little of the scan")
        return v


class ScannerScanRequest(BaseModel):
    kind: ScanKind
    source: ScannerSource | None = Field(None, description="the glass or the feeder; the Settings default "
                                                          "if left out")
    duplex: bool = Field(False, description="both sides of each page (feeder only)")
    dpi: int | None = Field(None, description="the Settings default if left out")


class ScannerInfo(BaseModel):
    """The network scanner, as it describes itself."""

    enabled: bool
    url: str | None = None
    ok: bool = False
    message: str | None = None
    model: str | None = None
    state: str | None = Field(None, description="Idle, Processing, Testing or Stopped")
    adf_state: str | None = Field(None, description="the feeder's state, such as ScannerAdfLoaded")
    sources: list[ScannerSource] = []
    duplex: bool = False
    resolutions: list[int] = []
    busy_job: int | None = Field(None, description="the job the scanner is scanning for")
    vision: str | None = Field(None, description="the vision model set up to read scans (model at where), if any")
    vision_cloud: bool = Field(False, description="the vision model is a cloud service")


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
                        "catalogue", "makemkv", "paths", "scanner", "ocr", "vision", "label_ai"]
VisionMode = Literal["off", "struggles", "every", "manual"]
VisionProvider = Literal["local", "openai", "anthropic"]
MediaServer = Literal["emby", "jellyfin", "plex"]
TvEpisodeOrder = Literal["tmdb", "tvdb_aired", "tvdb_dvd"]
NotifyEvent = Literal["disc", "review", "exists", "ripped", "transcoded", "copied", "done", "failed", "setup"]
PushoverPriority = Annotated[int, Field(ge=-2, le=1)]
TranscodeCrop = Literal["off", "when_transcoding", "always"]
CatalogueSearch = Literal["off", "searxng"]
TranscodeEncoder = Literal["auto", "qsv", "vaapi", "nvenc", "x265"]
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


SettingsSection = Literal["identify", "drives", "scanner", "library", "media-servers", "transcoding",
                          "notifications", "workers", "system"]
SettingKind = Literal["bool", "choice", "number", "range", "text", "path", "list", "secret", "custom"]


class SettingChoice(BaseModel):
    value: str | int
    label: str


class SettingField(BaseModel):
    """One editable setting, as config.Settings describes it, for the Settings screen's
    labels, limits and search."""
    name: str
    section: SettingsSection | None = Field(description="None for one saved elsewhere (not a field on the page)")
    title: str
    help: str = ""
    kind: SettingKind
    advanced: bool = False
    controller_only: bool = Field(False, description="used by this machine only, not by remote workers")
    secret: bool = False
    nullable: bool = Field(False, description="empty saves null (unset), else the default")
    default: Any = None
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    choices: list[SettingChoice] = []
    placeholder: str = ""
    keywords: str = ""


# Types the API spells out more exactly than the settings do (settings.json keeps plain dicts).
_API_TYPES: dict[str, Any] = {
    "metadata_providers": dict[ProviderList, list[ProviderSlot]],
    "notify_events": dict[NotifyEvent, PushoverPriority | None],
    "transcode_sources": dict[TranscodeSourceKind, TranscodeSource],
}


def _plain(annotation: Any) -> tuple[Any, bool]:
    """A setting's type as the API sends it (paths and secrets as text), and whether it may be None."""
    args = get_args(annotation) if get_origin(annotation) in (Union, UnionType) else ()
    nullable = type(None) in args
    if nullable:
        rest = [a for a in args if a is not type(None)]
        annotation = reduce(operator.or_, rest)
    if annotation in (Path, SecretStr):
        annotation = str
    return annotation, nullable


def _limits(f: FieldInfo) -> list[Any]:
    return [m for m in f.metadata if not isinstance(m, UI)]


def _update_model() -> type[BaseModel]:
    """Every setting the UI may change, each optional: only what's sent is saved.
    A secret sent as "" is cleared, and a folder sent as "" goes back to its default."""
    fields: dict[str, Any] = {}
    for name, f in Settings.model_fields.items():
        if name not in EDITABLE or not ui_of(f).update:
            continue
        t, _ = _plain(f.annotation)
        t = _API_TYPES.get(name, t)
        u = ui_of(f)
        limits = [] if name in SECRETS or name in FOLDERS else _limits(f)
        if u.ge is not None or u.le is not None:
            limits = [Field(ge=u.ge, le=u.le)]
        fields[name] = (Annotated[(t, *limits)] | None if limits else t | None, None)
    return create_model("SettingsUpdate", __module__=__name__, __doc__=_update_model.__doc__,
                        __validators__={"blank_is_none": model_validator(mode="before")(_blank_is_none)}, **fields)


def _blank_is_none(values: Any) -> Any:
    """Text as typed, trimmed, and empty is None (unset, or the default; api.put_settings).
    Secrets and folders keep "", which clears them."""
    if not isinstance(values, dict):
        return values
    out = {}
    for k, v in values.items():
        if isinstance(v, str) and k not in SECRETS and k not in FOLDERS:
            v = v.strip() or None
        out[k] = v
    return out


class SettingsViewBase(BaseModel):
    """What SettingsView adds to the settings themselves."""
    providers: list[ProviderInfo] = []
    library_dir: str
    scratch_dir: str
    movies_dir: str = Field("", description="where films go (a separate folder, or <library>/Movies)")
    tv_dir: str = Field("", description="where TV goes (a separate folder, or <library>/TV)")
    disc_backups_dir: str = Field("", description="where full disc backups go")
    custom_movies_dir: str = Field("", description="the films folder as set, or empty for <library>/Movies")
    custom_tv_dir: str = Field("", description="the TV folder as set, or empty for <library>/TV")
    custom_disc_backups_dir: str = Field("", description="the disc backups folder as set, or empty for <library>/Disc backups")
    makemkv_drive_data: dict[str, bool] = Field(description="drive id -> has imported private data")
    makemkv_drive_dumps: dict[str, bool] = Field({}, description="drive id -> has a firmware dump (dump_*.tgz)")
    backups_dir: str = Field("", description="where the database backups go")
    vision_models: dict[str, str] = Field({}, description="vision provider -> the model it uses when none is set")


def _view_model() -> type[BaseModel]:
    """The settings as saved: every editable one, except a secret, which is only whether it's set
    (<name>_set). Folders are in SettingsViewBase."""
    fields: dict[str, Any] = {}
    for name, f in Settings.model_fields.items():
        if name not in EDITABLE or name in FOLDERS:
            continue
        if name in SECRETS:
            fields[f"{name}_set"] = (bool, False)
            continue
        t, nullable = _plain(f.annotation)
        t = _API_TYPES.get(name, t)
        fields[name] = (t | None if nullable else t, f.get_default(call_default_factory=True))
    return create_model("SettingsView", __base__=SettingsViewBase, __module__=__name__,
                        __doc__=_view_model.__doc__, **fields)


SettingsUpdate = _update_model()
SettingsView = _view_model()


# Settings that mean nothing on Windows: the render node VAAPI and Quick Sync open on Linux.
LINUX_ONLY_SETTINGS = {"vaapi_device"}


def setting_fields() -> list[SettingField]:
    from . import proc, transcode

    out = []
    for name, f in Settings.model_fields.items():
        u = ui_of(f)
        if not u or proc.WINDOWS and name in LINUX_ONLY_SETTINGS:
            continue
        t, nullable = _plain(f.annotation)
        limits = {k: getattr(m, k) for m in f.metadata for k in ("ge", "le") if hasattr(m, k)}
        limits |= {k: v for k, v in (("ge", u.ge), ("le", u.le)) if v is not None}
        choices = u.choices
        if not choices and get_origin(t) is Literal:
            choices = tuple((a, str(a)) for a in get_args(t))
        if name == "transcode_encoder" and proc.WINDOWS:
            choices = tuple((v, label.replace(", VAAPI", "")) for v, label in choices if transcode.available(v))
        secret = name in SECRETS
        kind = ("secret" if secret else u.kind or ("bool" if t is bool else "choice" if choices
                else "number" if t in (int, float) else "path" if name in FOLDERS else "list" if t == list[str]
                else "text" if t is str else "custom"))
        out.append(SettingField(
            name=name, section=u.section, title=u.title, help=u.help, kind=kind, advanced=u.advanced,
            controller_only=u.controller_only, secret=secret, nullable=nullable,
            default=None if secret else to_jsonable_python(f.get_default(call_default_factory=True)),
            minimum=limits.get("ge"), maximum=limits.get("le"), step=u.step or (1 if t is int else None),
            choices=[SettingChoice(value=v, label=label) for v, label in choices],
            placeholder=u.placeholder, keywords=u.keywords))
    return out


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
    show_key: str | None = Field(None, description="the show's key (\"tmdb:1399\", \"tvmaze:82\")")
    show_tmdb_id: int | None = Field(None, description="older clients: a TMDB id, in place of show_key")
    season: int | None = Field(None, ge=0)
    disc_number: int | None = Field(None, ge=1)
    start_episode: int | None = Field(None, ge=1)
    compilation: bool = False

    @model_validator(mode="after")
    def _show(self) -> TvProposeRequest:
        if self.show_key is None:
            if self.show_tmdb_id is None:
                raise ValueError("pick the show")
            self.show_key = f"tmdb:{self.show_tmdb_id}"
        parse_show_key(self.show_key)
        return self


class TvMatchTextRequest(BaseModel):
    text: str = Field(max_length=20000, description="episode names in disc order, one per line or comma separated")


class TitleMapUpdate(BaseModel):
    title_index: int
    role: TitleRole
    episodes: list[EpisodeRef] = []


class TvSplitRequest(BaseModel):
    cuts: list[int] | None = Field(None, description="marks (0-based) where an episode starts")
    cut_at: float | None = Field(None, gt=0, description="also cut here (s), or at a break within 10 s of it")
    off: bool | None = Field(None, description="true keeps the long title whole, false splits it again")
    title_index: int | None = Field(None, description="which long title (else the first)")


class TvConfirmRequest(BaseModel):
    mapping: list[TitleMapUpdate]


class MediaRequest(BaseModel):
    media: MediaKind


class SeasonCell(BaseModel):
    episode: Episode
    status: Literal["present", "missing", "duplicate", "gone"] = Field(
        description="gone: a disc in the collection has it, but its file isn't in the show's folder any more")
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


class WebSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=300)


class WebResult(BaseModel):
    """One page from a web search (SearXNG), for identifying a disc by hand."""

    title: str
    url: str
    snippet: str = ""
    suggestion: str | None = Field(None, description="the film or show name in the page title, to search TMDB for")


class LabelReading(BaseModel):
    """What the AI model made of a cryptic disc label, from the button in Review."""

    titles: list[str] = Field(description="full titles the label may stand for, most likely first")
    kind: Literal["movie", "tv"] | None = None
    model: str
    seconds: float
    candidates: list[Candidate] = Field(default=[], description="a film disc's candidates, ranked again with "
                                                                "these titles")


class TitleSnapshots(BaseModel):
    """Frames from one of the disc's titles, from its file in scratch."""

    title_index: int
    duration_s: int
    ripped: float = Field(0.0, description="how much of the title is in scratch, 0..1")
    frames: list[int] = Field(default=[], description="frame numbers ready to fetch (more appear as the rip goes on)")
    at_s: list[int] = Field(default=[], description="where each frame is in the title, in seconds")


class Snapshots(BaseModel):
    titles: list[TitleSnapshots] = []
    note: str | None = Field(None, description="why there are no frames (yet)")


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
    encoder: Literal["qsv", "vaapi", "nvenc", "x265"] = Field(description="what ripshelf transcodes with now")
    encoder_note: str
    checked_at: float
    windows: bool = Field(False, description="GPUs aren't listed on Windows; the encoders' test encodes tell")


class Status(BaseModel):
    version: str
    makemkv_version: str | None = None
    fake_hardware: bool
    problems: list[SetupProblem]
    review_count: int
    web_search: bool = Field(False, description="a SearXNG address is set, so Review can search the web")
    label_ai: bool = Field(False, description="an AI model can read labels, so Review shows its button")


class Tool(BaseModel):
    found: bool
    path: str | None = Field(None, description="where it is, if found")


class SetupTools(BaseModel):
    """The programs ripshelf runs, for the setup page."""
    makemkv: Tool
    makemkv_registered: bool = Field(description="a MakeMKV key is set here (or, on Windows, in MakeMKV itself)")
    ffmpeg: Tool
    ffprobe: Tool
    mkvmerge: Tool | None = Field(None, description="MKVToolNix, which splits a TV title holding several episodes")
    java: Tool = Field(description="a Java runtime, which MakeMKV needs for some Blu-ray menus (BD-J)")
    tesseract: Tool | None = Field(None, description="Tesseract, which reads the text on cover scans")
    zbar: Tool | None = Field(None, description="zbar, which reads the barcode on cover scans (the pyzbar "
                                                 "package; on Windows it needs the Visual C++ 2013 runtime)")
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
    delete_files: bool = Field(False, description="also delete the files the jobs put in the library (a "
                                                  "full disc backup's are kept)")


class FinishedPage(BaseModel):
    """Finished, failed and cancelled jobs, newest first, for paging back through the history."""
    jobs: list[Job]
    older: int = Field(description="finished jobs older than these (or than ``before``, if there are none)")


class DeleteJobsResult(BaseModel):
    deleted: list[int]
    files_deleted: list[str] = Field(default=[], description="library files deleted with them")


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


PendingScans.model_rebuild()
