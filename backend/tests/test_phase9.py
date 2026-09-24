"""Entities (n°16), hybrid search and collections (n°17), conversations (n°18), models page (n°20)."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app import ai_models, entities, live, llm, main, worker
from app.models import Entity, EntityMention, LibraryConversation, LibraryMessage, VideoChatMessage, VideoEntityState
from tests.test_phase4 import add_video, sse_events
from tests.test_phase7 import MODULES, make_media, make_session
from tests.test_upload import SuccessfulQueue


@pytest.fixture
def env(monkeypatch, tmp_path):
    session = make_session(tmp_path / "p9.db")
    for module in (*MODULES, ai_models):
        monkeypatch.setattr(module, "SessionLocal", session)
    monkeypatch.setattr(main.settings, "data_dir", tmp_path / "data")
    for folder in ("uploads", "audio", "exports"):
        (tmp_path / "data" / folder).mkdir(parents=True)
    monkeypatch.setattr(main.settings, "embedding_model", "test-embed")
    monkeypatch.setattr(main, "Queue", SuccessfulQueue)
    ai_models.forget()
    yield session
    ai_models.forget()


LATER = datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc)
MEETING = [
    (0.0, 5.0, "Bonjour, je suis Claire Martin, de la société Acme."),
    (5.0, 10.0, "M. Karim Benali présente le budget."),
    (10.0, 15.0, "Rendez-vous à Lyon le 15 octobre."),
]


def fake_extraction(monkeypatch, per_block=None):
    """The LLM names what the test says, with a time; invented items are left for the checks to drop."""
    def extract(text, vocabulary=()):
        return per_block if per_block is not None else [
            {"nom": "Claire Martin", "type": "personne", "moment": "00:00:00"},
            {"nom": "Acme", "type": "organisation", "moment": "00:00:00"},
            {"nom": "Karim Benali", "type": "personne", "moment": "00:00:05"},
            {"nom": "Lyon", "type": "lieu", "moment": "00:00:10"},
            {"nom": "15 octobre", "type": "date", "moment": "00:00:10"},
            {"nom": "Napoléon", "type": "personne", "moment": "00:00:05"},  # not in the text
            {"nom": "Acme", "type": "organisation", "moment": "02:00:00"},  # outside the block
            {"nom": "le directeur", "type": "fonction", "moment": "00:00:05"},  # unknown type
        ]
    monkeypatch.setattr(llm, "extract_entities", extract)


# --- n°16: entities ----------------------------------------------------------------------------

def test_names_are_folded_so_spellings_meet():
    assert entities.entity_key("person", "M. Karim Benali") == entities.entity_key("person", "karim BENALI") == "karim benali"
    assert entities.entity_key("organization", "Société Générale") == "societe generale"
    assert entities.parse_moment("00:01:05") == 65.0 and entities.parse_moment("[01:05]") == 65.0
    assert entities.parse_moment("99:99") is None


def test_a_name_far_into_a_block_is_found_and_a_part_of_a_word_is_not():
    block = "[00:00:00] " + "Introduction. " * 40 + "\n[00:03:00] Le rendez-vous est à Lyon, rue de la Lyonnaise."
    found = entities.validate([
        {"nom": "Lyon", "type": "lieu", "moment": "00:03:00"},
        {"nom": "Lyonn", "type": "lieu", "moment": "00:03:00"},
    ], block)
    assert [item.name for item in found] == ["Lyon"]


@pytest.mark.parametrize("kind, name, kept", [
    ("date", "15 octobre", True), ("date", "lundi prochain", True), ("date", "fin juin 2026", True),
    ("date", "le trimestre prochain", True), ("date", "12/03", True), ("date", "Black Friday", True),
    # What a laptop review returned as dates or places.
    ("date", "1200 euros", False), ("date", "150 fps", False), ("date", "20%", False),
    ("place", "144 hertz", False), ("place", "16 pouces", False), ("place", "Lyon", True),
    ("person", "Intervenant 1", False), ("person", "Claire Martin", True), ("organization", "MSI", True),
])
def test_implausible_entities_are_dropped(kind, name, kept):
    assert entities.plausible(kind, name) is kept


def test_a_mention_is_moved_to_the_line_naming_it():
    starts = [0.0, 5.0, 10.0, 60.0]
    texts = ["On compare les prix.", "Chez Amazon, il est à 1200 euros.", "La sélection est en description.", "Amazon encore."]
    # The model said 00:00:10: the line naming Amazon is at 00:00:05.
    assert entities.locate(starts, texts, 10.0, "Amazon") == (5.0, "Chez Amazon, il est à 1200 euros.")
    # Nothing within 30 s: the line at the given time is kept.
    assert entities.locate(starts, texts, 0.0, "Fnac") == (0.0, "On compare les prix.")


def test_only_checked_entities_are_kept(env, monkeypatch):
    fake_extraction(monkeypatch)
    add_video(env, "v", MEETING, name="Réunion.mp4")
    assert worker.extract_video_entities("v") == 5
    with env() as db:
        names = sorted((e.kind, e.name) for e in db.query(Entity))
        assert names == [("date", "15 octobre"), ("organization", "Acme"), ("person", "Claire Martin"), ("person", "Karim Benali"), ("place", "Lyon")]
        state = db.get(VideoEntityState, "v")
        assert (state.status, state.mentions) == ("READY", 5)
        mention = db.query(EntityMention).join(Entity).filter(Entity.name == "Lyon").one()
        assert (mention.start_seconds, mention.context) == (10.0, "Rendez-vous à Lyon le 15 octobre.")


def test_an_entity_page_gathers_every_video(client, env, monkeypatch):
    fake_extraction(monkeypatch)
    add_video(env, "a", MEETING, name="Réunion A.mp4")
    add_video(env, "b", [(0.0, 5.0, "Claire Martin valide le devis d'Acme.")], name="Réunion B.mp4", created_at=LATER)
    worker.extract_video_entities("a")
    fake_extraction(monkeypatch, [{"nom": "claire martin", "type": "personne", "moment": "00:00:00"}])
    worker.extract_video_entities("b")
    listing = client.get("/entities").json()
    claire = next(item for item in listing if item["name"] == "Claire Martin")
    assert (listing[0]["id"], claire["videos"], claire["kind_label"]) == (claire["id"], 2, "Personne")
    assert [item["name"] for item in client.get("/entities?kind=place").json()] == ["Lyon"]
    assert [item["name"] for item in client.get("/entities?q=benali").json()] == ["Karim Benali"]
    page = client.get(f"/entities/{claire['id']}").json()
    assert (page["videos"], page["mentions"]) == (2, 2)
    assert [(video["title"], [m["context"] for m in video["mentions"]]) for video in page["appearances"]] == [
        ("Réunion B.mp4", ["Claire Martin valide le devis d'Acme."]),
        ("Réunion A.mp4", ["Bonjour, je suis Claire Martin, de la société Acme."]),
    ]
    # The library filter and the questions use the same list.
    assert sorted(v["id"] for v in client.get(f"/videos?entity={claire['id']}").json()) == ["a", "b"]
    chips = client.get("/videos/a/entities").json()
    assert chips["status"] == "READY" and {chip["name"] for chip in chips["entities"]} >= {"Claire Martin", "Lyon"}


def test_rename_hide_and_merge(client, env, monkeypatch):
    fake_extraction(monkeypatch, [
        {"nom": "Karim Benali", "type": "personne", "moment": "00:00:05"},
        {"nom": "Benali", "type": "personne", "moment": "00:00:05"},
        {"nom": "Lyon", "type": "lieu", "moment": "00:00:10"},
    ])
    add_video(env, "v", [*MEETING, (15.0, 20.0, "Benali confirme.")])
    worker.extract_video_entities("v")
    ids = {item["name"]: item["id"] for item in client.get("/entities").json()}
    assert client.patch(f"/entities/{ids['Lyon']}", json={"name": "Lyon (Rhône)"}).json()["name"] == "Lyon (Rhône)"
    assert client.patch(f"/entities/{ids['Lyon']}", json={"hidden": True}).json()["hidden"] is True
    assert "Lyon (Rhône)" not in [item["name"] for item in client.get("/entities").json()]
    assert "Lyon (Rhône)" in [item["name"] for item in client.get("/entities?hidden=true").json()]
    # "Benali" is Karim Benali: merged, and a new extraction keeps it merged.
    merged = client.post(f"/entities/{ids['Benali']}/merge", json={"into": ids["Karim Benali"]}).json()
    assert merged["name"] == "Karim Benali"
    worker.extract_video_entities("v")
    names = [item["name"] for item in client.get("/entities").json()]
    assert "Benali" not in names and client.get(f"/entities/{ids['Karim Benali']}").json()["mentions"] == 1
    assert client.post(f"/entities/{ids['Lyon']}/merge", json={"into": ids["Lyon"]}).status_code == 422
    assert client.get("/entities/999999").status_code == 404


def test_a_corrected_transcript_is_read_again(client, env, monkeypatch, no_entity_jobs):
    fake_extraction(monkeypatch)
    add_video(env, "v", MEETING)
    worker.extract_video_entities("v")
    with env() as db:
        segment_id = db.query(main.TranscriptSegment).filter_by(video_id="v").first().id
    client.patch(f"/videos/v/segments/{segment_id}", json={"text": "Bonjour, je suis Claire Martinez."})
    assert no_entity_jobs == ["v"]
    with env() as db:
        assert db.get(VideoEntityState, "v").status == "STALE"


def test_the_entities_job_and_the_catch_up(env, monkeypatch, no_entity_jobs):
    fake_extraction(monkeypatch)
    add_video(env, "old", MEETING)
    add_video(env, "new", MEETING)
    with env() as db:
        db.add(main.ProcessingJob(id="j", video_id="new", kind="ENTITIES", stage="QUEUED", status="QUEUED", progress=0))
        db.commit()
    worker.run_entities("j")
    with env() as db:
        assert db.get(main.ProcessingJob, "j").status == "COMPLETED"
    worker.enqueue_missing_entities()
    assert no_entity_jobs == ["old"]


def test_a_failed_extraction_is_recorded(client, env, monkeypatch):
    def broken(text, vocabulary=()):
        raise ConnectionError("Ollama down")

    monkeypatch.setattr(llm, "extract_entities", broken)
    add_video(env, "v", MEETING)
    with env() as db:
        db.add(main.ProcessingJob(id="j", video_id="v", kind="ENTITIES", stage="QUEUED", status="QUEUED", progress=0))
        db.commit()
    worker.run_entities("j")
    with env() as db:
        assert db.get(main.ProcessingJob, "j").status == "FAILED" and db.get(VideoEntityState, "v").status == "FAILED"
    assert client.get("/entities/progress").json() == {"videos": 1, "ready": 0, "failed": 1, "waiting": 0}
    # A background job never becomes "the video's job".
    assert client.get("/videos/v").json()["job"] is None


def test_the_extraction_prompt_asks_for_json(monkeypatch):
    payloads = []

    def completion(prompt, **options):
        payloads.append(options)
        return json.dumps({"entites": [{"nom": "Acme", "type": "organisation", "moment": "00:00:01"}, "bruit"]}), "stop"

    monkeypatch.setattr(llm, "chat_completion", completion)
    assert llm.extract_entities("[00:00:01] Acme signe.") == [{"nom": "Acme", "type": "organisation", "moment": "00:00:01"}]
    assert payloads[0]["json_schema"]["required"] == ["entites"] and payloads[0]["temperature"] == 0.0
    monkeypatch.setattr(llm, "chat_completion", lambda prompt, **options: ("pas du json", "stop"))
    assert llm.extract_entities("[00:00:01] Acme") == []
    # Cut by the token limit: the items written whole are kept.
    cut = '{"entites": [{"nom": "Acme", "type": "organisation", "moment": "00:00:01"}, {"nom": "Lyo'
    monkeypatch.setattr(llm, "chat_completion", lambda prompt, **options: (cut, "length"))
    assert llm.extract_entities("[00:00:01] Acme") == [{"nom": "Acme", "type": "organisation", "moment": "00:00:01"}]
    assert llm._chat_payload("x", model="m", temperature=0, max_output_tokens=10, stream=False, json_schema={"type": "object"})["format"] == {"type": "object"}


# --- n°17: hybrid search and collections ---------------------------------------------------------

def indexed_library(session):
    add_video(session, "budget", [(0.0, 10.0, "Le budget annuel est voté par le conseil.")], name="Conseil.mp4")
    add_video(session, "velo", [(0.0, 10.0, "Les pistes cyclables et les vélos électriques.")], name="Mobilité.mp4")
    worker.index_video("budget")
    worker.index_video("velo")


def test_hybrid_search_adds_videos_close_in_meaning(client, env, monkeypatch):
    indexed_library(env)
    monkeypatch.setattr(main, "SEMANTIC_MAX_DISTANCE", 0.9)
    # "conseil vélos": no video has both words, but each is close to part of it.
    response = client.get("/videos?q=conseil vélos")
    assert response.headers["X-Search-Mode"] == "hybrid"
    rows = response.json()
    assert {row["id"] for row in rows} == {"budget", "velo"}
    # Found by meaning only: the snippet is the passage, not a stray word.
    assert all(row["snippet"]["source"] == "meaning" for row in rows)
    assert client.get("/videos?q=conseil vélos&mode=exact").json() == []
    # A video containing the words comes first.
    assert client.get("/videos?q=budget").json()[0]["id"] == "budget"


def test_far_passages_are_not_results(client, env):
    indexed_library(env)
    assert [row["id"] for row in client.get("/videos?q=budget").json()] == ["budget"]


def test_a_video_found_by_meaning_only_shows_its_passage(client, env, monkeypatch):
    indexed_library(env)
    monkeypatch.setattr(main, "SEMANTIC_MAX_DISTANCE", 0.9)
    # "cyclables" is in the passage, but the library search sees whole words only: "cyclable" is not.
    row = next(row for row in client.get("/videos?q=cyclable vélos électriques").json() if row["id"] == "velo")
    assert row["snippet"]["text"].startswith("Les pistes cyclables")


def test_search_falls_back_to_words_without_embeddings(client, env, monkeypatch):
    indexed_library(env)

    def down(texts, on_batch=None):
        raise ConnectionError("Ollama down")

    monkeypatch.setattr(main, "embed_texts", down)
    response = client.get("/videos?q=budget")
    assert response.headers["X-Search-Mode"] == "exact" and [row["id"] for row in response.json()] == ["budget"]


def test_saved_searches(client, env):
    saved = client.post("/library/searches", json={"name": "Conseils", "query": "?q=conseil&tag=Finance"}).json()
    assert saved["query"] == "q=conseil&tag=Finance"
    assert client.post("/library/searches", json={"name": "conseils", "query": "q=x"}).status_code == 409
    assert client.patch(f"/library/searches/{saved['id']}", json={"name": "Conseils 2026"}).json()["name"] == "Conseils 2026"
    assert [row["name"] for row in client.get("/library/searches").json()] == ["Conseils 2026"]
    assert client.delete(f"/library/searches/{saved['id']}").json() == {"deleted": True}
    assert client.delete(f"/library/searches/{saved['id']}").status_code == 404


# --- n°18: conversations -------------------------------------------------------------------------

def conversation(session, cid="c", title="Budget ?", answer="Le budget est voté [1].", feedback=None):
    now = main.utcnow()
    with session() as db:
        db.add(LibraryConversation(id=cid, title=title, scope="tag « Finance »", video_ids=json.dumps(["budget"]), created_at=now, updated_at=now))
        db.flush()
        db.add(LibraryMessage(id=f"{cid}-q", conversation_id=cid, role="user", content=title, created_at=now))
        db.add(LibraryMessage(
            id=f"{cid}-a", conversation_id=cid, role="assistant", content=answer, feedback=feedback,
            sources=json.dumps([{"n": 1, "video_id": "budget", "title": "Conseil.mp4", "start_seconds": 754.0, "end_seconds": 800.0}]),
            created_at=main.utcnow(),
        ))
        db.commit()


def test_conversations_are_renamed_searched_and_flagged(client, env):
    conversation(env, "c1", "Budget ?", "Le budget est voté.")
    conversation(env, "c2", "Vélos ?", "Des pistes cyclables.")
    assert client.patch("/library/conversations/c1", json={"title": "Budget 2026"}).json()["title"] == "Budget 2026"
    assert [c["id"] for c in client.get("/library/conversations?q=cyclables").json()] == ["c2"]
    assert [c["id"] for c in client.get("/library/conversations?q=2026").json()] == ["c1"]
    assert client.put("/library/messages/c2-a/feedback", json={"value": -1}).json() == {"feedback": -1}
    assert client.put("/library/messages/c2-q/feedback", json={"value": 1}).status_code == 404
    assert client.put("/library/messages/c2-a/feedback", json={"value": 5}).status_code == 422
    flagged = client.get("/library/conversations?flagged=true").json()
    assert [(c["id"], c["flagged"]) for c in flagged] == [("c2", True)]
    detail = client.get("/library/conversations/c2").json()
    assert detail["messages"][1]["feedback"] == -1


def test_a_conversation_is_exported_with_its_sources(client, env):
    conversation(env, feedback=-1)
    response = client.get("/library/conversations/c/export")
    assert response.headers["content-type"].startswith("text/markdown")
    assert "conversation.md" in response.headers["content-disposition"]
    text = response.text
    assert text.startswith("# Budget ?") and "## Question" in text and "Le budget est voté [1]." in text
    assert "1. Conseil.mp4 — 00:12:34" in text and "signalée comme incorrecte" in text


def test_video_chat_thumbs_and_export(client, env):
    add_video(env, "v", MEETING, name="Réunion.mp4")
    with env() as db:
        db.add(VideoChatMessage(id="q", video_id="v", role="user", content="Qui parle ?"))
        db.add(VideoChatMessage(id="a", video_id="v", role="assistant", content="Claire Martin."))
        db.commit()
    assert client.put("/videos/v/chat/messages/a/feedback", json={"value": 1}).json() == {"feedback": 1}
    assert client.get("/videos/v/chat/messages").json()[1]["feedback"] == 1
    assert client.put("/videos/other/chat/messages/a/feedback", json={"value": 1}).status_code == 404
    text = client.get("/videos/v/chat/export").text
    assert "# Questions sur « Réunion.mp4 »" in text and "Claire Martin." in text


# --- n°20: models --------------------------------------------------------------------------------

TAGS = {"models": [
    {"name": "qwen3:8b", "size": 5_200_000_000, "details": {"parameter_size": "8.2B", "quantization_level": "Q4_K_M", "family": "qwen3"}},
    {"name": "qwen3:14b", "size": 9_300_000_000, "details": {"parameter_size": "14.8B", "quantization_level": "Q4_K_M", "family": "qwen3"}},
    {"name": "bge-m3:latest", "size": 1_200_000_000, "details": {"family": "bert"}},
]}


@pytest.fixture
def ollama(monkeypatch):
    calls = []

    def fake(method, path, **kwargs):
        calls.append((method, path, kwargs.get("json")))
        if path == "/api/tags":
            return TAGS
        if path == "/api/ps":
            return {"models": [{"name": "qwen3:8b", "size": 6e9, "size_vram": 6e9}]}
        if path == "/api/generate":
            return {"load_duration": 2.5e9, "eval_count": 120, "eval_duration": 1.5e9, "prompt_eval_count": 90,
                    "prompt_eval_duration": 0.1e9, "total_duration": 4.2e9, "response": "Le budget déborde de 12 %."}
        return {}

    monkeypatch.setattr(main, "_ollama", fake)
    monkeypatch.setattr(main, "_redis_json", lambda key: {"name": "RTX 5080", "memory_total_mb": 16303, "memory_used_mb": 9000, "whisper_device": "cuda"} if key == main.GPU_KEY else None)
    return calls


def test_the_models_page_lists_what_is_installed(client, env, ollama):
    page = client.get("/models").json()
    assert page["llm"] == {"current": main.settings.llm_model, "default": main.settings.llm_model, "chosen": False}
    assert [(m["name"], m["embedding"]) for m in page["installed"]] == [("qwen3:8b", False), ("qwen3:14b", False), ("bge-m3:latest", True)]
    assert next(s for s in page["suggestions"] if s["name"] == "qwen3:14b")["installed"] is True
    assert page["gpu"]["name"] == "RTX 5080" and page["whisper"]["device"] == "cuda"
    assert "large-v3-turbo" in [choice["name"] for choice in page["whisper"]["choices"]]


def test_choosing_models(client, env, ollama):
    assert client.put("/settings/models", json={"llm_model": "llama9:70b"}).status_code == 422
    assert client.put("/settings/models", json={"llm_model": "bge-m3"}).json()["detail"].startswith("C'est un modèle d'embeddings")
    assert client.put("/settings/models", json={"whisper_model": "huge"}).status_code == 422
    assert client.put("/settings/models", json={"llm_model": "qwen3:14b", "whisper_model": "medium"}).json() == {
        "llm_model": "qwen3:14b", "whisper_model": "medium",
    }
    assert (ai_models.llm_model(), ai_models.whisper_model()) == ("qwen3:14b", "medium")
    # Every LLM call now uses it.
    assert llm._chat_payload("x", model=None, temperature=0, max_output_tokens=10, stream=False)["model"] == "qwen3:14b"
    assert client.delete("/models/llm/qwen3:14b").status_code == 409
    assert client.delete(f"/models/llm/{main.settings.embedding_model}").status_code == 409
    assert client.delete("/models/llm/qwen3:8b").status_code == 409  # the environment's default
    # Back to the environment's models.
    assert client.put("/settings/models", json={}).json()["llm_model"] == main.settings.llm_model


def test_the_llm_speed_test(client, env, ollama):
    result = client.post("/models/llm/benchmark", json={"name": "qwen3:14b"}).json()
    assert (result["load_seconds"], result["tokens_per_second"], result["prompt_tokens_per_second"]) == (2.5, 80.0, 900.0)
    assert ollama[-1][2]["model"] == "qwen3:14b" and ollama[-1][2]["options"]["num_predict"] == 200


def test_a_pull_reports_an_unreachable_ollama(client, env, monkeypatch):
    monkeypatch.setattr(main.settings, "ollama_url", "http://127.0.0.1:9")
    events = sse_events(client.post("/models/llm/pull", json={"name": "qwen3:4b"}).text)
    assert events == [("error", {"detail": "Ollama ne répond pas"})]
    assert client.post("/models/llm/pull", json={"name": "rm -rf /"}).status_code == 422


class FakeRedis:
    def __init__(self):
        self.values, self.lists = {}, {}

    def set(self, key, value, ex=None):
        self.values[key] = value

    def get(self, key):
        return self.values.get(key)

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    def lpop(self, key):
        items = self.lists.get(key) or []
        return items.pop(0) if items else None


def test_the_whisper_speed_test_runs_in_the_live_service(client, env, monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(main.Redis, "from_url", lambda *args, **kwargs: redis)
    monkeypatch.setattr(main, "_redis_json", lambda key: json.loads(redis.get(key)) if redis.get(key) else None)
    assert client.post("/models/whisper/benchmark", json={"name": "small"}).status_code == 409  # no video yet
    source = make_media(main.settings.uploads_dir / "v.mp4", seconds=3)
    add_video(env, "v", MEETING, name="Réunion.mp4")
    with env() as db:
        db.get(main.Video, "v").path = str(source)
        db.commit()
    assert client.post("/models/whisper/benchmark", json={"name": "enormous"}).status_code == 422
    request = client.post("/models/whisper/benchmark", json={"name": "small"}).json()
    assert request["sample"] == "Réunion.mp4"
    assert client.get(f"/models/whisper/benchmark/{request['id']}").json()["status"] == "pending"

    class Model:
        def transcribe(self, audio, **options):
            return iter([SimpleNamespace(text=" Bonjour.")]), SimpleNamespace(language="fr")

    assert live.serve_benchmark(redis, model_factory=lambda name: Model()) is True
    result = client.get(f"/models/whisper/benchmark/{request['id']}").json()
    assert (result["status"], result["model"], result["audio_seconds"], result["text"]) == ("done", "small", 3.0, "Bonjour.")
    assert result["speed"] > 0
    assert live.serve_benchmark(redis) is False


def test_gpu_state_without_a_card(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(live.subprocess, "run", missing)
    state = live.gpu_state()
    assert "name" not in state and state["whisper_device"] == live.settings.whisper_device
