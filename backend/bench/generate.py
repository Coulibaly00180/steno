"""Generate the synthetic French cases of the voices bench from bench/scripts.

    MSYS_NO_PATHCONV=1 docker run --rm -v "<repo>/backend/bench:/bench" python:3.12-slim \\
        sh -c "apt-get update -qq && apt-get install -y -qq ffmpeg >/dev/null && pip install -q piper-tts numpy && python /bench/generate.py"

Voices from rhasspy/piper-voices, pinned to one revision (licences in
bench/NOTICE.md): siwis (CC BY 4.0), upmc (CC BY-SA 4.0), mls (CC BY 4.0,
125 speakers from Multilingual LibriSpeech). The generated audio is
committed, so running the bench needs none of this.

A script line is `person|gap|text`; the gap is the seconds after the previous
line ends, negative when they talk at the same time.
"""
import json
import random
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
RATE = 16000
REVISION = "c10ece1aade47bb51c153c893d14e5bf8e5b7117"
VOICE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/" + REVISION + "/fr/fr_FR/{name}/medium/fr_FR-{name}-medium.onnx{suffix}"
# person: (voice, speaker id within the voice)
VOICES = {
    "Claire": ("siwis", 0),
    "Pierre": ("upmc", 1),
    "Sophie": ("upmc", 0),
    "Karim": ("mls", 1),
    "Marc": ("mls", 4),
    "Lina": ("mls", 5),
}
# Not everyone is equally loud in a meeting.
GAIN = {"Claire": 0.9, "Pierre": 0.8, "Sophie": 1.0, "Karim": 0.7, "Marc": 0.85, "Lina": 0.75}
CASES = {
    "dialogue": {"script": "dialogue.txt", "background": None},
    "reunion": {"script": "reunion.txt", "background": None},
    "grande-reunion": {"script": "grande-reunion.txt", "background": None},
    "musique": {"script": "musique.txt", "background": "music"},
    "bruit": {"script": "bruit.txt", "background": "noise"},
    "silence": {"script": None, "background": "room"},
}


def voice(name: str, cache: Path) -> Path:
    model = cache / f"fr_FR-{name}-medium.onnx"
    for suffix in ("", ".json"):
        target = Path(str(model) + suffix)
        if not target.exists():
            print(f"téléchargement de {target.name}", flush=True)
            urllib.request.urlretrieve(VOICE_URL.format(name=name, suffix=suffix), target)
    return model


def speak(text: str, person: str, cache: Path) -> np.ndarray:
    name, speaker = VOICES[person]
    with tempfile.TemporaryDirectory() as folder:
        raw, wav = Path(folder) / "line.txt", Path(folder) / "line.wav"
        raw.write_text(text, encoding="utf-8")
        subprocess.run([sys.executable, "-m", "piper", "-m", str(voice(name, cache)), "-s", str(speaker),
                        "-i", str(raw), "-f", str(wav)], check=True, capture_output=True)
        pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav), "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
                             check=True, capture_output=True).stdout
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    # Piper leaves a little silence around the words: trim it, so the truth has the speech itself.
    loud = np.flatnonzero(np.abs(samples) > 0.01)
    return samples[loud[0]:loud[-1] + 1] if len(loud) else samples


def music(seconds: float, level: float) -> np.ndarray:
    """A soft chord progression with a beat: speech-like frequencies, like a real background."""
    t = np.arange(int(seconds * RATE)) / RATE
    chords = [(220.0, 277.2, 329.6), (196.0, 246.9, 293.7), (174.6, 220.0, 261.6), (196.0, 246.9, 311.1)]
    signal = np.zeros_like(t)
    for index, chord in enumerate(chords * int(seconds // 8 + 1)):
        part = (t >= index * 2.0) & (t < index * 2.0 + 2.0)
        for frequency in chord:
            signal[part] += np.sin(2 * np.pi * frequency * t[part])
    beat = 0.6 + 0.4 * (np.sin(2 * np.pi * 2.0 * t) > 0)
    signal *= beat
    return level * signal / np.max(np.abs(signal))


def pink_noise(seconds: float, level: float, rng: np.random.Generator) -> np.ndarray:
    white = rng.standard_normal(int(seconds * RATE))
    spectrum = np.fft.rfft(white)
    spectrum /= np.sqrt(np.maximum(np.arange(len(spectrum)), 1))
    noise = np.fft.irfft(spectrum, n=len(white))
    return level * noise / np.max(np.abs(noise))


def build(case: str, settings: dict, cache: Path, rng: np.random.Generator) -> None:
    lines, speakers = [], []
    if settings["script"]:
        for raw in (HERE / "scripts" / settings["script"]).read_text(encoding="utf-8").splitlines():
            if raw.strip() and not raw.startswith("#"):
                person, gap, text = raw.split("|", 2)
                lines.append((person, float(gap), text.strip()))
    clips, truth, cursor = [], [], 0.5
    for person, gap, text in lines:
        samples = speak(text, person, cache) * GAIN[person]
        start = max(0.2, cursor + gap)
        end = start + len(samples) / RATE
        clips.append((start, samples))
        truth.append({"speaker": person, "start": round(start, 3), "end": round(end, 3), "text": text})
        speakers.append(person)
        cursor = max(cursor, end)
    total = (cursor + 1.5) if lines else 20.0
    mix = np.zeros(int(total * RATE), dtype=np.float32)
    for start, samples in clips:
        offset = int(start * RATE)
        mix[offset:offset + len(samples)] += samples
    if settings["background"] == "music":
        mix += music(total, 0.12)
    elif settings["background"] == "noise":
        mix += pink_noise(total, 0.08, rng)
    elif settings["background"] == "room":
        mix += pink_noise(total, 0.01, rng)
    mix = 0.9 * mix / max(1e-6, float(np.max(np.abs(mix))))
    folder = HERE / "cases" / case
    folder.mkdir(parents=True, exist_ok=True)
    pcm = (mix * 32767).astype(np.int16).tobytes()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-i", "-",
                    "-c:a", "libopus", "-b:a", "48k", str(folder / "audio.ogg")], input=pcm, check=True)
    (folder / "truth.json").write_text(json.dumps({
        "language": "fr",
        "speakers": sorted(set(speakers), key=speakers.index),
        "duration": round(total, 2),
        "lines": truth,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{case}: {total:.0f} s, {len(set(speakers))} personnes, {len(truth)} lignes", flush=True)


def main() -> None:
    cache = HERE / ".cache" / "voices"
    cache.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(4)
    random.seed(4)
    for case, settings in CASES.items():
        build(case, settings, cache, rng)


if __name__ == "__main__":
    main()
