# Design note: the label gateway (proposed)

- Status: Proposed, for approval before any code; nothing here is built
- Date: 2026-10-05
- Related: the UI proposal (Claude Docs, "Label Intake — UI proposal"),
  `docs/design/ccds-implementation-check.md`, `label-docx-reader/src/label_docx/service.py`

## Why

The review app needs one API on its own origin: the reader's service refuses other origins and
must not face the internet until it is hardened, and every action a reviewer takes has to land in
one audit trail. A thin gateway in front of the reader and the implementation check gives the app
that API, while sign-in, storage and logs stay Google-managed services.

## Shape

| Part                     | What it is                                                                                                               |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------ |
| `gateway/` package       | TypeScript on Hono, the stack of the existing pipeline service; its own package, tests and CI job, not a route in `src/` |
| Cloud Run service        | `label-gateway`, the only one with public ingress; behind IAP with the client's SSO (Workforce Identity Federation)      |
| Reader service           | The existing `label-docx-service`, internal ingress only; the gateway calls it to ingest and read                        |
| Implementation-check job | A Cloud Run job running `zone-a/scripts/check_implementation.py` for a CCDS pair; it writes the report to Cloud Storage  |
| Cloud Storage            | Source documents, reports and wording files; CMEK, versioned, retention-locked                                           |
| Audit                    | Every request that changes something writes a structured event (who, what, which document hash) to the locked log bucket |

## First endpoints

| Method and path                      | Answers                                                                         |
| ------------------------------------ | ------------------------------------------------------------------------------- |
| `GET /api/changes`                   | Each open change: its edits' summaries (status counts, late, late markets)      |
| `GET /api/changes/{edit}`            | One edit: its redline, then each label's status, place, wording and days late   |
| `POST /api/labels`                   | Ingest a document through the reader; answers its receipt                       |
| `GET /api/labels/{sha256}`           | The reader's result, with its outcome                                           |
| `POST /api/changes/{edit}/decisions` | Justified deviation or needs update for one label, with the reviewer's identity |

The gateway verifies IAP's signed header (`x-goog-iap-jwt-assertion`, audience checked) on every
request and never trusts an unsigned identity header.

## Decisions to approve

1. **A separate service, not a route in the pipeline service.** It keeps the products apart and
   lets each be validated on its own.
2. **Reports are made by a job and stored**, not computed per request: a report is evidence, so
   it is kept as written, with its inputs' hashes.
3. **Cloud Storage first, BigQuery later**: reports as JSON objects now; an index in BigQuery
   when the portfolio views need queries across reports.
4. **One Google Cloud project per client**, made by the existing Terraform.

## Phases

1. Gateway skeleton: IAP verification, health, audit events, CI, Terraform, deploy to a
   non-production project.
2. Changes endpoints over stored reports; the review app's Changes screen reads them.
3. Ingest and decisions; decisions written to the audit trail and to the report's next run.
