"""Score results.json against references.json: coverage, length vs budget, chapters.

Usage: python data/corpus/score.py   (prints a Markdown report)
The word budget mirrors backend/app/analysis_options.py (R-1).
"""
import json
import math
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent


def word_budget(seconds: float, length: str) -> int:
    minutes = max(0.0, seconds) / 60
    clamp = lambda value, low, high: int(max(low, min(high, value)))  # noqa: E731
    standard = clamp(250 + 8 * max(0.0, minutes - 15), 250, 1500)
    if length == "short":
        return clamp(200 + 2.5 * minutes, 200, 450)
    if length == "detailed":
        return clamp(2 * standard, 500, 2500)
    return standard


def words(text: str) -> int:
    return len(re.findall(r"\w+", text or ""))


def main() -> None:
    results = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
    references = json.loads((HERE / "references.json").read_text(encoding="utf-8"))
    print("| Vidéo | Durée | Niveau | Mots / budget | Couverture | Manquants | Temps |")
    print("|---|---|---|---|---|---|---|")
    for key, result in results.items():
        topics = references.get(key, {}).get("topics", {})
        duration = result["duration_seconds"]
        for length in ("short", "standard", "detailed"):
            run = result["runs"].get(length)
            if not run or not run.get("summary"):
                print(f"| {key} | {duration / 60:.0f} min | {length} | échec : {run and run.get('error')} | | | |")
                continue
            summary = run["summary"].lower()
            missing = [name for name, pattern in topics.items() if not re.search(pattern, summary)]
            covered = len(topics) - len(missing)
            budget = word_budget(duration, length)
            coverage = f"{covered}/{len(topics)}" if topics else "—"
            print(f"| {key} | {duration / 60:.0f} min | {length} | {words(run['summary'])} / {budget} "
                  f"({words(run['summary']) / budget:.0%}) | {coverage} | {', '.join(missing) or '—'} | {run['seconds'] // 60} min |")
    print()
    print("| Vidéo | Chapitres | Limite | Débuts attendus retrouvés (± 2 min) |")
    print("|---|---|---|---|")
    for key, result in results.items():
        chapters = result.get("chapters") or []
        limit = max(3, min(40, round(3 * math.sqrt(result["duration_seconds"] / 60))))
        expected = references.get(key, {}).get("chapter_starts")
        found = "—"
        if expected:
            hits = sum(any(abs(c["start_seconds"] - start) <= 120 for c in chapters) for start in expected)
            found = f"{hits}/{len(expected)}"
        print(f"| {key} | {len(chapters)} | {limit} | {found} |")
    print()
    for key, result in results.items():
        print(f"### {key}: chapitres")
        for chapter in result.get("chapters") or []:
            seconds = int(chapter["start_seconds"])
            print(f"- {seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d} {chapter['title']}")
        standard = result["runs"].get("standard", {})
        print(f"\nÉtapes (secondes depuis l'import) : {standard.get('stages')}\n")


if __name__ == "__main__":
    main()
