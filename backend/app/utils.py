import json
import subprocess
from pathlib import Path


def ffprobe_duration(path: Path, *, timeout_seconds: int = 60) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def split_text(text: str, max_chars: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            words = paragraph.split()
            for word in words:
                candidate = f"{current} {word}".strip()
                if len(candidate) > max_chars and current:
                    chunks.append(current)
                    current = word
                else:
                    current = candidate
            continue
        candidate = f"{current}\n{paragraph}".strip()
        if len(candidate) > max_chars and current:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
