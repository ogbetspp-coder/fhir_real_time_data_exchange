# ema-flow engineering instructions

## Objective

Prove deterministic interoperability from an HL7 Global ePI Type 2 product graph and
authoritative SmPC sections to EMA EU ePI 1.0.0, then write validated R5 resources to
Google Cloud Healthcare API and observe their native near-real-time BigQuery stream.

## Non-negotiable rules

- Never generate, summarize, or infer regulated clinical narrative. Preserve supplied XHTML.
- Fail closed on missing, duplicate, or ambiguous mandatory QRD sections.
- Pin and checksum all external FHIR packages and examples.
- Do not claim regulatory or GxP compliance. Produce qualification-supporting evidence.
- Never log FHIR payloads, credentials, tokens, or clinical text.
- Never commit `.env`, Application Default Credentials, Terraform state, or vendored packages.
- Use synthetic product information only.

## Commands

- Bootstrap: `npm ci`
- Format: `npm run format`
- Full local gate: `npm run check`
- Build: `npm run build`
- Local deterministic demo: `npm run demo`
- Service: `npm run dev`
- Terraform: `terraform -chdir=infra fmt -check -recursive && terraform -chdir=infra validate`
- Contracts and vectors: `npm run contracts:check` (regenerates `contracts/generated` and
  `test/fixtures/fidelity/vectors.json` and fails on drift)

## Workflow

1. Read `docs/architecture.md` and the relevant ADR before architectural changes.
2. Add or update tests with every mapping or validation change.
3. Keep transformations pure and deterministic; isolate Google API side effects.
4. Update the mapping manifest and the evidence schema (`src/contracts/run-manifest.ts`,
   regenerated into `contracts/generated/`) together.
5. Run the full local gate before commit. Cloud Build is the authoritative release gate.
6. Use a separate git worktree for every parallel writing agent.

## Cursor Cloud specific instructions

The project requires Node 22+, Java 21 for the official HL7 validator, and Terraform 1.9+.
Read-only development and unit tests need no cloud credentials. Real-cloud smoke tests use
Application Default Credentials and an explicitly configured non-production GCP project.
