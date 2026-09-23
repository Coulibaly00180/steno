"""Database backups (n°13): scheduled and on demand, with a guided restore.

A backup is a `pg_dump -Fc` of the whole database in `data/backups`. The
media files stay in `data/` next to it: copying the `data` folder elsewhere
copies the whole library. Scheduled backups ("auto") are pruned to the number
kept; on-demand ones ("manuel") and the safety copies taken before a restore
("avant-restauration") are only deleted by the user.

Restoring replaces the database: it runs from the command line, services
stopped (`docker compose --profile tools run --rm restore <fichier>`), and
restores into a temporary database first, so a failed restore leaves the
current one untouched.
"""
import fcntl
import json
import logging
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import make_url

from . import app_settings
from .config import settings
from .db import SessionLocal
from .schemas import BackupSettings

logger = logging.getLogger(__name__)

KINDS = ("auto", "manuel", "avant-restauration")
_NAME = re.compile(r"^steno-(\d{8}-\d{6})(?:-\d+)?-(auto|manuel|avant-restauration)\.dump$")
# Any dump of the folder is listed, e.g. those of the former `make backup`.
_ANY_DUMP = re.compile(r"^[\w.-]{1,200}\.dump$")
STATUS_FILE = ".status.json"
LOCK_FILE = ".lock"
# A failed scheduled backup is retried after this delay, not on every tick.
RETRY_AFTER_SECONDS = 15 * 60


class BackupError(RuntimeError):
    """A refused or failed backup operation, with a message for the user."""


class BackupBusy(BackupError):
    pass


@dataclass
class BackupInfo:
    name: str
    kind: str
    size_bytes: int
    created_at: datetime


def backups_dir() -> Path:
    return settings.backups_dir


def _connection() -> dict:
    url = make_url(settings.database_url)
    return {
        "host": url.host or "localhost",
        "port": str(url.port or 5432),
        "user": url.username or "postgres",
        "password": url.password or "",
        "database": url.database or "postgres",
    }


def _environment(connection: dict) -> dict:
    return {**os.environ, "PGPASSWORD": connection["password"]}


def _info(path: Path) -> BackupInfo:
    match = _NAME.match(path.name)
    stat = path.stat()
    if match:
        created = datetime.strptime(match.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
        kind = match.group(2)
    else:
        created = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        kind = "manuel"
    return BackupInfo(path.name, kind, stat.st_size, created)


def list_backups() -> list[BackupInfo]:
    folder = backups_dir()
    if not folder.is_dir():
        return []
    found = [_info(path) for path in folder.iterdir() if path.is_file() and _ANY_DUMP.match(path.name)]
    return sorted(found, key=lambda info: info.created_at, reverse=True)


def backup_path(name: str) -> Path:
    """The backup file of this name; refuses anything that is not a dump of the folder."""
    if not _ANY_DUMP.match(name):
        raise BackupError("Nom de sauvegarde invalide")
    path = backups_dir() / name
    if not path.is_file():
        raise BackupError("Sauvegarde introuvable")
    return path


@contextmanager
def _exclusive():
    """One backup or restore at a time, across the API and the scheduler.

    Best effort: some bind mounts (Docker Desktop) may not support flock; two
    dumps at once only cost time, and a restore refuses to run while any other
    session (a pg_dump included) uses the database.
    """
    backups_dir().mkdir(parents=True, exist_ok=True)
    with open(backups_dir() / LOCK_FILE, "w") as lock:
        locked = False
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except BlockingIOError as exc:
            raise BackupBusy("Une sauvegarde est déjà en cours") from exc
        except OSError:
            logger.warning("File locks unsupported in %s; backups run unlocked", backups_dir())
        try:
            yield
        finally:
            if locked:
                fcntl.flock(lock, fcntl.LOCK_UN)


def _new_name(kind: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    name, number = f"steno-{stamp}-{kind}.dump", 2
    while (backups_dir() / name).exists():
        name, number = f"steno-{stamp}-{number}-{kind}.dump", number + 1
    return name


def _dump(connection: dict, destination: Path) -> None:
    subprocess.run(
        [
            "pg_dump", "-h", connection["host"], "-p", connection["port"], "-U", connection["user"],
            "-Fc", "-f", str(destination), connection["database"],
        ],
        check=True,
        env=_environment(connection),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=settings.pg_dump_timeout_seconds,
    )


def _create_unlocked(kind: str) -> BackupInfo:
    if kind not in KINDS:
        raise ValueError(kind)
    name = _new_name(kind)
    final = backups_dir() / name
    partial = backups_dir() / f".{name}.partial"
    try:
        _dump(_connection(), partial)
        os.replace(partial, final)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", "replace").strip()[-500:]
        logger.error("pg_dump failed: %s", detail)
        raise BackupError("La sauvegarde a échoué (pg_dump)") from exc
    except subprocess.TimeoutExpired as exc:
        raise BackupError("La sauvegarde a dépassé le délai autorisé") from exc
    finally:
        partial.unlink(missing_ok=True)
    return _info(final)


def create_backup(kind: str = "manuel") -> BackupInfo:
    with _exclusive():
        info = _create_unlocked(kind)
    logger.info("Backup %s written (%d bytes)", info.name, info.size_bytes)
    return info


def delete_backup(name: str) -> None:
    backup_path(name).unlink()


def prune(keep: int) -> list[str]:
    """Delete the oldest scheduled backups beyond `keep`; others are never pruned."""
    automatic = [info for info in list_backups() if info.kind == "auto"]
    removed = []
    for info in automatic[keep:]:
        (backups_dir() / info.name).unlink(missing_ok=True)
        removed.append(info.name)
    return removed


def read_status() -> dict:
    try:
        return json.loads((backups_dir() / STATUS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_status(**values) -> None:
    backups_dir().mkdir(parents=True, exist_ok=True)
    (backups_dir() / STATUS_FILE).write_text(json.dumps(values), encoding="utf-8")


def load_settings() -> BackupSettings:
    with SessionLocal() as db:
        return app_settings.load(db, app_settings.BACKUPS, BackupSettings)


def due(config: BackupSettings, now: datetime | None = None) -> bool:
    """A scheduled backup is due: none yet, the latest is too old, and no recent failure."""
    if not config.enabled:
        return False
    now = now or datetime.now(timezone.utc)
    status = read_status()
    failed_at = status.get("last_error_at")
    if failed_at and now.timestamp() - failed_at < RETRY_AFTER_SECONDS:
        return False
    latest = next((info for info in list_backups() if info.kind == "auto"), None)
    return latest is None or (now - latest.created_at).total_seconds() >= config.interval_hours * 3600


def run_scheduled(now: datetime | None = None) -> BackupInfo | None:
    """The scheduler's tick: back up when due, then prune."""
    config = load_settings()
    if not due(config, now):
        return None
    try:
        info = create_backup("auto")
    except BackupBusy:
        return None
    except BackupError as exc:
        _write_status(last_error=str(exc), last_error_at=time.time())
        return None
    _write_status(last_success_at=time.time())
    prune(config.keep)
    return info


# --- Restore (command line only) ------------------------------------------------------------

def _psql(connection: dict, database: str, sql: str) -> str:
    result = subprocess.run(
        ["psql", "-h", connection["host"], "-p", connection["port"], "-U", connection["user"],
         "-d", database, "-v", "ON_ERROR_STOP=1", "-At", "-c", sql],
        check=True, env=_environment(connection), capture_output=True, text=True,
    )
    return result.stdout.strip()


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def other_sessions(connection: dict, database: str) -> int:
    return int(_psql(
        connection, "postgres",
        f"SELECT count(*) FROM pg_stat_activity WHERE datname = {_quote_literal(database)} AND pid <> pg_backend_pid()",
    ) or 0)


def restore(name: str, *, database: str | None = None, safety_copy: bool = True) -> dict:
    """Replace `database` (the application's by default) with the backup `name`."""
    path = backup_path(name)
    connection = _connection()
    target = database or connection["database"]
    temporary = f"{target}_restauration"
    with _exclusive():
        if other_sessions(connection, target):
            raise BackupError(
                "Des services utilisent encore la base. Arrêtez-les d'abord : docker compose stop api worker scheduler web"
            )
        safety = _create_unlocked("avant-restauration") if safety_copy and database is None else None
        _psql(connection, "postgres", f"DROP DATABASE IF EXISTS {_quote_identifier(temporary)}")
        _psql(connection, "postgres", f"CREATE DATABASE {_quote_identifier(temporary)}")
        result = subprocess.run(
            ["pg_restore", "-h", connection["host"], "-p", connection["port"], "-U", connection["user"],
             "--no-owner", "--exit-on-error", "-d", temporary, str(path)],
            env=_environment(connection), capture_output=True, text=True,
        )
        if result.returncode != 0:
            _psql(connection, "postgres", f"DROP DATABASE IF EXISTS {_quote_identifier(temporary)}")
            logger.error("pg_restore failed: %s", result.stderr[-1000:])
            raise BackupError(f"La restauration a échoué ; la base actuelle est intacte. Détail : {result.stderr.strip()[-300:]}")
        if other_sessions(connection, target):
            _psql(connection, "postgres", f"DROP DATABASE IF EXISTS {_quote_identifier(temporary)}")
            raise BackupError("Un service s'est reconnecté pendant la restauration ; la base actuelle est intacte")
        _psql(connection, "postgres", f"DROP DATABASE IF EXISTS {_quote_identifier(target)}")
        _psql(connection, "postgres", f"ALTER DATABASE {_quote_identifier(temporary)} RENAME TO {_quote_identifier(target)}")
    return {"restored": name, "database": target, "safety_copy": safety.name if safety else None}


def _print_list() -> None:
    for info in list_backups():
        print(f"{info.name}\t{info.kind}\t{info.size_bytes / 1024 ** 2:.1f} Mo\t{info.created_at:%Y-%m-%d %H:%M} UTC")


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    usage = "Usage : python -m app.backups [list | create | restore <fichier.dump>]"
    command = argv[0] if argv else "list"
    try:
        if command == "list":
            _print_list()
        elif command == "create":
            print(f"Sauvegarde écrite : {create_backup('manuel').name}")
        elif command == "restore" and len(argv) == 2:
            report = restore(argv[1])
            if report["safety_copy"]:
                print(f"Copie de sécurité de la base remplacée : {report['safety_copy']}")
            print(f"Base restaurée depuis {report['restored']}. Relancez l'application : docker compose up -d")
        else:
            print(usage)
            return 2
    except BackupError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    return 0


def as_dict(info: BackupInfo) -> dict:
    return asdict(info)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
