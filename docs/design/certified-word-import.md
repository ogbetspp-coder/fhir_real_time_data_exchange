# A company's Word SmPC through Zone B: the contract, the recompute and the drawing (ADR 0006 P4)

- Status: decided, 2026-10-06 (the owner took the recommendations: D2 (a), D3 (a), and D4's
  narrow upload path in the existing CMEK submissions bucket); being built in steps (below,
  "Progress"): D1, D2 and D4 built, D3 not
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
Contract versions: `ingestion-provenance` and `CanonicalSubmission` next major (a new value of
`kind`, which Zone B branches on: ADR 0002's rule); the run manifest records the source kind.
(Corrected 2026-10-06, after the review of #193: this said "next minor", on the ground that a new
variant of a discriminated union refuses nothing that parsed before; ADR 0002 makes an enum
addition on a field Zone B branches on a major.)

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
importer: "certified-word-import/<version>"              the TypeScript importer
changes                                                   the tracked changes the view settled
sectionPages: [{ page, key, code }]                      one page per section
extractedText                                            the pages (SourceDocumentText)
```

The extractor token is `certified-word/` and the SHA-256 of the canonical JSON of
`{ importer, recompute: recompute.versions }`, so neither the Python nor the TypeScript side can
change under the same token; the gate checks it from the source alone, and refuses a submission
another importer version made. The importer's version is locked to the hash of
`src/certified-word/` (with the fidelity scanner's two files and the strict JSON reader it uses)
and of its golden vectors (`src/certified-word/importer.lock.json`, `npm run certified-word:lock`,
`test/certified-word/lock.test.ts`), as the authority importer's is. The drawing record (D3) joins
the source when it is built, a new version again. `CanonicalSubmission` and `ingestion-provenance`
are 3.0.0 and the run manifest 6.0.0, majors (its `contractVersion` follows the submission's;
5.0.0 is frozen); the renderer gate's and the withheld design's change, which had reserved
`CanonicalSubmission` 3.0.0, takes 4.0.0 and the run manifest's next major after 6.0.0.

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
  assignments) made with the mapping this build carries for the document (the SmPC's or, since
  importer 1.2.0, the package leaflet's); the mapping's tree, codes
  included; each title one line of plain text that draws something; each narrative read by Zone
  B's scanner as its page, and a section without a narrative having the empty page and being
  neither a leaf nor one whose narrative the mapping requires; and the product (tightened after the
  review of #193, which found a substring check accepting "Synthetic Exampli", "mg" and the
  holder's address):
  - **the name** is section 1's whole first line, or that line up to just before its first
    whitespace-separated token that begins with a digit (the strength), the whitespace before it
    left out: "BRUKINSA" or "BRUKINSA 80 mg hard capsules" of "BRUKINSA 80 mg hard capsules", and
    nothing else, not "BRUKINSA 80" (the re-review of #193 found a cut at any word boundary
    accepted); it does not end in punctuation;
  - **the holder** is section 7's first line, exactly;
  - neither first line may be one only the page writes (a table's or a picture's, holding a grid
    marker or U+FFFC), and neither the name nor the holder may hold a noncharacter or U+FFFC;
  - **the EU authorisation numbers** are exactly those standing alone on section 8 (after a line's
    start, a space, a tab or one of `,;:(`, before its end, a space, a tab, one of `,;:()`, or a
    full stop that ends the line or comes before whitespace); every `E U /` there, in any case
    and with any whitespace inside it, is the start of one of them; and every line of section 8
    that holds a slash or a character drawn as one (U+2215, U+2044, U+FF0F) begins with one of
    them, so a number with a look-alike letter ("ЕU", a Cyrillic E) or slash is refused, not passed
    over. A run of presentations (`.../001-003`) is refused rather than expanded.

  The headings a person assigned are bound both ways: the result's assigned headings (its
  structure's sections with status `assigned`, each key with its paragraph) are exactly the
  request's.

- **What it makes:** narratives, pages and titles exactly as the recompute gave them; a Type 1
  graph of the confirmed product only (the MedicinalProductDefinition with our id and the EU
  product numbers, the Organization with our id, one RegulatedAuthorization per EU authorisation
  number), packs, ingredients and substances declared not supplied; the Bundle's identifier
  `certified-word:<document id>` in our system `https://khs.dev/fhir/identifier/certified-word`;
  `Bundle.timestamp` and `Composition.date` the run's `createdAt`; `Composition.title` the
  product's name.
- **The leaflet** (since importer 1.2.0, 2026-10-07; `docs/design/pl-structure.md`, "Zone B") is
  carried by its own mapping (`fhir/mappings/cap-pl-en.json`) and typed `pl`, with its own product
  check, since its template places the product elsewhere:
  - **the name** is the structure's `name` (`zone_a.leaflet`: what stands for X in every section
    1 line of the leaflet, its list of sections included), and must stand for X in section 1's
    heading as the label writes it, the mapping's form exactly, character for character ("1. What
    Synthetic Exampline is and what it is used for"); it does not end in punctuation;
  - **the holder** is the first line of section 6's holder section ("Marketing Authorisation
    Holder and Manufacturer", or "Marketing Authorisation Holder" alone), exactly, and that line is
    not one only the page writes;
  - **no EU authorisation number**: the leaflet's template has no place for one, so a person
    confirms none (a request naming one is refused), and the Type 1 record has no
    RegulatedAuthorization, which the Type 1 preflight allows for a leaflet only. The canonical
    product's id joins the leaflet to its SmPC's record.
- **False refusals these rules make**, accepted until a label shows the need:
  - a name that is neither section 1's whole first line nor that line up to its strength: a name
    whose strength is written without a space ("Brand10 mg"), whose invented name itself begins
    with a digit or holds a token that does (cut there, or taken whole), whose line names a form
    before the strength, or that is a section 1 opening otherwise (a list label, a sentence);
  - a name that ends in punctuation ("X (recombinant)");
  - a holder whose name is not section 7's first line exactly (a name over two lines, in a table,
    or after other text), and a section 1 or 7 that begins with a table or a picture;
  - a line of section 8 that holds a slash and does not begin with a number (a date "01/2024", a
    note), a number written otherwise than the strict form, a run included;
  - in a leaflet, a name written otherwise in its section 1 heading than the structure reads it
    (two spaces, a non-breaking hyphen), a section 1 heading a person assigned, and a holder
    section that begins with anything but the holder ("Marketing Authorisation Holder:" above it,
    the manufacturer first).
- **Open: the document id is bound to nothing.** The importer takes the ePI's document id as a
  person confirmed it and writes the record's identifier from it, and nothing checks that the id
  names this product's ePI and no other: two labels could be filed under one ePI, or one label's
  versions under two. P5's canonical-product registry, which keeps each document id with its
  product, and the gate before anything persists (D2 lifts the dry-run refusal only with it) must
  bind it.

Cross-language fixtures: CI's Node job has no Python, so
`zone-a/scripts/certified_word_fixtures.py` builds synthetic Word labels in Python and commits each
label (`<name>.docx`) and what `python -m zone_a.recompute` writes for it, byte for byte
(`<name>.json`, in `test/fixtures/certified-word/recompute/`), with `--check`; the importer's tests
and golden vectors read the results, and the gate's tests read the labels as the uploads (below,
"The gate").

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

**D4, as built (2026-10-07): the narrow upload path.** The producer stores the .docx in the
existing CMEK submissions bucket (`SUBMISSION_BUCKET`, `infra/security.tf`), at
`uploads/sha256/<its SHA-256, lower-case hex>.docx`, and names that URI in
`sourceDocument.document.storageUri`. The gate accepts exactly that: the configured bucket, that
prefix, that suffix and the source's own `sha256` in the name, nothing else (another bucket or
prefix, a hash in upper case or of other bytes, a path with more segments, a URL); it refuses a
`byteLength` over 32 MiB before reading; it reads with one ranged request of `byteLength + 1` bytes,
undecompressed (the submission reader's own read, `src/gcp/submission-reader.ts`), under the
worker's identity; and it requires the length and the SHA-256 of what it read to be the source's.
A Cloud Storage failure other than a missing object is not a refusal: it fails the run, as the
submission reader's does. **IAM: no change.** The worker already holds `roles/storage.objectViewer`
on the whole submissions bucket (`worker_submission_reader`), which it needs for the submissions
themselves, whose paths the producer chooses; a prefix condition on that grant would refuse those.
The prefix is held in code instead. Terraform grants no identity write access to the bucket (the
demonstration's seed, `scripts/demo/seed.ts`, writes as the operator), so there is no write grant
to narrow either; when the producer gets an identity (the label gateway), its grant should be
`objectCreator` with a condition on `uploads/sha256/`, as the signer's on the evidence bucket is.

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

## The gate

As built (2026-10-07), `src/certified-word/gate.ts` checks a certified Word submission as follows,
after the shape bound and the lossless parse, and refuses at the first that fails:

1. the importer that made it is the one the gate runs (`certified-word-import/<IMPORTER_VERSION>`
   of this build), before anything is read;
2. it reads the .docx at `document.storageUri` under its own identity (D4, above) and requires its
   SHA-256 and length;
3. it runs `python -I -X utf8 -m zone_a.recompute LABEL.docx` with `sourceDocument.recompute` on
   standard input (D2, `src/certified-word/recompute.ts`): Python's isolated mode, UTF-8 whatever
   the locale, the label in a directory of its own (removed after), the working directory and
   `ZONE_A_ROOT` the image's copy of the registry and mapping files, and an environment of
   `ZONE_A_ROOT` alone, so no variable naming a credential, a project, a proxy or a path reaches
   it; standard error discarded (a traceback may quote the label), standard output capped at 32 MiB,
   killed at 60 s. Status 0 is its result; status 1 with its refusal (strict UTF-8 and JSON,
   `{"refusal": {code, detail}}` exactly) refuses the run with the closed code
   `certified-word-recompute-refused`, the recompute's code kept in the issue only if it is a
   token, the detail never; a request naming versions this build does not have is one such refusal
   (`versions`). Anything else (no Python, the timeout, the cap, another status or a signal, status
   1 without a refusal) refuses with a closed reason (`unavailable`, `timeout`,
   `output-too-large`, `exit-status`, `not-a-refusal`);
4. it makes the importer's request again from the submission (the upload and the recompute's
   request from the source, the document id from the Bundle's identifier, the product from the
   record: the MedicinalProductDefinition's canonical id and name, the Organization's canonical id
   and name, the RegulatedAuthorizations' numbers; the approval's own fields), runs the importer
   its build contains on the recompute's bytes with the run's free fields (`submissionId`,
   `createdAt`, `extractionRunId`, `serviceVersion`, the page text's and the report's URIs), and
   requires the very submission, page text and fidelity report it was sent, by SHA-256, as the
   authority gate does. Malformed output is the importer's refusal (`bytes`, `shape`, `binding`...);
5. **the drawing (D3) is not built**: a run that is not a dry run is refused here, after all of the
   above passed, with the closed code `certified-word-drawing-missing`;
6. a dry run then goes through the ordinary gate with `certifiedWordDryRun` bound to the
   submission's hash; nothing is persisted.

**What a dry run proves**, where the worker can recompute (its image sets `RECOMPUTE_PYTHON` and
`ZONE_A_ROOT`, and `SUBMISSION_BUCKET` and `GOOGLE_CLOUD_PROJECT` are set): that the bytes at the
upload's content address are the ones the source pins, that this build's recompute makes from
them, under the source's request, the result this build's importer makes the very submission, page
text and report from, and that the ordinary gate's checks pass. It does **not** prove that Chrome
draws the narratives as Word does (D3), nor that the document id names this product's ePI (P5),
and it persists nothing. Where the worker cannot recompute (no bucket or no Python configured: a
local run, the official validation set), a dry run checks only what the submission holds (the
token, the importer, the pages against the record) as before, and any other run is refused with
`certified-word-not-recomputed`. A submission never passes without both legs: no run that is not a
dry run passes at all until D3. The two dry runs say which they were: the run's HTTP answer and its
completion log line carry the closed field `certifiedWordCheck`, `recomputed` or
`submission-only` (after the review of #196, which found the two `validated` answers alike).

**The run manifest** is unchanged (6.0.0): it already names the worker's image (`runtime.imageDigest`)
and, through the extractor token (`parser`), the importer's and every recompute version. Whether a
dry run recomputed is in its answer and log, not in the manifest; step 6 below records it there,
with the drawing, as a contract change.

**The worker's Python, where it is built and proven.** `RECOMPUTE_PYTHON` and `ZONE_A_ROOT` must be
absolute paths (the configuration refuses others: the subprocess has no PATH). The image installs
Python 3.14.7, and every CI workflow pins the same patch (`test/ci/images.test.ts`), so the
interpreter CI tests is the one that ships. The deploy rebuilds the image in Cloud Build from the
source `gcloud builds submit` uploads, which `.gcloudignore` filters: the label reader's code is
uploaded (until the review of #196 it was not, and the build would have failed),
`test/ci/gcloudignore.test.ts` holds every path an image copies, every project its lock names by
path and every path a build step reads to surviving that filter, and a Cloud Build step runs the
recompute on the committed labels inside the image it just built, byte for byte, before anything is
pushed (`scripts/ci/worker-recompute-smoke.sh`, which CI's Images job also runs, once more on a
changed result that must fail). A merge deploys for exactly the files of `zone-a/`,
`label-docx-reader/` and `qrd/` the Dockerfile copies (`test/ci/deploy-trigger.test.ts`).

**Memory: an oversized label fails closed, by killing its instance.** The gate caps what it reads
(32 MiB of .docx) and what the recompute writes (32 MiB), and the reader caps what a package may
unpack to (`MAX_PACKAGE_BYTES`, 256 MiB, 20 MiB a part), but not what the Python process holds while
it reads: its memory counts against the worker container's (1 GiB, four requests at once,
`infra/run.tf`). A label large enough to exceed it gets the instance killed for memory by Cloud
Run: the run fails with no answer and nothing is persisted (a certified Word run persists nothing
yet in any case), and every other request in flight on that instance fails with it, as a crash
does. Nothing is accepted that should not be; the cost is availability. Not measured: the first
deploy should record the recompute's peak memory on the largest label at hand (the US prescribing
information of 9,795 paragraphs) and, if it is near the limit, run one recompute at a time or give
the worker more memory.

Still to build:

- (step 5) D3's signed drawing record for the submission's narratives, required in place of the
  refusal above;
- (step 6) the ordinary gate with that proof bound to the submission's hash, in place of
  `certifiedWordDryRun`, and the recompute's run and the drawing recorded in the run manifest.

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
   `certified-word` source in `CanonicalSubmission` and `ingestion-provenance` 3.0.0 (run manifest
   6.0.0), the importer `src/certified-word/` (`certified-word-import/1.0.0`, locked) with its
   golden vectors over the recompute's results
   for synthetic Word labels, the titles carried as written (D6), the ADR 0001 and ADR 0002
   amendments, and the gate's dry-run-only acceptance. A certified Word submission runs through
   the worker's pipeline dry, and its record and EMA output pass the official validator.
3. **The recompute in the worker, and the upload path** (D2 and D4, 2026-10-07; "D4, as built" and
   "The gate" above): the worker image carries Python 3.14.7 and the zone-a and label-docx packages
   from `zone-a/uv.lock`, and the registry and mapping files; the gate reads the upload from its
   content address and requires its hash and length, runs the recompute in an isolated subprocess,
   runs the importer again and requires the same submission, page text and report; a run that is
   not a dry run is still refused, now with `certified-word-drawing-missing`. `zone_a.recompute`
   reads its files from `ZONE_A_ROOT` where set (`recompute/1.1.0`), and the importer is
   `certified-word-import/1.1.0` (its directory holds the gate). Tested in CI three ways: the Check
   job runs the gate on the recompute's committed results (no Python there), the Zone A job runs
   the same tests with its own Python through the gate's runner and requires it to make each
   committed result again byte for byte, and the Images job runs the gate's runner inside the
   worker image on the committed labels, byte for byte, as Cloud Build does in the image it is
   about to push (after the review of #196: "The worker's Python, where it is built and proven").
4. **The package leaflet** (2026-10-07, importer 1.2.0; "The leaflet" above and
   `docs/design/pl-structure.md`, "Zone B"): the importer carries `document: "pl"` by the
   leaflet's mapping, the worker takes each source's mapping by its document type, and a certified
   Word leaflet runs through the worker's pipeline dry, its EMA output the EMA's leaflet document,
   which passes the official validator.
5. Next: the drawing records (D3); and P5's form, which supplies what
   the request says a person confirmed, and the registry that binds the document id.
