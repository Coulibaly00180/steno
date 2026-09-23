"""Export files, always rebuilt from the database state.

Called by the worker at the end of a run and by the API after a manual edit,
so the downloads never lag behind what the page shows.
"""
import json
import re

from sqlalchemy.orm import Session

from .analysis_options import SUMMARY_LENGTH_LABELS, split_stored_terms, word_budget
from .config import settings
from .models import SummaryTemplate, Video
from .speakers import speaker_labels
from .utils import timestamp

EXPORT_NAMES = (
    "summary.md",
    "transcript.txt",
    "transcript.srt",
    "transcript.vtt",
    "translation.txt",
    "translation.srt",
    "translation.vtt",
    "chapters.txt",
    "metadata.json",
)


def srt_time(value: float) -> str:
    ms = int(round(value * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    sec, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{milli:03d}"


_TRANSLATED_LINE = re.compile(r"^\[(\d{1,2}):(\d{2}):(\d{2})\]\s*(.*)$")
# A cue never stays on screen longer than this when the next one is far away.
MAX_CUE_SECONDS = 7.0


def translated_cues(translated: str, segments: list[tuple[float, float]]) -> list[tuple[float, float, str]]:
    """Subtitles of the translation (n°20): each "[hh:mm:ss] text" line is a cue.

    The translation keeps the source timestamps; a cue ends where the source
    segment starting at that second ends, never after the next cue.
    """
    ends = {}
    for start, end in segments:
        ends.setdefault(int(start), end)
    cues: list[list] = []
    for raw in translated.splitlines():
        match = _TRANSLATED_LINE.match(raw.strip())
        if match:
            hours, minutes, seconds, text = match.groups()
            start = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
            if text.strip():
                cues.append([float(start), ends.get(start, start + MAX_CUE_SECONDS), text.strip()])
        elif raw.strip() and cues:
            cues[-1][2] += " " + raw.strip()
    for current, following in zip(cues, cues[1:]):
        current[1] = min(current[1], following[0]) if following[0] > current[0] else current[1]
    return [(start, max(end, start + 0.5), text) for start, end, text in cues]


def _srt(rows) -> str:
    body = "\n\n".join(
        f"{index}\n{srt_time(start)} --> {srt_time(end)}\n{text}" for index, (start, end, text) in enumerate(rows, 1)
    )
    return body + ("\n" if body else "")


def _vtt(rows) -> str:
    return "WEBVTT\n\n" + "\n\n".join(
        f"{srt_time(start).replace(',', '.')} --> {srt_time(end).replace(',', '.')}\n{text}" for start, end, text in rows
    ) + "\n"


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def write_exports(db: Session, video_id: str) -> None:
    video = db.get(Video, video_id)
    if video is None:
        raise LookupError(video_id)
    export_dir = settings.exports_dir / video.id
    export_dir.mkdir(parents=True, exist_ok=True)
    labels = speaker_labels(video)
    rows = [
        (segment.start_seconds, segment.end_seconds,
         f"{labels[segment.speaker_id]} : {segment.text}" if segment.speaker_id in labels else segment.text)
        for segment in video.segments
    ]
    summary = video.summaries[-1] if video.summaries else None

    (export_dir / "transcript.txt").write_text(video.transcript_text or "", encoding="utf-8", newline="\n")
    translation = export_dir / "translation.txt"
    if video.translated_text:
        translation.write_text(video.translated_text, encoding="utf-8", newline="\n")
        cues = translated_cues(video.translated_text, [(s.start_seconds, s.end_seconds) for s in video.segments])
        (export_dir / "translation.srt").write_text(_srt(cues), encoding="utf-8", newline="\n")
        (export_dir / "translation.vtt").write_text(_vtt(cues), encoding="utf-8", newline="\n")
    else:
        for name in ("translation.txt", "translation.srt", "translation.vtt"):
            (export_dir / name).unlink(missing_ok=True)
    if summary:
        (export_dir / "summary.md").write_text(summary.content_markdown, encoding="utf-8", newline="\n")

    (export_dir / "transcript.srt").write_text(_srt(rows), encoding="utf-8", newline="\n")
    (export_dir / "transcript.vtt").write_text(_vtt(rows), encoding="utf-8", newline="\n")

    chapters_file = export_dir / "chapters.txt"
    if video.chapters:
        # Video-platform format: the first chapter starts at 00:00:00.
        lines = [
            f"{timestamp(0 if index == 0 else chapter.start_seconds)} {chapter.title}"
            for index, chapter in enumerate(video.chapters)
        ]
        chapters_file.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    else:
        chapters_file.unlink(missing_ok=True)

    summary_length = summary.summary_length if summary else None
    template = db.get(SummaryTemplate, summary.template_id) if summary and summary.template_id else None
    (export_dir / "metadata.json").write_text(json.dumps({
        "video_id": video.id,
        "model": summary.model if summary else settings.llm_model,
        "whisper_model": settings.whisper_model,
        "num_ctx": settings.llm_num_ctx,
        "target_language": video.target_language,
        "source_language": video.detected_language,
        "source_language_forced": video.source_language_forced,
        "summary_length": summary_length,
        "summary_length_label": SUMMARY_LENGTH_LABELS.get(summary_length or "", summary_length),
        "word_budget": word_budget(video.duration_seconds, summary_length) if summary_length else None,
        "template_name": template.name if template else None,
        "vocabulary": split_stored_terms(video.vocabulary),
        "glossary_snapshot": split_stored_terms(video.glossary_snapshot),
        "chapters": [{"start_seconds": c.start_seconds, "title": c.title} for c in video.chapters],
        "transcript_edited_at": _iso(video.transcript_edited_at),
        "summary_edited_at": _iso(summary.edited_at) if summary else None,
    }, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
