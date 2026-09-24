import json
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TemplateCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    prompt: str = Field(min_length=1)


class TemplateOut(TemplateCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    is_default: bool
    created_at: datetime
    updated_at: datetime | None = None


class GlossaryIn(BaseModel):
    terms: list[str]


class GlossaryOut(BaseModel):
    terms: list[str]


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    video_id: str
    stage: str
    status: str
    progress: int
    error: str | None
    summary_length: str = "standard"
    kind: str = "FULL"
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    # Active jobs only (n°13): 0 = running, n = n-th in the queue; seconds until
    # this job ends, None until a job of the same kind has completed once.
    queue_position: int | None = None
    estimated_seconds_remaining: float | None = None


class SegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    start_seconds: float
    end_seconds: float
    text: str
    speaker_id: int | None = None
    # Doubtful words (n°1): [start, end, probability %] in `text`.
    doubts: list[list[int]] = []

    @field_validator("doubts", mode="before")
    @classmethod
    def _stored_json(cls, value):
        """Stored as JSON text (TranscriptSegment.doubts), or absent."""
        if not value:
            return []
        return json.loads(value) if isinstance(value, str) else value


class SpeakerOut(BaseModel):
    id: int
    position: int
    name: str | None
    label: str
    seconds: float
    share: float


class SpeakerRenameIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, max_length=80)


class SpeakerMergeIn(BaseModel):
    into: int


class DiarizeIn(BaseModel):
    num_speakers: int | None = Field(default=None, ge=1, le=20)


class SummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    template_id: str | None = None
    # None when the template has been deleted since (R-6).
    template_name: str | None = None
    summary_length: str | None = None
    language: str | None
    content_markdown: str
    model: str
    created_at: datetime
    edited_at: datetime | None = None


class ChapterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    start_seconds: float
    title: str


class VideoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    original_filename: str
    duration_seconds: float
    size_bytes: int
    status: str
    detected_language: str | None
    target_language: str | None
    created_at: datetime


class SnippetOut(BaseModel):
    """Passage around the words a library search matched (n°17)."""

    text: str
    # [start, end) character ranges of the matched words in `text`.
    ranges: list[list[int]]
    source: str  # "transcript" or "translation"
    start_seconds: float | None = None


class VideoListItem(VideoOut):
    tags: list[str] = []
    snippet: SnippetOut | None = None
    job: JobOut | None = None


class TagsIn(BaseModel):
    tags: list[str] = Field(max_length=100)


class TagsOut(BaseModel):
    tags: list[str]


class TagCount(BaseModel):
    name: str
    count: int


class VideoDetail(VideoOut):
    transcript_text: str | None
    translated_text: str | None
    source_language_forced: bool = False
    vocabulary: list[str] = []
    glossary_snapshot: list[str] = []
    # Effective vocabulary sizes actually sent to Whisper and to the LLM (R-9).
    whisper_terms_count: int = 0
    llm_terms_count: int = 0
    # "video" or "audio" source; the extracted WAV serves as a fallback track.
    media_kind: str = "video"
    source_available: bool = False
    audio_available: bool = False
    chapters: list[ChapterOut] = []
    transcript_edited_at: datetime | None = None
    translated_at: datetime | None = None
    # Corrections made after the latest summary / translation.
    summary_outdated: bool = False
    translation_outdated: bool = False
    tags: list[str] = []
    # How the chat reads this video: "full" transcript, "passages" (semantic
    # search, n°6), "partial" (long transcript not indexed yet: its start only).
    chat_mode: str = "none"
    # Speaker identification (n°8).
    diarize: bool = False
    num_speakers: int | None = None
    diarization_error: str | None = None
    # What happens to the media once processed (n°14): keep, audio, delete.
    source_policy: str = "keep"
    # Link the media was downloaded from (n°12).
    source_url: str | None = None
    speakers: list[SpeakerOut] = []
    segments: list[SegmentOut]
    summaries: list[SummaryOut]
    job: JobOut | None = None


class ChatQuestion(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=4000)


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    video_id: str
    role: str
    content: str
    interrupted: bool = False
    feedback: int | None = None
    created_at: datetime


class LibraryQuestion(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=4000)
    # A new conversation names its videos; a follow-up uses the ones it started with.
    video_ids: list[str] = Field(default=[], max_length=500)
    conversation_id: str | None = Field(default=None, max_length=36)
    scope: str | None = Field(default=None, max_length=200)


class LibraryConversationOut(BaseModel):
    id: str
    title: str
    scope: str | None
    video_count: int
    created_at: datetime
    updated_at: datetime
    # An answer of the conversation was rated wrong (n°18).
    flagged: bool = False


class LibraryMessageOut(BaseModel):
    id: str
    role: str
    content: str
    sources: list[dict] = []
    interrupted: bool = False
    feedback: int | None = None
    created_at: datetime


class LibraryConversationDetail(LibraryConversationOut):
    video_ids: list[str]
    messages: list[LibraryMessageOut]


class RegenerateIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_id: str | None = Field(default=None, max_length=36)
    summary_length: str = "standard"
    custom_prompt: str | None = Field(default=None, max_length=2000)


class SummaryEditIn(BaseModel):
    content_markdown: str = Field(min_length=1, max_length=50000)


class SegmentEditIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    text: str | None = Field(default=None, min_length=1, max_length=2000)
    # Set (even to null) only when the request names it: see model_fields_set.
    speaker_id: int | None = None


class ReplaceIn(BaseModel):
    find: str = Field(min_length=1, max_length=200)
    replace: str = Field(default="", max_length=200)
    match_case: bool = False
    whole_word: bool = False


class ReplaceOut(BaseModel):
    replaced: int
    segments: int


class GlossarySuggestionOut(BaseModel):
    term: str
    variants: list[str]
    occurrences: int
    videos: int


class GlossaryTermIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    term: str = Field(min_length=1, max_length=60)


class WatchFolderSettings(BaseModel):
    """Defaults applied to the files dropped in the watched folder (n°9)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    enabled: bool = False
    target_language: str | None = Field(default=None, max_length=32)
    template_id: str | None = Field(default=None, max_length=36)
    summary_length: str = "standard"
    source_language: str | None = Field(default=None, max_length=8)
    use_global_glossary: bool = True
    diarize: bool = False
    num_speakers: int | None = Field(default=None, ge=1, le=20)
    source_policy: str = "keep"
    tag: str | None = Field(default=None, max_length=40)


class BackupSettings(BaseModel):
    """Scheduled database backups (n°13)."""

    enabled: bool = True
    interval_hours: int = Field(default=24, ge=1, le=168)
    keep: int = Field(default=7, ge=1, le=100)


class StorageActionIn(BaseModel):
    # "audio": compact audio only; "delete_media": text only; "delete_work_audio": the extracted WAV.
    action: str = Field(max_length=32)


class ImportOptionsIn(BaseModel):
    """The import form's options, as JSON (recordings, links)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    target_language: str | None = Field(default=None, max_length=32)
    template_id: str | None = Field(default=None, max_length=36)
    custom_prompt: str | None = Field(default=None, max_length=2000)
    summary_length: str | None = Field(default=None, max_length=16)
    source_language: str | None = Field(default=None, max_length=8)
    vocabulary: str | None = Field(default=None, max_length=1000)
    use_global_glossary: bool = True
    diarize: bool = False
    num_speakers: int | None = None
    source_policy: str | None = Field(default=None, max_length=16)


class RecordingCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    mime_type: str = Field(min_length=1, max_length=80)
    live: bool = False
    language: str | None = Field(default=None, max_length=8)


class RecordingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    status: str
    live: bool
    mime_type: str
    size_bytes: int
    chunks: int
    language: str | None
    video_id: str | None
    live_error: str | None
    created_at: datetime
    updated_at: datetime


class UrlPreviewIn(BaseModel):
    url: str = Field(min_length=1, max_length=2000)


class UrlImportIn(ImportOptionsIn):
    url: str = Field(min_length=1, max_length=2000)
    title: str | None = Field(default=None, max_length=200)


class UrlImportSettings(BaseModel):
    """Imports from a link (n°12): video platforms are the user's choice, off by default."""

    platforms: bool = False


class ModelSettings(BaseModel):
    """Models chosen in the interface (n°20); None keeps the environment's (LLM_MODEL, WHISPER_MODEL)."""

    llm_model: str | None = Field(default=None, max_length=120)
    whisper_model: str | None = Field(default=None, max_length=60)


class ModelPullIn(BaseModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9][\w.\-/:]*$")


class BenchmarkIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class FeedbackIn(BaseModel):
    # 1: useful, -1: wrong, None: no opinion.
    value: int | None = Field(default=None, ge=-1, le=1)


class ConversationRenameIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=120)


class SavedSearchIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    query: str = Field(default="", max_length=2000)


class EntityUpdateIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=120)
    hidden: bool | None = None


class EntityMergeIn(BaseModel):
    into: int


class ActionIn(BaseModel):
    """An action or decision (n°5), created by hand or edited: only the fields sent change."""

    model_config = ConfigDict(str_strip_whitespace=True)

    kind: Literal["action", "decision"] | None = None
    text: str | None = Field(default=None, max_length=400)
    owner: str | None = Field(default=None, max_length=80)
    due_text: str | None = Field(default=None, max_length=80)
    due_date: date | None = None
    status: Literal["open", "done", "dropped"] | None = None
    start_seconds: float | None = Field(default=None, ge=0)


class ClipIn(BaseModel):
    """A passage to cut out (n°7)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str | None = Field(default=None, max_length=200)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    subtitles: Literal["none", "track", "burned"] = "none"
    subtitle_source: Literal["original", "translation"] = "original"


class SeriesIn(BaseModel):
    """A series of meetings (n°6), with the meetings to put in it at creation."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    video_ids: list[str] = Field(default_factory=list, max_length=500)


class SeriesRenameIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)


class VideoSeriesIn(BaseModel):
    """The series of a meeting; None takes it out of its series."""

    series_id: str | None = None


class QualitySettings(BaseModel):
    """Quality runs on the reference corpus (n°4): replayed by themselves when a prompt or a model changes."""

    auto: bool = True


class QualityRunIn(BaseModel):
    scope: Literal["quick", "full"] = "quick"


class AccessSettings(BaseModel):
    """Access control (n°15), never sent to the browser: the password's scrypt hash and the session key.

    `version` grows at each password change: the sessions opened before become invalid.
    `require_local`: ask the password on this computer too (otherwise only from the network).
    """

    password_hash: str | None = None
    secret: str | None = None
    version: int = 0
    require_local: bool = False


class LoginIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class PasswordIn(BaseModel):
    """Set, change or remove (new_password None) the password; the current one is asked when there is one."""

    current_password: str | None = Field(default=None, max_length=200)
    new_password: str | None = Field(default=None, min_length=8, max_length=200)
    require_local: bool | None = None


class OnboardingSettings(BaseModel):
    """First-launch assistant (n°19): shown until finished or skipped."""

    done: bool = False
