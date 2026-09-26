"""Who speaks when (n°8): local speaker diarization.

Nemotron 3 Diarization (app/nemotron.py, feuille de route n° 3) is the default
engine; this module picks it (`engine_for`) and keeps sherpa-onnx, described
below, for more than 8 speakers or when the Nemotron model is missing.

Models (downloaded when the image is built, no account needed):
- segmentation: pyannote segmentation-3.0 (MIT), exported to ONNX by sherpa-onnx;
- speaker embeddings: NeMo TitaNet small.

Measured on AMI meeting ES2004a (4 speakers, 17.5 min, CPU): TitaNet gave
3.9 % speaker confusion with the count given, against 14.8 % for WeSpeaker and
22.6 % for 3D-Speaker; the rest of the error (12 % missed) is overlapped speech.

The audio is processed in windows (constant memory up to 6 hours), each one
deliberately over-segmented. One global clustering of the per-window voice
prints then links the windows, merges a voice split in two, and honours the
number of speakers when the user gives it. sherpa's own threshold was too
brittle for that: 5 speakers found for 4 at 1.1, a single one at 1.2.
"""
import bisect
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import nemotron
from .config import settings
from .transcription import iter_audio_windows

logger = logging.getLogger(__name__)

RATE = 16000
WINDOW_SECONDS = 1200
# Fine-grained clustering inside a window: merging happens globally.
LOCAL_THRESHOLD = 1.0
# Voice-print (cosine) distance under which two clusters are one person.
# Tuned on AMI ES2004a, IS1009a, TS3003a (4 speakers each): 0.35 found 4
# speakers in all three (DER 19.8-21.2 %); 0.45 found 3, 3 and 2.
GLOBAL_THRESHOLD = 0.35
# Samples of a cluster's voice print: its longest turns, each capped.
PRINT_TURNS = 8
PRINT_MIN_SECONDS = 1.0
PRINT_MAX_SECONDS = 10.0
# A "speaker" with less speech than this is a fragment of another one
# (unless the user asked for that many speakers).
MIN_SPEAKER_SECONDS = 15.0
MIN_SPEAKER_SHARE = 0.02


@dataclass(frozen=True)
class Turn:
    start: float
    end: float
    speaker: int  # 1, 2… in order of first appearance


def models_available() -> bool:
    directory = Path(settings.diarization_models_dir)
    return (directory / "segmentation.onnx").is_file() and (directory / "embedding.onnx").is_file()


def _engines():
    import sherpa_onnx

    directory = Path(settings.diarization_models_dir)
    threads = settings.diarization_threads
    embedding = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(directory / "embedding.onnx"), num_threads=threads)
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(directory / "segmentation.onnx")),
            num_threads=threads,
        ),
        embedding=embedding,
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=LOCAL_THRESHOLD),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise RuntimeError("Invalid diarization configuration")
    return sherpa_onnx.OfflineSpeakerDiarization(config), sherpa_onnx.SpeakerEmbeddingExtractor(embedding)


@dataclass
class Cluster:
    turns: list[tuple[float, float]]
    vector: np.ndarray
    seconds: float


def _voice_print(extractor, samples: np.ndarray, offset: float, turns: list[tuple[float, float]]) -> np.ndarray | None:
    usable = sorted((t for t in turns if t[1] - t[0] >= PRINT_MIN_SECONDS), key=lambda t: t[0] - t[1])[:PRINT_TURNS]
    vectors = []
    for start, end in usable:
        begin = int((start - offset) * RATE)
        stop = int((min(end, start + PRINT_MAX_SECONDS) - offset) * RATE)
        stream = extractor.create_stream()
        stream.accept_waveform(RATE, samples[begin:stop])
        stream.input_finished()
        vector = np.asarray(extractor.compute(stream), dtype=np.float32)
        norm = np.linalg.norm(vector)
        if norm > 0:
            vectors.append(vector / norm)
    if not vectors:
        return None
    mean = np.mean(vectors, axis=0)
    return mean / (np.linalg.norm(mean) or 1)


def cluster_speakers(clusters: list[Cluster], num_speakers: int | None = None) -> list[int]:
    """Group clusters into speakers: average-linkage merges, closest pair first.

    Returns, for each cluster, the index of its group. With `num_speakers`,
    merges continue until exactly that many groups remain (or stop there);
    without it, they stop at GLOBAL_THRESHOLD, then groups with too little
    speech join the closest remaining group.
    """
    groups: list[list[int]] = [[index] for index in range(len(clusters))]
    vectors = [cluster.vector for cluster in clusters]
    seconds = [cluster.seconds for cluster in clusters]

    def centroid(group: list[int]) -> np.ndarray:
        weights = np.array([seconds[i] for i in group]) + 1e-6
        mean = np.average([vectors[i] for i in group], axis=0, weights=weights)
        return mean / (np.linalg.norm(mean) or 1)

    def closest_pair(candidates: list[int] | None = None):
        centroids = [centroid(group) for group in groups]
        best = None
        for a in range(len(groups)):
            for b in range(a + 1, len(groups)):
                if candidates is not None and a not in candidates and b not in candidates:
                    continue
                distance = 1 - float(centroids[a] @ centroids[b])
                if best is None or distance < best[0]:
                    best = (distance, a, b)
        return best

    total = sum(seconds) or 1
    minimum = max(MIN_SPEAKER_SECONDS, MIN_SPEAKER_SHARE * total)

    def absorb_small(floor: int) -> None:
        """Fragments with little speech (unreliable voice prints) join their closest group."""
        while len(groups) > floor:
            small = [index for index, group in enumerate(groups) if sum(seconds[i] for i in group) < minimum]
            if not small:
                return
            _, a, b = closest_pair(small)
            groups[a].extend(groups.pop(b))

    target = num_speakers if num_speakers and num_speakers > 0 else None
    if target is None:
        while len(groups) > 1:
            distance, a, b = closest_pair()
            if distance > GLOBAL_THRESHOLD:
                break
            groups[a].extend(groups.pop(b))
        absorb_small(1)
    else:
        # Fragments first: merging "down to N" by distance alone once joined two
        # real speakers and kept noise fragments as speakers (AMI IS1009a, 45.8 % DER).
        absorb_small(target)
        while len(groups) > target:
            _, a, b = closest_pair()
            groups[a].extend(groups.pop(b))

    labels = [0] * len(clusters)
    for group_index, group in enumerate(groups):
        for cluster_index in group:
            labels[cluster_index] = group_index
    return labels


def number_by_appearance(turns: list[tuple[float, float, int]]) -> list[Turn]:
    """Speaker 1 speaks first; consecutive turns of one speaker are joined."""
    order: dict[int, int] = {}
    result: list[Turn] = []
    for start, end, group in sorted(turns):
        speaker = order.setdefault(group, len(order) + 1)
        if result and result[-1].speaker == speaker and start - result[-1].end < 0.5:
            result[-1] = Turn(result[-1].start, max(result[-1].end, end), speaker)
        else:
            result.append(Turn(start, end, speaker))
    return result


def engine_for(num_speakers: int | None = None, engine: str | None = None) -> str:
    """The engine that will run: Nemotron unless asked otherwise, missing, or given more voices than it follows."""
    chosen = engine or settings.diarization_engine
    if chosen == "nemotron" and (not nemotron.model_available() or (num_speakers or 0) > nemotron.SPEAKERS):
        return "sherpa"
    return chosen


def diarize(
    wav_path: Path,
    *,
    num_speakers: int | None = None,
    on_progress: Callable[[float], None] | None = None,
    window_seconds: float = WINDOW_SECONDS,
    engine: str | None = None,
) -> list[Turn]:
    """Speaker turns of a 16 kHz mono WAV."""
    if engine_for(num_speakers, engine) == "nemotron":
        active = nemotron.activity(wav_path, on_progress=on_progress)
        return number_by_appearance(nemotron.turns_from_activity(active, num_speakers))
    return _diarize_sherpa(wav_path, num_speakers=num_speakers, on_progress=on_progress, window_seconds=window_seconds)


def _diarize_sherpa(
    wav_path: Path,
    *,
    num_speakers: int | None,
    on_progress: Callable[[float], None] | None,
    window_seconds: float,
) -> list[Turn]:
    diarizer, extractor = _engines()
    clusters: list[Cluster] = []
    for offset, end, samples in iter_audio_windows(wav_path, window_seconds):
        result = diarizer.process(samples).sort_by_start_time()
        local: dict[int, list[tuple[float, float]]] = {}
        for segment in result:
            local.setdefault(segment.speaker, []).append((offset + segment.start, offset + segment.end))
        for turns in local.values():
            vector = _voice_print(extractor, samples, offset, turns)
            if vector is not None:
                clusters.append(Cluster(turns, vector, sum(e - s for s, e in turns)))
            else:
                # Only sub-second turns: kept, attached later to their closest neighbour in time.
                clusters.append(Cluster(turns, np.zeros(1, dtype=np.float32), sum(e - s for s, e in turns)))
        if on_progress:
            on_progress(end)
    printable = [cluster for cluster in clusters if cluster.vector.size > 1]
    if not printable:
        return []
    labels = cluster_speakers(printable, num_speakers)
    turns = [(s, e, label) for cluster, label in zip(printable, labels) for s, e in cluster.turns]
    # Voice-print-less fragments take the speaker talking just before them.
    for cluster in clusters:
        if cluster.vector.size > 1:
            continue
        for start, end in cluster.turns:
            previous = [t for t in turns if t[0] <= start]
            if previous:
                turns.append((start, end, max(previous)[2]))
    return number_by_appearance(turns)


def assign_speakers(segments: list[tuple[float, float]], turns: list[Turn]) -> list[int | None]:
    """For each transcript segment, the speaker who talks the most during it."""
    ordered = sorted(turns, key=lambda turn: turn.start)
    starts = [turn.start for turn in ordered]
    speakers: list[int | None] = []
    for start, end in segments:
        overlap: dict[int, float] = {}
        index = max(0, bisect.bisect_right(starts, start) - 1)
        # Turns are short; look back a little in case a long one started earlier.
        for turn in ordered[max(0, index - 20):]:
            if turn.start >= end:
                break
            shared = min(end, turn.end) - max(start, turn.start)
            if shared > 0:
                overlap[turn.speaker] = overlap.get(turn.speaker, 0.0) + shared
        if overlap:
            speakers.append(max(overlap, key=overlap.get))
        else:
            # Whisper sometimes keeps a phrase the segmentation saw as silence: nearest turn.
            nearest = min(ordered, key=lambda turn: min(abs(turn.start - end), abs(turn.end - start)), default=None)
            speakers.append(nearest.speaker if nearest else None)
    return speakers
