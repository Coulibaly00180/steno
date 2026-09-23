"""Disk space (n°14): what each video occupies, and freeing it.

A processed video keeps its text (transcript, translation, summary, exports)
whatever happens to its media. Three ways to free space:

- "audio": the source becomes a compact mono AAC file (~20 Mo per hour), the
  extracted WAV is dropped. Playback, subtitles and speaker detection still work.
- "delete_media": the source and the WAV are deleted; only the text remains.
- "delete_work_audio": only the WAV (~115 Mo per hour), re-extracted from the
  source when needed again.

The same "audio" and "delete" rules can be chosen at import (`source_policy`)
and are applied by the worker once the video is processed.
"""
import os
import shutil
import subprocess
from pathlib import Path

from sqlalchemy import select

from .config import settings
from .db import SessionLocal
from .models import Video

# Ogg formats are what Wikimedia Commons and many free podcasts publish; ffmpeg reads them.
AUDIO_SUFFIXES = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".oga", ".opus"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".ogv"}
SOURCE_POLICIES = ("keep", "audio", "delete")
STORAGE_ACTIONS = ("audio", "delete_media", "delete_work_audio")
AUDIO_ONLY_SUFFIX = ".m4a"
AUDIO_ONLY_BITRATE = "48k"
# Already compressed audio: converting it again would save little and lose quality.
COMPRESSED_AUDIO_SUFFIXES = {".mp3", ".m4a", ".ogg", ".oga", ".opus"}


class StorageError(RuntimeError):
    """A refused or failed storage action, with a message for the user."""


def work_audio_path(video_id: str) -> Path:
    return settings.audio_dir / f"{video_id}.wav"


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def directory_size(path: Path) -> int:
    total = 0
    if not path.is_dir():
        return 0
    for root, _, files in os.walk(path):
        for name in files:
            total += file_size(Path(root) / name)
    return total


def video_usage(video: Video) -> dict:
    source = Path(video.path)
    usage: dict = {
        "source_bytes": file_size(source),
        "audio_bytes": file_size(work_audio_path(video.id)),
        "exports_bytes": directory_size(settings.exports_dir / video.id),
    }
    usage["total_bytes"] = usage["source_bytes"] + usage["audio_bytes"] + usage["exports_bytes"]
    usage["source_available"] = source.is_file()
    usage["source_kind"] = "audio" if source.suffix.lower() in AUDIO_SUFFIXES else "video"
    usage["can_compact"] = can_compact(source)
    return usage


def can_compact(source: Path) -> bool:
    return source.is_file() and source.suffix.lower() not in COMPRESSED_AUDIO_SUFFIXES


def disk_summary() -> dict:
    try:
        disk = shutil.disk_usage(settings.data_dir)
        total, free = disk.total, disk.free
    except OSError:
        total = free = None
    return {
        "disk_total_bytes": total,
        "disk_free_bytes": free,
        "backups_bytes": directory_size(settings.backups_dir),
        "inbox_bytes": directory_size(settings.inbox_dir),
    }


def _transcode_to_audio(source: Path, destination: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-nostdin", "-i", str(source), "-vn", "-ac", "1", "-c:a", "aac",
            "-b:a", AUDIO_ONLY_BITRATE, "-movflags", "+faststart", "-f", "mp4", str(destination),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=settings.ffmpeg_timeout_seconds,
    )


def compact_to_audio(video_id: str) -> int:
    """Replace the source by a compact audio file; returns the bytes saved.

    ffmpeg writes a temporary file outside any transaction; the video row then
    points to the new file only if its source did not change meanwhile.
    """
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        if video is None:
            raise StorageError("Vidéo introuvable")
        source = Path(video.path)
    if not can_compact(source):
        raise StorageError("Aucun fichier source à convertir" if not source.is_file() else "La source est déjà un fichier audio compressé")
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    final = settings.uploads_dir / f"{video_id}{AUDIO_ONLY_SUFFIX}"
    temporary = settings.uploads_dir / f".{video_id}.compact{AUDIO_ONLY_SUFFIX}"
    before = file_size(source) + file_size(work_audio_path(video_id))
    try:
        _transcode_to_audio(source, temporary)
        if file_size(temporary) == 0:
            raise StorageError("La conversion n'a produit aucun son")
        with SessionLocal() as db:
            video = db.scalar(select(Video).where(Video.id == video_id).with_for_update())
            if video is None or Path(video.path) != source:
                raise StorageError("La vidéo a changé pendant la conversion")
            os.replace(temporary, final)
            video.path = str(final)
            video.filename = final.name
            db.commit()
    finally:
        temporary.unlink(missing_ok=True)
    if source != final:
        source.unlink(missing_ok=True)
    work_audio_path(video_id).unlink(missing_ok=True)
    return max(0, before - file_size(final))


def delete_media(video: Video) -> int:
    """Delete the source and the WAV; the text stays. Returns the bytes freed."""
    source = Path(video.path)
    freed = file_size(source) + file_size(work_audio_path(video.id))
    source.unlink(missing_ok=True)
    work_audio_path(video.id).unlink(missing_ok=True)
    return freed


def delete_work_audio(video: Video) -> int:
    if not Path(video.path).is_file():
        raise StorageError("La piste de travail est la seule piste de lecture : utilisez « Supprimer les médias »")
    freed = file_size(work_audio_path(video.id))
    work_audio_path(video.id).unlink(missing_ok=True)
    return freed
