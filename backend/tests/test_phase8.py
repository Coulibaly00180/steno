"""Browser recordings (n°10), live transcript (n°11), imports from a link (n°12)."""
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from app import live, main, url_import, worker
from app.models import LiveSegment, ProcessingJob, Recording, Video
from tests.test_phase4 import sse_events
from tests.test_phase7 import MODULES, make_session
from tests.test_upload import SuccessfulQueue


@pytest.fixture
def env(monkeypatch, tmp_path):
    session = make_session(tmp_path / "p8.db")
    for module in (*MODULES, live, url_import):
        monkeypatch.setattr(module, "SessionLocal", session)
    monkeypatch.setattr(main.settings, "data_dir", tmp_path / "data")
    for folder in ("uploads", "audio", "exports"):
        (tmp_path / "data" / folder).mkdir(parents=True)
    monkeypatch.setattr(main, "Queue", SuccessfulQueue)
    monkeypatch.setattr(main, "ffprobe_duration", lambda _, **__: 12.0)
    return session


def make_opus(path: Path, seconds: float = 6.0, container: str = "webm") -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-f", "lavfi", "-i", f"sine=frequency=300:duration={seconds}",
         "-c:a", "libopus", "-b:a", "32k", "-f", container, str(path)],
        check=True, capture_output=True,
    )
    return path


def chunks_of(data: bytes, size: int = 4000) -> list[bytes]:
    return [data[i:i + size] for i in range(0, len(data), size)]


# --- n°12: links ---------------------------------------------------------------------------

@pytest.fixture
def web(monkeypatch):
    """A fake Internet: routes of example.org, every name resolving to a public address."""
    routes: dict[str, httpx.Response | callable] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        route = routes.get(str(request.url))
        if route is None:
            return httpx.Response(404)
        return route(request) if callable(route) else route

    monkeypatch.setattr(url_import, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False))
    monkeypatch.setattr(url_import, "_resolve", lambda host: ["10.0.0.5"] if host.endswith(".internal") else ["93.184.215.14"])
    # Video platforms are off unless a test turns them on (the `platforms` fixture).
    monkeypatch.setattr(url_import, "platforms_enabled", lambda: False)
    return routes


FEED = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel><title>Le podcast</title>
<item><title>Episode 2</title><pubDate>Tue, 22 Sep 2026 06:00:00 GMT</pubDate><itunes:duration>01:02:03</itunes:duration>
<enclosure url="/audio/ep2.mp3" type="audio/mpeg" length="1200"/></item>
<item><title>Notes</title><enclosure url="https://example.org/notes.pdf" type="application/pdf"/></item>
<item><title>Episode 1</title><itunes:duration>600</itunes:duration><enclosure url="https://example.org/audio/ep1.mp3" type="audio/mpeg"/></item>
</channel></rss>"""


def test_private_and_odd_links_are_refused(web):
    for url, message in [
        ("ftp://example.org/a.mp3", "http"),
        ("https://user:secret@example.org/a.mp3", "identifiant"),
        ("http://nas.internal/a.mp3", "réseau local"),
    ]:
        with pytest.raises(url_import.UrlImportError, match=message):
            url_import.check_url(url)


def test_private_links_may_be_allowed(web, monkeypatch):
    monkeypatch.setattr(url_import.settings, "url_import_allow_private", True)
    assert url_import.check_url("http://nas.internal/a.mp3") == "http://nas.internal/a.mp3"


def test_a_direct_link_is_recognised(web):
    web["https://example.org/talk"] = httpx.Response(200, headers={
        "content-type": "video/mp4", "content-length": "5000", "content-disposition": "attachment; filename=\"Conference.mp4\"; filename*=UTF-8''Conf%C3%A9rence%202026.mp4",
    }, content=b"x" * 5000)
    assert url_import.probe("https://example.org/talk") == {
        "kind": "media", "url": "https://example.org/talk", "title": "Conférence 2026", "filename": "Conférence 2026.mp4",
        "size_bytes": 5000, "content_type": "video/mp4",
    }


def test_a_podcast_feed_lists_its_episodes(web):
    web["https://example.org/feed"] = httpx.Response(200, headers={"content-type": "application/rss+xml"}, content=FEED)
    feed = url_import.probe("https://example.org/feed")
    assert feed["title"] == "Le podcast"
    assert [(e["title"], e["url"], e["duration_seconds"]) for e in feed["episodes"]] == [
        ("Episode 2", "https://example.org/audio/ep2.mp3", 3723.0),
        ("Episode 1", "https://example.org/audio/ep1.mp3", 600.0),
    ]
    assert feed["episodes"][0]["published"].startswith("2026-09-22")


def test_a_web_page_is_refused_with_an_explanation(web):
    web["https://example.org/watch?v=1"] = httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html></html>")
    with pytest.raises(url_import.UrlImportError, match="plateformes"):
        url_import.probe("https://example.org/watch?v=1")


def test_a_feed_with_entities_is_refused(web):
    bomb = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><rss><channel><title>&b;</title></channel></rss>'
    web["https://example.org/bomb"] = httpx.Response(200, headers={"content-type": "application/xml"}, content=bomb)
    with pytest.raises(url_import.UrlImportError, match="entités"):
        url_import.probe("https://example.org/bomb")


def test_every_redirect_is_checked(web):
    web["https://example.org/go"] = httpx.Response(302, headers={"location": "http://redis.internal:6379/"})
    with pytest.raises(url_import.UrlImportError, match="réseau local"):
        url_import.probe("https://example.org/go")
    web["https://example.org/loop"] = httpx.Response(302, headers={"location": "/loop"})
    with pytest.raises(url_import.UrlImportError, match="redirections"):
        url_import.probe("https://example.org/loop")


def test_a_download_is_written_whole_or_not_at_all(web, tmp_path, monkeypatch):
    web["https://example.org/a.mp3"] = httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=b"m" * 3000)
    progress = []
    path, name = url_import.download("https://example.org/a.mp3", tmp_path, "vid", on_progress=lambda done, total: progress.append(done))
    assert (path.name, name, path.read_bytes()) == ("vid.mp3", "a.mp3", b"m" * 3000) and progress[-1] == 3000
    monkeypatch.setattr(url_import.settings, "max_download_bytes", 1000)
    with pytest.raises(url_import.UrlImportError, match="trop volumineux"):
        url_import.download("https://example.org/a.mp3", tmp_path, "big")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["vid.mp3"]


def test_a_link_import_is_queued_then_downloaded_by_the_worker(client, env, web, monkeypatch):
    web["https://example.org/feed"] = httpx.Response(200, headers={"content-type": "application/rss+xml"}, content=FEED)
    web["https://example.org/audio/ep2.mp3"] = httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=b"m" * 3000)
    preview = client.post("/imports/url/preview", json={"url": "https://example.org/feed"}).json()
    episode = preview["episodes"][0]
    job = client.post("/imports/url", json={"url": episode["url"], "title": episode["title"], "summary_length": "short"}).json()
    assert (job["status"], job["summary_length"]) == ("QUEUED", "short")
    video = client.get(f"/videos/{job['video_id']}").json()
    assert (video["original_filename"], video["source_url"], video["source_available"]) == ("Episode 2", episode["url"], False)

    monkeypatch.setattr(worker, "ffprobe_duration", lambda _, **__: 42.0)
    worker.set_video_status(job["video_id"], "PROCESSING")
    path, duration = worker.download_source(job["id"], job["video_id"], episode["url"])
    assert duration == 42.0 and path.read_bytes() == b"m" * 3000
    with env() as db:
        stored = db.get(Video, job["video_id"])
        assert (stored.path, stored.duration_seconds, stored.size_bytes) == (str(path), 42.0, 3000)


def test_a_failed_download_fails_the_video_and_can_be_retried(client, env, web):
    job = client.post("/imports/url", json={"url": "https://example.org/gone.mp3"}).json()
    with env() as db:
        db.get(ProcessingJob, job["id"]).status = "QUEUED"
        db.commit()
    with pytest.raises(worker.PipelineError):
        worker.run_pipeline(job["id"])
    video = client.get(f"/videos/{job['video_id']}").json()
    assert video["status"] == "FAILED" and "404" in video["job"]["error"]
    # No file was ever stored: the retry downloads it again.
    assert client.post(f"/videos/{job['video_id']}/retry").status_code == 200


def test_link_errors_are_shown(client, env, web):
    assert client.post("/imports/url/preview", json={"url": "http://nas.internal/x.mp3"}).status_code == 422
    assert client.post("/imports/url", json={"url": "notaurl"}).status_code == 422
    assert client.post("/imports/url", json={"url": "https://example.org/a.mp3", "summary_length": "huge"}).status_code == 422


# --- n°10: recordings ----------------------------------------------------------------------

def start(client, **extra) -> dict:
    return client.post("/recordings", json={"title": "Réunion d'équipe", "mime_type": "audio/webm;codecs=opus", **extra}).json()


def test_a_recording_is_uploaded_in_order_and_analysed(client, env, tmp_path):
    data = make_opus(tmp_path / "source.webm").read_bytes()
    recording = start(client)
    for index, chunk in enumerate(chunks_of(data)):
        response = client.put(f"/recordings/{recording['id']}/chunks/{index}", content=chunk)
        assert response.status_code == 200
    # A chunk sent again after a network error: acknowledged, not written twice.
    assert client.put(f"/recordings/{recording['id']}/chunks/0", content=data[:4000]).json()["size_bytes"] == len(data)
    assert client.put(f"/recordings/{recording['id']}/chunks/99", content=b"x").status_code == 409
    assert [r["id"] for r in client.get("/recordings").json()] == [recording["id"]]

    job = client.post(f"/recordings/{recording['id']}/finish", json={"summary_length": "short", "source_policy": "audio"}).json()
    video = client.get(f"/videos/{job['video_id']}").json()
    assert video["original_filename"] == "Réunion d'équipe.ogg" and video["media_kind"] == "audio" and video["source_available"]
    assert video["source_policy"] == "audio"
    assert client.get("/recordings").json() == []
    assert client.get(f"/recordings/{recording['id']}").json()["video_id"] == job["video_id"]
    assert not list(main.settings.uploads_dir.glob(".rec-*"))
    assert client.post(f"/recordings/{recording['id']}/finish", json={}).status_code == 409
    assert client.put(f"/recordings/{recording['id']}/chunks/{len(chunks_of(data))}", content=b"x").status_code == 409


def test_recording_refusals(client, env):
    assert client.post("/recordings", json={"title": "x", "mime_type": "video/x-flv"}).status_code == 422
    recording = start(client)
    assert client.post(f"/recordings/{recording['id']}/finish", json={}).json()["detail"] == "L'enregistrement est vide"
    client.put(f"/recordings/{recording['id']}/chunks/0", content=b"not audio at all")
    response = client.post(f"/recordings/{recording['id']}/finish", json={})
    assert response.status_code == 422 and "conservé" in response.json()["detail"]
    # Kept: the user can still discard it.
    assert client.delete(f"/recordings/{recording['id']}").json() == {"deleted": True}
    assert client.get(f"/recordings/{recording['id']}").json()["status"] == "CANCELLED"
    assert not list(main.settings.uploads_dir.glob(".rec-*"))


def test_the_live_transcript_streams_until_the_recording_ends(client, env):
    recording = start(client, live=True)
    with env() as db:
        db.add_all([
            LiveSegment(recording_id=recording["id"], start_seconds=0.0, end_seconds=2.0, text="Bonjour à tous."),
            LiveSegment(recording_id=recording["id"], start_seconds=2.0, end_seconds=4.0, text="Premier point."),
        ])
        db.get(Recording, recording["id"]).status = "CANCELLED"
        db.commit()
    events = sse_events(client.get(f"/recordings/{recording['id']}/live").text)
    assert [(name, data.get("text")) for name, data in events] == [
        ("segment", "Bonjour à tous."), ("segment", "Premier point."), ("end", None),
    ]


# --- n°11: live transcript -----------------------------------------------------------------

class FakeModel:
    """One sentence per 5 seconds of audio, the last one possibly cut; no word timings."""

    def __init__(self, punctuation="."):
        self.calls = []
        self.punctuation = punctuation

    def transcribe(self, audio, **options):
        self.calls.append((len(audio) / live.RATE, options))
        seconds = len(audio) / live.RATE
        segments, start, number = [], 0.0, 0
        while start < seconds:
            number += 1
            end = min(seconds, start + 5.0)
            segments.append(SimpleNamespace(start=start, end=end, text=f" phrase {number}{self.punctuation} ", words=None))
            start = end
        return iter(segments), SimpleNamespace(language="fr")


def pcm(seconds: float) -> bytes:
    return (np.ones(int(seconds * live.RATE), dtype=np.int16) * 100).tobytes()


def test_an_unfinished_sentence_waits_for_what_follows():
    model = FakeModel()
    transcriber = live.LiveTranscriber()
    transcriber.add(pcm(2.0))
    assert not transcriber.due() and transcriber.step(model) == []
    transcriber.add(pcm(10.0))  # 12 s: sentences at 0-5, 5-10, 10-12 (within the last 2 s: may change)
    assert transcriber.step(model) == [(0.0, 5.0, "phrase 1."), (5.0, 10.0, "phrase 2.")]
    assert transcriber.committed == 10.0 and transcriber.language == "fr"
    assert model.calls[-1][1]["word_timestamps"] is True
    # The audio before the committed point is dropped: memory stays small.
    assert transcriber.base == 10.0 and len(transcriber.samples) == 2 * live.RATE
    transcriber.add(pcm(4.0))  # 10-15 ends after 16 - 2 s: not stable yet
    assert transcriber.step(model) == []
    transcriber.add(pcm(3.0))
    assert transcriber.step(model) == [(10.0, 15.0, "phrase 1.")]
    # The previous lines guide the next pass.
    assert "phrase 2." in model.calls[-1][1]["initial_prompt"]


def test_one_long_segment_is_cut_into_sentences_by_its_word_timings():
    def word(start, end, text):
        return SimpleNamespace(start=start, end=end, word=text)

    class OneSegment(FakeModel):
        def transcribe(self, audio, **options):
            words = [word(0.0, 0.5, " Bonjour"), word(0.5, 1.0, " à"), word(1.0, 1.4, " tous."), word(1.6, 2.0, " Nous"),
                     word(2.0, 2.5, " parlons"), word(2.5, 3.0, " du"), word(3.0, 3.8, " parc."), word(4.0, 4.5, " Le"),
                     word(4.5, 9.0, " parc")]
            return iter([SimpleNamespace(start=0.0, end=9.0, text="…", words=words)]), SimpleNamespace(language="fr")

    transcriber = live.LiveTranscriber()
    transcriber.add(pcm(10.0))
    assert transcriber.step(OneSegment()) == [(0.0, 1.4, "Bonjour à tous."), (1.6, 3.8, "Nous parlons du parc.")]
    assert transcriber.committed == 3.8


def test_a_long_window_moves_on_and_silence_is_skipped():
    class Silent(FakeModel):
        def transcribe(self, audio, **options):
            return iter([]), SimpleNamespace(language=None)

    transcriber = live.LiveTranscriber()
    transcriber.add(pcm(live.MAX_WINDOW_SECONDS + 1))
    assert transcriber.step(Silent()) == []
    assert transcriber.committed == pytest.approx(live.MAX_WINDOW_SECONDS)
    # No sentence end for 25 s: what is stable is kept anyway.
    transcriber = live.LiveTranscriber()
    transcriber.add(pcm(live.MAX_WINDOW_SECONDS + 1))
    assert transcriber.step(FakeModel(punctuation="")) == [(0.0, 20.0, "phrase 1 phrase 2 phrase 3 phrase 4")]


def test_a_restart_skips_what_was_already_transcribed():
    transcriber = live.LiveTranscriber(committed=8.0)
    transcriber.add(pcm(12.0))
    assert transcriber.base == 8.0 and len(transcriber.samples) == 4 * live.RATE


def test_the_decoder_turns_webm_chunks_into_pcm(tmp_path):
    data = make_opus(tmp_path / "a.webm", seconds=3.0).read_bytes()
    decoder = live.LiveDecoder()
    received = b""
    for chunk in chunks_of(data, 2000):
        decoder.feed(chunk)
        received += decoder.take()
    received += decoder.finish()
    assert abs(len(received) / 2 / live.RATE - 3.0) < 0.2


def test_the_live_service_follows_a_recording(client, env, tmp_path):
    data = make_opus(tmp_path / "a.webm", seconds=8.0).read_bytes()
    recording = start(client, live=True, language="fr")
    model = FakeModel()
    service = live.LiveService(model_factory=lambda: model)
    for index, chunk in enumerate(chunks_of(data)):
        client.put(f"/recordings/{recording['id']}/chunks/{index}", content=chunk)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not service.tick():
        time.sleep(0.2)
    with env() as db:
        lines = [row.text for row in db.query(LiveSegment).filter_by(recording_id=recording["id"])]
    assert lines and lines[0] == "phrase 1."
    assert model.calls[0][1]["language"] == "fr"
    # Stopped: its decoder is closed.
    client.delete(f"/recordings/{recording['id']}")
    service.tick()
    assert service.sessions == {}


def two_sided_webm(path: Path) -> Path:
    """You (left, 300 Hz) for 4 s, then them (right, 500 Hz) for 4 s, coming back into your microphone at a fifth."""
    # Commas escaped: inside a filter they would separate its options.
    left = r"if(lt(t\,4)\,0.5*sin(2*PI*300*t)\,0.1*sin(2*PI*500*t))"
    right = r"if(lt(t\,4)\,0\,0.5*sin(2*PI*500*t))"
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-f", "lavfi", "-i", f"aevalsrc={left}|{right}:d=8:s=48000",
         "-c:a", "libopus", "-b:a", "64k", "-f", "webm", str(path)],
        check=True, capture_output=True,
    )
    return path


def test_each_live_line_goes_to_the_side_the_voice_was_on():
    meter = live.SideMeter()
    rate = live.RATE
    t = np.arange(8 * rate) / rate
    mine = np.where(t < 4, 0.5 * np.sin(2 * np.pi * 300 * t), 0.1 * np.sin(2 * np.pi * 500 * t))  # then their echo
    theirs = np.where(t < 4, 0.0, 0.5 * np.sin(2 * np.pi * 500 * t))
    stereo = (np.stack([mine, theirs], axis=1) * 32767).astype(np.int16)
    for part in np.array_split(stereo, 7):  # decoded a little at a time, not on 30 ms boundaries
        meter.add(part)
    assert meter.sides_of([(0.2, 3.8, "Bonjour."), (4.2, 7.8, "Merci."), (20.0, 21.0, "Plus tard.")]) == ["you", "others", None]


def test_a_two_sided_live_transcript_labels_its_lines(client, env, tmp_path):
    data = two_sided_webm(tmp_path / "call.webm").read_bytes()
    recording = start(client, live=True, sides=True)
    model = FakeModel()
    service = live.LiveService(model_factory=lambda: model)
    for index, chunk in enumerate(chunks_of(data)):
        client.put(f"/recordings/{recording['id']}/chunks/{index}", content=chunk)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not service.tick():
        time.sleep(0.2)
    with env() as db:
        lines = [(row.text, row.side) for row in db.query(LiveSegment).filter_by(recording_id=recording["id"])]
    # 0-5 s: you for 4 s, them for 1 s. The transcription still heard the mix of both.
    assert lines[0] == ("phrase 1.", "you")
    assert abs(model.calls[0][0] - 8.0) < 0.3
    client.delete(f"/recordings/{recording['id']}")
    service.tick()


def test_a_failing_live_transcript_leaves_the_recording_going(client, env, tmp_path):
    data = make_opus(tmp_path / "a.webm", seconds=6.0).read_bytes()
    recording = start(client, live=True)
    client.put(f"/recordings/{recording['id']}/chunks/0", content=data)

    class Broken:
        def transcribe(self, audio, **options):
            raise RuntimeError("CUDA out of memory")

    service = live.LiveService(model_factory=Broken)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and client.get(f"/recordings/{recording['id']}").json()["live"]:
        service.tick()
        time.sleep(0.2)
    state = client.get(f"/recordings/{recording['id']}").json()
    assert state["status"] == "RECORDING" and not state["live"] and "continue" in state["live_error"]
