# Technical roadmap

This is the engineering plan for the hub, in the order the pieces depend on each other. It
names the Google Cloud component intended for each item, because the standing decision is to
use fit-for-purpose managed services rather than build what Google already operates.

Sizes are rough build effort: **S** days, **M** a couple of weeks, **L** longer. Nothing here
is a delivery commitment, and nothing here claims regulatory or GxP compliance; see
`docs/validation/README.md` for what would be required before regulated use.

## Delivered

| Item                                                                                              | Components                                       |
| ------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| Deterministic Type 2 → EMA ePI transform, profile validation, persistence, signed evidence        | Cloud Run, Cloud Healthcare API, GCS, Cloud KMS  |
| Near-real-time analytical projection                                                              | Healthcare API native BigQuery stream, Workflows |
| Zone A / Zone B trust boundary: `CanonicalSubmission` contract, hash-bound approval, ingress gate | Zod → generated JSON Schema, checked in CI       |
| Mechanical narrative fidelity check (`fidelity-norm/1.1.0`) with language-neutral golden vectors  | Pure library, no cloud dependency                |
| By-reference submission transport, `document` run source, Workflows document branch               | Cloud Storage, Cloud Run, Workflows              |
| Queryable transformation ledger incl. approval and fidelity columns                               | BigQuery                                         |
| Deploy pipeline with a quality gate that runs before any cloud credential exists                  | GitHub Actions, Terraform, Cloud Build           |

## Next — making the conversion wedge real

The strategic bet: most product information is still authored documents, so whoever converts
them into the canonical graph reliably owns the entry point to every downstream projection.

| #   | Item                                                                                                                                                                                                                   | Components                                                                            | Size |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- | ---- |
| 1   | Zone A service skeleton: pydantic models generated from `contracts/generated/`, a CI gate mirroring `npm run check`, and a golden-vector equivalence test proving the fidelity re-implementation matches byte for byte | Cloud Run, Artifact Registry                                                          | M    |
| 2   | Document parser: PDF/DOCX → pages with `bodyStart`/`bodyEnd`, U+00AD for discretionary hyphens, row-major TAB/LF tables, QRD-template-aware segmentation; parser versions pinned and checksummed                       | Document AI (layout parser), Cloud Storage                                            | L    |
| 3   | AI structuring: constrained extraction into the contract types, codes only from terminology lookups with receipts, plus an adversarial evaluation harness                                                              | Vertex AI (Gemini, controlled generation), Vertex AI Evaluation                       | L    |
| 4   | Review and approval API with an append-only approval record store and an explicit authorization model                                                                                                                  | Cloud Run, Identity Platform, Firestore or Spanner, Cloud KMS                         | M    |
| 5   | Terminology facade (`$validate-code`, `$expand`) over the client's ontology platform, with versioned lookup receipts                                                                                                   | Cloud Run, Memorystore, Healthcare API terminology                                    | M    |
| 6   | Reviewer UI: source document beside sections, span highlighting, hash chain, text-free diff badges, signed approve action; narrative never editable                                                                    | Angular Material 3 or Flutter, Firebase App Hosting, Identity Platform, Looker embeds | L    |

## Later — reach and reuse

| #   | Item                                                                                                                                                            | Components                                                             | Size |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- | ---- |
| 7   | Adapter framework: one contract plus a conformance test suite, so each system integration is a configuration rather than a project                              | Cloud Run, Eventarc, Pub/Sub                                           | M    |
| 8   | First adapters — Veeva Vault RIM, then SAP and LIMS — proving the framework against real integration depth                                                      | Application Integration, Cloud Run                                     | L    |
| 9   | **ePI query service (Model Context Protocol)** — read-only, citation-bearing access to the hub for any AI assistant. See `docs/design/epi-mcp-query-service.md` | Cloud Run, Identity Platform, Cloud Armor, Vertex AI Search, Cloud KMS | M    |
| 10  | Adoption metrics that double as the commercial case: share of fields auto-extracted, corrections per document, minutes per document                             | BigQuery, Looker                                                       | S    |

## AI and analytics

Ranked by business value against demonstrable effect. The rule for every item: **AI proposes,
math proves, humans decide.** Nothing on this list is permitted to author, alter, or summarise
regulated narrative inside the system of record.

| Rank | Item                                                                                                          | Components                                   | Size |
| ---- | ------------------------------------------------------------------------------------------------------------- | -------------------------------------------- | ---- |
| 1    | Auditable AI structuring — the headline capability, mechanically verified rather than trusted (items 2 and 3) | Document AI, Vertex AI                       | L    |
| 2    | Review-cycle-time prediction over the ledger, surfaced as a dashboard tile: the cheapest credible ML win      | BigQuery ML linear regression, Looker        | S    |
| 3    | Change-impact analysis — which documents, markets, and translations a variation touches, hash-linked          | Vertex AI embeddings, BigQuery vector search | M    |
| 4    | Portfolio question answering with FHIR citations                                                              | Vertex AI Search                             | M    |
| 5    | Anomaly detection on numeric changes between versions (strengths, volumes, ages)                              | BigQuery ML                                  | S    |
| 6    | QRD compliance advisor: structural and terminology conformance advice before submission                       | Vertex AI, existing validation outcomes      | M    |
| 7    | Multilingual meaning-drift check between language versions of the same ePI                                    | Vertex AI (Translation LLM), embeddings      | M    |
| 8    | Patient-facing ePI assistant over the published leaflet                                                       | Vertex AI Search, Firebase App Hosting       | M    |

## Needs a person, not a model

- Enable GitHub branch protection on `main` requiring the CI check.
- Confirm the `approverId` policy: an opaque identity-provider subject id, never an e-mail
  address. The contract enforces the shape; the policy is an organisational decision.
- Decide whether `fixture` and `healthcare-api` run sources stay enabled once Zone A is the
  intended producer (ADR 0002 recommends disabling them).

## Deliberately not on this roadmap

- Authoring, drafting, or summarising regulated narrative anywhere in the system of record.
- Claiming Annex 11 or 21 CFR Part 11 compliance. Electronic signature, identity-provider
  binding of the approver, and segregation of duties are named as future control boundaries in
  ADR 0002, not as delivered controls.
- Replacing regulatory, medical, quality, or legal review.
- Direct submission to a regulator.
