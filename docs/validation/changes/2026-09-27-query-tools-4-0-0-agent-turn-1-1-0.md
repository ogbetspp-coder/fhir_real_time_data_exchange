# Recorded change: `query-tools` 4.0.0 and `agent-turn` 1.1.0, 2026-09-27

_Follows the procedure in `docs/validation/README.md`, "Change control for shared, evidenced
libraries" (steps 0–8). Audit batch B01 of the 2026-09-27 repository audit (PR #129), with its
first two review rounds' findings closed. Numbered to follow `query-tools` 3.0.0 (audit batch
B03, PR #127, recorded in `2026-09-27-query-tools-3-0-0.md`); this change is to be rebased onto
that one and its generated schemas regenerated, never hand-merged._

**What changed.**

- `QUERY_TOOLS_VERSION` moves to `4.0.0`, a **major** with a new `$id`
  (`https://khs.dev/contracts/query-tools/4.0.0/schema.json`). `QuoteVerification` becomes a
  union on `result`: a `match` must carry `match` (a new `$defs` entry `QuoteMatch`, with
  `endOffset` at least 1, and a zod refinement — which the published schema cannot express —
  that `startOffset` is before `endOffset`) and must have searched at least one section
  (`sectionsSearched` at least 1); a `no-match` must not carry `match`. The MCP server keeps
  advertising an object-rooted `outputSchema` for `verify_quote` (`QuoteVerificationWireSchema`),
  because MCP requires one; every answer the service returns passes the union first.
- `AGENT_TURN_VERSION` moves from `1.0.0` to `1.1.0`, a **minor**: optional `outcome`
  (`answered` | `tools-unavailable` | `model-failed` | `turn-id-missing` | `internal-error`),
  `errorClass` (an exception's class name, `^[A-Za-z_][A-Za-z0-9_]{0,127}$`), `principalDigest`
  (`Sha256Hex`) and `assistantFlags` (`reserved-label-removed` | `checksum-removed` |
  `identifier-removed` | `label-text-repeated`); and three members of `VerificationFlag`:
  `coverage-gap`, `checksum-mismatch`, `table-not-quotable`.

**Classification.** ADR 0002, "Versioning": "Patch changes alter descriptions only; minor
changes add optional fields …; anything else is a new major `$id`. Enum additions on fields that
Zone B branches on are major." `query-tools`: an instance 3.0.0 accepts — a `match` without a
location, or over no section, or a `no-match` with one — is refused, so a consumer holding 4.0.0
refuses what an earlier producer was allowed to send: a major. `agent-turn`: every added field is
optional, and nothing in Zone B reads the agent's record (it is written by the agent and read by
people and by the join on `turnId`), so the enum additions are not on a field Zone B branches on:
a minor. A reader validating agent records against 1.0.0 refuses a 1.1.0 record that carries a
new field or flag; the records are written only by the agent, whose own vendored schema moves
with it.

**Why.** The audit (AG-4) found that the agent's post-check stamped a block "Verified" on a
`match` it did not examine: no location, no section searched, a quote hash of zeros and an
unknown normalisation version together came back verified, because `QuoteVerification` made
`match` optional whatever `result` said. The agent now holds every match to the chunk's exact
offsets and every hash (`agent/src/verifiable_answer_agent/postcheck.py`); the contract change
makes the service's promise say the same. `agent-turn` 1.1.0 carries what the audit found the
turn record could not say: how a turn that showed no answer ended (AG-6), the session user
without the e-mail address the contract refuses (AG-1), and what was done to the model's own
words (AG-3, AG-7).

**Impact assessment (step 0).**

- `src/contracts/query-tools.ts` is imported by the query service (`src/query/app.ts`,
  `src/query/tools.ts`) and the contract generator. The service never produced an instance the
  union refuses: it pairs `match` with `result: "match"` by construction and filters to the named
  section before searching. `src/query/app.ts` registers the wire schema instead of the union
  (one line); `src/query/tools.ts` types its match as `QuoteMatch`. No service output changes.
- `src/contracts/agent-turn.ts` is imported only by the contract generator; its consumer is the
  agent's vendored copy.
- The Python agent vendors both schemas (`agent/scripts/sync_contract.py`), re-vendored in this
  change; `agent/tests/test_contract.py` pins `/query-tools/4.0.0/` and `/agent-turn/1.1.0/`.
- Zone A reads neither contract.
- Previously produced evidence remains reproducible: no approved hash, vector or fixture moves.
  Agent records written under 1.0.0 remain as written (none was written by a live turn: the
  deployed agent failed every Gemini Enterprise turn before writing, audit AG-1).

**Deploy order.** The agent validates the service's answers against its vendored copy. A 4.0.0
agent reads any answer the service has ever produced as before, so the agent may be redeployed
before, with or after the service. The service's rollout changes nothing it answers. The agent
must be redeployed from `main` after this change for its records to be 1.1.0, and before the
live proof turn (roadmap item 1).

**Steps 1–6.** 1: `QUERY_TOOLS_VERSION` is `4.0.0`, `AGENT_TURN_VERSION` `1.1.0`. 2:
`contracts/generated/query-tools.schema.json`, `contracts/generated/agent-turn.schema.json` and
`contracts/generated/index.json` regenerated; the agent's vendored copies rewritten
(`sync_contract.py --check` passes). `npm run contracts:check` shows no other generated artefact
moved. 3: no fidelity vector changed. 4: the adversarial cases are in
`test/contracts/quote-verification.test.ts` (a match without a location, over no section, ending
where it starts, ending before it starts; a no-match with a location — each accepted by 2.0.1),
`agent/tests/test_postcheck.py` (the audit's loose match, a prefix, chunks that do not tile, each
wrong hash and version) and `agent/tests/test_audit.py` and `agent/tests/test_finish_turn.py`
(every `outcome`, `errorClass`, `principalDigest`, `assistantFlags`, validated against the
vendored 1.1.0 schema). 5: ADR 0002 is unchanged; this change follows its rules. 6: the rows whose
evidence changed are UR-22 (its residual "the agent's post-check … does not check that the
chunks' offsets are contiguous" is closed), UR-26, UR-27, UR-37 and UR-38; their text is left to
the documentation batch that owns `docs/validation/README.md` (B17), as is the paragraph above
the procedure that still names `2.0.1` and `1.0.0` as current.

**Step 7.** Not applicable: no approved hash moves.

**Blast radius.**

- A client of the query service that read `result` alone is unaffected; one that validated
  against the published schema now also refuses a match without a location, which the service
  never sends.
- The published `QuoteVerification` is a `oneOf`, where it was an object; a code generator
  produces a union type. `tools/list` still advertises an object.
- Agent records gain fields a 1.0.0 reader refuses; no such reader exists outside the agent.

**Approval (step 8).** Not obtained at the time of writing: recorded in the pull request.
