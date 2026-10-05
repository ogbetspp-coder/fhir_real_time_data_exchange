# ADR 0006: Importing a company's Word label into the canonical record

- Status: Proposed (for the owner's decision; nothing here is built)
- Date: 2026-10-05
- Related: ADR 0001, ADR 0002 (invariants 7, 8 and 11), ADR 0003, ADR 0005 (decisions 1–4,
  renderer gate), `docs/fidelity-normalization.md` §2, §5, §7, §8,
  `docs/design/fidelity-norm-3-0-0.md` ("Drawn documents: open items", tenth review),
  `docs/design/approval.md`, `docs/design/smpc-structure.md`, the label reader's README

## Context

Most labels exist only as Word documents. The label reader (`label-docx-reader/`) reads a Word
body's text exactly or refuses it, and its independent conservation check (`label_docx.certify`)
accounts for every character; `zone_a.certified` takes only certified reads. `zone_a.structure`
finds a centralised SmPC's QRD sections in such a read by the template's own headings. The next
step is the ePI.

The record does not admit this today, for reasons this ADR has to answer rather than set aside:

1. **A Word document is a drawn document.** `docs/fidelity-normalization.md` §7 qualifies a
   structured source only (an FHIR ePI Bundle), and the tenth review of `fidelity-norm/3.0.0`
   ruled on purpose that every Word document, however read, is drawn. A structured source is
   trusted because its page is the scanner's text of T(div), its narrative is T(div), T's closed
   lists drop only what cannot change the drawn page, and the renderer gate compares drawings
   (ADR 0005). A Word read has none of that yet. The certified read matches Word's text, not
   Word's drawing.
2. **Drawn-document problems remain for a Word read**, besides the open items:
   - **Soft hyphens:** a soft hyphen the reader keeps (U+00AD) is refused by §2.
   - **Out-of-flow content:** floating pictures, shapes, frames and floating tables are drawn by
     Word but are not in the text.
   - **Pictures:** an inline picture is a U+FFFC without its bytes; a picture's crop and scale
     are lost.
   - **Clipping:** a row of exact height can clip wrapped text.
   - **Empty numbered paragraphs:** one still draws its label.
3. **Tables are counted, not gridded.** The reader counts `<w:tc>` elements, not grid columns.
   It does not report `gridSpan`, `gridBefore`, `gridAfter` or vertical merges (a merged
   continuation cell reads as empty), and it folds nested tables into the outer cell, which §7
   forbids.
4. **The graph and the gate.**
   - ADR 0001 allows a Type 1 graph for an authority import only.
   - ADR 0002 ties `type1` to an authority publication (invariant 7) and refuses a non-synthetic
     drawn source (invariant 11).
   - Preflight requires identifiers on the MedicinalProductDefinition and the Organization.
   - Product data is never inferred from narrative.
5. **The template's own formatting is intended.** The QRD template shades and highlights "the
   national reporting system listed in Appendix V" in section 4.8. All five pinned SmPCs carry
   it (`docs/design/qrd-conformance-check.md`).

## Decision (proposed)

1. **A third source kind, `certified-word`, with its own contract in §7.** It is neither the
   structured nor the drawn path. It is trusted by three things together:
   - **Exactness:** the label reader's read and its independent conservation check.
   - **Recompute:** Zone B re-runs the pinned reader, the pinned structurer and the page
     serialiser on the pinned .docx bytes, and refuses unless it gets the same bytes. This is the
     analogue of invariant 8's refetch for an authority import.
   - **A drawing cross-check, per section.** It is the analogue of ADR 0005's renderer gate. The
     text Microsoft Word itself shows for the document (the reader's Word oracle) must equal the
     text a browser shows for our narrative (the Chrome oracle), section by section, under the
     rules the two oracles already use.

   Adding a qualified source invalidates no existing output. By §8's own reasoning, and as
   `3.1.0` did, this is therefore a **minor** version (`fidelity-norm/3.3.0`, after the reserved
   `3.2.0`). Its new vectors are reviewed by hand and the differential run covers them.

2. **The page text is defined exactly**, by a stated serialiser in §7's form:
   - one page per section, in pre-order, empty parents included (as ADR 0005 decision 4 does);
   - list labels as §7 writes them;
   - table grid markers;
   - a picture as U+FFFC, its SHA-256, U+FFFC, with the reader's bare U+FFFC never in a page.

   The narrative is built separately (decision 3), and the fidelity check compares the two. The
   check is not circular, because the builder's lists are closed and decision 1's drawing
   cross-check holds them.

3. **The narrative builder, with closed lists.** Nothing is added, reordered or reworded.
   - **Text:** one `p` per paragraph.
   - **Marks:** bold, italic, superscript and subscript become `b`, `i`, `sup` and `sub`. An
     underline is unwrapped where it cannot change the text (`zone_a.underline`) and refuses the
     section where it can.
   - **The template's own formatting:** shading and highlight are dropped only where the registry
     names them exactly. That is a list of one: the 4.8 reporting statement. Any other strike,
     faint text, highlight, shading or right-to-left text refuses the section.
   - **Lists:** `ol` or `ul` only where the labels Word draws are exactly that list type's
     sequence, from a closed list of bullet glyphs. A label such as "a)", which no HTML list
     draws, refuses the section, and so does an empty numbered paragraph.
   - **Pictures:** a picture is carried as a `data:` URI of its PNG or JPEG bytes (by signature)
     only if Word draws it uncropped and at no more than its own size. Any other picture, and any
     floating picture, shape, frame or floating table, refuses the section.
   - **Hyphens and rows:** U+00AD refuses the section (§2); so does a row of exact height.
   - **Tables:** no table is admitted until the reader reports grid spans, vertical merges and
     nested tables (prerequisite P1).

4. **Structure.**
   - **Section titles:** a section's title is the Word heading line where it is a form the
     registry allows.
   - **Assigned headings:** a heading a person assigned (`zone_a.structure`) is carried as the
     document writes it. The projection's template title is not substituted for it (today's
     `transform.ts` substitution is changed for this source kind), and the difference is a
     conformance finding.
   - **What the record keeps:** the root section keeps the preamble's text (the black-triangle
     statement, for instance), and named subsections are nested sections.
   - **Refused for now:** note references and page numbers (as `zone_a.certified` does), tracked
     changes and comments. A label submitted for an ePI carries one approved text.

5. **A Type 1 graph with declared, verified metadata.**
   - **Declaration:** a named reviewer declares the product name, the marketing authorisation
     holder, the authorisation number, and the identifiers preflight requires (for example, the
     PMS product id and the SPOR organisation id).
   - **Verification:** the system confirms that each declared name and number appears verbatim in
     the section that states it (1, 7 or 8), and refuses otherwise.
   - **Not supplied:** packs, ingredients and substances are declared not supplied.
   - **Amendments:** ADR 0001 (Type 1 allowed for `certified-word`) and ADR 0002 (invariant 7
     admits `certified-word` with `type1` and an attestation; invariant 11 admits a certified
     Word source).

6. **Provenance pins every version.** `IngestionProvenance.extraction` gains a composite
   extractor record for this kind, with the reader, format, conservation-check, structurer,
   registry, mapping, serialiser and builder versions. Invariant 7's single extractor token
   becomes that record's hash.

7. **The approval** is the signed statement `docs/design/approval.md` already designs, naming
   the certified read's SHA-256. As that note says, it claims neither 21 CFR Part 11 nor
   EU GMP Annex 11; that is the owner's quality function's decision.

8. **Scope:** the centralised SmPC in English (QRD 10.4), with the root `lang` attribute set to
   `en`. The package leaflet, the labelling and other languages come later, each from its own
   template.

## Prerequisites, in order

1. **P1, reader:**
   - report grid spans, vertical merges and nested tables;
   - extract picture bytes with crop and scale, refusing what it cannot carry;
   - in this path, refuse floating objects instead of setting them aside.
2. **P2, specification:** `fidelity-norm/3.3.0`, with §7's `certified-word` contract, its
   serialiser and vectors.
3. **P3, Zone A:** the builder, the page serialiser and the drawing cross-check.
4. **P4, contract and Zone B:**
   - the `certified-word` source kind, the recompute and the composite extractor record;
   - the ADR 0001 and 0002 amendments;
   - the preflight identifiers, and the title rule for assigned headings.
5. **P5, review screen:** the declared-metadata form and the approval.

## Open questions for the owner

1. Approve the trust model of decision 1 (exact read, recompute, Word-against-Chrome drawing
   check) for admitting Word labels?
2. Approve decision 5 (metadata declared by a reviewer and verified verbatim against sections 1,
   7 and 8), and name the identifier systems the client uses?
3. Approve decision 4's rule that an assigned heading is carried as the document writes it,
   rather than replaced by the template's title?
