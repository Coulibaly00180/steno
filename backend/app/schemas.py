from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


class VideoListItem(VideoOut):
    tags: list[str] = []
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
    created_at: datetime


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class LibraryQuestion(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=4000)
    video_ids: list[str] = Field(min_length=1, max_length=500)
    history: list[HistoryMessage] = Field(default=[], max_length=12)


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
