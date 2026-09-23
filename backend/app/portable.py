"""Portable library archive (n°13): move a library to another Sténo, or merge two.

Unlike a database backup (which replaces everything), an archive is imported
into the current library: videos already present (same id) are skipped. It is
an uncompressed tar, streamed without temporary file (media are already
compressed):

    steno-library.json          manifest: format, version, date, counts
    glossary.json               {"terms": [...]}
    templates.json              [{name, description, prompt}]
    videos/<id>/video.json      one processed video, its text and metadata
    videos/<id>/<source file>   its media, when asked for
    conversations.json          saved questions on several videos

Derived data is not exported: the semantic index, the exports and the summary
cache are rebuilt after the import.
"""
import json
import logging
import os
import re
import sys
import tarfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from sqlalchemy import func, select

from .analysis_options import GLOSSARY_MAX_TERMS
from .config import settings
from .db import SessionLocal
from .exports import write_exports
from .models import (
    Chapter,
    GlossaryTerm,
    LibraryConversation,
    LibraryMessage,
    Speaker,
    Summary,
    SummaryTemplate,
    Tag,
    TranscriptSegment,
    Video,
    VideoChatMessage,
)
from .schema import head_revision
from .storage import AUDIO_SUFFIXES, VIDEO_SUFFIXES

logger = logging.getLogger(__name__)

FORMAT = "steno-library"
VERSION = 1
MANIFEST = "steno-library.json"
CHUNK = 1024 * 1024
_BLOCK = 512
_VIDEO_ID = re.compile(r"^[A-Za-z0-9-]{1,36}$")
_VIDEO_MEMBER = re.compile(r"^videos/([A-Za-z0-9-]{1,36})/video\.json$")


class ArchiveError(ValueError):
    """An archive that cannot be imported, with a message for the user."""


# --- Export ---------------------------------------------------------------------------------

def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def _header(name: str, size: int) -> bytes:
    info = tarfile.TarInfo(name)
    info.size, info.mtime, info.mode = size, int(time.time()), 0o644
    return info.tobuf(format=tarfile.PAX_FORMAT, encoding="utf-8")


def _padding(size: int) -> bytes:
    return b"\0" * (-size % _BLOCK)


def _json_member(name: str, value) -> bytes:
    data = json.dumps(value, ensure_ascii=False, indent=1).encode("utf-8")
    return _header(name, len(data)) + data + _padding(len(data))


def _file_member(name: str, path: Path) -> Iterator[bytes]:
    size = path.stat().st_size
    yield _header(name, size)
    sent = 0
    with path.open("rb") as source:
        while sent < size:
            chunk = source.read(min(CHUNK, size - sent))
            if not chunk:
                break
            sent += len(chunk)
            yield chunk
    if sent < size:  # the file shrank meanwhile: the header announced `size` bytes
        yield b"\0" * (size - sent)
    yield _padding(size)


def video_document(video: Video, template_names: dict[str, str]) -> dict:
    positions = {speaker.id: speaker.position for speaker in video.speakers}
    source = Path(video.path)
    return {
        "id": video.id,
        "original_filename": video.original_filename,
        "duration_seconds": video.duration_seconds,
        "size_bytes": video.size_bytes,
        "detected_language": video.detected_language,
        "target_language": video.target_language,
        "source_language_forced": video.source_language_forced,
        "transcript_text": video.transcript_text,
        "translated_text": video.translated_text,
        "vocabulary": video.vocabulary,
        "glossary_snapshot": video.glossary_snapshot,
        "transcript_edited_at": _iso(video.transcript_edited_at),
        "translated_at": _iso(video.translated_at),
        "diarize": video.diarize,
        "num_speakers": video.num_speakers,
        "diarization_error": video.diarization_error,
        "created_at": _iso(video.created_at),
        "media_suffix": source.suffix.lower(),
        "speakers": [{"position": s.position, "name": s.name} for s in video.speakers],
        "segments": [
            {"start": s.start_seconds, "end": s.end_seconds, "text": s.text, "speaker": positions.get(s.speaker_id)}
            for s in video.segments
        ],
        "summaries": [
            {
                "id": s.id, "template_name": template_names.get(s.template_id or ""), "language": s.language,
                "summary_length": s.summary_length, "content_markdown": s.content_markdown, "model": s.model,
                "edited_at": _iso(s.edited_at), "created_at": _iso(s.created_at),
            }
            for s in video.summaries
        ],
        "chapters": [{"start": c.start_seconds, "title": c.title} for c in video.chapters],
        "tags": [tag.name for tag in video.tags],
        "chat": [
            {"id": m.id, "role": m.role, "content": m.content, "interrupted": m.interrupted, "created_at": _iso(m.created_at)}
            for m in video.chat_messages
        ],
    }


def exportable_video_ids() -> list[str]:
    """Processed videos only: the others have no result to carry over."""
    with SessionLocal() as db:
        return list(db.scalars(select(Video.id).where(Video.status == "COMPLETED").order_by(Video.created_at)))


def export_stream(include_media: bool) -> Iterator[bytes]:
    """The archive, piece by piece: one video in memory at a time, media read in chunks."""
    video_ids = exportable_video_ids()
    with SessionLocal() as db:
        templates = list(db.scalars(select(SummaryTemplate).order_by(SummaryTemplate.name)))
        template_names = {template.id: template.name for template in templates}
        glossary = list(db.scalars(select(GlossaryTerm.term).order_by(GlossaryTerm.position)))
        yield _json_member(MANIFEST, {
            "format": FORMAT, "version": VERSION, "created_at": _iso(datetime.now(timezone.utc)),
            "schema_revision": head_revision(), "videos": len(video_ids), "media": include_media,
        })
        yield _json_member("glossary.json", {"terms": glossary})
        yield _json_member("templates.json", [
            {"name": t.name, "description": t.description, "prompt": t.prompt} for t in templates
        ])
    for video_id in video_ids:
        with SessionLocal() as db:
            video = db.get(Video, video_id)
            if video is None:  # deleted during the export
                continue
            document = video_document(video, template_names)
            source = Path(video.path)
        yield _json_member(f"videos/{video_id}/video.json", document)
        if include_media and source.is_file():
            yield from _file_member(f"videos/{video_id}/source{source.suffix.lower()}", source)
    with SessionLocal() as db:
        conversations = [
            {
                "id": c.id, "title": c.title, "scope": c.scope, "video_ids": json.loads(c.video_ids),
                "created_at": _iso(c.created_at), "updated_at": _iso(c.updated_at),
                "messages": [
                    {"role": m.role, "content": m.content, "sources": json.loads(m.sources) if m.sources else [],
                     "interrupted": m.interrupted, "created_at": _iso(m.created_at)}
                    for m in c.messages
                ],
            }
            for c in db.scalars(select(LibraryConversation).order_by(LibraryConversation.created_at))
        ]
    yield _json_member("conversations.json", conversations)
    yield b"\0" * (2 * _BLOCK)


def export_filename() -> str:
    return f"steno-bibliotheque-{datetime.now(timezone.utc):%Y%m%d-%H%M}.tar"


# --- Import ---------------------------------------------------------------------------------

def _date(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _read_json(archive: tarfile.TarFile, member: tarfile.TarInfo):
    handle = archive.extractfile(member)
    if handle is None:
        raise ArchiveError(f"Élément illisible : {member.name}")
    try:
        return json.loads(handle.read().decode("utf-8"))
    except ValueError as exc:
        raise ArchiveError(f"Élément invalide : {member.name}") from exc


def _copy_media(archive: tarfile.TarFile, member: tarfile.TarInfo, destination: Path) -> None:
    handle = archive.extractfile(member)
    if handle is None:
        raise ArchiveError(f"Média illisible : {member.name}")
    temporary = destination.with_name(f".{destination.name}.import")
    try:
        with temporary.open("wb") as out:
            while chunk := handle.read(CHUNK):
                out.write(chunk)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _merge_glossary(db, terms: list[str]) -> int:
    current = list(db.scalars(select(GlossaryTerm).order_by(GlossaryTerm.position)))
    known = {row.term.casefold() for row in current}
    position = max((row.position for row in current), default=-1) + 1
    added = 0
    for term in terms:
        if not isinstance(term, str) or not term.strip() or len(term) > 60 or term.casefold() in known:
            continue
        if len(current) + added >= GLOSSARY_MAX_TERMS:
            break
        db.add(GlossaryTerm(term=term.strip(), position=position))
        known.add(term.casefold())
        position += 1
        added += 1
    return added


def _merge_templates(db, templates: list[dict]) -> tuple[int, dict[str, str]]:
    """Templates missing by name are added; returns the count and every name → id."""
    by_name = {t.name: t.id for t in db.scalars(select(SummaryTemplate))}
    added = 0
    for template in templates:
        name = str(template.get("name") or "").strip()[:120]
        if not name or name in by_name or not template.get("prompt"):
            continue
        row = SummaryTemplate(
            id=str(uuid.uuid4()), name=name, description=template.get("description"), prompt=str(template["prompt"]), is_default=False,
        )
        db.add(row)
        by_name[name] = row.id
        added += 1
    return added, by_name


def _tags(db, names: list[str]) -> list[Tag]:
    tags = []
    for name in dict.fromkeys(" ".join(str(n).split())[:40] for n in names if str(n).strip()):
        tag = db.scalar(select(Tag).where(func.lower(Tag.name) == name.lower()))
        if tag is None:
            tag = Tag(name=name)
            db.add(tag)
            db.flush()
        tags.append(tag)
    return tags


def _insert_video(db, document: dict, path: Path, template_ids: dict[str, str]) -> None:
    video = Video(
        id=document["id"],
        filename=path.name,
        original_filename=str(document.get("original_filename") or path.name)[:255],
        path=str(path),
        duration_seconds=float(document.get("duration_seconds") or 0),
        size_bytes=int(document.get("size_bytes") or 0),
        status="COMPLETED",
        detected_language=document.get("detected_language"),
        target_language=document.get("target_language"),
        source_language_forced=bool(document.get("source_language_forced")),
        transcript_text=document.get("transcript_text"),
        translated_text=document.get("translated_text"),
        vocabulary=document.get("vocabulary"),
        glossary_snapshot=document.get("glossary_snapshot"),
        transcript_edited_at=_date(document.get("transcript_edited_at")),
        translated_at=_date(document.get("translated_at")),
        diarize=bool(document.get("diarize")),
        num_speakers=document.get("num_speakers"),
        diarization_error=document.get("diarization_error"),
        created_at=_date(document.get("created_at")) or datetime.now(timezone.utc),
    )
    video.tags = _tags(db, document.get("tags") or [])
    db.add(video)
    db.flush()
    speakers = {}
    for item in document.get("speakers") or []:
        speaker = Speaker(video_id=video.id, position=int(item["position"]), name=item.get("name"))
        db.add(speaker)
        speakers[speaker.position] = speaker
    db.flush()
    db.add_all([
        TranscriptSegment(
            video_id=video.id, start_seconds=float(s["start"]), end_seconds=float(s["end"]), text=str(s["text"]),
            speaker_id=speakers[s["speaker"]].id if s.get("speaker") in speakers else None,
        )
        for s in document.get("segments") or []
    ])
    existing_summaries = set(db.scalars(select(Summary.id).where(Summary.id.in_(
        [s.get("id") for s in document.get("summaries") or [] if s.get("id")]
    ))))
    for s in document.get("summaries") or []:
        db.add(Summary(
            id=s["id"] if s.get("id") and s["id"] not in existing_summaries else str(uuid.uuid4()),
            video_id=video.id, template_id=template_ids.get(s.get("template_name") or ""), language=s.get("language"),
            summary_length=s.get("summary_length"), content_markdown=str(s["content_markdown"]), model=str(s.get("model") or ""),
            edited_at=_date(s.get("edited_at")), created_at=_date(s.get("created_at")) or datetime.now(timezone.utc),
        ))
    db.add_all([Chapter(video_id=video.id, start_seconds=float(c["start"]), title=str(c["title"])[:200]) for c in document.get("chapters") or []])
    db.add_all([
        VideoChatMessage(
            id=str(uuid.uuid4()), video_id=video.id, role=str(m["role"])[:16], content=str(m["content"]),
            interrupted=bool(m.get("interrupted")), created_at=_date(m.get("created_at")) or datetime.now(timezone.utc),
        )
        for m in document.get("chat") or []
    ])


def _insert_conversations(db, conversations: list[dict], known_videos: set[str]) -> int:
    existing = set(db.scalars(select(LibraryConversation.id)))
    added = 0
    for item in conversations:
        conversation_id = str(item.get("id") or "")
        video_ids = [v for v in item.get("video_ids") or [] if v in known_videos]
        if not _VIDEO_ID.match(conversation_id) or conversation_id in existing or not video_ids:
            continue
        now = datetime.now(timezone.utc)
        conversation = LibraryConversation(
            id=conversation_id, title=str(item.get("title") or "Conversation")[:120], scope=(item.get("scope") or None),
            video_ids=json.dumps(video_ids), created_at=_date(item.get("created_at")) or now,
            updated_at=_date(item.get("updated_at")) or now,
        )
        db.add(conversation)
        db.flush()
        for message in item.get("messages") or []:
            db.add(LibraryMessage(
                id=str(uuid.uuid4()), conversation_id=conversation_id, role=str(message["role"])[:16],
                content=str(message["content"]), sources=json.dumps(message.get("sources") or [], ensure_ascii=False),
                interrupted=bool(message.get("interrupted")), created_at=_date(message.get("created_at")) or now,
            ))
        added += 1
    return added


def import_archive(path: Path, *, on_video=None) -> dict:
    """Merge the archive into the library; returns what was added and skipped."""
    from . import worker  # read at call time: the tests replace worker.enqueue_index_job

    try:
        archive = tarfile.open(path, "r:")
    except (tarfile.TarError, OSError) as exc:
        raise ArchiveError("Ce fichier n'est pas une archive de bibliothèque Sténo") from exc
    report = {"videos_added": 0, "videos_skipped": 0, "media_added": 0, "templates_added": 0, "glossary_added": 0,
              "conversations_added": 0, "errors": []}
    with archive:
        members = {member.name: member for member in archive.getmembers() if member.isfile()}
        manifest = members.get(MANIFEST)
        if manifest is None:
            raise ArchiveError("Ce fichier n'est pas une archive de bibliothèque Sténo")
        header = _read_json(archive, manifest)
        if header.get("format") != FORMAT:
            raise ArchiveError("Ce fichier n'est pas une archive de bibliothèque Sténo")
        if int(header.get("version") or 0) > VERSION:
            raise ArchiveError("Archive créée par une version plus récente de Sténo : mettez l'application à jour")

        with SessionLocal() as db:
            if "glossary.json" in members:
                report["glossary_added"] = _merge_glossary(db, _read_json(archive, members["glossary.json"]).get("terms") or [])
            templates = _read_json(archive, members["templates.json"]) if "templates.json" in members else []
            report["templates_added"], template_ids = _merge_templates(db, templates)
            db.commit()

        media = {}
        for name in members:
            parts = name.split("/")
            if len(parts) == 3 and parts[0] == "videos" and parts[2].startswith("source."):
                media[parts[1]] = members[name]
        added: list[str] = []
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        for name, member in members.items():
            match = _VIDEO_MEMBER.match(name)
            if not match:
                continue
            video_id = match.group(1)
            document = _read_json(archive, member)
            label = str(document.get("original_filename") or video_id)
            try:
                if document.get("id") != video_id:
                    raise ArchiveError("identifiant incohérent")
                with SessionLocal() as db:
                    if db.get(Video, video_id) is not None:
                        report["videos_skipped"] += 1
                        continue
                    suffix = str(document.get("media_suffix") or ".mp4").lower()
                    if suffix not in AUDIO_SUFFIXES | VIDEO_SUFFIXES:
                        suffix = ".mp4"
                    destination = settings.uploads_dir / f"{video_id}{suffix}"
                    if video_id in media and Path(media[video_id].name).suffix.lower() == suffix:
                        _copy_media(archive, media[video_id], destination)
                        report["media_added"] += 1
                    _insert_video(db, document, destination, template_ids)
                    db.commit()
                added.append(video_id)
                report["videos_added"] += 1
            except Exception as exc:
                logger.warning("Unable to import video %s", video_id, exc_info=True)
                report["errors"].append(f"{label} : {exc if isinstance(exc, ArchiveError) else 'données invalides'}")
            if on_video:
                on_video(report)

        with SessionLocal() as db:
            known = set(db.scalars(select(Video.id)))
            if "conversations.json" in members:
                report["conversations_added"] = _insert_conversations(db, _read_json(archive, members["conversations.json"]), known)
            db.commit()

    for video_id in added:
        try:
            with SessionLocal() as db:
                write_exports(db, video_id)
            worker.enqueue_index_job(video_id)
        except Exception:
            logger.warning("Exports or indexing of imported video %s failed", video_id, exc_info=True)
    return report


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(argv) == 3 and argv[0] == "export" and argv[1] in ("--media", "--sans-media"):
        destination = Path(argv[2])
        with destination.open("wb") as out:
            for piece in export_stream(argv[1] == "--media"):
                out.write(piece)
        print(f"Archive écrite : {destination} ({destination.stat().st_size / 1024 ** 2:.1f} Mo)")
        return 0
    if len(argv) == 2 and argv[0] == "import":
        try:
            report = import_archive(Path(argv[1]))
        except ArchiveError as exc:
            print(f"Erreur : {exc}", file=sys.stderr)
            return 1
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print("Usage : python -m app.portable export --media|--sans-media <archive.tar> | import <archive.tar>")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

