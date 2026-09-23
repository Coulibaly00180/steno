from datetime import datetime, timezone
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Table, Text, UniqueConstraint, false, func, text
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


class Speaker(Base):
    """A voice found in the video (n°8); `position` gives « Intervenant 1, 2… » until renamed."""

    __tablename__ = "speakers"
    __table_args__ = (UniqueConstraint("video_id", "position", name="uq_speakers_video_position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str | None] = mapped_column(String(80), nullable=True)

    @property
    def label(self) -> str:
        return self.name or f"Intervenant {self.position}"


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VideoChatMessage(Base):
    __tablename__ = "video_chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GlossaryTerm(Base):
    """Global glossary (F-11.9): one row per term, in the order typed."""

    __tablename__ = "glossary_terms"
    __table_args__ = (Index("uq_glossary_terms_term_lower", func.lower(text("term")), unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    term: Mapped[str] = mapped_column(String(60))
    position: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
