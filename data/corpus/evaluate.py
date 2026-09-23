"""Run the reference corpus through the running application (docs/specs/phase-1 §19).

Usage (host, stack running):  python data/corpus/evaluate.py [key ...]
Uploads each corpus file through the API (standard length), then regenerates
the summary in short and detailed, and stores everything in results.json.
The uploaded videos are deleted at the end: the corpus files stay here.
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:8000"
HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results.json"
# The template a user would pick for each kind of content (starter templates).
TEMPLATES = {
    "reunion-fr": "Compte-rendu de réunion",
    "cours-fr": "Cours / formation",
    "podcast-en": "Podcast / interview",
    "conference-en": "Présentation / démo",
    "clip-fr": "Cours / formation",
}


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(API + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read())


def wait(job_id):
    """Poll a job, recording when each stage starts."""
    stages, last = {}, None
    started = time.monotonic()
    while True:
        job = call("GET", f"/jobs/{job_id}")
        if job["stage"] != last:
            stages[job["stage"]] = round(time.monotonic() - started)
            last = job["stage"]
            print(f"  {time.strftime('%H:%M:%S')} {job['stage']} {job['progress']}%", flush=True)
        if job["status"] in ("COMPLETED", "FAILED"):
            return job, round(time.monotonic() - started), stages
        time.sleep(5)


def upload(path, template_id):
    output = subprocess.run(
        ["curl", "-sS", "-X", "POST", f"{API}/videos", "-F", f"file=@{path}", "-F", "summary_length=standard",
         "-F", f"template_id={template_id}"],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(output)


def main(keys):
    sources = json.loads((HERE / "sources.json").read_text(encoding="utf-8"))
    results = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else {}
    template_ids = {template["name"]: template["id"] for template in call("GET", "/templates")}
    for key in keys or sources:
        source = sources[key]
        print(f"== {key} ({source['duration_seconds'] // 60} min)", flush=True)
        template_id = template_ids[TEMPLATES[key]]
        job = upload(HERE / source["file"], template_id)
        if "video_id" not in job:
            print(f"  upload refused: {job}", flush=True)
            continue
        video_id = job["video_id"]
        runs = {}
        try:
            job, seconds, stages = wait(job["id"])
            runs["standard"] = {"status": job["status"], "error": job["error"], "seconds": seconds, "stages": stages}
            for length in ("short", "detailed"):
                if job["status"] != "COMPLETED":
                    break
                regen = call("POST", f"/videos/{video_id}/summaries", {"summary_length": length, "template_id": template_id})
                regen_job, seconds, stages = wait(regen["id"])
                runs[length] = {"status": regen_job["status"], "error": regen_job["error"], "seconds": seconds, "stages": stages}
            video = call("GET", f"/videos/{video_id}")
            by_length = {summary["summary_length"]: summary["content_markdown"] for summary in video["summaries"]}
            for length, run in runs.items():
                run["summary"] = by_length.get(length)
            results[key] = {
                "template": TEMPLATES[key],
                "duration_seconds": video["duration_seconds"],
                "detected_language": video["detected_language"],
                "transcript_characters": len(video["transcript_text"] or ""),
                "segments": len(video["segments"]),
                "transcript": video["transcript_text"],
                "chapters": video["chapters"],
                "runs": runs,
            }
            RESULTS.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            call("DELETE", f"/videos/{video_id}")
            print(f"  deleted {video_id}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
