## Task: Document Intake and Review Service
Build a small backend service that ingests noisy document data into a relational database and exposes a simple API for querying documents and aggregated statistics.

Using JSONL input files, implement an ingestion command/script that: /input_docs

- reads the input files
- parses and validates records
- stores normalized data into a relational database
Design a relational schema to store:

- documents
- authors
- organizations
- tags
- ingestion runs and/or errors (optional)
Expose a small HTTP API with at least:

- POST /ingestions
 Trigger import of the dataset
- GET /documents
 List documents with filtering and pagination
- GET /documents/{id}
 Retrieve document details (including related entities)
- GET /stats
 Return aggregated statistics
Suggested filters:

- date range
- tag
- organization
- status
- simple text search (title/body)
Implement at least one additional processing step, for example:

- duplicate detection
- keyword extraction
- document classification
- simple scoring/ranking
- summary generation
Store the result and expose it via API or stats.

Provide execution logs from a full run that show:

- ingestion start/end
- number of records processed
- errors and skipped records
- derived processing steps
- final counts

---

### Notes

- Up to 1 day of work
- AI is allowed
- Publish results on github
- README with:
- database schema (migrations or SQL)
- sample execution log (choose any quickly readable format)
- example API usage (curl or similar)
- stats produced
