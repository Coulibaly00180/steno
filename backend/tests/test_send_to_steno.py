"""Access tokens for the browser extension and scripts (feuille de route n° 4, phase 1)."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import auth, main
from app.models import AccessToken, ProcessingJob, Video
from app.schemas import AccessSettings
from tests.test_phase11 import REMOTE, env, guarded  # noqa: F401  (fixtures reused)


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("password, require_local, remote, scope, expected", [
    (False, False, False, "import", 200),   # this computer, no password: a valid token changes nothing
    (False, False, False, None, 401),       # ...but a revoked one is refused, so the extension can tell
    (False, False, True, "full", 403),      # a token never opens a network left closed
    (True, False, True, "import", 200),     # the network, with a token instead of a session
    (True, False, True, None, 401),
    (True, True, False, "import", 200),     # the password asked here too
    (True, True, False, None, 401),
])
def test_who_may_go_through_with_a_token(password, require_local, remote, scope, expected):
    config = AccessSettings(password_hash="h" if password else None, secret="s", require_local=require_local)
    decision = auth.decide("/imports/url", remote=remote, token=None, config=config, bearer=True, bearer_scope=scope, method="POST")
    assert (decision.status if not decision.allowed else 200) == expected
    # Public paths stay public, token or not.
    assert auth.decide("/auth/status", remote=True, token=None, config=config, bearer=True, bearer_scope=None).allowed


@pytest.mark.parametrize("method, path, allowed", [
    ("POST", "/imports/url", True), ("POST", "/imports/url/preview", True), ("GET", "/templates", True),
    ("GET", "/settings/url-import", True), ("GET", "/jobs/b2d1cc25-87d6-4aae-8ae3-ffc76826a929", True),
    ("PUT", "/settings/url-import", False), ("GET", "/tags", False), ("DELETE", "/videos/x", False),
    ("GET", "/library/export/obsidian.zip", False), ("GET", "/jobs/x/events", False), ("POST", "/templates", False),
    ("GET", "/templates/../videos", False), ("POST", "/imports/url/", False),
])
def test_an_import_token_only_sends_links(method, path, allowed):
    config = AccessSettings(password_hash="h", secret="s")
    decision = auth.decide(path, remote=True, token=None, config=config, bearer=True, bearer_scope="import", method=method)
    assert decision.allowed == allowed
    if not allowed:
        assert decision.status == 403 and decision.detail == auth.ERROR_TOKEN_SCOPE
    assert auth.decide(path, remote=True, token=None, config=config, bearer=True, bearer_scope="full", method=method).allowed
    assert not auth.decide(path, remote=True, token=None, config=config, bearer=True, bearer_scope="autre", method=method).allowed


@pytest.mark.parametrize("header, expected", [
    (None, None), ("Bearer steno_abc", "steno_abc"), ("bearer  steno_abc ", "steno_abc"), ("Basic dXNlcg==", ""), ("Bearer", ""),
])
def test_the_bearer_header_is_read(header, expected):
    assert auth.bearer_token(header) == expected


def test_a_token_is_random_and_only_its_digest_is_kept():
    token, digest, prefix = auth.new_access_token()
    other, _, _ = auth.new_access_token()
    assert token.startswith("steno_") and len(token) > 40 and token != other
    assert digest == auth.token_digest(token) and len(digest) == 64 and token not in digest
    assert prefix == token[:10]


def test_tokens_are_created_used_and_revoked(client, env, guarded):  # noqa: F811
    client.put("/auth/password", json={"new_password": "un mot de passe"})
    phone = TestClient(main.app)  # another device of the network, without session
    assert phone.get("/tags", headers=REMOTE).status_code == 401

    created = client.post("/access/tokens", json={"name": "  Script de sauvegarde  ", "scope": "full"})
    assert created.status_code == 201
    body = created.json()
    token = body["token"]
    assert body["name"] == "Script de sauvegarde" and body["prefix"] == token[:10] and body["last_used_at"] is None
    assert body["scope"] == "full"
    with env() as db:
        row = db.get(AccessToken, body["id"])
        assert row.token_hash == auth.token_digest(token) and token not in (row.token_hash, row.prefix, row.name)

    # The list never shows the token again.
    listed = client.get("/access/tokens").json()
    assert [item["id"] for item in listed] == [body["id"]] and "token" not in listed[0]

    assert phone.get("/tags", headers=REMOTE | bearer(token)).status_code == 200
    assert client.get("/access/tokens").json()[0]["last_used_at"] is not None
    assert phone.get("/tags", headers=REMOTE | bearer(token + "x")).status_code == 401

    # Revoked: refused at the very next request, with a message the extension shows.
    assert client.delete(f"/access/tokens/{body['id']}").status_code == 204
    refused = phone.get("/tags", headers=REMOTE | bearer(token))
    assert refused.status_code == 401 and refused.json()["detail"] == auth.ERROR_BAD_TOKEN
    assert client.delete(f"/access/tokens/{body['id']}").status_code == 404
    assert client.get("/access/tokens").json() == []


def test_a_token_cannot_manage_tokens(client, env, guarded):  # noqa: F811
    client.put("/auth/password", json={"new_password": "un mot de passe"})
    token = client.post("/access/tokens", json={"name": "Script", "scope": "full"}).json()
    headers = REMOTE | bearer(token["token"])
    assert client.get("/tags", headers=headers).status_code == 200
    for response in (
        client.get("/access/tokens", headers=headers),
        client.post("/access/tokens", json={"name": "Autre"}, headers=headers),
        client.delete(f"/access/tokens/{token['id']}", headers=headers),
    ):
        assert response.status_code == 403 and "ne peut pas gérer les jetons" in response.json()["detail"]
    # Nor change the password: from the network the current one is still asked.
    assert client.put("/auth/password", json={"new_password": "pris par le jeton"}, headers=headers).status_code == 403
    assert len(client.get("/access/tokens").json()) == 1


def test_changing_or_removing_the_password_revokes_every_token(client, env, guarded):  # noqa: F811
    client.put("/auth/password", json={"new_password": "un mot de passe"})
    first = client.post("/access/tokens", json={"name": "A", "scope": "full"}).json()["token"]
    assert client.get("/tags", headers=REMOTE | bearer(first)).status_code == 200
    client.put("/auth/password", json={"new_password": "un autre mot de passe"})
    assert client.get("/tags", headers=REMOTE | bearer(first)).status_code == 401
    assert client.get("/access/tokens").json() == []
    second = client.post("/access/tokens", json={"name": "B", "scope": "full"}).json()["token"]
    client.put("/auth/password", json={"new_password": None})
    client.put("/auth/password", json={"new_password": "un troisième mot de passe"})
    assert client.get("/tags", headers=REMOTE | bearer(second)).status_code == 401
    # Only "require_local" changed: the tokens stay.
    third = client.post("/access/tokens", json={"name": "C", "scope": "full"}).json()["token"]
    client.put("/auth/password", json={"require_local": True})
    assert client.get("/tags", headers=REMOTE | bearer(third)).status_code == 200


def test_an_import_token_is_refused_elsewhere(client, env, guarded):  # noqa: F811
    client.put("/auth/password", json={"new_password": "un mot de passe"})
    created = client.post("/access/tokens", json={"name": "Extension"}).json()
    assert created["scope"] == "import"
    headers = REMOTE | bearer(created["token"])
    assert client.get("/templates", headers=headers).status_code == 200
    refused = client.get("/tags", headers=headers)
    assert refused.status_code == 403 and refused.json()["detail"] == auth.ERROR_TOKEN_SCOPE
    assert client.put("/settings/url-import", json={"platforms": True}, headers=headers).status_code == 403
    assert client.post("/access/tokens", json={"name": "x", "scope": "admin"}).status_code == 422


def test_a_token_does_not_open_a_closed_network(client, env, guarded):  # noqa: F811
    token = client.post("/access/tokens", json={"name": "Extension", "scope": "full"}).json()["token"]
    response = client.get("/tags", headers=REMOTE | bearer(token))
    assert response.status_code == 403 and "mot de passe" in response.json()["detail"]
    # On this computer, without password, the token is checked but changes nothing.
    assert client.get("/tags", headers=bearer(token)).status_code == 200
    assert client.get("/tags", headers=bearer("steno_inconnu")).status_code == 401
    assert client.get("/tags", headers={"Authorization": "Basic dXNlcjpwdw=="}).status_code == 401


def test_the_last_use_is_written_once_a_minute(env):  # noqa: F811
    token, digest, prefix = auth.new_access_token()
    with env() as db:
        db.add(AccessToken(name="t", token_hash=digest, prefix=prefix))
        db.commit()
    start = datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc)
    assert auth.check_access_token(token, now=start) == "import"
    assert auth.check_access_token(token, now=start + timedelta(seconds=30)) == "import"
    with env() as db:
        assert db.query(AccessToken).one().last_used_at.replace(tzinfo=timezone.utc) == start
    assert auth.check_access_token(token, now=start + timedelta(seconds=61))
    with env() as db:
        assert db.query(AccessToken).one().last_used_at.replace(tzinfo=timezone.utc) == start + timedelta(seconds=61)
    assert not auth.check_access_token("pas_un_jeton") and not auth.check_access_token("steno_" + "a" * 300)


def test_tokens_are_limited(client, env, guarded, monkeypatch):  # noqa: F811
    monkeypatch.setattr(auth, "MAX_TOKENS", 2)
    for name in ("a", "b"):
        assert client.post("/access/tokens", json={"name": name}).status_code == 201
    assert client.post("/access/tokens", json={"name": "c"}).status_code == 409
    assert client.post("/access/tokens", json={"name": "   "}).status_code == 422


def test_the_extension_queues_a_link_from_the_network(client, env, guarded, monkeypatch):  # noqa: F811
    """What the extension does on another computer: POST /imports/url with its token."""
    client.put("/auth/password", json={"new_password": "un mot de passe"})
    token = client.post("/access/tokens", json={"name": "Extension Edge"}).json()["token"]
    monkeypatch.setattr(main.url_import, "check_url", lambda url: url.strip())
    page = "https://www.youtube.com/watch?v=abc"
    response = TestClient(main.app).post(
        "/imports/url", json={"url": page, "title": "Séminaire, jour 3", "diarize": True, "num_speakers": 3},
        headers=REMOTE | bearer(token),
    )
    assert response.status_code == 200
    job = response.json()
    with env() as db:
        video = db.get(Video, job["video_id"])
        assert (video.source_url, video.original_filename) == (page, "Séminaire, jour 3")
        assert db.get(ProcessingJob, job["id"]).status == "QUEUED"


@pytest.mark.parametrize("host, allowed", [
    ("127.0.0.1:3000", True), ("localhost:3000", True), ("192.168.1.20:8443", True), ("[::1]:8000", True), ("api:8000", True),
    ("web", True), ("pc-salon.local", True), ("nas.home.arpa:8443", True), (None, True),
    ("attacker.example", False), ("evil.com:3000", False), ("127.0.0.1.nip.io", False), ("steno.example.org.", False), (":80", False),
])
def test_public_host_names_are_refused(host, allowed):
    assert auth.host_allowed(host) == allowed


def test_a_rebound_page_cannot_mint_a_token(client, env, guarded):  # noqa: F811
    """DNS rebinding: attacker.example pointed at 127.0.0.1 would pass as "this computer"."""
    response = client.post("/access/tokens", json={"name": "porte dérobée"}, headers={"Host": "attacker.example:3000"})
    assert response.status_code == 421 and response.json()["detail"] == auth.ERROR_HOST
    assert client.get("/health", headers={"Host": "attacker.example"}).status_code == 421
    assert client.get("/access/tokens").json() == []


def test_the_host_seen_by_the_frontend_proxy_is_checked_too(client, env):  # noqa: F811
    assert client.get("/health", headers={"Host": "api:8000", "X-Forwarded-Host": "attacker.example"}).status_code == 421
    assert client.get("/health", headers={"Host": "api:8000", "X-Forwarded-Host": "127.0.0.1:3000"}).status_code == 200
