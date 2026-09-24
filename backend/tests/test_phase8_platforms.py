"""Video platforms through yt-dlp (n°12, optional): a fake yt-dlp, no network."""
import httpx
import pytest
from yt_dlp.utils import DownloadError

from app import url_import, worker
from app.models import Video
from tests.test_phase8 import env, web  # noqa: F401  (fixtures reused)

PAGE = "https://www.example-videos.org/watch?v=abc"
LIST = "https://www.example-videos.org/playlist?list=xyz"
PRIVATE = "https://www.example-videos.org/watch?v=private"
LONG = "https://www.example-videos.org/watch?v=long"
CATALOG = {
    PAGE: {"title": "Conférence : l'IA locale", "duration": 635, "uploader": "Blender", "extractor_key": "Youtube",
           "webpage_url": PAGE, "license": "Creative Commons Attribution license (reuse allowed)"},
    LIST: {"title": "Cycle de conférences", "extractor_key": "YoutubeTab", "entries": [
        {"title": "Épisode 1", "url": "https://www.example-videos.org/watch?v=1", "duration": 60},
        {"title": "Épisode 2", "url": "https://www.example-videos.org/watch?v=2", "duration": 90},
    ]},
    LONG: {"title": "Direct de 10 h", "duration": 36000, "extractor_key": "Youtube", "webpage_url": LONG},
}


class FakeYoutubeDL:
    instances = []

    def __init__(self, options):
        self.options = options
        FakeYoutubeDL.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        if url == PRIVATE:
            raise DownloadError("ERROR: [youtube] private: Private video. Sign in if you've been granted access")
        if url not in CATALOG:
            raise DownloadError(f"ERROR: Unsupported URL: {url}")
        info = dict(CATALOG[url])
        if download:
            refused = self.options.get("match_filter", lambda info, **_: None)(info)
            if refused:
                return info
            data = b"a" * 4000
            for hook in self.options.get("progress_hooks", []):
                hook({"status": "downloading", "downloaded_bytes": len(data), "total_bytes": len(data)})
            with open(self.options["outtmpl"].replace("%(ext)s", "m4a"), "wb") as out:
                out.write(data)
        return info


@pytest.fixture
def platforms(web, monkeypatch):  # noqa: F811
    """A platform page (HTML), with the option turned on and a fake yt-dlp."""
    FakeYoutubeDL.instances = []
    for url in (PAGE, LIST, PRIVATE, LONG):
        web[url] = httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=b"<html></html>")
    monkeypatch.setattr(url_import, "platforms_enabled", lambda: True)
    monkeypatch.setattr(url_import, "_youtube_dl", FakeYoutubeDL)
    return web


def test_platforms_are_off_by_default(client, env, web):  # noqa: F811
    web[PAGE] = httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html></html>")
    response = client.post("/imports/url/preview", json={"url": PAGE})
    assert response.status_code == 422 and "Paramètres › Import de liens" in response.json()["detail"]
    assert client.get("/settings/url-import").json() == {"platforms": False}


def test_the_option_is_saved(client, env):  # noqa: F811
    assert client.put("/settings/url-import", json={"platforms": True}).json() == {"platforms": True}
    assert client.get("/settings/url-import").json() == {"platforms": True}
    assert url_import.platforms_enabled() is True


def test_a_platform_video_is_previewed(platforms):
    preview = url_import.probe(PAGE)
    assert preview == {
        "kind": "platform", "url": PAGE, "title": "Conférence : l'IA locale", "duration_seconds": 635,
        "uploader": "Blender", "site": "Youtube", "license": "Creative Commons Attribution license (reuse allowed)",
    }
    # Known sites only: the generic extractor would fetch any page.
    assert FakeYoutubeDL.instances[0].options["allowed_extractors"] == ["default", "-generic"]


def test_a_playlist_is_listed_like_a_feed(platforms):
    preview = url_import.probe(LIST)
    assert preview["kind"] == "feed" and preview["title"] == "Cycle de conférences"
    assert [(e["title"], e["duration_seconds"]) for e in preview["episodes"]] == [("Épisode 1", 60), ("Épisode 2", 90)]


def test_platform_refusals_are_explained(platforms):
    with pytest.raises(url_import.UrlImportError, match="Private video"):
        url_import.probe(PRIVATE)
    with pytest.raises(url_import.UrlImportError, match="Durée maximale"):
        url_import.probe(LONG)
    platforms["https://example.org/blog"] = httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html></html>")
    with pytest.raises(url_import.UrlImportError, match="Aucune vidéo reconnue"):
        url_import.probe("https://example.org/blog")


def test_only_the_audio_is_downloaded(platforms, tmp_path):
    progress = []
    path, name = url_import.download(PAGE, tmp_path, "vid", on_progress=lambda done, total: progress.append((done, total)))
    assert (path.name, name, path.stat().st_size) == ("vid.m4a", "Conférence l'IA locale.m4a", 4000)
    assert progress == [(4000, 4000)]
    options = FakeYoutubeDL.instances[-1].options
    assert options["format"].startswith("bestaudio") and options["noplaylist"] is True
    assert sorted(p.name for p in tmp_path.iterdir()) == ["vid.m4a"]


def test_a_too_long_platform_video_leaves_nothing(platforms, tmp_path):
    with pytest.raises(url_import.UrlImportError, match="Durée maximale"):
        url_import.download_platform(LONG, tmp_path, "vid")
    with pytest.raises(url_import.UrlImportError, match="Private video"):
        url_import.download_platform(PRIVATE, tmp_path, "vid")
    assert list(tmp_path.iterdir()) == []


def test_the_worker_downloads_a_platform_video(client, env, platforms, monkeypatch):  # noqa: F811
    job = client.post("/imports/url", json={"url": PAGE, "title": "Conférence : l'IA locale"}).json()
    monkeypatch.setattr(worker, "ffprobe_duration", lambda _, **__: 635.0)
    path, duration = worker.download_source(job["id"], job["video_id"], PAGE)
    assert path.suffix == ".m4a" and duration == 635.0
    with env() as db:
        video = db.get(Video, job["video_id"])
        assert (video.original_filename, video.source_url, video.path) == ("Conférence : l'IA locale", PAGE, str(path))
