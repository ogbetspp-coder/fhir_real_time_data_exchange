# Narrative fidelity normalisation specification

Version: `fidelity-norm/3.1.0` (`NORMALIZATION_VERSION` in `src/fidelity/normalize.ts`; history
in section 9)

This document is the language-neutral specification of the text normalisation and XHTML
extraction used by the narrative fidelity check (ADR 0003). The TypeScript implementation in
`src/fidelity/` and any re-implementation must produce identical results for the golden vectors
in `test/fixtures/fidelity/`. Every rule below is applied identically to the extractor's page
text and to the narrative text; nothing is applied to one side only, with two exceptions (section 2): the
reserved code points reject in narrative and are written by the extractor into page text,
because they carry the table grids and pictures that section 5 emits; and U+00AD and U+200B
reject in narrative, while page text keeps U+00AD as the mark of a word hyphenated at a line end.

## 1. Text units

- Offsets, lengths, and slices are counted in Unicode code points (not UTF-16 code units, not
  bytes). In TypeScript iterate with `Array.from(text)`; in Python index `str` directly.
- A span's `textSha256` is the SHA-256 hex digest of the UTF-8 encoding of the raw
  (un-normalised) slice `text[startOffset:endOffset]`.
- The `page` values of `pages`, in array order, are 1, 2, …, N; anything else is a structural
  error. A document therefore cannot leave out the page a section's first word is read against
  (section 6).
- A page's body is the half-open range `[bodyStart, bodyEnd)` in code points. Spans must lie
  inside the body. Text outside the body (running headers, footers, page numbers) is ignored
  by contiguity checks, but the report records the whole page length (`pageCodePoints`) next
  to the body length so excluded text is visible.
- The body range is declared by the extractor and is bounded rather than trusted: `bodyStart`
  must be 0 or immediately follow U+000A, and that U+000A must not itself follow U+00AD,
  directly or through U+000D (step 1 would delete it, so it lies inside a word); `bodyEnd` must
  equal `bodyStart`, or the code point before it must be U+000A: a non-empty body always ends
  with its own line terminator. This holds at the end of the page too. Section 6 lets a section
  end at `bodyEnd` without reading further, and that is sound only because the code point
  before `bodyEnd` is then a line feed, which is whitespace; without this rule a page ending
  "… is 1" with no line feed, followed by a page beginning "0 mg", let a section end at "1".
  The rule makes every page break a line break in the text the check reads; it does not prove
  that the line break separates words in the document — that is the extractor's contract
  (section 7: a word broken across lines ends in U+00AD). U+00AD followed by U+000A is allowed
  at the end of a body because a word may continue on the next page; section 6 decides whether
  a section may end there. A page may
  exclude at most 240 code points in total. A page that violates any of these makes every span
  on it `span-not-found` (reason `body-boundary` or `excluded-text`) and the report `failed`.
- Every hash of a JSON value (`reportHash`, `extractedTextSha256`, `narrativeBindingSha256`,
  the contract hashes) is the SHA-256 of canonical JSON: object keys sorted by UTF-16 code
  unit order (RFC 8785), no insignificant whitespace, `JSON.stringify` number and string
  formatting. Locale-aware sorting is never used. Two consequences an implementation must
  honour explicitly: the sorted order must be emitted directly, never obtained by inserting
  keys into a language-native object or map that reorders integer-like keys (`"2"`, `"10"`);
  and strings are written as `JSON.stringify` writes them, so an unpaired surrogate is emitted
  as the escape `\udXXX`, never as a raw code unit. Every number in a hashed structure is an
  integer; a value such as `1.0` is the integer 1 wherever it is read.
- `extractedTextSha256` is the hash of the whole `SourceDocumentText` value the check ran
  against. `reportHash` is the hash of the report with its own `reportHash` member removed;
  every other member of the report, including `issues` and each section result, is inside it.
- `narrativeBindingSha256` is the hash of an array, in Composition section order, of objects
  `{ "sourceKey", "normalizedTextSha256" }`, where `normalizedTextSha256` is the SHA-256 of the
  UTF-8 bytes of the section's normalised narrative (section 5 then section 3), or JSON `null`
  when the narrative cannot be normalised. It is computable from the Bundle alone.

## 2. Rejection (before any normalisation)

The input is malformed, and the section fails with `malformed-narrative` (reason
`forbidden-character`), if it contains:

- U+FFFD REPLACEMENT CHARACTER;
- any C0 control other than U+0009 TAB, U+000A LF and U+000D CR;
- U+007F DELETE, and the C1 controls U+0080–U+009F;
- U+FFFE or U+FFFF;
- the bidirectional controls U+061C, U+200E, U+200F, U+202A–U+202E and U+2066–U+2069;
- from 3.0.0, the interlinear annotation controls U+FFF9–U+FFFB and the prepended
  concatenation marks U+0600–U+0605, U+06DD, U+070F, U+0890, U+0891, U+08E2, U+110BD and
  U+110CD;
- an unpaired UTF-16 surrogate (a code point in U+D800–U+DFFF).

U+000B and U+000C reject on both sides (`forbidden-character`): they are not XML characters,
and a renderer draws them as nothing or as a box. The C1 controls reject because a renderer
shows something the check does not see: an HTML parser remaps a character reference to all but
five of them through windows-1252 (`&#133;` is drawn as "…"), and a literal one is drawn as a
blank or a box. The bidirectional controls reject because their reach
differs between a narrative block and a line of page text, and no EU product-information
language needs them. The interlinear annotation controls are reserved by Unicode for internal
use and drawn as a blank ("Take 10 " U+FFF9 "000" is drawn as one number with a gap in it), and a
prepended concatenation mark is drawn across the digits after it (U+070F draws a bar over "000",
which reads as a repeating decimal); no EU language needs them either.

Rejection applies to page text as well; a page containing these characters makes every span on
it `span-not-found`.

**Invisible breaks (narrative only).** A narrative `div` whose text holds U+00AD SOFT HYPHEN or
U+200B ZERO WIDTH SPACE, raw or as a character reference, is `malformed-narrative` with reason
`invisible-character` (from 3.0.0), decided after `reserved-character` for the whole `div` and
for each decoded reference. Both are break opportunities a renderer takes at a narrow width,
drawing "2-" / "10 mg" or "2" / "10 mg" where the check reads "210 mg". The rule is one-sided:
page text keeps U+00AD as the extractor's mark for a word hyphenated at a line end (section 7),
and step 1 still deletes both there. (Before 3.0.0 a narrative could hold U+00AD, and a rule
refused it only before a block boundary; that rule, `soft-hyphen-at-boundary`, is withdrawn.)

**Reserved code points (narrative only).** U+FFFC OBJECT REPLACEMENT CHARACTER and the
noncharacters U+FDD0–U+FDEF are reserved: the scanner emits U+FFFC for a picture and U+FDD0–U+FDD5
for a table's grid (section 5), so they never occur in narrative text itself. A narrative `div`
that contains one is `malformed-narrative` with reason `reserved-character`, decided after the
`forbidden-character` check of the whole `div` and before the scan; a character reference that
decodes to one is `reserved-character`, decided right after its own `forbidden-character` check.
This rule is one-sided: page text may contain these code points, because the extractor writes
them for grids and pictures (section 7), and an extractor whose text layer itself contains one
must refuse the document.

For narrative, section 2 applies to the div string as decoded from JSON (RFC 8259, where an
escaped surrogate pair is one code point). It covers every code point, including markup,
attribute values and text outside the root. A surrogate code point that remains rejects. The
code is `forbidden-character`, and this check precedes the scan. Each character reference is
also checked on its own as the scan decodes it (section 5): a reference to half a surrogate
pair rejects even if the next reference completes it.

## 3. Normalisation steps (ordered)

1. Delete invisible formatting characters — closed list: U+00AD SOFT HYPHEN, U+200B ZERO WIDTH
   SPACE, U+FEFF ZERO WIDTH NO-BREAK SPACE, U+2060 WORD JOINER. When U+00AD is immediately
   followed by U+000A (or U+000D U+000A), that line break is deleted with it: a soft hyphen at a
   line end marks a word broken across lines.
2. Expand ligatures — closed list: U+FB00 → `ff`, U+FB01 → `fi`, U+FB02 → `fl`, U+FB03 → `ffi`,
   U+FB04 → `ffl`, U+FB06 → `st`. (NFKC is not used: it would also flatten superscripts and
   subscripts, which are content.)
3. Unicode Normalization Form C (NFC). It runs after steps 1 and 2 so that a composition an
   invisible character or a ligature would otherwise block (`e` + ZWSP + combining acute) is
   applied in the first pass.
4. Replace a bullet glyph that starts a list item with U+0020 — closed list: U+2022, U+2023,
   U+25A0, U+25A1, U+25AA, U+25AB, U+25CB, U+25CF, U+25E6. A bullet glyph starts a list item
   only when all three hold: it is at the start of a line — after U+000A, plus optional step 5
   whitespace; step 5 whitespace follows it; and its line (the text between the U+000A before
   it and the next U+000A or the end) contains no U+0009. A bullet glyph replaced by this step
   counts as whitespace for a bullet glyph after it (so U+000A `• • x` is `x`). Anywhere else a
   bullet glyph is content: `2 • 10` in a line is not `2 10`. U+2219 BULLET OPERATOR and U+2043
   HYPHEN BULLET are not in the list, because one is a multiplication sign and the other a
   dash: `2∙10` against a table row reading `2` and `10` is a mismatch. (Positions are those of
   the text after step 3.)

   A line that contains U+0009 is a table row: the extractor writes a row as cells separated by
   U+0009 (section 7), and the scanner writes a table cell, and everything inside one, on a line
   of U+0009-separated text (section 5). A bullet glyph in a table cell is therefore content on
   both sides, wherever it stands in the cell: `<td>2</td><td>• 10</td>` does not verify
   against the row `2` U+0009 `10`, and a page row `• 10` U+0009 `2` needs a narrative cell that
   shows `• 10`. The rule applies identically to both sides, with one addition for page text:
   when the verifier normalises a slice of page text whose last line continues past the slice
   on the page, that line's U+0009 status is the status of the whole page line in the body, not
   of its part inside the slice (section 6). A span that stops before a row's U+0009 (`• Adults`
   of `• Adults` U+0009 `10 mg`) therefore still reads the bullet as content.

   The start of the text is not the start of a line. Normalised text contains no U+000A, so
   normalising it again replaces no bullet glyph; with the start of the text counted as a line
   start, a bullet kept because its line contains U+0009 would be replaced the second time, once
   that U+0009 had become U+0020. The narrative's text always begins with U+000A (the root
   `div`, section 5), and the verifier reads a page slice from its line terminator (section 6),
   so a list item at the start of a section is still a list item on both sides.

5. Replace every whitespace-class code point with U+0020 — closed list: U+0009, U+000A,
   U+000D, U+0020, U+00A0, U+2000–U+2005, U+2007, U+2008, U+2028, U+2029, U+3000.
   Then collapse runs of U+0020 to a single U+0020 and remove leading and trailing U+0020.
   U+000B, U+000C and U+0085 are not in the list: section 2 rejects them. U+1680 OGHAM SPACE
   MARK is not in the list (from 3.0.0): a renderer draws it as a stroke, so "Take 2" U+1680
   "10 mg" reads as a range, not as two numbers, and it is content. Nor are the spaces
   narrower than a quarter of an em, U+2006, U+2009, U+200A, U+202F and U+205F (from 3.0.0):
   at text sizes a renderer draws them two to five pixels wide, and in some fonts barely wider
   than nothing, so "2" U+200A "10" looks like "210"; they are content. Section 6 still reads them as a gap
   between the groups of a number.

The procedure is idempotent: applying it twice yields the first result (the golden vectors
include the blocking cases that make step order matter, and step 4's rule that the start of the
text is not a line start is what keeps it idempotent with the U+0009 rule).

## 4. Deliberately not normalised

These are treated as content; a difference is a mismatch:

- letter case;
- straight versus typographic quotation marks and apostrophes (U+0027, U+0022, U+2018, U+2019,
  U+201C, U+201D and others);
- hyphen and dash variants (U+002D, U+2010–U+2015, U+2212) and line-break de-hyphenation
  (`intra-` at a line end followed by `venous` stays `intra- venous`; the extractor must emit
  discretionary hyphens as U+00AD so that step 1 removes them together with the line break);
- superscript and subscript code points (`m²`, `H₂O`) versus plain digits. Markup cannot turn
  one into the other: the digits and signs inside `sup` and `sub` are folded to these code
  points (section 5), so `m<sup>2</sup>` is `m²` and never equals a source's `m2`;
- footnote and reference markers, numbered-list markers (`1.`, `a)`), and other punctuation (an
  `ol` emits the markers a renderer draws as text, section 5, so they are compared too);
- U+200C ZERO WIDTH NON-JOINER and U+200D ZERO WIDTH JOINER;
- any character not named in section 3.

## 5. XHTML to text

The narrative `text.div` is scanned without a DOM. Any violation makes the section
`malformed-narrative`. The rule behind every bullet is the same: markup may not change what a
reader sees without the check seeing it. The renderer each rule was checked against is Chrome on
macOS with its default fonts, in HTML and XML modes; another platform's fonts can draw a code
point differently (a Japanese or Korean face on Windows draws U+005C as "¥" or "₩" under a `ja`
or `ko` root), which is a stated residual. Where a renderer — usually an HTML parser given the
narrative as `innerHTML` — would draw markup differently from the text the scanner emits, the
markup is folded into the text or rejected.

- Exactly one root element `div` carrying `xmlns="http://www.w3.org/1999/xhtml"`; only
  whitespace may appear outside it.
- Element names are lower-case. Block elements emit a line break before their start tag and
  after their end tag: `div p h1 h2 h3 h4 h5 h6 ul ol li table thead tbody tfoot tr td th
caption blockquote dl dt dd hr`. `br` emits a line break. Inline elements contribute only their
  text: `span b i em strong sup sub small abbr cite code` (`sup` and `sub` fold theirs,
  below); `img` is inline and emits a picture (below).
- The line break a block element or `br` emits is U+000A, except that the start and end tags
  of `td` and `th`, and every block element and `br` inside an open `td` or `th`, emit
  U+0009: a table cell and everything in it is on one line of U+0009-separated text, as the
  extractor writes a table row (section 7). So a bullet glyph in a cell is never a list item
  (section 3 step 4), and a soft hyphen before a break inside a cell joins nothing.
- A line feed or carriage return in text — raw U+000A or U+000D, or a reference to either — is
  emitted as U+0020. A renderer draws it as a space: only a block boundary or `br` is a line
  break. So `<p>Take 2` U+000A `• 10 mg</p>`, which renders "Take 2 • 10 mg", reads "Take 2 •
  10 mg" to the check (the bullet is mid-line and so content), and does not verify against a
  page whose "• 10 mg" starts a new line.
- Only `br`, `hr` and `img` may be self-closing, and they must be: `<x/>` for any other
  element, and a `br`, `hr` or `img` written as a start tag without `/` (`<br>`, `<hr></hr>`,
  `<img src="x">`), reject (`void-element`). An HTML parser ignores the `/` on every other
  element, so `<sup/>6`, `<b/>text` and `<li/>` open an element around the text that
  follows; and an XML renderer draws no children of a `br` written `<br>…</br>`.
- Any other element (including `script`, `style`, `svg`, `object`, `iframe`, `del`, `s`,
  `strike`, `math`, form controls, and `q`, whose renderer generates quotation marks the source
  may not contain, and `pre`, which keeps whitespace a renderer draws as columns the check
  cannot see) rejects (`unknown-element`). From 3.0.0 so do `u` and `a`: a renderer underlines
  both (`a` when it has a target), and an underline turns what it underlines into another sign
  or word — `CrCl <u>&lt;</u> 30` is drawn "CrCl ≤ 30", `&gt;` "≥", `+` "±", `=` "≡", the
  modifier letter U+02C2 exactly "≤", and `1<u>a</u>` "1ª" — which no closed list of code
  points bounds. A narrative keeps the words of a link, not its target (ADR 0005).
- Lists. `li` is allowed only as a direct child of `ol` or `ul`; anywhere else it is
  `misnested-tag` (an HTML parser closes an open `li` at the next `<li>`, and an `li` nested
  anywhere inside an `ol`'s item continues its numbering). The only children of `ol` and `ul`
  are `li`: any other element directly inside, any character reference, and any raw code point
  other than U+0009, U+000A, U+000D and U+0020 directly inside, rejects (`list-content`); a
  renderer draws such content outside the numbering. `li` of a `ul` emits nothing of its own:
  a bullet carries no content, and section 3 step 4 removes a list bullet from page text. Each
  `li` of an `ol` emits, directly after the line break of its start tag, its marker, then `.`,
  then U+0020, because a renderer draws it. The item's ordinal is the `ol`'s `start`, or one
  when it has none, plus the item's index among the `li` children, counted from 0. The marker is
  the ordinal in the counter style the `ol`'s `type` names; an `ol` without `type` is decimal,
  whatever list encloses it:
  - `1`: decimal; a negative ordinal is U+002D followed by its magnitude; no leading zeros.
  - `a` and `A`: alphabetic, for an ordinal of 1 or more. Start from an empty string; while
    `n > 0`: `n = n − 1`, prepend the letter at index `n mod 26` of `a…z` (`A…Z` for `A`),
    `n = floor(n / 26)`. So 26 is `z`, 27 is `aa` and 703 is `aaa`. An ordinal of 0 or less is
    decimal.
  - `i` and `I`: additive roman, for 1 to 3999. Repeatedly append the letters of the largest
    value in 1000 `m`, 900 `cm`, 500 `d`, 400 `cd`, 100 `c`, 90 `xc`, 50 `l`, 40 `xl`, 10 `x`,
    9 `ix`, 5 `v`, 4 `iv`, 1 `i` that is not more than the remainder; upper case for `I`. Any
    other ordinal is decimal.

  What this does not claim: a viewer that applies its own list style, or draws no list numbers
  at all (a plain XML view), shows something other than what the check read; the narrative
  cannot carry a stylesheet, so the style is the one `type` names. List nesting is not compared
  (`<ol><li>A<ol><li>B</li></ol></li></ol>` and two consecutive lists both read `1. A 1. B`),
  and neither is a `ul` bullet (`<ul><li>1. x</li></ul>` reads as `<ol><li>x</li></ol>`): both
  are indentation and bullets, which section 3 step 4 already removes from page text.

- Pictures. `img` has exactly one attribute, `src`, a PNG or JPEG `data:` URI (below), and must
  have it (`forbidden-attribute` otherwise, including for `alt`, which a renderer draws when a
  picture does not load and which would be compared with nothing; the missing `src` is decided
  after the attributes and before `void-element`). A reference (a path or a URL) is refused:
  what it draws is whatever the viewer's origin serves at that path, or nothing, and neither is
  bound by the check. `img` emits U+FFFC, the 64 lower-case hexadecimal digits of the SHA-256 of
  the UTF-8 bytes of its `src` value, and U+FFFC again, with no whitespace added, so the text
  states where each picture stands and which bytes it draws; the closing U+FFFC composes with
  nothing, so a combining mark after a picture cannot join its last digit (section 3 step 3).
  What a picture shows is not read by any text check; a `data:` media type is not checked
  against the bytes, which a renderer sniffs, but it draws the same bytes on both sides.
- The content of `sup` and `sub` is text and character references only; a child element
  rejects (`script-content`). Folding happens after references are decoded. Inside `sup`:
  `0`–`9` → U+2070, U+00B9, U+00B2, U+00B3, U+2074–U+2079; `+` → U+207A; `-` and U+2212 →
  U+207B; `=` → U+207C; `(` → U+207D; `)` → U+207E. Inside `sub`: `0`–`9` → U+2080–U+2089;
  `+` → U+208A; `-` and U+2212 → U+208B; `=` → U+208C; `(` → U+208D; `)` → U+208E. Folding
  applies to U+0030–U+0039. Inside sup, U+2010–U+2015, U+02D7, U+FE58, U+FE63, U+FF0D and
  U+2796 fold to U+207B, and U+FE62, U+FF0B and U+2795 fold to U+207A (as well as '-', U+2212
  → U+207B and '+' → U+207A). Inside sub, the same characters fold to U+208B and U+208A.
  Inside sup, the subscript digits U+2080–U+2089 and the subscript signs U+208A–U+208E reject
  (`unmappable-script`); inside sub, the superscript digits U+2070, U+00B9, U+00B2, U+00B3,
  U+2074–U+2079 and the superscript signs U+207A–U+207E reject: a subscript digit raised is not
  a superscript one, and the check would otherwise see a character the reader does not. In the
  same way, inside sub the superscript letters U+2071 and U+207F, and inside sup the subscript
  letters U+2090–U+209C, reject. Inside sup or sub, any other code point of general category N
  that is not a target of the element's own table, any code point of general category Sm, Ps,
  Pe or Pd that is neither a source nor a target of the fold tables (`＝` U+FF1D, `﹙` U+FE59,
  `⸺` U+2E3A, `︱` U+FE31, `~`, `<`, `[`), and U+00B1 and U+2213, reject (`unmappable-script`).
  The element's own script digits and signs (U+2070, U+00B9, U+00B2, U+00B3, U+2074–U+207E
  inside `sup`; U+2080–U+208E inside `sub`) are kept. From 3.1.0, inside `sub` only, U+221E
  INFINITY is also kept unchanged (`AUC<sub>(0-∞)</sub>` is `AUC₍₀₋∞₎`), and so is U+00BD
  VULGAR FRACTION ONE HALF under the lowered-half rule below (`t<sub>½</sub>` is `t½`). Neither
  has a subscript form. Inside `sup` both still reject: `2<sup>½</sup>` is a square root, not
  "2½". Every other code point of general category N, Sm, Ps, Pe or Pd inside `sub` still
  rejects as stated above. Other code points (letters, footnote
  marks, ®, `/`) are kept unchanged. Raising a letter or a mark is taken not to change what it
  says, which is not always so: `10<sup>n</sup>` reads "10n" and verifies against a plain "10n"
  (a stated residual, as in ADR 0003),
  so `C<sub>max</sub>`, `<sup>a</sup>` and `<sup>®</sup>` are accepted, and `t<sub>1/2</sub>`
  is `t₁/₂`.
- Tables contain only table parts. The only children of `table` are `caption`, `thead`,
  `tbody`, `tfoot` and `tr`; `thead`, `tbody` and `tfoot` contain only `tr`; `tr` contains only
  `td` and `th`. Any other element directly inside `table`, `thead`, `tbody`, `tfoot` or `tr`
  rejects (`table-content`), and so does any character reference, and any raw code point other
  than U+0009, U+000A, U+000D and U+0020, directly inside them: a renderer moves such content
  out of the table. A `table` start tag while another table is open — in a cell, in a caption,
  at any depth — rejects (`table-structure`), so a table's grid text never nests.
- Table grids. Cells are placed by the HTML table model: in each row a cell takes the first
  slot, from the left, that no cell covers; it covers `colspan` slots of its own row and of each
  of the next `rowspan − 1` rows (both default to 1). A cell that would cover a slot of its own
  row that is already covered (a `colspan` running into a cell spanning down from above) rejects
  (`table-shape`), decided at its start tag; that is the only way two cells can overlap. A cell
  whose rows run past the last row of its row group (`thead`, a `tbody`, `tfoot`, or the rows
  directly under `table`) rejects (`table-shape`), decided at the end tag of that group, or at
  `</table>` for rows directly under it: a renderer clips it silently. A row that covers a slot
  but in which no cell spanning one row starts rejects (`table-shape`), decided at its `</tr>`:
  a renderer draws it at zero height, so a cell that starts in it and spans down reads, drawn,
  against the next row only. At `</table>`, every row must cover exactly the slots 0 … w−1,
  with one w for the whole table (otherwise a ragged row, or a slot inside a row that no cell
  covers), and in every column 0 … w−1 a cell spanning one column must start (a renderer draws
  a column without one at zero width, its spanning cells' text reading against the columns
  beside it); otherwise `table-shape`, decided in that order after the row group's clipped
  spans. A table with no rows, or whose rows all cover no slot (w = 0), is accepted; next to a
  row that covers a slot, a row that covers none is ragged.
  The tables of one narrative cover at most 50 000 slots together, counting each cell's
  `colspan` × `rowspan` when it is placed; the cell that crosses the bound rejects
  (`table-size`), decided after its overlap check: a small table can span a grid whose markers
  are hundreds of times its own length.

  The text carries the grid, in six reserved code points (section 2): U+FDD0 (table) after the
  line break of `<table>`; U+000A then U+FDD1 (end of table) before the line break of
  `</table>`; U+FDD2 (row) after the line break of `<tr>`; U+FDD3 (cell) then U+0009 after the
  U+0009 of a `td` or `th` start tag. A slot a cell covers without starting in it is U+0009, a
  marker, U+0009: U+FDD4 when it is covered by the cell to its left in that cell's own row (its
  `colspan`), U+FDD5 when it is covered by a cell in a row above (its `rowspan`). The U+FDD5
  slots to the left of a cell are emitted just before the cell's start-tag break; a cell's
  U+FDD4 slots just after its end-tag break; the U+FDD5 slots after a row's last cell just
  before the line break of `</tr>`. A caption's text stands between U+FDD0 and the first U+FDD2.
  After normalisation each marker is its own token, and the text determines the drawn grid:
  rows are delimited by U+FDD2, a U+FDD3 slot holds the text up to the next marker, a U+FDD4
  slot belongs to the cell owning the slot to its left, a U+FDD5 slot to the cell owning the
  slot above, and U+FDD1 ends the table, so text after it is not in the last cell. Which cell a
  value is in is therefore checked, and an empty cell is distinct from a covered slot. Not
  compared: whitespace inside a cell, `td` versus `th`, `scope`, and which row group a row is
  in (row groups are in rendering order and spans cannot cross them). Nor is which line of a
  cell a value is on: a line break inside a cell is a space, so `<td>10 mg<br/>&#160;</td>` and
  `<td>&#160;<br/>10 mg</td>` read the same next to a cell of two lines, while a renderer draws
  the value level with a different line of its neighbour. Line alignment across cells also
  depends on wrapping and margins, which no text check sees; an extractor of a drawn document
  cannot tell a wrapped line from a line break, so a rule that compared lines would refuse most
  real tables. Which cell a value is in is proved; where in the cell it sits is not. The slots
  are the HTML table model's, and the zero-height and zero-width rules above make every
  accepted row that covers a slot, and every column, drawn with some size. How large is not compared: a row or column
  whose only single cells are empty is drawn a few pixels high or wide, which a reader can
  overlook, as a source drawn the same way can be.

- Table parts must appear in the one document order that renders as written, because
  renderers place them by role: `caption` (at most one) first; then either rows (`tr`)
  directly under `table`, or sections in the order `thead` (at most one), `tbody` (any
  number), `tfoot` (at most one), never both forms in one table. `caption`, `thead`, `tbody`,
  `tfoot` must be direct children of `table`; `tr` of `table` or a section; `td` and `th` of
  `tr`. Violations reject (`table-structure`, `table-section-order`, or `misnested-tag`).
- An `hr` while a `td`, `th` or `caption` is open rejects (`table-content`, from 3.0.0): the
  rule is only as wide as its column, so `<td>1<hr/>2</td>` is drawn as the stacked fraction ½
  where the text says "1 2".
- Nesting is bounded (`nesting-depth`, from 3.0.0): an element's start tag rejects when 32
  elements are already open below the root, a void element (`br`, `hr`, `img`) included at its
  own start tag, so at most 32 are open below the root (an HTML parser stops nesting at 512 open
  elements and moves what follows elsewhere, so a deeply nested `sup` is drawn after the text
  that follows it); at most one of `small`, `code`, `h5` and `h6` open at once, and no heading
  inside an open heading (each shrinks the text, and together they reach seven pixels); and at
  most six of `blockquote`, `ul`, `ol` and `dd` open at once, the new element included (each indents, and
  more push the text off a narrow page).
- The raw sequence `]]>` in text rejects (`cdata`): it ends a CDATA section to an XML parser,
  which then refuses the document and draws none of it.
- The lowered-half rule (from 3.1.0). ½ is a number, and kept unfolded it can join a number or
  an index on either side without the text saying which: `log<sub>2½</sub>` (base 2½) and
  `log<sub>2</sub>½` (log₂ of ½) would both read `log₂½`, and so would `logₙ<sub>½</sub>` and
  `logₙ½`. So a `sub` whose content holds U+00BD is accepted only when all three hold, and
  otherwise rejects (`unmappable-script`; its offset is that of the first U+00BD in the `sub`,
  as the character or its reference, in the div, like every other `unmappable-script`):
  - its emitted content is exactly the one code point U+00BD (so `<sub>½ </sub>`,
    `<sub>2½</sub>` and `<sub>-½</sub>` reject);
  - the code point emitted immediately before that content is an ASCII letter (U+0041–U+005A,
    U+0061–U+007A) emitted outside `sup` and `sub` (so `t<sub>½</sub>` and `<em>t</em><sub>½</sub>`
    are accepted, and `1<sub>½</sub>`, `logₙ<sub>½</sub>`, `log<sub>n</sub><sub>½</sub>`, a
    word joiner before the `sub`, or a `sub` at a line start or after a space, reject); and
  - the code point emitted immediately after it, if there is one, is U+000A, U+0009, U+0020, `)`,
    `.`, `,`, `;` or `:`, emitted outside `sup` and `sub` (so `t<sub>½</sub> 2` and `(t<sub>½</sub>)`
    are accepted, and `t<sub>½</sub>2`, `t<sub>½</sub>ⁿ`, `t<sub>½</sub><sub>2</sub>`,
    `t<sub>½</sub><sub> </sub>`, a thin space or a word joiner after the `sub`, reject).

  "Emitted" means in the scanner's text, across any markup (a tag that emits nothing is not a
  neighbour; a block boundary's U+000A, a cell's U+0009, a grid marker or a list number is).
  Nothing is read past: the neighbours are the adjacent code points. The rule is checked after
  the scan, `sub` by `sub` in document order (the error order below). Every lowered ½ in the
  EMA's published English labels (six, surveyed 2026-09-24) is `t<sub>½</sub>` followed by a
  space or `)`.

- A combining mark or a composition across inline markup rejects (`combining-across-markup`,
  from 3.0.0): at each start or end tag of `span`, `b`, `i`, `em`, `strong`, `sup`, `sub`,
  `small`, `abbr`, `cite` or `code`, the first code point emitted after the tag, read past
  every Default_Ignorable_Code_Point (section 6) that is not itself of category M, must not be of
  general category M; and the 64 code points of the emitted text before the tag and the 64 after
  it must give the same text through section 3 steps 1 to 3 together as separately (which
  catches the Hangul jamo that compose without being marks). Its offset is in the emitted text,
  not in the `div`. A renderer draws the text on each side of such a tag in its own run, so a
  combining mark after the tag does not join the letter before it: `CrCl &lt;<b>&#x338;</b> 30`
  is drawn "CrCl </ 30" while NFC reads "≮", and `caf<b>e</b>&#x301;` is drawn with a separate
  accent. A word joiner or a zero-width joiner between the tag and the mark is drawn as nothing
  and changes neither (`q<b>&#x2060;&#x301;</b>` is drawn "q ´").
- A root language tag changes the font a renderer picks (`lang="ja"` draws Latin text, dashes
  and ellipses in a Japanese font) but not the text; it is not compared.
- Allowed attributes: `xmlns` (root), `xml:lang` and `lang` (root `div` only), `scope` (`th`
  only), `type` and `start` (`ol` only), `colspan`
  and `rowspan` (`td` and `th` only), `src` (`img` only, required there). Values must be double-
  or single-quoted. Any other attribute — in particular `style`, `hidden`, `title`, `class`,
  `id`, `alt`, `reversed` and `value` — rejects, and so does an allowed attribute on any other
  element and a repeated attribute name on one element. A viewer's stylesheet or script can key
  on a class, an id or a language tag to hide content, and a narrative needs none of them below
  the root.
- Attribute values are never compared against the source, so they must not be able to carry
  text. Each value must match its token form or the element rejects: `xml:lang`, `lang`,
  `scope` — `[A-Za-z0-9_.:-]{1,32}`; `type` — exactly `1`, `a`, `A`, `i` or `I`; `start` — `0|-?[1-9][0-9]{0,3}`; `colspan`, `rowspan` — `[1-9][0-9]{0,2}|1000`;
  `src` — `data:image/png;base64,` or `data:image/jpeg;base64,` followed by a body that is
  non-empty, at most 1 398 104 code points (the base64 length of 1 MiB), a multiple of 4 long,
  all `[A-Za-z0-9+/=]`, with `=` only as its last one or two code points (tested directly, not
  by one regular expression); a reference, `https:` and every other scheme are refused, because
  what they draw can change after approval or is nothing, and fetching it tracks the reader.
  `src` is the one value compared with the source, through the hash `img` emits. Anything else
  (spaces, `<`, `&`, other schemes) rejects. These bounds limit, but do not eliminate, what
  attribute values can carry. (Up to 3.0.0 `a` was allowed with an `https://` `href`; 3.0.0
  refuses the element, above.)
- Comments, processing instructions, CDATA sections, DOCTYPE declarations, a stray `<`, a
  stray `&`, unbalanced or misnested tags reject.
- Entities: `&amp; &lt; &gt; &quot; &apos;`, decimal `&#N;`, and hexadecimal `&#xH;` only.
  Each decoded code point is subject to section 2 on its own. Named HTML entities such as
  `&nbsp;` reject.
- The extracted text is then normalised (section 3) by the verifier, which is where an empty
  result is decided: a narrative whose normalised text holds nothing but the gaps of section 6
  (whitespace, the thin spaces, the blank glyphs and the Default_Ignorable code points) and the grid markers
  U+FDD0–U+FDD5 draws nothing inked, and is `malformed-narrative` with reason `empty-narrative`
  (a table of empty cells is empty, and so is a thin space alone; a list number, a picture and
  U+1680, drawn as a stroke, are drawn). The scanner itself never produces that reason. The
  crosswalk (`src/fhir/transform.ts`) decides whether a mandatory section carries narrative by
  the same test on the scanner's text, except that for a mandatory section it also ignores
  pictures, because a picture can draw nothing and what one shows is never read; a lone list
  bullet counts there (the fidelity check then reads it as `empty-narrative`).

Because block boundaries become U+000A or U+0009 and the whitespace step collapses them,
paragraph boundaries, headings and line breaks are structure, not content. A table's cells are
content through its grid markers, and cell text is content.

### Tokeniser

The scanner is a single left-to-right pass over the `div` string, matching at the current
offset only (sticky matching), after the section 2 check of the whole string. The grammar is
given as regular expressions in a dialect where every class is spelled out: `[0-9]` never
means a non-ASCII digit, `$` never matches before a trailing line break, and `WS` stands for
exactly U+0009, U+000A, U+000D and U+0020 — `[\t\n\r ]`, never a dialect's `\s`. An HTML
parser treats only those as whitespace inside a tag and reads any other code point as part of
the tag name, so `<sup` followed by U+00A0 and `>` is an unknown element to a renderer (`10`
then `6` renders as `106`), and `<br` U+00A0 `/>`, `<table` U+3000 `>` or `<td` U+FEFF `>` is
not a `br`, `table` or `td`. Any other code point where the grammar allows `WS` makes the tag
fail its grammar, which is `malformed-tag` (below). An implementation whose regex dialect
differs in any of these must spell the class out; a port that copies the reference's regex
text verbatim is wrong. General category N (section 5, `sup` and `sub`) is the Unicode
general category of the pinned Unicode version (Unicode 16.0, the pinned runtime's; ADR 0003).

- Start tag: `<(NAME)(ATTRS)WS*(/?)>` where `NAME` is `[A-Za-z][A-Za-z0-9]*` and `ATTRS` is
  zero or more of `WS+ ANAME WS* = WS* ( "[^"<]*" | '[^'<]*' )`, with `ANAME` =
  `[A-Za-z_:][-A-Za-z0-9_:.]*`. Attribute values may not contain `<` or their own quote
  character; the value grammars of section 5 are then applied to the unquoted value as
  whole-string matches.
- End tag: `</(NAME)WS*>`.
- Entity: `&( [A-Za-z]+ | #[0-9]{1,7} | #x[0-9A-Fa-f]{1,6} );`. Named entities are only the
  five listed; a decoded code point above U+10FFFF is `unknown-entity`.
- At `<`: a comment (`<!--`), CDATA section (`<![CDATA[`), any other `<!`, and `<?` reject with
  `comment`, `cdata`, `doctype`, `processing-instruction` respectively. A `</` that does not
  match the end-tag grammar is `malformed-tag`. A `<` that matches neither grammar is
  `malformed-tag` when the next character is an ASCII letter and `stray-lt` otherwise.
- Root: the first element must be `div`, else `root-not-div`; a second element at depth zero,
  or any element after the root has closed, is `multiple-roots`; non-whitespace text (or an
  entity) outside the root is `text-outside-root`, where whitespace means U+0009, U+000A,
  U+000D, U+0020 only. A root `div` without `xmlns="http://www.w3.org/1999/xhtml"` is
  `root-not-div`, checked after its attributes; `xmlns` anywhere else, or with any other
  value, is `forbidden-attribute`.
- A block element's start tag emits a line break; its end tag emits one; a self-closing `hr`
  therefore emits two. `br` emits one. Each is U+000A, or U+0009 for `td` and `th` and inside
  an open `td` or `th` (section 5). The scanner's text therefore always begins with U+000A, the
  root `div`'s.
- Input ending with no root is `root-not-div`; input ending with elements still open is
  `unbalanced-tag`.

The first violation encountered in this pass decides the code; scanning does not continue. The
reason code is inside `reportHash`, so the order in which violations are decided is normative:

- At a start tag: `malformed-tag`/`stray-lt`, then `uppercase-element`, then
  `unknown-element`, then `multiple-roots`/`root-not-div`, then attributes in document order
  (`forbidden-attribute`; root without xmlns = `root-not-div`; an `img` without `src` =
  `forbidden-attribute`, after its attributes), then `void-element`, then `nesting-depth`, then the parent check, then
  `table-structure`, then `table-section-order`, then `table-shape` (an overlapping cell), then
  `table-size`. The
  parent check: `script-content` if the parent is `sup` or `sub`; else `misnested-tag` for a
  table part (`caption`, `thead`, `tbody`, `tfoot`, `tr`, `td`, `th`) or an `li` in the wrong
  parent; else `table-content` for any other element whose parent is `table`, `thead`,
  `tbody`, `tfoot` or `tr`; else `list-content` for any other element whose parent is `ol` or
  `ul`; then `table-content` for an `hr` while a `td`, `th` or `caption` is open.
- At an end tag: `malformed-tag`, then `uppercase-element`, then `unbalanced-tag`, then
  `misnested-tag`, then `table-shape` (for `</tr>`, a row drawn at zero height; for `</thead>`,
  `</tbody>`, `</tfoot>` and `</table>`, a clipped row span, then at `</table>` the row widths,
  then a column drawn at zero width).
- At `&`: `stray-amp`, then `text-outside-root`, then `unknown-entity`, then
  `forbidden-character`, then `reserved-character`, then `invisible-character`, then
  `table-content`, then `list-content`, then `unmappable-script`.
- At a raw code point: `text-outside-root`, `table-content` or `list-content` (whichever the
  parent makes applicable), then `cdata` (the start of `]]>`), then `unmappable-script`.
- After a clean scan: `unmappable-script` (the lowered-half rule, in document order), then
  `combining-across-markup`, then `empty-narrative`.

(So `<table><td>a</td></table>` is `misnested-tag`, `<iframe/>` is `unknown-element` and
`<img/>` is `forbidden-attribute`.) The section 2 checks of the whole `div`, which precede the
scan, decide `forbidden-character`, then `reserved-character`, then `invisible-character` before any
of these.

The scanner's reason codes are, in the order of this section: `forbidden-character`,
`reserved-character`, `invisible-character`, `root-not-div`, `multiple-roots`, `text-outside-root`,
`uppercase-element`, `unknown-element`, `void-element`, `list-content`, `script-content`,
`unmappable-script`, `table-content`, `table-shape`, `table-size`, `table-structure`,
`table-section-order`,
`misnested-tag`, `nesting-depth`, `combining-across-markup`,
`forbidden-attribute`, `comment`, `processing-instruction`,
`cdata`, `doctype`, `malformed-tag`, `stray-lt`, `stray-amp`, `unknown-entity`,
`unbalanced-tag`.

## 6. Verification and report rules

- A section's expected text is assembled from its spans as one contiguous raw slice per page —
  from the first span's start to the last span's end on that page, including whatever lies
  between consecutive spans (which must normalise to nothing). The source's own characters
  therefore decide where words begin and end; the verifier never inserts whitespace between
  spans on the same page, so adjacent spans cannot split a word.
- A slice of page text that the verifier normalises — the first slice of a section, and every
  gap it tests for blankness (between spans, the tail and head around a page break, and the
  coverage gaps) — is read from the U+000A that ends the previous line when only §3 step 5
  whitespace other than U+000A lies between that U+000A and the slice's start. Before
  `bodyStart` that is the code point section 1 makes U+000A; nothing else outside the body is
  read. At the very start of a page with no header there is no such U+000A, and the slice is
  read from its start. This gives a bullet glyph at the start of a line the line start that
  section 3 step 4 requires (normalisation does not count the start of a text as one), and
  adds nothing but whitespace to what is compared. A page-2 slice of a section that continues
  across a page break is not extended: the page-1 slice ends with its own line terminator.
- When the verifier normalises a slice of page text (a section's joined slices, or a gap), and
  the slice's last line does not end with U+000A inside the slice, that line's U+0009 status
  for section 3 step 4 is decided on the whole page line: from the U+000A before it to the next
  U+000A, within the body. A slice cut before a row's U+0009 is still on a table row.
- When a section continues onto the next page, the page-1 slice is extended to `bodyEnd` and
  the page-2 slice starts at `bodyStart` (the blank tail and head must normalise to nothing),
  and the slices are concatenated verbatim with no separator: the body's own final line
  terminator, or a soft hyphen when a word continues, decides how the pages join. The verifier
  never inserts a character of its own.
- A section may omit whole whitespace-delimited tokens but never begin or end inside one.
  After all spans resolve, the first span's start and the last span's end are checked; a cut
  is `invalid-provenance` with reason `word-cut`. The rule is about whitespace, not about
  letters: punctuation is not a boundary, because inside a number it is part of the number
  (`1.5`, `−20`, `0.5`, `1,000`) and inside a word it is part of the word (`non-steroidal`).
  - Edge whitespace is the §3 step 5 whitespace list without U+00A0 and U+2007. Those two join
    the groups of a number (`10 000`), so for the edge rules they are not a boundary between
    tokens.
  - A gap is §3 step 5 whitespace; a space narrower than a quarter of an em (U+2006, U+2009,
    U+200A, U+202F, U+205F), which §3 makes content; a blank glyph, drawn with no ink: U+2800
    BRAILLE PATTERN BLANK, and U+1878, U+18AA, U+A4A2, U+A4A3, U+A4B4, U+A4C1 and U+A4C5,
    Mongolian and Yi letters that Chrome's default serif face on macOS lacks and draws as an
    em-wide blank (found by drawing every assigned code point in Chrome's default faces and
    measuring the ink, `scripts/fidelity/blank-glyph-sweep.md`); or a Default_Ignorable_Code_Point of Unicode 16.0 (U+00AD,
    U+034F, U+061C, U+115F–U+1160, U+17B4–U+17B5, U+180B–U+180F, U+200B–U+200F, U+202A–U+202E,
    U+2060–U+206F, U+3164, U+FE00–U+FE0F, U+FEFF, U+FFA0, U+FFF0–U+FFF8, U+1BCA0–U+1BCA3,
    U+1D173–U+1D17A, U+E0000–U+E0FFF), which Unicode says a renderer should not draw (a few
    fonts draw some of them, U+3164 or U+115F; since a digit is never a gap, reading past more
    of them only refuses more). The digit-group rule below reads past every gap, so "10"
    U+2009 " 000" and "10" U+2063 " 000" are each one number.
  - Start. Read backwards from the code point before the first span's start offset through
    page n's body. On passing its bodyStart, continue from the last code point of page n−1's
    body, and so on through earlier pages. Only body text is read, and earlier pages are read
    as declared even if they fail §1 or §2. Skip edge whitespace and stop at the first other
    code point c. The result is word-cut if no code point was skipped before c, whatever c's
    category, or if c is U+00AD. If reading passes the start of page 1's body, there is no cut.
  - End. The result is word-cut if the last span, with trailing edge whitespace removed, ends
    in U+00AD. Otherwise it is word-cut unless the code point at the end offset is edge
    whitespace, or the end offset is at or past `bodyEnd` (section 1 makes the code point
    before `bodyEnd` a line feed).
  - Numbers grouped with a space. On either edge it is also a cut when the code point on the
    inner side of the edge is a digit (general category Nd), and the first code point beyond
    it that is not a gap, read inside the body without crossing a U+000A, is also Nd. The inner
    code point is the span's first code point that is not a gap (at the start) or its last (at
    the end): a span that begins or ends with a gap is judged by the digit inside it. So "…is
    10" cannot be taken from "…is 10 000 IU", with a gap or two between the groups ("10 ␠␠000",
    "10 U+2009␠000"), even by a span that ends with the first space,
    nor "000 IU" after it by a span that starts with the second; a number at the end of one
    line and a number at the start of the next are separate. (The inner code point skips the
    joiners U+00A0 and U+2007 as well, although they are not edge whitespace: a span ending "10"
    U+00A0 before a space still ends inside the number.)
  - These rules apply together; any one of them makes a cut.
  - They read the page's text and do not rebuild a table's grid, so a number split across two
    cells drawn side by side ("10" | "000 IU") is not one number to them. For a structured
    source, the only kind this version qualifies (from 3.0.0), a span covers the whole page (section 7), so no span
    edge falls between cells; the query service's quote-edge rule, whose quotes can, rebuilds
    the grid (`docs/design/epi-mcp-query-service.md`).

  So "Maximum dose is 1" cannot be taken from "Maximum dose is 1.5 mg", nor "20 °C." from
  "−20 °C.", nor "5 mg." from "0.5 mg.", nor "is 10" from "is 10 000 IU"; a section cannot begin at "safe for pregnant women"
  when the page before ends "un" and U+00AD, whether a line break, a blank line, or a page
  break with a blank head lies between; and it cannot end after "intra", U+00AD and a space.
  What the rule does not claim: it proves that a section's edges touch whitespace in the page
  text, not that the whitespace ends a sentence or a clause ("Take 5" can still be taken from
  "Take 5 mg twice"), and a span that itself begins or ends with whitespace is judged by the
  code points outside it, so it can be refused although its words are whole (a false
  failure). Two separate numbers on one line separated only by whitespace ("Take 2 10 mg
  tablets") cannot be split by a section edge either (a false failure the digit rule accepts).

  Other false failures these rules accept, stated: a list item at the very top of a page whose
  body starts at 0 (no header, so no line terminator to read the slice from: its bullet is
  content, and a `<ul><li>` narrative mismatches); a table row whose first cell starts with a
  bullet, cut before its U+0009 (the bullet is content on the page and a list item in a
  paragraph narrative); and a list written by the extractor with U+0009 after the bullet,
  which section 7 forbids.

- The joined text is normalised (section 3) and must equal the normalised narrative exactly.
- A report with zero narrative sections is `failed` (issue `No narrative sections to verify`);
  "nothing to check" is never a pass.
- `coverage` records `pageCodePoints` (all page text), `bodyCodePoints` (declared bodies),
  `coveredCodePoints` (verified spans), and `uncoveredGaps` (non-blank body text between
  verified spans). Coverage never fails the check; it is evidence for reviewers.

### Section results

Each section of the Composition that carries the canonical source code system produces one
result. Sections and provenance entries are matched by `sourceKey`; a duplicate key on either
side is a structural error (below), not a result.

- Members present in every result: `sourceKey`, `path` (the section's Composition path),
  `status`, `spanCount` (number of provenance spans; 0 without provenance).
- `normalizedTextSha256` is present whenever the narrative normalises (every status except
  `malformed-narrative`), and equals the value that enters the binding hash.
- `reason` is present for `malformed-narrative`, `span-not-found`, and `invalid-provenance`,
  and absent otherwise. `details` is present for `mismatch` only. An absent member is absent,
  never `null`; this decides `reportHash`.
- The status is decided in this order, stopping at the first that applies: no provenance entry →
  `missing-provenance`; narrative does not normalise → `malformed-narrative` with the scanner's
  code (section 5, including `forbidden-character` for the `div` or a decoded reference),
  `forbidden-character` (section 2 on the extracted text), or `empty-narrative`; span
  resolution fails → the status and reason below; normalised source and narrative differ →
  `mismatch`; else `verified`.
- Span resolution examines the spans in order and stops at the first failure. Per span:
  `span-not-found` with reason `page-not-found`, `page-malformed` (the page contains a section
  2 character), `body-boundary` or `excluded-text` (section 1), `outside-body` (the span lies
  outside the body or is empty), `hash-mismatch` (`textSha256`); then, against the previous
  span, `invalid-provenance` with reason `span-order` (same page, starts before the previous
  ends), `non-contiguous` (the gap does not normalise to nothing, or the pages are not
  consecutive). After all spans, `invalid-provenance` with reason `word-cut` (the edge rules
  above).
- Overlap is decided after every section is resolved: the spans of all `verified` sections are
  ordered by page, then start offset; whenever two consecutive spans on one page belong to
  different sections and the later starts before the earlier ends, both sections are marked;
  every marked section that was `verified` becomes `invalid-provenance` with reason `overlap`,
  and its spans are left out of the coverage figures.
- `details` (the diff hint) is computed on the two normalised texts as code-point sequences:
  `expectedLength` and `actualLength`; `firstDifferingOffset`, the length of the common prefix;
  `commonSuffixLength`, the length of the common suffix of what follows that prefix (so prefix
  plus suffix never exceeds the shorter text); `expectedWordCount` and `actualWordCount`, the
  number of U+0020-separated tokens of each normalised text, 0 for an empty text; and
  `expectedSha256`, `actualSha256`, the SHA-256 of each text's UTF-8 bytes.

### Report

- `status` is `passed` if and only if every section is `verified` and `issues` is empty.
- `issues` holds exactly these strings, each once per occurrence and in this order: for each
  page that fails a section 1 rule, `Page <n>: body-boundary` or `Page <n>: excluded-text`
  (in page order); `No narrative sections to verify` when there are no sections; `Orphan
provenance <sourceKey>` for each provenance entry with no section, in provenance order.
  The wording is normative because `issues` is inside `reportHash`.
- `summary` is `{ "total": <sections>, "verified": <verified sections> }`.
- Structural errors do not produce a report at all: a normalisation version other than the
  implementation's own, page numbers that are not 1, 2, …, N in array order (section 1, which
  includes a duplicate page number and a missing page), a page number or body offset that is
  not an integer, a page whose body range is not a valid range, a span whose `page`,
  `startOffset` or `endOffset` is not an integer, a duplicate `sourceKey` among sections or
  among provenance entries. An integer is a JSON number with no fractional part (`1.0` is 1);
  a boolean is not an integer, although some languages treat `true` as 1 — an implementation
  in one of them must test for a boolean before it tests for an integer. Their wording is not
  normative.

## 7. Extractor contract

The page text an extractor produces is the reference the narrative is checked against, so the
extractor is a controlled component: its name and version are recorded in
`IngestionProvenance.extraction.parser`, pinned, and checksummed like the FHIR packages.

**What this version qualifies (from 3.0.0).** A structured source is an FHIR ePI document Bundle whose section
narratives are XHTML (ADR 0005); every other source, a Word document however it is read
included, is a drawn document. The rules below are complete for a structured source (the
structured-source rule below): its page text is exactly the scanner's text for each section,
so none of the layout rules for drawn documents comes into play; whether T drops only what
cannot change the drawn page is ADR 0005's to secure (its requirements and renderer cross-check). For a drawn document they are not complete. The independent reviews of 3.0.0 found cases where text
a conforming extractor may write lets a narrative verify that the document does not draw: a line
wrapped at the space between the groups of a number or after a dash, which section 6's edge
rules then read as a token boundary; an undrawn soft hyphen at the end of a block, which step 1
joins to the next block; a bullet after an invisible character, or after a line break the
document draws, at the start of a line; a page footnote or margin text drawn inside a continued
paragraph; and a table caption split just after U+FDD0. Several of these hold for 2.0.0 too.
They are listed, with the fixes the reviews proposed, in `docs/design/fidelity-norm-3-0-0.md`
("Drawn documents: open items"). Until a later version closes them, no drawn-document extractor
is qualified: a report over a drawn document's text proves agreement with that text, not with
what the document draws, and must not support an approval. The drawn-document rules below stand
as the start of that work.

An extractor must:

- take every character from the document's embedded text layer through a pinned,
  deterministic library, so that for a born-digital document the extracted text is
  character-exact by construction. A component that classifies structure (headers, footers,
  headings, tables) may be used to decide body ranges and serialise tables, but its own text
  is never emitted: the extractor spike (`docs/design/extractor-spike.md`) showed that a layout
  parser straightens quotes, substitutes dashes and spaces, and flattens superscripts, and
  because the narrative is derived from the extracted text, the fidelity check cannot see
  such a change — both sides carry it;
- record in `extractorVersion` the pinned versions of every component whose output reaches the
  text or the body ranges, read from each component's own version endpoint rather than from
  its response when the response does not carry one;
- decode the text layer as Unicode, never through Latin-1 or windows-1252: page text contains
  no section 2 character. Page text contains no U+000B or U+000C; a page break is the page
  record, not a character;
- emit each table as section 5's scanner does: U+FDD0, then on its own line the caption's text if
  any; each row
  as U+000A U+FDD2 followed by its slots from left to right, where a slot in which a cell starts
  is U+0009 U+FDD3 U+0009 followed by the cell's text, a slot covered by a merged cell from the
  left in that cell's own first row is U+0009 U+FDD4 U+0009, and any other covered slot is
  U+0009 U+FDD5 U+0009; then U+000A U+FDD1. Every row has the same number of slots; a line break
  inside a cell is emitted as U+0020, except a discretionary hyphen, which is U+00AD U+000A. An
  extractor that cannot tell a merged slot from an empty cell, or cannot recover a table's grid,
  must refuse the document rather than guess: the grid is compared (section 5);
- write a table that continues across a page break as its logical text, split once per break.
  The logical text is what the scanner would read from the table the document draws: one U+FDD0,
  the caption, the rows in order, each row's slots in order with each cell's text whole (a cell
  that spans rows belongs to the row it starts in, and all of its text stands in its own slot),
  one U+FDD1. A table drawn inside a table cell has no logical text (section 5 refuses nested
  tables), and an extractor that meets one must refuse the document rather than flatten it into
  the outer cell. Nothing else stands between U+FDD0 and U+FDD1:
  - a header row the document repeats on a new page is written where the table first has it, a
    footer row it repeats where the table last has it; every other copy, and a continuation label
    ("Table 2 (continued)"), is drawn text excluded from its page's body like a running header or
    footer, counted in `pageCodePoints` and against the page's 240 excluded code points (section
    1). A copy is known by the document's structure (a Word header row marked to repeat, a
    tagged PDF's artifact), never by matching text, because a real row can equal the header row;
    an extractor that cannot tell a copy that way, or cannot exclude it at a body edge, must
    refuse the document;
  - text the document draws between two parts of the table that is not a running header or
    footer (a page footnote, a note at the page foot) is written immediately after the table's
    U+FDD1, in page order and then in reading order.

  The earlier page's body holds the logical text up to one split point, and the later page's
  body the rest. The split point is a row boundary (including just after U+FDD0 or just before
  U+FDD1), a cell boundary, or section 3 step 5 whitespace inside a cell's or the caption's text,
  and the extractor then writes the later page's text beginning with U+FDD2, U+FDD1, or U+0009
  (which it inserts before a cell's or the caption's continuation); or the split is inside a word
  the document hyphenates at the break, and the earlier page's body ends with U+00AD U+000A and the
  later page's text begins with the rest of the word, which section 3 step 1 joins. So, after step
  1, every line of a table's body text from its first U+FDD2 to the line before its U+FDD1 that
  holds anything but whitespace holds U+0009 or U+FDD2, a caption's continuation holds U+0009,
  and section 3 step 4 reads a bullet at the start of either as content. Any split point allowed
  here gives the same normalised text (tested for every split point of 450 random tables during
  this version's review). An extractor that cannot produce the logical text, or
  cannot tell which text belongs to the table, must refuse the document. The verifier does not
  check this, as it checks no other duty of this section; an extractor that breaks it can move
  text into or out of a cell on the page side only;

- write a line of body text that continues a paragraph, heading, list item or caption (after the
  document wraps it, or after a page break) and begins with a section 3 step 4 bullet glyph
  followed by whitespace with a leading U+0009, so that step 4 reads the glyph as the content it
  is (a document drawing "Take 2 • 10 mg" wrapped after "2" must not read "Take 2 10 mg"). Only
  a drawn list item's own marker starts a line without it. An extractor that cannot tell a
  continuation from a new list item must refuse the document;
- emit a list marker (a bullet glyph, section 3 step 4) followed by U+0020, never U+0009: the
  tab after a list marker is layout, not a cell boundary. A word processor's list (`•` U+0009
  `Adults: 10 mg`) extracted with its tab reads as a table row, its bullet as content, and a
  `<ul><li>` narrative fails against it — safe, but a false failure that the extractor must
  avoid. A numbered list's marker is emitted as drawn (`3.`, `b.`, `iv.`) followed by U+0020,
  never U+0009 and never nothing, even where the document's text layer has no space after it;
- emit an inline picture, in the text's reading order, as U+FFFC, the SHA-256 hex of the `src`
  a narrative must carry for it, and U+FFFC: the `data:image/png;base64,` or
  `data:image/jpeg;base64,` URI of its exact bytes in canonical padded base64 (the media type is
  that of the stored part). The bytes are a complete PNG or JPEG file as the document stores
  it: a Word part of type `image/png` or `image/jpeg`, a PDF image stored as a DCT (JPEG)
  stream. A picture stored any other way (a PDF's Flate-compressed samples, JPX, JBIG2, CCITT, a
  Word EMF or GIF) has no such file, and the extractor must refuse the document rather than
  re-encode it. A picture is an embedded raster image drawn inline, including a logo
  or a decorative image; vector drawings, shapes and text boxes are not, and an extractor that
  meets one it cannot read as text must refuse the document. A structured source's picture that
  is a reference is resolved against the authority's published base URL and carried as its
  pinned bytes; it is emitted as nothing only on pinned evidence that the authority's own viewer
  draws nothing for it, and any other failure to fetch it fails the import (ADR 0005). An extractor that cannot place
  pictures must refuse a document that has them. An extractor whose text layer itself contains
  U+FFFC or a code point in U+FDD0–U+FDEF must refuse the document (section 2);
- put every block on its own line, as the scanner does: U+FDD0, a caption, each U+FDD2 and
  U+FDD1 each start a line, and outside a table cell a paragraph, heading or list item starts one
  (inside a cell it stays on the cell's line, section 5). A title printed
  above a table is a caption only where the document marks it as one (a Word caption bound to
  the table, a tagged PDF's `Caption` structure element); otherwise, as in an untagged PDF, it
  is a paragraph before U+FDD0. Outside a table cell, a `ul` item's bullet is emitted as section 3 step 4 removes it, or
  not at all; inside a cell, not at all (step 4 removes nothing on a cell's line);
- for a structured source, emit one page per source section, in source order, with the whole
  page as its body, holding exactly the text section 5's scanner code emits for T(div), and
  nothing else (the scanner's text begins and ends with U+000A). T is ADR 0005's stated lexical
  transform of the div string: it deletes attributes and CSS declarations on closed lists,
  unwraps a `span` left with no attributes, and a link or a `u` whose text an underline cannot
  change (ADR 0005), keeping their text; rewrites a raised or
  lowered run as `sup` or `sub`, and replaces a referenced picture with its pinned `data:` URI or
  deletes it; it does nothing else. A picture's token is the hash of its `src` exactly as T(div)
  holds it, not re-encoded. Where the scanner refuses T(div) (any
  section 5 reason), where T meets anything not on its
  lists, or where the div's text, character references decoded, holds U+00AD or another
  section 3 step 1 invisible character, the extractor refuses the section. A section draws nothing when section 5's `empty-narrative` test holds for
  its text; it gets its page and no narrative, and every page without a span must normalise to
  nothing (the importer and the gate refuse otherwise, ADR 0005). A section without `text` has
  the empty page: its text is `""`, and `bodyStart` and `bodyEnd` are 0. The narrative
  section's span covers that page's body, and sections 1 and 6 apply unchanged. The drawn-document rules of this section (line
  layout, continuation lines, discretionary hyphens, tables across page breaks, body ranges) do
  not apply;
- in a raised or lowered glyph run, emit every digit and sign of section 5's folding tables as
  its script code point — raised: U+0030–U+0039 as U+2070, U+00B9, U+00B2, U+00B3,
  U+2074–U+2079, `+`, U+FE62, U+FF0B and U+2795 as U+207A, `-`, U+2212, U+2010–U+2015,
  U+02D7, U+FE58, U+FE63, U+FF0D and U+2796 as U+207B, `=` as U+207C, `(` and `)` as U+207D
  and U+207E; lowered: the same characters as U+2080–U+2089 and U+208A–U+208E — and every
  other character as it is, except that an extractor refuses the document where such a run holds
  what section 5 refuses inside the corresponding element (stated from 3.1.0: a number with no
  script form, a raised ½ or ∞, a lowered ½ outside the lowered-half rule), since the text
  cannot carry its position. An
  extractor that cannot tell a glyph's baseline shift is unverifiable and must refuse the
  document (emit no SourceDocumentText) rather than emit plain digits: the narrative is
  derived from the page text, so if the text layer flattens `10⁹` to `109`, both sides say
  `109` and the check passes against a document that shows `10⁹`;
- emit discretionary (line-break) hyphens as U+00AD and hard hyphens verbatim;
- declare `bodyStart`/`bodyEnd` per page so repeated headers and footers are excluded, with
  every non-empty body ending in its final line terminator, including at the end of the page
  (section 1), and never exclude more than a running header and footer;
- not apply any normalisation of its own beyond faithful text extraction.

## 8. Change control

Any change to sections 2–6 is a new `NORMALIZATION_VERSION`. The golden vectors are
regenerated, every changed vector is reviewed by hand with a recorded reason, the ADR is
amended, and previously approved submissions require re-approval because the version is part
of the approved content hash.

The increment states what changed. A **patch** increment documents behaviour the vectors
already pin: no normalised text, extracted text, status, reason, coverage figure, or diff hint
in any vector changes, and only the hashes that embed the version string move. A **minor**
increment changes at least one vector's outcome. Anything that invalidates a span, a hash
rule, or the extractor contract is **major**. Every increment, patch included, is part of the
approved content and so still requires re-approval of anything approved under the previous
version.

A second-language implementation is proven by agreement with the reference on inputs neither
author chose — a seeded differential run over generated inputs — and not by the golden vectors
alone: vectors authored from the reference only establish agreement where its author already
looked. The vectors remain the fixed, reviewed floor; the differential run is the proof.

## 9. Version history

- `fidelity-norm/3.1.0` (minor) — inside `sub`, U+221E INFINITY is kept unchanged instead of
  rejecting (`unmappable-script`), and U+00BD VULGAR FRACTION ONE HALF is kept under the
  lowered-half rule: as a `sub`'s whole content, right after an ASCII letter and right before a
  break, a space, closing punctuation or nothing, none of them raised or lowered. So the
  half-life `t<sub>½</sub>` and `AUC<sub>(0-∞)</sub>`, which the EMA's published labels write,
  read `t½` and `AUC₍₀₋∞₎` (section 5; `docs/design/fidelity-norm-3-1-0.md`, ADR 0005). The
  lowered-half rule is checked after the scan, before `combining-across-markup`. Section 7 states
  that an extractor refuses a raised or lowered run holding what section 5 refuses in the
  corresponding element. Nothing else changes: every narrative 3.0.0 accepts reads the same,
  inside `sup` both still reject, and no text arises that a narrative could not already write
  with the code points themselves. Thirty-four XHTML vectors are added, each an input whose
  outcome under 3.0.0 differs or which pins the new rule's edges; the existing vectors change only
  in the version string and the hashes that embed it.
- `fidelity-norm/3.0.0` (major) — numbered lists, table grids and pictures, seen as a reader
  sees them (`docs/design/fidelity-norm-3-0-0.md`, as amended by its independent reviews; ADR
  0005). `ol` is allowed with `type` and `start`, and each of its items emits the marker a
  renderer draws (decimal, alphabetic, roman); `li` is allowed only directly in `ol` or `ul`,
  whose only children are `li` (`list-content`). `colspan` and `rowspan` return, placed by the
  HTML table model with overlaps, clipped row spans, holes, ragged rows, rows drawn at zero height and columns drawn at zero width refused
  (`table-shape`), and nested tables refused (`table-structure`); every table's text carries its
  grid in the reserved code points U+FDD0–U+FDD5, which closes the cell-association residual
  2.0.0 stated, apart from the line a value sits on inside a multi-line cell, which is stated as
  a residual. `img` is allowed with `src` alone (a PNG or JPEG `data:` URI; references refused) and
  emits U+FFFC, the SHA-256 of its `src` and U+FFFC; the tables of a narrative cover at most
  50 000 slots (`table-size`); a narrative of only grid markers is `empty-narrative`. U+FFFC and
  U+FDD0–U+FDEF reject in narrative
  (`reserved-character`, one of the two rules applied to one side only). U+1680 OGHAM SPACE MARK, drawn as a stroke, and the spaces narrower than a quarter of an em (U+2006, U+2009, U+200A, U+202F, U+205F) leave the section 3 whitespace list and are content; narrative rejects U+00AD and U+200B (`invisible-character`, withdrawing `soft-hyphen-at-boundary`); nesting is bounded (`nesting-depth`); `]]>` in text rejects (`cdata`); a combining mark or a composition across inline markup rejects (`combining-across-markup`); `u` and `a` reject (`unknown-element`: an underline turns a sign into another, "<" into "≤"), and with them `href`; an `hr` in a table cell or caption rejects (`table-content`: it is drawn as a fraction bar); section 2 adds the interlinear annotation controls and the prepended concatenation marks; section 6's digit-group rule reads past every gap; and a narrative of gaps alone is `empty-narrative`. Section 7 qualifies structured sources only;
  drawn-document extraction is not qualified until a later version closes the open items the
  reviews recorded. The extractor contract (section 7) writes tables with their grid, numbered markers with a space, pictures with their hash, and
  a structured source as one page per section. Major under section 8: extractor output that
  conformed to 2.0.0 (tables without the grid) no longer does, every table's normalised text
  changes, and narratives with `li` outside a list or non-`li` content in a `ul`, accepted by
  2.0.0, now reject. The changed vectors are listed with their reasons in
  `docs/validation/changes/2026-09-23-fidelity-norm-3-0-0.md`.

  Amended 2026-09-24 without a new version: section 7 now says that a structured source's
  section without `text` has the empty page (`""`, body [0, 0)), where it was silent
  (`docs/design/authority-import-contract.md`, D4). That is documentation of existing behaviour:
  section 1 already admits an empty body, on which no span can lie, no rule of sections 2–6
  changes, and no vector, normalised text or hash moves. It invalidates nothing in the
  extractor contract, so under section 8 it is not a new `NORMALIZATION_VERSION`, and nothing
  approved under 3.0.0 needs re-approval.

- `fidelity-norm/2.0.0` (major) — markup may not change what a reader sees without the check
  seeing it (`docs/design/fidelity-norm-2-0-0.md`, as amended by its independent re-review).
  Only `br` and `hr` may be self-closing, and must be (`void-element`); digits and signs inside
  `sup` and `sub` fold to script code points, other numbers and U+00B1, U+2213 there reject
  (`unmappable-script`), and `sup`/`sub` hold no element (`script-content`) (section 5); U+00AD
  before a line break anywhere in the emitted narrative text rejects, decided after the scan
  (section 5); the section-edge rules read back through whitespace and across pages, pages are
  numbered 1..N, and a non-empty body ends with its own line terminator even at the end of a
  page (sections 1 and 6); section 2 adds U+000B, U+000C, the C1 controls and the bidirectional
  controls on both sides, applies to the whole `div` as decoded from JSON before the scan and to
  each decoded reference on its own, and U+000B, U+000C and U+0085 leave the section 3
  whitespace list; `class` and `id` are removed, `lang` and `xml:lang` are allowed on the root
  only, `href` has only its `https://` form and `scope` is allowed on `th` only (section 5);
  tables contain only table parts and rows of one width (`table-content`, `table-shape`), and
  `colspan`, `rowspan` and `pre` are removed (section 5); the order in which violations are
  decided is stated in full (section 5); and the extractor contract gains the script,
  unverifiable-document, table-slot and page-break rules (section 7). Major under section 8:
  extractor output that conformed to 1.1.1 (plain digits in a raised run, uneven table rows, a
  final line without its line feed) no longer does, and spans that were valid (a section
  beginning after a blank line that follows a hyphenated word) become `word-cut`. The changed
  vectors are listed with their reasons in
  `docs/validation/changes/2026-09-23-fidelity-norm-2-0-0.md`.

  Folded into 2.0.0 before its release, from a second independent review that found false
  passes in the first 2.0.0 implementation (no version was released in between): **C1** —
  whitespace inside a tag is U+0009, U+000A, U+000D and U+0020 only, because an HTML parser
  reads U+00A0, U+2000–U+200A, U+3000 or U+FEFF there as part of the tag name (`10<sup` U+00A0
  `>6` rendered "106" and verified against "10⁶"); any other code point there is
  `malformed-tag` (section 5, Tokeniser). **C2** — a span edge must touch whitespace or a body
  edge, whatever the code point beyond it; punctuation is no longer a boundary, because
  "Maximum dose is 1" verified against "Maximum dose is 1.5 mg" and "20 °C." after "−"
  (section 6). **C3** — a bullet glyph is replaced only at the start of a line and before
  whitespace, and U+2219 and U+2043 are no longer bullets, because `2∙10` verified against a
  table row reading 2 and 10 (section 3 step 4). **L1** — inside `sup` the subscript digits and
  signs, and inside `sub` the superscript ones, reject, which reverses the first reading of
  "not a target of these tables" (a script digit of either table was kept in both); U+2015,
  U+02D7, U+FE58 and U+2796 fold to minus and U+2795 to plus (section 5). **L2** — a span
  field that is not an integer, and a page number that is a boolean, are structural errors
  (section 6). The residual of cell association is stated in section 5.

  Round 2 of that review, also folded into 2.0.0: a line feed or carriage return in narrative
  text, raw or referenced, is emitted as U+0020, because a renderer draws it as a space and
  `<p>Take 2` U+000A `• 10 mg</p>` otherwise verified against "Take 2" and a new line "• 10 mg";
  `soft-hyphen-at-boundary` therefore applies to U+00AD before a block boundary or `br` only
  (section 5). A number grouped with a space, U+2009, U+00A0, U+202F or U+2007 cannot be cut at
  a section edge, and U+00A0, U+2007 and U+202F are not edge whitespace (section 6). A bullet
  glyph on a line that contains U+0009 is content, the scanner writes a table cell on a
  U+0009-separated line, and so a bullet in a table cell is never a list item; the start of a
  text is no longer a line start for step 4, which keeps the procedure idempotent, and the
  verifier reads a page slice from its line terminator instead (sections 3, 5 and 6). Inside
  `sup` and `sub` the other kind's script letters, and any mathematical symbol, bracket or dash
  outside the fold tables, reject (section 5). A bracketed footnote marker in `sup`
  (`<sup>[1]</sup>`) is therefore `unmappable-script`: a false failure, accepted.

  Round 3, also folded into 2.0.0: the digit-group edge rule reads the span's first and last
  code points that are not whitespace, so a span ending or starting in the whitespace between
  two groups ("…is 10␠" of "…is 10␠␠000") still cuts the number (section 6); the U+0009 status
  of a page slice's last line is that of the whole page line, so a span stopping before a row's
  U+0009 cannot turn the bullet in its first cell into a list item (sections 3 and 6); and the
  extractor contract says a list marker is followed by U+0020, not U+0009 (section 7).

- `fidelity-norm/1.1.1` (patch) — documents behaviour the vectors already pinned but the text
  left to the reference implementation, found when the check was re-implemented in a second
  language: the tokeniser grammar and error-code precedence, the two line breaks of a
  self-closing block, where `empty-narrative` is decided, what `reportHash` and
  `narrativeBindingSha256` cover, the exact `issues` strings, the reason-code catalogue and
  its order, overlap handling and its effect on coverage, the presence rules of section-result
  members, the diff-hint definitions, and the `passed` rule (section 1, 5, 6). Also states the
  canonical-JSON consequences an implementation must honour explicitly — no native-object
  reordering of integer-like keys, `\udXXX` for unpaired surrogates, integers only — after the
  reference itself was found to violate the first. No vector outcome changed.
- `fidelity-norm/1.1.0` — U+00AD followed by a line break deletes the break (step 1);
  cross-page slices are concatenated verbatim including blank gaps (section 6); body ranges
  must sit on line boundaries and exclude at most 240 code points (section 1); span edges must
  fall on word boundaries (section 6); `ol` and `q` reject, table parts must be in rendering
  order, and U+00AD before a structural line break rejects (section 5); attribute grammars
  tightened (section 5). Closes review rounds 2 and 3. No submission was ever approved under
  1.0.0, so no re-approval was due.
- `fidelity-norm/1.0.0` — initial specification.
