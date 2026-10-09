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
   not built, takes 3.5.0, after 3.3.0's grey and 3.4.0's typed labels, so versions are released
   in order; §8 now names this case). Its new
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
     where the registry named it, the 4.8 reporting statement only), and so is C0C0C0 shading,
     which Word draws as the light grey highlight (amended 2026-10-07, owner decision 9 below).
     Any other strike, faint text, highlight, shading or right-to-left text refuses the section,
     but a strike, a highlight or a solid shading over the spaces that end a paragraph's text,
     which Word paints over nothing (amended 2026-10-08, by Word's own print, below).
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
   other tab is still refused: Word draws it as a jump to a tab stop (amended by decisions 11 and
   12: a paragraph's lone tab and its indent's tabs are spaces too).
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
   highlight or shading is still refused (amended by decision 9: C0C0C0 shading too).
6. **Lists an HTML list cannot draw:** EMA's stylesheet draws every `ul` with discs, and FHIR
   allows no `start` or `type` on `ol`, so a dash bullet, "a)" or "(i)" numbering, or numbering
   that does not start at one would be refused or drawn with another marker. Each such item is
   written as Word draws it, a `p` of its label, a space and its text, as a typed label is: the
   page already writes that line (§7), and the drawing check reads the label as the line's start.
   Nesting is still refused (one list level in a section), and so is a non-disc bullet glyph in a
   table cell, where the page leaves a bullet glyph out. (Narrowed 2026-10-08,
   `fidelity-norm/3.5.0`, a fix with no new decision: a label so written is refused,
   `list-label`, where it and its space would join a number to it, by decision 11's test below:
   a label that is a dash or minus, or ends in a number, before an item whose text begins with
   a number, since "– 2 to 8 °C" reads as −2 °C, a storage temperature, and the label "1" before
   "000 mg" as "1 000 mg".)
7. **A tab after a typed label:** decision 1 above, widened. The tab after a label typed at the
   start of a paragraph is written as a space: a bullet glyph or a dash, one to three of the same
   footnote mark (`*`, `†`, `§`, ...), or an enumerator with its punctuation ("1.", "a)", "(iv)"),
   outside a table (amended by decision 9: in a table cell too) and after no list label (§7,
   `fidelity-norm/3.3.0`). A bare letter or number
   before a tab is no label ("n" then a tab then "= 50" is a column) and is still refused, as is
   every other tab (amended by decision 11: where that tab is the paragraph's only one, it is a
   space as a lone tab). On the EMA SmPC cuts it carries 228 of the 845 body paragraphs still
   refused for a tab.

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

## Owner decision (2026-10-07)

Taken as recommended, under the owner's authorisation of 2026-10-06 (decision 8's).

9. **A tab after a typed label, in a table cell too; a grey Word draws as the template's:**
   decision 7, widened. The tab after a label typed at the start of a paragraph's text (past §3
   step 5 whitespace, after no list label) is written as a space in a table cell as well as
   outside one, and a typed label is also:
   - U+2011 NON-BREAKING HYPHEN, beside "-", U+2013 and U+2014 (decision 8 already reads it as
     the hyphen it draws);
   - a caption's number: `Table` or `Figure`, then U+0020 or U+00A0, then one to three ASCII
     digits, optionally one ASCII lower-case letter, optionally "." or ":" ("Table 1:",
     "Table 12a:", "Figure 3.");
   - a raised footnote key: one to three code points, every one inside a superscript mark and of
     bidi class L, EN, ES, ET, CS or ON (a raised "a", "1", "*", "†"): a tab separates bidi
     segments and a space does not, so a key that is or may join right-to-left text (R, AL, AN,
     a bidi control, NSM, BN) keeps its tab, refused.

   Then any number of spaces, as before (§7, `fidelity-norm/3.4.0`). Everything else is still
   refused: a bare letter or number that is not raised ("n" then a tab then "= 50" is a column),
   more than three raised code points, a lead mixing raised and level code points, a raised
   right-to-left key, "Tables 1",
   "Table" with no number, a second tab, a tab after a list label. The tab is written as a space
   where it is raised or lowered with its label too, as the narrative already wrote it. (Amended
   by decisions 11 and 12, 2026-10-08: a leading tab is an indent, read past before the label,
   and a lead that is no label but holds the paragraph's only tab has that tab written as a space
   as a lone tab; a typed label keeps precedence, so its tab is a space where decision 11 would
   refuse it, raised with its key. Fixed the same day, `fidelity-norm/3.5.0`, for the reason of
   decision 11's number guard and with no new decision: a typed label's tab is not a space where,
   as decision 11 tests it, it would join a number to a number or to a dash before it: "Table
   1", a tab, "2-year", a raised "1", a tab, "2", and "–", a tab, "2 to 8 °C" (as −2 °C, a
   storage temperature) keep their tab, refused; "Table 1:", a tab, "2-year", a raised "a", a
   tab, "2", and "–", a tab, "Tablets" carry.)

   Evidence (EMA's published English Word PI cuts, 296 SmPC and 286 leaflet files; internal, counts
   only): among the SmPCs whose structure is ready but a section is refused, the refused tabs in
   built sections were 490 in a table cell (193 after one raised letter, 112 after one level symbol
   such as a bullet or "*", 33 after a raised symbol, 23 after a raised digit), 313 after a
   "Table N:" caption outside a cell and 91 inside one, and 177 mid-text (61 after a raised digit,
   49 after a raised letter). EMA's own 37 English ePI Bundles (`label-docx-reader/corpus/ema-epi`)
   carry no tab anywhere and write "Table 1: ..." with a space. With this rule, the SmPC files
   still blocked by some tab fall from 54 to about 32 (measured by the coordinator's scan).

   The template's grey of decision 5 is also C0C0C0 shading, which Word draws in exactly the
   light grey highlight's colour. No new reading: Word's own print of a synthetic probe, one
   paragraph per mark, counted by colour at 144 dpi (2026-10-07):

   | reader mark                 | Word draws | as                       |
   | --------------------------- | ---------- | ------------------------ |
   | `shading-D9D9D9`            | #D9D9D9    | (decision 5)             |
   | `shading-pct15-AUTO-AUTO`   | #D9D9D9    | `shading-D9D9D9` (3.7.0) |
   | `shading-pct15-AUTO-FFFFFF` | #D9D9D9    | `shading-D9D9D9` (3.7.0) |
   | `highlight-lightGray`       | #C0C0C0    | (decision 5)             |
   | `shading-C0C0C0`            | #C0C0C0    | `highlight-lightGray`    |
   | `shading-BFBFBF`            | #BFBFBF    | refused                  |
   | `shading-E6E6E6`            | #E6E6E6    | refused                  |

   A solid fill is opaque, and a theme fill is another reader mark (`shading-THEME-...`), so
   C0C0C0 is that colour wherever it stands. It is the same silver span, which the drawing check
   holds to Chrome drawing it under exactly the shaded characters. The 15% patterns wait: the
   reader spells a pattern's colour from `w:color` alone (a themed accent at 15% reads as
   `shading-pct15-AUTO-AUTO` too), and what Word draws for an automatic fill over a painted cell or
   paragraph is not on record; both are the reader's to say, planned for `docx-reader/1.34.0`.
   Any other shading is still refused. In the built but blocked files of the same cuts, C0C0C0
   stands in 3 leaflet files. (Both questions were settled on 2026-10-09, `fidelity-norm/3.7.0`,
   below: the pattern greys are carried.)

## Owner decisions (2026-10-08)

Taken as recommended, under the owner's authorisation of 2026-10-08, night ("fully authorized ...
you know the strategic intent"); each is **for the owner's review**. Section 7,
`fidelity-norm/3.5.0`, `word-epi/1.5.0`; the change record is
`docs/validation/changes/2026-10-08-fidelity-norm-3-5-0.md`.

10. **Grid columns a row leaves out:** a row whose grid columns Word leaves out at its start
    (`gridBefore`) or end (`gridAfter`) is written with an empty cell over those columns, a `td`
    with their `colspan`, one row high and with no content, at the row's start or end, and the
    page writes the same empty cell. Word draws no cell there and no text; the ePI holds the same
    text in the same columns, and only an empty cell's box may be drawn where Word draws none. A
    row may leave columns out at both ends. Still refused (`table-shape`): a vertical merge that
    would run through or start under an empty cell (an empty cell starts no merge, so the cell
    below is under no cell of its columns), and a row of no cells of its own that leaves columns
    out. Evidence (EMA's English Word PI cuts, internal, counts only): rows that leave columns
    out stand in 8 SmPC and 12 leaflet files; the leaflets' rows leave them out at the start 106
    times, the SmPCs' at the end 155 times.
11. **A lone tab is a space:** in a paragraph that draws no list label, in a table cell or outside
    one, whose text past its indent (decision 12) holds exactly one tab, that tab is written as a
    space where no number stands on both sides of it (read past every gap, as §6 reads "1 000" as
    one number: "1", a tab, "000" never reads as "1 000"; a typed label's tab is held to the same
    guard), it is not inside a raised or lowered run, and the text holds no right-to-left code point or explicit bidi control (a tab separates
    bidi segments and a space does not, so a space between two right-to-left words, or one and a
    number, is drawn with them reversed; a narrowing of the recommendation, for exactness). Two or
    more tabs stay refused (`tab`): a column layout. Decisions 1, 7 and 9 keep precedence, so a
    typed label's tab raised with its key is a space where this rule would refuse it; decisions
    7 and 9 are narrowed by the same guard, and decision 6 too (above). **The guard, one test for
    every tab written as a space (an indent's, a typed label's, a lone one) and every label
    written as text:** no space where it would join a number to what stands before it, that is,
    where the text after it begins with a number and the last code point before it, read
    backwards past every §6 gap (a line break too, which a cell writes as a space) and to its
    base past combining marks, is numeric or a dash or minus (general category Pd, or U+2212,
    U+207B, U+208B, U+FE63, U+FF0D, U+2796, U+2043, U+02D7; the reader maps a Symbol font's minus
    to U+2212). Numeric is a Unicode numeric value: a digit
    of any script, as the read holds a raised one, a script digit such as "¹", a vulgar fraction
    such as "½", a circled digit, a Roman numeral, a CJK numeral. A text begins with a number
    where its first code point past gaps is numeric, or a run of ".", ",", "·", U+066B, their
    fullwidth and small forms, "±", "+" and any dash or minus stands before one ("1", a tab, ",5
    mg"; "1", a tab, "+.5"; "5", a tab, "–20 °C", an en dash as the EMA writes a minus, which as a
    space would read as the range 5–20 °C); not a bracket or a comparison sign ("(", "≥", "<", "~"), after which a
    number reads as no sign's. So "Storage:", a line break, "-", a tab, "2 to 8 °C" and "5", a
    line break, a tab, "2" keep their tab. Rationale: the text, its order and its
    code points are Word's; a lone tab's gap becomes a space, as the EMA's own ePIs write it.
    With it, the SmPC files with tab blockers fall from 32 to 11, and those left all have several
    tabs (the coordinator's scan). **A gap, and its follow-up:** the reader reads `w:tab` and
    `w:ptab` as U+0009 and reports neither tab stops nor leaders, so a tab whose stop draws a dot
    or an underscore leader, or a `w:ptab` whose own `w:leader` attribute draws one, would be
    written as a space, its leader dropped; this holds for decisions 1, 7 and 9, already live,
    too. On the EMA's English cuts no paragraph holding a tab (0 of 32 048) has a tab stop with a
    leader set directly or by its paragraph's own style (the coordinator's count, which did not
    cover a numbering level's tabs, the document defaults or a style's `basedOn` chain);
    `docx-reader/1.34.0` is to refuse a leader of any of these on any paragraph that holds a tab.
12. **An indent:** the tabs before a paragraph's text starts (in the §3 step 5 whitespace before
    its first other code point, a line feed ending it) are its indent, each written as a space;
    the tabs after it are judged by decisions 7, 9 and 11 as if it were absent. So a tab, "●", a
    tab and text carries (an indent, then a typed bullet), and a tab, "a", a tab, "b", a tab, "c"
    stays refused. In a paragraph that draws a list label it is refused, as every tab there is (a
    narrowing of the recommendation, which named no list label: the gap is then Word's between
    the label and the text). The section 5 scanner and the drawing check read leading spaces as
    nothing, as a browser draws them; the page and the narrative write the same spaces. Evidence:
    "tab:leading" in 6 leaflet files, and "several" tabs with "●" in 7.

Not owner decisions, each the same drawing (2026-10-08, the same change):

- **A grid column at which no cell starts is dropped**, and each cell spans the columns kept: a
  fix of the builder's grid. Every cell over such a column also covers the one before it, so
  §5's HTML table model draws it at no width, which §5 rejects (`table-shape`, its zero-width
  rule), where Word draws it inside the cells that span it; without it Chrome draws the same
  table, each value in the same cell, merges kept. The page and the narrative lay the grid by the
  same rule, each by its own code (`zone_a.word_epi._slots` and `_rows`, held to each other on
  random grids), so the fidelity check still compares two readings. Evidence: 13 narratives
  refused `table-shape` in the SmPC built files, rows such as two cells, the first of two
  columns, then one of three, where column 1 never starts a cell.
- **A strike, a highlight or a solid shading Word paints over nothing is left out:** a `strike`,
  a highlight of one of Word's sixteen colours (`highlight-black` to `highlight-lightGray`) or a
  solid shading (`shading-` and six hex digits, a clear pattern's fill), whose text, from its
  start to the paragraph's end, is only U+0020 (a paragraph or table cell of only such spaces
  included). Word's own print of two synthetic probes (Word for Mac to PDF, 2026-10-08,
  `zone-a/tests/fixtures/word-oracle/`; the ink of each line counted at 288 dpi):

  | probe paragraph ("Alpha beta" and ten spaces)  | Word draws                               |
  | ---------------------------------------------- | ---------------------------------------- |
  | ten U+0020 struck at the end                   | the unstruck text (ink 2501, as without) |
  | ten U+0020 struck between "Alpha" and "beta"   | a line through the gap (ink 2695)        |
  | ten U+00A0 struck at the end                   | a line (ink 2695)                        |
  | ten U+0020 highlighted yellow at the end       | the text only, no yellow                 |
  | ten U+0020 highlighted yellow between words    | yellow                                   |
  | a paragraph, or a table cell, of them alone    | nothing                                  |
  | ten U+0020 shaded 000000 at the end, or a cell | the text only, no black; a cell, nothing |

  Every other strike, highlight or shading over whitespace (between words, over U+00A0, a tab
  after it) is still refused (`formatting`), and so are a double strike, a pattern's shading
  (`shading-pct15-...`, `shading-solid-...`) and a theme's (`shading-THEME-...`) over trailing
  spaces, which the print did not show: a follow-up, when Word is probed for them. Evidence: in the SmPC built files, a strike over whitespace stands 10 times at a
  paragraph's end and 5 between words; a yellow highlight over whitespace 4 times in cells and 3
  in the body (SmPC) and 4 in cells (leaflets); black shading over whitespace in cells 6 times
  (SmPC) and 7 (leaflets).

## Owner decision (2026-10-09)

Taken as recommended under the owner's authorisation of 2026-10-08; **for the owner's review**.
Section 7, `fidelity-norm/3.7.0`, `word-epi/1.7.0`; the change record is
`docs/validation/changes/2026-10-09-fidelity-norm-3-7-0.md`.

13. **A nudge is layout:** a run raised or lowered by `w:position` is left out, as an underline
    that changes nothing is (in the narrative, the page and the drawing check, in a heading
    too), where all of these hold: the shift is at most 2 half-points (a point) up or down; the
    run's size is its paragraph's (the reader's `position±N-size<run>-in<paragraph>`,
    `docx-reader/1.34.0`); and no code point of it is superscript or subscript. Every other
    shift is still refused (`formatting`): a larger one, a run smaller or larger than its
    paragraph's text (a superscript typed by hand is raised and smaller), or a shift with
    `vertAlign`. Rationale: a full-size run moved up or down by at most a point reads as the
    same characters on the same line; it cannot become an exponent, an index or a footnote mark,
    which are smaller or moved further. Evidence (EMA's English Word PI cuts, internal, counts
    only): shifts of -1 (1 941 uses), +2 (635) and +1 (197) dominate, and `formatting` on a
    position blocks 21 SmPC files.

Not an owner decision, the same change: **the 15% pattern greys** (`shading-pct15-AUTO-AUTO`,
`shading-pct15-AUTO-FFFFFF`) are the template's grey, by decision 9's print: Word draws each as
exactly #D9D9D9, `shading-D9D9D9`'s colour, on a white page, over a cell shaded FFFF00 and over a
paragraph shaded D9D9D9 (Word for Mac to PDF, 2026-10-07; an automatic fill is opaque white). What
held them back in 3.4.0 is gone: from `docx-reader/1.34.0` the reader spells a pattern's theme
colour and theme fill (`shading-pct15-THEME-accent2-AUTO`, which Word draws #FCEBE0), so an
automatic colour is the automatic one. Every other pattern stays refused: 10% or 20%, another
colour or fill, `shading-pct15-AUTO-D9D9D9`, a theme's. Evidence: in the built but blocked files,
`shading-pct15-AUTO-AUTO` stands in 10 SmPC files and `shading-pct15-AUTO-FFFFFF` in 2 (and in
leaflets).

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
  worker takes each source's mapping by its document type, and a certified Word leaflet is
  transformed to the EMA's leaflet document, which passes the official validator, in a dry run
  only until the query service and the signer read one. A leaflet is carried only with its titles
  as written: a Type 2 leaflet, whose template titles write X, is refused. The certified Word importer (`certified-word-import/1.2.0`) carries `document: "pl"`
  with the leaflet's own product check: the name is what stands for X in its section 1 heading
  (the structure's `name`), the holder is section 6's, and the leaflet states no EU number, so its
  Type 1 record has no RegulatedAuthorization.
- **Still to do:** in the reader (P1), floating tables, frames, right-to-left tables and page
  breaks reported in place of the scan; P2; in P4, the drawing records (D3); P5.
