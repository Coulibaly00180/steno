"""Download the speaker diarization models at image build time (n°8).

Public GitHub releases of sherpa-onnx and a public Hugging Face repository
(Nemotron): no account, no token, no licence to accept at install time, and
nothing is downloaded when the application runs.
Checksums pin the exact files that were evaluated on the AMI meetings.
"""
import hashlib
import io
import sys
import tarfile
import urllib.request
from pathlib import Path

RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
SEGMENTATION = (
    f"{RELEASES}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
    "24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488",
    "sherpa-onnx-pyannote-segmentation-3-0/model.onnx",
)
EMBEDDING = (
    # "recongition" is the release tag's actual spelling.
    f"{RELEASES}/speaker-recongition-models/nemo_en_titanet_small.onnx",
    "ad4a1802485d8b34c722d2a9d04249662f2ece5d28a7a039063ca22f515a789e",
)


# Nemotron 3 Diarization (feuille de route n° 3, phase 3): the int8 ONNX export,
# at a pinned revision (OpenMDW 1.1; licence and notices in models/nemotron/).
NEMOTRON = "https://huggingface.co/onnx-community/Nemotron-3-Diarization-ONNX/resolve/353b6f8ad2cac3580e982d7fbdf0a010786b0406/onnx"
NEMOTRON_FILES = (
    ("model_quantized.onnx", "fff7d18c7439c9fdc1c6c4dfec924cb42d3344264ca879780dfaf7ee886e6c1e"),
    ("model_quantized.onnx_data", "002d7483e1c865c35c82220fdb378f185ff213c6d35922b38ae421c8ec72c338"),
)


def download(url: str, sha256: str) -> bytes:
    with urllib.request.urlopen(url, timeout=300) as response:
        data = response.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != sha256:
        raise SystemExit(f"Checksum mismatch for {url}: {digest}")
    return data


def download_to(url: str, sha256: str, target: Path) -> None:
    """Streamed to disk: the Nemotron weights are 120 MB."""
    digest = hashlib.sha256()
    partial = target.with_name(target.name + ".part")
    with urllib.request.urlopen(url, timeout=300) as response, partial.open("wb") as out:
        while block := response.read(1 << 20):
            digest.update(block)
            out.write(block)
    if digest.hexdigest() != sha256:
        partial.unlink()
        raise SystemExit(f"Checksum mismatch for {url}: {digest.hexdigest()}")
    partial.replace(target)


def main(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    url, sha256, member = SEGMENTATION
    with tarfile.open(fileobj=io.BytesIO(download(url, sha256)), mode="r:bz2") as archive:
        extracted = archive.extractfile(member)
        if extracted is None:
            raise SystemExit(f"{member} missing from {url}")
        (target / "segmentation.onnx").write_bytes(extracted.read())
    url, sha256 = EMBEDDING
    (target / "embedding.onnx").write_bytes(download(url, sha256))
    nemotron = target / "nemotron"
    nemotron.mkdir(exist_ok=True)
    for name, sha256 in NEMOTRON_FILES:
        download_to(f"{NEMOTRON}/{name}", sha256, nemotron / name)
    print(f"Diarization models in {target}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/models/diarization"))
