"""Access from the network (n°15): password, sessions, HTTPS proxy; first launch (n°19)."""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from redis import Redis
from sqlalchemy import delete, func, select

from .. import app_settings, auth
from ..config import settings
from ..db import SessionLocal
from ..models import AccessToken, Video
from ..schemas import AccessSettings, AccessTokenIn, LoginIn, OnboardingSettings, PasswordIn

router = APIRouter()


# --- Access: password and network (n°15) ------------------------------------------------------------

HTTPS_ROOT_CERTIFICATE = Path("https") / "caddy" / "pki" / "authorities" / "local" / "root.crt"


def _is_remote(request: Request) -> bool:
    return request.headers.get(auth.REMOTE_HEADER) == "1"


def _client_id(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "local")


def _set_session(response: Response, request: Request, config: AccessSettings) -> None:
    response.set_cookie(
        auth.COOKIE, auth.make_token(config), max_age=auth.SESSION_SECONDS, httponly=True, samesite="lax",
        secure=request.headers.get("x-forwarded-proto") == "https", path="/",
    )


def _access_out(config: AccessSettings, request: Request) -> dict:
    remote = _is_remote(request)
    return {
        "password_set": bool(config.password_hash),
        "require_local": config.require_local,
        "remote": remote,
        "required": bool(config.password_hash) and (remote or config.require_local),
        "authenticated": auth.valid_token(request.cookies.get(auth.COOKIE), config),
        "network_blocked": remote and not config.password_hash,
    }


@router.get("/auth/status")
def auth_status(request: Request):
    return _access_out(auth.load(), request)


@router.post("/auth/login")
def login(payload: LoginIn, request: Request, response: Response):
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
    client = _client_id(request)
    if auth.login_limited(redis, client):
        raise HTTPException(429, "Trop de tentatives : réessayez dans 15 minutes")
    config = auth.load()
    if not config.password_hash:
        raise HTTPException(409, "Aucun mot de passe n'est défini")
    if not auth.verify_password(payload.password, config.password_hash):
        raise HTTPException(401, "Mot de passe incorrect")
    auth.reset_attempts(redis, client)
    _set_session(response, request, config)
    return _access_out(config, request) | {"authenticated": True}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE, path="/")
    return {"authenticated": False}


@router.put("/auth/password")
def set_password(payload: PasswordIn, request: Request, response: Response):
    """Set, change or remove the password.

    On this computer (and unless the password is asked here too), no current
    password is needed: whoever sits at the computer owns Sténo. From the
    network, the current password is required.
    """
    remote = _is_remote(request)
    with SessionLocal() as db:
        config = app_settings.load(db, app_settings.ACCESS, AccessSettings)
        if config.password_hash:
            trusted = not remote and not config.require_local
            if not trusted and not auth.verify_password(payload.current_password or "", config.password_hash):
                raise HTTPException(403, "Mot de passe actuel incorrect")
        elif remote:
            raise HTTPException(403, auth.ERROR_NETWORK_DISABLED)
        if "new_password" in payload.model_fields_set:
            config.password_hash = auth.hash_password(payload.new_password) if payload.new_password else None
            # Every session opened with the previous password closes, and every access token is revoked:
            # one created from a stolen session must not outlive the reset.
            config.version += 1
            db.execute(delete(AccessToken))
            if not config.password_hash:
                config.require_local = False
        if payload.require_local is not None:
            if payload.require_local and not config.password_hash:
                raise HTTPException(409, "Définissez d'abord un mot de passe")
            config.require_local = payload.require_local
        config.secret = config.secret or auth.new_secret()
        auth.save(db, config)
        db.commit()
    if config.password_hash:
        # Whoever set it stays signed in.
        _set_session(response, request, config)
    else:
        response.delete_cookie(auth.COOKIE, path="/")
    result = _access_out(config, request)
    return result | {"authenticated": bool(config.password_hash)}


@router.get("/network")
def network_info():
    """Access from the local network: the HTTPS proxy's address, and whether it has started."""
    address = settings.lan_address.strip() or None
    return {
        "https_ready": (settings.data_dir / HTTPS_ROOT_CERTIFICATE).is_file(),
        "address": address,
        "port": settings.https_port,
        "url": f"https://{address}:{settings.https_port}" if address else None,
    }


@router.get("/network/certificate")
def network_certificate():
    """The local certificate authority of the HTTPS proxy: install it once to remove the browser warning."""
    path = settings.data_dir / HTTPS_ROOT_CERTIFICATE
    if not path.is_file():
        raise HTTPException(404, "Le proxy HTTPS n'a pas encore démarré")
    return FileResponse(path, media_type="application/x-x509-ca-cert", filename="steno-autorite-locale.crt")


# --- Access tokens: extension and scripts (feuille de route n° 4, phase 1) ---------------------------


def _no_token(request: Request) -> None:
    """Tokens are managed from a session or this computer, never with a token: a stolen one cannot mint others."""
    if auth.bearer_token(request.headers.get("authorization")) is not None:
        raise HTTPException(403, "Un jeton d'accès ne peut pas gérer les jetons : ouvrez Paramètres › Accès et sécurité")


def _token_out(row: AccessToken) -> dict:
    return {"id": row.id, "name": row.name, "prefix": row.prefix, "scope": row.scope, "created_at": row.created_at,
            "last_used_at": row.last_used_at}


@router.get("/access/tokens")
def list_tokens(request: Request):
    _no_token(request)
    with SessionLocal() as db:
        return [_token_out(row) for row in db.scalars(select(AccessToken).order_by(AccessToken.created_at.desc(), AccessToken.id.desc()))]


@router.post("/access/tokens", status_code=201)
def create_token(payload: AccessTokenIn, request: Request):
    """A new token, shown in full this once: only its SHA-256 is kept."""
    _no_token(request)
    token, digest, prefix = auth.new_access_token()
    with SessionLocal() as db:
        if (db.scalar(select(func.count()).select_from(AccessToken)) or 0) >= auth.MAX_TOKENS:
            raise HTTPException(409, f"{auth.MAX_TOKENS} jetons au plus : révoquez ceux qui ne servent plus")
        row = AccessToken(name=payload.name, token_hash=digest, prefix=prefix, scope=payload.scope)
        db.add(row)
        db.commit()
        db.refresh(row)
        return _token_out(row) | {"token": token}


@router.delete("/access/tokens/{token_id}", status_code=204)
def revoke_token(token_id: int, request: Request):
    """Revoked at once: the next request carrying it is refused (tokens are never cached)."""
    _no_token(request)
    with SessionLocal() as db:
        row = db.get(AccessToken, token_id)
        if row is None:
            raise HTTPException(404, "Jeton introuvable")
        db.delete(row)
        db.commit()
    return Response(status_code=204)


# --- First launch (n°19) ---------------------------------------------------------------------------


@router.get("/settings/onboarding")
def get_onboarding():
    """Done once finished or skipped, or as soon as the library has a video (an existing installation)."""
    with SessionLocal() as db:
        state = app_settings.load(db, app_settings.ONBOARDING, OnboardingSettings)
        has_videos = db.scalar(select(Video.id).limit(1)) is not None
    return {"done": state.done or has_videos}


@router.put("/settings/onboarding")
def put_onboarding(payload: OnboardingSettings):
    with SessionLocal() as db:
        app_settings.save(db, app_settings.ONBOARDING, payload)
        db.commit()
    return get_onboarding()
