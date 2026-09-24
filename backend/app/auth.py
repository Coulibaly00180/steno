"""Access control (n°15): an optional password, required from the local network.

Sténo listens on 127.0.0.1 only. The optional `https` service (Caddy, profile
`reseau`) opens it to the local network over HTTPS and marks each request
it forwards with `X-Steno-Remote: 1` (it overwrites any such header sent by the
browser). So:

- a request without the mark comes from this computer: free, unless the user
  asked for the password here too (`require_local`);
- a marked request needs a session; without a password set, the network
  access is refused (never open to the network by accident).

The password is stored as a scrypt hash; the session is a signed cookie
(HMAC-SHA256) carrying the password version: changing the password closes
every session opened before.
"""
import base64
import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass

from . import app_settings
from .db import SessionLocal
from .schemas import AccessSettings

logger = logging.getLogger(__name__)

COOKIE = "steno_session"
REMOTE_HEADER = "x-steno-remote"
SESSION_SECONDS = 30 * 24 * 3600
CACHE_SECONDS = 5.0
# scrypt: 16 MiB of memory per attempt, ~50 ms. Brute force over the network stays slow (and rate limited).
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
LOGIN_ATTEMPTS = 10
LOGIN_WINDOW_SECONDS = 15 * 60
PUBLIC_PATHS = frozenset({"/health", "/ready", "/auth/status", "/auth/login", "/auth/logout", "/network/certificate"})

ERROR_LOGIN_REQUIRED = "Connexion requise"
ERROR_NETWORK_DISABLED = (
    "Accès depuis le réseau désactivé : définissez d'abord un mot de passe sur l'ordinateur où Sténo est installé "
    "(Paramètres › Accès et sécurité)."
)


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        SCRYPT_N, SCRYPT_R, SCRYPT_P, base64.b64encode(salt).decode(), base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        computed = hashlib.scrypt(
            password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(computed, base64.b64decode(digest))


def _sign(secret: str, payload: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def make_token(config: AccessSettings, now: float | None = None) -> str:
    expires = int((now or time.time()) + SESSION_SECONDS)
    payload = f"{config.version}.{expires}"
    return f"{payload}.{_sign(config.secret or '', payload)}"


def valid_token(token: str | None, config: AccessSettings, now: float | None = None) -> bool:
    if not token or not config.secret or not config.password_hash:
        return False
    try:
        version, expires, signature = token.split(".")
        payload = f"{version}.{expires}"
        if not hmac.compare_digest(signature, _sign(config.secret, payload)):
            return False
        return int(version) == config.version and int(expires) > (now or time.time())
    except ValueError:
        return False


# --- stored settings, cached a few seconds (read on every request) -----------------------------

_cache: tuple[float, AccessSettings] | None = None


def load() -> AccessSettings:
    global _cache
    moment = time.monotonic()
    if _cache is not None and moment - _cache[0] < CACHE_SECONDS:
        return _cache[1]
    with SessionLocal() as db:
        value = app_settings.load(db, app_settings.ACCESS, AccessSettings)
    _cache = (moment, value)
    return value


def cached() -> AccessSettings | None:
    """The settings if the cache is fresh, else None (the caller loads them off the event loop)."""
    if _cache is not None and time.monotonic() - _cache[0] < CACHE_SECONDS:
        return _cache[1]
    return None


def forget() -> None:
    global _cache
    _cache = None


def save(db, config: AccessSettings) -> None:
    app_settings.save(db, app_settings.ACCESS, config)
    forget()


# --- decisions -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    allowed: bool
    status: int = 200
    detail: str | None = None


def decide(path: str, *, remote: bool, token: str | None, config: AccessSettings) -> Decision:
    """May this request go through? (pure: tested without the HTTP layer)"""
    if path in PUBLIC_PATHS or path.startswith("/auth/"):
        return Decision(True)
    if not config.password_hash:
        return Decision(False, 403, ERROR_NETWORK_DISABLED) if remote else Decision(True)
    if not remote and not config.require_local:
        return Decision(True)
    if valid_token(token, config):
        return Decision(True)
    return Decision(False, 401, ERROR_LOGIN_REQUIRED)


def login_limited(redis, client: str) -> bool:
    """True when this client made too many attempts (10 per 15 minutes)."""
    key = f"steno:login:{client}"
    try:
        attempts = redis.incr(key)
        if attempts == 1:
            redis.expire(key, LOGIN_WINDOW_SECONDS)
        return attempts > LOGIN_ATTEMPTS
    except Exception:
        # Redis down: the scrypt cost still slows guessing down.
        logger.warning("Unable to count login attempts", exc_info=True)
        return False


def reset_attempts(redis, client: str) -> None:
    try:
        redis.delete(f"steno:login:{client}")
    except Exception:
        pass


def new_secret() -> str:
    return secrets.token_urlsafe(32)
