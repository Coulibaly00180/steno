"""Quality runs (n°4), meeting series (n°6), clips (n°7), access control (n°15), first launch (n°19)."""
import json
import subprocess
import tarfile
from datetime import date, datetime, timedelta, timezone

import pytest

from app import auth, clips, llm, main, portable, quality, series, worker
from app.llm import ChunkSummary
from app.models import ActionItem, MeetingSeries, ProcessingJob, QualityRun, Summary, Video, VideoClip
from app.schemas import AccessSettings
from tests.test_phase4 import add_video
from tests.test_phase7 import MODULES, make_session
from app.routes import access as access_routes, clips as clips_routes, quality as quality_routes
from tests.test_upload import SuccessfulQueue

REAL_LOAD = auth.load
DAY = datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc)
LINES = [
    (0.0, 4.0, "Bonjour à tous."),
    (4.0, 9.0, "Premier point, le budget."),
    (9.0, 14.0, "Claire enverra le devis."),
    (14.0, 20.0, "Merci et à lundi."),
]


@pytest.fixture
def env(monkeypatch, tmp_path):
    session = make_session(tmp_path / "p11.db")
    for module in (*MODULES, clips, quality, auth):
        monkeypatch.setattr(module, "SessionLocal", session)
    monkeypatch.setattr(main.settings, "data_dir", tmp_path / "data")
    for folder in ("uploads", "audio", "exports"):
        (tmp_path / "data" / folder).mkdir(parents=True)
    monkeypatch.setattr(main, "Queue", SuccessfulQueue)
    monkeypatch.setattr(quality, "Queue", SuccessfulQueue)
    monkeypatch.setattr(clips_routes, "Queue", SuccessfulQueue)
    return session


def meeting(session, video_id, *, name, day, summary=None, actions=()):
    add_video(session, video_id, LINES, name=name, created_at=day)
    with session() as db:
        if summary:
            db.add(Summary(id=f"s-{video_id}", video_id=video_id, content_markdown=summary, model="m", created_at=day))
        for index, (kind, text, status) in enumerate(actions):
            db.add(ActionItem(id=f"{video_id}-a{index}", video_id=video_id, kind=kind, text=text, status=status, position=index))
        db.commit()


# --- n°7: clips ------------------------------------------------------------------------------------


def test_subtitles_of_a_range_start_at_zero(env):
    add_video(env, "v", LINES)
    with env() as db:
        video = db.get(Video, "v")
        cues = clips.range_cues(video, 5.0, 12.0)
    # The cues cut by the range are kept, shortened to it, shifted to the clip's start.
    assert cues == [(0.0, 4.0, "Premier point, le budget."), (4.0, 7.0, "Claire enverra le devis.")]


def test_the_ffmpeg_call_follows_the_options(tmp_path):
    source, output, srt = tmp_path / "in.mp4", tmp_path / "out.mp4", tmp_path / "s.srt"
    burned = clips.ffmpeg_command(source, output, 12.5, 30.0, picture=True, subtitles="burned", srt=srt)
    assert burned[burned.index("-ss") + 1] == "12.500" and burned[burned.index("-t") + 1] == "17.500"
    assert any(part.startswith(f"subtitles={srt}") for part in burned) and "mov_text" not in burned
    track = clips.ffmpeg_command(source, output, 0, 10, picture=True, subtitles="track", srt=srt, language="fr")
    assert "mov_text" in track and "language=fra" in track and track.count("-i") == 2
    audio = clips.ffmpeg_command(source, tmp_path / "out.m4a", 0, 10, picture=False, subtitles="burned", srt=srt)
    assert "-vn" in audio and "libx264" not in audio and not any("subtitles=" in part for part in audio)


def test_a_clip_needs_a_processed_video_with_its_media(client, env):
    add_video(env, "pending", LINES, status="QUEUED")
    assert client.post("/videos/pending/clips", json={"start_seconds": 0, "end_seconds": 5}).status_code == 409
    add_video(env, "v", LINES)
    response = client.post("/videos/v/clips", json={"start_seconds": 0, "end_seconds": 5})
    assert response.status_code == 409 and "supprimés" in response.json()["detail"]
    media = main.settings.uploads_dir / "v.mp4"
    media.write_bytes(b"x")
    with env() as db:
        db.get(Video, "v").path = str(media)
        db.commit()
    assert client.post("/videos/v/clips", json={"start_seconds": 4, "end_seconds": 4.5}).status_code == 422
    translation = client.post("/videos/v/clips", json={"start_seconds": 0, "end_seconds": 5, "subtitle_source": "translation"})
    assert translation.status_code == 409


def test_a_clip_is_queued_listed_and_deleted(client, env):
    add_video(env, "v", LINES)
    media = main.settings.uploads_dir / "v.mp4"
    media.write_bytes(b"x")
    with env() as db:
        db.get(Video, "v").path = str(media)
        db.commit()
    created = client.post("/videos/v/clips", json={"start_seconds": 4, "end_seconds": 99, "subtitles": "burned"}).json()
    # The end is brought back to the video's length; the default title gives the range.
    assert created["status"] == "QUEUED" and created["end_seconds"] == 20.0 and created["title"].startswith("Extrait 00:00:04")
    with env() as db:
        job = db.get(ProcessingJob, created["job_id"])
        assert job.kind == "CLIP" and job.rq_job_id == "rq-job-id"
    listed = client.get("/videos/v/clips").json()
    assert [clip["id"] for clip in listed] == [created["id"]] and listed[0]["progress"] == 0
    # A clip is never « the video's processing ».
    assert client.get("/videos/v").json()["job"] is None
    assert client.get(f"/clips/{created['id']}/file").status_code == 404
    # Deleting a queued clip cancels its job.
    assert client.delete(f"/clips/{created['id']}").json() == {"deleted": True}
    assert client.get("/videos/v/clips").json() == []
    with env() as db:
        assert db.get(ProcessingJob, created["job_id"]).status == "CANCELLED"


def test_a_failed_job_shows_on_its_clip(client, env):
    add_video(env, "v", LINES)
    with env() as db:
        db.add(ProcessingJob(id="j", video_id="v", kind="CLIP", status="CANCELLED", stage="CANCELLED", progress=40))
        db.add(VideoClip(id="c", video_id="v", job_id="j", title="t", start_seconds=0, end_seconds=5, status="RUNNING"))
        db.commit()
    assert client.get("/videos/v/clips").json()[0]["status"] == "CANCELLED"


def _make_media(path, seconds=6):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=25:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:v", "libx264", "-preset", "ultrafast",
         "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )


def _probe(path):
    output = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(output)


@pytest.mark.parametrize("subtitles, expected_streams", [("burned", {"video", "audio"}), ("track", {"video", "audio", "subtitle"})])
def test_the_worker_cuts_the_clip(env, subtitles, expected_streams):
    add_video(env, "v", LINES)
    media = main.settings.uploads_dir / "v.mp4"
    _make_media(media)
    with env() as db:
        db.get(Video, "v").path = str(media)
        db.add(ProcessingJob(id="j", video_id="v", kind="CLIP", status="QUEUED", stage="QUEUED", progress=0))
        db.add(VideoClip(id="clip1", video_id="v", job_id="j", title="Le budget", start_seconds=1.0, end_seconds=4.0,
                         subtitles=subtitles, status="QUEUED"))
        db.commit()
    worker.run_clip("j")
    with env() as db:
        clip = db.get(VideoClip, "clip1")
        job = db.get(ProcessingJob, "j")
        assert (clip.status, job.status) == ("READY", "COMPLETED"), clip.error
        path = clips.clip_path(clip)
        assert path.name.startswith("le-budget-00h00m01s-00h00m04s") and clip.size_bytes == path.stat().st_size
    info = _probe(path)
    assert abs(float(info["format"]["duration"]) - 3.0) < 0.3
    assert {stream["codec_type"] for stream in info["streams"]} == expected_streams
    # No temporary file left next to it.
    assert [item.name for item in clips.clips_dir("v").iterdir()] == [path.name]


def test_an_audio_source_gives_an_audio_clip(env):
    add_video(env, "v", LINES)
    media = main.settings.uploads_dir / "v.m4a"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=duration=5", "-c:a", "aac", str(media)], check=True)
    with env() as db:
        db.get(Video, "v").path = str(media)
        db.add(ProcessingJob(id="j", video_id="v", kind="CLIP", status="QUEUED", stage="QUEUED", progress=0))
        db.add(VideoClip(id="clip1", video_id="v", job_id="j", title="Son", start_seconds=0, end_seconds=2, subtitles="burned"))
        db.commit()
    worker.run_clip("j")
    with env() as db:
        clip = db.get(VideoClip, "clip1")
        assert clip.status == "READY" and clip.filename.endswith(".m4a")


def test_a_clip_whose_media_vanished_fails_alone(env):
    add_video(env, "v", LINES)
    with env() as db:
        db.add(ProcessingJob(id="j", video_id="v", kind="CLIP", status="QUEUED", stage="QUEUED", progress=0))
        db.add(VideoClip(id="clip1", video_id="v", job_id="j", title="t", start_seconds=0, end_seconds=2))
        db.commit()
    worker.run_clip("j")
    with env() as db:
        clip, video = db.get(VideoClip, "clip1"), db.get(Video, "v")
        assert clip.status == "FAILED" and "supprimés" in clip.error and video.status == "COMPLETED"


# --- n°6: series of meetings -------------------------------------------------------------------------


@pytest.mark.parametrize("title, key", [
    ("Comité budget 2026-09-24.mp4", "comite budget"),
    ("Comité budget du 17 septembre", "comite budget"),
    ("Point hebdo #12", "point hebdo"),
    ("Réunion projet S38 - lundi", "reunion projet"),
    ("Weekly sync week 3.m4a", "weekly sync"),
])
def test_recurring_titles_meet_once_dates_and_numbers_are_gone(title, key):
    assert series.title_key(title) == key


@pytest.mark.parametrize("title, name", [
    ("Comité budget 2026-09-24", "Comité budget"),
    ("Comité budget du 17 septembre", "Comité budget"),
    ("Point hebdo #12 - lundi", "Point hebdo"),
    ("Revue projet (semaine 38)", "Revue projet"),
])
def test_a_series_is_named_after_the_title_without_its_date(title, name):
    assert series.display_name(title) == name


def test_recurring_meetings_are_suggested(client, env):
    meeting(env, "a", name="Comité budget 2026-09-14.mp4", day=DAY)
    meeting(env, "b", name="Comité budget 2026-09-21.mp4", day=DAY + timedelta(days=7))
    meeting(env, "c", name="Interview Karim.mp4", day=DAY)
    suggestions = client.get("/series/suggestions").json()
    assert [(item["name"], [v["id"] for v in item["videos"]], item["series_id"]) for item in suggestions] == [
        ("Comité budget", ["a", "b"], None),
    ]
    created = client.post("/series", json={"name": "Comité budget", "video_ids": ["a", "b"]}).json()
    assert client.get("/series/suggestions").json() == []
    # A new meeting with the same title: suggested for the existing series.
    meeting(env, "d", name="Comité budget 2026-09-28.mp4", day=DAY + timedelta(days=14))
    suggestion = client.get("/videos/d/series").json()["suggestion"]
    assert suggestion["series_id"] == created["id"] and [v["id"] for v in suggestion["videos"]] == ["d"]
    assert client.post("/series", json={"name": "comité BUDGET"}).status_code == 409


def test_a_series_lists_its_meetings_open_actions_and_decisions(client, env):
    meeting(env, "a", name="Comité 1.mp4", day=DAY, actions=[
        ("action", "Envoyer le devis", "open"), ("decision", "Budget validé", "open"), ("action", "Relancer Acme", "done"),
    ])
    meeting(env, "b", name="Comité 2.mp4", day=DAY + timedelta(days=7), actions=[("action", "Préparer le salon", "open")])
    meeting(env, "other", name="Autre.mp4", day=DAY, actions=[("action", "Hors série", "open")])
    created = client.post("/series", json={"name": "Comité", "video_ids": ["b", "a"]}).json()
    detail = client.get(f"/series/{created['id']}").json()
    assert [m["id"] for m in detail["meetings"]] == ["a", "b"]
    assert sorted(item["text"] for item in detail["open_actions"]) == ["Envoyer le devis", "Préparer le salon"]
    assert [item["text"] for item in detail["decisions"]] == ["Budget validé"] and detail["done_actions"] == 1
    listed = client.get("/series").json()
    assert listed == [{"id": created["id"], "name": "Comité", "meetings": 2, "last_meeting_at": listed[0]["last_meeting_at"],
                       "open_actions": 2}]
    actions_page = client.get("/actions", params={"series_id": created["id"], "status": "open", "kind": "action"}).json()
    assert sorted(item["text"] for item in actions_page["items"]) == ["Envoyer le devis", "Préparer le salon"]
    position = client.get("/videos/b/series").json()
    assert position["series"]["position"] == 2 and position["previous"]["id"] == "a" and position["next"] is None
    # Out of the series, then the series deleted: the meetings stay.
    assert client.put("/videos/b/series", json={"series_id": None}).json()["series"] is None
    assert client.delete(f"/series/{created['id']}").json() == {"deleted": True}
    with env() as db:
        assert db.get(Video, "a").series_id is None and db.get(MeetingSeries, created["id"]) is None


def test_what_changed_since_the_previous_meeting(client, env, monkeypatch):
    meeting(env, "a", name="Comité 1.mp4", day=DAY, summary="# Points\n- Budget en discussion\n- Salon de Lyon",
            actions=[("action", "Envoyer le devis à Acme", "open"), ("action", "Réserver la salle", "open")])
    meeting(env, "b", name="Comité 2.mp4", day=DAY + timedelta(days=7),
            summary="# Points\n- Budget validé\n- Nouveau client Xylo\n- Claire a envoyé le devis à Acme")
    created = client.post("/series", json={"name": "Comité", "video_ids": ["a", "b"]}).json()
    assert client.get("/videos/a/series/changes").json()["status"] == "first"
    prompts = []

    def answer(prompt, **kwargs):
        prompts.append(prompt)
        assert kwargs["json_schema"] == series.SCHEMA
        return json.dumps({
            "nouveau": ["Nouveau client Xylo", "Nouveau client Xylo", "x"],
            "evolue": ["Budget : validé, alors qu'il était en discussion"],
            "plus_mentionne": ["Salon de Lyon"],
            "actions_faites": [{"numero": 1, "preuve": "Claire a envoyé le devis à Acme"}, {"numero": 7, "preuve": "inventé"}],
        }), "stop"

    monkeypatch.setattr(llm, "chat_completion", answer)
    changes = client.get("/videos/b/series/changes").json()
    assert "1. Envoyer le devis à Acme" in prompts[0] and "2. Réserver la salle" in prompts[0] and "Comité" in prompts[0]
    assert changes["status"] == "ready" and changes["previous"]["id"] == "a" and changes["series"]["id"] == created["id"]
    assert changes["new"] == ["Nouveau client Xylo"] and changes["dropped"] == ["Salon de Lyon"]
    assert changes["changed"] == ["Budget : validé, alors qu'il était en discussion"]
    # Only the actions that exist are said done, with the sentence that shows it; nothing is closed by itself.
    assert [(item["id"], item["evidence"]) for item in changes["resolved"]] == [("a-a0", "Claire a envoyé le devis à Acme")]
    assert len(changes["open_actions"]) == 2
    # Cached until something changes; closing an action asks again.
    client.get("/videos/b/series/changes")
    assert len(prompts) == 1
    client.patch("/actions/a-a0", json={"status": "done"})
    client.get("/videos/b/series/changes")
    assert len(prompts) == 2 and "Envoyer le devis" not in prompts[1]
    client.get("/videos/b/series/changes", params={"refresh": True})
    assert len(prompts) == 3


@pytest.mark.parametrize("evidence, kept", [
    ("Claire a envoyé le devis à Acme lundi", True),
    ("Karim a présenté son projet, validé par le comité", True),
    ("Sophie n'a pas encore relancé le fournisseur, elle le fera avant vendredi", False),
    ("Le devis doit être envoyé", False),
    ("Claire enverra le devis ; Karim va le relire", False),
    ("The quote has not been sent yet", False),
])
def test_a_proof_that_says_not_done_is_refused(evidence, kept):
    result = series.validate({"actions_faites": [{"numero": 1, "preuve": evidence}]}, ["a1"])
    assert bool(result["resolved"]) is kept


def test_a_meeting_without_series_has_no_changes(client, env):
    meeting(env, "a", name="Seul.mp4", day=DAY)
    assert client.get("/videos/a/series/changes").json() == {"status": "none"}


def test_series_and_actions_travel_in_the_archive(env, tmp_path, monkeypatch):
    meeting(env, "a", name="Comité 1.mp4", day=DAY, summary="# R", actions=[("action", "Envoyer le devis", "done")])
    with env() as db:
        db.add(MeetingSeries(id="s1", name="Comité"))
        db.flush()
        db.get(Video, "a").series_id = "s1"
        item = db.get(ActionItem, "a-a0")
        item.owner, item.due_date = "Claire", date(2026, 9, 28)
        db.commit()
    archive = tmp_path / "library.tar"
    archive.write_bytes(b"".join(portable.export_stream(False)))
    other = make_session(tmp_path / "other.db")
    for module in MODULES:
        monkeypatch.setattr(module, "SessionLocal", other)
    monkeypatch.setattr(main.settings, "data_dir", tmp_path / "other-data")
    main.settings.uploads_dir.mkdir(parents=True)
    monkeypatch.setattr(worker, "enqueue_index_job", lambda video_id: None)
    with other() as db:
        db.add(MeetingSeries(id="existing", name="comité"))
        db.commit()
    assert portable.import_archive(archive)["videos_added"] == 1
    with other() as db:
        video = db.get(Video, "a")
        # Joined to the series of the same name.
        assert video.series_id == "existing"
        items = db.query(ActionItem).filter_by(video_id="a").all()
        assert [(i.text, i.owner, i.due_date, i.status) for i in items] == [("Envoyer le devis", "Claire", date(2026, 9, 28), "done")]
    with tarfile.open(archive) as handle:
        document = json.loads(handle.extractfile("videos/a/video.json").read())
    assert document["series"] == "Comité"


# --- n°4: quality runs -------------------------------------------------------------------------------


REFERENCE = {"language": "fr", "topics": {"budget": "budget", "devis": "devis", "salon": "salon"}, "chapter_starts": [0, 60]}


def test_a_summary_is_scored_against_its_reference():
    summary = "Le budget est validé. Claire enverra le devis."
    # A chapter start counts within 2 minutes: 0 is found, 600 is not (the nearest chapter starts at 200).
    score = quality.score_item(summary, 600, [(0.0, "Intro"), (200.0, "Suite")], REFERENCE | {"chapter_starts": [0, 600]}, "fr")
    assert (score["covered"], score["topics"], score["missing"]) == (2, 3, ["salon"])
    assert score["words"] == 8 and score["budget"] == 250 and score["length_ratio"] == 0.032
    assert (score["chapter_hits"], score["chapter_expected"], score["language_ok"]) == (1, 2, True)


def test_the_comparison_tells_what_got_worse():
    before = {"a": {"covered": 3, "topics": 3, "length_ratio": 0.9, "chapter_hits": 2, "chapter_expected": 2, "language_ok": True}}
    after = {"a": {"covered": 2, "topics": 3, "length_ratio": 1.8, "chapter_hits": 2, "chapter_expected": 2, "language_ok": False,
                   "language": "en"}}
    changes = quality.compare(after, before)
    assert [(c["measure"], c["worse"]) for c in changes] == [("coverage", True), ("length", True), ("language", True)]
    assert changes[0]["text"] == "couverture 3/3 → 2/3" and changes[1]["text"] == "longueur 90% → 180% du budget"
    assert quality.compare(before, after)[0]["worse"] is False


def _corpus(folder, with_media=True):
    corpus = folder / "corpus"
    corpus.mkdir(parents=True)
    (corpus / "sources.json").write_text(json.dumps({"clip-fr": {"file": "clip.ogv", "duration_seconds": 600}}), encoding="utf-8")
    (corpus / "references.json").write_text(json.dumps({"clip-fr": REFERENCE}), encoding="utf-8")
    if with_media:
        (corpus / "clip.ogv").write_bytes(b"media")
    return corpus


def test_a_run_replays_the_corpus_with_the_analysis_code(env, monkeypatch):
    _corpus(main.settings.data_dir)
    monkeypatch.setattr(quality, "transcript_for", lambda key, media, **_: {
        "rows": [[0.0, 30.0, "Le budget et le devis."], [65.0, 90.0, "Le salon de Lyon."]], "language": "fr", "seconds": 4, "cached": False,
    })
    monkeypatch.setattr(worker, "release_whisper_model", lambda: None)
    monkeypatch.setattr(worker, "summarize_chunk", lambda chunk, language, **kw: ChunkSummary(
        text="Budget, devis.", chapters=[("00:00:00", "Budget"), ("00:01:05", "Salon")],
    ))
    monkeypatch.setattr(worker, "final_summary", lambda *args, **kw: "Le budget et le devis sont traités.")
    run = quality.enqueue("quick", "manual")
    assert run.status == "QUEUED" and run.rq_job_id == "rq-job-id" and len(run.prompt_version) == 8
    quality.run_quality(run.id)
    with env() as db:
        stored = db.get(QualityRun, run.id)
        results = json.loads(stored.results)
    assert stored.status == "COMPLETED" and stored.score == pytest.approx(66.7)
    item = results["clip-fr"]
    assert (item["covered"], item["missing"], item["chapter_hits"], item["transcription_seconds"]) == (2, ["salon"], 2, 4)
    assert item["summary"] == "Le budget et le devis sont traités."


def test_a_missing_file_is_reported_not_fatal(env, monkeypatch):
    _corpus(main.settings.data_dir, with_media=False)
    monkeypatch.setattr(worker, "release_whisper_model", lambda: None)
    run = quality.enqueue("quick", "manual")
    quality.run_quality(run.id)
    with env() as db:
        stored = db.get(QualityRun, run.id)
    assert stored.status == "COMPLETED" and json.loads(stored.results) == {"clip-fr": {"error": "Fichier absent du corpus"}}


def test_a_run_starts_by_itself_when_prompts_or_models_change(env, monkeypatch):
    assert quality.maybe_schedule() is None  # no corpus on this machine
    _corpus(main.settings.data_dir)
    first = quality.maybe_schedule()
    assert first is not None
    assert quality.maybe_schedule() is None  # one already waiting
    with env() as db:
        db.get(QualityRun, first).status = "COMPLETED"
        db.commit()
    assert quality.maybe_schedule() is None  # nothing changed
    monkeypatch.setattr(quality.ai_models, "llm_model", lambda: "qwen3:14b")
    second = quality.maybe_schedule()
    with env() as db:
        assert db.get(QualityRun, second).llm_model == "qwen3:14b"
        db.get(QualityRun, second).status = "COMPLETED"
        db.commit()
    monkeypatch.setattr(quality.ai_models, "llm_model", lambda: "gemma3:12b")
    quality_routes.put_quality_settings(quality_routes.QualitySettings(auto=False))
    assert quality.maybe_schedule() is None


def test_the_quality_page(client, env):
    assert client.post("/quality/runs", json={"scope": "quick"}).status_code == 409
    _corpus(main.settings.data_dir)
    overview = client.get("/quality").json()
    assert overview["available"] and overview["items"][0]["present"] and not overview["current"]["tested"]
    started = client.post("/quality/runs", json={"scope": "quick"}).json()
    assert started["status"] == "QUEUED" and started["trigger"] == "manual"
    assert client.post("/quality/runs", json={"scope": "quick"}).status_code == 409
    assert client.delete(f"/quality/runs/{started['id']}").status_code == 409
    assert client.post(f"/quality/runs/{started['id']}/cancel").json() == {"cancelled": True}
    assert client.delete(f"/quality/runs/{started['id']}").json() == {"deleted": True}


def test_a_run_is_compared_with_the_previous_one_of_its_scope(client, env):
    for index, (covered, ratio) in enumerate([(3, 1.0), (2, 1.0)]):
        with env() as db:
            db.add(QualityRun(
                id=f"r{index}", status="COMPLETED", scope="quick", trigger="auto", llm_model="m", whisper_model="w",
                prompt_version="p", fingerprint=str(index), created_at=DAY + timedelta(hours=index),
                results=json.dumps({"a": {"covered": covered, "topics": 3, "length_ratio": ratio}}), score=100 * covered / 3,
            ))
            db.commit()
    runs = client.get("/quality").json()["runs"]
    assert [run["id"] for run in runs] == ["r1", "r0"]
    assert runs[0]["previous_id"] == "r0" and runs[0]["changes"][0]["text"] == "couverture 3/3 → 2/3"
    assert runs[1]["previous_id"] is None and runs[1]["changes"] == []


# --- n°15: access control ----------------------------------------------------------------------------


def test_passwords_are_hashed_and_checked():
    stored = auth.hash_password("correct horse")
    assert stored.startswith("scrypt$") and "correct" not in stored
    assert auth.verify_password("correct horse", stored) and not auth.verify_password("wrong", stored)
    assert not auth.verify_password("x", None) and not auth.verify_password("x", "md5$abc")


def test_sessions_expire_and_close_when_the_password_changes():
    config = AccessSettings(password_hash="h", secret="s", version=3)
    token = auth.make_token(config, now=1000)
    assert auth.valid_token(token, config, now=2000)
    assert not auth.valid_token(token, config, now=1000 + auth.SESSION_SECONDS + 1)
    assert not auth.valid_token(token, config.model_copy(update={"version": 4}), now=2000)
    assert not auth.valid_token(token, config.model_copy(update={"secret": "other"}), now=2000)
    assert not auth.valid_token(token[:-2] + "xx", config, now=2000) and not auth.valid_token("garbage", config)


@pytest.mark.parametrize("password, require_local, remote, token, expected", [
    (False, False, False, False, 200),  # this computer, no password: as before
    (False, False, True, False, 403),   # the network is closed until a password is set
    (True, False, False, False, 200),   # the password protects the network access only
    (True, False, True, False, 401),
    (True, False, True, True, 200),
    (True, True, False, False, 401),    # asked here too
    (True, True, False, True, 200),
])
def test_who_may_go_through(password, require_local, remote, token, expected):
    config = AccessSettings(password_hash="h" if password else None, secret="s", require_local=require_local)
    decision = auth.decide("/videos", remote=remote, token=auth.make_token(config) if token else None, config=config)
    assert (decision.status if not decision.allowed else 200) == expected
    assert auth.decide("/auth/status", remote=True, token=None, config=config).allowed
    assert auth.decide("/ready", remote=True, token=None, config=config).allowed


class FakeRedis:
    def __init__(self):
        self.values = {}

    def incr(self, key):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    def expire(self, key, seconds):
        pass

    def delete(self, key):
        self.values.pop(key, None)


@pytest.fixture
def guarded(env, monkeypatch):
    """The real access settings, read from the test database."""
    monkeypatch.setattr(auth, "load", REAL_LOAD)
    monkeypatch.setattr(auth, "cached", lambda: None)
    auth.forget()
    redis = FakeRedis()
    monkeypatch.setattr(access_routes.Redis, "from_url", lambda *args, **kwargs: redis)
    yield redis
    auth.forget()


REMOTE = {"X-Steno-Remote": "1", "X-Forwarded-For": "192.168.1.30"}


def test_the_network_access_needs_a_password(client, guarded):
    response = client.get("/tags", headers=REMOTE)
    assert response.status_code == 403 and "définissez d'abord un mot de passe" in response.json()["detail"]
    assert client.get("/tags").status_code == 200
    # The network cannot set the first password.
    assert client.put("/auth/password", json={"new_password": "un mot de passe"}, headers=REMOTE).status_code == 403
    assert client.put("/auth/password", json={"new_password": "court"}).status_code == 422
    local = client.put("/auth/password", json={"new_password": "un mot de passe"})
    assert local.json()["password_set"] and auth.COOKIE in local.cookies

    remote = main.app  # a second browser, on the network, without the cookie
    from fastapi.testclient import TestClient
    phone = TestClient(remote)
    assert phone.get("/tags", headers=REMOTE).status_code == 401
    status = phone.get("/auth/status", headers=REMOTE).json()
    assert status["required"] and not status["authenticated"]
    assert phone.post("/auth/login", json={"password": "faux"}, headers=REMOTE).status_code == 401
    assert phone.post("/auth/login", json={"password": "un mot de passe"}, headers=REMOTE).status_code == 200
    assert phone.get("/tags", headers=REMOTE).status_code == 200
    # This computer still goes through without it.
    assert TestClient(remote).get("/tags").status_code == 200

    # Changing the password (here, without the current one) closes the phone's session.
    client.put("/auth/password", json={"new_password": "un autre mot de passe"})
    assert phone.get("/tags", headers=REMOTE).status_code == 401
    # From the network, the current password is asked.
    phone.post("/auth/login", json={"password": "un autre mot de passe"}, headers=REMOTE)
    assert phone.put("/auth/password", json={"new_password": "encore un autre"}, headers=REMOTE).status_code == 403
    assert phone.put("/auth/password", json={"current_password": "un autre mot de passe", "require_local": True},
                     headers=REMOTE).status_code == 200
    assert TestClient(remote).get("/tags").status_code == 401
    assert phone.post("/auth/logout").json() == {"authenticated": False}


def test_logins_are_rate_limited(client, guarded):
    client.put("/auth/password", json={"new_password": "un mot de passe"})
    other = {"X-Steno-Remote": "1", "X-Forwarded-For": "10.0.0.9"}
    for _ in range(auth.LOGIN_ATTEMPTS):
        assert client.post("/auth/login", json={"password": "faux"}, headers=other).status_code == 401
    assert client.post("/auth/login", json={"password": "un mot de passe"}, headers=other).status_code == 429
    # Another device is not blocked.
    assert client.post("/auth/login", json={"password": "un mot de passe"}, headers=REMOTE).status_code == 200


def test_removing_the_password_closes_the_network_again(client, guarded):
    client.put("/auth/password", json={"new_password": "un mot de passe", "require_local": False})
    assert client.put("/auth/password", json={"require_local": True}).json()["require_local"]
    removed = client.put("/auth/password", json={"new_password": None, "current_password": "un mot de passe"}).json()
    assert not removed["password_set"] and not removed["require_local"]
    assert client.get("/tags", headers=REMOTE).status_code == 403


def test_the_network_page(client, env, monkeypatch):
    monkeypatch.setattr(main.settings, "lan_address", "192.168.1.20")
    info = client.get("/network").json()
    assert info == {"https_ready": False, "address": "192.168.1.20", "port": 8443, "url": "https://192.168.1.20:8443"}
    assert client.get("/network/certificate").status_code == 404
    certificate = main.settings.data_dir / access_routes.HTTPS_ROOT_CERTIFICATE
    certificate.parent.mkdir(parents=True)
    certificate.write_text("-----BEGIN CERTIFICATE-----")
    assert client.get("/network").json()["https_ready"]
    assert client.get("/network/certificate", headers=REMOTE).text.startswith("-----BEGIN")


# --- n°19: first launch ------------------------------------------------------------------------------


def test_the_first_launch_assistant_shows_once(client, env):
    assert client.get("/settings/onboarding").json() == {"done": False}
    assert client.put("/settings/onboarding", json={"done": True}).json() == {"done": True}
    client.put("/settings/onboarding", json={"done": False})
    # An installation that already has videos never shows it.
    add_video(env, "v", LINES)
    assert client.get("/settings/onboarding").json() == {"done": True}


def test_the_summary_code_is_shared_with_the_quality_runs(monkeypatch):
    """compose_summary writes nothing: the FULL job and the quality runs call the same code."""
    monkeypatch.setattr(worker, "summarize_chunk", lambda chunk, language, **kw: ChunkSummary(text="Bloc.", chapters=[]))
    monkeypatch.setattr(worker, "final_summary", lambda *args, **kw: "Final.")
    stages = []
    final, blocks, chapters, key = worker.compose_summary(
        source_text="[00:00:00] Bonjour.", output_language="français", summary_length="standard", duration_seconds=60,
        template_prompt="# R", custom_prompt=None, vocabulary=[], on_stage=lambda stage, progress: stages.append(stage),
    )
    assert final == "Final." and blocks[0].endswith("Bloc.") and chapters == [] and key
    assert stages[0] == "SUMMARIZING_CHUNKS" and stages[-1] == "SUMMARIZING_FINAL"
    # Same text: the blocks come from the cache.
    stages.clear()
    worker.compose_summary(
        source_text="[00:00:00] Bonjour.", output_language="français", summary_length="standard", duration_seconds=60,
        template_prompt="# R", custom_prompt=None, vocabulary=[], on_stage=lambda stage, progress: stages.append((stage, progress)),
        cache={"key": key, "blocks": blocks, "chapters": []},
    )
    assert stages[0] == ("SUMMARIZING_CHUNKS", 85)

