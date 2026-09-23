# `fidelity-norm/2.0.0`: markup may not change what a reader sees without the check seeing it

_Proposal, 2026-09-23, revised the same day after an independent adversarial review that ran
every case below against the reference and against an HTML parser (parse5). Changes
`docs/fidelity-normalization.md` sections 1, 2, 3, 5, 6, 7 and 9. Implemented only after this
revision is reviewed._

_Amended and implemented, 2026-09-23. An independent re-review of this revision found one more
live false pass (R1) and gaps in changes B, D, E, F, G, H and I; its eleven amendments (R1–R11,
"Amendments adopted" at the end of this note) were adopted exactly as worded and supersede the
text they name. Where the body below and an amendment disagree, the amendment and
`docs/fidelity-normalization.md` 2.0.0 decide; the body is kept as the reasoning that was
reviewed, with the statements the amendments reversed (E's page-text whitespace, F's
allowed list, the impact on real tables, and the root `id`) corrected in place. The change
record is `docs/validation/changes/2026-09-23-fidelity-norm-2-0-0.md`._

## Why

ADR 0003's rule is one sentence: **false failures are acceptable; false passes are not.** Each
case below verifies today while a reader sees something the source does not say. They are all
the same defect: the specification treats a piece of markup as plain text, and a renderer —
usually an HTML parser given the narrative as `innerHTML` — does not.

| #   | Verifies today                                                                                                                                           | A reader sees                                                              |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| 1   | `10<sup>6</sup> mg` against "106 mg"                                                                                                                     | 10⁶ mg                                                                     |
| 1b  | `10<sup/>6 mg` against "106 mg"                                                                                                                          | 10⁶ mg (an HTML parser ignores `/` on a non-void element)                  |
| 2   | `non&#173;&#13;<br/>smokers` against "nonsmokers"                                                                                                        | "non", a line break, "smokers"                                             |
| 3   | `non&#173;` + a raw line feed + `smokers` against "nonsmokers"                                                                                           | "non smokers"                                                              |
| 4   | a section starting at page 2 "safe for pregnant women" after page 1 ended "un&#173;" LF — or after a blank line, or with page 1 left out of the document | "safe for pregnant women"                                                  |
| 5   | `&#xD835;&#xDEFC;`, or a raw lone surrogate split by markup (`\uD835<b></b>\uDEFC`)                                                                      | replacement characters, where the check sees one letter                    |
| 6   | `Do <span class="d-none">not</span> take`                                                                                                                | "Do take", in a viewer whose stylesheet hides `d-none`                     |
| 7   | text, `span`, `p`, `b` or a nested table directly inside `table`, `tbody` or `tr`                                                                        | that content moved above the table                                         |
| 8   | a table whose cells are merged or spanned differently from the source (`<td>Daily 10 mg</td>`, `colspan`)                                                | "Adults: 1 mg" where the source table gives 1 mg to children               |
| 9   | `Age 2&#133;11 years` against "Age 2 11 years"; `≥ 30&#11;50 mg`                                                                                         | "2…11" (C1 characters are remapped through windows-1252); a box or nothing |
| 10  | `Do<br>not</br> take`                                                                                                                                    | "Do take" in an XML renderer, which draws no children of `br`              |

## The changes

### A. Only `br` and `hr` may be self-closing, and they must be (§5)

`<x/>` for any other element rejects, and so does a `br` or `hr` written as a start tag without
`/` (`void-element`). An HTML parser ignores the `/` on every other element, so `<sup/>6`,
`<a href="…"/>text`, `<li/>` and `<h1/>` open an element around the text that follows. Closes 1b
and 10.

### B. Super- and subscript digits and signs are folded to code points (§5)

Section 4 already says superscript and subscript code points are content (`m²` is not `m2`).
Markup must not be a way round it, for the characters that change what a number means:

- The content of `sup` and `sub` is text and character references only; a child element rejects
  (`script-content`). Folding happens after references are decoded.
- Inside `sup`: `0–9` → U+2070, U+00B9, U+00B2, U+00B3, U+2074–U+2079; `+` → U+207A; `-` and
  U+2212 → U+207B; `=` → U+207C; `(` → U+207D; `)` → U+207E.
- Inside `sub`: `0–9` → U+2080–U+2089; `+` → U+208A; `-` and U+2212 → U+208B; `=` → U+208C; `(` →
  U+208D; `)` → U+208E.
- Every other code point inside `sup` or `sub` is kept as it is. Raising a letter or a footnote
  mark does not change what it says, so `C<sub>max</sub>`, `AUC<sub>inf</sub>`,
  `t<sub>1/2</sub>`, `<sup>a</sup>` and `<sup>®</sup>` are accepted, and only their digits and
  signs are folded (`t<sub>1/2</sub>` is `t₁/₂`).

So `10<sup>6</sup>` is `10⁶`, which does not equal a source's `106`. Residual, stated: a letter
exponent (`2<sup>n</sup>` against `2n`) still verifies; it does not occur in the numbers of an
SmPC.

### C. A soft hyphen may not stand before a line break in narrative text (§5)

In page text, U+00AD before a line break is the extractor's mark for a word broken across lines
(§7), and §3 step 1 joins it. In XHTML a line break inside text is a space, and a block boundary
or `br` is a visible new line: neither is a hyphenated word. Replacing the current
`soft-hyphen-at-boundary` bullet:

- In the text the scanner emits, U+00AD followed by U+000A, or by U+000D and then U+000A,
  rejects (`soft-hyphen-at-boundary`), whatever produced the break: raw text, a reference, a
  block boundary or `br`. Page text is unchanged.

Closes 2 and 3.

### D. A section may not begin or end inside a word, across whitespace or pages (§1, §6)

- Pages are numbered exactly 1..N in the document; anything else is a structural error. (Today a
  missing page lets a section begin mid-word with nothing before it to look at.)
- **Start.** Take the nearest code point before the first span that is not §3 whitespace,
  searching back through that page's body and, if everything before the span is blank, through
  the previous page's body. If it is U+00AD, or a word character with no whitespace between it
  and the span, the result is `word-cut`.
- **End.** If the last span, with trailing §3 whitespace removed, ends in U+00AD, the result is
  `word-cut`. The existing rule for a span ending at `bodyEnd` stays.
- Everywhere the specification says "a U+00AD U+000A pair" it now says "U+00AD followed by U+000A
  or by U+000D U+000A".

Closes 4.

### E. Characters are checked as received and as decoded (§2)

- §2 applies to the `div` as received, where every unpaired UTF-16 unit is a surrogate code point
  and rejects, and to each character reference on its own as it is decoded, so a reference to half
  a pair rejects (`forbidden-character`) even when the next reference would complete it.
- U+0080–U+009F reject on both sides. U+0085 leaves the whitespace list in §3 step 5.
- In narrative text, raw or decoded, U+000B and U+000C reject: they are not XML characters, and a
  renderer draws them as nothing or as a box. ~~Page text keeps them as whitespace.~~ Amended
  (R2): they reject on both sides, and page text contains none.
- Bidirectional controls U+202A–U+202E and U+2066–U+2069 reject on both sides: their reach
  differs between a narrative block and page text, and no EU product-information language needs
  them.

Closes 5 and 9.

### F. No attribute can hide or restyle text (§5)

- `class` is removed from the allowed attributes.
- `lang`, `xml:lang` and `id` are allowed on the root `div` only, and `href` loses its `#token`
  form: a viewer's stylesheet or script can key on any of them to hide content, and a narrative
  needs none of them below the root.
- The allowed list becomes: `xmlns`, `xml:lang`, `lang`, `id` (root `div` only); `href` (on `a`
  only, `https://` form only); `scope`. Amended (R10): `id` is dropped entirely, the root
  included, and `scope` is allowed on `th` only — `xmlns` (root), `xml:lang` and `lang` (root
  `div` only), `href` (`a` only, `https://` form only), `scope` (`th` only).

Closes 6.

### G. Tables contain only table parts, cells cannot be spanned, and every row is the same width (§5)

Table cells carry meaning in 4.2, 4.5, 4.8 and 5.2: which dose belongs to which population,
which frequency to which reaction.

- The only children of `table` are `caption`, `thead`, `tbody`, `tfoot` and `tr`; `thead`,
  `tbody` and `tfoot` contain only `tr`; `tr` contains only `td` and `th`. Any other element,
  and any text or reference other than U+0009, U+000A, U+000D, U+0020 directly inside these,
  rejects (`table-content`).
- `colspan` and `rowspan` are removed, and `pre` is removed from the allowed elements: each lets
  cells or columns be drawn differently from the text the check sees.
- Every `tr` of one table has the same number of cells, `td` and `th` together; otherwise
  `table-shape`, checked when the table closes.

Closes 7, and the reproduced forms of 8. **It does not close 8:** two tables with the same text
and the same row width can still split that text into cells differently. Making cell and row
boundaries content on both sides needs a table extractor contract, and is the next major
version. Stated in "What this does not close".

### H. Order in which violations are decided (§5)

The reason code is inside `reportHash`, so both implementations must agree on which violation
wins. The scan is one pass in document order and the first violation decides, as today; §2
checks on the received `div` come first, and a decoded reference is checked where it occurs in the
pass. After a scan with no violation: `soft-hyphen-at-boundary` (on the emitted text), then
`empty-narrative` (the verifier, as today). `table-shape` is decided in the pass, at the closing
tag of the table.

### I. The extractor contract (§7)

- In a raised or lowered glyph run, every digit and sign of change B's tables is emitted as its
  script code point; other characters are emitted as they are. An extractor that cannot tell a
  glyph's baseline shift must mark the page unverifiable rather than emit plain digits.
- Table rows are emitted with one U+0009 between cells and the same cell count per row.

This is where the check is honest about its limit: **the narrative is derived from the page
text**, so if the text layer flattens `10⁹` to `109`, both sides say `109` and the check passes
against a PDF that shows `10⁹`. Only the extractor can close that, and until it proves it does,
it is a residual outside the check.

## Version and impact

**`fidelity-norm/2.0.0`, a major increment under §8**: extractor output that conformed to 1.1.1
(plain digits in a raised run, uneven table rows) no longer does, and spans that were valid (a
section beginning after a blank line that follows a hyphenated word) become `word-cut`.

- **Existing vectors that change**, each reviewed with its reason: `superscript-markup-over-plain-digit`
  (passed → mismatch: the case is the defect), `hidden-extra-element` (mismatch →
  `forbidden-attribute`), `allowed-attributes` (the root `class` → `forbidden-attribute`),
  `inline-dropped` (`2` → `²`), and every vector that uses `colspan`, `rowspan`, `pre`, `id` or
  `lang` below the root, a non-self-closing `br`, or U+0085 as whitespace. The implementation lists
  each one.
- **Persisted documents.** None of the synthetic products uses any construct above (they are
  plain `<div><p>…</p></div>`), so their narratives normalise to the same text. They were approved
  under 1.1.1, and the version is part of the approved content, so the demonstration store is
  re-seeded and re-approved when this ships.
- **Real labels.** Tables with merged or spanned cells, and `pre`, are refused until the table
  contract exists. That is the intended direction of error. Amended (R7): it does not block a
  spanned source table outright — the extractor emits a spanned cell's text once, in its first
  slot, and each other slot it covers as an empty cell, so the narrative can carry the same
  table as unspanned cells of one width; the engine (roadmap item 8) must produce such
  unspanned tables, and a narrative that keeps `colspan` or `rowspan` is refused.

## Lockstep

- The TypeScript reference `src/fidelity/{normalize,xhtml,verify}.ts` and the Python port
  `zone-a/src/zone_a/fidelity/{normalize,xhtml,verify}.py`.
- Golden vectors (`test/fixtures/fidelity/cases.ts` → `vectors.json`), a new vector for every
  row of the table above and each boundary of each rule.
- The differential generator (`scripts/fidelity/differential.ts`) extended to produce every new
  construct: self-closing tags, table children and shapes, surrogates split by markup and by
  reference, C1 and bidi characters, soft hyphens before whitespace and line breaks, pages that
  begin blank and documents with a missing page.
- Exported fixtures (`test/fixtures/contracts/*`), the agent test double's `NORMALIZATION_VERSION`
  and any test literal naming `fidelity-norm/1.1.1`.
- Specification §9 history, ADR 0003, the change record, `zone-a/README.md`.
- Deployment: the worker and the query service ship together; the demonstration store is
  re-seeded.

## What this does not close

- **Cell association** in tables of equal row width (above). Cell boundaries flatten to
  whitespace, so a narrative table with the same text and row width can move a value between
  populations — a dose from the Adults column to the Children column — and verify. The empty
  slots R7 requires for a spanned source cell make that easier, because an empty narrative cell
  costs nothing. Next major version, with the table extractor contract.
- **A flattened text layer** (change I). Outside the check until the extractor proves it.
- **Letter exponents** (change B).
- **A viewer's own stylesheet or script** acting on the element names that remain. After this
  change the narrative carries no class, no id (amended, R10: not even on the root) and no
  language tag below the root to attach them to.
- **Strong right-to-left letters** can reorder adjacent numbers (R9); no EU product-information
  language uses them.

## Amendments adopted (independent re-review, 2026-09-23)

Adopted exactly as worded; each supersedes the text of the change it names.

- **R1 (§1).** "`bodyEnd` must equal `bodyStart`, or the code point before it must be U+000A: a
  non-empty body always ends with its own line terminator." Closes a live false pass: page 1
  "The maximum daily dose is 1" with no final LF, page 2 "0 mg.\n", a section ending on page 1
  verified.
- **R2 (E).** "U+000B and U+000C reject on both sides (`forbidden-character`)." §7: "Page text
  contains no U+000B or U+000C; a page break is the page record, not a character." The vector
  `allows-tab-lf-cr-ff-vt` changes intentionally.
- **R3 (H), exact order.** At a start tag: malformed-tag/stray-lt, then uppercase-element, then
  unknown-element, then multiple-roots/root-not-div, then attributes in document order
  (forbidden-attribute; root without xmlns = root-not-div), then void-element, then the parent
  check, then table-structure, then table-section-order. The parent check: script-content if
  the parent is sup or sub; else misnested-tag for a table part (caption, thead, tbody, tfoot,
  tr, td, th) in the wrong parent; else table-content for any other element whose parent is
  table, thead, tbody, tfoot or tr. At an end tag: malformed-tag, then uppercase-element, then
  unbalanced-tag, then misnested-tag, then table-shape (for `</table>`). At `&`: stray-amp, then
  text-outside-root, then unknown-entity, then forbidden-character, then table-content. At a
  raw code point: text-outside-root or table-content. After a clean scan:
  soft-hyphen-at-boundary, then empty-narrative. (Pin: `rejects-cell-outside-row` stays
  misnested-tag; `rejects-unknown-element` (`<img/>`) stays unknown-element.) The
  implementation places `unmappable-script`, which R3 does not name, last at `&` and at a raw
  code point (it can co-occur with neither table code); the specification states it.
- **R4 (D).** Start: "Read backwards from the code point before the first span's start offset
  through page n's body. On passing its bodyStart, continue from the last code point of page
  n−1's body, and so on through earlier pages. Only body text is read, and earlier pages are
  read as declared even if they fail §1 or §2. Skip code points in the §3 step 5 whitespace list
  and stop at the first other code point c. The result is word-cut if c is U+00AD, or if c is a
  word character and no code point was skipped. If reading passes the start of page 1's body,
  there is no cut." End: "The result is word-cut if the last span, with trailing §3 step 5
  whitespace removed, ends in U+00AD, or if the code point at its end offset is a word character
  and lies inside the body." Pages: "The `page` values of `pages`, in array order, are 1, 2, …,
  N; anything else is a structural error." These replace the start and end rules of change D;
  the re-review simulated them over all golden verify vectors and no currently verified vector
  changes (confirmed by the implementation).
- **R5 (B).** "Folding applies to U+0030–U+0039. Inside sup, U+2010–U+2014, U+FE63 and U+FF0D
  fold to U+207B, and U+FE62 and U+FF0B fold to U+207A (as well as '-', U+2212 → U+207B and '+'
  → U+207A). Inside sub, the same characters fold to U+208B and U+208A. Inside sup or sub, any
  other code point of general category N that is not a target of these tables, and U+00B1 and
  U+2213, reject (unmappable-script)." Other code points (letters, footnote marks, ®) are kept
  unchanged. §7's extractor rule mirrors these tables. The first implementation read "target
  of these tables" as the script digits of either table, kept in both `sup` and `sub`; the
  second review reversed that (L1, below): only the element's own script digits and signs are
  kept, and the other script's reject.
- **R6 (E).** "§2 applies to the div string as decoded from JSON (RFC 8259, where an escaped
  surrogate pair is one code point). It covers every code point, including markup, attribute
  values and text outside the root. A surrogate code point that remains rejects. The code is
  forbidden-character, and this check precedes the scan." Each decoded character reference is
  also checked on its own (a reference to half a pair rejects even if the next completes it).
- **R7 (I).** Unverifiable: "…must refuse the document (emit no SourceDocumentText)." Tables: "a
  spanned cell's text is emitted once, in its first slot, and each other slot it covers as an
  empty cell; a line break inside a cell is emitted as U+0020, except a discretionary hyphen,
  which is U+00AD U+000A." "Blocks many real 4.8 tables" is softened accordingly (above).
- **R8 (G).** "Any character reference, and any raw code point other than U+0009, U+000A, U+000D
  and U+0020" directly inside table/thead/tbody/tfoot/tr rejects (table-content). "Every tr of a
  table, in whichever section, has the same number of td and th children; cells of a nested
  table do not count." A table with no rows is accepted.
- **R9 (E).** Bidi rejection on both sides also covers U+200E, U+200F and U+061C (with
  U+202A–U+202E, U+2066–U+2069). Added to "What this does not close": strong right-to-left
  letters can reorder adjacent numbers; no EU product-information language uses them.
- **R10 (F).** Drop `id` entirely (root too); `scope` allowed on `th` only. Allowed attributes:
  xmlns (root), xml:lang and lang (root div only), href (a only, https:// form only), scope (th
  only).
- **R11 (lockstep).** The differential generator treated U+0085, U+000B and U+000C as
  whitespace; it now draws them as section 2 characters, so generated cases exercise §3 rather
  than mostly returning forbidden-character, and it produces every new construct: self-closing
  tags, sup/sub with digits, signs, letters, dashes and non-ASCII digits, table children and
  uneven rows, surrogates split by markup and by reference, C1, bidi, VT and FF characters, soft
  hyphens before whitespace and line breaks (raw, entity, CR LF, br, block), pages beginning
  blank, missing and misnumbered pages, and page bodies without a final LF. `XhtmlErrorCode`
  and the reason-code catalogue gain `void-element`, `script-content`, `unmappable-script`,
  `table-content` and `table-shape`. The query-tools UR-22 record notes that `verify_quote` now
  returns `invalid-request` for a quote containing a C1, bidi, VT or FF character. Impact: real
  PDFs whose text layer was decoded as Latin-1 (U+0092, U+0095, U+0096) now fail the page —
  intended.

## Second review, folded into 2.0.0 (2026-09-23)

A second independent review of the implementation found false passes before 2.0.0 was released.
They were fixed in 2.0.0 itself, not in a new version, and `docs/fidelity-normalization.md`
states each rule; `docs/validation/changes/2026-09-23-fidelity-norm-2-0-0.md` records them.

- **C1.** Whitespace inside a tag was JavaScript's `\s`, which includes U+00A0, U+2000–U+200A,
  U+3000 and U+FEFF; an HTML parser reads those as part of the tag name, so `10<sup` U+00A0
  `>6` rendered "106" and verified against "10⁶", and `br`, `table` and `td` written that way
  vanished. Now `[\t\n\r ]` only; anything else is `malformed-tag`.
- **C2.** Word characters were letters, digits and marks, so punctuation was a boundary:
  "Maximum dose is 1" verified against "Maximum dose is 1.5 mg", and a section could start at
  "20 °C." after "−" or "5 mg." after "0.". Now a span edge must touch whitespace (or a body
  edge) whatever the code point beyond it; R4's look-back stays. This replaces R4's "word
  character" tests.
- **C3.** Step 4 replaced bullet glyphs anywhere, so `2∙10` verified against a table row reading
  2 and 10. Now a bullet is replaced only at a line start and before whitespace, and U+2219 and
  U+2043 are not bullets.
- **L1.** Inside `sup`, subscript digits and signs reject; inside `sub`, superscript ones do
  (this reverses the reading of "not a target" recorded under R5). U+2015, U+02D7, U+FE58 and
  U+2796 fold to minus and U+2795 to plus.
- **L2.** A span field that is not an integer, or a page number that is a boolean, is a
  structural error in both implementations (Python tests for `bool` before `int`).

### Round 2 of the second review (2026-09-23)

- **Text line breaks.** A raw or referenced U+000A or U+000D in narrative text is emitted as
  U+0020, because a renderer draws it as a space: `<p>Take 2` U+000A `• 10 mg</p>` verified
  against a page whose "• 10 mg" starts a new line. Only a block boundary or `br` emits U+000A,
  so `soft-hyphen-at-boundary` now concerns only those; U+00AD before a text line break is
  followed by a space and joins nothing.
- **Grouped numbers at section edges.** A section edge with a digit on its inner side and a
  digit as the first non-whitespace code point beyond it, on the same line, is a cut; U+00A0,
  U+2007 and U+202F are not edge whitespace. "…is 10" of "…is 10 000 IU" verified.
- **Bullets in table cells.** A bullet glyph on a line that contains U+0009 is content, and the
  scanner writes a table cell and everything inside it on a U+0009-separated line, so a bullet
  in a cell is never a list item on either side (`<td>2</td><td>• 10</td>` verified against the
  row 2 U+0009 10). To keep normalisation idempotent, the start of a text is no longer a line
  start for step 4; the narrative's text begins with U+000A, and the verifier reads a page
  slice from its line terminator. This departs from C3's wording "after text start or LF",
  deliberately: with the start of the text counted, a bullet kept for its U+0009 would be
  replaced when the result was normalised again.
- **Script letters and symbols.** Inside `sub`, U+2071 and U+207F reject; inside `sup`,
  U+2090–U+209C reject; inside either, any code point of general category Sm, Ps, Pe or Pd that
  is neither a source nor a target of the fold tables rejects (`unmappable-script`), which
  covers U+FF1D, U+FE59, U+2E3A, U+FE31, `~`, `<` and `[`.
