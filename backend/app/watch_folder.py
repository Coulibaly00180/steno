"""Watched folder (n°9): a file dropped in `data/inbox` is imported on its own.

The scheduler scans the folder every few seconds (polling: file events do
not cross a Windows or macOS bind mount). A file is imported once its size
and date have not changed for `watch_stable_seconds` (a copy in progress
grows), with the defaults chosen in the settings. It is moved, not copied,
into the uploads: the inbox is on the same volume.

A file that cannot be imported goes to `inbox/_rejets/`, with a
`<nom>.motif.txt` explaining why; the settings page lists them.
"""
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from . import app_settings
from .config import settings
from .db import SessionLocal
from .schemas import WatchFolderSettings

logger = logging.getLogger(__name__)

REJECTED_DIR = "_rejets"
REASON_SUFFIX = ".motif.txt"
# Partial downloads and system files are never imported.
PARTIAL_SUFFIXES = {".part", ".partial", ".crdownload", ".download", ".tmp", ".temp"}
IGNORED_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}
# The API is briefly unavailable (Redis restarting…): try the same file again later.
RETRY_AFTER_SECONDS = 60


def inbox() -> Path:
    return settings.inbox_dir


def rejected_dir() -> Path:
    return inbox() / REJECTED_DIR


def is_candidate(path: Path) -> bool:
    name = path.name
    return (
        path.is_file()
        and not name.startswith((".", "~"))
        and name.casefold() not in IGNORED_NAMES
        and path.suffix.lower() not in PARTIAL_SUFFIXES
    )


def pending_files() -> list[dict]:
    """Files waiting in the inbox (being copied, or about to be imported)."""
    if not inbox().is_dir():
        return []
    found = []
    for path in sorted(inbox().iterdir()):
        if is_candidate(path):
            stat = path.stat()
            found.append({"name": path.name, "size_bytes": stat.st_size,
                          "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc)})
    return found


def rejected_files() -> list[dict]:
    folder = rejected_dir()
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
        if not path.is_file() or path.name.endswith(REASON_SUFFIX) or not is_candidate(path):
            continue
        reason_file = folder / f"{path.name}{REASON_SUFFIX}"
        try:
            reason = reason_file.read_text(encoding="utf-8").strip()
        except OSError:
            reason = "Motif inconnu"
        found.append({"name": path.name, "size_bytes": path.stat().st_size, "reason": reason,
                      "rejected_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)})
    return found


def rejected_path(name: str) -> Path:
    """A rejected file of this name; refuses any path outside the folder."""
    if not name or "/" in name or "\\" in name or name in (".", "..") or name.endswith(REASON_SUFFIX):
        raise FileNotFoundError(name)
    path = rejected_dir() / name
    if not path.is_file():
        raise FileNotFoundError(name)
    return path


def _free_name(folder: Path, name: str) -> Path:
    """`name`, or `name (2)`… when a file of that name already exists."""
    candidate, number = folder / name, 2
    stem, suffix = Path(name).stem, Path(name).suffix
    while candidate.exists():
        candidate, number = folder / f"{stem} ({number}){suffix}", number + 1
    return candidate


def reject(path: Path, reason: str) -> Path:
    rejected_dir().mkdir(parents=True, exist_ok=True)
    destination = _free_name(rejected_dir(), path.name)
    os.replace(path, destination)
    (rejected_dir() / f"{destination.name}{REASON_SUFFIX}").write_text(reason + "\n", encoding="utf-8")
    logger.info("Watched folder: %s rejected (%s)", path.name, reason)
    return destination


def retry_rejected(name: str) -> None:
    """Put a rejected file back in the inbox: it is tried again at the next scan."""
    path = rejected_path(name)
    os.replace(path, _free_name(inbox(), name))
    (rejected_dir() / f"{name}{REASON_SUFFIX}").unlink(missing_ok=True)


def delete_rejected(name: str) -> None:
    rejected_path(name).unlink()
    (rejected_dir() / f"{name}{REASON_SUFFIX}").unlink(missing_ok=True)


def load_settings() -> WatchFolderSettings:
    with SessionLocal() as db:
        return app_settings.load(db, app_settings.WATCH_FOLDER, WatchFolderSettings)


def import_file(path: Path, config: WatchFolderSettings) -> str:
    """Import one stable file; returns the job id. Raises HTTPException when refused."""
    from . import main  # the API module owns the import rules shared with the upload form

    original_filename = path.name
    suffix = main.media_suffix(original_filename)
    if len(original_filename) > 255:
        raise HTTPException(422, "Le nom du fichier ne peut pas dépasser 255 caractères")
    options = main.import_settings(
        target_language=config.target_language,
        template_id=config.template_id,
        custom_prompt=None,
        summary_length=config.summary_length,
        source_language=config.source_language,
        vocabulary=None,
        use_global_glossary=config.use_global_glossary,
        diarize=config.diarize,
        num_speakers=config.num_speakers,
        source_policy=config.source_policy,
        tag=config.tag,
    )
    video_id = str(uuid.uuid4())
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    destination = settings.uploads_dir / f"{video_id}{suffix}"
    os.replace(path, destination)
    try:
        job = main.create_import(destination, original_filename, options, video_id)
    except BaseException:
        # Back where it was: the caller rejects it or keeps it for later.
        os.replace(destination, path)
        raise
    logger.info("Watched folder: %s imported as video %s", original_filename, video_id)
    return job.id


@dataclass
class _Seen:
    size: int
    mtime_ns: int
    since: float


class InboxWatcher:
    """Keeps the size and date of each file between two scans: a stable file is imported."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.seen: dict[str, _Seen] = {}
        self.retry_at: dict[str, float] = {}

    def scan(self, config: WatchFolderSettings | None = None) -> list[str]:
        """One pass over the inbox; returns the names imported."""
        config = config or load_settings()
        if not config.enabled or not inbox().is_dir():
            self.seen.clear()
            return []
        now = self.clock()
        imported = []
        present = set()
        for path in sorted(inbox().iterdir()):
            if not is_candidate(path):
                continue
            name = path.name
            present.add(name)
            try:
                stat = path.stat()
            except OSError:
                continue
            previous = self.seen.get(name)
            if previous is None or (previous.size, previous.mtime_ns) != (stat.st_size, stat.st_mtime_ns):
                self.seen[name] = _Seen(stat.st_size, stat.st_mtime_ns, now)
                continue
            if now - previous.since < settings.watch_stable_seconds or self.retry_at.get(name, 0) > now:
                continue
            if stat.st_size == 0:
                continue  # an empty file is a copy that has not started yet
            try:
                import_file(path, config)
                imported.append(name)
            except HTTPException as exc:
                # 5xx: a service is down, the same file can succeed later; 4xx: the file or the settings are refused.
                if exc.status_code >= 500:
                    logger.warning("Watched folder: %s kept for later (%s)", name, exc.detail)
                    self.retry_at[name] = now + RETRY_AFTER_SECONDS
                    continue
                reject(path, str(exc.detail))
            except Exception:
                logger.exception("Watched folder: unable to import %s", name)
                self.retry_at[name] = now + RETRY_AFTER_SECONDS
                continue
            self.seen.pop(name, None)
            self.retry_at.pop(name, None)
        for name in set(self.seen) - present:
            self.seen.pop(name, None)
            self.retry_at.pop(name, None)
        return imported
