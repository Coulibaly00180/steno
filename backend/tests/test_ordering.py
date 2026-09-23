from datetime import datetime, timedelta, timezone

from app import main
from app.models import Summary, TranscriptSegment, Video

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def create_video(session, uploads_dir):
    source = uploads_dir / "ordering.mp4"
    source.write_bytes(b"video")
    with session() as db:
        db.add(Video(
            id="video-ordering",
            filename=source.name,
            original_filename=source.name,
            path=str(source),
            duration_seconds=60.0,
            size_bytes=5,
            status="COMPLETED",
            transcript_text="[00:00:00] Bonjour.",
        ))
        db.commit()
    return "video-ordering"


def test_summaries_are_ordered_by_creation_and_chat_uses_the_latest(client, upload_environment, monkeypatch):
    session, uploads_dir = upload_environment
    video_id = create_video(session, uploads_dir)
    with session() as db:
        # Inserted out of chronological order on purpose.
        for name, offset in (("latest", 2), ("oldest", 0), ("middle", 1)):
            db.add(Summary(
                id=f"summary-{name}",
                video_id=video_id,
                content_markdown=f"Résumé {name}",
                model="test",
                created_at=T0 + timedelta(minutes=offset),
            ))
        db.commit()

    response = client.get(f"/videos/{video_id}")

    assert response.status_code == 200
    assert [s["id"] for s in response.json()["summaries"]] == ["summary-oldest", "summary-middle", "summary-latest"]

    captured = {}

    def fake_answer(question, context, history, **_):
        captured["context"] = context
        return "ok"

    monkeypatch.setattr(main, "answer_video_question", fake_answer)
    client.post(f"/videos/{video_id}/chat/messages", json={"question": "Et alors ?"})
    assert "Résumé latest" in captured["context"]
    assert "Résumé oldest" not in captured["context"]


def test_segments_are_ordered_by_start_time(client, upload_environment):
    session, uploads_dir = upload_environment
    video_id = create_video(session, uploads_dir)
    with session() as db:
        for start in (30.0, 0.0, 12.5):
            db.add(TranscriptSegment(video_id=video_id, start_seconds=start, end_seconds=start + 1, text=f"t={start}"))
        db.commit()

    response = client.get(f"/videos/{video_id}")

    assert [s["start_seconds"] for s in response.json()["segments"]] == [0.0, 12.5, 30.0]
