# `fidelity-norm/2.0.0`: markup may not change what a reader sees without the check seeing it

_Proposal, 2026-09-23, revised the same day after an independent adversarial review that ran
every case below against the reference and against an HTML parser (parse5). Changes
`docs/fidelity-normalization.md` sections 1, 2, 3, 5, 6, 7 and 9. Implemented only after this
revision is reviewed._

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
  renderer draws them as nothing or as a box. Page text keeps them as whitespace.
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
  only, `https://` form only); `scope`.

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
  contract exists. That is the intended direction of error, and it blocks many real 4.8 tables:
  the engine (roadmap item 8) must produce unspanned tables, or the table contract comes first.

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

- **Cell association** in tables of equal row width (above). Next major version, with the table
  extractor contract.
- **A flattened text layer** (change I). Outside the check until the extractor proves it.
- **Letter exponents** (change B).
- **A viewer's own stylesheet or script** acting on the element names that remain. After this
  change the narrative carries no class, no id below the root and no language tag below the root
  to attach them to.
