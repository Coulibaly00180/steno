"""Speaker labels in the transcript (n°8).

With speakers, every transcript line reads "[hh:mm:ss] Name : text": the
summaries, the chat, the search and the exports all see who speaks, and a
renamed speaker only needs the transcript to be rebuilt.
"""
from .models import Speaker, TranscriptSegment, Video
from .utils import timestamp


def segment_line(segment: TranscriptSegment, labels: dict[int, str]) -> str:
    label = labels.get(segment.speaker_id) if segment.speaker_id is not None else None
    return f"[{timestamp(segment.start_seconds)}] {label} : {segment.text}" if label else f"[{timestamp(segment.start_seconds)}] {segment.text}"


def speaker_labels(video: Video) -> dict[int, str]:
    return {speaker.id: speaker.label for speaker in video.speakers}


def build_transcript(video: Video) -> str:
    labels = speaker_labels(video)
    return "\n".join(segment_line(segment, labels) for segment in video.segments)


def speaking_seconds(video: Video) -> dict[int, float]:
    seconds: dict[int, float] = {}
    for segment in video.segments:
        if segment.speaker_id is not None:
            seconds[segment.speaker_id] = seconds.get(segment.speaker_id, 0.0) + max(0.0, segment.end_seconds - segment.start_seconds)
    return seconds


def speakers_payload(video: Video) -> list[dict]:
    seconds = speaking_seconds(video)
    total = sum(seconds.values()) or 1.0
    return [
        {
            "id": speaker.id,
            "position": speaker.position,
            "name": speaker.name,
            "label": speaker.label,
            "seconds": round(seconds.get(speaker.id, 0.0), 1),
            "share": round(seconds.get(speaker.id, 0.0) / total, 3),
        }
        for speaker in video.speakers
    ]


def apply_turns(db, video: Video, speaker_per_segment: list[int | None]) -> None:
    """Replace the video's speakers with numbered ones (1…n) from a diarization."""
    for segment in video.segments:
        segment.speaker_id = None
    video.speakers.clear()
    db.flush()
    numbers = sorted({number for number in speaker_per_segment if number is not None})
    created = {number: Speaker(video_id=video.id, position=position) for position, number in enumerate(numbers, 1)}
    video.speakers.extend(created.values())
    db.flush()
    for segment, number in zip(video.segments, speaker_per_segment):
        segment.speaker_id = created[number].id if number is not None else None
