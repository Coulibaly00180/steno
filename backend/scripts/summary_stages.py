"""Where a summary loses the expected subjects: coverage at each stage, on the reference corpus.

    docker compose -f compose.yaml -f compose.gpu.yaml run --rm --no-deps -v "$PWD/backend:/app" worker \\
        python -m scripts.summary_stages conference-en clip-fr [--json FILE]

For each file (its cached transcript: run a quality run first), the expected
subjects of data/corpus/references.json are looked for in:
- the transcript (a subject never said cannot be summarised);
- the block summaries;
- the merged group summaries (long files only);
- the final summary.
Exactly the analysis code (`compose_summary`'s steps), no database write.
"""
import json
import re
import sys
import time
from pathlib import Path

from app import ai_models, analysis_options, quality
from app.config import settings
from app.db import SessionLocal
from app.llm import final_summary, summarize_chunk
from app.utils import split_text, timestamp
from app.worker import (
    DEFAULT_TEMPLATE, _range_heading, chunk_time_range, final_input_budget, final_output_tokens, reduce_block_summaries,
)


def corpus() -> tuple[dict, dict]:
    loaded = quality.load_corpus()
    if loaded is None:
        raise SystemExit("Corpus de référence absent (data/corpus)")
    return loaded


def found(topics: dict, text: str) -> list[str]:
    lowered = text.lower()
    return [name for name, pattern in topics.items() if re.search(pattern, lowered)]


def stages(key: str) -> dict:
    sources, references = corpus()
    topics = references[key]["topics"]
    cached = quality._transcript_cache(key, ai_models.whisper_model())
    if not cached.is_file():
        raise SystemExit(f"{key} : pas de transcription en cache ({cached.name}), lancez d'abord une passe de qualité")
    transcript = json.loads(cached.read_text(encoding="utf-8"))
    text = "\n".join(f"[{timestamp(start)}] {line}" for start, _, line in transcript["rows"])
    duration = float(sources[key].get("duration_seconds") or transcript["rows"][-1][1])
    language = analysis_options.language_name(transcript.get("language")) or "français"
    with SessionLocal() as db:
        template = quality._template_prompts(db).get(quality.TEMPLATES.get(key, ""), DEFAULT_TEMPLATE)
    budget = analysis_options.word_budget(duration, "standard")
    report: dict = {"key": key, "topics": len(topics), "transcript": found(topics, text)}

    started = time.monotonic()
    blocks = []
    for chunk in split_text(text, settings.summary_chunk_chars):
        blocks.append(f"{_range_heading(chunk_time_range(chunk))}\n{summarize_chunk(chunk, language).text}")
    report["blocks"] = found(topics, "\n".join(blocks))
    report["block_count"] = len(blocks)
    groups = reduce_block_summaries(blocks, language, budget_tokens=final_input_budget(final_output_tokens(budget)),
                                    detailed=False, vocabulary=[])
    report["groups"] = found(topics, "\n".join(groups))
    report["group_count"] = len(groups)
    final = final_summary("\n\n".join(groups), template, language, word_budget=budget)
    report["final"] = found(topics, final)
    report["seconds"] = round(time.monotonic() - started)
    report["texts"] = {"groups": groups, "final": final}
    return report


def main(argv: list[str]) -> None:
    out = None
    if "--json" in argv:
        out = argv[argv.index("--json") + 1]
        argv = [a for a in argv if a not in ("--json", out)]
    results = []
    for key in argv:
        result = stages(key)
        results.append(result)
        names = corpus()[1][key]["topics"]
        print(f"== {key} ({result['block_count']} blocs, {result['group_count']} groupes, {result['seconds']} s)")
        for stage in ("transcript", "blocks", "groups", "final"):
            missing = [name for name in names if name not in result[stage]]
            print(f"   {stage:<11} {len(result[stage])}/{result['topics']}  manquants : {', '.join(missing) or '—'}")
    if out:
        Path(out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:])
