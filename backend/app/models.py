from datetime import date, datetime, timezone
from sqlalchemy import BigInteger, Boolean, Column, Date, DateTime, Float, ForeignKey, Index, Integer, String, Table, Text, UniqueConstraint, false, func, text
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def utcnow():
    return datetime.now(timezone.utc)


video_tags = Table(
    "video_tags",
    Base.metadata,
    Column("video_id", ForeignKey("videos.id", ondelete="CASCADE", name="fk_video_tags_video_id"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE", name="fk_video_tags_tag_id"), primary_key=True),
    Index("ix_video_tags_tag_id", "tag_id"),
)


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    original_filename: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(Text)
    duration_seconds: Mapped[float] = mapped_column(Float)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), default="UPLOADED")
    detected_language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    transcript_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    translated_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # True when the user forced the spoken language (then stored in detected_language).
    source_language_forced: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Video-specific terms and the global glossary frozen at import, one term per line.
    vocabulary: Mapped[str | None] = mapped_column(Text, nullable=True)
    glossary_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Last manual correction of the transcript, and when the stored translation was made:
    # a summary or translation older than the correction is stale.
    transcript_edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    translated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Block summaries and chapters of the last run, reused by a regeneration of
    # the same text at the same detail level (JSON, see app.summarize).
    summary_cache: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Speaker identification (n°8): asked at import, with an optional number of
    # speakers; the error is kept when it failed (the video is still processed).
    diarize: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    num_speakers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    diarization_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "sides" (feuille de route n° 3, phase 4): a recording with your microphone on
    # the left channel and the other side's sound on the right (app.sides).
    audio_layout: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # What happens to the media once processed (n°14): "keep", "audio" (compact
    # audio only) or "delete" (text only). See app.storage.
    source_policy: Mapped[str] = mapped_column(String(16), default="keep", server_default="keep")
    # Link the media was downloaded from (n°12): the worker fetches it first.
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Recurring meetings (n°6): the series, and « what changed since last time » (JSON cache, see app.series).
    series_id: Mapped[str | None] = mapped_column(
        ForeignKey("meeting_series.id", ondelete="SET NULL", name="fk_videos_series_id"), nullable=True, index=True
    )
    series_changes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Explicit ordering: PostgreSQL returns rows in no guaranteed order, and
    # callers rely on ``summaries[-1]`` being the most recent summary.
    segments: Mapped[list["TranscriptSegment"]] = relationship(
        cascade="all, delete-orphan",
        order_by="(TranscriptSegment.start_seconds, TranscriptSegment.id)",
    )
    summaries: Mapped[list["Summary"]] = relationship(
        cascade="all, delete-orphan",
        order_by="Summary.created_at",
    )
    chat_messages: Mapped[list["VideoChatMessage"]] = relationship(
        cascade="all, delete-orphan",
        order_by="VideoChatMessage.created_at",
    )
    chapters: Mapped[list["Chapter"]] = relationship(
        cascade="all, delete-orphan",
        order_by="Chapter.start_seconds",
    )
    tags: Mapped[list["Tag"]] = relationship(secondary=video_tags, order_by="Tag.name")
    speakers: Mapped[list["Speaker"]] = relationship(cascade="all, delete-orphan", order_by="Speaker.position")


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(64), default="QUEUED")
    status: Mapped[str] = mapped_column(String(32), default="QUEUED")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    template_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    custom_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_length: Mapped[str] = mapped_column(String(16), default="standard", server_default="standard")
    # FULL: transcription and summary; SUMMARY: new summary of an existing transcript.
    kind: Mapped[str] = mapped_column(String(16), default="FULL", server_default="FULL")
    rq_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Each stage reached and when, as JSON [[stage, epoch seconds], ...] (performance tracking).
    stage_times: Mapped[str | None] = mapped_column(Text, nullable=True)


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    start_seconds: Mapped[float] = mapped_column(Float)
    end_seconds: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    speaker_id: Mapped[int | None] = mapped_column(
        ForeignKey("speakers.id", ondelete="SET NULL", name="fk_transcript_segments_speaker_id"), nullable=True, index=True
    )
    # Words Whisper was unsure of (n°1): JSON [[start, end, probability %], …] in `text`;
    # cleared when the line is corrected by hand.
    doubts: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "you" or "others" in a two-sided recording (app.sides): the track the line was heard on.
    side: Mapped[str | None] = mapped_column(String(8), nullable=True)


class Speaker(Base):
    """A voice found in the video (n°8); `position` gives « Intervenant 1, 2… » until renamed.

    In a two-sided recording, `side` is "you" or "others" and `side_position`
    numbers the voices of that side (« Vous », « Vous 2 », « Participant 1 »…);
    no `side_position`: the whole side, not split (« Vous », « Participants »).
    """

    __tablename__ = "speakers"
    __table_args__ = (UniqueConstraint("video_id", "position", name="uq_speakers_video_position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    side: Mapped[str | None] = mapped_column(String(8), nullable=True)
    side_position: Mapped[int | None] = mapped_column(Integer, nullable=True)

    @property
    def label(self) -> str:
        if self.name:
            return self.name
        if self.side == "you":
            return "Vous" if (self.side_position or 1) == 1 else f"Vous {self.side_position}"
        if self.side == "others":
            return f"Participant {self.side_position}" if self.side_position else "Participants"
        return f"Intervenant {self.position}"


class SummaryTemplate(Base):
    __tablename__ = "summary_templates"
    __table_args__ = (
        # At most one default template (R-3); the API keeps exactly one.
        Index(
            "uq_summary_templates_single_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default"),
            sqlite_where=text("is_default"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt: Mapped[str] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, onupdate=utcnow)


class Summary(Base):
    __tablename__ = "summaries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    template_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    summary_length: Mapped[str | None] = mapped_column(String(16), nullable=True)
    content_markdown: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(120))
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The source passage of each line (n°3), cached with the text and index it was computed for.
    sources: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VideoChatMessage(Base):
    __tablename__ = "video_chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    # An answer cut short (the reader left, or the model stopped): what was written is kept.
    interrupted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # The reader's thumb on an answer (n°18): 1 useful, -1 wrong, None not rated.
    feedback: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LibraryConversation(Base):
    """A conversation with several videos at once (n°19), kept like the per-video chat."""

    __tablename__ = "library_conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    # Human-readable scope ("tag « Finance »") and the videos it covers (JSON list of ids).
    scope: Mapped[str | None] = mapped_column(String(200), nullable=True)
    video_ids: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    messages: Mapped[list["LibraryMessage"]] = relationship(
        cascade="all, delete-orphan", order_by="LibraryMessage.created_at"
    )


class LibraryMessage(Base):
    __tablename__ = "library_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("library_conversations.id", ondelete="CASCADE", name="fk_library_messages_conversation_id"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    # Passages cited by an answer (JSON): [n] in the text opens the video at that moment.
    sources: Mapped[str | None] = mapped_column(Text, nullable=True)
    interrupted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    feedback: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GlossaryTerm(Base):
    """Global glossary (F-11.9): one row per term, in the order typed."""

    __tablename__ = "glossary_terms"
    __table_args__ = (Index("uq_glossary_terms_term_lower", func.lower(text("term")), unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    term: Mapped[str] = mapped_column(String(60))
    position: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TermCorrection(Base):
    """A misrecognition fixed by hand (n°2): the glossary learns from repeated ones.

    Kept when the video is deleted: what was learnt stays useful.
    """

    __tablename__ = "term_corrections"
    __table_args__ = (Index("ix_term_corrections_term_lower", func.lower(text("term"))),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str | None] = mapped_column(
        ForeignKey("videos.id", ondelete="SET NULL", name="fk_term_corrections_video_id"), nullable=True
    )
    misheard: Mapped[str] = mapped_column(String(120))
    term: Mapped[str] = mapped_column(String(60))
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GlossaryDismissal(Base):
    """A suggested term the user declined: never suggested again."""

    __tablename__ = "glossary_dismissals"
    __table_args__ = (Index("uq_glossary_dismissals_term_lower", func.lower(text("term")), unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    term: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Recording(Base):
    """A recording made in the browser (n°10), uploaded chunk by chunk while it runs.

    RECORDING until the user stops it; then FINISHED (imported as `video_id`)
    or CANCELLED. `live` asks the live service for a running transcript (n°11).
    """

    __tablename__ = "recordings"
    __table_args__ = (Index("ix_recordings_status", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="RECORDING")
    live: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Microphone on the left channel, the tab's or the system's sound on the right (phase 4).
    sides: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    mime_type: Mapped[str] = mapped_column(String(80))
    path: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    chunks: Mapped[int] = mapped_column(Integer, default=0)
    # Language forced for the live transcript, when the user knows it.
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    video_id: Mapped[str | None] = mapped_column(
        ForeignKey("videos.id", ondelete="SET NULL", name="fk_recordings_video_id"), nullable=True
    )
    live_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    live_segments: Mapped[list["LiveSegment"]] = relationship(
        cascade="all, delete-orphan", order_by="LiveSegment.start_seconds"
    )


class LiveSegment(Base):
    """A line of the live transcript: a preview, replaced by the full transcription at the end."""

    __tablename__ = "live_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recording_id: Mapped[str] = mapped_column(
        ForeignKey("recordings.id", ondelete="CASCADE", name="fk_live_segments_recording_id"), index=True
    )
    start_seconds: Mapped[float] = mapped_column(Float)
    end_seconds: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)


class ActionItem(Base):
    """An action or a decision of a video (n°5): found in its summary, or added by hand.

    `edited`: changed by the user, so a new summary never replaces it.
    """

    __tablename__ = "action_items"
    __table_args__ = (Index("ix_action_items_status_due", "status", "due_date"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    video_id: Mapped[str] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE", name="fk_action_items_video_id"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(String(400))
    owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    due_text: Mapped[str | None] = mapped_column(String(80), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open", server_default="open")
    start_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="auto", server_default="auto")
    edited: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MeetingSeries(Base):
    """Recurring meetings grouped together (n°6): the weekly committee, the project review…"""

    __tablename__ = "meeting_series"
    __table_args__ = (Index("uq_meeting_series_name_lower", func.lower(text("name")), unique=True),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VideoClip(Base):
    """A passage cut out of a video to share it (n°7), rendered by a CLIP job.

    `subtitles`: none, track (a subtitle track players can show) or burned (drawn on the picture).
    """

    __tablename__ = "video_clips"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    video_id: Mapped[str] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE", name="fk_video_clips_video_id"), index=True
    )
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    start_seconds: Mapped[float] = mapped_column(Float)
    end_seconds: Mapped[float] = mapped_column(Float)
    subtitles: Mapped[str] = mapped_column(String(16), default="none", server_default="none")
    subtitle_source: Mapped[str] = mapped_column(String(16), default="original", server_default="original")
    status: Mapped[str] = mapped_column(String(16), default="QUEUED", server_default="QUEUED")
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class QualityRun(Base):
    """A replay of the reference corpus (n°4): which models and prompts, and the scores obtained.

    `results`: JSON, one entry per corpus item (coverage, length, chapters, timings, summary).
    """

    __tablename__ = "quality_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    scope: Mapped[str] = mapped_column(String(16))
    trigger: Mapped[str] = mapped_column(String(16))
    llm_model: Mapped[str] = mapped_column(String(120))
    whisper_model: Mapped[str] = mapped_column(String(120))
    prompt_version: Mapped[str] = mapped_column(String(16))
    fingerprint: Mapped[str] = mapped_column(String(64))
    progress: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    current: Mapped[str | None] = mapped_column(String(200), nullable=True)
    results: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    rq_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Entity(Base):
    """A person, organisation, place or date named in the library (n°16).

    `key` is the folded name (no accents, case, titles): two spellings of one
    name meet there. `hidden`: dismissed by the user, still extracted but not listed.
    """

    __tablename__ = "entities"
    __table_args__ = (UniqueConstraint("kind", "key", name="uq_entities_kind_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(120))
    key: Mapped[str] = mapped_column(String(120))
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Merged by the user: new mentions of this spelling go to that entity.
    merged_into: Mapped[int | None] = mapped_column(
        ForeignKey("entities.id", ondelete="SET NULL", name="fk_entities_merged_into"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EntityMention(Base):
    """Where an entity is named: a video, a moment, the line said there."""

    __tablename__ = "entity_mentions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE", name="fk_entity_mentions_entity_id"), index=True
    )
    video_id: Mapped[str] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE", name="fk_entity_mentions_video_id"), index=True
    )
    start_seconds: Mapped[float] = mapped_column(Float)
    context: Mapped[str] = mapped_column(Text)


class VideoEntityState(Base):
    """Extraction state of a video: READY, STALE (transcript edited since) or FAILED."""

    __tablename__ = "video_entity_states"

    video_id: Mapped[str] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE", name="fk_video_entity_states_video_id"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(120))
    transcript_hash: Mapped[str] = mapped_column(String(64))
    mentions: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SavedSearch(Base):
    """A library search kept as a collection (n°17): its filters, as a query string."""

    __tablename__ = "saved_searches"
    __table_args__ = (Index("uq_saved_searches_name_lower", func.lower(text("name")), unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80))
    query: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppSetting(Base):
    """Settings changed from the interface (watched folder, backups), as JSON per section."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Chapter(Base):
    """Thematic section of a video, produced with the block summaries."""

    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    start_seconds: Mapped[float] = mapped_column(Float)
    title: Mapped[str] = mapped_column(String(200))


class Tag(Base):
    """Free label of the library (n°17); names are unique regardless of case."""

    __tablename__ = "tags"
    __table_args__ = (Index("uq_tags_name_lower", func.lower(text("name")), unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobDuration(Base):
    """How long a completed job took, kept after its video is deleted (n°13 estimates)."""

    __tablename__ = "job_durations"
    __table_args__ = (Index("ix_job_durations_kind_finished_at", "kind", "finished_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16))
    media_seconds: Mapped[float] = mapped_column(Float)
    elapsed_seconds: Mapped[float] = mapped_column(Float)
    translated: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Seconds per stage (JSON {stage: seconds}) and the wait in the queue before it started.
    stages: Mapped[str | None] = mapped_column(Text, nullable=True)
    queued_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)


class Passage(Base):
    """A stretch of transcript (~1 min) and its embedding, for the semantic search (n°6, n°19)."""

    __tablename__ = "passages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    start_seconds: Mapped[float] = mapped_column(Float)
    end_seconds: Mapped[float] = mapped_column(Float)
    # "[hh:mm:ss] text" lines, as given to the LLM.
    text: Mapped[str] = mapped_column(Text)
    # No fixed dimension: another embedding model only needs a re-index. Exact
    # search, no ANN index: a whole library is a few tens of thousands of rows.
    embedding: Mapped[list[float]] = mapped_column(Vector())


class VideoIndex(Base):
    """State of a video's passages: READY, STALE (transcript edited since) or FAILED."""

    __tablename__ = "video_indexes"

    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(120))
    transcript_hash: Mapped[str] = mapped_column(String(64))
    passages: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
