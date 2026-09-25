"""Pure validation, normalization, and completeness scoring for source records."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit

_DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
_SAFE_MAX_INTEGER = 2**63 - 1
_PLACEHOLDERS = {"n/a", "na", "not applicable"}
_DOCUMENT_FIELDS = (
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
)


@dataclass(frozen=True)
class Issue:
    field: str
    reason: str
    value_preview: str


@dataclass(frozen=True)
class NormalizationResult:
    document: dict[str, Any] | None
    issues: list[Issue]


def _is_safe_text(value: str) -> bool:
    return "\x00" not in value and not any(0xD800 <= ord(char) <= 0xDFFF for char in value)


def content_fingerprint(title: str, body: str | None) -> str | None:
    """Return a stable content key only when a document has a body."""
    if body is None:
        return None
    digest = hashlib.sha256()
    digest.update(b"content-v1\x00")
    for value in (title, body):
        canonical = " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
        encoded = canonical.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, byteorder="big"))
        digest.update(encoded)
    return digest.hexdigest()


def record_fingerprint(document: dict[str, Any]) -> str:
    """Return a stable fallback key for a normalized record without a strong identifier."""

    def canonical_value(value: Any) -> Any:
        if isinstance(value, str):
            return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
        if isinstance(value, list):
            return sorted(canonical_value(item) for item in value)
        if isinstance(value, date):
            return value.isoformat()
        return value

    payload = {field: canonical_value(document[field]) for field in _DOCUMENT_FIELDS}
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    return hashlib.sha256(b"record-v1\x00" + encoded).hexdigest()


def _preview(value: Any, *, field: str) -> str:
    if field == "body" and isinstance(value, str):
        return f"<body string length={len(value)}>"
    try:
        rendered = json.dumps(value, ensure_ascii=True, default=str, sort_keys=True)
    except (TypeError, ValueError):
        rendered = f"<{type(value).__name__}>"
    return rendered[:200]


def _issue(issues: list[Issue], field: str, reason: str, value: Any) -> None:
    issues.append(Issue(field=field, reason=reason, value_preview=_preview(value, field=field)))


def _optional_text(
    record: dict[str, Any], field: str, issues: list[Issue], *, placeholder: bool = False
) -> str | None:
    value = record.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        _issue(issues, field, "invalid_text", value)
        return None
    if not _is_safe_text(value):
        _issue(issues, field, "unsafe_text", value)
        return None
    text = value.strip()
    if not text:
        return None
    if placeholder and text.casefold() in _PLACEHOLDERS:
        _issue(issues, field, "placeholder_text", value)
        return None
    return text


def _parse_date(record: dict[str, Any], field: str, issues: list[Issue]) -> date | None:
    value = record.get(field)
    if isinstance(value, bool):
        _issue(issues, field, "invalid_date", value)
        return None
    if isinstance(value, int):
        candidate = str(value)
    elif isinstance(value, str):
        candidate = value.strip()
    else:
        candidate = ""
    if not candidate:
        _issue(issues, field, "invalid_date", value)
        return None
    formats = ("%Y-%m-%d", "%Y%m%d")
    for format_ in formats:
        try:
            return datetime.strptime(candidate, format_).date()
        except ValueError:
            continue
    _issue(issues, field, "invalid_date", value)
    return None


def _parse_bool(record: dict[str, Any], field: str, issues: list[Issue]) -> bool | None:
    value = record.get(field)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
    _issue(issues, field, "invalid_boolean", value)
    return None


def _parse_tags(record: dict[str, Any], issues: list[Issue]) -> list[str]:
    value = record.get("tags")
    if value is None:
        return []
    values: list[Any]
    if isinstance(value, list):
        values = value
    elif isinstance(value, str):
        values = value.split(",")
    else:
        _issue(issues, "tags", "invalid_tags", value)
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str) or not _is_safe_text(item):
            _issue(issues, "tags", "invalid_tag", item)
            continue
        tag = item.strip().casefold()
        if not tag:
            _issue(issues, "tags", "empty_tag", item)
            continue
        if tag not in seen:
            tags.append(tag)
            seen.add(tag)
    return tags


def _parse_count(record: dict[str, Any], field: str, issues: list[Issue]) -> int | None:
    value = record.get(field)
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _SAFE_MAX_INTEGER:
        return value
    _issue(issues, field, "invalid_nonnegative_integer", value)
    return None


def _parse_status(record: dict[str, Any], issues: list[Issue]) -> str | None:
    value = record.get("status")
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, str):
        status = value.strip().casefold()
        if status in {"published", "draft", "archived", "unknown"}:
            return status
    _issue(issues, "status", "invalid_status", value)
    return None


def _parse_document_type(record: dict[str, Any], issues: list[Issue]) -> str | None:
    value = record.get("document_type")
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, str) and _is_safe_text(value):
        return value.strip().casefold()
    _issue(issues, "document_type", "invalid_document_type", value)
    return None


def _parse_language(record: dict[str, Any], issues: list[Issue]) -> str | None:
    value = record.get("language")
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str) or not _is_safe_text(value):
        _issue(issues, "language", "invalid_language", value)
        return None
    language = value.strip().casefold()
    if language == "english":
        return "en"
    if language == "xx":
        _issue(issues, "language", "unknown_language", value)
        return None
    return language


def _parse_url(record: dict[str, Any], issues: list[Issue]) -> str | None:
    value = record.get("url")
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str) or not _is_safe_text(value):
        _issue(issues, "url", "invalid_url", value)
        return None
    url = value.strip()
    if any(char.isspace() for char in url):
        _issue(issues, "url", "invalid_url", value)
        return None
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid:
        _issue(issues, "url", "invalid_url", value)
        return None
    return url


def _parse_doi(record: dict[str, Any], issues: list[Issue]) -> str | None:
    value = record.get("doi")
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, str) and _is_safe_text(value):
        doi = value.strip().casefold()
        if _DOI_PATTERN.fullmatch(doi):
            return doi
    _issue(issues, "doi", "invalid_doi", value)
    return None


def _score(document: dict[str, Any]) -> tuple[int, str]:
    score = 25
    score += 25 if document["body"] else 0
    score += 10 if document["abstract"] else 0
    score += 10 if document["published_at"] else 0
    score += 10 if document["tags"] else 0
    score += 5 if document["author_name"] else 0
    score += 5 if document["organization_name"] else 0
    score += 10 if document["url"] or document["doi"] else 0
    tier = "high" if score >= 80 else "medium" if score >= 50 else "low"
    return score, tier


def normalize_record(value: Any) -> NormalizationResult:
    """Normalize one decoded JSON value without mutating it or touching persistence."""
    issues: list[Issue] = []
    if not isinstance(value, dict):
        _issue(issues, "record", "record_is_not_object", value)
        return NormalizationResult(document=None, issues=issues)

    raw_title = value.get("title")
    if not isinstance(raw_title, str) or not _is_safe_text(raw_title) or not raw_title.strip():
        _issue(issues, "title", "missing_required_title", raw_title)
        return NormalizationResult(document=None, issues=issues)

    document: dict[str, Any] = {field: None for field in _DOCUMENT_FIELDS}
    document["title"] = raw_title.strip()
    for field in ("external_id", "abstract", "body", "region", "source_name"):
        document[field] = _optional_text(value, field, issues)
    document["author_name"] = _optional_text(value, "author_name", issues, placeholder=True)
    document["organization_name"] = _optional_text(
        value, "organization_name", issues, placeholder=True
    )
    document["published_at"] = _parse_date(value, "published_at", issues)
    document["updated_at"] = _parse_date(value, "updated_at", issues)
    document["open_access"] = _parse_bool(value, "open_access", issues)
    document["peer_reviewed"] = _parse_bool(value, "peer_reviewed", issues)
    document["tags"] = _parse_tags(value, issues)
    document["status"] = _parse_status(value, issues)
    document["document_type"] = _parse_document_type(value, issues)
    document["language"] = _parse_language(value, issues)
    document["url"] = _parse_url(value, issues)
    document["doi"] = _parse_doi(value, issues)
    for field in ("citation_count", "page_count", "word_count"):
        document[field] = _parse_count(value, field, issues)
    if (
        document["published_at"]
        and document["updated_at"]
        and document["updated_at"] < document["published_at"]
    ):
        _issue(issues, "updated_at", "updated_before_published", value.get("updated_at"))
    document["completeness_score"], document["quality_tier"] = _score(document)
    return NormalizationResult(document=document, issues=issues)
