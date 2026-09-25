import json
from dataclasses import asdict

import pytest

from nitka.normalization import content_fingerprint, normalize_record


def warnings_for(result, field):
    return [issue for issue in result.issues if issue.field == field]


@pytest.mark.parametrize("value", [None, [], "record", 7, True])
def test_non_objects_are_rejected(value):
    result = normalize_record(value)
    assert result.document is None
    assert result.issues[0].reason == "record_is_not_object"


@pytest.mark.parametrize(
    "record",
    [{}, {"title": None}, {"title": ""}, {"title": " \t "}, {"title": 8}, {"title": False}],
)
def test_title_is_required_nonempty_text(record):
    result = normalize_record(record)
    assert result.document is None
    assert result.issues[0].field == "title"
    assert result.issues[0].reason == "missing_required_title"


def test_output_contains_only_normalized_source_fields_and_computed_fields():
    result = normalize_record({"title": " Example ", "version": 3, "relevance_score": 8})
    assert set(result.document) == {
        "external_id",
        "title",
        "abstract",
        "body",
        "published_at",
        "updated_at",
        "status",
        "document_type",
        "language",
        "region",
        "source_name",
        "url",
        "doi",
        "open_access",
        "peer_reviewed",
        "citation_count",
        "page_count",
        "word_count",
        "author_name",
        "organization_name",
        "tags",
        "completeness_score",
        "quality_tier",
    }
    assert result.document["title"] == "Example"


@pytest.mark.parametrize(
    "field",
    [
        "external_id",
        "abstract",
        "body",
        "region",
        "source_name",
        "author_name",
        "organization_name",
    ],
)
def test_optional_text_is_trimmed_and_preserves_display_case(field):
    result = normalize_record({"title": "Example", field: "  Mixed Case  "})
    assert result.document[field] == "Mixed Case"
    assert not warnings_for(result, field)


@pytest.mark.parametrize("value", ["", "   ", None])
def test_optional_empty_text_becomes_null_without_warning(value):
    result = normalize_record({"title": "Example", "abstract": value})
    assert result.document["abstract"] is None
    assert not warnings_for(result, "abstract")


@pytest.mark.parametrize("value", [8, False, ["text"], {"text": "value"}])
def test_invalid_optional_text_is_null_and_retains_document(value):
    result = normalize_record({"title": "Example", "external_id": value})
    assert result.document["external_id"] is None
    assert warnings_for(result, "external_id")


@pytest.mark.parametrize("field", ["author_name", "organization_name"])
@pytest.mark.parametrize("value", ["N/A", " n/a ", "NA", " Not Applicable "])
def test_people_and_organizations_discard_obvious_placeholders(field, value):
    result = normalize_record({"title": "Example", field: value})
    assert result.document[field] is None
    assert warnings_for(result, field)


@pytest.mark.parametrize("value", ["Bad\x00text", "Bad\ud800text", "Bad\udffftext"])
def test_title_rejects_text_postgres_cannot_store(value):
    result = normalize_record({"title": value})
    assert result.document is None
    assert warnings_for(result, "title")


@pytest.mark.parametrize(
    "field", ["abstract", "body", "external_id", "author_name", "organization_name"]
)
@pytest.mark.parametrize("value", ["Bad\x00text", "Bad\ud800text"])
def test_optional_text_unsafe_for_postgres_becomes_null(field, value):
    result = normalize_record({"title": "Example", field: value})
    assert result.document[field] is None
    assert warnings_for(result, field)


def test_issue_previews_are_bounded_json_safe_and_do_not_leak_body():
    body = "PRIVATE BODY " * 100 + "\x00"
    result = normalize_record(
        {"title": "Example", "body": body, "external_id": {"value": "x" * 1000}}
    )
    body_issue = warnings_for(result, "body")[0]
    assert "PRIVATE BODY" not in body_issue.value_preview
    assert str(len(body)) in body_issue.value_preview
    for issue in result.issues:
        assert len(issue.value_preview) <= 200
        json.dumps(asdict(issue), ensure_ascii=False).encode("utf-8")


def test_input_is_not_mutated():
    incoming = {"title": " Title ", "external_id": " id ", "author_name": " Jane DOE "}
    before = incoming.copy()
    normalize_record(incoming)
    assert incoming == before


def test_content_fingerprint_ignores_case_unicode_form_and_repeated_whitespace():
    first = content_fingerprint("Climate\u00a0Policy", "A   document about policy.")
    second = content_fingerprint("climate policy", "a document about policy.")

    assert first == second
    assert content_fingerprint("Climate Policy", None) is None
    assert first != content_fingerprint("Climate Policy", "A different document about policy.")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        (" YES ", True),
        ("no", False),
        ("1", True),
        ("0", False),
    ],
)
def test_nullable_booleans_are_normalized(value, expected):
    result = normalize_record({"title": "Example", "open_access": value})
    assert result.document["open_access"] is expected
    assert not warnings_for(result, "open_access")


@pytest.mark.parametrize("value", [2, "perhaps", [], 1.0])
def test_invalid_nullable_boolean_becomes_null_with_warning(value):
    result = normalize_record({"title": "Example", "peer_reviewed": value})
    assert result.document["peer_reviewed"] is None
    assert warnings_for(result, "peer_reviewed")


def test_dates_are_parsed_and_inverted_order_is_warned():
    result = normalize_record(
        {"title": "Example", "published_at": 20240115, "updated_at": "2024-01-14"}
    )
    assert str(result.document["published_at"]) == "2024-01-15"
    assert str(result.document["updated_at"]) == "2024-01-14"
    assert any(issue.reason == "updated_before_published" for issue in result.issues)


@pytest.mark.parametrize("value", [None, "", "invalid-date", "2023-13-45", 20231345, True])
def test_missing_or_invalid_dates_become_null_with_warning(value):
    result = normalize_record({"title": "Example", "published_at": value})
    assert result.document["published_at"] is None
    assert warnings_for(result, "published_at")


def test_tags_normalize_dedupe_and_discard_invalid_items():
    result = normalize_record(
        {"title": "Example", "tags": [" Energy ", "energy", "", None, 4, "Policy"]}
    )
    assert result.document["tags"] == ["energy", "policy"]
    assert len(warnings_for(result, "tags")) == 3


@pytest.mark.parametrize("value", [" Climate,climate, Policy ", None])
def test_tags_accept_csv_and_null(value):
    result = normalize_record({"title": "Example", "tags": value})
    expected = ["climate", "policy"] if value is not None else []
    assert result.document["tags"] == expected


@pytest.mark.parametrize("field", ["citation_count", "page_count", "word_count"])
@pytest.mark.parametrize("value", [0, 12, None])
def test_nonnegative_integer_counts_are_accepted(field, value):
    result = normalize_record({"title": "Example", field: value})
    assert result.document[field] == value
    assert not warnings_for(result, field)


@pytest.mark.parametrize("field", ["citation_count", "page_count", "word_count"])
@pytest.mark.parametrize("value", [-1, True, 1.5, "12", 2**63])
def test_invalid_counts_become_null_with_warning(field, value):
    result = normalize_record({"title": "Example", field: value})
    assert result.document[field] is None
    assert warnings_for(result, field)


def test_status_language_type_url_and_doi_rules():
    result = normalize_record(
        {
            "title": "Example",
            "status": " PUBLISHED ",
            "document_type": " REPORT ",
            "language": " English ",
            "url": "https://example.org/path",
            "doi": "10.1234/Example-Value",
        }
    )
    document = result.document
    assert document["status"] == "published"
    assert document["document_type"] == "report"
    assert document["language"] == "en"
    assert document["url"] == "https://example.org/path"
    assert document["doi"] == "10.1234/example-value"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", False),
        ("status", "review"),
        ("language", "xx"),
        ("url", "http://"),
        ("url", "https://user:pass@example.org"),
        ("doi", "not-a-doi"),
    ],
)
def test_invalid_categorical_and_identifier_values_are_null_with_warning(field, value):
    result = normalize_record({"title": "Example", field: value})
    assert result.document[field] is None
    assert warnings_for(result, field)


@pytest.mark.parametrize(
    ("record", "score", "tier"),
    [
        ({"title": "A", "abstract": "B", "author_name": "C", "organization_name": "D"}, 45, "low"),
        ({"title": "A", "body": "B"}, 50, "medium"),
        (
            {
                "title": "A",
                "body": "B",
                "abstract": "C",
                "published_at": "2024-01-01",
                "tags": ["x"],
            },
            80,
            "high",
        ),
        (
            {
                "title": "A",
                "body": "B",
                "abstract": "C",
                "published_at": "2024-01-01",
                "author_name": "C",
            },
            75,
            "medium",
        ),
        (
            {
                "title": "A",
                "body": "B",
                "abstract": "C",
                "published_at": "2024-01-01",
                "tags": ["x"],
                "author_name": "C",
                "organization_name": "D",
                "url": "https://example.org",
            },
            100,
            "high",
        ),
    ],
)
def test_completeness_score_and_tier_boundaries(record, score, tier):
    result = normalize_record(record)
    assert result.document["completeness_score"] == score
    assert result.document["quality_tier"] == tier
