"""One Whisper transcription at a time across the workers (WORKER_REPLICAS > 1).

Measured on an RTX 5080 (16 GB), 10 minutes of audio next to a summary:

    Whisper alone          16.1 s   3.7 GB of video memory
    LLM alone              12.2 s  11.3 GB
    both at the same time  18.6 s  13.5 GB   (28.3 s one after the other)

A transcription next to the LLM fits. Two transcriptions next to the LLM would
not (~18 GB): past the card's memory, the drivers spill into system memory and
a 2-minute step took 58 minutes. So the transcriptions take turns; the LLM
calls already do (Ollama serves one request at a time, OLLAMA_NUM_PARALLEL=1).

The overlap above did not carry over to whole analyses on one card (three
imports: 97 s and 83 s with one worker, 102 s and 95 s with two; see
docs/specs/performance-workers.md): one worker is the default, this slot keeps
a second one safe.

The slot is a Redis key with a 2-minute lease, renewed every 30 s by a thread
of the process holding it: a worker killed mid-transcription frees it within
2 minutes.
"""
import logging
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager

from redis import Redis

from .config import settings

logger = logging.getLogger(__name__)

KEY = "steno:transcription-slot"
LEASE_SECONDS = 120
RENEW_SECONDS = 30
POLL_SECONDS = 2.0

# Only the holder renews or frees the slot (a lease that expired may belong to another worker now).
_RENEW = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('expire', KEYS[1], ARGV[2]) end return 0"
_RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end return 0"


@contextmanager
def transcription_slot(*, on_wait: Callable[[], None] | None = None, check: Callable[[], None] | None = None,
                       redis: Redis | None = None, key: str = KEY):
    """Hold the transcription slot inside the block.

    `on_wait()` is called once if another worker holds it; `check()` between two
    tries (it raises to give up, e.g. when the job is cancelled meanwhile).
    """
    client = redis or Redis.from_url(settings.redis_url)
    token = uuid.uuid4().hex
    waited = False
    while not client.set(key, token, nx=True, ex=LEASE_SECONDS):
        if not waited:
            waited = True
            if on_wait:
                on_wait()
        if check:
            check()
        time.sleep(POLL_SECONDS)
    stop = threading.Event()

    def renew() -> None:
        while not stop.wait(RENEW_SECONDS):
            try:
                client.eval(_RENEW, 1, key, token, LEASE_SECONDS)
            except Exception:
                logger.warning("Unable to renew the transcription slot", exc_info=True)

    renewer = threading.Thread(target=renew, name="transcription-slot", daemon=True)
    renewer.start()
    try:
        yield
    finally:
        stop.set()
        try:
            client.eval(_RELEASE, 1, key, token)
        except Exception:
            # The lease expires by itself.
            logger.warning("Unable to free the transcription slot", exc_info=True)
