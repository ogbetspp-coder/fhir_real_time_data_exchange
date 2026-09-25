# A withheld section: importing a publication with an authority's defect recorded in place (roadmap 3a, PR 3c)

- Status: proposed, 2026-09-25; third draft, after two design reviews
- Owner decisions (2026-09-25): withhold the Imatinib Teva tablets SmPC's 5.1 and import the
  other 31 sections; a section may be withheld only on reviewed, measured evidence of the
  authority's defect, the whole record is marked incomplete everywhere it is read, and the safety
  sections 4.2 to 4.9 are never withheld
- Decides: how an authority import carries a section it cannot accept, instead of refusing the
  whole publication
- Amends: `AGENTS.md` ("fail closed on … mandatory QRD sections", a scoped exception); ADR 0003
  (the verifier's scope); ADR 0005 decision 3 and its consequence;
  `docs/design/authority-import-contract.md` D2, D3, D4, D6, D7, D9, D10, D12, D13;
  `docs/fidelity-normalization.md` §7 (a new minor version, W5); the query service's and the
  agent's designs (`docs/design/epi-mcp-query-service.md`, `docs/design/verifiable-answers.md`)
- Related: ADR 0002, `docs/design/authority-import-t.md` (T5's evidence),
  `docs/design/authority-import-renderer.md` (the evidence), `docs/design/qrd-conformance-check.md`

## What this is for

Section 5.1 of the EMA's Imatinib Teva film-coated tablets SmPC is misleading when drawn, not
only outside T's rules. It was measured in Chrome, with the FHIR div's inline styles only, at
widths from 280 to 1 000 CSS px:

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

### W1. What may be withheld: a leaf section whose drawing is shown unsound, never a safety section

A section may be withheld only when all hold:

1. **A person asks.** The import request (D2) gains `withheld`: a list of `{ path, code }` in the
   document's pre-order, `path` the section's `SourcePath` and `code` its code as served. The
   request is approved content (D2), covered by `approvedContentSha256` (D8).
2. **Its drawing is shown unsound, by evidence that is not ours.** The renderer gate's record for
   the document (`docs/design/authority-import-renderer.md` R8), which the worker image's own code
   reproduced, holds for the section:
   - **no refusal of ours** at any width, ratio or mode: nothing the gate could not judge (a model
     difference, a missing glyph, a script it cannot bound, a calibration failure, an XML
     `parsererror`);
   - **at least one defect**: lower-bound evidence from Chrome's own rectangles, with no widening
     and no threshold of ours: two cells' text touching or overlapping (`cells-touch`), text off
     the page (`off-page`), or a drawn line through text (`line-through-text`), none resting on a
     broken picture's box.

   A failure of the gate's conservative checks (a 0.25 em gap, a 0.5 px clearance, a contrast
   margin) is not a defect: it shows the gate cannot prove the drawing sound, not that it is
   unsound, and such a section refuses the import, as today. So is every refusal of ours (a font
   we cannot draw, a rule of T's stricter than the drawing, a picture we cannot fetch, a scanner
   grammar). The finding is stated for what the gate draws: the FHIR div as published, with its
   inline styles only, in fonts metric-compatible with those it names.

3. **A leaf.** The section has no subsections (5.1 has none), so "information withheld" never
   stands over carried text.
4. **Not a safety section.** The section is not 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8 or 4.9
   (`smpc.4.2` to `smpc.4.9` in the mapping) and not under one: a defect there refuses the whole
   label (the owner's decision).
5. **Not a document-level failure.** Bytes, shape, binding, tree and titles (D1, D4, D5) still
   refuse the import whatever is listed.

Both directions are enforced: a listed section without a defect, or with a refusal of ours,
refuses the import (`withheld-section-not-shown`); a section with a failure or a defect that is not
listed refuses it (`renderer-evidence-failed`, as for any carried section). Withholding is never
automatic and never silent. For a synthetic publication, a withheld section needs a record too
(the renderer note's R5).

### W2. Where the checks sit (D10)

- At `tree`, once the sections are placed: each listed path names a section with that code
  (`withheld-section-unknown`), the list is in pre-order with no repeat
  (`withheld-section-duplicate`), and no listed section has subsections or is a safety section or
  under one (`withheld-section-not-permitted`).
- At `pictures`, `narrative` and `record`: a listed section is skipped. Its pictures are neither
  checked, fetched nor listed in `pictures` (D6), T and the scanner do not read it, and it gives T5
  no evidence (W4). Unlisted sections are checked as today, stopping at the first refusal.
- At `rendering`: W1.2 in both directions, in the renderer note's lookup.

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
- **The document says it is partial.** `Composition.status` is `partial` ("the document is
  incomplete") instead of D9's `final` whenever a section is withheld, in the record and in the
  EMA output; the profiles do not constrain it (Composition-uv-epi, EUEpiComposition and its
  specialisations checked). The FHIR Provenance (D12) carries the extension
  `https://khs.dev/fhir/StructureDefinition/ext-record-incomplete` with the withheld sections'
  codes; the run manifest's ingestion evidence, the ledger row (its BigQuery schema) and every
  query result carry `incomplete: true` and the codes.
- **Provenance.** The source record gains `withheldSections: [{ path, code, page, defects }]`
  (`path` and `code` checked against the request; `defects` recomputed from the record: the kinds
  found) and, when anything is withheld, `rendering: { gateVersion, recordSha256 }` (checked: the
  record the lookup used). The record thus shapes approved content only when something is
  withheld, and is then named in it.
- **Structuring decisions (D9).** `Composition.status` becomes a decision by rule
  (`status-from-publication` or `partial-when-withheld`); one `defaulted-by-rule` decision per
  withheld section for its `emptyReason` and one for its notice (`rule: withheld-section`).
- **Pages.** The section's page (fidelity §7) is empty and has no span. A synthetic withheld
  section carries the same notice; D7's rule that every synthetic narrative says "not for clinical
  use" does not apply to it, since it is not narrative (W5).

### W4. T5's evidence and a withheld section

T5 waives an underlined `+` on evidence from the same document's plain use of the token. A
withheld section gives no evidence: the request's list is known before T runs, and the first pass
skips it. A section that needed evidence only from a withheld section then refuses; withholding A
can make B refused, and B can be withheld only on its own defect. Every section that gives
evidence is carried, and every carried section must pass the gate, so the evidence is drawn and
judged. For Imatinib Teva nothing changes: 5.1 gives no evidence (T refuses it), and 4.2's
evidence comes from 4.1, 4.5 and 4.8, all carried.

### W5. The fidelity contract recognises a withheld section (a minor version)

Today the verifier collects every coded section with `text` (`collectNarrativeSections`), so a
notice would be refused as narrative without provenance. `fidelity-norm/3.2.0`:

- **Who decides.** The document gate's section walk (`canonical-submission.ts`) decides which
  sections are withheld, from the recomputed provenance of an `authority-publication` source, and
  passes that list to the verifier as an explicit input (`withheld`); for every other source the
  list is empty. The verifier does not infer it from the Bundle.
- **The rule.** A listed section must be exactly W3's: the one coding, the notice constant byte for
  byte, `text.status` `generated`, and nothing else. Anywhere else, on any source, an `emptyReason`,
  a `generated` narrative or the notice refuses.
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
- **The query service**: `get_section` on a withheld section returns a new outcome,
  `section-withheld`, with its defects, never the notice as document content; `verify_quote`
  reports the withheld sections as not searched, so a `no-match` on an incomplete record never
  reads as absence; `get_provenance` returns the withheld sections; every result about the
  document carries `incomplete`. `QUERY_TOOLS_VERSION` and the agent's vendored contracts move.
- **The agent**: a question about a withheld section is answered with the fact that it is withheld
  and why, never from memory or another section; every answer from an incomplete record says so.
- **Before persistence.** D1's dry run is lifted for a publication with a withheld section only once
  the query service and the agent do the above (PR 5's precondition).
- **The round trip** (PR 4) reports the withheld section as an expected, recorded difference.
- **The defect is recorded** in `docs/design/qrd-conformance-check.md` with its measurements, as
  a finding reportable to the authority; the record's pull request publishes the defects' captures
  for its reviewer (the renderer note's R1).

### W7. Versions

- `CanonicalSubmission` 2.1.0: the request's `withheld`, the source record's `withheldSections` and
  `rendering`, `Composition.status` `partial`. `schemaVersion` is a literal, so a 2.0.0 submission
  does not read as 2.1.0: 2.0.0 authority imports (dry runs only; none approved) are re-imported,
  and readers that must keep reading old documents accept both (the run manifest's
  `contractVersion`, D13), as `AnyRunManifestSchema` does for manifests.
- `fidelity-norm/3.2.0` and the fidelity report's version (W5); one minor version of the run
  manifest, shared with the renderer note's R10; `QUERY_TOOLS_VERSION`; the ledger's schema; the
  importer's version (D10's lock). Generated schemas and Zone A models are regenerated.
- `AGENTS.md`: "An authority import may carry a mandatory section as withheld only under
  `docs/design/authority-import-withheld.md` (measured evidence of the authority's defect, never a
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

## Verification

- The importer: a synthetic publication with one section whose record shows a defect imports with
  it withheld; a listed section with only a conservative failure, or with a refusal of ours, or
  with nothing, refuses (`withheld-section-not-shown`); an unlisted failing section refuses; a
  listed safety section, a section with subsections, a repeat or an out-of-order list refuses at
  `tree`; a document-level refusal refuses whatever is listed; a withheld section's picture is not
  fetched; a section whose only T5 evidence is withheld refuses; listing 4.2 Posology refuses.
- The gate's recomputation reproduces the withheld list, the defects and the status.
- `fidelity-norm/3.2.0`'s vectors and differential cases; the verifier refuses the notice, an
  `emptyReason` or a `generated` narrative on any other source or section.
- The EMA output passes official validation (`cmp-1`, `cmp-2`, `partial`) with a withheld section.
- The Imatinib Teva tablets SmPC imports in dry run with 5.1 withheld, its defects `cells-touch`
  and `off-page`, once the renderer gate draws the document.

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
