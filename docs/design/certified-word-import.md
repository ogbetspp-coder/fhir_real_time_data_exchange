# A company's Word SmPC through Zone B: the contract, the recompute and the drawing (ADR 0006 P4)

- Status: decided, 2026-10-06 (the owner took the recommendations: D2 (a), D3 (a), and D4's
  narrow upload path in the existing CMEK submissions bucket); being built in steps (below,
  "Progress")
- Implements: ADR 0006 decisions 1, 5, 6 and 7, prerequisite P4
- Related: ADR 0002 (invariants 7, 8, 11), ADR 0004 (service boundaries), ADR 0005's amendment
  (the renderer gate's attested records), `docs/design/authority-import-contract.md` (D1, the
  recompute this mirrors), `docs/design/approval.md`, `zone-a/src/zone_a/word_epi.py`,
  `zone-a/src/zone_a/drawing.py`, `zone-a/src/zone_a/product.py` (proposed, not merged)

## What is built, and what is not

Zone A can now turn a Word SmPC into ePI sections: the label reader reads it exactly or refuses,
`zone_a.structure` finds its QRD sections, `zone_a.word_epi` writes each section's narrative and
page by separate code, and `zone_a.drawing` holds each narrative to what Chrome draws. None of it
reaches the canonical record: Zone B accepts no Word source but a synthetic one (invariant 11).
ADR 0006 decision 1 trusts a Word source by three things together. Zone B can run the first two
itself only if it can run the Python that makes them, and the third only with a browser. That is
the question this note puts to the owner.

## Decisions

**D1. The source kind.** `IngestionProvenance.sourceDocument` gains a third variant:

```text
kind: "certified-word"
document: { sha256, byteLength, filename, storageUri }   the .docx as uploaded (CMEK bucket)
reader: { reader, format, checker }                      the label reader's three versions
structure: { structurer, registryVersion, mappingVersion,
             assignments: [{ key, paragraph }] }          what a person confirmed (decision 4)
builder: "word-epi/<version>"                            page rule and narrative builder
drawing: { checker, application, record }                the drawing check, its Chrome, D3's record
sectionPages: [{ page, key, code }]                      one page per carried section
extractedText                                            the pages (SourceDocumentText)
```

Invariant 7's single extractor token becomes the SHA-256 of the canonical JSON of
`{reader, structure, builder, drawing.checker}` (decision 6), named `certified-word/<hash>`.
Contract versions: `ingestion-provenance` and `CanonicalSubmission` next minor (a new variant of a
discriminated union refuses nothing that parsed before); the run manifest records the source
kind.

**D1, as built (2026-10-06).** The recompute (D2, below) names every version that decides its
result, so the source pins its request rather than each version apart, and the drawing waits for
D3:

```text
kind: "certified-word"
mediaType: the .docx media type
document: { sha256, byteLength, filename, storageUri }   the .docx as uploaded (D4)
recompute: { document: smpc | pl, view, part,             zone_a.recompute's request: what a
             assignments: { key: paragraph },             person named and confirmed, and every
             versions: { recompute, reader, format,      version the recompute names
                         structurer, registryVersion,
                         mappingVersion, builder } }
changes                                                   the tracked changes the view settled
sectionPages: [{ page, key, code }]                      one page per section
extractedText                                            the pages (SourceDocumentText)
```

The extractor token is `certified-word/` and the SHA-256 of the canonical JSON of
`recompute.versions`; the gate checks it from the source alone. The drawing record (D3) joins the
source when it is built, a minor version again. `CanonicalSubmission` and `ingestion-provenance`
are 2.1.0, the run manifest 5.1.0 (its `contractVersion` follows the submission's; 5.0.0 is
frozen).

The importer (`src/certified-word/`) is a pure, deterministic function of the recompute's bytes
and what a person confirmed, as the authority importer is of the authority's bytes:

- **The request** (`CertifiedWordRequestSchema`, not a published contract: every value in it
  reaches the record): the upload's name and storage URI; the recompute's request; the ePI's
  document id, our id for the ePI this label's part is a version of, confirmed once (never derived
  from the part's position in the file, which a later version of the file may change); the
  canonical product (our id; the name and holder exactly as chosen from the label's text, with
  our id for the holder; every EU authorisation number in `zone_a.product`'s strict form); and the
  approval placeholder, an attestation as a drawn source's.
- **What it checks**, refusing at the first stage that fails with a closed reason: the request;
  the bytes (strict UTF-8 and JSON, the authority's reader); the recompute's own refusal; the
  result's shape; that the result is the one the request names (versions, document, view, part,
  assignments) made with the mapping this build carries, of an SmPC; the mapping's tree, codes
  included; each title one line of plain text; each narrative read by Zone B's scanner as its
  page, and a section without a narrative having the empty page and being neither a leaf nor one
  whose narrative the mapping requires; and the product: its name on one line of section 1, its
  holder on one line of section 7, and its EU authorisation numbers exactly those standing alone
  on section 8 (after a line's start, a space, a tab or one of `,;:(`, before its end, a space, a
  tab or one of `,;:.()`), with no other `EU/` there. A run of presentations (`.../001-003`) is
  refused rather than expanded.
- **What it makes:** narratives, pages and titles exactly as the recompute gave them; a Type 1
  graph of the confirmed product only (the MedicinalProductDefinition with our id and the EU
  product numbers, the Organization with our id, one RegulatedAuthorization per EU authorisation
  number), packs, ingredients and substances declared not supplied; the Bundle's identifier
  `certified-word:<document id>` in our system `https://khs.dev/fhir/identifier/certified-word`;
  `Bundle.timestamp` and `Composition.date` the run's `createdAt`; `Composition.title` the
  product's name.
- **The leaflet** is refused (`binding: document-not-carried`): Zone B's crosswalk and preflights
  are the SmPC's, and the canonical document types name the SmPC only.

Cross-language fixtures: the Node gate cannot run Python, so
`zone-a/scripts/certified_word_fixtures.py` builds synthetic Word labels in Python and commits
what `python -m zone_a.recompute` writes for each, byte for byte
(`test/fixtures/certified-word/recompute/`), with `--check`; the importer's tests and golden
vectors read them.

**D2. The recompute (decision 1's second leg).** Zone B must make, from the uploaded bytes and the
recorded assignments, the very pages and narratives the submission carries, as invariant 8 does
for an authority import. The code that makes them is Python (`label-docx-reader/`, `zone-a/`).

- **(a) Recommended: the worker runs it.** The worker image gains Python 3.14 and the two
  packages, installed from their `uv.lock` files with hashes, and the gate runs
  `python -m zone_a.recompute` on the bytes in a subprocess with no network, comparing its output
  byte for byte. One process's evidence stays one process's (ADR 0004 point 4); no new identity,
  bucket or service. Cost: a larger image (a Python runtime and two packages that use its
  standard library only) and seconds per import (the certified read of the largest label at hand,
  pembrolizumab's US prescribing information, 9,795 paragraphs, took 4.1 s on a laptop).
- (b) A separate recompute service on Cloud Run with its own identity, called by the gate. A new
  deployable, IAM and a network hop, for code that has no state and no secret.

Either way the recomputed code is deterministic and has no model in it, so it may run in Zone B.

**D3. The drawing (decision 1's third leg).** Zone A's drawing verdict cannot be trusted by Zone B
(Zone A's identity is not trusted; ADR 0002). Options:

- **(a) Recommended: the renderer gate's attested records, extended.** ADR 0005's amendment runs a
  dedicated Cloud Build configuration, under its own identity, that draws in a pinned Chrome image
  and signs a record only when two drawings reproduce it. Today it draws only publications pinned
  in `labels/`. Extended, it would draw a submission's narratives on request (Zone A asks, by the
  submission's narrative hashes), and Zone B would accept only a record whose signature verifies
  against the pinned key and whose hashes are the submission's. New: a build trigger that takes a
  request, and the request path. Reused: the image, the identity, the key, the verification.
- (b) Chrome in the worker image, drawing at import time. Simplest to wire; the worker image then
  carries a browser, and a browser's attack surface, in the process that holds write access to the
  FHIR store, which ADR 0004 point 1 argues against.
- (c) Zone B trusts Zone A's verdict. Rejected: it makes the drawing check a claim.

**D4. The bytes.** The uploaded .docx is stored once, content-addressed, in the CMEK intake bucket
(the label gateway's design, `docs/design/label-gateway.md`, which is also not yet approved). The
submission names it by `storageUri` and `sha256`; Zone B reads it from there under its own
identity and refuses unless the hash and length match.

**D5. Who is it for (decision 5).** `zone_a.product` proposes, from the label, its EU
authorisation numbers (strict format, each with its product number) and the exact text of
sections 1 and 7 for the name and the holder. A person confirms the name and holder once per
product, by choosing from that text. The confirmed product is a canonical product record (our id,
then each regulator's ids), kept where the gateway keeps its records. Each later label is matched
to it by exact equality of its EU product numbers. The MedicinalProductDefinition and Organization
identifiers preflight requires come from the record, never from narrative.

**D6. The titles (decision 4).** A section's title is its heading line where the registry allows
it. An assigned heading (a remediation finding the label team accepted) is carried as written, and
`src/fhir/transform.ts` does not substitute the template's title for this source kind.

As built: the title is the heading line the recompute gives (`zone_a.word_epi`), assigned or
not; the crosswalk carries it as written for a certified Word source (`TitleRule`
`as-written`), and the EMA preflight holds it only to being there, not to the template's titles.
Every other source keeps the template's rule.

**D7. The approval (decision 7).** The approver signs the statement `docs/design/approval.md`
designs, naming the submission's hash, which covers the pages, the narratives, the structure's
assignments and the canonical product it is for.

## The gate, once it recomputes

Until the worker runs the recompute (D2), `src/certified-word/gate.ts` accepts a certified Word
submission only as a dry run and refuses it when `DRY_RUN` is false
(`certified-word-not-recomputed`); nothing is compared but what the submission holds. With D2 and
D4 built, the gate will, before the ordinary gate:

1. read the .docx at `document.storageUri` under its own identity and require its SHA-256 and
   length (D4);
2. run `python -m zone_a.recompute` on those bytes with `sourceDocument.recompute` as its request,
   in a subprocess with no network; its refusal refuses the run, and so does a request naming
   versions the worker's build does not have;
3. make the importer's request again from the submission: the upload from the source, the
   recompute's request from the source, the document id from the Bundle's identifier, the product
   from the record (the MedicinalProductDefinition's id and name, the Organization's id and name,
   the RegulatedAuthorizations' numbers) and the approval's fields, with the run's free fields
   (`submissionId`, `createdAt`, `extractionRunId`, `serviceVersion`, the page text's and the
   report's URIs);
4. run the importer its build contains on the recompute's bytes and require the very submission,
   page text and fidelity report it was sent, by SHA-256, as the authority gate does;
5. with D3, require the renderer gate's signed drawing record for the submission's narratives;
6. then run the ordinary gate with that proof bound to the submission's hash, in place of
   `certifiedWordDryRun`, and record the recompute's versions and the worker's image in the run
   manifest.

## ADR amendments this carries

- ADR 0001: a Type 1 graph is allowed for a `certified-word` source.
- ADR 0002: invariant 7 admits `certified-word` with `type1`, an attested approval and the
  composite extractor token; invariant 11 admits a certified Word source that D2 recomputed and
  D3 drew; a new invariant for D4's bytes.

## What the owner is asked

1. D2: the worker runs the Python recompute (recommended), or a separate service.
2. D3: extend the renderer gate's attested records to Word narratives (recommended), or Chrome in
   the worker.
3. Whether D4 waits for the label gateway, or a narrower upload path comes first.

Nothing here is built until these are answered; the contract change (D1) and the product proposal
(D5) can be built and tested without deploying anything.

**Answered 2026-10-06 (evening):** D2 (a), the worker runs the recompute; D3 (a), the renderer
gate's attested records, extended; D4, a narrow upload path in the existing CMEK submissions
bucket, before the label gateway.

## Progress

1. **The recompute** (D2's function): `zone_a.recompute` (`recompute/1.0.0`), run as
   `python -m zone_a.recompute LABEL.docx < REQUEST.json`. One function for both sides: the
   producer makes a part's sections with it, the gate makes them again and compares. The request
   names the document (`smpc` or `pl`), the view, which part, the assignments and every version
   that decides the result; a request naming other versions is refused, so only the build that
   made a submission recomputes it. It refuses unless every section of the part is carried. No
   network, clock or browser; tested on synthetic SmPCs and leaflets, byte for byte twice.
2. **The source kind and the importer** (D1, 2026-10-06; "D1, as built" above): the
   `certified-word` source in `CanonicalSubmission` and `ingestion-provenance` 2.1.0 (run manifest
   5.1.0), the importer `src/certified-word/` with its golden vectors over the recompute's results
   for synthetic Word labels, the titles carried as written (D6), the ADR 0001 and ADR 0002
   amendments, and the gate's dry-run-only acceptance. A certified Word submission runs through
   the worker's pipeline dry, and its record and EMA output pass the official validator.
3. Next: the worker image and the gate's subprocess (D2, "The gate, once it recomputes"), the
   upload path (D4) and the drawing records (D3); the leaflet through Zone B; and P5's form, which
   supplies what the request says a person confirmed.
