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
- Use synthetic product information only, with one scoped exception: an authority's published
  ePI (public, approved text) for roadmap item 3a. No client or confidential content until the
  data-handling paragraph in `docs/design/verifiable-answers.md` is written.

## Commands

- Bootstrap: `npm ci`
- Format: `npm run format`
- Full local gate: `npm run check` (its test step is `npm run test:coverage`: the suite under v8
  coverage with per-directory floors in `vitest.config.ts`; raise a floor when coverage rises)
- Every CI gate (Node, Zone A, Agent; not the official HL7 validator): `scripts/check-all.sh`.
  Needs Python 3.14 and uv 0.12.17 (`agent/.uv-bootstrap`, `zone-a/.uv-bootstrap`, `$UV` or
  `PATH`); `test/ci/check-all.test.ts` keeps it in step with `.github/workflows/ci.yml`.
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
5. Run the full local gate before commit. `.github/workflows/ci.yml` is the merge gate on every
   pull request. On `main`, `npm run check` runs again as its own `gate` job in
   `.github/workflows/deploy.yml`, which holds no cloud token; the `deploy` job needs it. A merge
   that touches only documentation, `test/`, `agent/`, `zone-a/`, `.claude/` or `.cursor/` does
   not deploy. Cloud Build
   only builds images.
6. Use a separate git worktree for every parallel writing agent.

## Toolchain

Node 22.14 (`.nvmrc`, the version CI uses), Python 3.14 with uv for `zone-a/` and `agent/`
(`.python-version`), Java 21 for the official HL7 validator, and Terraform 1.16+.
Read-only development and unit tests need no cloud credentials. Real-cloud smoke tests use
Application Default Credentials and an explicitly configured non-production GCP project.
