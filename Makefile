.PHONY: up dev down logs pull-model gpu reset migrate db-revision db-current backup test test-backend test-frontend

up:
	docker compose up --build

dev:
	docker compose -f compose.yaml -f compose.dev.yaml up --build

gpu:
	docker compose -f compose.yaml -f compose.gpu.yaml up --build

pull-model:
	docker compose --profile tools run --rm model-pull

logs:
	docker compose logs -f api worker web

down:
	docker compose down

reset:
	docker compose down -v
	rm -rf data/uploads/* data/audio/* data/exports/*

migrate:
	docker compose run --rm migrate

# Usage: make db-revision m="add summary_length" (dev overlay: the file lands in backend/migrations)
db-revision:
	docker compose -f compose.yaml -f compose.dev.yaml run --rm migrate alembic revision --autogenerate -m "$(m)"

db-current:
	docker compose run --rm migrate alembic current

backup:
	mkdir -p data/backups
	docker compose exec -T postgres pg_dump -U videoai -Fc videoai > data/backups/videoai-$$(date +%Y%m%d-%H%M%S).dump

# Tests always run in Docker, on an isolated stack (see AGENTS.md).
test: test-backend test-frontend

test-backend:
	docker compose -f compose.test.yaml run --rm backend-tests

test-frontend:
	docker compose -f compose.test.yaml run --rm frontend-tests
