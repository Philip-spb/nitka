# Document intake and review service

## Agreed ingestion policy

The source is deliberately noisy. The importer processes input one JSONL line
at a time, continues after bad records, and records every skipped record or
field-level repair in the ingestion log.

### Record acceptance

- A JSON value that is not an object (for example, a top-level array) is
  skipped with `record_is_not_object`.
- A document requires a `title`: it must be a non-empty string after trimming.
  Missing, `null`, empty, or non-string titles cause the whole record to be
  skipped with `missing_required_title`.
- `external_id` is useful for source traceability but is not a required field.
- All other recoverable field problems keep the document and create a warning
  with the source file, line number, field, original value, and reason.

### Normalisation rules

- Whitespace is trimmed from textual values.
- Tags accept an array of strings or a comma-separated string. Empty values,
  non-string array items, numbers, and objects are omitted with a warning.
- `open_access` and `peer_reviewed` accept `true`/`false`, `1`/`0`, and
  `yes`/`no` (case-insensitive). Other values become `NULL` with a warning.
- Dates accept `YYYY-MM-DD` and `YYYYMMDD`. Invalid, empty, and absent dates
  become `NULL` with a warning. Date filtering uses `published_at` only.
  Valid records where `updated_at` precedes `published_at` are retained and
  receive `updated_before_published`.
- `document_type` is lower-cased; empty values become `NULL`.
- `language` maps `english` to `en`; `xx`, empty, and absent values are
  treated as unknown (`NULL`).
- Authors and organisations require non-empty text. Numeric values are
  omitted with a warning. Similar-looking names are not merged automatically.
- `citation_count`, `page_count`, and `word_count` are nullable integers.
  Only integers greater than or equal to zero are stored; non-numeric and
  negative values become `NULL` with a warning.
- The incoming `relevance_score` is not used for ranking because its source
  scale is inconsistent. It is outside the MVP schema.

### Additional processing: simple scoring/ranking

Each accepted document receives a deterministic `completeness_score` (0–100)
and a `quality_tier`:

| Condition | Points |
| --- | ---: |
| Valid non-empty title | 25 |
| Non-empty body | 25 |
| Non-empty abstract | 10 |
| Valid `published_at` | 10 |
| At least one valid tag | 10 |
| Valid author | 5 |
| Valid organisation | 5 |
| Valid URL or DOI | 10 |

- `high`: 80–100
- `medium`: 50–79
- `low`: 0–49

The document API returns the score and tier. Aggregate statistics include the
average score and the count of documents in each tier.

## Initial JSONL profiling

`analyze_jsonl.py` reads JSONL/NDJSON records one at a time and produces a
data-quality profile. It does not change source data. The report includes file
and record counts, malformed JSON examples, field presence/null/empty counts,
observed JSON types, and frequent values.

```bash
uv run python analyze_jsonl.py /path/to/input_docs
```

Reports are written to `reports/data-profile.json` (for further analysis) and
`reports/data-profile.md` (for a quick review). Options such as
`--sample-limit 25` and `--output-dir my-report` are available via `--help`.
