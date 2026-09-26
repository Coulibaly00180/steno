"""Checks app/nemotron.py against Hugging Face transformers (feuille de route n° 3, phase 3).

Run it again whenever the model, its revision or the port changes.

1. Reference probabilities, in a throwaway container (torch + transformers, nothing added to the image):

    MSYS_NO_PATHCONV=1 docker run --rm -v "$PWD/.bench-cache:/cache" -v "$PWD/backend:/app:ro" \
      -e HF_HOME=/cache/hf -e BENCH_CACHE=/cache/bench \
      python:3.12-slim sh -c "apt-get update -qq && apt-get install -y -qq ffmpeg git >/dev/null && \
      pip install -q torch --index-url https://download.pytorch.org/whl/cpu && \
      pip install -q 'git+https://github.com/huggingface/transformers@c8b81b63232be35ab1774dd3cabbf499d8b9808f' librosa && \
      cd /app && python -m bench.nemotron_reference reference"

2. The port on the same files, with the fp32 and the int8 ONNX exports:

    docker compose -f compose.test.yaml run --rm bench python -m bench.nemotron_reference compare

Measured on 2026-09-26 (dialogue, grande-reunion, AMI ES2004a 17 min): fp32, no
frame decided differently; int8 (the one in the image), 0.00 to 0.07 % of frames.
"""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from bench.run import CACHE, HERE, ami

MODELS = CACHE.parent / "nemotron"
REFERENCE = MODELS / "ref"
EXPORT = "https://huggingface.co/onnx-community/Nemotron-3-Diarization-ONNX/resolve/353b6f8ad2cac3580e982d7fbdf0a010786b0406/onnx"
CHECKPOINT = ("nvidia/Nemotron-3-Diarization", "f667ed73aee57d40cc39428eb768b4fd87a0a29e")


def sources() -> dict[str, Path]:
    audio, _ = ami(0)
    return {"dialogue": HERE / "cases" / "dialogue" / "audio.ogg",
            "grande-reunion": HERE / "cases" / "grande-reunion" / "audio.ogg", "ami": audio}


def decode(source: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def reference() -> None:
    import torch
    from transformers import AutoModelForAudioFrameClassification, AutoProcessor

    name, revision = CHECKPOINT
    processor = AutoProcessor.from_pretrained(name, revision=revision)
    model = AutoModelForAudioFrameClassification.from_pretrained(name, revision=revision).eval()
    REFERENCE.mkdir(parents=True, exist_ok=True)
    for case, source in sources().items():
        inputs = processor(decode(source), sampling_rate=16000, return_tensors="pt")
        with torch.no_grad():
            np.save(REFERENCE / f"{case}.npy", model(**inputs).logits[0].sigmoid().numpy())
        print(case, "done", flush=True)


def compare() -> None:
    import onnxruntime

    from app import nemotron

    for variant in ("model.onnx", "model_quantized.onnx"):
        path = MODELS / "onnx" / variant
        for suffix in ("", "_data"):
            target = path.with_name(path.name + suffix)
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(["curl", "-sLf", "-o", str(target), f"{EXPORT}/{target.name}"], check=True)
        session = onnxruntime.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        for case, source in sources().items():
            with tempfile.TemporaryDirectory() as folder:
                wav = Path(folder) / "a.wav"
                subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                                str(wav)], check=True)
                started = time.monotonic()
                probs = nemotron.activity(wav, session=session, keep_probabilities=True)
                seconds = time.monotonic() - started
            expected = np.load(REFERENCE / f"{case}.npy")
            differ = np.mean((expected > 0.5) != (probs[:len(expected)] > 0.5))
            print(f"{variant:<22} {case:<16} {len(probs) / 100:7.1f} s in {seconds:5.1f} s | frames {len(probs)} / "
                  f"{len(expected)} | décisions différentes : {100 * differ:.2f} %", flush=True)


if __name__ == "__main__":
    {"reference": reference, "compare": compare}[sys.argv[1]]()
