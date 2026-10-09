# The drawing record: Zone B's proof that Chrome draws a certified Word label as it was read (ADR 0006 P4, D3)

- Status: proposed 2026-10-07; revised the same day after an independent review of #197, whose
  recommendations the owner said to go ahead on. Build order step 1 partly measured and PR 1
  built the same day ("Step 1: measured, and PR 1 as built", below); PR 2 built and step 1's
  remainder measured the same day ("Step 2: PR 2 as built, and measured", below); PR 3 built the
  same day, pinning dev's image and key ("Step 3: PR 3 as built", below); PR 4, the gate's step 5,
  built the same day ("Step 4: PR 4 as built", below); step 6 waits for P5
- Implements: `docs/design/certified-word-import.md` D3 (a), the owner's choice of 2026-10-06
  ("extend the renderer gate's attested records"), and that note's "The gate", steps 5 and 6
- Fits: `docs/design/authority-import-renderer.md` R1 (attested records), frozen, its architecture
  owner-approved. It follows R1's pattern with a key, identity and bucket of its own, and changes
  nothing for an authority import ("What this takes from R1", below)
- Related: ADR 0002 (invariant 11), ADR 0005's amendment of 2026-09-25, ADR 0006 decision 1,
  `zone-a/src/zone_a/drawing.py`, `label-docx-reader/src/label_docx/browser.py`,
  `src/certified-word/gate.ts`, `src/approval/statement.ts`, `docs/design/pl-structure.md`

## Summary

As soon as it has uploaded a .docx and a person has confirmed its structure, the producer asks for
a drawing of that .docx under that recompute request. A build of main's code, under an identity
used for nothing else, remakes the sections with `zone_a.recompute` and checks them by
`zone_a.drawing`'s rules, twice, in a pinned Chrome-and-Python image with no network. Only if both
runs are identical and every section agrees does it sign a record, with a key used for nothing
else. The record holds hashes computed in the container: the .docx, the recompute's output and
each narrative drawn. Zone B's gate (step 5) finds it from the submission's .docx and request,
checks the signature against a key pinned in the worker image, and requires every hash to match
its own. The same record serves every submission of that label version, SmPC or leaflet.

## What exists, and what is assumed

**Verified on 2026-10-07** (main at 4030270; the dev project, read with `gcloud` only):

- The renderer image (`Dockerfile.renderer`: chrome-headless-shell 154.0.8037.57 and the pinned
  fonts) is built and checked in CI's Renderer job only, in about 40 s (three runs). It is pushed
  nowhere: dev's one image repository, `ema-flow-images`, holds the worker, query, signer and
  validator images, and has no cleanup policy.
- R1's signing half was never built: the renderer note's Delivery dropped it on 2026-09-29 (3c-C3
  to C5). Dev has no render identity, no signing key for records, no attestation bucket, no Cloud
  Build trigger at all, and no repository connection in `europe-west4`. So D3 (a)'s "Reused: the
  image, the identity, the key, the verification" holds for the image only.
- `zone_a.drawing` (`word-drawing/1.1.2`) draws with whatever Chrome is installed, through
  `label_docx.browser` (`browser-verifier/1.1.0`), with Chrome's own default stylesheet. The
  recording its tests replay was made with Google Chrome 154.0.8037.98.
- The recompute's output holds each section's narrative but not the read it was made from (each
  paragraph's text, marks, list label and pictures), which the comparison needs.
- Since #198 the worker carries the package leaflet (`document: "pl"`), in dry runs only.
- `src/certified-word/gate.ts`, in a worker that can recompute, refuses at step 5 every run that is
  not a dry run, with `certified-word-drawing-missing`.
- The GitHub repository is public.

**Assumed, each to be measured or tested before it is relied on** (build order, step 1 or the
change named):

- chrome-headless-shell runs `label_docx.browser`'s two pages (one read with `--dump-dom`, one over
  the DevTools pipe) as Google Chrome does, and gives the same verdicts on the fixtures and on the
  EMA's SmPCs and leaflets. (Measured: the same raw answers on every SmPC and leaflet that builds,
  with the shell's macOS build; the Linux image on the fixtures. "Step 1", below.)
- What the check compares does not depend on the window's width or on the fonts: it reads text by
  block, styles as Chrome computes them, list labels and picture sizes, never glyph positions.
  (Measured: the width and the device pixel ratio on the corpus; the fonts on the fixtures only.)
- Cloud Build, for a trigger fired by a Pub/Sub message (PR 2): it runs the build as the trigger's
  own service account without the publisher holding `actAs` on it; it binds a field of the
  message's JSON body to a substitution; its CEL filter can refuse a message before any build
  starts; and it accepts an inline build with no repository connection. (Tested, each of them:
  "Step 2", below.)
- Every timing not measured below ("Cost and operations"). (Measured on Cloud Build's machines:
  "Step 2".)

## The trust model, plainly

Zone B trusts three things: main's code (whoever can change main can change everything, as R1
says), the signature of the drawing identity, checked against a public key pinned in the worker
image, and what the worker reads and recomputes itself. It trusts nothing the producer says.

**Today the producer is the operator:** the person who runs `scripts/demo/seed.ts` under their own
Google account, which holds the owner role on dev (checked 2026-10-07). That person is already
inside the trust root, so nothing here protects against them. The design is for the day the
producer is a separate service the owners do not fully trust (the label gateway). The checks below
are built to hold then.

## 1. The request

- **Who asks: the producer**, the identity that uploads the .docx (today the operator; later the
  label gateway). Zone A's code writes the request; Zone A has no identity of its own. Zone B does
  not ask: a run without a record tells its caller `certified-word-drawing-missing`, and the
  caller asks.
- **When:** as soon as the .docx is at its content address in the submissions bucket (D4) and a
  person has confirmed the structure. The submission does not need to exist yet.
- **What it carries:** one value, the drawing request:

  ```text
  DrawingRequest (canonical JSON, RFC 8785)
    docxSha256   the .docx's SHA-256, lower-case hex: it names the upload uploads/sha256/<hash>.docx
    recompute    { document, view, part, assignments, versions }, exactly the submission's
                 sourceDocument.recompute: "smpc" or "pl", the view a person named, which part,
                 the headings a person assigned, and every version the recompute names
  ```

  It travels in the body of one Pub/Sub message, as the field `request`: the base64url form of its
  canonical bytes. The **record's key** is the SHA-256 of those canonical bytes. The producer
  needs `roles/pubsub.publisher` on that topic, and no other new grant.

- **No narrative hash, no submission hash.** The build makes the narratives itself from the
  .docx, so it hashes them itself. A record serves every submission whose .docx and request are
  these, which is right, since nothing else in a submission is drawn.
- **What is drawn:** every section with a narrative, as the recompute makes it from the .docx under
  the request: an SmPC's or a leaflet's. A section with no narrative (an empty heading) is not
  drawn, as in Zone A.
- **With which stylesheet: none but Chrome's own default**, not the EMA ePI viewer's. Three
  reasons. `zone_a.drawing`'s rules and verdicts are defined that way. The EMA's stylesheet is not
  pinned in this repository, and the EMA can change it. And the renderer gate draws authority labels
  without it too (R2). That the EMA's viewer could draw otherwise is a stated residual, as in R2.
- **How:** each narrative in its own element of one page, parsed as HTML, read in turn, as
  `zone_a.drawing` does today. R2's widths, device pixel ratios and XML mode are not used: nothing
  this check compares depends on them (assumed above). The narrative is persisted as XHTML, so PR 1
  tests that Chrome's HTML parser and its XML parser make the same tree of every narrative drawn
  (the same elements, attributes and text, in order, but for a `tbody` the HTML parser inserts).

## 2. The verdict

**What the drawing is compared with:** the label reader's read of the .docx, the very read the
recompute made the narratives from. In the image, one call gives both: `zone_a.recompute` gains a
function that returns its result (the bytes Zone B compares) together with the read behind it. Its
output does not change. Nothing reads the .docx a second time.

**What "agrees" means:** `zone_a.drawing.check`'s rules, exactly, because that code runs:

- **Lines.** Both sides are cut into lines where a paragraph or a line ends (on Chrome's side, also
  where a table cell ends). Spaces, tabs and line feeds collapse to one space. Lines are trimmed,
  and empty lines dropped. A list label the narrative writes as text starts its line. The lines
  must be the same, in the same order, character for character.
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

A section agrees when all four are equal and Chrome reported no error for it. **Only a drawing in
which every section agrees is signed.** One that differs signs nothing: the build fails, and its log
says where (a line and character, the names of the marks, "list markers differ" or the picture
rule), never what. Zone A's preview shows the producer the same.

**The code that runs inside the image, and nothing else:**

- `zone_a.drawing`, `word-drawing/1.2.0`: today's rules unchanged, plus an entry point,
  `python -m zone_a.drawing LABEL.docx < REQUEST.json`, that hashes the .docx it opens and requires
  the request's `docxSha256`, runs the recompute and the check, and writes the record's fields
  (section 3) as canonical JSON. It exits non-zero, with nothing on standard output, on a refusal
  of the recompute, a browser failure (no Chrome, a crash, a timeout, a page error) or a section
  that differs; standard error then gives the closed code or the place, never the text;
- `zone_a.recompute` (`recompute/1.2.0`, for the new function) and what it imports: the label
  reader, `zone_a.structure`, `zone_a.leaflet`, `zone_a.word_epi`;
- `label_docx.browser` (`browser-verifier/1.1.0`, unchanged), which writes the two pages and
  drives Chrome;
- chrome-headless-shell 154.0.8037.57, through a one-line launcher. PR 1 tries Chrome's own
  sandbox inside the hardened container (section 4); if it cannot start there, the launcher adds
  `--no-sandbox`, as the renderer gate runs it, and the residual is recorded. (Measured: it cannot
  start there, so the launcher, `src/render/image/word-drawing-chrome.sh`, adds `--no-sandbox`;
  "Step 1", below.)
- Python 3.14.7, installed as the worker image installs it (as built: by uv 0.12.17, pinned by
  digest, in a stage built on the renderer's, since that image installs nothing from Debian outside
  its dated snapshot and has the certificates uv needs).

The two packages, with the registry and mapping files the recompute reads, come from the build's
checkout of main. They are not installed in the image, so a code change does not change the image.

## 3. The record

### Its fields

```text
WordDrawingRecord (canonical JSON, RFC 8785)
  recordVersion  "word-drawing-record/1.0.0"
  environment    "dev" | "validation" | "prod"
  commitSha      the first-parent commit of main the build ran
  request        the DrawingRequest, as decoded; its SHA-256 is the record's key
  document       { sha256, byteLength }       of the .docx bytes the container opened
  recompute      { outputSha256 }             of the bytes the recompute's result serialises to,
                                              exactly what `python -m zone_a.recompute` writes
  drawing        { version, chrome, imageDigest }   "word-drawing/1.2.0"; Chrome's version as it
                                                    reports it; the image by digest
  sections       [{ key, narrativeDivSha256 }]      every narrative drawn, in the recompute's order
  keyVersion     the version of the key that signs it
```

**Every hash is computed in the container**, from the exact bytes or string it used:
`narrativeDivSha256` is the SHA-256 of the UTF-8 bytes of the very string handed to Chrome, as the
importer computes the submission's (`sha256Utf8(narrative.div)`, `src/certified-word/import.ts`).
`imageDigest` alone is added outside, by the signing step, from the lock it pulled the image by.
Nothing comes from the requester but the request, which is the key.

It is stored as `{ record, signatureBase64 }`: an RSA-PSS signature over the record's canonical
bytes, written as a signed approval statement writes its own. Being signed means every listed
section agrees. It holds hashes, keys and versions, never a word of the label.

### Who signs it, and with which key

An identity and a key used for Word drawings and nothing else, not shared with R1's authority
records (which, if ever built, get their own):

- **the identity** `ema-flow-word-drawing-<environment>`. Its grants, proven exhaustive by a
  Terraform test as the signer's are (`test/infra/signer-identity.test.ts`):
  - `roles/storage.objectViewer` on the submissions bucket, under a condition: objects under
    `uploads/sha256/` only, so it never reads a submission, a page text or a report;
  - `roles/storage.objectCreator` and `roles/storage.objectViewer` on the record bucket, never
    `objectAdmin`: overwriting an object needs the delete permission, which neither role has, so
    IAM itself enforces create-if-absent;
  - `roles/cloudkms.signer` on its key, not `signerVerifier`;
  - reader on the repository the image is pulled from, and log writer.
- **the key** `word-drawing-hsm`, in the evidence key ring: for signing only, in an HSM, RSA-PSS
  3072 with SHA-256 (the approval key's algorithm, since a record must stay verifiable as long as
  the evidence), `prevent_destroy`, 120 days before any destruction, and watched by the existing
  key alert, which fires on any key's destruction request, disabled version or grant change.

### Determinism, and the signing rules

- The build makes the record's fields twice, in two containers, each with its own Python and
  Chrome. The two outputs must be equal, byte for byte, before anything is signed (R1's two
  regenerations; R7's rule). If they differ, nothing is signed, the build fails, and the
  difference is investigated, never waived. There are no pixel fields, so there is no ε.
- A browser failure, a recompute refusal or a section that differs ends the container with a
  non-zero status, and nothing is signed.
- A request for a record that already exists ends at the build's first step. If two builds of one
  request run at once (a message delivered twice), the second's create-if-absent write finds the
  first's record. RSA-PSS signatures are randomised, so two signatures of one record differ: the
  build compares the stored record's canonical bytes with its own, never the signatures. If the
  records differ, the build fails as a determinism failure. (As built, "Step 2": the stored object
  must also carry a signature that verifies with the pinned key, and the records may differ in
  their commit alone, since two builds of one request can run on either side of a merge.)

### Where it is stored, and why no person commits it

In a bucket where Terraform lets the drawing identity alone write:
`<project>-<prefix>-word-drawings`, encrypted with the evidence key. At project level others can
write it too (checked in dev on 2026-10-07): the owner, `ema-flow-deployer`
(`roles/storage.admin`), and Cloud Build's legacy service account
`<project number>@cloudbuild.gserviceaccount.com` (`roles/cloudbuild.builds.builder`, which writes
every bucket and publishes to every topic). The owner can also sign with the key, and the deployer
could grant itself that; both are the trust root already (section 4). The legacy account cannot
sign, so what it, or anyone without the signer grant, puts at a record's path is not a record: the
build verifies any object it finds there and fails on one that is not ("Step 2"), and the gate
refuses it. What it can do is deny that label its record (a stated residual).

The path is `word/<key>/<drawing id>/<key version>.json`. The drawing id is the SHA-256 of the
canonical JSON `{ version, imageDigest }` that the build's commit pins in
`src/render/word-drawing/lock.json`, outside `src/certified-word/`, so pinning a new image does not
change the importer's version. The worker may read the bucket. The producer may not: Zone A's
preview already tells it where a drawing differs, and the run's answer tells it whether a record
was found.

**R1's rule that a person commits each record does not apply here.** Three reasons:

1. The repository is public. A record describes a company's unpublished label: its hash, its
   section keys, its versions. That does not belong in a public repository; and `AGENTS.md`
   admits no client content at all until the data-handling paragraph of
   `docs/design/verifiable-answers.md` is written.
2. In R1 the commit adds no trust ("nothing trusts it for having been committed"). Trust comes from
   the identity running main's code and signing, and that stays.
3. Each import would wait for a pull request, a merge and a deploy.

So Word has no propose mode and no committed store. A person still decides: the approver (D7),
who approves the submission, and whose review can show the record.

### How Zone B verifies it

In the worker, at step 5, after steps 1 to 4, with no call to Cloud KMS:

1. **The key.** It computes the SHA-256 of the canonical JSON of
   `{ docxSha256: source.document.sha256, recompute: source.recompute }`.
2. **The read.** For each key version this build pins for its environment and has not revoked,
   highest first, it reads `word/<key>/<drawing id>/<version>.json`. The first object it finds
   decides: one that fails below refuses the run, and the gate never steps over it to a lower
   version. None found is `missing`. Any Storage error but not-found fails the run, as the D4 read
   does; it is never read as `missing`.
3. **The bytes.** Their size is capped (64 KiB) before anything is parsed. They must be the
   canonical JSON of exactly the shape above, with no repeated key. The record's `keyVersion` must
   be the version in its path, and the SHA-256 of its `request` must be the key in its path.
4. **The signature**, against that version's public key, pinned in the worker image
   (`src/render/word-drawing/keys/<environment>/<version>.pem`, with a `revoked` list), by
   `src/approval/statement.ts`'s check, factored out so both call one function: a 3072-bit RSA key,
   base64 written one way only, RSA-PSS with SHA-256 and a 32-byte salt over the canonical bytes.
5. **The fields.** The environment is this deployment's. The request is the source's .docx hash and
   recompute request. The document's hash and length are the source's. `outputSha256` is the
   SHA-256 of the bytes the worker's own recompute wrote at step 3. The drawing's version and image
   are the ones this build pins. And `sections` equals the submission's provenance `sections`, in
   order, each `key` the `sourceKey` and the same `narrativeDivSha256`. Both lists are in the
   mapping tree's order, a parent before its children: the importer holds the recompute's sections
   to it (`placeSections`), and the provenance walks the Composition the same way.
6. **The evidence.** It keeps the record's bytes with the run's evidence, by hash, as R10 does for
   a record.

For Word, this replaces R1's check at deploy time, in the image build. That check served records
committed to the repository and copied into the image. The worker already reads the .docx from Cloud
Storage at run time (D4), so reading the record there adds no new kind of dependency, and the
signature check itself needs no network.

### Why this key is safe

The record says: _the .docx with this hash, recomputed under this request by the code at this
commit, gives output with this hash, whose narratives (these hashes) Chrome in this image draws as
the read, twice, identically._

Zone B accepts a submission only if:

- the submission's source pins that .docx, and the worker read those bytes itself;
- its request is that request;
- the worker's own recompute of them gives that output hash;
- the submission is exactly what the worker's importer makes of that output (step 4);
- its narratives have those hashes, in that order.

So every narrative it would persist is a string the record says Chrome draws as read. Nothing else
in a submission (the product, the document id, the approval, the run's fields) is drawn, so nothing
else needs binding. Two submissions of one label version share one record, rightly.

What the requester chooses is the key itself. A request for the same .docx under another request
makes another record, at another key, which no submission with the first request ever finds. The
build fetches the .docx by its content address and checks its hash. It takes nothing else from the
requester. The circularity of the first draft, a record naming a submission that would want to name
it, is gone: neither names the other.

## 4. The flow, and where trust changes hands

1. **Zone A, at the producer** (Zone B trusts none of it; ADR 0002). It reads the .docx, a person
   confirms the structure, it builds the sections and previews their drawing with the local Chrome.
2. **Upload.** _Boundary: the submissions bucket._ The producer stores the .docx at
   `uploads/sha256/<hash>.docx`.
3. **Request.** _Boundary: one Pub/Sub topic; the producer may publish to it and do nothing else._
   It publishes the drawing request. It can then make the submission, its page text and its report
   meanwhile.
4. **The drawing build.** _Boundary: the drawing identity and its key, running main's code only._
   Below, "The trigger and the build".
5. **The run.** _Boundary: Workflows calls the worker with the `RunRequest`, as today._
6. **Zone B's gate,** under the worker's identity: steps 1 to 4 as built (the importer's version,
   the .docx's bytes, the recompute, the importer again), then step 5, the record.
7. **Persistence waits for P5** (section 5). Then: the approval as `approval.md` has it, the
   transform, validation, the FHIR store, the run manifest and the evidence. A leaflet also waits,
   since #198, until the query service and the signer read one.

### The trigger and the build

- **The trigger.** It is fired by a message on the topic. Its source is `refs/heads/main` and its
  build configuration a fixed path (or inline), both literals in Terraform, which a Terraform test
  holds. A CEL filter on the trigger lets only a message whose `request` is base64url of a bounded
  length start a build, so a malformed one never does. The build's `queueTtl` is short (minutes),
  so a backlog expires rather than runs late.
- **Values only through `env:`.** The request reaches each step as an environment value, never in
  a step's arguments or script text.
- **Step 1, slim** (a pinned Cloud SDK image, before the image or the .docx is downloaded): it
  fetches main's source, decodes the request, computes the key, and looks for the record under the
  newest pinned key version. If it is there, every later step ends at once.
- **Step 2,** with network: it parses the request strictly (canonical JSON, the recompute
  request's own shape) and reads the .docx by the worker's rules (the configured bucket, the
  content address, the 32 MiB cap, the hash). It pulls the image by digest.
- **Step 3,** with no network: two containers, each running `python -m zone_a.drawing`. Each is
  hardened as `scripts/render/run.mjs` hardens the renderer's: `--network none`, a read-only root,
  a tmpfs `/tmp`, `--cap-drop=ALL`, `no-new-privileges`, the image's non-root user, and bounded
  processes and memory.
- **Step 4,** with network: it requires the two outputs to be identical, checks that the commit is
  a first-parent commit of main (R1), adds the environment, commit, image digest and key version,
  signs with Cloud KMS, and writes create-if-absent.

As built (PR 2), these are the steps `exists`, `pull`, `parse`, `fetch`, `draw-1` and `draw-2` at
once, `record` and `sign` of `scripts/word-drawing/build.sh` ("Step 2", below).

**What a compromised Zone A, or producer, could do:**

- make Zone B refuse its own submissions;
- spend build minutes by publishing well-formed requests. A request for a record that exists stops
  at step 1; one whose .docx is not uploaded stops at step 2. A budget alert is the backstop (a
  residual);
- send a label built to attack Chrome's picture decoders, or the reader, inside the drawing
  containers. Both drawings get the same input, so one exploit in the container could make both
  outputs agree falsely. An escape from the container as well reaches the build machine, its
  metadata server, and so the drawing identity's token, which can sign a false record and read
  every uploaded label. This is the residual of running Chrome on producer-supplied bytes; the
  hardening above is what stands in the way (Chrome's own sandbox could not start on CI's host,
  and the launcher runs Chrome without it: "Step 1");
- what it can do today without D3, it still can: assert an approval, which is an attestation until
  `approval.md`'s signed approvals are enforced.

**What it could not do,** short of that escape:

- sign a record, or write to the record bucket: neither grant is its own;
- read a record, or any other company's upload: no grant;
- have anything drawn but the recompute of the uploaded .docx under the request: the build reads
  the .docx itself, by its hash;
- have a record serve a submission with another .docx, request, output, drawing version or image,
  or another environment: Zone B checks each;
- have a narrative persisted that Chrome draws otherwise than the read: such a drawing is never
  signed;
- change the code that draws: it runs from main. Whoever can change main, the trigger, its
  configuration or the pinned keys (the owners and deployers) is the trust root, as R1 states.

## 5. The gate change

### Step 5

After step 4 passes, in every run the worker recomputes, the gate looks for the record and verifies
it as in section 3. Its closed codes, each of which the HTTP caller learns, as it learns the three
existing ones:

- `certified-word-drawing-missing`: no record at any pinned key version's path (the existing code,
  now meaning just that);
- `certified-word-drawing-invalid`: bytes over the cap, not a canonical record of the shape, a
  `keyVersion` or request not those of its path, or a signature that does not verify;
- `certified-word-drawing-mismatch`: a signed record whose environment, request, .docx, output,
  drawing or sections are not this submission's.

There is no `failed`: a record is signed only when every section agrees.

**Until P5.** `certified-word-import.md` keeps every run dry until P5 binds the ePI's document id
to its product ("Open: the document id is bound to nothing"). So until then:

- a dry run is refused for `invalid` or `mismatch`; with no record it answers as today; with a
  verified one its closed field `certifiedWordCheck` says `"drawn"`, beside today's `"recomputed"`
  and `"submission-only"`;
- a run that is not dry is still refused: `missing`, `invalid` or `mismatch` where they apply, and,
  once the record verifies, `certified-word-document-unbound`, one more closed code.

### Step 6, with P5 (a contract change, under change control)

Built with P5's binding of the document id, not before, since only then can a run persist:

- The ordinary gate accepts a certified Word submission in a run that is not dry only with
  `GateOptions.certifiedWordDrawn = { submissionSha256, recordSha256 }`, which only
  `src/certified-word/gate.ts` sets, after step 5 and P5's check (as only the authority gate sets
  `recomputedImport`).
- The run manifest's `IngestionEvidence` gains
  `certifiedWord: { recomputeSha256, drawing: { recordSha256, keyVersion, version, imageDigest } }`,
  present exactly when `sourceKind` is `certified-word`, a rule like the one `authority` has. That
  is a major under ADR 0002's rule (a block required for a source kind Zone B branches on): run
  manifest **7.0.0**, with 6.0.0 frozen and still readable, a change record, and
  `npm run contracts:lock -- --record <change record>`. The renderer gate's reserved run-manifest
  major moves to the one after 7.0.0.
- `CanonicalSubmission` and `ingestion-provenance` do not change: the record and the submission do
  not name each other.

## 6. Cost and operations

**Measured** on this Mac (Apple M2, Google Chrome 154.0.8037.98, one process at a time), on the
EMA's published Word product information, cut into SmPCs and leaflets
(`label-docx-reader-scratch/ema-pi-tc/en-smpc` and `en-pl`; a tracked label by its accepted view;
counts and times only, no text kept). "Builds" means with no heading left for a person to assign.

- **SmPCs:** 296 files; 130 SmPCs build (an Annex I can hold several), and 11 of them carry every
  section, which the recompute requires.
  - The largest of the 11 (28 narratives, 260,247 characters of narrative markup, 712
    paragraphs): one `zone_a.drawing.check` took 4.0 s the first time and 1.8 s the second; reading
    and building it, 0.9 s.
  - The next two (27 narratives each): 1.8 to 2.1 s per check.
  - A larger file that does not carry every section (26 narratives, 1,711 paragraphs): 1.9 to
    2.6 s per check; reading and building it, 11 s.
  - The slowest single read among the files that build (2,280 paragraphs, five SmPCs, by its
    accepted view): 40.1 s.
- **Leaflets:** 286 files; 110 leaflets build, and 12 of them carry every section.
  - The three largest of the 12 (15 or 16 narratives, about 26,000 characters of narrative markup,
    376 to 470 paragraphs): 1.1 to 1.2 s per check, 4.4 s for the very first check, with Chrome
    cold; reading and building each, 2.2 to 2.4 s.
  - The slowest read among the files that build (2,023 paragraphs): 8.1 s.
- Times for a tracked label include a first, refused read without a view, except the 40.1 s,
  which is its view's read alone.

Each check starts Chrome twice (once for the text, once for the list labels). The build runs two
checks, side by side.

**Measured in PR 2** on Cloud Build's default machine, an e2-standard-2 ("Step 2", below): about
1.5 build-minutes for a label of the corpus, and under 4 for the slowest read that builds.
Most of it is fixed: pulling the Cloud SDK image (38 s), main's source (2 to 3 s), the drawing
image (about 20 s, estimated from the worker image's pull) and signing. The two drawings, at
once, take 5 to 26 s for the corpus's labels that carry every section, and 132 to 152 s for the
slowest read. The build's timeout (600 s) and each container's memory (2 GiB) are set from these,
as R11 sets the authority build's.
The 40 s read is also closer to the worker's 60 s recompute limit (D2) than D2's 4.1 s figure
suggests, so the first deploy's measurement of the recompute should include that label.

**Operations:**

- From request to record: a few minutes, once per label version, before the submission is made.
- A merge that changes the drawing's version or image: a worker built after it looks under its own
  drawing id, so it does not find older records (`missing`), and the producer asks again. Between
  such a merge and its deploy, the build (main's code) and the worker (the deployed code) can
  differ; the request is repeated after the deploy. A merge that changes the recompute's versions
  already refuses older requests in both places.
- Key rotation and revocation, as R1 has them: a new key version, its public key pinned in a pull
  request; a revoked version listed in `revoked`; records requested again.
- **The image** (recommended, from the review): the existing images build (`cloudbuild.images.yaml`,
  every deploy) also builds the `word-drawing` target and pushes it to `ema-flow-images`, and a
  pull request pins its digest per environment. That saves a repository, an identity and a trigger
  over R1's separate image build. The cost: about 40 s more per deploy (CI's time for the renderer
  image), a third-party download (Chrome, the Debian snapshot) on the deploy's path, and a new
  digest each deploy that nothing uses until pinned. Old digests stay, since the repository has no
  cleanup policy; one must be added with an exception for pinned digests if it ever is.
- **The repository connection** (tested in PR 2): a trigger with an inline build that clones the
  public repository at `refs/heads/main` over HTTPS needs no GitHub connection. It records the
  commit and checks it is first-parent on main. Cloud Build accepts such a trigger and runs it
  ("Step 2"), so the owner's connection step falls away.
- **New infrastructure**, all in Terraform (PR 2, `infra/word-drawing.tf`): the drawing identity,
  its key, its record bucket, one topic, one trigger and the worker's read on the record bucket.
  The producer's publish grant is not made: today's producer is the operator, whose owner role
  already publishes; the label gateway's grant comes with the gateway.

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
3. **Key the record by the submission** (this note's first draft). One record per submission, and
   the producer could ask only once the submission exists. The drawing identity would need to read
   the whole submissions bucket. And the submission could never name the record. Keyed by the .docx
   and the request, none of that holds. Replaced.
4. **Zone B asks for the drawing itself** when the record is missing. One step fewer for the
   producer, but the worker gains a publish grant, and every refused run would start a build. Not
   recommended.
5. **Check records at deploy time, as R1 does,** copying them into the worker image. A pure lookup
   at run time, but every import would wait for a deploy, and with the records out of the
   repository there is nothing reviewed to copy. Not recommended.
6. **Share R1's identity and key with authority records** (the first draft). Fewer resources, but a
   flaw in one kind of drawing could sign the other kind. Replaced by a key and an identity of its
   own.

## 8. The owner

**Answered 2026-10-07:** the owner said to go ahead on the recommendations of this design's first
draft and of its review, which this revision takes in: among them `zone_a.drawing` itself in a
second image target, and the drawing identity's read of `uploads/sha256/` alone. PR 2 is still
reviewed as any change is; its deploy creates an HSM key that Terraform cannot destroy and whose
destruction waits 120 days.

**Left for the owner:** no GitHub connection. PR 2's test showed a Pub/Sub trigger runs an inline
build that clones the public repository ("Step 2"). Two steps remain outside Terraform:

- `scripts/gcp/plan-identity.sh` (`--check` first), run before the merge that adds the trigger
  (PR 2), as the planner always is, gives the planner `cloudbuild.builds.get`: without it, every
  plan after that deploy fails to read the trigger.
- A hardening item that predates this design: Cloud Build's legacy service account holds
  `roles/cloudbuild.builds.builder` on the project, which writes every bucket (the record bucket
  among them) and publishes to every topic. Nothing here builds as it; removing the binding is the
  owner's, and PR 2 does not.

## What this takes from R1, and what differs

**Taken from R1:** main's code only, with the first-parent `commitSha` check; a Cloud Build trigger
under a dedicated identity; two drawings in separate containers with no network before anything is
signed; a signing key per environment, its public keys pinned in the repository with a `revoked`
list; create-if-absent writes to a bucket only that identity may write; the renderer image's Chrome
and fonts; R1's trust root.

**Different, for Word only:**

- its own identity, key and bucket, shared with nothing;
- the build takes a request, which is data, and makes the record; it does not regenerate a
  committed one;
- no person commits the record, and there is no propose mode (section 3);
- the worker verifies the record at run time; the image build does not, at deploy;
- only a drawing in which every section agrees is signed;
- the drawing image is a second target of `Dockerfile.renderer` with Python added, built in the
  existing images build;
- no captures: no person acknowledges anything, and the verdict is exact;
- R2's widths, ratios and modes, R3's model, R4's geometry and R8's record shape are not used;
  `zone_a.drawing`'s check is.

**Not touched:** R2 to R11 for authority imports. Their dropped parts (3c-C3 to C5) stay dropped.

## Step 1: measured, and PR 1 as built (2026-10-07)

**Where it was measured.** The Mac this was built on (Apple M2, 8 GB) has no container runtime,
and the EMA corpus must not leave it, so step 1 was split in two, and is only partly done (what it
has not measured is listed at the end of this section):

- **The corpus, on the Mac.** Each label was drawn through `zone_a.drawing` itself, one at a time,
  in two Chromes: chrome-headless-shell 154.0.8037.57 for macOS (the image's Chrome for Testing
  release, built for arm64) and Google Chrome 154.0.8037.98. Only counts and times were kept.
- **The image, in CI.** The `Word drawing` job builds it on ubuntu-24.04 (x86_64, Docker) and runs
  it hardened on the committed fixtures.

The corpus harness is committed: `zone-a/scripts/word_drawing_corpus.py` (`parity`, `repeat` and
`summary`). It writes counts, codes and times only, and names a label by its position in its folder,
never by its name. The counts below were made first by a scratch script, and then again with this
harness, from the code committed here, with the same results: 131 SmPCs and 110 leaflets that build,
4,649 sections, every comparison equal, and 1,080 runs over 27 labels, each exiting 0 with one
output per label.

**Parity on the corpus.** Reads are by the accepted view where a label is tracked (187 of 241
parts). "Builds" means with no heading left for a person and not refused whole by the builder (20
more SmPCs and 2 leaflets are ready but refused whole: a floating table, a character scale, a page
break between words).

- **SmPCs:** 296 files. 131 SmPCs build and were drawn: 3,277 narratives, 4.5 million characters of
  markup. 11 carry every section. "What exists" above counted 130. No counting rule tried here
  gives 130: per SmPC it is 131; counting only files whose every SmPC builds gives 128; adding the
  SmPCs the builder refuses whole gives 151. The earlier count's script was not kept, so the
  difference is unexplained.
- **Leaflets:** 286 files. 110 build and were drawn: 1,372 narratives. 12 carry every section.
- **Every one of the 4,649 sections agrees in both Chromes.** Chrome's raw answers are byte for byte
  the same in both Chromes for all 241 parts. Those answers are each section's text, each text
  node's computed facts, each picture's decoded size and the list markers. Any verdict computed from
  them is therefore the same too.
- **No corpus section differs.** A verdict that differs is exercised by the seeded tests
  (`zone-a/tests/test_word_epi.py`, `test_drawing_record.py`), not by the corpus.

**The window and the fonts.** The shell drew every part again at 375 by 812 pixels and a device
pixel ratio of 2, and gave the same raw answers on all 241 parts. It did so through a launcher the
harness writes (`window_launcher`), which runs the shell with `--window-size=375,812` and
`--force-device-scale-factor=2` before `label_docx.browser`'s own switches; the reader is not
changed. A page drawn through that launcher reports 375 by 812 at a ratio of 2, and 800 by 600 at 1
without it. The fonts were macOS's for the
corpus. The image's pinned fonts were measured on the fixtures in CI only. There, all six (the five
Word-made SmPCs and the QRD template) gave raw answers byte for byte those of Google Chrome's
recording on macOS, and every section agreed. The corpus in the Linux image, with its pinned fonts,
is not measured (PR 2).

**Repeatability.**

- The shell drew every part a second time: the same raw answers, 241 of 241.
- `python -m zone_a.drawing` ran with the shell on every label it can sign: the 23 corpus labels
  that carry every section and the 4 committed labels it signs. Each ran 20 times one after another
  and 20 times two at a time. That is 1,080 runs: every one exited 0, and each label's 40
  outputs were byte for byte the same.
- In the image (CI), each committed label ran twice, in two processes. The four it signs gave the
  same bytes twice, and the refused one was refused with nothing written.

**HTML and XML.** Every narrative drawn was parsed by Chrome's HTML parser, as the drawing page
parses it, and by its XML parser, as XHTML. The two trees were the same every time:

- 4,649 narratives on the corpus, in the shell and in Google Chrome;
- 262 of 262 narratives in the image.

The check finds the seeded differences it should: an HTML-closed `p`, a `pre`'s first line feed, a
CDATA section, an entity XML lacks, and text after the root.

**Chrome's sandbox in the hardened container.** It cannot start there. The container was Docker
on ubuntu-24.04, with its default seccomp profile, every capability dropped and no new privileges.
There chrome-headless-shell 154.0.8037.57 ended at once with
`FATAL:...zygote_host_impl_linux.cc:129] No usable sandbox! If you are running on Ubuntu 23.10+ or
another Linux distro that has disabled unprivileged user namespaces with AppArmor, ...` (CI run
37651573909). The message points at the host: ubuntu-24.04 restricts unprivileged user namespaces
through AppArmor, a policy of the runner rather than of the container's flags. So a seccomp profile
admitting user namespaces would not by itself let the sandbox start on such a host; that was not
tried, and loosening the host is not this design's to do. The launcher adds `--no-sandbox`, as the
renderer gate runs Chrome, and the stated residual holds: one layer fewer stands between a label
built to attack Chrome and the container's isolation. Whether Cloud Build's hosts allow the
sandbox is for PR 2 to measure.

**Time and memory on the M2.** One label ran at a time, but the Mac was swapping throughout, so the
tails are loose: the slowest check, 8.5 s, took 2.6 s when repeated.

- **`zone_a.drawing.check`, per SmPC or leaflet:**
  - with the shell: median 0.9 s (90th percentile 1.5 s for SmPCs, 1.3 s for leaflets);
  - with Google Chrome: median 2.8 s for SmPCs and 3.1 s for leaflets;
  - the largest SmPC that carries every section (28 narratives, 260,247 characters): 1.5 s.
- **The parse check:** median 0.2 s per part.
- **Reads:** median 2.5 s. The slowest among files that build took 59 s: 2,280 paragraphs, its
  refused read first and then its accepted view. That file peaked at 895 MB resident in Python. The
  largest process the run waited for, Chrome's browser process, was 223 MB; Chrome's renderer
  processes are not counted.
- **The entry point, per label it signs** (read, recompute and check, Python's start included): on
  average 1.0 to 3.5 s for an SmPC and 1.2 to 2.1 s for a leaflet, one after another. One leaflet
  of 2,023 paragraphs took 5.7 s. Two at a time added up to 1.2 s a run. No process the runs waited
  for (Python, or Chrome's browser process) went above 178 MB.
- **In the image (CI, x86_64):**
  - the image builds in 36 s, with no cache;
  - the whole check takes 21 s;
  - the entry point takes 1.0 s per run of each committed label, and the check 0.4 to 0.6 s per
    Word-made SmPC;
  - the container peaked at 146 MiB over the whole run.

**PR 1's choices, where this note left them open:**

1. **Where Python comes from.** uv installs it in a stage built on the renderer, not on Debian. The
   renderer installs nothing from Debian outside its dated snapshot (`scripts/ci/renderer-pins.mjs`
   refuses any other apt install, and now any later stage not built on the renderer or pinned by
   digest), and it already has the certificates.
2. **Who writes `recordVersion`.** The container writes it with the fields it computes. The signing
   step adds the environment, the commit, `drawing.imageDigest` and the key version (section 4,
   step 4).
3. **The request.** On standard input it must be its canonical JSON exactly: the very bytes whose
   hash is the record's key, so no repeated key or other spelling reaches a record.
4. **Exit statuses.** Status 1 is every refusal or failure, and 2 a wrong command line. Standard
   error is `refused: <code>`, `browser-failed`, `drawn-otherwise: <key>: <where>` or
   `error: <type>`; only the type is named, since an exception's message may quote the label.
5. **The launcher.** `scripts/render/word-drawing.mjs` uses the renderer's hardening (`HARDENING`,
   with its 6 GiB and 2,048 processes). It mounts, read-only, Zone A's and the reader's sources,
   the registry, the mappings, Zone A's scripts and the fixtures CI draws; PR 2's build adds the
   label. The check also reads the container's isolation from inside, as the renderer's smoke check
   does.
6. **The lock.** It is `{version, imageDigests: {dev, validation, prod}}`, each digest null until
   PR 3. A test holds the version to `DRAWING_VERSION`.
7. **HTML against XML** is a test (CI and the corpus), as section 1 has it, not a rule the entry
   point applies. Making it a rule would cost about 0.2 s per label and change what a record says,
   so it is the owner's choice.
8. **CI.** `Word drawing` is a job of its own. It runs on every change, since Zone A's code, the
   reader's and the image are all its inputs. The deploy does not wait for it, as for Renderer,
   since nothing the deploy ships reads a drawing yet. It is not yet a required check, which only
   the owner can add.

**Not measured, so left for PR 2 (each needs the cloud, or a machine with a container runtime
where the corpus may go).** Each was measured in PR 2 ("Step 2", below):

- **The corpus in the Linux image:** parity, with its pinned fonts, on every SmPC and leaflet that
  builds; and time and memory per label there. In the image, CI measured the fixtures only.
- **The image on Cloud Build's machines.** An e2-standard-2 runs on an Intel or an AMD CPU, so the
  image runs twice on each, and every output must be byte for byte the CI run's.
- **e2-standard-2 timings:** per label, for the slowest read, and for the whole build. The build's
  timeout and the two containers' memory are set from them.
- **Chrome's sandbox on Cloud Build's hosts** (below: on GitHub's it cannot start).
- **The trigger's tests:** payload binding, the CEL filter, `actAs` and the inline clone.

## Step 2: PR 2 as built, and measured (2026-10-07)

**What PR 2 adds.** Everything persistent is in Terraform (`infra/word-drawing.tf`), and the
deploy applies it after the merge; nothing was made by hand.

- The drawing identity `ema-flow-word-drawing-<env>`, holding exactly six grants, which
  `test/infra/word-drawing.test.ts` proves exhaustive: `cloudkms.signer` on `word-drawing-hsm`;
  `objectCreator` and `objectViewer` on the record bucket; `objectViewer` on the submissions bucket
  under the condition `uploads/sha256/`; `artifactregistry.reader` on `ema-flow-images`; and log
  writer.
- The key `word-drawing-hsm` in the evidence ring: HSM, RSA-PSS 3072 with SHA-256, signing only,
  `prevent_destroy`, 120 days before any destruction.
- The record bucket `<project>-ema-flow-<env>-word-drawings`: on the evidence key, no versioning,
  public access prevented, `prevent_destroy`. The worker may read it.
- The topic `ema-flow-<env>-word-drawing-requests`, and the trigger `ema-flow-<env>-word-drawing`
  in the region.
- The image: `cloudbuild.images.yaml` builds the `word-drawing` target on every deploy, with the
  legacy builder (as CI's Word drawing job now builds it too), and pushes it as
  `word-drawing:<commit>`. PR 3 pins a digest from it.
- The build itself: `scripts/word-drawing/build.sh`, one step per call;
  `scripts/word-drawing/stored.py`, which decides whether a stored object is a record the build
  accepts; and, in the image, `zone-a/scripts/word_drawing_build.py` (the strict parse, and the
  record's assembly).
- The planner gains `cloudbuild.builds.get` (`scripts/gcp/plan-identity.sh`), the one permission
  that reads a trigger, and the deploy's effective-IAM export names the drawing identity.

**The trigger, as built.** Fired by a message on the topic; its substitution `_REQUEST` is bound to
`$(body.message.data.request)`, and its filter is `size(_REQUEST) > 0 && size(_REQUEST) <= 4000 &&
_REQUEST.matches("^[A-Za-z0-9_-]+$")` (4,000 characters: a request with every SmPC section assigned
is about 1,250). The request reaches the first step alone, as `REQUEST` in its `env`; the other
inputs (environment, buckets, key, image repository) are literals Terraform writes into the build's
`options.env`. The build has no source and no configuration file: its steps are written in the
trigger, and the first fetches `refs/heads/main` of the public repository over HTTPS. Its timeout
is 600 s and its `queueTtl` 300 s. The steps, each in an image pinned by digest:

1. `exists` (Cloud SDK 588.0.0-slim): main's source, depth 1, cleaned and required clean
   (`git clean -ffdx`, then nothing untracked or ignored: no extra public key, no module on
   Python's path); the request decoded only where it is unpadded base64url and the one spelling of
   its bytes; the key; the drawing id from the lock; the newest key version pinned for the
   environment (`src/render/word-drawing/keys/<env>/<n>.pem`, less those `revoked.json` lists); and
   the record looked for by Cloud Storage's JSON API, at most 64 KiB. Found (200), it must be a
   record this build accepts (`scripts/word-drawing/stored.py`: exactly `{record, signatureBase64}`,
   the one base64 spelling of a signature that verifies with the pinned key, and a canonical record
   of this request, environment, key version and image), and then every later step ends at once;
   anything else at the path fails the build. Not found (404), the build goes on; any other answer
   fails it. Until PR 3 pins an image and a key, every build ends here, refused.
2. `pull` (the images build's Docker builder): the image, by the pinned digest.
3. `parse` (the image, hardened, no network): the request, its canonical JSON exactly, of the
   recompute request's shape, assigning only sections of the document's template, with this
   build's versions (`word_drawing_build.py request`), before the .docx is read.
4. `fetch` (Cloud SDK): the .docx at its content address, at most 32 MiB (`curl --max-filesize`,
   then its length), of the SHA-256 the request names.
5. `draw-1` and `draw-2`, at once (the image, hardened, no network, 2 GiB each):
   `python -m zone_a.drawing`.
6. `record` (the image, no network): the two outputs byte for byte the same, each exactly the
   fields of this request, made the record with the environment, the commit, the image's digest
   and the key version (a JSON integer), as canonical JSON (`word_drawing_build.py record`).
7. `sign` (Cloud SDK): the commit is on main's first-parent line, fetched again; Cloud KMS signs the
   SHA-256 of the record's bytes; `{record, signatureBase64}` is checked by `stored.py`, as any
   stored object is, before anything is written (a record that did not verify would hold its path
   for good); and it is written with `ifGenerationMatch=0`. Where an object is there already (412,
   or 403), `stored.py` must accept it and find it this build's record but for its commit (two
   builds of one request on either side of a merge differ there alone); otherwise the build fails,
   naming its own commit and the stored one's, as a planted object or a drawing that is not
   deterministic.

Cloud Build makes the trigger's push subscription itself (`gcb-<trigger>`, seven days' retention),
outside Terraform, and deletes it with the trigger (observed).

**The trigger's tests** (a throwaway topic, publisher and trigger in `europe-west4`, applied by a
scratch Terraform configuration of the same blocks, tested and destroyed):

- **No repository connection.** Cloud Build accepted a Pub/Sub trigger whose build is inline, with
  no source, and ran it: the first step fetched `refs/heads/main` anonymously over HTTPS (main at
  `67581df`, in 2 to 3 s).
- **Payload binding.** The step's `REQUEST` was the message's `request` exactly (its length and
  SHA-256 compared), and a later step without the `env` entry did not see it.
- **The filter.** No build started for a padded request, a base64 one (`+/`), a body without
  `request`, a body that is not JSON, an empty request, or one of 4,001 characters. One of 4,000
  characters started a build and arrived whole.
- **`actAs`.** The messages were published by a throwaway service account holding only
  `pubsub.publisher` on the topic; the builds ran as the trigger's service account.

**Create-if-absent, by IAM** (a throwaway bucket and an identity holding `objectCreator` and
`objectViewer`, destroyed after): the first write with `ifGenerationMatch=0` was created (200); a
second was refused as existing (412); a write without the precondition was refused (403), as was a
delete (403); the object read back unchanged; a missing one read 404.

**Chrome's sandbox on Cloud Build's hosts.** It cannot start there either. The hosts run Debian's
5.10 cloud kernel, cgroup v1, Docker 20.10.24 with AppArmor and its default seccomp profile, and
the shell ended at once with the same `No usable sandbox!`. So `--no-sandbox` stays, and so does
the residual.

**The image on Cloud Build, Intel and AMD.** Built with the legacy builder in 80 to 84 s
(781 MB). Eight builds ran it, five on Intel (Xeon at 2.20 GHz, e2-standard-2) and three on AMD
(EPYC 7B12: one e2-standard-2, two e2-highcpu-8). In every one:

- `word_drawing_check.py` passed, hardened (19 to 35 s): every committed label's fields the bytes
  it requires, each drawn twice alike, the refused one refused; every carried section of the
  Word-made SmPCs and the QRD template agreeing, with raw answers byte for byte Google Chrome's
  recording; 262 of 262 narratives parsed alike;
- `python -m zone_a.drawing`, two containers at once on each signed label, wrote the same bytes in
  every run of every build: one output per label across both CPUs.

**The corpus in the Linux image.** The 181 corpus files that build a part were drawn in the image,
each label in a container of its own (hardened, 2 GiB, `/tmp` made executable for the harness's
window launcher), by `word_drawing_corpus.py one`, with the image's shell in every Chrome's place;
names stayed on the Mac, and the files went to a temporary path of the build-staging bucket, deleted
after (`gs://…-build-staging/word-drawing-pr2-corpus/`, within its seven-day soft-delete window).

- Parity: 131 SmPCs and 110 leaflets drawn, 3,277 and 1,372 narratives: every one of the 4,649
  sections agrees. Every part's counts (builds, sections, drawn, every section, characters of
  markup) are the Mac's, on all 253 parts, and so are the reads (view, paragraphs).
- Repeatability: the shell drew every part again, and at 375 by 812 and a ratio of 2: the same raw
  answers on all 241 parts. HTML and XML parse trees: 4,649 of 4,649 the same.
- Across machines: two runs on AMD (e2-highcpu-8) and one on Intel (e2-standard-2) gave the same
  raw answers on all 241 parts, and every section agreed in each.
- Not measured: the corpus's raw answers against macOS's; the Mac kept no digests of them. The
  fixtures' raw answers are byte for byte Google Chrome's on macOS (above).

**Time and memory on e2-standard-2** (Intel; two containers at once, as the build runs them):

- The 23 corpus labels that carry every section, five builds each: 5.3 to 25.9 s a pair (median
  8.9 s); each container peaked at 114 to 186 MiB. Every label wrote one output across all twelve
  runs (six builds, two containers).
- The slowest read that builds (2,280 paragraphs, its accepted view, refused at `section` for its
  first part): 132 to 152 s a pair, each container peaking at 893 to 896 MiB. Under the build's
  own bound, 2 GiB (below), the same: refused at `section` in both containers, in 119 s, neither
  killed for memory.
- The committed labels: 2.1 to 3.9 s a pair on Intel, 1.4 to 2.5 s on AMD (both e2-standard-2);
  99 to 114 MiB each.
- Fixed: the Cloud SDK image's pull, 38 s; main's source, 2 to 3 s; a trigger's build queued for 1
  to 32 s; the worker image (393 MB) pulled from `ema-flow-images` in 9.4 to 10.3 s, so the drawing
  image (781 MB) in about 20 s, estimated.
- So the timeout, 600 s, is four times the slowest read's drawing and about two and a half times
  the slowest build, all steps counted; the `queueTtl`, 300 s, lets a backlog expire. Each drawing
  container is bounded to 2 GiB: more than twice the slowest read's peak, the worker's own bound
  for the same recompute, and 4 GiB for the two of 8 GB.

**The build's own steps, end to end** (`build.sh` on e2-standard-2, from `parse` to `record`,
with a committed label and what `exists` writes seeded, since the key and the bucket do not exist
yet): `parse` 1.9 s; `draw-1` and `draw-2` at once, 4.2 and 4.4 s, byte for byte the same; and
`record` 1.9 s, which wrote the record of section 3 with the four fields the build adds.

**Uncertain until the first build after PR 3:** what needs the deployed resources has run only
against stand-ins (`test/infra/word-drawing.test.ts`) and the throwaway tests above: `exists`'s
lookup in the record bucket, `pull` of the pushed image as the drawing identity, `fetch` under the
conditioned grant, Cloud KMS's signature, and the write. The first synthetic label's record, made
once PR 3 is merged ("Step 3"), is their first run; until PR 3, a request ends at `exists`, refused,
since nothing is pinned.

## Step 3: PR 3 as built (2026-10-07)

**What PR 3 pins, for dev only.** Validation and prod stay unpinned (a null digest, no key), so
their builds still end at `exists`, refused, until a pull request pins each from its own deploy.

- `src/render/word-drawing/lock.json`: dev's image,
  `sha256:eff4827a9f5ebefe001e624ae197ecace2ca126d8827f2fe70dacf1632061c27`, the `word-drawing`
  target as the images build pushed it from main at `cb27bd9` (tag `word-drawing:cb27bd9c4a95`).
  Dev's drawing id is therefore
  `03d058be759c88620d0748c07ea6c747c468b7ad9b0e95f23476e07db67d4000`.
- `src/render/word-drawing/keys/dev/1.pem`: `word-drawing-hsm` version 1's public key, the PEM as
  Cloud KMS gave it. No `revoked.json`: nothing is revoked, and the build reads its absence as an
  empty list.

**How each was verified** (read only, with `gcloud` and GETs authorised by its token):

- **The image.** The deploy's run 37688275636 (a push to main, at
  `cb27bd9c4a95f8876da978f9fb527d1d730cd3b4`, main's first-parent head) made Cloud Build build
  `adba9d09-1227-4361-88b4-d2c20930ad67` in `europe-west4`, as `ema-flow-build-dev`, with
  `_REVISION` that commit. The build's results list `word-drawing:cb27bd9c4a95` at the pinned
  digest. The manifest the registry serves for the digest hashes to it, and is one linux/amd64
  image, not an index. Its configuration carries `org.opencontainers.image.revision` =
  `cb27bd9c4a95f8876da978f9fb527d1d730cd3b4`, and runs as `node` with the drawing's environment
  (`LABEL_CHROME`, `ZONE_A_ROOT=/work`).
- **The key.** Version 1 is `ENABLED`, `RSA_SIGN_PSS_3072_SHA256`, `HSM`, made at
  2026-10-07T21:31:28Z. The PEM from `gcloud kms keys versions get-public-key` and the API's
  `publicKey` are byte for byte the same, and its CRC32C is the `pemCrc32c` the API sent with it
  (939517586). It is a 3072-bit RSA key, exponent 65537, and both OpenSSL (as `stored.py` runs
  it) and Node take it for RSA-PSS with SHA-256 and a 32-byte salt.

**Tests.** `test/render/word-drawing.test.ts`: dev's digest is a digest reference; the key is RSA
3072, exponent 65537, its PEM the key's own SubjectPublicKeyInfo export with KMS's CRC32C, and it
verifies under PSS with SHA-256 and a 32-byte salt. `test/infra/word-drawing.test.ts`: with main's
own lock and keys, `exists` goes on in dev, by the pinned image and key version 1, and validation
and prod are still refused. `scripts/word-drawing/build.sh` is unchanged.

**What PR 3 leaves.**

- **The worker's copy of the key.** The worker image carries `dist/` and not `src/`, so the key is
  not in it yet. PR 4, whose gate reads it, puts `src/render/word-drawing/` in the worker image.
- **No version moves.** The lock and the keys are outside `src/certified-word/` and every other
  lock, so neither importer, nor `word-drawing/1.2.0`, nor a contract changes.
- **The first record.** Once PR 3 is merged, the committed synthetic SmPC
  (`test/fixtures/certified-word/recompute/smpc.docx`, SHA-256 `f86f053e…fbee09`) is uploaded to its
  content address and its request published once (the pull request's post-merge verification). Its
  record is at `word/7e4c389f…cd92809/03d058be…db67d4000/1.json`, with `recompute.outputSha256` the
  committed `smpc.json`'s and 32 sections. That build is the first run of what "Uncertain until the
  first build after PR 3" lists. PR 4's dry run then finds the record.

## Step 4: PR 4 as built (2026-10-07)

**What PR 4 adds.** Step 5 of section 5, as section 3's "How Zone B verifies it" has it, and
nothing of step 6.

- `src/certified-word/drawing.ts`, in the importer's locked directory (certified Word importer
  1.3.0):
  - **the pins**, read as `scripts/word-drawing/build.sh` reads them: the lock's version and the
    environment's image digest, and each `keys/<environment>/<n>.pem` that `revoked.json` does not
    list, highest first, each refused unless it is a 3072-bit RSA key;
  - **the read**, `word/<key>/<drawing id>/<n>.json` for each pinned version in turn, under the
    worker's own identity: not-found goes on to the next version; any other Storage error fails the
    run; the first object found decides, and is never stepped over;
  - **the bytes**: over 64 KiB refused before anything is parsed; strict UTF-8 and JSON with no
    repeated key (the importer's own reader); the canonical JSON of exactly
    `{ record, signatureBase64 }` with the record's fields of section 3; `keyVersion` the path's;
    the SHA-256 of `request` the path's key;
  - **the signature**, by `verifyPss`, factored out of `src/approval/statement.ts` so an approval
    statement and a drawing record are verified by one function (the one base64 spelling, RSA-PSS,
    SHA-256, a 32-byte salt, over the canonical bytes);
  - **the fields**: the environment the worker's; the document's hash and length the source's;
    `outputSha256` that of the bytes the worker's recompute wrote; the drawing's version and image
    the pins'; `sections` the provenance's, in order.
- `src/certified-word/gate.ts`: step 5 after step 4 in every run that recomputes, dry or not.
  `certified-word-drawing-invalid` and `-mismatch` refuse; a dry run answers `drawn` where the
  record verified and `recomputed` where none was found; a run that is not dry is refused with
  `certified-word-document-unbound` once a record verified and `certified-word-drawing-missing`
  where none was found (none at any pinned key version's path, or, with no image, key or record
  bucket, none looked for). `SubmissionRefusal` gains the three new codes, which the HTTP caller
  learns as it learns the others.
- **The worker**: its image copies `src/render/word-drawing/` (root's, read-only to the service)
  to its working directory, where the gate reads it; and Terraform names it the record bucket
  (`WORD_DRAWING_BUCKET`) and its environment (`WORD_DRAWING_ENVIRONMENT`, `var.environment`),
  which select the pins. Its read of the bucket was granted in PR 2. The image's recompute smoke
  (`scripts/ci/worker-recompute-smoke.mjs`, in CI's Images job and in Cloud Build before the push)
  reads the pins there as the gate does, so an image without them fails before it is pushed.
- **The build** (`scripts/word-drawing/build.sh`, `sign`, after the review of #207): a stored
  object over the worker's 64 KiB cap, signature and all, is refused before it is written, since
  written create-if-absent it would hold its path for good and be refused at every run.

**Choices where this note left them open:**

1. **A worker that recomputes but is not given the record bucket** finds no record: a dry run
   answers `recomputed`, and any other run is refused `certified-word-drawing-missing`. It is
   never taken for drawn.
2. **A record whose request is not the submission's is `invalid`, never `mismatch`:** the path's
   key is made from the submission's source, and the record's request must hash to it, so such a
   record is at another path or fails there. The record's `document` is compared with the source
   as well.
3. **Not compared:** `drawing.chrome` (the pinned image fixes the Chrome) and `commitSha` (the
   build checked it is on main's first-parent line), as section 3's list has it.
4. **Section 3's item 6, keeping the record's bytes with the run's evidence,** waits with step 6
   for P5: until then no certified Word run persists anything, so there is no evidence to keep it
   with.

**Tests.** Against the first real record: what dev's build signed with key version 1 for the
committed synthetic SmPC, copied byte for byte into `test/fixtures/certified-word/drawing/`.

- `test/certified-word/drawing.test.ts`:
  - its path is the one made from the submission's .docx and request and dev's pins; it verifies
    against the pinned key, and its 32 sections are the provenance's, in order;
  - `invalid`: another signature, one in another base64 spelling (`==` more), one with no salt
    instead of 32 bytes, one by another key, a section's hash or the commit changed, its
    bytes spelled otherwise, a repeated key, not JSON, empty, a byte-order mark, another field, a
    `keyVersion` or a request not its path's, and a record over 64 KiB (which, 500 sections long
    instead of 700, is a `mismatch`: the cap acts first);
  - `mismatch` (records signed by a key made for the test): the environment, the document's hash
    or length, the output, the drawing's version or image, the sections reordered or one fewer;
  - `missing` with nothing at the path, and no read at all where no image or key is pinned; the
    highest version read first, and an object there that fails refusing though version 1 holds a
    good record; a Storage error thrown;
  - the pins: versions highest first, revoked ones left out, a malformed `revoked.json` or a key
    that is not RSA 3072 refused.
- `test/certified-word/recompute.test.ts`, the gate on the committed label (and in CI's Zone A
  job, with the real recompute): the dry run of the synthetic SmPC `drawn`, alone and through the
  worker's pipeline; not dry, `certified-word-document-unbound`, and without a record
  `certified-word-drawing-missing`; `invalid` and `mismatch` (the recompute's output in other bytes
  that make the same submission) dry or not; a Storage error failing the run.
- **After a version move** (`word-epi/1.4.0` and `word-drawing/1.2.1`, 2026-10-07,
  `docs/validation/changes/2026-10-07-fidelity-norm-3-4-0.md`): the real record is of the build it
  was drawn by, so `drawing.test.ts` holds it to that build (the recompute's bytes it names,
  frozen as `smpc.recompute.json`, its request, and dev's pins with `word-drawing/1.2.0`) and
  shows a later build finds it nowhere; `recompute.test.ts` holds the gate to a record made as
  dev's build makes one for this build, signed by a key made for the test (dev drew the synthetic
  SmPC for this build on 2026-10-08, below; the tests keep the test key, since every reader or
  builder version moves the path again). The image is unchanged: the
  code is mounted from the checkout.
- `test/infra/word-drawing.test.ts`: the worker named the record bucket and `var.environment`.
  `test/ci/images.test.ts`: the worker image, and only it, copies the pins.
- Each rule of step 5 removed in turn from `drawing.ts` (the cap, `keyVersion`, the request's
  key, the first object deciding, the environment, the output, the sections) fails a test.

**In dev.** The deployed worker runs with `DRY_RUN=false`, and the run request has no dry-run
field, so no dry run can be made through it. Before the merge, read only: dev's upload of the
synthetic SmPC and its record, read with `gcloud storage cat`, through this gate on this machine
with the real recompute: the dry run `drawn`, through the pipeline `validated` and `drawn`, and not
dry `certified-word-document-unbound`. After the deploy, a run of that submission through the
deployed worker is expected to be refused `certified-word-document-unbound`, which only a record
the worker found under its own identity and verified with the key its image carries gives
(the pull request's post-deploy steps).

**Verified after the deploy (2026-10-08, main a00b4cd).** A run of the synthetic SmPC's submission, through the deployed
worker under `ema-flow-workflow-dev`, answered HTTP 422 `certified-word-document-unbound`: step 5
read the record under the worker's identity and verified it with the key its image carries.

**The record for `word-epi/1.4.0` (2026-10-08, main 1fa5212, #208).** A builder version is part of the
request, so `fidelity-norm/3.4.0`'s build looks for the synthetic SmPC's record at another path. Its
request (key `48c680712be78fa7…`) was published once to `ema-flow-dev-word-drawing-requests` at
02:33:40 UTC; the build succeeded and stored
`word/48c68071…/a69dd51c…/1.json`, which `scripts/word-drawing/stored.py` accepts against
`src/render/word-drawing/keys/dev/1.pem`, dev, key version 1, image `sha256:eff4827a…`, `word-drawing/1.2.1` and that
request (exit 0, commit 1fa5212).

**The record for `word-epi/1.5.0` (2026-10-08, main 35feaa0, #211).** The synthetic SmPC's request for
this build (key `20a0a9887d3f757b…`) was published once at 07:58:23 UTC; the build succeeded and stored
`word/20a0a988…/978b736d…/1.json`, which `scripts/word-drawing/stored.py` accepts against
`src/render/word-drawing/keys/dev/1.pem`, dev, key version 1, image `sha256:eff4827a…`,
`word-drawing/1.2.2` and that request (exit 0, commit 35feaa0).

**`word-epi/1.5.0` (`fidelity-norm/3.5.0`, 2026-10-08).** The builder's version moves the
request's path again, and the drawing's version (`word-drawing/1.2.2`) its drawing id, so this
build finds the record for `word-epi/1.4.0` nowhere; the tests keep the test key, as above, and
dev's image is unchanged (the code is mounted from the checkout). Dev drew the synthetic SmPC again
for this build after the deploy, as for 1.4.0 (the record below).

**`word-epi/1.6.0` (`fidelity-norm/3.6.0`, 2026-10-09).** The builder's version moves the
request's path again, and the drawing's version (`word-drawing/1.2.5`) its drawing id, so this
build finds the record for `word-epi/1.5.0` or `1.5.1` nowhere; the tests keep the test key, as above, and
dev's image is unchanged (the code is mounted from the checkout). The builder's version moves
with the scanner it reads (section 5's half-life forms); the drawing check's rules do not change.
The synthetic SmPC's request is to be published again after the deploy, as for 1.5.0.

**`word-epi/1.7.0` (`fidelity-norm/3.7.0`, 2026-10-09).** The builder's version moves the
request's path again, and the drawing's version (`word-drawing/1.3.0`) its drawing id, so this
build finds the record for `word-epi/1.6.0` nowhere; the tests keep the test key, as above, and
dev's image is unchanged (the code is mounted from the checkout). The drawing check's rules move
with the builder's: on the read's side a nudge (`zone_a.word_epi.nudged`, ADR 0006 owner
decision 13), which the narrative leaves out, is left out too, as an underline is; any other
shift is still a mark Chrome does not draw, and differs. The 15% pattern greys join `GREY`, which
the check already reads as the narrative's silver span. The synthetic SmPC's request is to be
published again after the deploy, as for 1.5.0.

## Build order, each change reviewed on its own

1. **Measure first** (partly done 2026-10-07, above, in "Step 1": the corpus with the pinned
   build of chrome-headless-shell for macOS, since the Mac it ran on has no container runtime, and
   the image on the fixtures in CI; what remains is listed there and goes to PR 2), with nothing
   merged: build the `word-drawing` image locally from a scratch copy of the target. In it, measure:
   - **parity:** the same verdicts as Google Chrome on the five Word-made SmPCs, the synthetic
     certified Word fixtures (SmPC and leaflet), and the EMA's SmPCs and leaflets that build;
   - **repeatability:** two runs, byte for byte;
   - **the HTML and XML parsers** making the same tree of every narrative;
   - **Chrome's sandbox** in the hardened container;
   - **time and memory** per label.

   If parity fails anywhere, the design stops there and the difference is understood first.

2. **PR 1, the code and the image target** (built 2026-10-07):
   - `recompute/1.2.0`'s function, and `zone_a.drawing`'s entry point (`word-drawing/1.2.0`);
   - the `word-drawing` target and its launcher;
   - a CI job that draws the fixtures in the image and requires the recorded verdicts, two
     identical runs and the parser test;
   - step 1's corpus parity, repeatability and timings recorded in the pull request.
3. **PR 2, the infrastructure** (built 2026-10-07, "Step 2"): section 6's list, with the Terraform
   tests (the identity's grants proven exhaustive; the trigger's ref and configuration as literals)
   and the trigger tests (payload binding, CEL filter, `actAs`, the inline clone).
4. **PR 3, the pins** (built 2026-10-07 for dev, "Step 3"):
   - each environment's image digest in `src/render/word-drawing/lock.json`;
   - the key's first public key in `src/render/word-drawing/keys/<environment>/`;
   - after its merge, the first record of a synthetic label in dev.
5. **PR 4, the gate** (built 2026-10-07, "Step 4"):
   - step 5 with its codes and the dry run's `"drawn"`;
   - `certified-word-document-unbound` for the runs that are not dry;
   - the shared PSS check, and the pinned keys in the worker image;
   - ADR 0002's invariant 11 restated;
   - then a dry run in dev that finds the synthetic label's record.

   Step 6 and run manifest 7.0.0 come with P5.

## Stated residuals

- The record proves what Chrome's default stylesheet draws. The EMA viewer applies its own, which
  could draw a list label, a colour or a background otherwise (R2's residual).
- One Chrome build is the reference: the pinned chrome-headless-shell on Linux, not every browser
  a reader uses.
- An exploit in the drawing container could make both drawings agree falsely; an escape from the
  container reaches the build machine's metadata server and the drawing identity's token, which can
  sign and can read every upload. Chrome's own sandbox could not start in the hardened container
  on CI's host (measured, "Step 1") nor on Cloud Build's (measured, "Step 2"), and the launcher
  runs Chrome without it, so one layer fewer stands in the way.
- The planner's `cloudbuild.builds.get`, needed to read the trigger, also reads every build's
  metadata, so any pull request's plan can read each drawing request as it was published. A request
  the build accepts holds only the .docx's hash, section keys of the template, counts and this
  build's versions (`parse` refuses any other); but a publisher could put any 4,000 characters of
  base64url in one, and the planner could read them, refused or not.
- Who may publish a request: the producer, and, at project level, the owner, `ema-flow-deployer`
  (`roles/pubsub.admin`) and Cloud Build's legacy service account. Each can spend build minutes;
  none can sign.
- Who may write the record bucket at project level (above): an object planted at a record's path
  fails every build of that request and is refused by the gate, so it denies that label its record
  until removed; it cannot be taken for one.
- Cloud Build's own push subscription (`gcb-<trigger>`) keeps a message it could not hand to a build
  for seven days and retries it; `queueTtl` bounds only a build already made.
- A flood of well-formed requests costs build minutes; the backstop is a budget alert.
- The record does not vouch for the product, the document id (P5) or the approval: only that each
  narrative draws as the .docx was read.
