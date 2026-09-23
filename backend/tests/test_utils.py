from types import SimpleNamespace

from app import utils


def test_ffprobe_duration_has_a_bounded_subprocess(monkeypatch, tmp_path):
    captured = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(stdout='{"format": {"duration": "12.5"}}')

    monkeypatch.setattr(utils.subprocess, "run", fake_run)

    assert utils.ffprobe_duration(tmp_path / "clip.mp4", timeout_seconds=17) == 12.5
    assert captured["kwargs"]["timeout"] == 17
