.PHONY: install db-up db-stop migrate serve ingest test lint

install:
	uv sync --locked

db-up:
	podman compose up -d

db-stop:
	podman compose stop

migrate:
	uv run alembic upgrade head

serve:
	uv run uvicorn nitka.api:app --host 127.0.0.1 --port 8000 --reload

ingest:
	uv run nitka ingest

test:
	uv run pytest -q

lint:
	uv run ruff check nitka tests alembic
	uv run ruff format --check nitka tests alembic
