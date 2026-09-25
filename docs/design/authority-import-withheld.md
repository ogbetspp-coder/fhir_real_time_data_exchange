# A withheld section: importing a publication with an authority's defect recorded in place (roadmap 3a, PR 3c)

- Status: proposed, 2026-09-25 (owner decision of the same day: "withhold 5.1, import the other 31")
- Decides: how an authority import carries a section the importer cannot accept, instead of
  refusing the whole publication
- Amends: ADR 0005 decision 3 and its consequence "a label with any refused … section cannot be
  imported whole"; `docs/design/authority-import-contract.md` D2 (the request), D3 (the source
  record), D4 (pages; a section that draws nothing), D9 (the record), D13 (versions)
- Related: ADR 0002 (a hash-bound human decision), ADR 0003 (no false passes),
  `docs/design/authority-import-t.md`, `docs/design/authority-import-renderer.md`,
  `docs/design/qrd-conformance-check.md`

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
- Its two pictures are the Kaplan-Meier figures the at-risk tables belong to.

ADR 0005 decision 3 refuses a defect and records it; its consequence is that a label with any
refused section cannot be imported until the authority corrects it. Of the EMA ePI API's whole
public corpus (268 Bundles; English SmPCs for five centrally authorised products), no English
SmPC gets past T, pictures and structure except this label, blocked only by 5.1. The owner's
decision is that the other 31 sections are imported, with their full proof, and 5.1 is carried
as a withheld section: its heading and code, no narrative, and the defect stated.

## Decisions

### W1. A withheld section is a human decision, justified mechanically

The import request (D2) gains `withheld`: a list of `{ path, code, reason }`, with `path` the
section's `SourcePath` (`Composition.section[0].section[4].section[0]`), `code` its code as served,
and `reason` from a closed list with one value, `authority-defect`. The request is approved
content (D2), so who withheld which section is part of the hash-bound decision (ADR 0002). The
importer, and the gate recomputing it (D1), require, in the stated check order (D10):

1. **It exists.** Each listed path names a section of the document with that code, and no path
   is listed twice.
2. **It is refused on its own.** Each listed section, run through the importer alone, is refused
   by a section-level check: the pictures stage for a picture in its own div, T, the scanner, the
   invisible-character check, or D4's record check for that section; or the renderer gate records
   it as failed (`docs/design/authority-import-renderer.md`). A listed section the importer would
   accept refuses the import (`withheld-section-accepted`): nothing that passes can be withheld.
3. **Every refused section is listed.** A section refused at a section-level check and not listed
   refuses the import, as today. So withholding is never automatic, and never silent.
4. **Only section-level refusals.** A document-level refusal (bytes, shape, binding, tree, titles:
   D1, D4, D5) refuses the import whatever is listed: the publication's identity, tree and
   headings are what make the other sections' proof mean anything.

A withheld section's reason is recomputed and recorded: its first refusal as `{ stage, reason }`
(T and the scanner stop at the first; the stage order is D10's), or the renderer record's hash
and failed checks.

### W2. What the record carries for it

- **Its place.** The section keeps its place in the tree, its title and its code, as every
  section does (D4). Its subsections, if any, are judged on their own; withholding a section does
  not withhold its subsections, and a withheld subsection does not withhold its parent.
- **No authority content.** Nothing of the withheld section's div enters the record: no text, no
  picture, no fragment. Its pictures are not fetched (D6 runs only on sections carried).
- **A stated notice.** FHIR's `cmp-1` requires a leaf section to hold text, entries or
  subsections, and `cmp-2` allows `emptyReason` only on a section without entries. The section
  carries `emptyReason` `unavailable` (HL7 list-empty-reason: "information to populate this list
  cannot be obtained"), and a narrative generated from it, `text.status: generated`, whose div is
  a fixed sentence, a stated rule of the record's language: `<div xmlns="…"><p>Withheld: the
authority's published text of this section could not be verified (authority-defect).</p></div>`.
  It is our record's statement about the section, generated from structured data, never presented
  as the authority's words.
- **Provenance.** `provenance.sourceDocument` gains `withheldSections`: `{ path, code, page,
reason, refusal }` per section, `refusal` being W1's recomputed `{ stage, reason }` or the
  renderer record's hash. Class: recomputed (D3).
- **Pages.** A withheld section's page (fidelity §7) is empty and carries no span; the notice is
  not a page's text. The fidelity report's `coverage.uncoveredGaps` stays 0. The section's
  `narrativeDivSha256` is absent, and the fidelity check does not read a narrative whose status
  is `generated` from a withheld section (one sentence added to fidelity §7, documentation of
  existing behaviour for a status the importer never wrote before; no vector changes).
- **The approval.** Unchanged in form (D8); the request it names now holds the withheld list, so
  the approver's hash covers it.

### W3. Everything downstream shows the gap

- **The EMA output** (`src/fhir/transform.ts`): the crosswalk carries `emptyReason` for a
  withheld section and its generated notice, and nothing else it did not carry before; any other
  section with `emptyReason` still refuses.
- **The query service** (PR 5): `get_section` on a withheld section returns a new outcome,
  `section-withheld`, with the reason, never `section-not-found` and never the notice as document
  content; `verify_quote` can match nothing in it.
- **The agent** (PR 5): a question about a withheld section is answered with the fact that the
  authority's section is withheld and why, never from memory or another section.
- **The round trip** (PR 4): the withheld section is an expected, recorded difference, reported
  as such, not a failure and not silence.
- **The defect is recorded** in `docs/design/qrd-conformance-check.md` with its measurements,
  as a finding reportable to the authority.

### W4. Versions

The request, the source record and the record change shape: `CanonicalSubmission` 2.1.0 (a
minor version: a 2.0.0 submission, with no `withheld`, reads the same), the generated schemas and
Zone A models regenerated, the importer's version bumped (D10's lock), and ADR 0005's consequence
amended. The synthetic publication's vectors gain a withheld-section case.

## What this is not

- Not a repair: nothing the authority wrote is changed or carried in another form.
- Not a way to accept a section: a withheld section is never answerable, quotable or proven.
- Not automatic: a person lists each withheld section, and the import refuses if the list is
  wrong in either direction.

## Stated residuals

- A reader of our record sees the notice where the authority's page draws 5.1; that is the
  point, and the notice says so.
- A publication with a withheld section is less than the authority published; whether it may be
  entitled to the query service is PR 5's decision, with the notice in every answer about it.

## Verification

- The importer's tests: a synthetic publication with one refused section imports with it
  withheld; a listed section that passes refuses (`withheld-section-accepted`); an unlisted
  refused section refuses; a document-level refusal refuses whatever is listed; a withheld
  section's picture is not fetched.
- The gate's recomputation reproduces the withheld list and reasons.
- The EMA output passes official validation (`cmp-1`, `cmp-2`) with a withheld section.
- The Imatinib Teva tablets SmPC imports in dry run with 5.1 withheld (`narrative: offset`),
  once the renderer gate passes its other 31 sections.
