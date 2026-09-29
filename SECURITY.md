# Security policy

This repository is proprietary to KHS Advisory LLC (see `LICENSE`).

## Reporting a vulnerability

Report suspected vulnerabilities privately to **security@khsadvisory.com**. Do not open a public
issue, pull request or discussion.

> **Placeholder.** This address is not yet confirmed as monitored. Confirming it, or replacing it
> with the monitored address, is an item on the production gate (`docs/roadmap.md`).

Include what you found, how to reproduce it, and what it could affect. You will receive an
acknowledgement within five working days and a plan for remediation once the report is assessed.

## Scope

- The code in this repository: the transformation pipeline, contracts, fidelity check, query
  service and agent, and the two parts that read untrusted third-party content: the authority
  importer (`src/authority/`) and the renderer (`src/render/`, Chrome without its sandbox).
- The infrastructure it declares (`infra/`) and the scripts that manage it (`scripts/gcp/`).

Google Cloud services themselves are out of scope; report those to Google.

## How the platform is protected

The controls, and the evidence that each has run, are recorded in `docs/foundations.md` and
`docs/design/cmek-rollout.md`: customer-managed keys on every store of the record, an HSM-backed
signing key for run evidence, deploys restricted to the main branch's deploy workflow through
Workload Identity Federation with no service account keys, a hermetic HL7 validator, and every
third-party action and image pinned by digest.
