"""Voices bench (feuille de route n° 3, phase 1): who speaks, and what Whisper hears.

    docker compose -f compose.test.yaml run --rm bench                       # the French cases, then the check
    docker compose -f compose.test.yaml run --rm bench python -m bench.run --ami --ami-minutes 0   # plus all 17 min of AMI
    options: --case NAME…  --ami  --ami-minutes N (5; 0: whole)  --model NAME (Whisper; default: WHISPER_MODEL)
             --speakers-given (also measure with the number of speakers given)  --json FILE  --check

Columns:
- erreur de personne: share of the speech of one person at a time that goes to
  the wrong speaker, or to nobody, after the best one-to-one matching of the
  labels (10 ms frames; moments when two people talk are left out);
- voix: speakers found / speakers in the case;
- mots: words of the script found in the transcript (the French cases);
- bonne personne: words of the transcript put on the right person;
- lignes: lines of the transcript (the silence case: must be 0).

The cases come from generate.py (committed); AMI ES2004a (a real four-person
meeting in English, CC BY 4.0) is downloaded once into the cache, with its
reference turns from pyannote/AMI-diarization-setup.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.request
from collections import Counter
from itertools import permutations
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CACHE = Path(os.environ.get("BENCH_CACHE", HERE / ".cache"))
AMI_AUDIO = "https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/ES2004a/audio/ES2004a.Mix-Headset.wav"
AMI_RTTM = "https://raw.githubusercontent.com/pyannote/AMI-diarization-setup/main/only_words/rttms/test/ES2004a.rttm"
FRAME = 0.01
CASES = ["dialogue", "reunion", "grande-reunion", "musique", "bruit", "silence"]


def words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return re.findall(r"[a-z0-9]+", folded)


def decode(source: Path | str, target: Path, seconds: float | None = None) -> None:
    command = ["ffmpeg", "-v", "error", "-y", "-i", str(source)]
    if seconds:
        command += ["-t", str(seconds)]
    subprocess.run(command + ["-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target)], check=True)


def ami(minutes: float) -> tuple[Path, dict]:
    """ES2004a: the audio (downloaded once) and its reference turns, cut to `minutes` (0: all)."""
    folder = CACHE / "ami"
    folder.mkdir(parents=True, exist_ok=True)
    audio, rttm = folder / "ES2004a.Mix-Headset.wav", folder / "ES2004a.rttm"
    for url, target in ((AMI_AUDIO, audio), (AMI_RTTM, rttm)):
        if not target.exists():
            print(f"téléchargement de {target.name}", flush=True)
            urllib.request.urlretrieve(url, str(target) + ".part")
            os.replace(str(target) + ".part", target)
    limit = minutes * 60 if minutes else None
    lines = []
    for raw in rttm.read_text().splitlines():
        parts = raw.split()
        if len(parts) >= 8 and parts[0] == "SPEAKER":
            start, length, speaker = float(parts[3]), float(parts[4]), parts[7]
            if limit is None or start < limit:
                lines.append({"speaker": speaker, "start": start, "end": min(start + length, limit or 1e9), "text": None})
    speakers = sorted({line["speaker"] for line in lines}, key=lambda s: min(l["start"] for l in lines if l["speaker"] == s))
    duration = limit or max(line["end"] for line in lines)
    return audio, {"language": "en", "speakers": speakers, "duration": duration, "lines": lines, "cut": limit}


def frames(lines: list[dict], speakers: list[str], duration: float) -> tuple[np.ndarray, np.ndarray]:
    count = int(duration / FRAME) + 1
    who, how_many = np.full(count, -1), np.zeros(count, dtype=np.int32)
    for line in lines:
        a, b = int(line["start"] / FRAME), int(line["end"] / FRAME)
        who[a:b] = speakers.index(line["speaker"])
        how_many[a:b] += 1
    return who, how_many


def best_mapping(matrix: np.ndarray) -> dict[int, int]:
    """Reference speaker → predicted label, one to one, most frames in agreement."""
    refs, preds = matrix.shape
    if not preds:
        return {}
    if max(refs, preds) <= 8:
        best, choice = -1, ()
        labels = list(range(preds)) + [-1] * max(0, refs - preds)
        for order in set(permutations(labels, refs)):
            score = sum(matrix[r, p] for r, p in enumerate(order) if p >= 0)
            if score > best:
                best, choice = score, order
        return {r: p for r, p in enumerate(choice) if p >= 0}
    mapping, used = {}, set()
    for r, p in sorted(np.ndindex(matrix.shape), key=lambda rp: -matrix[rp]):
        if r not in mapping and p not in used:
            mapping[r] = p
            used.add(p)
    return mapping


def speaker_scores(truth: dict, turns: list) -> dict:
    speakers, duration = truth["speakers"], truth["duration"]
    who, how_many = frames(truth["lines"], speakers, duration)
    labels = sorted({turn.speaker for turn in turns})
    predicted = np.full(len(who), -1)
    for turn in turns:
        predicted[int(turn.start / FRAME):int(turn.end / FRAME)] = labels.index(turn.speaker)
    single = how_many == 1
    matrix = np.zeros((len(speakers), len(labels)), dtype=np.int64)
    for r, p in zip(who[single & (predicted >= 0)], predicted[single & (predicted >= 0)]):
        matrix[r, p] += 1
    mapping = best_mapping(matrix)
    right = sum(matrix[r, p] for r, p in mapping.items())
    total = int(single.sum())
    return {
        "speaker_error": round(1 - right / total, 4) if total else None,
        "missed": round(float((single & (predicted < 0)).sum()) / total, 4) if total else None,
        "voices": f"{len(labels)}/{len(speakers)}",
        "found_all": len(labels) == len(speakers),
        "_mapping": {labels[p]: r for r, p in mapping.items()},
    }


def text_scores(truth: dict, rows: list, numbers: list, mapping: dict) -> dict:
    result: dict = {"lines": len(rows)}
    if truth["lines"] and all(line["text"] for line in truth["lines"]):
        expected = Counter(w for line in truth["lines"] for w in words(line["text"]))
        heard = Counter(w for _, _, text in rows for w in words(text))
        result["words_found"] = round(sum(min(n, heard[w]) for w, n in expected.items()) / sum(expected.values()), 4)
    speakers = truth["speakers"]
    right = counted = 0
    for (start, end, text), number in zip(rows, numbers):
        overlap = Counter()
        for line in truth["lines"]:
            shared = min(end, line["end"]) - max(start, line["start"])
            if shared > 0:
                overlap[line["speaker"]] += shared
        if not overlap:
            continue
        n = len(words(text))
        counted += n
        if number is not None and mapping.get(number) == speakers.index(overlap.most_common(1)[0][0]):
            right += n
    if counted:
        result["person"] = round(right / counted, 4)
    return result


def run_case(name: str, audio: Path, truth: dict, model, speakers_given: bool) -> dict:
    from app.config import settings
    from app.diarization import assign_speakers, diarize
    from app.transcription import transcribe_windows

    with tempfile.TemporaryDirectory() as folder:
        wav = Path(folder) / "audio.wav"
        decode(audio, wav, truth.get("cut"))
        started = time.monotonic()
        rows, _ = transcribe_windows(model, wav, language=truth["language"], initial_prompt=None, beam_size=settings.whisper_beam_size)
        transcribed = time.monotonic() - started
        result: dict = {"case": name, "transcription_s": round(transcribed, 1)}
        if truth["speakers"]:
            started = time.monotonic()
            turns = diarize(wav)
            result["diarization_s"] = round(time.monotonic() - started, 1)
            scores = speaker_scores(truth, turns)
            mapping = scores.pop("_mapping")
            result |= scores
            numbers = assign_speakers([(start, end) for start, end, _ in rows], turns)
            result |= text_scores(truth, rows, numbers, mapping)
            if speakers_given:
                given = speaker_scores(truth, diarize(wav, num_speakers=len(truth["speakers"])))
                result["speaker_error_given"] = given["speaker_error"]
        else:
            result["lines"] = len(rows)
    return result


def check(results: list[dict]) -> list[str]:
    limits = json.loads((HERE / "thresholds.json").read_text(encoding="utf-8"))
    failures = []
    for result in results:
        rules = limits.get(result["case"], {})
        for key, bound in rules.get("max", {}).items():
            if result.get(key) is not None and result[key] > bound:
                failures.append(f"{result['case']} : {key} = {result[key]} > {bound}")
        for key, bound in rules.get("min", {}).items():
            if result.get(key) is not None and result[key] < bound:
                failures.append(f"{result['case']} : {key} = {result[key]} < {bound}")
        if rules.get("all_voices") and not result.get("found_all"):
            failures.append(f"{result['case']} : voix {result.get('voices')}")
    return failures


def percent(value) -> str:
    return "—" if value is None else f"{100 * value:.1f} %"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Banc « voix » de Sténo")
    parser.add_argument("--case", nargs="*", default=None)
    parser.add_argument("--ami", action="store_true")
    parser.add_argument("--ami-minutes", type=float, default=5)
    parser.add_argument("--model", default=None)
    parser.add_argument("--speakers-given", action="store_true")
    parser.add_argument("--json", default=None)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    from faster_whisper import WhisperModel

    from app.config import settings

    name = args.model or settings.whisper_model
    print(f"Whisper {name} sur {settings.whisper_device}", flush=True)
    model = WhisperModel(name, device=settings.whisper_device, compute_type=settings.whisper_compute_type)
    jobs = []
    # --case with no name: AMI only.
    for case in CASES if args.case is None else args.case:
        folder = HERE / "cases" / case
        jobs.append((case, folder / "audio.ogg", json.loads((folder / "truth.json").read_text(encoding="utf-8"))))
    if args.ami:
        audio, truth = ami(args.ami_minutes)
        jobs.append((f"ami-ES2004a{'' if not args.ami_minutes else f'-{args.ami_minutes:g}min'}", audio, truth))
    results = []
    header = f"{'Cas':<22} {'erreur de personne':>19} {'voix':>6} {'mots':>8} {'bonne personne':>15} {'lignes':>7} {'durée':>8}"
    print(header, flush=True)
    for case, audio, truth in jobs:
        result = run_case(case, audio, truth, model, args.speakers_given)
        results.append(result)
        seconds = result.get("transcription_s", 0) + result.get("diarization_s", 0)
        print(f"{case:<22} {percent(result.get('speaker_error')):>19} {result.get('voices', '—'):>6} "
              f"{percent(result.get('words_found')):>8} {percent(result.get('person')):>15} {result.get('lines', '—'):>7} "
              f"{seconds:>6.0f} s", flush=True)
        if args.speakers_given and result.get("speaker_error_given") is not None:
            print(f"{'':<22} nombre de voix donné : {percent(result['speaker_error_given'])}", flush=True)
    if args.json:
        Path(args.json).write_text(json.dumps({"model": name, "results": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.check:
        failures = check(results)
        for failure in failures:
            print(f"SOUS LE SEUIL : {failure}", flush=True)
        return 1 if failures else 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
