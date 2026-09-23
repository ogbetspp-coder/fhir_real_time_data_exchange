# Recorded change: `src/lib/logger.ts` allow-list, 2026-09-20

_Moved verbatim from `docs/validation/README.md`, whose "Change control for shared, evidenced
libraries" section holds the procedure (steps 0–8) this record follows. The UR- rows and
section names it cites ("above", "Release criteria") are in that file._

**What changed.** `credentialType` was added to `allowedFieldNames`. The forbidden-key pattern
matches the substring "credential", so before this change the logger dropped that field from
every written line and the retained audit line was a strict subset of the published
`QueryAuditRecord`. The pattern itself, the other two allowed names (`resourceType`,
`resourceId`), and the value guard (over 512 characters, or containing `<`, is dropped) are
unchanged; `credential` and `credentials` are still dropped, and a test asserts it.

**Why.** UR-24 and UR-39 both recorded the same gap: the record named a field that the retained
log line did not carry, so the evidence was narrower than the contract said it was. Either the
contract or the logger had to move. The field's value is one of the two members of the
contract's `CredentialType` enum (`id-token`, `access-token`) and cannot carry text or a token,
so widening the logger was the smaller change.

**Impact assessment (step 0).** `src/lib/logger.ts` is imported by the worker (`src/server.ts`,
`src/app.ts`, `src/pipeline.ts`, `src/gcp/submission-reader.ts`) and by the query service
(`src/query/app.ts`, `src/query/server.ts`). The Python agent under `agent/` does not import it.
`credentialType` appears nowhere in `src/` outside `src/contracts/query-tools.ts`,
`src/query/`, and the logger's own allow-list, so no worker log line changes; the full suite is
green. No previously produced evidence is affected: this changes what may be written to a log,
never a hash, a manifest, an approved content hash, or a contract version, so no version
literal was bumped and steps 1–3 and 7 do not apply.

**Blast radius.** Any service importing the logger may now write a field named
`credentialType`, and the key filter will no longer stop it. What keeps that safe is the field's
type at each call site plus the value guard, not the key name — a future caller that put free
text under that key would defeat the guard unless the text were over 512 characters or carried
`<`. This is the cost of the change and is stated rather than assumed away. **Superseded by the
next record**, which replaced the bare name exemption with a name paired with a value shape, so
free text under that key is now dropped whatever its length.

**Approval (step 8).** Not obtained: author and releaser are the same identity. Branch protection
on `main` does exist and requires status checks, but it requires no reviewer, so nothing forces a
second pair of eyes (see "Release criteria"). This record is the impact assessment
step 0 requires, not the approval step 8 requires.
