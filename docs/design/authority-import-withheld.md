# A withheld section: importing a publication with an authority's defect recorded in place (roadmap 3a, PR 3c)

- Status: proposed, 2026-09-25; second draft after the first design review
- Owner decisions (2026-09-25): withhold the Imatinib Teva tablets SmPC's 5.1 and import the
  other 31 sections; a section may be withheld only on reviewed, measured evidence of the
  authority's defect, the whole record is marked incomplete everywhere it is read, and the safety
  sections 4.2 to 4.9 are never withheld
- Decides: how an authority import carries a section it cannot accept, instead of refusing the
  whole publication
- Amends: `AGENTS.md` ("fail closed on … mandatory QRD sections", a scoped exception); ADR 0003
  (the verifier's scope: a withheld section); ADR 0005 decision 3 and its consequence;
  `docs/design/authority-import-contract.md` D2, D3, D4, D9, D10, D12, D13;
  `docs/fidelity-normalization.md` §7 (a new minor version, W5)
- Related: ADR 0002, `docs/design/authority-import-t.md` (T5's evidence),
  `docs/design/authority-import-renderer.md` (the evidence), `docs/design/qrd-conformance-check.md`

## What this is for

Section 5.1 of the EMA's Imatinib Teva film-coated tablets SmPC is misleading when drawn, not
only outside T's rules. It was measured in Chrome with inline styles only, at widths from 280 to
1 000 CSS px:

- The at-risk tables under the two Kaplan-Meier figures put up to three values in one cell. At
  800 px, table 8's "182:8" and "177:12" stand 2.33 px apart (0.19 em). At 360 px, 19 of its cell
  pairs touch, drawing "10:842:840:84", and table 9 merges ("64:2246:2327:25") at 537 px and
  below. The EMA's PDF draws each value under its own axis tick.
- The row heading "At-risk : Events" has `margin-left: -14.6pt`; at 580 px and below "Events"
  wraps to a line starting 19.45 px left of the table, off the page at 360 px ("vents").
- In table 2, twelve paragraphs with a −1 pt right margin in unpadded, bordered cells cross the
  cell edge by up to 1.28 px at some widths (at 561 px the border runs through the "n" of
  "evaluation").

ADR 0005 decision 3 refuses such a publication. Of the EMA ePI API's public corpus (268 Bundles;
English SmPCs for five centrally authorised products) no English SmPC gets past T, pictures and
structure except this label, blocked only by 5.1. The owner decided that the other 31 sections
are imported with their full proof and 5.1 is carried as withheld: its heading and code, none of
the authority's content, and the gap stated wherever the record is read.

## Decisions

### W1. What may be withheld: a measured defect of the authority's drawing, never a safety section

A section may be withheld only when all hold:

1. **A person asks.** The import request (D2) gains `withheld`: a list of `{ path, code }`, with
   `path` the section's `SourcePath` and `code` its code as served. The request is approved content
   (D2), covered by the approval's `approvedContentSha256` (D8).
2. **The authority's drawing is measured misleading.** The renderer gate's record for the
   document (`docs/design/authority-import-renderer.md`, reproduced in CI from reviewed code)
   shows the section's drawing, as the authority serves it, failing at least one of the gate's
   drawn checks (R4: overlap, a line under a sign, the gap between cells, the frame, reach,
   contrast, folds), in the whole document as drawn. A refusal of ours (a font we cannot draw, a
   rule of T's stricter than the drawing, a picture we cannot fetch, a scanner grammar) is not a
   defect of the authority's and is never grounds: such a section refuses the import, as today.
   So withholding rests only on evidence a reviewer can see and CI reproduces, never on the
   importer's own caution.
3. **Not a safety section.** The section is not 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8 or 4.9
   (`smpc.4.2` to `smpc.4.9` in the mapping) and not under one: a defect there refuses the whole
   label (the owner's decision).
4. **Not a document-level failure.** Bytes, shape, binding, tree and titles (D1, D4, D5) still
   refuse the import whatever is listed: the publication's identity, tree and headings are what
   make the other sections' proof mean anything.

And both directions are enforced: a listed section whose drawing passes every check refuses the
import (`withheld-section-accepted`), and a section whose drawing fails and is not listed refuses
it (`renderer-evidence-failed`, as for any carried section). Withholding is never automatic and
never silent.

### W2. Where the checks sit (D10)

- At `tree`, once the sections are placed: each listed path names a section with that code
  (`withheld-section-unknown`), none is listed twice (`withheld-section-duplicate`), none is a
  safety section or under one (`withheld-section-not-permitted`).
- At `pictures`, `narrative` and `record`: a listed section is skipped. Its pictures are neither
  checked nor fetched, T and the scanner do not read it, and it gives T5 no evidence (W4).
  Unlisted sections are checked as today, stopping at the first refusal.
- At `rendering`: W1.2 in both directions, from the record, in the renderer note's lookup.

### W3. What the record carries

- **Its place.** The section keeps its place in the tree, its title and its code (D4). Its
  subsections are judged on their own; a withheld section's subsections are not withheld by it,
  and a withheld subsection does not withhold its parent.
- **No authority content.** Nothing of the section's div enters the record: no text, no picture,
  no fragment.
- **A fixed notice.** FHIR's `cmp-1` requires a section to hold text, entries or subsections. A
  withheld section carries `emptyReason` `withheld` (HL7 list-empty-reason: "the information has
  been withheld", which can be a policy decision; this is ours, on the authority's defect) and
  `text` with `status: generated` whose div is a fixed constant generated from that code alone:
  `<div xmlns="http://www.w3.org/1999/xhtml"><p>Information withheld.</p></div>`. Nothing else:
  no entry, extension or other element. The reason and the evidence are in the provenance (W3,
  below), not in the notice.
- **The record is incomplete, and says so.** The provenance's source record gains
  `withheldSections: [{ path, code, page, finding }]`, with `path` and `code` checked against the
  request and `finding` recomputed as `{ stage: "rendering", checks: [names] }` from the record
  (the record's hash is in the run manifest, not the submission: the renderer note's R10). The
  FHIR Provenance (D12), the run manifest's ingestion evidence, the ledger row and every query
  result carry `incomplete: true` and the withheld sections' codes.
- **Structuring decisions (D9).** One `defaulted-by-rule` decision per withheld section for its
  `emptyReason` and one for its notice (`rule: withheld-section`).
- **Pages.** The section's page (fidelity §7) is empty and has no span.

### W4. T5's evidence and a withheld section

T5 waives an underlined `+` on evidence from the same document's plain use of the token. A
withheld section gives no evidence: the request's list is known before T runs, and the first pass
skips it. A section that needed evidence only from a withheld section then refuses (the cascade
is stated: withholding A can make B refused, and B can be withheld only on its own drawn
failure, W1.2). Evidence sections must also be carried and pass the renderer gate, which the
lookup checks. For Imatinib Teva nothing changes: 5.1 gives no evidence (T refuses it), and 4.2's
evidence comes from 4.1, 4.5 and 4.8, all carried.

### W5. The fidelity contract recognises a withheld section (a minor version)

Today the verifier collects every coded section with `text` (`collectNarrativeSections`), so a
notice would be refused as narrative without provenance. `fidelity-norm/3.2.0` adds one status:

- A section is **withheld** exactly when the submission's source is `authority-publication`, its
  path is in the recomputed `withheldSections`, its `emptyReason` is exactly `withheld`, its div
  is byte-equal to the notice constant, and it holds nothing else. On every other source, and
  anywhere else, an `emptyReason`, a `generated` narrative or the notice refuses.
- The report counts withheld sections and binds them in `narrativeBindingSha256`; the verifier
  does not compare the notice with any page, and `unverifiedTextIssues` and the synthetic check
  accept the notice only on a withheld section.
- New golden vectors and differential cases (TypeScript and the Zone A port), and ADR 0003's
  scope ("every section that carries the code and a `text.div`") amended to exclude a withheld
  section.

### W6. Everything downstream shows the gap

- **The EMA output** (`src/fhir/transform.ts`): the crosswalk is given the verified withheld list
  (never inferred from the Bundle), carries a withheld section's `emptyReason` and notice, and
  refuses `emptyReason` anywhere else. Its mandatory-narrative check does not count the notice as
  narrative: a withheld section passes it only as withheld.
- **The query service** (`get_section`, `verify_quote`, `get_provenance`): a withheld section
  returns a new outcome, `section-withheld`, with its finding, never the notice as document
  content; `verify_quote` matches nothing in it; every result about the document carries
  `incomplete`. `QUERY_TOOLS_VERSION` and the agent's vendored contracts move with it.
- **The agent**: a question about a withheld section is answered with the fact that it is
  withheld and why, never from memory or another section; every answer from an incomplete record
  says so.
- **Before persistence.** D1's dry run is lifted for a publication with a withheld section only
  once the query service and the agent do the above (PR 5's precondition).
- **The round trip** (PR 4) reports the withheld section as an expected, recorded difference.
- **The defect is recorded** in `docs/design/qrd-conformance-check.md` with its measurements, as
  a finding reportable to the authority.

### W7. Versions

- `CanonicalSubmission` 2.1.0: the request's `withheld` and the source record's
  `withheldSections`. `schemaVersion` is a literal, so a 2.0.0 submission does not read as 2.1.0:
  2.0.0 authority imports (dry runs only; none approved) are re-imported, and readers that must
  keep reading old documents (the run manifest's `contractVersion`, D13) accept both, as
  `AnyRunManifestSchema` does for manifests.
- `fidelity-norm/3.2.0` (W5); the run manifest's minor version (with the renderer note's R10);
  `QUERY_TOOLS_VERSION`; the importer's version (D10's lock). Generated schemas and Zone A models
  are regenerated.
- `AGENTS.md`'s rule gains: "An authority import may carry a mandatory section as withheld only
  under `docs/design/authority-import-withheld.md`: measured evidence of the authority's defect,
  never a safety section (4.2–4.9), the record marked incomplete."

## What this is not

- Not a repair: nothing the authority wrote is changed or carried in another form.
- Not a way to accept a section: a withheld section is never answerable, quotable or proven.
- Not a cover for our own limits: only the authority's measured drawing can justify it.
- Not automatic: a person lists each withheld section, and the import refuses if the list is
  wrong in either direction.

## Stated residuals

- A reader of our record sees "Information withheld." where the authority's page draws 5.1; the
  provenance, not the notice, says why.
- Whether an incomplete record may be entitled to the query service is PR 5's decision, with the
  incompleteness in every answer.

## Verification

- The importer: a synthetic publication with one section whose drawing fails imports with it
  withheld; a listed section that draws correctly refuses (`withheld-section-accepted`); an
  unlisted failing section refuses; a listed safety section refuses at `tree`; a document-level
  refusal refuses whatever is listed; a withheld section's picture is not fetched; a section whose
  only T5 evidence is withheld refuses; listing 4.2 Posology refuses.
- The gate's recomputation reproduces the withheld list and findings.
- `fidelity-norm/3.2.0`'s vectors and differential cases; the verifier refuses the notice or an
  `emptyReason` on any other source or section.
- The EMA output passes official validation (`cmp-1`, `cmp-2`) with a withheld section.
- The Imatinib Teva tablets SmPC imports in dry run with 5.1 withheld, its finding the gate's
  failed checks, once the renderer gate draws the document.

## Reviews

1. **First independent review** (2026-09-25). High: eligibility judged a section "alone", so a
   section passing only on T5's evidence from other sections (4.2 Posology) could be withheld;
   nothing recognised a withheld section (the verifier, the document gate and the crosswalk read
   any `text`, and the note's "no vector changes" was false); any refusal of ours qualified and
   would be recorded as the authority's defect, and a section drawing nothing could be withheld
   against `AGENTS.md`; a withheld section could give T5 evidence. Medium: 5.1's first refusal is
   the pictures stage's, not a drawn defect; where the renderer evidence lives; the versions
   (`schemaVersion` is a literal; the manifest, query tools and agent move too); the query service
   would present the notice as content; the checks' stages and D9's decisions unstated. Fixed in
   this draft: withholding rests only on the renderer gate's measured failure of the authority's
   drawing, never a safety section (the owner's decision); `fidelity-norm/3.2.0` recognises a
   withheld section by a closed rule; `emptyReason` `withheld` and a notice generated from it
   alone; T5's evidence excludes withheld sections; every consumer shows the gap and the record is
   marked incomplete; stages, versions and D9 stated.
