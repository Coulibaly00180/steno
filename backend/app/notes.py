"""Exports to the user's tools (n°8): Markdown notes (Obsidian) and an e-mail draft.

A note per video: YAML front matter (date, duration, languages, tags, link,
people), the summary, the actions as a checklist, the decisions, the
chapters, and optionally the transcript. People, organisations and places
are [[wikilinks]]: in Obsidian, each becomes a page linking its videos, and
the library export adds those pages, with what is said about each.

The e-mail draft is an .eml file: Outlook and Thunderbird open it as a new
message (X-Unsent), ready to address and send.
"""
import io
import re
import zipfile
from collections.abc import Iterator
from datetime import datetime, timezone
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ActionItem, Entity, EntityMention, Video
from .utils import timestamp

FILENAME_MAX = 120
_UNSAFE = re.compile(r'[\\/:*?"<>|#^\[\]]+')
_TIMESTAMP = re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


def demote_headings(markdown: str, levels: int = 2) -> str:
    """The summary's « # Résumé » under the note's « ## Compte-rendu »: its headings go down a level or two."""
    return "\n".join(
        f"{'#' * min(6, len(match.group(1)) + levels)} {match.group(2)}" if (match := _HEADING.match(line)) else line
        for line in markdown.splitlines()
    )


KIND_FOLDERS ={"person": "Personnes", "organization": "Organisations", "place": "Lieux", "date": "Dates"}


def safe_name(value: str, fallback: str = "Sans titre") -> str:
    """A file name every system accepts (and an Obsidian link target)."""
    text = " ".join(_UNSAFE.sub(" ", value).split()).strip(" .")
    return text[:FILENAME_MAX] or fallback


def _yaml(value) -> str:
    if value is None:
        return '""'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def video_title(video: Video) -> str:
    return Path(video.original_filename).stem if Path(video.original_filename).suffix else video.original_filename


def _entities(db: Session, video_id: str) -> list[Entity]:
    return list(db.scalars(
        select(Entity).join(EntityMention, EntityMention.entity_id == Entity.id)
        .where(EntityMention.video_id == video_id, Entity.hidden.is_(False))
        .group_by(Entity.id).order_by(Entity.kind, Entity.name)
    ))


def _items(db: Session, video_id: str) -> list[ActionItem]:
    return list(db.scalars(
        select(ActionItem).where(ActionItem.video_id == video_id).order_by(ActionItem.kind, ActionItem.position, ActionItem.created_at)
    ))


def video_note(db: Session, video: Video, *, transcript: bool = False, links: bool = True) -> str:
    """The Obsidian note of a video."""
    summary = video.summaries[-1].content_markdown if video.summaries else ""
    entities = _entities(db, video.id)
    items = _items(db, video.id)
    people = [entity.name for entity in entities if entity.kind == "person"]
    front = [
        "---",
        f"titre: {_yaml(video_title(video))}",
        f"date: {_aware(video.created_at):%Y-%m-%d}",
        f"duree: {_yaml(timestamp(video.duration_seconds))}",
        f"langue: {_yaml(video.detected_language or '')}",
    ]
    if video.target_language:
        front.append(f"traduction: {_yaml(video.target_language)}")
    if video.source_url:
        front.append(f"source: {_yaml(video.source_url)}")
    tags = ["steno", *[re.sub(r"\s+", "-", tag.name) for tag in video.tags]]
    front.append("tags: [" + ", ".join(_yaml(tag) for tag in tags) + "]")
    if people:
        front.append("personnes: [" + ", ".join(_yaml(f"[[{safe_name(name)}]]" if links else name) for name in people) + "]")
    front += [f"steno_id: {_yaml(video.id)}", "---", ""]

    body = [f"# {video_title(video)}", ""]
    if summary:
        body += ["## Compte-rendu", "", demote_headings(summary.strip()), ""]
    actions = [item for item in items if item.kind == "action"]
    decisions = [item for item in items if item.kind == "decision"]
    if actions:
        body += ["## Actions", ""]
        for item in actions:
            check = "x" if item.status == "done" else " "
            details = [f"**{item.owner}**" if item.owner else "", f"📅 {item.due_date.isoformat()}" if item.due_date else (item.due_text or "")]
            suffix = " — " + " · ".join(detail for detail in details if detail) if any(details) else ""
            dropped = "~~" if item.status == "dropped" else ""
            body.append(f"- [{check}] {dropped}{item.text}{dropped}{suffix}")
        body.append("")
    if decisions:
        body += ["## Décisions", ""] + [f"- {item.text}" for item in decisions] + [""]
    if video.chapters:
        body += ["## Chapitres", ""] + [f"- {timestamp(chapter.start_seconds)} {chapter.title}" for chapter in video.chapters] + [""]
    if entities and links:
        groups = {}
        for entity in entities:
            groups.setdefault(entity.kind, []).append(f"[[{safe_name(entity.name)}]]")
        body += ["## Cités", ""] + [f"- {KIND_FOLDERS[kind]} : {', '.join(names)}" for kind, names in groups.items()] + [""]
    if transcript and video.transcript_text:
        body += ["## Transcription", "", video.transcript_text.strip(), ""]
    return "\n".join(front + body)


def entity_note(db: Session, entity: Entity, titles: dict[str, str]) -> str:
    """The page of a person, organisation, place or date: what each video says of it."""
    lines = ["---", f"type: {_yaml(KIND_FOLDERS.get(entity.kind, entity.kind))}", "tags: [\"steno\"]", "---", "", f"# {entity.name}", ""]
    mentions = db.scalars(
        select(EntityMention).where(EntityMention.entity_id == entity.id).order_by(EntityMention.video_id, EntityMention.start_seconds)
    )
    current = None
    for mention in mentions:
        if mention.video_id not in titles:
            continue
        if mention.video_id != current:
            current = mention.video_id
            lines += ["", f"## [[{titles[current]}]]", ""]
        lines.append(f"- {timestamp(mention.start_seconds)} — {mention.context}")
    return "\n".join(lines) + "\n"


class _Sink(io.RawIOBase):
    """A write-only stream the zip writes into; the export hands its bytes on as they come."""

    def __init__(self):
        self.buffer = bytearray()
        self.position = 0

    def writable(self) -> bool:
        return True

    def write(self, data) -> int:
        self.buffer.extend(data)
        self.position += len(data)
        return len(data)

    def tell(self) -> int:
        return self.position

    def drain(self) -> bytes:
        data = bytes(self.buffer)
        self.buffer.clear()
        return data


def obsidian_zip(session_factory, *, transcripts: bool = False) -> Iterator[bytes]:
    """The library as an Obsidian folder, streamed: « Sténo/Vidéos », « Sténo/Personnes »…"""
    sink = _Sink()
    archive = zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED)
    with session_factory() as db:
        ids = list(db.scalars(select(Video.id).where(Video.status == "COMPLETED").order_by(Video.created_at)))
    titles: dict[str, str] = {}
    used: set[str] = set()
    for video_id in ids:
        with session_factory() as db:
            video = db.get(Video, video_id)
            if video is None:
                continue
            name = safe_name(video_title(video))
            if name.casefold() in used:
                name = safe_name(f"{name} ({_aware(video.created_at):%Y-%m-%d})")
            if name.casefold() in used:
                name = safe_name(f"{name} {video.id[:8]}")
            used.add(name.casefold())
            titles[video.id] = name
            archive.writestr(f"Sténo/Vidéos/{name}.md", video_note(db, video, transcript=transcripts))
        yield sink.drain()
    with session_factory() as db:
        for entity in db.scalars(select(Entity).where(Entity.hidden.is_(False))):
            folder = KIND_FOLDERS.get(entity.kind, "Autres")
            archive.writestr(f"Sténo/{folder}/{safe_name(entity.name)}.md", entity_note(db, entity, titles))
            yield sink.drain()
    archive.writestr("Sténo/Lisez-moi.md", (
        "# Export Sténo\n\nUne note par vidéo dans « Vidéos », une page par personne, organisation, lieu ou date citée. "
        "Ouvrez le dossier « Sténo » comme un coffre Obsidian, ou copiez-le dans le vôtre.\n"
    ))
    archive.close()
    yield sink.drain()


def email_draft(db: Session, video: Video, *, sender: str = "") -> bytes:
    """An .eml draft of the meeting report: summary, actions, decisions, in text and HTML."""
    title = video_title(video)
    summary = video.summaries[-1].content_markdown.strip() if video.summaries else "Aucun résumé."
    items = _items(db, video.id)
    # Headings as plain lines ending with « : »: the HTML part makes them titles again.
    summary = "\n".join(f"{match.group(2)} :" if (match := _HEADING.match(line)) else line.rstrip() for line in summary.splitlines())
    text_lines = [f"Compte-rendu : {title}", f"Date : {_aware(video.created_at):%d/%m/%Y} · Durée : {timestamp(video.duration_seconds)}", "", summary]
    open_actions = [item for item in items if item.kind == "action" and item.status != "dropped"]
    decisions = [item for item in items if item.kind == "decision"]
    if open_actions:
        text_lines += ["", "Suivi des actions :"]
        for item in open_actions:
            detail = " — ".join(part for part in (item.owner, item.due_date.strftime("%d/%m/%Y") if item.due_date else item.due_text) if part)
            text_lines.append(f"- [{'x' if item.status == 'done' else ' '}] {item.text}" + (f" ({detail})" if detail else ""))
    if decisions:
        text_lines += ["", "Décisions :"] + [f"- {item.text}" for item in decisions]
    text_lines += ["", "—", "Préparé avec Sténo (transcription et résumé locaux)."]
    body = _TIMESTAMP.sub(r"(\1)", "\n".join(text_lines))

    message = EmailMessage(policy=SMTP)
    message["Subject"] = f"Compte-rendu : {title}"
    if sender:
        message["From"] = sender
    message["X-Unsent"] = "1"  # Outlook opens it as a draft to send
    message.set_content(body)
    html = "".join(
        f"<h3>{_escape(line[:-2].rstrip())}</h3>" if line.endswith(" :") and not line.startswith("-")
        else f"<p>{_escape(line)}</p>" if line else "<br>"
        for line in body.splitlines()
    )
    message.add_alternative(f"<html><body style=\"font-family:sans-serif\">{html}</body></html>", subtype="html")
    return bytes(message)


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
