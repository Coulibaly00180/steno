"""Finishing touches: saved conversations and interrupted answers, translated reports, snippets."""
import asyncio

import pytest

from app import main, worker
from app.models import LibraryConversation, LibraryMessage, VideoChatMessage
from app.schemas import ChatQuestion, LibraryQuestion
from tests.test_phase4 import add_video, db_session, sse_events  # noqa: F401  (fixture reused)


def two_indexed_videos(session):
    add_video(session, "budget", [(float(i * 10), float(i * 10 + 10), f"Le budget annuel, point {i}.") for i in range(6)], name="Budget.mp4")
    add_video(session, "velo", [(0.0, 10.0, "Le conseil achète des vélos électriques.")], name="Conseil.mp4")
    worker.index_video("budget")
    worker.index_video("velo")


def fake_stream(monkeypatch, *pieces, then=None):
    async def stream(prompt, **kwargs):
        for piece in pieces:
            yield piece
        if then:
            await then()

    monkeypatch.setattr(main, "stream_chat", stream)


# --- n°19: conversations are kept -------------------------------------------------------------

def test_a_library_conversation_is_saved_listed_reopened_and_deleted(client, db_session, monkeypatch):
    two_indexed_videos(db_session)
    fake_stream(monkeypatch, "Le budget est voté ", "[1].")
    first = sse_events(client.post("/library/chat/stream", json={
        "question": "Que dit-on du budget ?", "video_ids": ["budget", "velo"], "scope": "tag « Finance »",
    }).text)
    conversation_id = first[0][1]["conversation_id"]
    client.post("/library/chat/stream", json={"question": "Et les vélos ?", "conversation_id": conversation_id})

    listing = client.get("/library/conversations").json()
    assert [(c["id"], c["title"], c["scope"], c["video_count"]) for c in listing] == [
        (conversation_id, "Que dit-on du budget ?", "tag « Finance »", 2)
    ]
    detail = client.get(f"/library/conversations/{conversation_id}").json()
    assert [(m["role"], m["content"]) for m in detail["messages"]] == [
        ("user", "Que dit-on du budget ?"), ("assistant", "Le budget est voté [1]."),
        ("user", "Et les vélos ?"), ("assistant", "Le budget est voté [1]."),
    ]
    assert detail["video_ids"] == ["budget", "velo"]
    # The cited passages are kept with the answer: the [1] still opens its video.
    assert detail["messages"][1]["sources"][0]["video_id"] in {"budget", "velo"}

    assert client.delete(f"/library/conversations/{conversation_id}").json() == {"deleted": True}
    assert client.get(f"/library/conversations/{conversation_id}").status_code == 404
    with db_session() as db:
        assert db.query(LibraryMessage).count() == 0


def test_a_follow_up_needs_an_existing_conversation(client, db_session):
    two_indexed_videos(db_session)
    response = client.post("/library/chat/stream", json={"question": "Q ?", "conversation_id": "missing"})
    assert response.status_code == 404
    assert client.post("/library/chat/stream", json={"question": "Q ?"}).status_code == 422


def test_a_failed_library_answer_keeps_its_partial_text(client, db_session, monkeypatch):
    two_indexed_videos(db_session)

    async def failing(prompt, **kwargs):
        yield "Le budget"
        raise ConnectionError("stopped")

    monkeypatch.setattr(main, "stream_chat", failing)
    events = sse_events(client.post("/library/chat/stream", json={"question": "Budget ?", "video_ids": ["budget"]}).text)
    assert events[-1][1]["saved"] is True
    with db_session() as db:
        answer = db.query(LibraryMessage).filter_by(role="assistant").one()
        assert (answer.content, answer.interrupted) == ("Le budget", True)


def test_a_library_conversation_without_any_answer_is_not_kept(client, db_session, monkeypatch):
    two_indexed_videos(db_session)

    async def failing(prompt, **kwargs):
        raise ConnectionError("down")
        yield  # pragma: no cover

    monkeypatch.setattr(main, "stream_chat", failing)
    events = sse_events(client.post("/library/chat/stream", json={"question": "Budget ?", "video_ids": ["budget"]}).text)
    assert events[-1][1]["saved"] is False
    with db_session() as db:
        assert db.query(LibraryConversation).count() == 0


# --- leaving the page keeps what was written ----------------------------------------------------

def test_leaving_a_video_chat_mid_answer_keeps_the_written_part(db_session, monkeypatch):
    add_video(db_session, "v", [(0.0, 5.0, "Le budget est validé.")])

    async def forever():
        await asyncio.sleep(30)

    fake_stream(monkeypatch, "Le budget ", "est validé", then=forever)

    async def read_two_pieces_then_leave():
        response = await main.stream_video_answer("v", ChatQuestion(question="Et le budget ?"))
        stream = response.body_iterator
        await stream.__anext__()
        await stream.__anext__()
        await stream.aclose()  # the browser closed the connection

    asyncio.run(read_two_pieces_then_leave())
    with db_session() as db:
        messages = db.query(VideoChatMessage).order_by(VideoChatMessage.created_at).all()
        assert [(m.role, m.content, m.interrupted) for m in messages] == [
            ("user", "Et le budget ?", False), ("assistant", "Le budget est validé", True),
        ]


def test_leaving_a_library_answer_mid_way_keeps_it_and_the_conversation(db_session, monkeypatch):
    two_indexed_videos(db_session)

    async def forever():
        await asyncio.sleep(30)

    fake_stream(monkeypatch, "Selon [1]", then=forever)

    async def read_then_leave():
        response = await main.stream_library_answer(LibraryQuestion(question="Budget ?", video_ids=["budget"]))
        stream = response.body_iterator
        await stream.__anext__()  # sources
        await stream.__anext__()  # first piece
        await stream.aclose()

    asyncio.run(read_then_leave())
    with db_session() as db:
        assert db.query(LibraryConversation).count() == 1
        answer = db.query(LibraryMessage).filter_by(role="assistant").one()
        assert (answer.content, answer.interrupted) == ("Selon [1]", True)


def test_leaving_before_any_word_keeps_nothing(db_session, monkeypatch):
    two_indexed_videos(db_session)

    async def forever():
        await asyncio.sleep(30)

    fake_stream(monkeypatch, then=forever)

    async def read_sources_then_leave():
        response = await main.stream_library_answer(LibraryQuestion(question="Budget ?", video_ids=["budget"]))
        stream = response.body_iterator
        await stream.__anext__()
        await stream.aclose()

    asyncio.run(read_sources_then_leave())
    with db_session() as db:
        assert db.query(LibraryConversation).count() == 0


@pytest.mark.parametrize("payload", [{"question": "Q ?", "video_ids": ["x"] * 501}])
def test_library_question_limits(client, payload):
    assert client.post("/library/chat/stream", json=payload).status_code == 422
