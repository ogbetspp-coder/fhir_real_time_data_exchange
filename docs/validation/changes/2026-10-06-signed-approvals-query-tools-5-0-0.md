# Recorded change: signed approvals, `approval-statement` 1.0.0, `review-record` 1.0.0 and `query-tools` 5.0.0, 2026-10-06

_Follows the procedure in `docs/validation/README.md`, "Change control for shared, evidenced
libraries" (steps 0–8). Roadmap item 2, phase 1 build steps 2 to 5 of `docs/design/approval.md`._

**What changed.**

- Two new contracts, 1.0.0 each, generated into `contracts/generated/`
  (`src/contracts/approval.ts`). `approval-statement`: the signed `ApprovalStatement` of D3 and its
  envelope `{ statement, signatureBase64 }`, whose canonical JSON is a document's head entry and
  the data of its versioned Provenance's signature. `review-record`: what the approver is shown
  (D6), which the signer renders into the one review file the statement's `reviewSha256` covers.
  The statement carries the `approve` kind only; the design's amendment of 2026-10-06 says why
  `request` is not in 1.0.0.
- `QUERY_TOOLS_VERSION` moves from `4.1.0` to `5.0.0`, a **major**: `QueryErrorCode` gains
  `not-approved`; `SectionContent` and `ProvenanceDetail` gain an optional `approval`
  (`ApprovalCitation`: the statement's hash, kind, meaning and sequence, the approver's subject,
  role, name and e-mail, the signing time, `superseded` and `supersededBy`); `QueryAuditRecord`
  gains optional `approverSub` and `statementSha256`. Nothing else in the schema changed (the diff
  of `contracts/generated/query-tools.schema.json` against `main` adds these and moves
  `PrincipalId` and `ApproverRole` within `$defs`).
- Zone A reads neither new contract (`NOT_ZONE_A` in `zone-a/scripts/generate_models.py` and its
  two tests).

**Classification.** ADR 0002, "Versioning": enum additions on a field a client branches on are
major. `query-tools`' added fields are optional, so with the query service's
`APPROVAL_VERIFICATION` off (the default) every answer it gives is one 4.1.0 also accepts: no
answer carries `approval` and none is `not-approved`. With it on, every answer carries `approval`,
which a 4.1.0 reader refuses (its objects are strict).

**Impact assessment (step 0).**

- `src/contracts/approval.ts` is imported by the approval library (`src/approval/`), the signer
  (`src/signer/`), the worker (`src/pipeline.ts`, `src/config.ts`) and the query service
  (`src/query/`).
- `src/contracts/query-tools.ts` is imported by the query service. With verification off its
  behaviour is unchanged: every existing test under `test/query/` passes unmodified.
- The Python agent vendors `query-tools` (`agent/scripts/sync_contract.py`), re-vendored in this
  change. The agent deployed on Agent Engine still carries 4.1.0; it accepts every answer the
  service gives while verification is off, and must be redeployed with 5.0.0 before verification
  is turned on (deploy order, below).
- No approved hash, fidelity vector or authority vector moves; the run manifest is unchanged.

**Deploy order.** 1. Merge: the service answers as before (verification off). 2. Approve the
demonstration documents through the signer and publish them (the design's step 6). 3. Redeploy the
agent from `main` (5.0.0), then turn `query_approval_verification` on, in one change.

**Steps 1–6.** 1: the version literals above. 2: `npm run contracts:generate`,
`npm run contracts:fixtures`, `npm run contracts:lock -- --record` this file, and
`sync_contract.py` in `agent/`. 3: no fidelity vector changed. 4: the adversarial cases are in
`test/approval/statement.test.ts` (another key, other content, another document, a replayed old
head, a stale review hash, an unmapped subject, a wrong environment, and more),
`test/signer/*.test.ts`, `test/persisted-run.test.ts` ("a persisted document run's approval") and
`test/query/approval.test.ts`. 5: ADR 0002 is unchanged; `docs/design/approval.md` carries the
amendment. 6: the traceability rows added are in `docs/validation/README.md`.

**Step 7.** Not applicable: no approved hash moves. Every document to be served with verification
on must be approved through the signer, which is the design's step 6.

**Approval (step 8).** Not obtained at the time of writing: recorded in the pull request.
