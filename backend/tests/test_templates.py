from types import SimpleNamespace

from app import main
from app.models import SummaryTemplate


class SuccessfulQueue:
    def __init__(self, *args, **kwargs):
        pass

    def enqueue(self, *args, **kwargs):
        return SimpleNamespace(id="rq-template-job")


def template_payload(name="Produit"):
    return {"name": name, "description": "Résumé produit", "prompt": "Résume les points clés."}


def test_template_name_and_prompt_are_validated(client, upload_environment):
    too_long = client.post("/templates", json=template_payload("x" * 121))
    assert too_long.status_code == 422

    blank_name = client.post("/templates", json=template_payload("   "))
    assert blank_name.status_code == 422

    blank_prompt = client.post("/templates", json={**template_payload(), "prompt": "  "})
    assert blank_prompt.status_code == 422


def test_duplicate_template_name_returns_409(client, upload_environment):
    first = client.post("/templates", json=template_payload("Unique"))
    duplicate = client.post("/templates", json=template_payload("Unique"))

    assert first.status_code == 200
    assert duplicate.status_code == 409
    assert duplicate.json() == {"detail": "Un template portant ce nom existe déjà"}


def test_upload_rejects_unknown_template_before_storing_file(client, upload_environment):
    session, uploads_dir = upload_environment

    response = client.post(
        "/videos",
        files={"file": ("clip.mp4", b"media", "video/mp4")},
        data={"template_id": "does-not-exist"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Template introuvable"}
    with session() as db:
        assert db.query(SummaryTemplate).count() == 0
    assert list(uploads_dir.iterdir()) == []


def test_upload_accepts_existing_template(client, upload_environment, monkeypatch):
    session, _ = upload_environment
    monkeypatch.setattr(main, "Queue", SuccessfulQueue)
    template = client.post("/templates", json=template_payload("Existing"))
    template_id = template.json()["id"]

    response = client.post(
        "/videos",
        files={"file": ("clip.mp4", b"media", "video/mp4")},
        data={"template_id": template_id},
    )

    assert response.status_code == 200
    with session() as db:
        job = db.query(main.ProcessingJob).one()
        assert job.template_id == template_id
