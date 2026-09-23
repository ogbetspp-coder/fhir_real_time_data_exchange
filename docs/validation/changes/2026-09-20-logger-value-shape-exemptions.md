# Recorded change: `src/lib/logger.ts` value-shape exemptions, 2026-09-20

_Moved verbatim from `docs/validation/README.md`, whose "Change control for shared, evidenced
libraries" section holds the procedure (steps 0–8) this record follows. The UR- rows and
section names it cites ("above", "Release criteria") are in that file.
"The previous record" and "the record above" mean [`src/lib/logger.ts` allow-list, 2026-09-20](2026-09-20-logger-allow-list.md)._

**What changed.** `allowedFieldNames` went from a set of names to a map of name to value shape,
and the filter now drops a forbidden-looking key unless its value also matches the shape
recorded for it: `credentialType` must be a short lowercase token, `resourceType` letters only,
`resourceId` an identifier. A 512-character string under `credentialType` is now dropped where
the previous record says it would have been written.

**Why.** The previous record's own blast-radius paragraph named the hazard: the exemption was
by key name, while the guarantee that made it safe (the field's type at its one call site) lived
in another file and was stated only in a comment. A reviewer reading the logger could not see
why the exemption was safe. Enforcing the shape where the exemption is made puts the claim and
the code in the same place.

**Impact assessment (step 0).** Importers are unchanged from the previous record: the worker
(`src/server.ts`, `src/app.ts`, `src/pipeline.ts`, `src/gcp/submission-reader.ts`) and the query
service (`src/query/app.ts`, `src/query/server.ts`); the Python agent does not import it. No
call site in `src/` logs `resourceType` or `resourceId` today, and the only `credentialType`
call site passes a `CredentialType` enum member, so no line any service writes changes. The
change can only narrow what is written, never widen it, so no previously produced evidence is
affected and no version literal moved. Steps 1–3 and 7 do not apply.

**Blast radius.** A future call site that puts a value of the wrong shape under one of the three
exempt keys loses that field from the line rather than leaking it. The failure mode is a missing
field, which a contract check on the written line catches, rather than content in the log.

**Approval (step 8).** Not obtained, for the same reason as the record above.
