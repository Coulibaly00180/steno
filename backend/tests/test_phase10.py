"""Doubtful words (n°1), summary sources (n°3), actions and decisions (n°5), exports to other tools (n°8)."""
import email
import io
import json
import zipfile
from datetime import date, datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from app import actions, llm, main, notes, transcription, verification, worker
from app.actions import extract as real_extract
from app.models import ActionItem, Entity, EntityMention, Summary, TranscriptSegment
from tests.test_phase4 import add_video
from tests.test_phase7 import MODULES, make_session
from tests.test_transcription import RATE, silence, tone, write_wav
from tests.test_upload import SuccessfulQueue

MEETING_DAY = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)  # a Monday
MEETING = [
    (0.0, 5.0, "Bonjour, point budget avec Claire et Karim."),
    (5.0, 10.0, "Le comité valide le budget marketing."),
    (10.0, 15.0, "Claire enverra le devis à Acme lundi prochain."),
]
SUMMARY = """# Résumé exécutif
Le comité valide le budget marketing [00:00:05].

# Décisions
- Budget marketing validé [00:00:05]

# Actions
- Claire enverra le devis à Acme lundi prochain [00:00:10]
- Non mentionné
"""


@pytest.fixture
def env(monkeypatch, tmp_path):
    session = make_session(tmp_path / "p10.db")
    for module in MODULES:
        monkeypatch.setattr(module, "SessionLocal", session)
    monkeypatch.setattr(main.settings, "data_dir", tmp_path / "data")
    for folder in ("uploads", "audio", "exports"):
        (tmp_path / "data" / folder).mkdir(parents=True)
    monkeypatch.setattr(main.settings, "embedding_model", "test-embed")
    monkeypatch.setattr(main, "Queue", SuccessfulQueue)
    return session


def meeting(session, video_id="v", *, summary=SUMMARY, name="Comité budget.mp4"):
    add_video(session, video_id, MEETING, name=name, created_at=MEETING_DAY)
    with session() as db:
        db.add(Summary(id=f"s-{video_id}", video_id=video_id, content_markdown=summary, model="m", created_at=MEETING_DAY))
        db.commit()


# --- n°1: doubtful words -------------------------------------------------------------------------

def word(text, probability):
    return SimpleNamespace(word=text, probability=probability, start=0, end=0)


def test_doubtful_words_are_located_in_the_line():
    segment = SimpleNamespace(words=[word(" Le", 0.99), word(" parc", 0.95), word(" de", 0.97), word(" Donia", 0.21), word(" de", 0.4)])
    text = "Le parc de Donia de"
    # « de » at 40 % is a function word: never worth checking.
    assert transcription.doubtful_words(segment, text) == [[11, 16, 21]]
    assert transcription.doubtful_words(SimpleNamespace(words=None), text) == []


def test_word_timings_are_asked_only_when_doubts_are_wanted(tmp_path):
    path = tmp_path / "d.wav"
    write_wav(path, np.concatenate([tone(3), silence(1)]))
    options = []

    class Model:
        def transcribe(self, audio, **kwargs):
            options.append(kwargs)
            return iter([SimpleNamespace(start=0.0, end=2.0, text=" Bonjour Karim", words=[word(" Bonjour", 0.9), word(" Karim", 0.3)])]), \
                SimpleNamespace(language="fr")

    doubts = []
    rows, _ = transcription.transcribe_windows(Model(), path, language=None, initial_prompt=None, beam_size=1, doubts=doubts)
    assert rows == [(0.0, 2.0, "Bonjour Karim")] and doubts == [[[8, 13, 30]]]
    assert options[0]["word_timestamps"] is True
    transcription.transcribe_windows(Model(), path, language=None, initial_prompt=None, beam_size=1)
    assert "word_timestamps" not in options[1]
    assert RATE == 16000


def test_doubts_are_shown_and_cleared_by_a_correction(client, env):
    add_video(env, "v", MEETING)
    with env() as db:
        rows = db.query(TranscriptSegment).filter_by(video_id="v").order_by(TranscriptSegment.id).all()
        rows[0].doubts = json.dumps([[27, 32, 35]])
        rows[1].doubts = json.dumps([[3, 9, 20]])
        db.commit()
        first, second = rows[0].id, rows[1].id
    segments = client.get("/videos/v").json()["segments"]
    assert segments[0]["doubts"] == [[27, 32, 35]] and segments[2]["doubts"] == []
    assert client.patch(f"/videos/v/segments/{first}", json={"text": "Bonjour, point budget avec Claire et Karim !"}).json()["doubts"] == []
    client.post("/videos/v/transcript/replace", json={"find": "comité", "replace": "Comité"})
    assert all(segment["doubts"] == [] for segment in client.get("/videos/v").json()["segments"])
    assert second


# --- n°3: summary sources ------------------------------------------------------------------------

def test_statement_lines_skip_headings_and_empty_sections():
    assert [index for index, _ in verification.statement_lines(SUMMARY)] == [1, 4, 7]
    assert verification.cited_clock("- Budget validé [00:01:05]") == 65.0 and verification.cited_clock("pas d'heure") is None


def test_each_line_points_to_its_passage(client, env, monkeypatch):
    meeting(env)
    assert client.get("/videos/v/summaries/s-v/sources").json() == {"status": "indexing", "lines": []}
    worker.index_video("v")
    monkeypatch.setattr(verification, "SUPPORT_MAX_DISTANCE", 0.9)
    calls = []
    real_embed = verification.embed_texts
    monkeypatch.setattr(verification, "embed_texts", lambda texts: calls.append(texts) or real_embed(texts))
    result = client.get("/videos/v/summaries/s-v/sources").json()
    assert result["status"] == "ready"
    lines = {line["line"]: line for line in result["lines"]}
    assert set(lines) == {1, 4, 7}
    assert lines[7]["supported"] and "devis" in lines[7]["excerpt"]
    assert lines[7]["cited_seconds"] == 10.0 and lines[7]["cited_matches"] is True
    # Cached with the summary: a second look asks nothing of the model.
    client.get("/videos/v/summaries/s-v/sources")
    assert len(calls) == 1
    assert client.get("/videos/v/summaries/missing/sources").status_code == 404


def test_a_line_with_no_close_passage_is_to_be_checked(client, env, monkeypatch):
    meeting(env, summary="- Les pistes cyclables seront repeintes en vert fluo dès demain matin")
    worker.index_video("v")
    monkeypatch.setattr(verification, "SUPPORT_MAX_DISTANCE", 0.5)
    line = client.get("/videos/v/summaries/s-v/sources").json()["lines"][0]
    assert line["supported"] is False


# --- n°5: actions and decisions -------------------------------------------------------------------

ANSWER = {"elements": [
    {"type": "decision", "texte": "Valider le budget marketing", "responsable": "Non précisé", "echeance": "", "moment": "00:00:05"},
    {"type": "action", "texte": "Envoyer le devis à Acme", "responsable": "Claire", "echeance": "lundi prochain",
     "echeance_date": "2026-09-28", "moment": "00:00:10"},
    {"type": "action", "texte": "Envoyer le devis à Acme", "responsable": "Claire"},  # duplicate
    {"type": "action", "texte": "Relancer", "echeance_date": "2026-10-01"},  # one word
    {"type": "action", "texte": "Préparer la présentation", "echeance_date": "2026-10-02", "moment": "05:00:00"},
    {"type": "remarque", "texte": "Ambiance détendue"},
]}


def test_items_are_checked(env):
    found = actions.validate(ANSWER["elements"], duration=15.0)
    assert [(f.kind, f.text, f.owner, f.due_text, f.due_date, f.start_seconds) for f in found] == [
        ("decision", "Valider le budget marketing", None, None, None, 5.0),
        ("action", "Envoyer le devis à Acme", "Claire", "lundi prochain", date(2026, 9, 28), 10.0),
        # A date the words do not give is dropped, and so is a moment beyond the end.
        ("action", "Préparer la présentation", None, None, None, None),
    ]


THURSDAY = date(2026, 9, 24)


@pytest.mark.parametrize("words, expected", [
    ("avant la fin du mois", date(2026, 9, 30)),  # the model said 31 October
    ("fin du mois prochain", date(2026, 10, 31)),
    ("lundi prochain", date(2026, 9, 28)), ("jeudi", date(2026, 10, 1)),
    ("demain", date(2026, 9, 25)), ("après-demain", date(2026, 9, 26)),
    ("fin de la semaine", date(2026, 9, 25)), ("la semaine prochaine", date(2026, 9, 28)),
    ("dans deux semaines", date(2026, 10, 8)), ("dans 3 jours", date(2026, 9, 27)),
    ("le 15 octobre", date(2026, 10, 15)), ("le 1er mars", date(2027, 3, 1)), ("15/10", date(2026, 10, 15)),
    ("au plus vite", None), ("", None),
])
def test_usual_deadlines_are_dated_by_the_code(words, expected):
    assert actions.date_from_words(words, THURSDAY) == expected


def test_the_code_date_wins_over_the_model_date():
    found = actions.validate([{"type": "action", "texte": "Relancer le fournisseur", "echeance": "avant la fin du mois",
                               "echeance_date": "2026-10-31"}], duration=60, reference=THURSDAY)
    assert found[0].due_date == date(2026, 9, 30)
    # Words the code cannot date: the model's date is kept.
    found = actions.validate([{"type": "action", "texte": "Livrer la maquette", "echeance": "avant la rentrée",
                               "echeance_date": "2026-11-02"}], duration=60, reference=THURSDAY)
    assert found[0].due_date == date(2026, 11, 2)


def test_function_words_are_never_doubtful():
    segment = SimpleNamespace(words=[word(" Alors", 0.09), word(" on", 0.15), word(" discounters", 0.07), word(",", 0.1)])
    assert transcription.doubtful_words(segment, "Alors on discounters,") == [[9, 20, 7]]


def test_the_extraction_dates_from_the_meeting_day(env, monkeypatch):
    monkeypatch.setattr(actions, "extract", real_extract)
    meeting(env)
    prompts = []

    def completion(prompt, **options):
        prompts.append((prompt, options))
        return json.dumps(ANSWER), "stop"

    monkeypatch.setattr(llm, "chat_completion", completion)
    assert worker.extract_video_actions("v") == 3
    prompt, options = prompts[0]
    assert "lundi 2026-09-21" in prompt and "Claire enverra le devis" in prompt and options["json_schema"] == actions.SCHEMA
    # Cut by the token limit: the items written whole are kept.
    monkeypatch.setattr(llm, "chat_completion", lambda prompt, **options: ('{"elements": [{"type": "action", "texte": "Envoyer le devis"}, {"type": "act', "length"))
    with env() as db:
        from app.models import Video
        assert [f.text for f in actions.extract(db.get(Video, "v"), SUMMARY)] == ["Envoyer le devis"]


def test_a_new_summary_keeps_what_the_user_touched(client, env, monkeypatch):
    meeting(env)
    found = actions.validate(ANSWER["elements"], duration=15.0)
    monkeypatch.setattr(actions, "extract", lambda video, summary: found)
    worker.extract_video_actions("v")
    items = {item["text"]: item for item in client.get("/videos/v/actions").json()}
    devis = items["Envoyer le devis à Acme"]
    assert client.patch(f"/actions/{devis['id']}", json={"status": "done"}).json()["status"] == "done"
    decision = items["Valider le budget marketing"]
    assert client.patch(f"/actions/{decision['id']}", json={"owner": "Comité"}).json()["edited"] is True
    manual = client.post("/videos/v/actions", json={"text": "Réserver la salle", "owner": "Karim", "due_date": "2026-09-30"}).json()
    assert (manual["source"], manual["kind"], manual["status"]) == ("manual", "action", "open")
    # A new summary finds only one item: untouched ones are replaced, the others stay.
    monkeypatch.setattr(actions, "extract", lambda video, summary: actions.validate(
        [{"type": "action", "texte": "Envoyer le devis à Acme", "responsable": "Claire"},
         {"type": "action", "texte": "Mettre à jour le planning"}], duration=15.0))
    worker.extract_video_actions("v")
    texts = sorted(item["text"] for item in client.get("/videos/v/actions").json())
    assert texts == ["Envoyer le devis à Acme", "Mettre à jour le planning", "Réserver la salle", "Valider le budget marketing"]
    assert client.patch(f"/actions/{manual['id']}", json={"text": ""}).status_code == 422
    assert client.patch(f"/actions/{manual['id']}", json={"status": "later"}).status_code == 422
    assert client.delete(f"/actions/{manual['id']}").json() == {"deleted": True}
    assert client.delete(f"/actions/{manual['id']}").status_code == 404


def test_the_library_lists_and_exports_its_actions(client, env, monkeypatch):
    meeting(env)
    monkeypatch.setattr(actions, "extract", lambda video, summary: actions.validate(ANSWER["elements"], duration=15.0))
    worker.extract_video_actions("v")
    listing = client.get("/actions?kind=action").json()
    assert [item["text"] for item in listing["items"]][0] == "Envoyer le devis à Acme"  # dated first
    assert listing["owners"] == ["Claire"] and listing["items"][0]["video_title"] == "Comité budget.mp4"
    assert [item["text"] for item in client.get("/actions?q=présentation").json()["items"]] == ["Préparer la présentation"]
    assert client.get("/actions?status=later").status_code == 422
    csv_text = client.get("/actions/export.csv?video_id=v").content.decode("utf-8")
    assert csv_text.startswith("﻿Type;Élément;Responsable")
    assert "Action;Envoyer le devis à Acme;Claire;lundi prochain;2026-09-28;À faire;Comité budget.mp4;00:00:10" in csv_text
    ics = client.get("/actions/export.ics").text
    assert ics.count("BEGIN:VEVENT") == 1 and "DTSTART;VALUE=DATE:20260928" in ics
    assert "SUMMARY:Envoyer le devis à Acme (Claire)" in ics and ics.endswith("END:VCALENDAR\r\n")


def test_a_failed_extraction_never_fails_the_video(env, monkeypatch):
    meeting(env)

    def broken(video, summary):
        raise ConnectionError("Ollama down")

    monkeypatch.setattr(actions, "extract", broken)
    with env() as db:
        db.add(main.ProcessingJob(id="j", video_id="v", kind="SUMMARY", stage="SUMMARIZING_FINAL", status="RUNNING", progress=90))
        db.commit()
    worker._actions_after_summary("j", "v")
    with env() as db:
        assert db.get(main.ProcessingJob, "j").stage == "EXTRACTING_ACTIONS"


def test_ics_lines_are_folded():
    item = ActionItem(id="a", video_id="v", kind="action", text="Préparer " + "la très longue présentation " * 6, owner=None,
                      due_date=date(2026, 10, 1), status="open", position=0)
    ics = actions.to_ics([(item, "Réunion")])
    assert all(len(line.encode("utf-8")) <= 75 for line in ics.split("\r\n"))


# --- n°8: exports -------------------------------------------------------------------------------

def with_entities(session):
    with session() as db:
        claire = Entity(kind="person", name="Claire", key="claire")
        acme = Entity(kind="organization", name="Acme", key="acme")
        db.add_all([claire, acme])
        db.flush()
        db.add(EntityMention(entity_id=claire.id, video_id="v", start_seconds=10.0, context="Claire enverra le devis à Acme lundi prochain."))
        db.add(EntityMention(entity_id=acme.id, video_id="v", start_seconds=10.0, context="Claire enverra le devis à Acme lundi prochain."))
        db.commit()


def test_a_video_note_for_obsidian(client, env, monkeypatch):
    meeting(env)
    with_entities(env)
    monkeypatch.setattr(actions, "extract", lambda video, summary: actions.validate(ANSWER["elements"], duration=15.0))
    worker.extract_video_actions("v")
    response = client.get("/videos/v/note.md")
    assert "Comit" in response.headers["content-disposition"]
    note = response.text
    assert note.startswith("---\ntitre: \"Comité budget\"\ndate: 2026-09-21\n")
    assert 'personnes: ["[[Claire]]"]' in note and "tags: [\"steno\"]" in note
    assert "- [ ] Envoyer le devis à Acme — **Claire** · 📅 2026-09-28" in note
    assert "## Décisions\n\n- Valider le budget marketing" in note and "[[Acme]]" in note
    assert "## Transcription" not in note and "## Transcription" in client.get("/videos/v/note.md?transcript=true").text
    # The summary's headings sit under « ## Compte-rendu ».
    assert "## Compte-rendu\n\n### Résumé exécutif" in note and "\n# Décisions" not in note


def test_the_library_as_an_obsidian_folder(client, env):
    meeting(env)
    meeting(env, "w", name="Comité budget.mp4")  # same title: the second note gets its date
    with_entities(env)
    response = client.get("/library/export/obsidian.zip")
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = sorted(archive.namelist())
        assert "Sténo/Vidéos/Comité budget.md" in names and "Sténo/Vidéos/Comité budget (2026-09-21).md" in names
        assert "Sténo/Personnes/Claire.md" in names and "Sténo/Organisations/Acme.md" in names
        claire = archive.read("Sténo/Personnes/Claire.md").decode("utf-8")
        assert "## [[Comité budget]]" in claire and "00:00:10 — Claire enverra le devis" in claire


def test_an_email_draft_of_the_report(client, env, monkeypatch):
    meeting(env)
    monkeypatch.setattr(actions, "extract", lambda video, summary: actions.validate(ANSWER["elements"], duration=15.0))
    worker.extract_video_actions("v")
    response = client.get("/videos/v/email.eml")
    assert response.headers["content-type"] == "message/rfc822"
    message = email.message_from_bytes(response.content, policy=email.policy.default)
    assert message["Subject"] == "Compte-rendu : Comité budget" and message["X-Unsent"] == "1"
    text = message.get_body(preferencelist=("plain",)).get_content()
    assert "- [ ] Envoyer le devis à Acme (Claire — 28/09/2026)" in text and "(00:00:05)" in text
    assert "Résumé exécutif :" in text and "# " not in text
    assert "<h3>Résumé exécutif</h3>" in message.get_body(preferencelist=("html",)).get_content()
    assert message.get_body(preferencelist=("html",)) is not None
    assert notes.safe_name('Budget: "Q3" / final?') == "Budget Q3 final"
