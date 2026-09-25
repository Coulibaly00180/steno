"""Time the library endpoints on a large synthetic library (test stack only).

    docker compose -f compose.test.yaml run --rm backend-tests python -m scripts.bench_library [videos]
    docker compose -f compose.test.yaml down -v

Fills the test database (never the application's: the name must end with
`_test`) with N processed videos of ~1 hour (transcripts of ~45 000
characters), tags, jobs, actions and entity mentions, then times the calls
the interface makes. The semantic search is off (no Ollama in the test stack):
the words search runs, as when the embedding model does not answer.
"""
import json
import random
import statistics
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import insert, text
from sqlalchemy.engine import make_url

from app.config import settings

WORDS = (
    "budget réunion projet client devis fournisseur planning équipe marketing produit lancement salon présentation "
    "décision action responsable échéance semaine mois trimestre objectif résultat vente contrat partenaire risque "
    "retard livraison qualité test recrutement stagiaire formation outil logiciel données serveur sécurité réseau"
).split()


def main(count: int) -> None:
    url = make_url(settings.database_url)
    if not (url.database or "").endswith("_test"):
        sys.exit(f"Refus : {url.database} n'est pas une base de test")
    from app import main as api
    from app.db import engine
    from app.migrate import main as migrate
    from app.models import ActionItem, Entity, EntityMention, ProcessingJob, Tag, Video, video_tags

    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    migrate()
    random.seed(4)
    now = datetime.now(timezone.utc)
    started = time.monotonic()
    with engine.begin() as connection:
        tag_ids = [connection.execute(insert(Tag).values(name=f"tag-{index}").returning(Tag.id)).scalar() for index in range(30)]
        entity_ids = [
            connection.execute(insert(Entity).values(kind="person", name=f"Personne {index}", key=f"personne {index}").returning(Entity.id)).scalar()
            for index in range(200)
        ]
        for start in range(0, count, 500):
            videos, jobs, links, actions, mentions = [], [], [], [], []
            for index in range(start, min(count, start + 500)):
                video_id = str(uuid.uuid4())
                lines = [f"[{index % 24:02d}:{second // 60 % 60:02d}:{second % 60:02d}] " + " ".join(random.choices(WORDS, k=12))
                         for second in range(0, 3600, 6)]
                created = now - timedelta(hours=index)
                videos.append({
                    "id": video_id, "filename": f"{video_id}.mp4", "original_filename": f"Réunion {index}.mp4",
                    "path": f"/nowhere/{video_id}.mp4", "duration_seconds": 3600.0, "size_bytes": 500_000_000,
                    "status": "COMPLETED", "detected_language": "fr", "transcript_text": "\n".join(lines), "created_at": created,
                })
                jobs.append({"id": str(uuid.uuid4()), "video_id": video_id, "stage": "COMPLETED", "status": "COMPLETED",
                             "progress": 100, "kind": "FULL", "created_at": created, "started_at": created, "finished_at": created})
                links += [{"video_id": video_id, "tag_id": tag} for tag in random.sample(tag_ids, 2)]
                actions += [{"id": str(uuid.uuid4()), "video_id": video_id, "kind": "action", "text": " ".join(random.choices(WORDS, k=6)),
                             "status": random.choice(["open", "done"]), "position": position, "created_at": created, "updated_at": created}
                            for position in range(3)]
                mentions += [{"entity_id": entity, "video_id": video_id, "start_seconds": 60.0, "context": "…"}
                             for entity in random.sample(entity_ids, 5)]
            connection.execute(insert(Video), videos)
            connection.execute(insert(ProcessingJob), jobs)
            connection.execute(insert(video_tags), links)
            connection.execute(insert(ActionItem), actions)
            connection.execute(insert(EntityMention), mentions)
    with engine.begin() as connection:
        connection.execute(text("ANALYZE"))
    print(f"{count} vidéos créées en {time.monotonic() - started:.0f} s", flush=True)

    from fastapi.testclient import TestClient

    api._semantic_matches = lambda db, filters, q: None
    client = TestClient(api.app)
    calls = [
        ("Bibliothèque (tout)", "/videos"),
        ("Bibliothèque, 50 premières", "/videos?limit=50"),
        ("Recherche « budget stagiaire »", "/videos?q=budget%20stagiaire&mode=exact"),
        ("Filtre tag", "/videos?tag=tag-3"),
        ("Actions à faire", "/actions?status=open&kind=action"),
        ("Personnes et dates", "/entities"),
        ("Tags", "/tags"),
    ]
    print(f"{'Appel':<34} {'médiane':>9} {'max':>8} {'éléments':>9} {'taille':>9}")
    for label, path in calls:
        timings, size, items = [], 0, 0
        for _ in range(5):
            begin = time.perf_counter()
            response = client.get(path)
            timings.append(time.perf_counter() - begin)
            assert response.status_code == 200, (path, response.text[:200])
            size = len(response.content)
            body = response.json()
            items = len(body["items"]) if isinstance(body, dict) and "items" in body else len(body) if isinstance(body, list) else 0
        print(f"{label:<34} {statistics.median(timings) * 1000:>7.0f} ms {max(timings) * 1000:>6.0f} ms {items:>9} {size / 1024:>7.0f} Ko", flush=True)
    print(json.dumps({"total_videos": count}))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 3000)
