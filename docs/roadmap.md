# Technical roadmap

This is the engineering plan for the hub, in the order the pieces should be built. It names
the Google Cloud component intended for each item, because the standing decision is to use
fit-for-purpose managed services rather than build what Google already operates.

Sizes are rough build effort: **S** days, **M** a couple of weeks, **L** longer. Nothing here
is a delivery commitment, and nothing here claims regulatory or GxP compliance; see
`docs/validation/README.md` for what would be required before regulated use.

## How the order was chosen

Three rules decided the sequence below.

1. **Test the load-bearing assumption first.** The entire Zone A design rests on an extractor
   producing page text that satisfies `docs/fidelity-normalization.md` section 7. That
   contract was written before any extractor met it. Item 0 exists to find out, cheaply,
   before anything is built on it.
2. **Reach a demonstrable end-to-end path before hardening further.** Four adversarial
   review rounds have made the deterministic pipe solid. The larger risk is now building
   nothing on it. The query service (item 1) is demonstrable on the store as it stands today
   and is deliberately placed ahead of the structuring work it does not depend on.
3. **Math first, AI on the remainder.** SmPC section headings are standardised and numbered.
   A deterministic segmenter sections most CAP documents with no model and passes fidelity by
   construction; AI structuring is then added as a measured improvement over it, not as the
   foundation. That is a stronger story for a regulated buyer, and it is the brand rule
   ("AI proposes, math proves, humans decide") applied to the build order.

## Delivered

| Item                                                                                                                      | Components                                       |
| ------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| Deterministic Type 2 → EMA ePI transform, profile validation, persistence, signed evidence                                | Cloud Run, Cloud Healthcare API, GCS, Cloud KMS  |
| Near-real-time analytical projection                                                                                      | Healthcare API native BigQuery stream, Workflows |
| Zone A / Zone B trust boundary: `CanonicalSubmission` contract, hash-bound approval, ingress gate                         | Zod → generated JSON Schema, checked in CI       |
| Mechanical narrative fidelity check (`fidelity-norm/1.1.1`) with golden vectors and a cross-language differential harness | Pure library, no cloud dependency                |
| By-reference submission transport, `document` run source, Workflows document branch                                       | Cloud Storage, Cloud Run, Workflows              |
| Queryable transformation ledger incl. approval and fidelity columns                                                       | BigQuery                                         |
| Per-client retention as native Cloud Storage policy                                                                       | Cloud Storage, Terraform variables               |
| Deploy pipeline with a quality gate that runs before any cloud credential exists                                          | GitHub Actions, Terraform, Cloud Build           |

## Next, in order

| #   | Item                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | Components                                                             | Size |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- | ---- |
| 0   | **Extractor spike.** Can a real extractor produce page text that satisfies the extractor contract and passes the fidelity check? Controlled round trip on a synthetic SmPC PDF, plus a counts-only characterisation of a real-world PDF. Ends in a written go / conditional / no-go. See `docs/design/extractor-spike.md`                                                                                                                                                                          | Document AI (Layout Parser), Cloud Storage                             | S    |
| 1   | **ePI query service, phase 1.** Read-only Model Context Protocol endpoint with `find_product`, `get_section`, `get_provenance`; entitlement filter and audit logging from the first line. Demonstrable on the store as it is. See `docs/design/epi-mcp-query-service.md`                                                                                                                                                                                                                           | Cloud Run, Identity Platform, Cloud Armor                              | M    |
| 2   | **Zone A skeleton** in a `zone-a/` directory of this repository: pydantic models generated from `contracts/generated/`, a CI gate mirroring `npm run check`, and the fidelity check re-implemented with a byte-for-byte golden-vector equivalence test — the first real test of the "language-neutral specification" claim                                                                                                                                                                         | Cloud Run, Artifact Registry                                           | M    |
| 3   | **Parser and deterministic segmenter.** Per the spike's verdict, a hybrid: characters from the PDF's embedded text layer through a pinned deterministic library (character-exact by construction), structure — headers, footers, heading levels, table cells — from Document AI's blocks aligned onto it, Document AI's own text never emitted; batch processing for documents past the online page limit; QRD-heading-based sectioning with no model; every component version pinned and recorded | pdf.js / PyMuPDF, Document AI (Layout Parser, batch), Cloud Storage    | M    |
| 4   | **Approval.** A command-line attestation first (enough to demonstrate the hand-off), then the approval API with an append-only record store and an explicit authorization model                                                                                                                                                                                                                                                                                                                    | Cloud Run, Identity Platform, Firestore or Spanner, Cloud KMS          | M    |
| 5   | **Minimal reviewer UI.** Source document beside sections, span highlighting, hash badges, a signed approve action; narrative never editable. The point at which human-in-the-loop becomes visible to a buyer                                                                                                                                                                                                                                                                                       | Angular Material 3 or Flutter, Firebase App Hosting, Identity Platform | L    |
| 6   | **AI structuring** as a measured improvement over item 3: constrained extraction into the contract types for what the segmenter cannot do, codes only from terminology lookups with receipts, an adversarial evaluation harness reporting coverage with and without the model                                                                                                                                                                                                                      | Vertex AI (Gemini, controlled generation), Vertex AI Evaluation        | L    |
| 7   | **Terminology facade** (`$validate-code`, `$expand`) over the client's ontology platform with versioned lookup receipts — needed once item 6 assigns product codes, not before                                                                                                                                                                                                                                                                                                                     | Cloud Run, Memorystore, Healthcare API terminology                     | M    |

Zone A lives in this repository for now: a separate repository would add coordination cost
and no benefit while one person builds it. Split it when a second team exists.

## Later — reach and reuse

| #   | Item                                                                                                                                | Components                            | Size |
| --- | ----------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- | ---- |
| 8   | Adapter framework: one contract plus a conformance test suite, so each system integration is a configuration rather than a project  | Cloud Run, Eventarc, Pub/Sub          | M    |
| 9   | First adapters — Veeva Vault RIM, then SAP and LIMS — proving the framework against real integration depth                          | Application Integration, Cloud Run    | L    |
| 10  | Query service phases 2–4: version history and diffs from the ledger, semantic section search, signed results                        | BigQuery, Vertex AI Search, Cloud KMS | M    |
| 11  | Adoption metrics that double as the commercial case: share of fields auto-extracted, corrections per document, minutes per document | BigQuery, Looker                      | S    |

## AI and analytics

Ranked by business value against demonstrable effect. The rule for every item: **AI proposes,
math proves, humans decide.** Nothing on this list is permitted to author, alter, or summarise
regulated narrative inside the system of record.

| Rank | Item                                                                                                     | Components                                   | Size | Depends on                                |
| ---- | -------------------------------------------------------------------------------------------------------- | -------------------------------------------- | ---- | ----------------------------------------- |
| 1    | Auditable AI structuring — the headline capability, mechanically verified rather than trusted            | Document AI, Vertex AI                       | L    | Items 0, 3, 6                             |
| 2    | Review-cycle-time prediction over the ledger, surfaced as a dashboard tile: the cheapest credible ML win | BigQuery ML linear regression, Looker        | S    | Real runs in the ledger (it has none yet) |
| 3    | Change-impact analysis — which documents, markets, and translations a variation touches, hash-linked     | Vertex AI embeddings, BigQuery vector search | M    | Converted content                         |
| 4    | Portfolio question answering with FHIR citations                                                         | Vertex AI Search                             | M    | Item 10                                   |
| 5    | Anomaly detection on numeric changes between versions (strengths, volumes, ages)                         | BigQuery ML                                  | S    | Version history                           |
| 6    | QRD compliance advisor: structural and terminology conformance advice before submission                  | Vertex AI, existing validation outcomes      | M    | Item 7                                    |
| 7    | Multilingual meaning-drift check between language versions of the same ePI                               | Vertex AI (Translation LLM), embeddings      | M    | Multilingual content                      |
| 8    | Patient-facing ePI assistant over the published leaflet                                                  | Vertex AI Search, Firebase App Hosting       | M    | Item 10                                   |

## Needs a person, not a model

- Enable GitHub branch protection on `main` requiring the CI check.
- Confirm the `approverId` policy: an opaque identity-provider subject id, never an e-mail
  address. The contract enforces the shape; the policy is an organisational decision.
- Decide whether `fixture` and `healthcare-api` run sources stay enabled once Zone A is the
  intended producer (ADR 0002 recommends disabling them).
- (Done 2026-09-20 for item 0: the deployer was granted `roles/documentai.editor` by hand —
  its roles are bootstrapped outside Terraform by design — and the spike's real-world inputs
  were EudraLex Volume 2C documents, which carry no product information, so the synthetic-only
  rule needed no exception.)

## Deliberately not on this roadmap

- Authoring, drafting, or summarising regulated narrative anywhere in the system of record.
- Claiming Annex 11 or 21 CFR Part 11 compliance. Electronic signature, identity-provider
  binding of the approver, and segregation of duties are named as future control boundaries in
  ADR 0002, not as delivered controls.
- Replacing regulatory, medical, quality, or legal review.
- Direct submission to a regulator.
