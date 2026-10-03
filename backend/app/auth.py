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

Access tokens (feuille de route n° 4, phase 1) let the browser extension and
scripts through instead of a session: `Authorization: Bearer steno_…`. Only
their SHA-256 is stored, and they are looked up on every request that carries
one (never cached): a revoked token is refused at the next request. A request
that presents a token must present a valid one, even where it would pass
without. Tokens do not open a network left closed (no password), and cannot
manage tokens nor the password (`routes/access.py`); changing or removing the
password revokes them all. A token's scope is "import" (the few routes the
extension needs, `IMPORT_ROUTES`) unless it was created with the full access.
"""
import base64
import hashlib
import hmac
import logging
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from . import app_settings
from .db import SessionLocal
from .models import AccessToken
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

TOKEN_PREFIX = "steno_"
TOKEN_SHOWN_CHARS = 10
# The last use is written at most once a minute per token, not on every request.
TOKEN_TOUCH_SECONDS = 60
MAX_TOKENS = 50
SCOPE_IMPORT, SCOPE_FULL = "import", "full"
TOKEN_SCOPES = (SCOPE_IMPORT, SCOPE_FULL)
# What an "import" token may do: send a link, follow its analysis, read the templates and the link option.
IMPORT_ROUTES = (
    ("GET", re.compile(r"/templates")),
    ("GET", re.compile(r"/settings/url-import")),
    ("POST", re.compile(r"/imports/url/preview")),
    ("POST", re.compile(r"/imports/url")),
    ("GET", re.compile(r"/jobs/[A-Za-z0-9-]{1,64}")),
)

ERROR_LOGIN_REQUIRED = "Connexion requise"
ERROR_BAD_TOKEN = "Jeton d'accès invalide ou révoqué"
ERROR_TOKEN_SCOPE = "Ce jeton ne permet que d'envoyer des liens à Sténo (portée « Envoyer des liens »)"
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


LOCAL_NAME_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".internal", ".localhost")
ERROR_HOST = "Nom d'hôte refusé : ouvrez Sténo par son adresse (127.0.0.1, localhost ou l'adresse du réseau local)"


def host_allowed(host: str | None) -> bool:
    """Is this Host header a name Sténo is reached by? Refuses public domain names (DNS rebinding).

    A page of attacker.example whose name is pointed at 127.0.0.1 would otherwise talk to Sténo
    as "this computer". Allowed: IP addresses, localhost, single-label names (`web`, `api`, a
    computer's name) and local suffixes (.local, .lan…). Mirrored in frontend/lib/hosts.ts.
    """
    if not host:
        return True  # HTTP/1.0 or an internal client: nothing to rebind
    name = host.strip().lower()
    if name.startswith("["):
        return "]" in name  # an IPv6 literal, with or without its port
    name = name.rsplit(":", 1)[0].rstrip(".")
    if not name:
        return False
    if re.fullmatch(r"[0-9.]+", name):
        return True
    if "." not in name:
        return True
    return name.endswith(LOCAL_NAME_SUFFIXES)


def scope_allows(scope: str, method: str, path: str) -> bool:
    if scope == SCOPE_FULL:
        return True
    return scope == SCOPE_IMPORT and any(method == allowed and pattern.fullmatch(path) for allowed, pattern in IMPORT_ROUTES)


def decide(path: str, *, remote: bool, token: str | None, config: AccessSettings,
           bearer: bool = False, bearer_scope: str | None = None, method: str = "GET") -> Decision:
    """May this request go through? (pure: tested without the HTTP layer)

    `bearer`: the request presents an access token; `bearer_scope`: its scope, None if unknown or revoked.
    """
    if path in PUBLIC_PATHS or path.startswith("/auth/"):
        return Decision(True)
    if remote and not config.password_hash:
        return Decision(False, 403, ERROR_NETWORK_DISABLED)
    if bearer:
        if bearer_scope is None:
            return Decision(False, 401, ERROR_BAD_TOKEN)
        return Decision(True) if scope_allows(bearer_scope, method, path) else Decision(False, 403, ERROR_TOKEN_SCOPE)
    if not config.password_hash:
        return Decision(True)
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


# --- access tokens (feuille de route n° 4, phase 1) -------------------------------------------------


def bearer_token(authorization: str | None) -> str | None:
    """The token of an `Authorization: Bearer …` header ("" for a malformed one: still refused)."""
    if not authorization:
        return None
    scheme, _, value = authorization.strip().partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def token_digest(token: str) -> str:
    # A 256-bit random token needs no slow hash: SHA-256 cannot be reversed nor guessed.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_access_token() -> tuple[str, str, str]:
    """(token shown once, digest stored, prefix shown in the list)."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return token, token_digest(token), token[:TOKEN_SHOWN_CHARS]


def check_access_token(token: str, now: datetime | None = None) -> str | None:
    """The scope of this token, None if unknown or revoked. Notes its last use. Reads the database: call off the event loop."""
    if not token.startswith(TOKEN_PREFIX) or len(token) > 200:
        return None
    now = now or datetime.now(timezone.utc)
    with SessionLocal() as db:
        row = db.scalar(select(AccessToken).where(AccessToken.token_hash == token_digest(token)))
        if row is None:
            return None
        scope = row.scope
        last = row.last_used_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last is None or now - last > timedelta(seconds=TOKEN_TOUCH_SECONDS):
            row.last_used_at = now
            db.commit()
    return scope
