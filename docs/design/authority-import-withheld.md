# A withheld section: importing a publication with an authority's defect recorded in place (roadmap 3a, PR 3c)

- Status: proposed, 2026-09-25; fourteenth draft, after thirteen design reviews
- Owner decisions (2026-09-25): withhold the Imatinib Teva tablets SmPC's 5.1 and import the
  other 31 sections; a section may be withheld only on reviewed, measured evidence of the
  authority's defect, the whole record is marked incomplete everywhere it is read, and the safety
  sections 4.2 to 4.9 are never withheld
- Decides: how an authority import carries a section it cannot accept, instead of refusing the
  whole publication
- Amends: `AGENTS.md` ("fail closed on … mandatory QRD sections", a scoped exception); ADR 0002
  (invariants 3, 4, 6 and 10: a coded section with a verified notice instead of a verified narrative; its 2026-09-24 amendment's contract, 3.0.0, and who judges content: an attested requester);
  ADR 0003 (the verifier's scope); ADR 0005 decision 3 and its consequence;
  `docs/design/authority-import-contract.md` D1 (the dry-run lift's condition), D2, D3, D4, D6, D7,
  D8 (the placeholder requester), D9, D10, D12, D13;
  `docs/fidelity-normalization.md` §7 (a new minor version, W5); `docs/design/approval.md` (item 2's
  signed request statement and its role, W6); the query service's and the agent's designs (`docs/design/epi-mcp-query-service.md`, `docs/design/verifiable-answers.md`)
- Related: ADR 0002, `docs/design/authority-import-t.md` (T5's evidence),
  `docs/design/authority-import-renderer.md` (the evidence), `docs/design/qrd-conformance-check.md`

## What this is for

Section 5.1 of the EMA's Imatinib Teva film-coated tablets SmPC is misleading when drawn, not
only outside T's rules. It was measured in macOS Chrome, with the FHIR div's inline styles only,
on a page with an 8 px margin, at widths from 280 to 1 000 CSS px (the renderer gate measures it
again in the pinned image):

- The at-risk tables under the two Kaplan-Meier figures put up to three values in one cell. At
  800 px, table 8's "182:8" and "177:12" stand 2.33 px apart (0.19 em). At 360 px, 18 of its cell
  pairs touch, drawing "10:842:840:84" (from 281 to 419 px they touch at every width), and table
  9's values stand as little as 0.19 px apart at content widths of 504 px and below
  ("64:2246:2327:25"). The EMA's
  PDF draws each value under its own axis tick.
- The row heading "At-risk : Events" has `margin-left: -14.6pt`; at 593 px and below "Events"
  wraps to a line starting 19.45 px left of the table, off a page without a margin ("vents").
- In table 2, twelve paragraphs with a −1 pt right margin in unpadded, bordered cells cross the
  cell edge by up to 1.28 px at some widths (the border touches a serif pixel of an "n" at 561 px,
  a touch, not a line through the letter).

ADR 0005 decision 3 refuses such a publication. Of the EMA ePI API's public corpus (268 Bundles;
English SmPCs for five centrally authorised products) no English SmPC gets past T, pictures and
structure except the two Imatinib Teva SmPCs, each blocked only by its 5.1; the capsules' 5.1 shows
no run-together cells, so only the tablets' can be withheld. The owner decided that the other 31 sections
are imported with their full proof and 5.1 is carried as withheld: its heading and code, none of
the authority's content, and the gap stated wherever the record is read.

## Decisions

### W1. What may be withheld: a leaf section whose drawing is shown unsound, never a safety section

A section may be withheld only when all hold:

1. **A person asks, having reviewed the evidence.** The import request (D2)
   names, as the renderer note's R5 requires of every import request but a synthetic one that
   withholds nothing, the evidence reviewed,
   `renderEvidence: { recordSha256, environment, capturesSha256 }`: the renderer record (one per
   document) and the index of the render build's captures of it, which its attestation names (R1),
   read with a review tool that verifies the attestation, the index and each capture's hash before
   showing them, in the environment the import is made in; and it gains `withheld`: a list of
   `{ path, code, confirmed, rejected }` in the document's pre-order, `path` the section's
   `SectionPath`, `code` its code as served, `confirmed` the defects (by their identity, `{ kind,
location }`, the renderer note's R8) they judged misleading and `rejected` those they judged
   sound (a split number such as "12" beside ".5" is geometrically a run-together and is rejected).
   `withheld`, when present, has at least one entry; `confirmed` and
   `rejected` are disjoint, without repeats, in the record's order, and their union is exactly the
   section's defects in the record. The request is approved content (D2), covered by
   `approvedContentSha256` (D8). The lookup refuses if the record, captures or environment are not
   the ones named (`renderer-evidence-changed`, the renderer note's R5), or the section's defects are
   not exactly those confirmed and rejected (`withheld-evidence-changed`), or none is confirmed (`withheld-section-not-shown`), so a later
   record never stands in for the one reviewed. The rejected defects are kept in the provenance, as
   findings against the gate's definition of a defect.
2. **Its drawing is shown unsound, by evidence that is not ours.** The renderer gate's record for
   the document (`docs/design/authority-import-renderer.md` R8), attested by the render build and
   verified by the image build (R1), holds for the section:
   - **no refusal of ours** at any width, ratio or mode: nothing the gate could not judge (a family
     it has no pinned face for, a missing glyph, a model difference, a script or mark it cannot
     bound, a calibration failure, an XML `parsererror`); the font check runs on every section,
     T's or not, so no defect is measured in a substitute font;
     **and** at least one confirmed defect (the renderer note's R8), a geometric finding that a reader
     cannot read the drawing as written, never taken from a content area, and using only the
     parameters R4 binds for defects (`off-page` alone reads a bound, one that contains all the ink,
     so the direction is safe): two cells'
     text on one line with no more space between them than between two letters of a word, their
     facing advances abutting or overlapping (`cells-run-together`: "182:8" beside "177:12" read as
     one run); a line through the body of a letter, not a touch at a tail or serif
     (`line-through-letter`); or a character whose every possible ink lies off the page
     (`off-page`); none involving a picture's box. Every defect is also a failure of the gate's
     checks.

     A contact (the renderer note's R4) is not a defect, nor is a failure that is not one (a line under
     a sign, a covered glyph, a contrast under 4.5:1, a fold off its position): neither can justify
     withholding. A section with such a failure and no confirmed defect refuses the import, as today;
     a section withheld on a confirmed defect carries none of its other failures into the record (they
     stay in the record by hash). Nor can any refusal of ours (a font
     we cannot draw, a rule of T's stricter than the drawing, a scanner grammar, and in a carried
     section a picture we cannot fetch; a withheld section's pictures are drawn as the import has
     them, 5.1's two as broken-image boxes, `unpinned`, until 3c-E pins the export, and no defect may
     involve a picture's box). The finding is stated for what the gate draws: the FHIR div as published, with its
     inline styles only, in fonts metric-compatible with those it names.

3. **A leaf.** The section has no subsections (5.1 has none), so "information withheld" never
   stands over carried text.
4. **Not a safety section.** The section is not 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8 or 4.9
   (`smpc.4.2` to `smpc.4.9` in the mapping) and not under one: a defect there refuses the whole
   label (the owner's decision).
5. **Not a document-level failure.** Bytes, shape, binding, tree and titles (D1, D4, D5) still
   refuse the import whatever is listed.

Both directions are enforced: a listed section without a defect, or with a refusal of ours,
refuses the import (`withheld-section-not-shown`); a section that is not listed must pass as any
carried section does (no refusal, no failure, every contact acknowledged; the renderer note's R5), and since every defect is a
failure, an unlisted defect refuses the import, at the first stage that refuses the section in
D10's order (`pictures`, `narrative`, `record` or, last, `rendering`). Withholding is never
automatic and never silent. For a synthetic publication, a withheld section needs a record too
(the renderer note's R5).

### W2. Where the checks sit (D10)

- At `tree`, once the sections are placed: each listed path names a section with that code
  (`withheld-section-unknown`), none is listed twice (`withheld-section-duplicate`), the list is in
  pre-order (`withheld-section-order`), and no listed section has subsections or is a safety section or
  under one (`withheld-section-not-permitted`).
- At `pictures`, `narrative` and `record`: a listed section is skipped. Its pictures are neither
  checked, fetched nor listed in `pictures` (D6), T and the scanner do not read it, and it gives T5
  no evidence (W4). Unlisted sections are checked as today, stopping at the first refusal.
- At `rendering`: W1.1's binding (`withheld-evidence-changed`) and W1.2 in both directions
  (`withheld-section-not-shown`), in the renderer note's lookup.

### W3. What the record carries

- **Its place.** The section keeps its place in the tree, its title and its code (D4).
- **No authority content.** Nothing of the section's div enters the record.
- **A fixed notice.** FHIR's `cmp-1` requires a section to hold text, entries or subsections. A
  withheld section carries exactly: `emptyReason` whose only element is one coding,
  `{ system: "https://khs.dev/fhir/CodeSystem/withheld-reason", code: "authority-defect" }` (no
  `text`, no `display`: no free text a viewer would show; HL7's list-empty-reason is a preferred
  binding, and its codes do not fit: `withheld` means privacy or confidentiality, `unavailable`
  that the information cannot be obtained); and `text` with `status: generated`, whose div is the
  fixed constant `<div xmlns="http://www.w3.org/1999/xhtml"><p>Information withheld.</p></div>`,
  generated from that code alone. No entry, extension or other element. The reason and the
  evidence are in the provenance, not in the notice.
- **The document says it is partial.** `Composition.status` is `partial` instead of D9's `final`
  whenever a section is withheld, and only then: on every source (drawn, synthetic, authority, and the fixture and `healthcare-api` run sources,
  which also pass through the crosswalk) the gate and the crosswalk require `final`, or exactly `partial` if and only if the verified withheld list is non-empty (the status
  code system is a hierarchy, and `preliminary` is a kind of `partial`, so no other status is
  allowed), and the query service fails closed on any other status, in the record and in the EMA output. R5 defines it as "partial
  (e.g. initial, interim or preliminary)", of which "data in the composition may be incomplete or
  unverified", the nearest of its codes to a document with a section withheld; the binding is required to the
  status value set, and no profile narrows it (Composition-uv-epi's short text lists five other
  codes, but constrains nothing; EUEpiComposition and its specialisations checked). Our EMA List
  (`createEmaList`), which references our document, stays `current`: the List is current; the
  document it lists says it is partial. The FHIR Provenance
  (D12) carries the extension `https://khs.dev/fhir/StructureDefinition/ext-record-incomplete` with
  each withheld section's canonical key (`smpc.*`), its EMA code (its SPOR system named) and its
  confirmed defect kinds, so the query service, which reads the FHIR store,
  can return them; the run manifest's ingestion evidence, the ledger row (its BigQuery schema) and every
  query result carry `incomplete: true` and the codes.
- **Provenance.** The source record gains `withheldSections: [{ path, code, page, confirmed,
rejected }]` (checked against the request and the record); and `rendering: { gateVersion,
gateSha256, recordSha256 }` (checked: the record the lookup used; the run's evidence keeps its
  bytes), carried as the renderer note's R5 requires `renderEvidence`: when the authority is not
  `synthetic` or a section is withheld. Such a submission
  re-verifies in its own worker image, whose store and gate it used (the renderer note's R10). The record shapes the approved content of every authority import
  (the contacts acknowledged, and what is withheld), and is named in it.
- **Structuring decisions (D9).** `Composition.status` becomes a decision by rule
  (`status-from-publication` or `partial-when-withheld`); one `defaulted-by-rule` decision per
  withheld section for its `emptyReason` and one for its notice (`rule: withheld-section`).
- **Pages and provenance sections.** The section's page (fidelity §7) is empty and has no span.
  ADR 0002's bijection between coded sections and provenance entries holds: a withheld section
  has a `provenance.sections` entry with no spans (`SectionProvenanceSchema.spans` allows none only
  there), its `narrativeDivSha256` the notice's hash and its `normalizedTextSha256` the hash of the
  empty text, a sentinel fidelity §7 defines for a withheld section in both implementations
  (`computeNarrativeBinding` and `get_provenance` use it, never the notice's text); the fidelity
  counts are `sectionsChecked` = `sectionsMatched` + `sectionsWithheld` (the structural rule that
  replaces `sectionsMatched` = `sectionsChecked`; `sectionsWithheld` is 0 for every other source),
  the binding entry of a withheld section carrying `status: withheld` beside the sentinel,
  and the gate's "missing provenance", "orphan" and text-hash checks read the entry as such. ADR
  0002's invariant 3, the binding recomputed from the Bundle, takes the withheld list as its second
  input. A synthetic withheld
  section carries the same notice; D7's rule that every synthetic narrative says "not for clinical
  use" does not apply to it, since it is not narrative (W5).

### W4. T5's evidence and a withheld section

T5 waives an underlined `+` on evidence from the same document's plain use of the token. A
withheld section gives no evidence: the request's list is known before T runs, and the first pass
skips it. A section that needed evidence only from a withheld section then refuses; withholding A
can make B refused, and B can be withheld only on its own defect. Every section that gives
evidence is carried, and every carried section must pass the gate, so the evidence is drawn and
judged. For Imatinib Teva nothing changes: 5.1 gives no evidence (T refuses it), and 4.2's
evidence comes from 4.1, 4.5, 4.8 and 5.2, all carried.

### W5. The fidelity contract recognises a withheld section (a minor version)

Today the verifier collects every coded section with `text` (`collectNarrativeSections`), so a
notice would be refused as narrative without provenance. `fidelity-norm/3.2.0`:

- **Who decides.** The document gate's section walk (`canonical-submission.ts`) decides which
  sections are withheld, from the recomputed provenance of an `authority-publication` source, and
  passes that list to the verifier as an explicit input (`withheld`); for every other source the
  list is empty. The verifier does not infer it from the Bundle.
- **The rule.** A listed section must be exactly W3's: the one coding, the notice constant byte for
  byte, `text.status` `generated`, and nothing else. Anywhere else, on any source, our
  `emptyReason` code, any `emptyReason`, or the notice refuses; and on an `authority-publication`
  source, whose importer writes every narrative `additional`, a `generated` narrative anywhere
  but a listed section refuses (a drawn source's narratives are `generated`, as today); the gate's section walk enforces this (the
  verifier's input holds only the listed sections' paths and divs), and the crosswalk refuses
  `emptyReason` anywhere it is not given.
- **The report.** `SectionStatus` gains `withheld`; `summary` gains `withheld`; the report's version
  moves (its `reportVersion` literal), withheld sections are bound in `narrativeBindingSha256`, and
  the report's `status` is `passed` when every section not withheld is verified. The provenance's
  and the run manifest's fidelity counts gain `sectionsWithheld`. `unverifiedTextIssues` and the
  synthetic check accept the notice only on a listed section.
- **Both implementations.** The TypeScript verifier and the Zone A port, with new golden vectors
  and differential cases; ADR 0003's scope ("every section that carries the code and a
  `text.div`") amended to exclude a withheld section; `AGENTS.md`'s fidelity-version reference
  updated.

### W6. Everything downstream shows the gap

- **The EMA output** (`src/fhir/transform.ts`): the crosswalk is given the verified withheld list
  (never inferred from the Bundle), carries a withheld section's `emptyReason` and notice and the
  Composition's `partial`, and refuses `emptyReason` anywhere else. Its mandatory-narrative check
  does not count the notice as narrative: a withheld section passes it only as withheld.
- **The query service**: every tool reads the version it answers from whole (`loadDocument`), and
  takes `incomplete` from it: a version is incomplete when its `Composition.status` is `partial`,
  and its withheld sections are those carrying our `emptyReason` code; `partial` without such a
  section, or such a section without `partial`, fails closed. Never from the latest Provenance by
  write order; for the current version, `get_provenance` cross-checks the Provenance's extension,
  and a disagreement fails closed. `get_section` on a withheld section returns a new outcome,
  `section-withheld`, a validated success variant of its output (not an error, which the agent treats
  as unavailable), with its confirmed defect kinds for the current version only when the
  Provenance's extension agrees with the version read (the same cross-check as `get_provenance`;
  otherwise, and for a superseded version, without them), never the notice as document content; `verify_quote`
  reports the withheld sections as not searched, so a `no-match` on an incomplete record never
  reads as absence; `get_provenance` returns the withheld sections; every result about the
  document carries `incomplete`; `get_provenance` also returns the record's hash and, per section,
  the contacts acknowledged and the attested requester (the renderer note's R10).
- **The agent**: a question about a withheld section is answered with the fact that it is withheld
  and why, never from memory or another section; every answer from an incomplete record carries a
  line saying so, rendered deterministically from `incomplete`, not written by the model.
- **Before persistence.** D1's dry run is lifted for a publication with a withheld section only once
  the query service and the agent do the above, and for any request that withholds a section or
  acknowledges a contact (the renderer note's R5) only once it is bound to an attested person: roadmap
  item 2's signer signs a request statement over the request's `approvedContentSha256` (which covers
  `renderEvidence`, `acknowledgedContacts` and `withheld`), in the approver map's `content-reviewer`
  role, which alone may acknowledge a contact or withhold a section; the requester should not be the
  person who proposed the record (the renderer note's R1), a separation that is a stated residual
  while the project has one named person (D2 names who requests an import; withholding is a decision about content, so its
  requester must be an identity, not D8's placeholder): PR 5's preconditions.
- **The round trip** (PR 4) reports the withheld section as an expected, recorded difference.
- **The defect is recorded** in `docs/design/qrd-conformance-check.md` with its measurements, as
  a finding reportable to the authority; the person reviews the render build's captures of the
  attested record (the record's pull request shows only a pre-check's), and the request names what
  was reviewed (W1.1).
- **Forward pointers.** `docs/architecture.md` (its statements that `emptyReason` and a mandatory
  leaf without narrative are refused), `docs/design/authority-import-contract.md` (D1, D2, D3, D4,
  D6, D7, D8, D9, D10, D12, D13), `docs/design/approval.md`,
  `docs/design/epi-mcp-query-service.md` and `docs/design/verifiable-answers.md` gain a line
  pointing here, in the change that implements each part.

### W7. Versions

- `CanonicalSubmission` 3.0.0 (a major, ADR 0002's rule: fields required for an authority import, and
  enums the gate branches on): the request's `renderEvidence`, `acknowledgedContacts` (the renderer
  note's R5) and `withheld`, the source record's `withheldSections` and `rendering`,
  `Composition.status` `partial`, `SectionProvenanceSchema.spans` allowing none for a withheld
  section. `schemaVersion` is a literal, so a 2.0.0 submission does not read as 3.0.0: 2.0.0
  authority imports (dry runs only; none approved) are re-imported, and 2.0.0 drawn and synthetic
  submissions are refused as 1.0.0's were at 2.0.0.
- `QUERY_TOOLS_VERSION` 3.0.0, a major (`incomplete` required; `get_section`'s `section-withheld`
  variant with its defect kinds; `get_provenance`'s withheld sections, record hash, contacts
  acknowledged and requester); the agent's turn record's next major (`AGENT_TURN_VERSION`, a required
  `incomplete`); the `ingestion-provenance` schema's published `$id`, its next major, from a constant
  rather than the string it is today (it stayed 1.0.0 through 2.0.0's change), and each frozen 2.0.0
  copy with its own `$id`; the fidelity report's version wherever it is written (the index, the
  `reportVersion` literal, the TypeScript and Zone A verifiers, the Zone A model, the golden vectors),
  made one constant per implementation.
- The mapping's `mappingVersion` and StructureMap move a minor version, since the crosswalk now carries
  a withheld section's `emptyReason` and notice and checks `Composition.status` (its precedent: 1.2.0
  to 1.3.0).
- `fidelity-norm/3.2.0` and the fidelity report's next major (W5: `SectionStatus` `withheld`,
  `summary.withheld`, `sectionsWithheld`); the run manifest's 3.0.0 (the renderer note's R10: the
  2.0.0 ingestion block frozen with its literal in `AnyRunManifestSchema`, with deep copies of the
  2.0.0 `AuthorityFetch`, `IngestionFidelity` and `Approval` schemas, read in a test with an authority
  import's dry-run manifest); the ledger's schema; the
  importer's version (D10's lock). Generated schemas and Zone A models are regenerated.
- `AGENTS.md`: "An authority import may carry a mandatory section as withheld only under
  `docs/design/authority-import-withheld.md` (reviewed, measured evidence of the authority's defect, never a
  safety section 4.2–4.9, the record marked incomplete)."

## What this is not

- Not a repair: nothing the authority wrote is changed or carried in another form.
- Not a way to accept a section: a withheld section is never answerable, quotable or proven.
- Not a cover for our own limits: only lower-bound evidence of the drawing can justify it.
- Not automatic: a person lists each withheld section, and the import refuses if the list is
  wrong in either direction.

## Stated residuals

- A reader of our record sees "Information withheld." where the authority's page draws 5.1; the
  provenance says why.
- The finding is of the FHIR div drawn with its inline styles in metric-compatible fonts; the
  EMA's viewer applies its own stylesheet, which the gate does not model.
- Whether an incomplete record may be entitled to the query service is PR 5's decision, with the
  incompleteness in every answer.
- A sound carried section whose only defect a person rejects (a split number, "12" beside ".5") is
  still a failure: it can neither be acknowledged nor withheld, and the label refuses (false, and
  closed).
- A synthetic publication with a withheld section can be imported only against a test store: the
  render build draws only publications pinned in `labels/`, so the deployed gate refuses it.
- The record named in a request changes whenever any section's outputs, the pins (3c-E's export
  among them) or the gate change; each change needs a new review and a new request, in each
  environment, before the publication can be imported again, withheld section or not (a
  re-import after a normalisation change included, ADR 0005 decision 4).

## Verification

- The importer (against a test store): a synthetic publication with one section whose record shows a
  defect imports with it withheld; a request that rejects every defect, lists a defect both confirmed
  and rejected, or names a defect under another kind, refuses; a listed section with only a contact, a failure that is not a defect, a refusal
  of ours, or nothing, refuses (`withheld-section-not-shown`); a request whose confirmed and rejected locations differ from
  the record's refuses (`withheld-evidence-changed`); an unlisted failing section refuses; a
  listed safety section, a section with subsections, a repeat or an out-of-order list refuses at
  `tree`; a document-level refusal refuses whatever is listed; a withheld section's picture is not
  fetched; a section whose only T5 evidence is withheld refuses; listing 4.2 Posology refuses.
- The gate's recomputation reproduces the withheld list, the defects and the status.
- The review tool (delivered with 3c-D, which first requires reviewed evidence) refuses a tampered capture, index or attestation, and one of
  another environment.
- `fidelity-norm/3.2.0`'s vectors and differential cases; the verifier refuses the notice, an
  `emptyReason` or a `generated` narrative on any other source or section.
- The EMA output passes official validation (`cmp-1`, `cmp-2`, `partial`) with a withheld section.
- The Imatinib Teva tablets SmPC imports in dry run with 5.1 withheld on `cells-run-together`
  (table 8, from 320 to 419 px, and at 671 px, at ratio 1; and at the other ratios as the pinned image measures them), once
  the renderer gate draws the document and a person has reviewed the record; no carried section of any pinned label shows a defect; the Greek letters of 4.1, 5.1
  and 5.2 are bounded, not refused.

## Reviews

1. **First independent review** (2026-09-25). High: eligibility judged a section "alone", so a
   section passing only on T5's evidence (4.2 Posology) could be withheld; nothing recognised a
   withheld section; any refusal of ours qualified, recorded as the authority's defect; a withheld
   section could give T5 evidence. Medium: 5.1's first refusal is the pictures stage's; where the
   renderer evidence lives; versions; the query service would present the notice as content; the
   checks' stages. Fixed in the second draft.
2. **Second independent review** (2026-09-25). High: the record's `pass` still let our own
   refusals justify withholding; the Composition still said `final`. Medium: a failed conservative
   check is not evidence of a defect (and in a section T refuses, checks that rest on T's allowlist
   fail by construction); the lookup keyed a withheld section by a T(div) hash it does not have;
   recognition placed in the verifier, which has no source kind; HL7's `withheld` means privacy; a
   parent section could be withheld over carried subsections; synthetic withholding had no
   evidence; the record shaped approved content without being named in it, and reviewers saw no
   drawing. Fixed in this draft: eligibility is no refusal of ours and at least one lower-bound
   defect (R8's `defects`), leaf sections only; `partial`; recognition by the document gate from
   the recomputed provenance; our own `authority-defect` code; the record named in the source
   record; captures published for review; synthetic withholding needs a record.
3. **Third independent review** (2026-09-25). High: the defects were defined on character
   rectangles, which are content areas larger than the ink, so every underline, a tight table's
   border and a first line at the page's top were "defects": a sound section (2, 3, 6.1) could be
   withheld, and 4.8 and Posology, with false defects, would have refused the label. Medium: R5 and
   W1 stated the carried-section rule differently; a section T refuses got no font check; ADR
   0002's invariants 4 and 6 were changed without being amended. Low: the recognition rule placed
   where it cannot run; the named record not kept; `get_section` could not return the defects;
   `partial` misquoted; five figures of 5.1 corrected. Fixed in this draft: defects are measured on
   the pixels Chrome paints (the renderer note's R8) and are failures too; the font check on every
   section; ADR 0002 amended with the provenance entry and counts; the rule enforced by the gate's
   walk; the record kept in the run's evidence; the Provenance extension carries defect kinds; the
   requester of a withholding bound to an attested identity before PR 5.
4. **Fourth independent review** (2026-09-25). High: the pixel defects still admitted sound
   drawings (a "j", an italic "f" or a ")" brushing an unpadded border) and missed 5.1's real
   defect, table 8's cells whose text runs together with abutting advances while their nearest
   pixels stand an ordinary letter-space apart; and 5.1's claimed `line-through-text` at 561 px is
   one serif pixel. Medium: Greek letters (α, β, μ in 4.1, 5.1, 5.2) would have been a refusal of
   ours; ADR 0005's amendment stale; "a `generated` narrative refuses on any source" would refuse
   every drawn submission; `incomplete` read from the latest Provenance, not the version read. Low:
   the withheld binding's sentinel, `spans`' minimum, the counts' rule, invariant 3, the stage
   order, the `partial` quote, our List. Fixed in this draft, with the renderer note's R8:
   `cells-run-together` on exact horizontal advances and `line-through-letter` through a glyph's
   body, with seeded controls; Greek bounded; the `generated` rule scoped to authority sources;
   `incomplete` from the version read; the bookkeeping stated.
5. **Fifth independent review** (2026-09-25). Medium: check 2's rounded-out bounds failed descenders
   a pixel above their own cells' borders in 4.2 and 4.8, safety sections that can never be
   withheld, so the label could not have been imported (the renderer note's fifth draft judges a
   glyph's own frame on pixels); the evidence "reviewed" was bound to nothing, so a later record
   could satisfy a request made against another. Low: a split number is the same geometry as a
   run-together; figures of table 9 and the 561 px "n"; shared definitions; where the defects are
   computed and what `incomplete` is; a withheld section's pictures; ADR 0002's invariant 10, ADR
   0003's wording and the forward pointers; the list's order. Fixed in this draft: the request names
   the record reviewed and the defects confirmed, and the lookup refuses a change; the reviewer
   rejects a split number; the rest as found.
6. **Sixth independent review** (2026-09-25). Medium: four of the renderer gate's checks still
   decided on rounded-out bounds and failed the safety sections 4.2 and 4.8 by construction; nothing
   named who produces a record, so the drawing a person reviews was not the attested one. Low: one
   record per document named per entry; the record's churn unstated; the binding entry's status;
   pointers to D7 and D10; the roadmap's "leaves the page"; "abut" at layout-unit precision. Fixed in
   this draft, with the renderer note's seventh: R4's principles decide on pixels and exact advances;
   the propose mode, and the review of the render build's captures of the attested record; the record
   named once per request; the churn stated as a residual; the rest as found.
7. **Seventh independent review** (2026-09-25). Medium: the captures a person reviews were not bound
   to the record named (stored by their own hash, after signing, and lost for good if their upload
   failed); confirmation was per kind while the rule it applies is per instance, and a record's
   defects had no location. Low: `withheldEvidence`'s rules and version; W6 pointed at a
   pre-check's captures; defects for a superseded version; `partial` without a withheld section;
   the delivery order; the page's top edge; which defect parameters bind; the capsules' 5.1; code
   systems named. Fixed in this draft, with the renderer note's eighth: captures written before
   signing under the record's hash and named by the attestation; defects located, confirmed or
   rejected one by one; the rest as found.
8. **Eighth independent review** (2026-09-25). Medium: a defect's location had no defined identity
   (the kind was not part of it, the index space unstated, confirmed and rejected neither disjoint
   nor ordered). Low: the captures bound only through an attestation neither the request nor the
   lookup names; `partial`'s child codes; `SourcePath` for `SectionPath`; `off-page`'s wording; a
   withheld section's pictures; synthetic withholding against the deployed gate; the "reviewed"
   condition missing from ADR 0005 and the roadmap; `docs/architecture.md`. Fixed in this draft, with
   the renderer note's ninth: defects identified by `{ kind, location }`, typed and sorted, confirmed
   and rejected disjoint and exhaustive; `withheldEvidence` names the captures, which the lookup
   checks; `final` or exactly `partial`; the rest as found. The renderer note's eighth review found
   4.2's raised 9 touching the line above; the owner's decision (a stated exception, the renderer
   note's P9) keeps the label importable.
9. **Ninth independent review** (2026-09-25). Medium: a defect's index space was undefined in a
   section T refuses, the only kind withheld; P9's contacts were "shown to the reviewer" by no
   mechanism. Low: the environment unchecked; the captures' sidecar unstated in the renderer note;
   re-attestation after a key's revocation; which widths are captured; the review tool undelivered;
   `get_section`'s kinds from the latest Provenance; `off-page` without ink; P9's bound from withheld
   sections; "a contact" for a touch; the run sources; 5.2's evidence. Fixed in this draft, with the
   renderer note's tenth: every authority import request names the evidence reviewed
   (`renderEvidence`, replacing `withheldEvidence`) and acknowledges each contact, which the owner
   decided (2026-09-25); the judge's own index space; the rest as found.
10. **Tenth independent review** (2026-09-25). Medium: a contact's identity had no section path, and a
    withheld section's contacts were unsettled; acknowledging a contact, a judgement of content,
    needed no attested identity. Low: when `renderEvidence` is required; statements left from when
    only withheld imports named the record; the review tool's delivery; the sidecar's choice among
    key versions; acknowledged contacts in no consumer; the contract's version against ADR 0002's
    rule; amendment lists; P9's touch; HTML-to-XML character mapping. Fixed in this draft, with the
    renderer note's eleventh (which, on the owner's decision of the same day, makes every contact the
    gate cannot prove harmless an acknowledged one): contacts identified by section; an attested
    requester for any acknowledgement; `rendering` in every authority import; the rest as found. The
    contract's version: `CanonicalSubmission` 3.0.0, a major, since new fields are required for an
    authority import and 2.0.0 submissions do not read as it (ADR 0002's rule).
11. **Eleventh independent review** (2026-09-25). Medium: two cells' numbers 0.19 px apart ("64:2246:"
    in 5.1's table 9) were only a contact, which a person could acknowledge; the run manifest's
    literal `contractVersion` made 3.0.0 unreadable for 2.0.0 manifests under a "minor" bump; ADR 0002
    was stale on the contract and on who judges content. Low: failures still described as conservative
    checks; `renderEvidence`'s scope stated three ways; withheld-only leftovers; the review tool; the
    acknowledgements in no consumer; amendment lists; PR 5's dependence on an attested identity for
    every real label; a reason code; W7's list. Fixed in this draft, with the renderer note's twelfth:
    cells closer than a stated gap are a failure; the run manifest 3.0.0 with 2.0.0 frozen; ADR 0002
    amended; failures and contacts in class terms; the rest as found.
12. **Twelfth independent review** (2026-09-25). Medium: the contact cap counted the withheld section's
    contacts and had no reason; P8(a)'s "letter" was open (a cedilla fusing into a line). Low: the
    requester's role had no source; versions (query tools, the agent's turn record, the provenance
    schema's `$id`, the fidelity report's two places); the frozen 2.0.0 manifest needs deep copies;
    stale "free of overlapping" and "conservative failure"; widths per ratio; "every real label";
    "attested identity" undefined and item 2 missing from the roadmap's dependencies; P8(b)'s scope;
    formatting. Fixed in this draft, with the renderer note's thirteenth: the cap over carried
    sections with `renderer-contacts-exceeded`; P8(a) a closed list; the Provenance names the attested
    requester; the versions listed; the rest as found. An attested identity is one the approval of
    roadmap item 2 authenticates and records; PR 5 depends on it.
13. **Thirteenth independent review** (2026-09-25). Medium: the attested requester had no design
    behind it in roadmap item 2 (approval.md designs a Type 2 approval, not a request) and no role.
    Low: the fidelity report's version written in more places than listed; the provenance schema's
    `$id`; the agent turn record's major; `section-withheld` as an error the agent reads as
    unavailable; the unstated residual about opened captures; AGENTS.md's other rule; the mapping's
    version; the cap's home; pointers; the T note's pointer; a rejected split number; query tools'
    reasons; formatting. Fixed in this draft: item 2's signer signs a request statement over the
    approved content in the `content-reviewer` role, the only role that may acknowledge or withhold,
    separation from the proposer a stated residual while the project has one person; the rest as
    found.
