# ema-flow engineering instructions

## Objective

Prove deterministic interoperability from an HL7 Global ePI Type 2 product graph and
authoritative SmPC sections to EMA EU ePI 1.0.0, then write validated R5 resources to
Google Cloud Healthcare API and observe their native near-real-time BigQuery stream.

## Non-negotiable rules

- Never generate, summarize, or infer regulated clinical narrative. Preserve supplied XHTML (a
  withheld section's fixed notice is the one generated div, under the exception below). The
  fidelity check proves its words, list numbers, table grids and embedded pictures; paragraph
  breaks, headings, bullets, list nesting and emphasis are kept, not proved. Its contract
  (`fidelity-norm/3.3.0`) qualifies structured sources (an FHIR ePI) and, from 3.2.0, a
  certified Word source (ADR 0006: a Word SmPC the label reader read exactly, its pages written
  by `zone_a.word_epi`); over any other drawn document's text (PDF, Word otherwise read) a report
  proves agreement with that text, not with the document,
  and cannot support an approval (the gate accepts a drawn submission only as a synthetic one,
  where `ALLOW_SYNTHETIC_SOURCES` is set). Presentation (styles, classes) may be dropped only by an
  authority import under ADR 0005.
- Fail closed on missing, duplicate, or ambiguous mandatory QRD sections. The one exception: an
  authority import may carry a mandatory section as withheld only under
  `docs/design/authority-import-withheld.md` (reviewed, measured evidence of the authority's defect,
  never a safety section 4.2–4.9, the record marked incomplete).
- Pin and checksum all external FHIR packages and examples.
- Do not claim regulatory or GxP compliance. Produce qualification-supporting evidence.
- Never log FHIR payloads, credentials, tokens, or clinical text.
- Never commit `.env`, Application Default Credentials, Terraform state, or vendored packages.
- Use synthetic product information only, with one scoped exception: an authority's published
  ePI (public, approved text) for roadmap item 3a and the QRD conformance check
  (`docs/design/qrd-conformance-check.md`). No client or confidential content until the
  data-handling paragraph in `docs/design/verifiable-answers.md` is written.

## Commands

- Bootstrap: `npm ci`
- Format: `npm run format`
- Full local gate: `npm run check` (its test step is `npm run test:coverage`: the suite under v8
  coverage with per-directory floors in `vitest.config.ts`; raise a floor when coverage rises)
- Every CI gate but Official validation, Renderer and Images (Node, Zone A with its `pytest --cov`
  floor, Agent): `scripts/check-all.sh`. Needs Python 3.14 and uv 0.12.17 (`agent/.uv-bootstrap`,
  `zone-a/.uv-bootstrap`, `$UV` or `PATH`); `test/ci/check-all.test.ts` holds its commands, job by
  job, to `.github/workflows/ci.yml`.
- Label reader (`label-docx-reader/`, its own Python project, brought in with its history): its
  five checks, as its README's "Development" lists them, run in that folder; CI runs them in
  `.github/workflows/label-docx-reader.yml` when it changes. Its `AGENTS.md` rules its code.
- Renderer: `npm run renderer:image`, then the `renderer:*` checks, each run in that image by
  `scripts/render/run.mjs` (Docker).
- Build: `npm run build`
- Local deterministic demo: `npm run demo`
- Service: `npm run dev`
- Terraform: `terraform -chdir=infra fmt -check -recursive && terraform -chdir=infra validate`
- Contracts and vectors: `npm run contracts:check` (regenerates `contracts/generated`,
  `test/fixtures/fidelity/vectors.json` and the importer's `test/fixtures/authority/vectors.json`
  and fails on drift). A contract version change also needs
  `npm run contracts:lock -- --record <change record>` (`contracts/versions.lock.json`). A change to
  `src/authority/` changes `IMPORTER_VERSION`, then `npm run authority:lock` (ADR 0004's amendment);
  a change to `src/certified-word/` changes its `IMPORTER_VERSION`, then
  `npm run certified-word:lock` (its vectors are regenerated under `contracts:check`)

## Workflow

1. Read `docs/architecture.md` and the relevant ADR before architectural changes.
2. Add or update tests with every mapping or validation change.
3. Keep transformations pure and deterministic; isolate Google API side effects.
4. Update the mapping manifest and the evidence schema (`src/contracts/run-manifest.ts`,
   regenerated into `contracts/generated/`) together.
5. Run the full local gate before commit. `.github/workflows/ci.yml` is the merge gate on every
   pull request. On `main`, `.github/workflows/deploy.yml`'s `gate` job, which holds no cloud
   token, runs `npm run check` again and makes the deploy's inputs; the `deploy` job installs
   nothing and waits for CI's run on the commit (every job but Renderer, which stays a required
   pull-request check). A merge touching only the paths in
   its `paths-ignore` does not deploy. Cloud Build builds images, and draws a certified Word
   label on request (`infra/word-drawing.tf`); it never applies or deploys.
6. Use a separate git worktree for every parallel writing agent.
7. Merge a pull request with a merge commit (or a squash), never by rebase or fast-forward: the
   importer lock's test reads main's first-parent history as released
   (`test/authority/lock.test.ts`), so a rebased branch would release every intermediate lock
   entry it carried and fail every later run.

## Toolchain

Node 22.22.0 (`.nvmrc`, the version CI uses and ADR 0003 pins), Python 3.14 with uv for `zone-a/`, `agent/` and `label-docx-reader/`
(`.python-version`), Java 21 for the official HL7 validator, and Terraform 1.16+.
Python follows the Google Python Style Guide as `docs/python-style.md` states (ruff enforces
it). Read-only development and unit tests need no cloud credentials. Real-cloud smoke tests use
Application Default Credentials and an explicitly configured non-production GCP project.
