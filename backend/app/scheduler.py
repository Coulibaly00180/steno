"""The `scheduler` service: watched folder (n°9) and scheduled backups (n°13).

One small loop, separate from the worker: the worker is busy for hours with a
long video, and neither task may wait for it. Its heartbeat in Redis lets
/status report it.
"""
import json
import logging
import signal
import time

from redis import Redis

from . import backups
from .config import settings
from .db import engine
from .schema import assert_schema_current
from .status import SCHEDULER_HEARTBEAT_KEY as HEARTBEAT_KEY
from .watch_folder import InboxWatcher

logger = logging.getLogger(__name__)

BACKUP_CHECK_SECONDS = 60


def heartbeat_ttl() -> int:
    return max(30, settings.scheduler_poll_seconds * 6)


def beat(redis: Redis, **details) -> None:
    try:
        redis.set(HEARTBEAT_KEY, json.dumps({"at": time.time(), **details}), ex=heartbeat_ttl())
    except Exception:
        logger.warning("Unable to write the scheduler heartbeat", exc_info=True)


def tick(watcher: InboxWatcher, *, check_backups: bool) -> None:
    try:
        watcher.scan()
    except Exception:
        logger.exception("Watched folder scan failed")
    if check_backups:
        try:
            info = backups.run_scheduled()
            if info:
                logger.info("Scheduled backup %s written", info.name)
        except Exception:
            logger.exception("Scheduled backup failed")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    assert_schema_current(engine)
    settings.inbox_dir.mkdir(parents=True, exist_ok=True)
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    watcher = InboxWatcher()
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    next_backup_check = 0.0
    logger.info("Scheduler started: inbox %s, backups %s", settings.inbox_dir, settings.backups_dir)
    while not stopping:
        now = time.monotonic()
        beat(redis)
        check = now >= next_backup_check
        tick(watcher, check_backups=check)
        if check:
            next_backup_check = now + BACKUP_CHECK_SECONDS
        # Short sleeps: a `docker compose stop` is answered at once.
        deadline = time.monotonic() + settings.scheduler_poll_seconds
        while not stopping and time.monotonic() < deadline:
            time.sleep(0.5)


if __name__ == "__main__":
    main()
