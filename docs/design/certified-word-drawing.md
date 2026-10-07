# The drawing record: Zone B's proof that Chrome draws a certified Word label as it was read (ADR 0006 P4, D3)

- Status: proposed, 2026-10-07, for the owner's review; docs only, nothing here is built
- Implements: `docs/design/certified-word-import.md` D3 (a), the owner's choice of 2026-10-06
  ("extend the renderer gate's attested records"), and that note's "The gate", steps 5 and 6
- Fits: `docs/design/authority-import-renderer.md` R1 (attested records), frozen, its architecture
  owner-approved; nothing changes there for an authority import ("What this changes in R1", below)
- Related: ADR 0002 (invariant 11), ADR 0005's amendment of 2026-09-25, ADR 0006 decision 1,
  `zone-a/src/zone_a/drawing.py`, `label-docx-reader/src/label_docx/browser.py`,
  `src/certified-word/gate.ts`, `docs/design/approval.md`

## Summary

The producer asks for a drawing by sending only the stored submission's address and hash. A build
running main's code under the renderer gate's identity reads the submission and the .docx itself,
remakes the sections with `zone_a.recompute`, and checks them by `zone_a.drawing`'s rules, twice,
in a pinned Chrome-and-Python image with no network. If both runs agree, it signs a record: the
submission's hash, the .docx, each section's narrative hash and verdict, the versions and the image.
Zone B's gate (step 5) finds it by the submission's hash, checks the signature with a key pinned in
the worker image, and requires every hash to be the submission's and every section to agree. The
renderer gate's signing half was never built; D3 builds it, as R1 designs it. Recommended: run
`zone_a.drawing` itself, not a port. Three questions for the owner, at the end.

## What exists, and what is assumed

**Verified on 2026-10-07** (main at 0c64a51; the dev project, read with `gcloud` only):

- The renderer image (`Dockerfile.renderer`: chrome-headless-shell 154.0.8037.57 and the pinned
  fonts) is built and checked in CI's Renderer job only. It is pushed nowhere: dev has one image
  repository, `ema-flow-images`, holding the worker, query, signer and validator images, and no
  `renderer-images`.
- R1's signing half does not exist. The renderer note's Delivery dropped it on 2026-09-29 (3c-C3 to
  C5). Dev has no render identity, no signing key for records (the evidence key ring holds
  `approval-signing-hsm`, `evidence-encryption`, `manifest-signing` and `manifest-signing-hsm`), no
  attestation bucket, and no `cloudbuild.render.yaml`; it has no Cloud Build trigger at all, and no
  repository connection in `europe-west4`. So D3 (a)'s "Reused: the image, the identity, the key,
  the verification" holds for the image only.
- `zone_a.drawing` (`word-drawing/1.1.2`) draws with whatever Chrome is installed, through
  `label_docx.browser` (`browser-verifier/1.1.0`), with Chrome's own default stylesheet. The
  recording its tests replay was made with Google Chrome 154.0.8037.98.
- The recompute's output (`recompute/1.1.0`) holds each section's narrative but not the read it was
  made from (each paragraph's text, marks, list label and pictures), which the comparison needs.
- `src/certified-word/gate.ts`, in a worker that can recompute, refuses at step 5 every run that
  is not a dry run, with `certified-word-drawing-missing`.
- The GitHub repository is public.

**Assumed, each to be proven by a test or a measurement in the first change that builds this:**

- chrome-headless-shell runs `label_docx.browser`'s two pages (one read with `--dump-dom`, one
  over the DevTools pipe) as Google Chrome does, and gives the same verdicts on the five Word-made
  fixtures (`zone-a/tests/fixtures/word-smpc/`) and on the EMA corpus.
- What the check compares does not depend on the window's width or on the fonts: it reads text by
  block, styles as Chrome computes them, list labels and picture sizes, never glyph positions.
- A Cloud Build trigger fired by a Pub/Sub message runs as its own service account without the
  publisher holding `actAs` on it, and hands the build the message's attributes.
- Every timing not measured below ("Cost and operations").

## 1. The request

- **Who asks: the producer**, the identity that writes submissions to the submissions bucket (today
  the operator, through `scripts/demo/seed.ts`; later the label gateway). It asks after it has
  stored the .docx at its content address (D4) and the submission, because the build reads both.
  Zone A's code writes the request, as the producer; Zone A has no identity of its own. Zone B does
  not ask: a run without a record tells its caller `certified-word-drawing-missing`, and the caller
  asks.
- **The request is the submission's reference, nothing else:** `{ uri, sha256 }`, the very
  `submission` of a `RunRequest` (a URI in the submissions bucket, and the canonical-JSON SHA-256
  that Zone B's submission reader checks). It is sent as two attributes of a message on one Pub/Sub
  topic. The producer needs `roles/pubsub.publisher` on that topic and no other new grant.
- **The narrative hashes are carried by reference.** The submission lists each section's
  `narrativeDivSha256` in its provenance, and its source names the .docx and the recompute's
  request. The hash pins all of them, so the build takes them from the submission. Anything else the
  requester said would be one more claim to check.
- **What is drawn:** every section that has a narrative, as the recompute makes it from the stored
  .docx under the submission's own request. That is the set the submission's provenance lists. A
  section with no narrative (an empty heading) is not drawn, as in Zone A.
- **With which stylesheet: none but Chrome's own default**, not the EMA ePI viewer's. Three
  reasons. `zone_a.drawing`'s rules and verdicts are defined that way. The EMA's stylesheet is not
  pinned in this repository, and the EMA can change it. And the renderer gate draws authority labels
  without it too (R2). That the EMA's viewer could draw otherwise is a stated residual, as in R2.
- **How:** each narrative in its own element of one page, parsed as HTML, read in turn, as
  `zone_a.drawing` does today. R2's widths, device pixel ratios and XML mode are not used: nothing
  this check compares depends on them (assumed above).

## 2. The verdict

**What the drawing is compared with:** the label reader's read of the .docx, the very read the
recompute made the narratives from. In the image, one call gives both: `zone_a.recompute` gains a
function that returns its result (the bytes Zone B compares) together with the read behind it.
Its output does not change. Nothing reads the .docx a second time, and nothing comes from the
requester.

**What "agrees" means:** `zone_a.drawing.check`'s rules, exactly, because that code runs:

- **Lines.** Both sides are cut into lines where a paragraph or a line ends (on Chrome's side, also
  where a table cell ends). Spaces, tabs and line feeds collapse to one space. Lines are
  trimmed, and empty lines dropped. A list label the narrative writes as text starts its line. The
  lines must be the same, in the same order, character for character.
- **Marks.** Each character that is not a space carries a set of marks, and the sets must be the
  same, character by character. Chrome's side is read from the style Chrome computed, by the
  reader's own thresholds: bold at weight 600 or more; italic; underline and strike only where their
  colour can be seen on the background; a border; superscript and subscript (by vertical alignment,
  or a shift of one point); faint (contrast under 1.33:1, or smaller than 2 pt); a colour that is
  neither black nor near black; a background that is not near white. On the read's side, an
  underline, and capitals over a character they draw the same, are left out, as the narrative leaves
  them out; the template's grey counts as the silver background the narrative draws.
- **List labels.** The labels Chrome draws, read from its accessibility tree, each with the space
  after it, in order, must equal the read's. A label drawn faint counts as none.
- **Pictures.** Each picture's size as Chrome decoded it must equal the size the reader read from
  its header. A picture Chrome cannot decode counts as 0 by 0, and differs.
- **Not compared,** as in Zone A: a list's indent and nesting (the builder refuses two levels in a
  section), and which line a bullet stands before.

**Tolerances: only those rules** (spaces collapsed, lines trimmed, the marks the narrative leaves
out, a faint label as none); no numeric tolerance. Everything compared is text, sets of mark names,
label strings or whole pixel counts. Nothing is measured on the drawn pixels.

A section **agrees** when all four are equal and Chrome reported no error for it. Otherwise the
record says where, never what: "line 12: text differs at character 40", the names of the marks that
differ, "list markers differ", or the picture rule.

**The code that runs inside the image, and nothing else:**

- `zone_a.drawing`, `word-drawing/1.2.0`: today's rules unchanged, plus an entry point,
  `python -m zone_a.drawing LABEL.docx < REQUEST.json`, that runs the recompute and the check for
  one label and writes the verdicts as canonical JSON;
- `zone_a.recompute` (`recompute/1.2.0`, for the new function) and what it imports: the label
  reader, `zone_a.structure`, `zone_a.word_epi`;
- `label_docx.browser` (`browser-verifier/1.1.0`, unchanged), which writes the two pages and
  drives Chrome;
- chrome-headless-shell 154.0.8037.57, through a one-line launcher that adds `--no-sandbox`, as the
  renderer gate runs it: inside a container with no network, no credentials and a read-only
  workspace;
- Python 3.14.7, installed as the worker image installs it.

The two packages, with the registry and mapping files the recompute reads, come from the build's
own checkout of main. They are not installed in the image, so a code change does not change the
image.

## 3. The record

### Its fields

```text
WordDrawingRecord (canonical JSON, RFC 8785)
  recordVersion  "word-drawing-record/1.0.0"
  environment    "dev" | "validation" | "prod"
  commitSha      the first-parent commit of main the build ran
  submission     { sha256 }                    sha256(submission), the RunRequest's hash
  document       { sha256, byteLength }        the .docx, as the build read it
  recompute      { versions, outputSha256 }    the versions the submission's request names, and the
                                               SHA-256 of the bytes the recompute writes
  drawing        { version, imageDigest, chrome }  "word-drawing/1.2.0"; the image by digest;
                                                   Chrome's version as it reports it
  sections       [{ key, narrativeDivSha256, agrees, where }]  every narrative, in the
                                                               recompute's order
  keyVersion     the version of the key that signs it
```

It is stored as `{ record, signature }`: an RSA-PSS signature over the record's canonical bytes.
It holds hashes, keys and positions only, never a word of the label.

### Who signs it, and with which key

R1's render identity, with its environment's key. Neither exists, so D3 creates both as R1 states
them, and the authority records use them too if they are ever built:

- **the identity** `ema-flow-render-<environment>`, with R1's grants (reader on the renderer image
  repository and the build's staging bucket, read and create on the attestation bucket, signer on
  its key, the repository connection's token, log writer), and one more: **reader on the
  submissions bucket**, for the submission and the .docx. R1's captures bucket and proposal identity
  are not needed for Word;
- **the key** `render-attestation-hsm`, in the evidence key ring: for signing only, in an HSM,
  RSA-PSS 3072 with SHA-256 (the approval key's algorithm, since a record must stay verifiable as
  long as the evidence), `prevent_destroy`, 120 days before any destruction, and watched by the
  existing key alert. Only the render identity may sign with it.

### The determinism rule

The build makes the record twice, in two containers with no network, each with its own Python and
Chrome. The two outputs must be equal, byte for byte, before anything is signed (R1's two
regenerations; R7's rule). If they differ, nothing is signed, the build fails, and the difference is
investigated, never waived. There are no pixel fields, so there is no ε. If a record already sits at
its path (a second request, or a message delivered twice), the build requires it to equal the one
it made, and stops.

### Where it is stored, and why no person commits it

It is stored in a bucket only the render identity may write: the attestation bucket R1 calls for,
made now and named `<project>-<prefix>-render-attestations`, encrypted with the evidence key. The path is
`word/<submission sha256>/<drawing id>/<key version>.json`. The drawing id is the SHA-256 of the
canonical JSON `{ version, imageDigest }` that the build's commit pins in
`src/certified-word/drawing.lock.json`, the file the worker reads too. Writes are create-if-absent.
The worker and the producer may read it.

**R1's rule that a person commits each record does not apply here.** Three reasons:

1. The repository is public. A record describes a company's unpublished label: its hash, its
   section keys, where it failed. That does not belong in a public repository; and `AGENTS.md`
   admits no client content at all until the data-handling paragraph of
   `docs/design/verifiable-answers.md` is written.
2. In R1 the commit adds no trust ("nothing trusts it for having been committed"). Trust comes from
   the render identity running main's code and signing, and that stays.
3. Each import would wait for a pull request, a merge and a deploy.

So Word has no propose mode and no committed store. A person still decides: the approver (D7), who
approves the submission's hash, and whose review can show the record.

### How Zone B verifies it

In the worker, at step 5, with no call to Cloud KMS:

1. It reads the record at its path, for each key version this build pins for its environment and
   has not revoked, highest first.
2. It parses it strictly (canonical bytes, no repeated keys, exactly the fields above) and checks
   the signature against that version's public key, pinned in the worker image
   (`src/render/attestation-keys/<environment>/<version>.pem`, with R1's `revoked` list).
3. It requires that:
   - the environment is this deployment's;
   - the submission hash is this submission's;
   - the document's hash and length are the source's;
   - the recompute's versions are the source's request's, and its output hash is the hash of the
     bytes the worker's own recompute wrote at step 3;
   - the drawing's version and image are the ones this build pins;
   - the sections are exactly the submission's provenance sections, each key with its
     `narrativeDivSha256`;
   - every section agrees.
4. It keeps the record's bytes with the run's evidence, by hash, as R10 does for a record.

For Word, this replaces R1's check at deploy time, in the image build. That check served records
committed to the repository and copied into the image. The worker already reads the .docx from
Cloud Storage at run time (D4), so reading the record there adds no new kind of dependency, and the
signature check itself needs no network.

## 4. The flow, and where trust changes hands

1. **Zone A, at the producer** (Zone B trusts none of it; ADR 0002). It reads the .docx, a person
   confirms the structure, it builds the sections, checks them with the local Chrome (a preview
   only), runs the recompute and the importer, and makes the submission, its page text and its
   report.
2. **Upload.** _Boundary: the submissions bucket._ The producer stores the .docx at
   `uploads/sha256/<hash>.docx`, then the submission and its two parts.
3. **Request.** _Boundary: one Pub/Sub topic; the producer may publish to it and do nothing else._
   It publishes `{ uri, sha256 }`.
4. **The drawing build.** _Boundary: the render identity and its key, running main's code only._
   1. With network: it checks the request's two values against strict patterns (and never pastes
      them into a command line). It reads the submission and the .docx with the
      worker's own readers and rules (the configured bucket, the size caps, the content address,
      the hash and the length). It stops if the record already exists, then pulls the drawing image
      by digest.
   2. With no network: two containers, each running `python -m zone_a.drawing` on the .docx with
      the source's recompute request. The two outputs must be equal.
   3. With network: it checks that the commit is a first-parent commit of main (R1), assembles the
      record, signs it with Cloud KMS and writes it, create-if-absent.
5. **The run.** _Boundary: Workflows calls the worker with the `RunRequest`, as today._
6. **Zone B's gate,** under the worker's identity: steps 1 to 4 as built (the importer's version,
   the .docx's bytes, the recompute, the importer again); step 5, the record; step 6, the ordinary
   gate with that proof bound to the submission's hash.
7. **Approval and persistence,** once P5 binds the document id (section 5): the approval as
   `approval.md` has it (today an attestation; the signed statement once enforced), then the
   transform, validation, the FHIR store, the run manifest and the evidence.

**What a compromised Zone A, or producer, could do:**

- make Zone B refuse its own submissions;
- spend build minutes by publishing requests. A request that does not name a stored certified Word
  submission, whose .docx has the pinned hash, is refused within the first step, and a request for
  a submission already drawn stops after the reads. A budget alert is the backstop (a residual);
- send a label built to attack Chrome's picture decoders, or the reader, inside the drawing
  containers. Both drawings get the same input, so one exploit could make both say "agrees". The
  containers have no network and no credentials, and the narratives are written by
  `zone_a.word_epi`, not by the producer. This is R6's residual (Chrome without its sandbox);
- what it can do today without D3, it still can: assert an approval, which is an attestation
  until `approval.md`'s signed approvals are enforced.

**What it could not do:**

- sign a record, or write to the record bucket: neither grant is its own;
- have anything drawn but the recompute of the stored .docx under the submission's own request: the
  build reads both itself and takes nothing else from the request;
- reuse a record for another submission, environment, .docx, recompute, drawing version or image:
  Zone B checks each;
- have a narrative persisted that Chrome draws otherwise than the read: the record says so, and
  Zone B refuses;
- change the code that draws: it runs from main, under the render identity. R1's trust root stands:
  whoever can change main, the build configuration, the trigger or the pinned keys (the owners and
  deployers) is trusted, as R1 states.

## 5. The gate change

### Step 5

After step 4 passes, in every run the worker recomputes, the gate verifies the record as in
section 3. It refuses with these closed codes, each of which the HTTP caller learns, as it learns the
three existing ones:

- `certified-word-drawing-missing`: no record for this submission and this build's drawing (the
  existing code, now meaning just that);
- `certified-word-drawing-invalid`: bytes that are not a canonical record of the right shape, or a
  signature that does not verify against a key version this build pins for its environment and has
  not revoked;
- `certified-word-drawing-mismatch`: a signed record whose environment, submission, .docx,
  recompute or sections are not this submission's;
- `certified-word-drawing-failed`: a section Chrome drew otherwise than the read.

A dry run is refused for any of these but `missing`. Without a record it passes as today
(`certifiedWordCheck: "recomputed"`); with a verified one it says `"drawn"`.

### Step 6, and what the run manifest records (a contract change, under change control)

- The ordinary gate accepts a certified Word submission in a run that is not dry only with
  `GateOptions.certifiedWordDrawn = { submissionSha256, recordSha256 }`, which only
  `src/certified-word/gate.ts` sets, after step 5 (as only the authority gate sets
  `recomputedImport`).
- **D3 alone does not let a run persist.** `certified-word-import.md` keeps the dry-run rule until
  P5 binds the ePI's document id to its product ("Open: the document id is bound to nothing").
  Until then, a run that is not dry and passes step 5 is refused with one more closed code,
  `certified-word-document-unbound`, and the gate sets `certifiedWordDrawn` only once P5's check
  passes.
- The run manifest's `IngestionEvidence` gains
  `certifiedWord: { recomputeSha256, drawing: { recordSha256, keyVersion, version, imageDigest } }`,
  present exactly when `sourceKind` is `certified-word`, a rule like the one `authority` has.
- That is a major under ADR 0002's rule (a block required for a source kind Zone B branches on): run
  manifest **7.0.0**, with 6.0.0 frozen and still readable, a change record, and
  `npm run contracts:lock -- --record <change record>`. The renderer gate's reserved run-manifest
  major moves to the one after 7.0.0.
- `CanonicalSubmission` and `ingestion-provenance` do not change. The record names the submission,
  so the submission cannot name the record. D1 had planned to put the record in the source; that
  would be circular.
- `src/certified-word/` changes, so the importer's version and lock change (its directory holds the
  gate), and so do the recompute's fixtures, for `recompute/1.2.0`.

## 6. Cost and operations

**Measured** on this Mac (Apple M2, Google Chrome 154.0.8037.98, one process at a time), on the
EMA's published Word SmPCs (`label-docx-reader-scratch/ema-pi-tc/en-smpc`, 296 files, a tracked
label by its accepted view; counts and times only, no text kept). 130 SmPCs build with no heading
left for a person to assign (an Annex I can hold several); 11 of them carry every section, which
the recompute requires.

- **The largest SmPC that carries every section** (28 narratives, 260,247 characters of narrative
  markup, 712 paragraphs): one `zone_a.drawing.check` took 4.0 s the first time and 1.8 s
  the second. Reading and building it took 0.9 s.
- The next two largest (27 narratives each): 1.8 to 2.1 s per check.
- The largest of those that do not carry every section (26 narratives, 1,711 paragraphs): 1.9 to
  2.6 s per check; reading and building took 11 s.
- The slowest single read among the files that build (2,280 paragraphs, five SmPCs, by its
  accepted view): 40.1 s.

Each check starts Chrome twice (once for the text, once for the list labels). The build runs two
checks, side by side.

**Estimated, to be measured on the first build:** about 2 to 4 build-minutes per label. Most of it
is fixed: fetching main's source, `npm ci`, pulling the image and signing. Each drawing container
runs a read and a check: under 5 s for the largest carried SmPC here, about 45 s for the slowest
read. Cloud Build's default machine is likely slower per core than an M2; by how much is not
measured. The build's timeout is set from that first measurement, as R11 sets the authority
build's. The 40 s read is also closer to the worker's 60 s recompute limit (D2) than D2's 4.1 s
figure suggests, so the first deploy's measurement of the recompute should include that label.

**Operations:**

- From request to record: a few minutes, once per submission. A refused run says
  `certified-word-drawing-missing` until then; the producer requests, then runs again.
- A merge that changes the drawing's version or image: a worker built after it looks for records
  under its own drawing id, so it does not find older ones (`missing`), and the producer asks again.
  Between such a merge and its deploy, the build (main's code) and the worker (the deployed code)
  can differ; the request is repeated after the deploy. A merge that changes the recompute's
  versions already refuses older submissions at step 3, and the build's recompute refuses them too.
- The drawing image is built only when its pins change, by R1's renderer image build
  (`cloudbuild.renderer-image.yaml`, with its own identity and writer on `renderer-images` only),
  and its digest is then locked in a pull request.
- Key rotation and revocation are R1's: a new key version, its public key pinned in a pull request;
  a revoked version listed in `revoked`; records requested again.
- New infrastructure, all in Terraform: the render identity, the renderer image build's identity,
  the HSM key, the attestation bucket, the `renderer-images` repository, one Pub/Sub topic, two
  Cloud Build triggers (one per image change, one per request), and the worker's read on the
  attestation bucket. The repository connection to GitHub is a step only the owner can take.

## 7. Alternatives within (a)

1. **Recommended: `zone_a.drawing` itself, in Python, in a second target of the renderer image.**
   `Dockerfile.renderer` gains a target, `word-drawing`: the `renderer` target unchanged, plus
   Python 3.14.7 and the launcher. The rules are Zone A's by construction, with one version for
   both. `browser.py` does not change, so the label reader needs no new mutation run. The cost:
   Python joins the image that runs Chrome; and `Dockerfile.renderer` changes, which R9's lock
   would count as a new gate version if it is ever built (no record exists, so nothing would be
   redrawn).
2. **A TypeScript port in `src/render/`, in the renderer image as it is.** The recompute writes the
   read's lines into its output, and a TypeScript judge draws and compares, as R1's judge reads T's
   outputs without loading T's code. Python stays out of the Chrome image. But the rules would
   exist twice, in two languages, to be held equal forever by a differential test; the recompute's
   output grows; and the importer's input changes. Against a 100% bar, two copies of a rule are a
   risk with no gain in what is proved.
3. **The submission names the record, not the reverse** (as D1 first planned): the record keyed
   by the .docx and the recompute's request, and its hash in the source. The approver's content
   hash would then cover the drawing, and one record would serve every submission of the same label
   version. But Zone A could not finish a submission until the record came back (a round trip
   inside its flow), and `CanonicalSubmission` would take another major. Keyed by the submission,
   as this note has it, the record needs no change to the submission's contract.
4. **Zone B asks for the drawing itself** when the record is missing. One step fewer for the
   producer, but the worker gains a publish grant, and every refused run would start a build. Not
   recommended.
5. **Check records at deploy time, as R1 does,** copying them into the worker image. A pure lookup
   at run time, but every import would wait for a deploy, and with the records out of the
   repository there is nothing reviewed to copy. Not recommended.

## 8. What the owner is asked

1. **Approve the new infrastructure, and make the repository connection.** It is listed in
   section 6. Its deploy creates an HSM key that Terraform cannot destroy and whose destruction
   waits 120 days. The GitHub connection to Cloud Build in `europe-west4` is a step only the owner
   can take; R1 needs it too.
2. **May the render identity read the submissions bucket?** It would be the first identity besides
   the worker to read companies' uploaded labels. It runs only main's code, and reads in a step
   with network but without Chrome.
3. **The recommendation:** `zone_a.drawing` itself, in Python, in a second image target (section 7,
   alternative 1), rather than a TypeScript port.

## What this changes in R1, and what it does not

**Kept from R1:** the render identity, running main's code only, with the first-parent `commitSha`
check; a Cloud Build trigger bound to the repository, under that identity; two drawings in separate containers with no
network before anything is signed; a KMS key per environment, its public keys pinned in the
repository with a `revoked` list; create-if-absent writes to a bucket only the render identity may
write; the renderer image (Chrome and fonts) pinned by digest from `renderer-images`; R1's trust
root, unchanged.

**Different, for Word only:**

- the build takes a request, which is data, and makes the record; it does not regenerate a
  committed one;
- no person commits the record, and there is no propose mode (section 3);
- the worker verifies the record at run time; the image build does not, at deploy;
- the drawing image is a second target of `Dockerfile.renderer` with Python added;
- no captures: no person acknowledges anything, and the verdict is exact;
- the render identity also reads the submissions bucket;
- R2's widths, ratios and modes, R3's model, R4's geometry and R8's record shape are not used;
  `zone_a.drawing`'s check is.

**Not touched:** R2 to R11 for authority imports. Their dropped parts (3c-C3 to C5) stay dropped.
The parts D3 builds (the identity, the key, the bucket, the image repository and its build) are
the ones those parts would need.

## Delivery, each change reviewed on its own

1. **The drawing, in code, no cloud:** `recompute/1.2.0`'s function; `zone_a.drawing`'s entry point
   (`word-drawing/1.2.0`); the `word-drawing` image target and its launcher. CI draws the five
   Word-made fixtures in that image and requires the recorded verdicts; a run on the EMA corpus,
   kept local, requires the same verdicts as Google Chrome on macOS. This proves or refutes the
   assumptions above.
2. **The infrastructure:** section 6's list, after the owner's answers. The owner then makes the
   repository connection, and the renderer image build pushes the first image.
3. **The pins:** the image's digest and the key's first public key
   (`src/certified-word/drawing.lock.json`, `src/render/attestation-keys/<environment>/`).
4. **The gate:** step 5 and step 6, `cloudbuild.word-drawing.yaml`, run manifest 7.0.0, and ADR
   0002's invariant 11 restated with step 5's codes; then the first record on a synthetic label in
   dev, checked by a dry run (`"drawn"`). A run that persists one waits for P5.

## Stated residuals

- The record proves what Chrome's default stylesheet draws. The EMA viewer applies its own, which
  could draw a list label, a colour or a background otherwise (R2's residual).
- One Chrome build is the reference: the pinned chrome-headless-shell on Linux, not every browser
  a reader uses.
- An exploit in the drawing containers could forge both drawings of one build (R6's residual).
- A flood of valid requests costs build minutes; the backstop is a budget alert.
- The record does not vouch for the product, the document id (P5) or the approval: only that each
  narrative draws as the .docx was read.
