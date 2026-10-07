# ADR 0006: Importing a company's Word label into the canonical record

- Status: Accepted (owner decisions of 2026-10-05); P1 tables and P3 built (Progress, below)
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

## Decision

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

   Adding a qualified source invalidates no existing output, so it is a **minor** version:
   `fidelity-norm/3.2.0` (amended 2026-10-05: the withheld design, which had reserved 3.2.0 and is
   not built, takes 3.4.0, after 3.3.0's grey, so versions are released in order; §8 now names
   this case). Its new
   vectors are reviewed by hand and the differential run covers them.

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
   - **Marks:** bold, italic, superscript and subscript become `strong`, `em`, `sup` and `sub`
     (amended 2026-10-05 from `b` and `i`: the EMA ePI style guide's elements; both pass the
     validator). An
     underline is unwrapped where it cannot change the text (`zone_a.underline`) and refuses the
     section where it can.
   - **The template's own formatting:** the QRD template's grey (a light grey highlight or D9D9D9
     shading) is a `span` styled `background-color: silver;`, the EMA ePI style guide's form for
     "not printed" text (amended 2026-10-06, owner decision 5 below; until then it was dropped
     where the registry named it, the 4.8 reporting statement only). Any other strike, faint
     text, highlight, shading or right-to-left text refuses the section.
   - **Lists:** `ul` for "•", and `ol` only for "1.", "2.", ... from one (amended 2026-10-05:
     FHIR's narrative rule txt-1 allows no `start` or `type` on `ol`, and the official validator
     with the EMA profiles refuses both). Any other list, "a)", "–" or "3." included, is written as
     Word draws it, each item a `p` of its label and its text (amended 2026-10-06, owner decision 6
     below; until then it refused the section). An empty numbered paragraph refuses the section.
   - **Pictures:** a picture is carried as a `data:` URI of its PNG or JPEG bytes (by signature)
     only if Word draws it uncropped and at no more than its own size. Any other picture, and any
     floating picture, shape, frame or floating table, refuses the section.
   - **Hyphens and rows:** U+00AD refuses the section (§2); so does a row of exact height.
   - **Tables:** no table is admitted until the reader reports grid spans, vertical merges and
     nested tables (prerequisite P1).

4. **Structure.**
   - **Section titles:** a section's title is the Word heading line where it is a form the
     registry allows.
   - **Headings that differ from the template are remediation findings.** A heading a person had
     to assign (`zone_a.structure`) is never silently changed. The difference is recorded as a
     finding and sent to the label team responsible for the product, who decide:
     - fix the source, which makes a new version of the label;
     - or accept the wording as written, which is recorded with their name.

     The ePI for that label waits for that decision. An accepted wording is carried as the
     document writes it, and the projection's template title is not substituted for it (today's
     `transform.ts` substitution changes for this source kind).

   - **What the record keeps:** the root section keeps the preamble's text (the black-triangle
     statement, for instance), and named subsections are nested sections.
   - **Refused for now:** note references and page numbers (as `zone_a.certified` does), and
     comments. A label submitted for an ePI carries one approved text: one with tracked changes
     is taken only by the view a person names (owner decisions of 2026-10-06, 2).

5. **A Type 1 graph whose metadata comes from the label, exactly, and is confirmed once per
   product.** The QRD template gives the product name, the marketing authorisation holder and
   the authorisation numbers their own sections (1, 7 and 8).
   - **Authorisation numbers** are taken by their strict format (`EU/1/YY/NNN/NNN`): every
     number section 8 states, exactly as written.
   - **The name and the holder** cannot be taken by rule alone. On the five pinned EMA SmPCs, a
     rule ("section 1 up to the strength", "section 7's first line") agrees with the EMA's own
     structured index (its List) for only three of five of each: "BRUKINSA" against
     "Brukinsa", a name with no strength to cut at, and a holder's name with trailing
     punctuation. Normalising case or punctuation would be guessing. So the system proposes the
     exact text from sections 1 and 7, highlighted in the document, and a person confirms it by
     choosing, never by retyping, once per product.
   - **Identity:** a confirmed product is a **canonical product**, our own identifier at the top,
     with regulator-specific identifiers beneath it:
     - EU: the SPOR organisation id of the holder, the EU authorisation numbers and the
       procedure number;
     - other regulators (FDA, MHRA, Swissmedic and others) in the same shape.

     Every later label is matched to a canonical product only by exact equality with confirmed
     values, which is how labels are sorted after the fact without a person.

   - **Not supplied:** packs, ingredients and substances are declared not supplied, as for an
     authority import.
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
   `en`, and its package leaflet (owner decision 8). The labelling, Annex II and other languages
   come later, each from its own template.

## Prerequisites, in order

1. **P1, reader:**
   - report grid spans, vertical merges and nested tables;
   - extract picture bytes with crop and scale, refusing what it cannot carry;
   - in this path, refuse floating objects instead of setting them aside.
2. **P2, specification:** `fidelity-norm/3.2.0`, with §7's `certified-word` contract, its
   serialiser and vectors.
3. **P3, Zone A:** the builder, the page serialiser and the drawing cross-check.
4. **P4, contract and Zone B:**
   - the `certified-word` source kind, the recompute and the composite extractor record;
   - the ADR 0001 and 0002 amendments;
   - the preflight identifiers, and the title rule for assigned headings.
5. **P5, review screen:** the declared-metadata form and the approval.

## Owner decisions (2026-10-05)

1. **The trust model of decision 1:** accepted for now.
2. **Product data:** from the label where that is 100% accurate; a canonical identifier at the
   top, branching into regulator-specific ones. Recorded as decision 5, with the measurement
   that shows where a person's one-time confirmation is needed.
3. **Headings that differ from the template:** a remediation finding for the responsible label
   team to decide (decision 4).

## Owner decisions (2026-10-06)

1. **A tab after a typed bullet:** a tab right after a bullet glyph (§3 step 4's) that begins a
   paragraph's text, outside a table and after no list label, is written as a space in the
   narrative and on the page (§7, `fidelity-norm/3.2.0`), as the EMA's own ePIs carry no tab. Any
   other tab is still refused: Word draws it as a jump to a tab stop.
2. **A label with tracked changes:** imported only by the view a person names, every change
   accepted or every one rejected (`zone_a.certified.read_body`, `epi_from_word.py --view`). The
   result records the view and the number of changes; with no view named it is refused, as
   before. This replaces "refused for now" for tracked changes in decision 4.
3. **An Annex I holding several SmPCs:** each SmPC is its own ePI (`zone_a.structure.smpcs`). A
   new SmPC starts at a section 1 heading after the first, or at the template's own statement
   before section 1 where it stands between that heading and the last section 10. A boundary
   the template's lines do not settle is for a person.
4. **"(S)" in a template heading:** the template leaves singular or plural to the author, so
   "NUMBER(S)", "NUMBER" and "NUMBERS" are each the template's wording for section 8
   (`zone_a.qrd.headings`). The ePI carries the label's own wording, as decision 4 says.

## Owner decisions (2026-10-06, evening)

The owner took the recommendations of the Wave 3 brief for every item. Where the brief named no
recommendation (dash bullets), the choice below is the one that keeps what Word draws.

5. **The QRD template's grey:** carried as the EMA ePI style guide's `<span style="background-color:
silver;">`, the one style `fidelity-norm/3.3.0` allows (§5, exactly that value, on `span`
   only: a background under text in its own colour hides nothing). Silver is the colour of Word's
   light grey highlight. The drawing check holds it to Chrome drawing that background under
   exactly the grey characters (`zone_a.drawing`), where it was left out before. Any other
   highlight or shading is still refused.
6. **Lists an HTML list cannot draw:** EMA's stylesheet draws every `ul` with discs, and FHIR
   allows no `start` or `type` on `ol`, so a dash bullet, "a)" or "(i)" numbering, or numbering
   that does not start at one would be refused or drawn with another marker. Each such item is
   written as Word draws it, a `p` of its label, a space and its text, as a typed label is: the
   page already writes that line (§7), and the drawing check reads the label as the line's start.
   Nesting is still refused (one list level in a section), and so is a non-disc bullet glyph in a
   table cell, where the page leaves a bullet glyph out.
7. **A tab after a typed label:** decision 1 above, widened. The tab after a label typed at the
   start of a paragraph is written as a space: a bullet glyph or a dash, one to three of the same
   footnote mark (`*`, `†`, `§`, ...), or an enumerator with its punctuation ("1.", "a)", "(iv)"),
   outside a table and after no list label (§7, `fidelity-norm/3.3.0`). A bare letter or number
   before a tab is no label ("n" then a tab then "= 50" is a column) and is still refused, as is
   every other tab. On the EMA SmPC cuts it carries 228 of the 845 body paragraphs still refused
   for a tab.

## Owner decision (2026-10-06, night)

Taken as recommended, under the owner's authorisation of the evening ("I follow your
recommendations ... the rest you are authorized to do as well"); each reading is the EMA's own.

8. **The package leaflet** (`docs/design/pl-structure.md`, `pl-structure/1.0.0`): found by its
   template's headings as the SmPC is, its tree and codes from the EMA's leaflet profile
   (`fhir/mappings/cap-pl-en.json`), with the medicine's name for the template's "X" ("X stands
   for the (invented) name of the medicine", the annotated template 10.4) and three readings of
   the template's own:
   - a choice that begins with a comma is written without the space before it, and a
     non-breaking hyphen is the hyphen it draws;
   - a heading that ends in the date completed at printing ("This leaflet was last revised in
     <{MM/YYYY}>...") is found by its text followed by that date, its placeholder or nothing;
   - "Marketing Authorisation Holder" alone heads the holder's section, which the template calls
     "Marketing Authorisation Holder and Manufacturer" and the annotated template allows only
     where the two are the same.

   A wording the annotated template leaves to the EMA case by case ("How X is given" for a
   medicine a nurse gives) is for a person, as decision 4 says. Each leaflet of a file is its own
   ePI, as decision 3 says for SmPCs. Two judgements of structure, not wording: the leaflet ends
   at the product information's next annex ("ANNEX IV") or the document's end, and numbered lines
   standing together are a list (the leaflet's list of its sections when it starts with section
   1's heading; else numbered steps, among which a section's heading stays a heading).

## Progress

- **P1, tables** (2026-10-05): the reader reports each body table's grid (`docx-reader/1.27.0`,
  `label-docx-json/1.16.0`), certified by the conservation check and held to Word's own saves.
- **P3** (2026-10-05): `zone_a.word_epi` (`word-epi/1.0.0`) builds each section's narrative and
  page text from the certified read by separate code (the page reads Word's grid row by row, the
  narrative works out each merged cell's rows), under decision 3's closed lists, and refuses a
  section on the first thing they do not list; it checks that the fidelity scanner reads the
  narrative as the page. `zone_a.drawing` (`word-drawing/1.0.0`) is decision 1's drawing check:
  Chrome draws each narrative, compared line by line and mark by mark with the read, which is what
  Word draws, and a section it draws otherwise, or does not draw, is refused. A picture the reader
  vouches for is carried as the `data:` URI of its exact bytes where Word draws it at no more than
  its own size and in its own proportions within 2% (the EMA templates' black triangle is drawn
  0.9% to 1.4% out of them); any other picture refuses its section. A document is refused whole where
  Word draws what the read does not yet say: a floating picture or shape (the certificate's
  count), and, by a conservative scan of the package until the reader reports them, a floating
  table, a frame, a right-to-left table or a page break between two words. On five SmPCs Word
  wrote from the pinned EMA ePIs (`zone-a/tests/fixtures/word-smpc/`), 147 of 160 sections are
  carried, Chrome draws every one as read, and each reads as the EMA's own text but where the
  fixtures' README says why not; the 13 refused are shading the narrative cannot carry and
  underlines over "+" or "≥". An independent review found ten holes, each fixed and tested
  (`zone-a/tests/test_word_epi.py`, "the independent review's cases").
- **Scoped refusals, phase 1** (2026-10-05, `docs/design/scoped-refusals.md`): the reader places
  every object anchored to a paragraph (`anchored`, `docx-reader/1.30.0`, `label-docx-json/1.18.0`)
  and sets a floating text box, group or canvas aside unread where nothing in it is counted or
  referred to elsewhere, certified by the check's own walk; `zone_a.word_epi` (`word-epi/1.1.0`)
  refuses the section that anchors a floating object (`anchored-object`), not the document, and a
  header's or footer's floating object (a logo) refuses nothing.
- **P1, pictures** (2026-10-05): each U+FFFC the reader writes says what it stands for (part,
  SHA-256, type by signature, pixels, extent, crop, and a reason from a closed list where it cannot
  be carried), certified; Word's own saves agree on every DrawingML picture of the corpus.
- **The package leaflet** (2026-10-06, owner decision 8): `zone_a.leaflet` (`pl-structure/1.0.0`)
  finds a leaflet's sections, `zone_a.word_epi` builds them unchanged; see
  `docs/design/pl-structure.md` for what it carries on the EMA's published Word leaflets.
- **P4, in part** (2026-10-06, `docs/design/certified-word-import.md`, "Progress"):
  `zone_a.recompute`, the one function the producer and Zone B's gate run (D2's function); the
  `certified-word` source kind (`CanonicalSubmission` 3.0.0) and its importer
  (`src/certified-word/`), a Type 1 graph of the confirmed product with the titles carried as
  written; the ADR 0001 and ADR 0002 amendments. The gate accepts one only as a dry run until it
  runs the recompute itself.
- **P4, decision 1's second leg** (2026-10-07, `docs/design/certified-word-import.md`, "The gate"):
  the worker image carries Python 3.14 and the zone-a and label-docx packages; the gate reads the
  uploaded .docx from its content address in the submissions bucket and requires its hash and
  length (D4), runs `python -m zone_a.recompute` on it in an isolated subprocess (D2), runs the
  importer again and requires the same submission, page text and report. A run that is not a dry
  run is still refused, now because the drawing (decision 1's third leg) is missing.
- **The package leaflet in Zone B** (2026-10-07, `docs/design/pl-structure.md`, "Zone B"): the
  worker takes each source's mapping by its document type, and a leaflet, from a Type 2 graph or
  a certified Word source, is transformed to the EMA's leaflet document, which passes the official
  validator. The certified Word importer (`certified-word-import/1.2.0`) carries `document: "pl"`
  with the leaflet's own product check: the name is what stands for X in its section 1 heading
  (the structure's `name`), the holder is section 6's, and the leaflet states no EU number, so its
  Type 1 record has no RegulatedAuthorization.
- **Still to do:** in the reader (P1), floating tables, frames, right-to-left tables and page
  breaks reported in place of the scan; P2; in P4, the drawing records (D3); P5.
