from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from nitka.api import create_app
from nitka.config import Settings
from nitka.repositories.documents import DocumentRepository

FIXTURE_FILE = Path(__file__).parent / "fixtures" / "sample.jsonl"


@pytest.fixture
def client(db_engine, tmp_path) -> TestClient:
    settings = Settings(database_url="postgresql+psycopg://unused", input_dir=tmp_path / "input_docs")
    return TestClient(create_app(settings=settings, engine=db_engine))


def import_fixture(client: TestClient) -> dict:
    response = client.post(
        "/ingestions",
        files={"file": ("sample.jsonl", FIXTURE_FILE.read_bytes(), "application/x-ndjson")},
    )
    assert response.status_code == 200
    return response.json()


def test_post_ingestions_runs_synchronously_and_returns_summary(client):
    payload = import_fixture(client)

    assert payload["status"] == "completed"
    assert payload["inserted"] == 5
    assert "final_counts" not in payload


def test_post_ingestions_rejects_a_file_with_an_unsupported_extension(client):
    response = client.post(
        "/ingestions",
        files={"file": ("records.txt", b'{"title":"Document"}\n', "text/plain")},
    )

    assert response.status_code == 422


def test_documents_list_supports_pagination_filters_literal_search_and_score_sorting(client):
    import_fixture(client)

    response = client.get("/documents", params={"page": 1, "page_size": 2, "sort": "score"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 5
    assert len(payload["items"]) == 2
    assert payload["items"][0]["completeness_score"] >= payload["items"][1]["completeness_score"]

    assert client.get("/documents", params={"tag": "ENERGY"}).json()["total"] == 2
    assert client.get("/documents", params={"organization": "Example Institute"}).json()["total"] == 1
    assert client.get("/documents", params={"status": "published"}).json()["total"] == 1
    assert client.get("/documents", params={"date_from": "2024-01-10", "date_to": "2024-01-15"}).json()["total"] == 2
    assert client.get("/documents", params={"q": "100%_energy"}).json()["total"] == 1


def test_documents_list_does_not_load_tags(client, db_engine):
    import_fixture(client)
    statements: list[str] = []

    def collect_statement(*args):
        statements.append(args[2])

    event.listen(db_engine, "before_cursor_execute", collect_statement)
    try:
        with Session(db_engine) as session:
            documents, total = DocumentRepository(session).list_documents(
                page=1,
                page_size=2,
                date_from=None,
                date_to=None,
                tag=None,
                organization=None,
                status=None,
                query=None,
                sort="id",
            )
    finally:
        event.remove(db_engine, "before_cursor_execute", collect_statement)

    assert total == 5
    assert len(documents) == 2
    assert not any("tags" in statement.lower() for statement in statements)


def test_document_detail_and_stats_include_related_entities_and_scoring(client):
    import_fixture(client)
    first = client.get("/documents", params={"status": "published"}).json()["items"][0]

    detail_response = client.get(f"/documents/{first['id']}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["author_name"] == "Alice Example"
    assert detail["organization_name"] == "Example Institute"
    assert detail["tags"] == ["energy", "policy"]
    assert detail["quality_tier"] == "high"
    assert "source_file" not in detail
    assert "source_line" not in detail

    stats = client.get("/stats").json()
    assert stats["documents"] == 5
    assert stats["authors"] == 2
    assert stats["quality_tiers"].keys() == {"low", "medium", "high"}
    assert stats["average_completeness_score"] is not None


def test_api_returns_validation_and_not_found_responses(client):
    assert client.get("/documents", params={"page_size": 101}).status_code == 422
    assert client.get("/documents", params={"date_from": "2024-02-01", "date_to": "2024-01-01"}).status_code == 422
    assert client.get("/documents/99999").status_code == 404


def test_empty_stats_are_explicit(client):
    stats = client.get("/stats").json()
    assert stats["documents"] == 0
    assert stats["average_completeness_score"] is None
    assert stats["quality_tiers"] == {"low": 0, "medium": 0, "high": 0}
