# Document Intake and Review Service

Small synchronous backend for importing noisy JSONL/NDJSON documents into
PostgreSQL and querying the normalized result.

The importer reads one JSONL/NDJSON file record by record and writes PostgreSQL
in batches of 250. It is deliberately synchronous: the dataset size does not
justify a queue, worker, or async write pipeline. `POST /ingestions` and the
CLI call the same import service.

## Tech stack

- Python 3.13 and `uv`
- FastAPI and Uvicorn
- PostgreSQL 17 in Podman Compose
- SQLAlchemy 2 and synchronous psycopg 3
- Alembic migrations
- pytest and Ruff

## Quick start

Create local configuration, start PostgreSQL, install dependencies, and apply
the schema migration:

```bash
cp .env.example .env
podman compose up -d
uv sync
uv run alembic upgrade head
```

The default database is available only on `127.0.0.1:5433`, with persistent
data in the `nitka_pgdata` named Podman volume. `podman compose stop` stops the
container without removing the volume.

To import one file from the command line:

```bash
uv run nitka ingest --input /absolute/path/to/documents.jsonl
```

The command requires a path to a `.jsonl` or `.ndjson` file. It writes structured
JSON events through the standard Python logger. `INPUT_DIR` configures the
directory used to persist files uploaded through the API; by default it is
`input_docs/`.

To start the API:

```bash
uv run uvicorn nitka.api:app --host 127.0.0.1 --port 8000
```

Interactive OpenAPI documentation is then available at
`http://127.0.0.1:8000/docs`.

## Import policy

The importer retains recoverable documents and writes an `ingestion_issues`
row plus a JSON log event for each warning. A top-level JSON value that is not
an object, malformed JSON, a blank line, or a missing/non-text/blank title is
skipped. A valid document always has a non-empty `title`.

- Text is trimmed; text PostgreSQL cannot store (NUL or surrogate characters)
  is removed with a warning. Author and organization placeholders such as
  `N/A` are removed.
- Tags accept a list of strings or comma-separated string. Tags are trimmed,
  lower-cased and de-duplicated within a document. Invalid values are ignored.
- `open_access` and `peer_reviewed` accept booleans, `0`/`1`, and
  `true`/`false`/`yes`/`no` strings.
- Dates accept `YYYY-MM-DD` or `YYYYMMDD`. Invalid, empty, and missing dates
  become `NULL` and emit a warning. `updated_at` earlier than `published_at`
  is retained with `updated_before_published`.
- `status` accepts `published`, `draft`, `archived`, and `unknown`, ignoring
  case. Unsupported values become `NULL`.
- `document_type` is lower-cased. `english` maps to `en`; `xx` becomes `NULL`.
- `citation_count`, `page_count`, and `word_count` accept only non-negative
  JSON integers. Text, floats, booleans and negative values become `NULL`.
- A URL must be a credential-free HTTP(S) URL with a hostname. DOI must match
  `10.<digits>/...`. There are no network lookups.

### Document uniqueness and duplicates

`external_id` is optional and deliberately **not** a uniqueness key: the source
may assign the same value to distinct documents. During import, a record is
considered a duplicate when the first applicable rule below finds a matching
stored document:

1. A normalized DOI matches. This rule applies only when the incoming document
   has a valid DOI.
2. A `content_fingerprint` matches when the incoming document has a non-empty
   body. The fingerprint is SHA-256 of the title and body after Unicode NFKC
   normalization, whitespace collapsing, and case folding. Thus differences in
   case, repeated whitespace, or equivalent Unicode representations do not
   create a second document.
3. If both DOI and body are absent, a `record_fingerprint` matches. It is
   SHA-256 of the complete normalized input record. This prevents exact
   normalized repeat imports, but does not treat a title on its own as unique.

Both kinds of fingerprint are stored in the `documents.content_fingerprint`
column; `title_body_fingerprint` and `record_fingerprint` name the two
calculation variants. PostgreSQL enforces unique normalized DOI and unique
non-null `content_fingerprint` values.

On a match the incoming record is **not updated or merged** into the stored
document. It is skipped, and `ingestion_issues` records the matching rule and
the existing document ID. Duplicate records do not produce a per-record log
event; their total is available as `already_imported` in the import summary.
This is intentional: without a defined field-level merge policy, silently
filling missing data could overwrite or combine contradictory source values.
Enrichment of existing documents is outside the current ingestion scope.

One transaction-scoped PostgreSQL advisory lock prevents concurrent imports. A
second API request receives `409 Conflict`. A fatal failure rolls back the
documents and issues from the active import and records a separate failed run.

## Completeness score

The additional processing step is deterministic ranking, calculated after
normalization and exposed in document responses and `/stats`.

| Condition | Points |
| --- | ---: |
| Valid non-empty title | 25 |
| Non-empty body | 25 |
| Non-empty abstract | 10 |
| Valid `published_at` | 10 |
| At least one valid tag | 10 |
| Valid author | 5 |
| Valid organization | 5 |
| Valid URL or DOI | 10 |

`high` is 80–100, `medium` is 50–79, and `low` is 0–49. This measures data
completeness, not relevance or editorial quality. The inconsistent source
`relevance_score` and `version` fields are outside the MVP schema.

## Database schema

| Table | Purpose |
| --- | --- |
| `documents` | Normalized document, nullable metadata, score, tier and deduplication keys |
| `authors` | Unique trimmed author names |
| `organizations` | Unique trimmed organization names |
| `tags` | Unique normalized tag names |
| `document_tags` | Document-to-tag many-to-many relationship |
| `ingestion_runs` | Lifecycle, counters, final entity counts and failure status |
| `ingestion_issues` | File/line-level skipped-record and field warning evidence |

The initial schema is versioned in
[`alembic/versions/0001_initial_schema.py`](alembic/versions/0001_initial_schema.py).
It includes database checks for nonblank title, non-negative numeric counts,
score range and valid quality tier.

## HTTP API

### Trigger import

```bash
curl -X POST http://127.0.0.1:8000/ingestions \
  -F 'file=@/absolute/path/to/documents.jsonl;type=application/x-ndjson'
```

The endpoint accepts one `.jsonl` or `.ndjson` file as `multipart/form-data`.
It first saves the upload in `input_docs/` (or `INPUT_DIR`) under a generated
unique name, then imports that stored file. The call waits for import completion
and returns a `200` summary such as:

```json
{
  "run_id": 1,
  "status": "completed",
  "processed": 9,
  "inserted": 5,
  "already_imported": 0,
  "skipped": 4,
  "warnings": 18,
  "final_counts": {"documents": 5, "authors": 2, "organizations": 1, "tags": 3}
}
```

### List and filter documents

```bash
curl 'http://127.0.0.1:8000/documents?tag=energy&status=published&sort=score'
curl 'http://127.0.0.1:8000/documents?q=energy&date_from=2024-01-01&date_to=2024-12-31'
```

`GET /documents` supports `page` (default 1), `page_size` (default 20, maximum
100), `date_from`, `date_to`, `tag`, `organization`, `status`, `q`, and
`sort=id|score`. Filters combine with AND. Search is case-insensitive literal
substring matching against title/body; `%` and `_` have no wildcard meaning.

### Retrieve one document and statistics

```bash
curl http://127.0.0.1:8000/documents/1
curl http://127.0.0.1:8000/stats
```

`GET /documents/{id}` returns related author, organization and tags. `GET /stats`
returns entity totals, status/organization/tag distributions, average
score and all three quality-tier counts.

## Sample full run and stats

The following output is from a complete run against the hand-authored
`tests/fixtures/sample.jsonl` fixture, not the supplied input dataset:

```text
{"event":"ingestion_started","input_file":"/…/sample.jsonl"}
{"event":"record_skipped","file":"sample.jsonl","line":6,"reason":"invalid_json"}
{"event":"record_warning","file":"sample.jsonl","line":2,"field":"citation_count","reason":"invalid_nonnegative_integer"}
{"event":"record_warning","file":"sample.jsonl","line":5,"field":"title","reason":"missing_required_title"}
{"event":"scoring_completed","quality_tiers":{"low":2,"medium":1,"high":2}}
{"event":"ingestion_completed","run_id":1,"processed":9,"inserted":5,"already_imported":0,"skipped":4,"warnings":18,"final_counts":{"documents":5,"authors":2,"organizations":1,"tags":3}}
```

The verified fixture `/stats` response was:

```json
{
  "documents": 5,
  "authors": 2,
  "organizations": 1,
  "tags": 3,
  "average_completeness_score": 58.0,
  "quality_tiers": {"low": 2, "medium": 1, "high": 2},
  "statuses": {"unknown": 2, "draft": 1, "published": 1, "archived": 1},
  "organizations_by_document": {"Example Institute": 1},
  "tags_by_document": {"policy": 2, "water": 1, "energy": 2}
}
```

## Development

```bash
uv run pytest -q
uv run ruff check nitka tests alembic main.py
uv run ruff format --check nitka tests alembic main.py
```

Tests create and drop a unique PostgreSQL schema for each test and never touch
application tables. `input_docs/`, local reports, exploratory profiler output,
`.env`, and `docs/` are ignored by Git.
