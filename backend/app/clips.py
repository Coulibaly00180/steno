"""Video clips (n°7): a chapter or a range cut out with ffmpeg, to share the right passage.

The clip is re-encoded (H.264 + AAC, MP4): a stream copy would start at the
previous keyframe, seconds before the chosen moment. Subtitles, optional, come
from the transcript (or its translation) of the range: a track players can
switch on, or drawn onto the picture for platforms that ignore tracks. A
source with no picture (audio import, media compacted to audio) gives an M4A.
"""
import json
import logging
import re
import subprocess
import unicodedata
from collections.abc import Callable
from pathlib import Path

from .config import settings
from .db import SessionLocal
from .exports import _srt, translated_cues
from .models import Video, VideoClip
from .speakers import speaker_labels
from .storage import StorageError, work_audio_path

logger = logging.getLogger(__name__)

SUBTITLE_MODES = ("none", "track", "burned")
SUBTITLE_SOURCES = ("original", "translation")
MIN_CLIP_SECONDS = 1.0
# A clip is a passage to share: a whole 6-hour video is exported as it is.
MAX_CLIP_SECONDS = 3 * 3600
# Burned subtitles: readable on a phone, outlined to stay legible on any picture.
BURNED_STYLE = "FontName=DejaVu Sans,FontSize=18,Outline=1.5,Shadow=0,MarginV=24"
_LANGUAGE_TAGS = {"fr": "fra", "en": "eng", "es": "spa", "de": "deu", "it": "ita", "pt": "por", "nl": "nld"}


def clips_dir(video_id: str) -> Path:
    """Next to the video's exports: deleting the video deletes its clips."""
    return settings.exports_dir / video_id / "clips"


def clip_path(clip: VideoClip) -> Path | None:
    return clips_dir(clip.video_id) / clip.filename if clip.filename else None


def source_media(video: Video) -> Path:
    """The file to cut: the source, else the work audio track. Raises when neither is left."""
    source = Path(video.path)
    if source.is_file():
        return source
    audio = work_audio_path(video.id)
    if audio.is_file():
        return audio
    raise StorageError("Les médias de cette vidéo ont été supprimés : impossible d'en extraire un passage")


def has_picture(path: Path) -> bool:
    """True when the file has a real video stream (a cover image in an MP3 does not count)."""
    output = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=codec_type:stream_disposition=attached_pic",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=settings.ffprobe_timeout_seconds,
    )
    if output.returncode != 0:
        return False
    try:
        streams = json.loads(output.stdout or "{}").get("streams") or []
    except ValueError:
        return False
    return any(not (stream.get("disposition") or {}).get("attached_pic") for stream in streams)


def safe_title(value: str) -> str:
    """A file name every system accepts, from the clip title."""
    ascii_text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "-", ascii_text).strip("-").lower()
    return text[:60] or "extrait"


def _clock(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600:02d}h{seconds // 60 % 60:02d}m{seconds % 60:02d}s"


def output_name(clip: VideoClip, audio_only: bool) -> str:
    return f"{safe_title(clip.title)}-{_clock(clip.start_seconds)}-{_clock(clip.end_seconds)}-{clip.id[:8]}{'.m4a' if audio_only else '.mp4'}"


def range_cues(video: Video, start: float, end: float, source: str = "original") -> list[tuple[float, float, str]]:
    """Subtitles of [start, end], shifted so the clip starts at 0."""
    if source == "translation" and video.translated_text:
        rows = translated_cues(video.translated_text, [(s.start_seconds, s.end_seconds) for s in video.segments])
    else:
        labels = speaker_labels(video)
        rows = [
            (s.start_seconds, s.end_seconds, f"{labels[s.speaker_id]} : {s.text}" if s.speaker_id in labels else s.text)
            for s in video.segments
        ]
    cues = []
    for cue_start, cue_end, text in rows:
        if cue_end <= start or cue_start >= end or not text.strip():
            continue
        cues.append((max(0.0, cue_start - start), min(end, cue_end) - start, text.strip()))
    return cues


def ffmpeg_command(
    source: Path, output: Path, start: float, end: float, *,
    picture: bool, subtitles: str = "none", srt: Path | None = None, language: str | None = None,
) -> list[str]:
    """The ffmpeg call. Seeking before -i is exact when re-encoding, and restarts the timestamps at 0."""
    command = ["ffmpeg", "-y", "-nostdin", "-v", "error", "-progress", "pipe:1", "-nostats",
               "-ss", f"{start:.3f}", "-i", str(source)]
    track = picture and subtitles == "track" and srt is not None
    if track:
        command += ["-i", str(srt)]
    command += ["-t", f"{end - start:.3f}"]
    if not picture:
        return command + ["-map", "0:a:0", "-vn", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(output)]
    command += ["-map", "0:v:0", "-map", "0:a:0?"]
    if subtitles == "burned" and srt is not None:
        # The path is ours (clip id only), no character to escape in the filter.
        command += ["-vf", f"subtitles={srt}:force_style='{BURNED_STYLE}'"]
    command += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k"]
    if track:
        command += ["-map", "1:0", "-c:s", "mov_text"]
        tag = _LANGUAGE_TAGS.get((language or "").split("-")[0].lower())
        if tag:
            command += ["-metadata:s:s:0", f"language={tag}"]
    return command + ["-movflags", "+faststart", str(output)]


def _run(command: list[str], duration: float, on_progress: Callable[[float], None] | None) -> None:
    """Run ffmpeg, reporting the share of the clip written (from -progress's out_time_us)."""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for line in process.stdout:  # type: ignore[union-attr]
            if on_progress and line.startswith("out_time_us=") and duration > 0:
                try:
                    done = int(line.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    continue
                on_progress(max(0.0, min(1.0, done / duration)))
        process.wait(timeout=settings.ffmpeg_timeout_seconds)
    except BaseException:
        process.kill()
        process.wait()
        raise
    if process.returncode != 0:
        error = (process.stderr.read() if process.stderr else "")[-500:]
        logger.warning("ffmpeg clip failed: %s", error)
        raise StorageError("La découpe a échoué (ffmpeg)")


def render(clip_id: str, on_progress: Callable[[float], None] | None = None) -> Path:
    """Write the clip file; returns its path. The caller records the result.

    Everything ffmpeg needs is read first: no database transaction stays open
    while it encodes.
    """
    with SessionLocal() as db:
        clip = db.get(VideoClip, clip_id)
        video = db.get(Video, clip.video_id) if clip else None
        if clip is None or video is None:
            raise StorageError("Extrait introuvable")
        source = source_media(video)
        start, end = clip.start_seconds, clip.end_seconds
        subtitles = clip.subtitles
        language = video.target_language if clip.subtitle_source == "translation" else video.detected_language
        cues = range_cues(video, start, end, clip.subtitle_source) if subtitles != "none" else []
        directory = clips_dir(video.id)
        names = {True: output_name(clip, audio_only=True), False: output_name(clip, audio_only=False)}
    picture = has_picture(source)
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / names[not picture]
    temporary = directory / f".{clip_id}.part{output.suffix}"
    srt = directory / f".{clip_id}.srt"
    if not picture or not cues:
        subtitles = "none"
    try:
        if subtitles != "none":
            srt.write_text(_srt(cues), encoding="utf-8", newline="\n")
        _run(
            ffmpeg_command(source, temporary, start, end, picture=picture,
                           subtitles=subtitles, srt=srt if subtitles != "none" else None, language=language),
            end - start, on_progress,
        )
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise StorageError("La découpe n'a produit aucun fichier")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
        srt.unlink(missing_ok=True)
    return output


def delete_file(clip: VideoClip) -> None:
    path = clip_path(clip)
    if path:
        path.unlink(missing_ok=True)
