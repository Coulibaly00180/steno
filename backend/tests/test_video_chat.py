from app import main
from app.models import Video, VideoChatMessage


def create_video(session, uploads_dir, *, transcript="[00:01:23] Le modèle recommandé est le PC Alpha."):
    source = uploads_dir / "chat-source.mp4"
    source.write_bytes(b"video")
    video = Video(
        id="video-chat",
        filename=source.name,
        original_filename="chat-source.mp4",
        path=str(source),
        duration_seconds=90.0,
        size_bytes=source.stat().st_size,
        status="COMPLETED",
        transcript_text=transcript,
        detected_language="fr",
    )
    with session() as db:
        db.add(video)
        db.commit()
    return video.id


def test_question_answer_is_persisted_with_video_history(client, upload_environment, monkeypatch):
    session, uploads_dir = upload_environment
    video_id = create_video(session, uploads_dir)
    captured = {}

    def fake_answer(question, context, history, **_):
        captured.update(question=question, context=context, history=history)
        return "Le PC Alpha est recommandé à 00:01:23."

    monkeypatch.setattr(main, "answer_video_question", fake_answer)

    response = client.post(f"/videos/{video_id}/chat/messages", json={"question": "Quel modèle est recommandé ?"})

    assert response.status_code == 200
    messages = response.json()
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "Le PC Alpha est recommandé à 00:01:23."
    assert captured["question"] == "Quel modèle est recommandé ?"
    assert "PC Alpha" in captured["context"]
    assert captured["history"] == []

    history_response = client.get(f"/videos/{video_id}/chat/messages")
    assert history_response.status_code == 200
    assert history_response.json() == messages
    with session() as db:
        assert db.query(VideoChatMessage).filter_by(video_id=video_id).count() == 2


def test_question_requires_a_transcript(client, upload_environment):
    session, uploads_dir = upload_environment
    video_id = create_video(session, uploads_dir, transcript=None)

    response = client.post(f"/videos/{video_id}/chat/messages", json={"question": "Que dit la vidéo ?"})

    assert response.status_code == 409
    assert response.json() == {"detail": "La transcription n'est pas encore disponible"}


def test_model_failure_does_not_store_an_orphan_question(client, upload_environment, monkeypatch):
    session, uploads_dir = upload_environment
    video_id = create_video(session, uploads_dir)
    monkeypatch.setattr(main, "answer_video_question", lambda *_, **__: (_ for _ in ()).throw(ConnectionError("offline")))

    response = client.post(f"/videos/{video_id}/chat/messages", json={"question": "Question"})

    assert response.status_code == 503
    with session() as db:
        assert db.query(VideoChatMessage).filter_by(video_id=video_id).count() == 0
