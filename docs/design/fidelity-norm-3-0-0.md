# `fidelity-norm/3.0.0`: numbered lists, table grids and pictures, seen as a reader sees them

_Proposal, 2026-09-23, amended after two independent reviews (findings listed at the end), and
implemented. `docs/fidelity-normalization.md` (3.0.0) is the normative text; where this note and
it differ, the specification wins. Prompted by roadmap item 3a (ADR 0005): the first real
label to go through the system, the EMA's own ePI for Imatinib Teva, has six numbered lists, 46
cells spanning columns, five spanning rows and two pictures, and every real summary of product
characteristics has some of each. 2.0.0 refuses all four._

## Why these were refused, and what changes

ADR 0003's rule stands: **false failures are acceptable; false passes are not.** Each construct
was refused because a renderer draws something the scanner did not see:

- `ol` (refused in 1.1.0): a renderer draws numbers ("1.", "a.", "iv.") that are not in the text,
  so a narrative list numbered from 3, or lettered where the source is numbered, verified
  against a source that says otherwise.
- `colspan`, `rowspan` (removed in 2.0.0): a spanned cell draws one value across several
  columns or rows. The first draft of this proposal treated that as the known cell-association
  residual of 2.0.0; the review showed it is wider. Without spans, reading order still ties a
  value to its row: showing "10 mg" against Children needs a second "10 mg" after "Children".
  With `rowspan`, `Adults | 10 mg (rowspan 3)` / `Children` / `Elderly` reads "Adults 10 mg
  Children Elderly", the same as a source where 10 mg is for adults only, and draws 10 mg
  against every row.
- `img` (always refused): a renderer draws a picture the check cannot read.

Every fix below does the same thing. What the renderer draws that changes what the words say
is folded into the text the check compares, and what cannot be folded is refused. For lists,
that means the numbers. For tables, it means the grid itself: the text states where every row
and cell starts, and which cell covers each slot. That also closes the cell-association residual
2.0.0 stated for tables without spans, apart from the line a value sits on inside a multi-line
cell (below). For pictures, it means the picture's bytes. Layout that changes where words stand
but not what they say (paragraph breaks, headings, line breaks, bullets, list nesting,
emphasis) is not compared, and is stated. The reserved code points that carry
the grid and the pictures can never occur in narrative text itself, so in the scanner's output
they mean exactly what the markup says.

## The changes

### A. `ol`: the renderer's numbers are text (§5)

- `ol` becomes a block element. Allowed attributes:
  - `type`, exactly `1`, `a`, `A`, `i` or `I`;
  - `start`, matching `0|-?[1-9][0-9]{0,3}`.
    Any other attribute on `ol` (`reversed`, `style`, `class`) rejects (`forbidden-attribute`), and
    so does any attribute on `li`, including `value`.
- `li` is allowed only as a direct child of `ol` or `ul`; anywhere else it is `misnested-tag`.
  (`<ol><li>a<li>b</li></li></ol>` is two items to an HTML parser, and an `li` inside a
  `blockquote`, `dd` or `td` of an item continues the counter; the review showed Chrome drawing
  "2." for both.)
- The only children of `ol` and `ul` are `li`. Any other element directly inside, any character
  reference, and any raw code point other than U+0009, U+000A, U+000D and U+0020 rejects
  (`list-content`, new). This newly rejects `<ul><p>…</p></ul>` and text directly in a `ul`,
  which 2.0.0 accepted.
- Each `li` that is a child of an `ol` emits its marker, then `.`, then U+0020, directly after
  the line break of its start tag. The item's ordinal is `start` (default 1) plus its index
  among the `li` children, counted from 0. The marker is the ordinal written in the counter
  style of `type`. An `ol` without `type` is decimal, whatever list encloses it.
  - `1`: decimal. A negative ordinal is U+002D followed by its magnitude, and there are no
    leading zeros.
  - `a` / `A`: alphabetic, for ordinals of 1 and above. Start with an empty string. While `n > 0`:
    set `n = n − 1`, prepend the letter at index `n mod 26` of `a…z` (or `A…Z`), and set
    `n = floor(n / 26)`. So 1 is `a`, 26 is `z`, 27 is `aa` and 703 is `aaa`. An ordinal of 0 or
    below is decimal.
  - `i` / `I`: additive roman, for 1 to 3999. Repeatedly take the largest value from this table
    that is not more than the remainder: 1000 `m`, 900 `cm`, 500 `d`, 400 `cd`, 100 `c`, 90 `xc`,
    50 `l`, 40 `xl`, 10 `x`, 9 `ix`, 5 `v`, 4 `iv`, 1 `i`. Use upper case for `I`. Any other
    ordinal is decimal.
- `ul` items emit nothing: a bullet carries no content, and §3 step 4 already removes a list
  bullet from page text.
- The marker is folded in as text, so it is normalised, compared and hashed like any other
  text. `<ol start="3"><li>Take</li></ol>` reads `3. Take`.

What this does not claim:

- The narrative cannot carry a stylesheet, because `style` and `class` reject, so the list style
  is the one `type` names. A viewer that applies its own stylesheet (`list-style-type`,
  `::marker`), or that draws no list numbers at all (a plain XML view), shows something other
  than what the check read. As in 2.0.0, a viewer's own styling is outside what any text check
  can see.

### B. Tables: the text carries the grid (§5, §7)

Grammar:

- `colspan` and `rowspan` are allowed on `td` and `th`, each matching `[1-9][0-9]{0,2}|1000`.
  `0`, leading zeros and any other form reject (`forbidden-attribute`).
- A `table` start tag while another table is open (in a cell or in a caption, at any depth)
  rejects (`table-structure`). Nested tables are refused rather than bracketed. No real label in
  `labels/ema-epi/` has one, and the grid text below then never nests.
- The tables of one narrative cover at most 50 000 slots together, counted as each cell's
  `colspan` × `rowspan` when it is placed; the cell that crosses the bound rejects (`table-size`,
  new), after its overlap check. Without it, 9 KB of markup produced 3 million code points of
  grid text.

Placement follows the HTML table model:

- In each row, a cell takes the first slot from the left that is not already covered. It then
  covers `colspan` slots in its own row and in each of the next `rowspan − 1` rows.
- If a slot the cell would cover in its own row is already covered (a `colspan` running into a
  cell spanning down from above), the result is `table-shape`, decided at that cell's start tag.
  That is the only way two cells can overlap: a cell's slots in later rows are, column by column,
  below its own-row slots.
- If a cell's rows run past the last row of its row group, the result is `table-shape`, decided
  at the end tag of that group (`thead`, `tbody`, `tfoot`, or `table` for rows directly under
  it). A renderer clips such a cell silently.
- At `</table>`, every row must cover exactly the slots 0 … w−1, with the same w for every row.
  Otherwise the result is `table-shape`. So there are no holes and no ragged rows.

Text. Six reserved code points, U+FDD0 to U+FDD5, carry the grid. Unicode sets them aside
permanently for internal use, and XML allows them. Written ⟦table⟧, ⟦/table⟧, ⟦row⟧, ⟦cell⟧,
⟦left⟧ and ⟦above⟧:

| Code point | Emitted                                                                           | Written  |
| ---------- | --------------------------------------------------------------------------------- | -------- |
| U+FDD0     | after the line break of `<table>`                                                 | ⟦table⟧  |
| U+FDD1     | U+000A, then U+FDD1, before the line break of `</table>`                          | ⟦/table⟧ |
| U+FDD2     | after the line break of `<tr>`                                                    | ⟦row⟧    |
| U+FDD3     | a slot where a cell starts: after the U+0009 of the cell's start tag, then U+0009 | ⟦cell⟧   |
| U+FDD4     | a slot covered by the cell to its left in the same row (its `colspan`)            | ⟦left⟧   |
| U+FDD5     | a slot covered by a cell in a row above (its `rowspan`)                           | ⟦above⟧  |

- Each ⟦left⟧ or ⟦above⟧ slot is emitted as U+0009, the code point, U+0009, in slot order. The
  scanner emits the ⟦above⟧ slots to the left of a cell just before that cell's start-tag break.
  It emits a cell's ⟦left⟧ slots just after its end-tag break. It emits the ⟦above⟧ slots after
  a row's last cell just before the line break of `</tr>`. A caption's text stands between
  ⟦table⟧ and the first ⟦row⟧.
- After normalisation a table reads, for example,
  `⟦table⟧ ⟦row⟧ ⟦cell⟧ Adults ⟦cell⟧ 10 mg ⟦row⟧ ⟦cell⟧ Children ⟦above⟧ ⟦/table⟧`. Every
  marker is its own whitespace-delimited token.
- This text determines the drawn grid. Rows are delimited by ⟦row⟧, and each row is a sequence of
  slots. A ⟦cell⟧ slot holds the text up to the next marker. A ⟦left⟧ slot belongs to the cell
  owning the slot to its left. An ⟦above⟧ slot belongs to the cell owning the slot above it. The
  table's end is ⟦/table⟧, so text after the table cannot be read as part of its last cell.
  Two narratives with the same normalised text therefore draw the same cells, with the same text,
  over the same slots. What is still not compared is whitespace inside a cell, whether a cell is
  `td` or `th`, `scope`, and which row group a row is in. These are presentation, because row
  groups are in rendering order (2.0.0) and spans cannot cross them (above). Nor is which line
  of a multi-line cell a value is on (second review, finding 1): `<td>10 mg<br/>&#160;</td>`
  and `<td>&#160;<br/>10 mg</td>` read the same next to a two-line cell, and a renderer draws
  the value level with a different line of its neighbour. Comparing lines was rejected: line
  alignment across cells also depends on wrapping and margins, and an extractor of a drawn
  document cannot tell a wrap from a line break, so a line rule would refuse most real PDF
  tables. ADR 0005's import, where both sides' markup is at hand, compares each cell's line
  breaks structurally instead.
- An empty cell is ⟦cell⟧ followed directly by the next marker. It is no longer indistinguishable
  from a covered slot, or from a slot the extractor dropped.

What this closes: the review's finding 3, and 2.0.0's stated residual that cell association is
not checked. A narrative that moves a dose from the Adults column to the Children column no
longer verifies; one that moves it to another line of the same cell still does (above). The price is on the extractor side (§7 below). An extractor that cannot
recover a table's grid cannot produce these markers, so any narrative with a table fails against
it. That is a false failure, and acceptable.

### C. `img`: a picture is bound to its bytes (§5, §7)

- `img` is an inline, void element. It must be self-closing, like `br` and `hr`, or it is
  `void-element`.
- It takes exactly one attribute, `src`, and `src` is required: `<img/>` without it is
  `forbidden-attribute`, decided after the attributes and before `void-element` (so `<img>` is
  `forbidden-attribute`, not `void-element`). `alt`, `title`, `width` and every other attribute reject
  (`forbidden-attribute`). `alt` is refused because a renderer draws it when the picture does not
  load, and its text would be compared with nothing.
- `src` must be exactly this form (the first draft also accepted a relative reference; the
  second review showed the EMA's own references draw at zero size, and that a reference that
  resolves draws whatever the viewer's origin serves, so references are refused):
  - A `data:` URI: `data:image/png;base64,` or `data:image/jpeg;base64,` followed by a non-empty
    body `B` of at most 1 398 104 code points (the base64 length of 1 MiB). `B` must satisfy all
    of the following:
    - every code point is `[A-Za-z0-9+/=]`;
    - its length is a multiple of 4;
    - `=` occurs only as the last one or two code points.

    These are counted and tested directly, not by one regular expression over a
    1.4-million-code-point value.

  - Anything else rejects (`forbidden-attribute`): a path, `https:`, `http:`, `javascript:`,
    protocol-relative `//…`, SVG or GIF `data:`, and a query or fragment. `https` is refused because the picture
    behind it can change after approval, and because fetching it tracks the reader.
- An `img` emits U+FFFC, the 64 lowercase hexadecimal digits of the SHA-256 of the UTF-8 bytes
  of its `src` value, and U+FFFC again. No whitespace is added. U+FFFC occurs nowhere else, so
  the text states where each picture stands and which bytes it draws. The closing U+FFFC
  composes with nothing, so a combining mark after the picture cannot join its last digit under
  NFC (second review, finding 11).
- An `img` may stand wherever inline text may. Inside `sup` or `sub` it is `script-content`, and
  directly inside a table part or a list container it is `table-content` or `list-content`, as
  for any element.

What this proves, and does not:

- The token binds the bytes. The narrative shows the source's own picture, and it cannot be
  swapped after approval without changing `normalizedTextSha256` and the binding.
- A `data:` media type is not checked against the bytes. A renderer sniffs them and may draw a
  GIF or WebP, including an animated one, but it draws the same bytes on both sides.
- What a picture shows is unverifiable by a text check. A picture of a dose table is carried
  faithfully, but it is not read. The query service must say that a section's text contains a
  picture (PR 5).
- Refusing `alt` removes a text equivalent. The EMA's black triangle, when it is a picture,
  reaches a screen reader as nothing.

### D. Reserved code points (§2, §5)

- U+FFFC and U+FDD0–U+FDEF (the whole Arabic Presentation Forms-A noncharacter block, of which
  3.0.0 uses six) are reserved. Narrative that contains one fails with `malformed-narrative`,
  reason `reserved-character` (new). This covers the `div` decoded from JSON, checked after the
  §2 check and before the scan, and any character reference that decodes to one, checked right
  after `forbidden-character`. So `<tr>&#xFFFC;</tr>` is `reserved-character`, not
  `table-content`, and `<sup>&#xFFFC;</sup>` is not `unmappable-script`.
- The rule is one-sided, and deliberately so. Page text may contain these code points, because
  §7 has the extractor emit them for grids and pictures. This is the only rule applied to one
  side only, and the document preamble says so.
- An extractor must refuse the document, and emit no SourceDocumentText, if its text layer itself
  contains U+FFFC or a code point in U+FDD0–U+FDEF, because the check would read it as a picture
  or as grid structure.

### E. Extractor contract (§7)

The contract replaces the 2.0.0 table and list rules:

- **Tables.** Emit each table as the scanner does. Emit ⟦table⟧ and then the caption, if any.
  Emit each row as ⟦row⟧ followed by its slots, left to right. A slot where a cell starts is
  U+0009 ⟦cell⟧ U+0009, then the cell's text. A slot covered by a merged cell is U+0009 ⟦left⟧
  U+0009 when the merge extends from the left in the cell's first row, and U+0009 ⟦above⟧
  U+0009 otherwise. End with ⟦/table⟧. Rows are separated by U+000A, and a line break inside a
  cell is U+0020, as before. Every row has the same number of slots. An extractor that cannot
  tell a merged slot from an empty cell must refuse the document, and must not guess.
- **Numbered lists.** Emit the number as drawn, followed by U+0020, never U+0009 and never
  nothing. A browser-made PDF that has no space code point after the number must still yield
  U+0020.
- **Pictures.** An inline picture in the text's reading order is U+FFFC, the SHA-256 hex of the
  `data:image/png;base64,` or `data:image/jpeg;base64,` URI of its exact bytes in canonical
  padded base64 (the media type is that of the stored part), and U+FFFC. A structured source's
  referenced picture is resolved to its bytes when they can be fetched and pinned, and otherwise
  draws nothing and is emitted as nothing (ADR 0005 records it).

  A picture is an embedded raster image drawn inline, including a logo and a decorative picture. Vector drawings, shapes and text boxes are not pictures, and
  the extractor must refuse the document when it meets one it cannot read as text. An extractor
  that cannot place pictures must refuse a document that has them.

- **Line layout.** Every block is on its own line, as the scanner writes it: ⟦table⟧, a caption,
  each ⟦row⟧ and ⟦/table⟧ each start a line, and so does a paragraph, heading or list item. A
  title printed above a table in a PDF is a paragraph before ⟦table⟧ (a PDF has no captions). A
  `ul` item's bullet is emitted as §3 step 4 removes it, or not at all.
- **Structured sources.** An extractor over a structured source (an authority's FHIR ePI, ADR 0005) emits one page per source section, in source order, with the whole page as the body,
  beginning and ending with U+000A as the scanner's text does. Each page's text is the section
  narrative as a renderer draws it, following the rules above. The narrative section's span
  covers that page's body. §1 and §6 then apply unchanged.

## Impact

- **Major** under §8. The extractor contract changes for tables, lists and pictures. Vector
  outcomes change: `ol`, `img`, `colspan` and `rowspan` move from rejection to accepted with
  folded text; every table's text gains the grid markers; and `li` outside a list, and non-`li`
  content in a `ul`, newly reject. Every submission approved under 2.0.0 needs re-approval, and
  the seeded synthetic demo (`docs/demo/verifiable-label.md`) is re-seeded. No real submission
  has been approved yet.
- The narrative text of every 2.0.0 table changes, so `normalizedTextSha256` and
  `narrativeBindingSha256` change for any section with a table.
- **Consumers.** `get_section` returns normalised text, so its table text carries the markers.
  A quote that spans two cells must carry them too, or `verify_quote` finds no match. That is a
  false failure, and a correct one, because a cross-cell quote loses which cell a value is in.
  The agent's instructions, and the query's presentation of the markers, are updated in PR 5.
  `transform.ts` decides whether a section's narrative is present from the scanner's visible
  text, and must not count markers as visible content.
- Both implementations (`src/fidelity/xhtml.ts`, `zone-a/src/zone_a/fidelity/xhtml.py`) change
  together. The differential generator gains lists, spans, pictures, nested tables and reserved
  references, including the overlap, clip and hole cases.
- **Reason codes.** `list-content`, `reserved-character` and `table-size` are added to the
  catalogue, in its order: `reserved-character` after `forbidden-character`, `list-content`
  after `void-element`, `table-size` after `table-shape`. A narrative whose normalised text is
  only U+0020 and grid markers is `empty-narrative` (a table of empty cells draws nothing), and
  `transform.ts` uses the same rule for whether a narrative is present. The §5 precedence
  statement is extended:
  - At a start tag, the parent check has four steps, in this order:
    1. `script-content`;
    2. `misnested-tag`, for a table part or `li` in the wrong parent;
    3. `table-content`;
    4. `list-content`.
  - After the parent check come `table-structure` (which now includes a nested `table`), then
    `table-section-order`, then `table-shape` for an overlapping cell, then `table-size`.
  - At an end tag, `table-shape` is decided for `</table>`, `</thead>`, `</tbody>` and
    `</tfoot>`.
  - At `&`: `reserved-character` comes right after `forbidden-character`, and `list-content`
    right after `table-content`.
  - At a raw code point, the first applicable code wins: `text-outside-root`, then
    `table-content`, then `list-content`, then `unmappable-script`.

## Cases the next review should run

Against the reference, parse5, and a browser in both HTML and XML parsing:

1. `<ol start="3"><li>Take</li></ol>` reads `3. Take`. It verifies against "3. Take" and
   mismatches against "1. Take".
2. `<ol type="a">` with 27 items: the 27th is `aa.`, and item 703 is `aaa.`.
3. `<ol type="i" start="4000">` gives `4000.`. `<ol type="I" start="-1">` gives `-1. 0. I.`.
4. `<ol type="a"><li>x<ol><li>y</li></ol></li></ol>`: the inner list is decimal, and restarts at
   `1.`.
5. `<ol> text <li>x</li></ol>` and `<ul><p>x</p></ul>` are `list-content`. `<ol><li>a<li>b</li></li></ol>`
   and `<div><li>x</li></div>` are `misnested-tag`.
6. A `rowspan` crossing from `thead` into `tbody` is `table-shape` at `</thead>`.
7. Rows `[colspan=2][1]` / `[1][1]` are `table-shape` at `</table>`, and `[colspan=2][1]` /
   `[1][1][1]` are accepted: both rows cover three slots.
8. `rowspan=2` in row 1, column 1, followed by a row 2 with two cells, gives three slots in each
   row. It reads `⟦row⟧ ⟦cell⟧ A ⟦cell⟧ B ⟦cell⟧ C ⟦row⟧ ⟦above⟧ ⟦cell⟧ D ⟦cell⟧ E`.
9. The review's overlap: `<tr><td>Adults</td><td rowspan="2">10 mg</td></tr><tr><td colspan="2">Children</td></tr>`
   is `table-shape` at the second row's cell.
10. The review's case 3 (rowspan 3 on "10 mg") reads with two ⟦above⟧ slots, and mismatches
    against a source row set with empty cells there.
11. A value that sits in column 2 in the narrative and column 1 in the source mismatches.
    This case was accepted in 2.0.0.
12. `<table>` inside a `<td>`, and inside a `<caption>`, is `table-structure`.
13. `<img src="data:image/png;base64,AA=="/>` inside `<p>` gives U+FFFC, the hash and U+FFFC, in
    place. Other bytes mismatch; a reference (`~/_entity/annotation/0c1d…`) is
    `forbidden-attribute`.
14. `<img src="data:…" alt="Take 10 mg"/>` and `<img/>` are `forbidden-attribute`.
    `<img src="data:…">` is `void-element`.
15. `&#xFFFC;` and a raw U+FDD3 in text are `reserved-character`. So are
    `<table><tr>&#xFFFC;</tr></table>` and `<sup>&#xFDD0;</sup>`.
16. These `src` values reject: `javascript:x`, `//evil/x`, `../x`, `a/../x`, `/x`, `x`,
    `https://h/x`, `data:image/svg+xml;base64,AAAA`, a base64 body of length 5, and `=` in the
    middle of a body.
17. 50 rows of `<td colspan="1000">` are accepted; one more slot is `table-size`.
18. `<table><tr><td></td></tr></table>` alone is `empty-narrative`.

## First review (2026-09-23): findings and what changed

The first review checked the proposal with parse5 7.1.2 and Chrome 153. Its scripts are kept in
the session scratchpad.

1. **High.** An `li` not directly in an `ol` still gets a number. Fixed: `li` is allowed only in
   `ol` or `ul`.
2. **High.** Overlap is possible by construction. Fixed: overlap is checked at placement.
3. **High.** Spans widen cell association. Fixed: the text carries the grid for every table
   (B), which also closes 2.0.0's residual. The review's other option, keeping spans refused and
   having the importer write tables without spans, was rejected. Unmerged, a dose that the source
   draws against every age group is drawn against the first only: the words are the same, but the
   label says something else.
4. **High.** The content of an image is unbound. Fixed: `src` is hashed into the text (C),
   `https` is refused, and the residual for references is stated.
5. **High.** Places where the implementations could disagree:
   - (a) The span grammar stopped at 999. Fixed: it now includes 1000.
   - (b) `start` accepted `-0`. Fixed.
   - (c) Character references inside lists were not covered. Fixed: they reject.
   - (d) `<img/>` without `src` had no reason code. Fixed: it is `forbidden-attribute`.
   - (e) The precedence of `reserved-character` was unstated. Fixed: it has its own pre-scan
     step, and comes after `forbidden-character` for references.
   - (f) The precedence of `list-content`, and where clipping is decided, were unstated. Fixed:
     both are stated.
   - (g) The base64 rules were not explicit. Fixed: they are stated as direct counts and tests.
   - (h) The `src` grammar needed a regex. Fixed: it is a regex, with no dot-initial segments.
   - (i) The counter styles were given only by name. Fixed: they are spelled out.
6. **High (ADR 0005).** Dropping style is not presentation-only. See ADR 0005, decision 1 as
   amended. Raised and lowered runs are folded as §7 requires. Struck, hidden, faint and tiny
   text is refused.
7. **High (ADR 0005).** Equality was undefined for list numbers. The reader, acting as an
   extractor under §7, draws them with the same algorithm (ADR 0005, decision 1).
8. **High (ADR 0005).** The gate for FHIR-source imports was unspecified. Fixed: E, structured
   sources, gives one page per source section, and §1 and §6 apply unchanged.
9. **Medium.** A `src` path is a text channel. It is now compared (C): a narrative can carry only
   the reference its source carries.
10. **Medium.** The extractor contract had gaps. Fixed (E): the numbered-marker space rule, the
    refusal of reserved code points in the text layer, and a definition of a picture.
11. **Medium.** The ADRs and AGENTS.md conflicted. Fixed: ADR 0001 and ADR 0003 are amended,
    and AGENTS.md's "Preserve supplied XHTML" rule is reworded to the owner's decision of
    2026-09-23.
12. **Low.** Residuals were unstated. They are now stated under A and C.
13. **Low.** Wording. The one-sided rule is stated in §1's preamble, and the impact claim is
    corrected.

## Second review (2026-09-23): findings and what changed

The second review tested the amended proposal and the draft TypeScript against Chrome 153 (one
instance, HTML and XML parsing; list numbers from the accessibility tree, grids from cell
rectangles) and parse5 7.1.2, and ran 1000 random span tables whose grid decoded from the
scanner's text equalled Chrome's drawn grid in both modes. Its scripts are in the session
scratchpad.

1. **High.** A line break inside a cell reopens cell association (`10 mg<br/>&#160;` against
   `&#160;<br/>10 mg` next to a two-line cell). Decided: stated as a residual (B), and the
   overclaim withdrawn; comparing lines would refuse most PDF tables, because wrapping and line
   breaks look alike on a page. ADR 0005's import compares each cell's line breaks
   structurally.
2. **High.** A table inside a caption was accepted and the grid nested. Fixed: any `table` while
   a table is open is `table-structure`.
3. **High.** The end-of-table marker's line break was stated differently in the code and here.
   Fixed: U+000A, then U+FDD1, then the end tag's break, in both documents.
4. **High.** The precedence of a missing `src` was unstated here. Fixed (C).
5. **High.** A reference `src` binds a string, not what is drawn; the EMA's references draw at
   zero size. Fixed: `src` is a `data:` URI only, and ADR 0005 decision 3 carries a referenced
   picture as its fetched, pinned bytes, or not at all when it cannot be fetched (recorded).
6. **Medium.** A small table produced a huge text. Fixed: 50 000 slots per narrative
   (`table-size`).
7. **Medium.** An empty table counted as present. Fixed: grid markers are not drawn text, for
   `empty-narrative` and in `transform.ts`.
8. **Medium.** The extractor contract left line layout open. Fixed (E, line layout and
   structured sources).
9. **Medium (ADR 0005).** The reader and the clean-div builder could share a misreading. Fixed in
   ADR 0005: a renderer cross-check in CI over every pinned publication, the reader refusing
   out-of-grammar values, self-closing non-void elements and zero-size pictures, and the
   authority stylesheet stated as a residual.
10. **Medium.** The ADRs and AGENTS.md claimed more than is proved. Fixed: AGENTS.md now says the
    check proves words, list numbers, table grids and embedded pictures, and that paragraph
    breaks, headings, bullets, list nesting and emphasis are kept, not proved; ADR 0003's first
    two Consequences are updated; list nesting and bullets are stated as residuals (§5).
11. **Low.** NFC could merge a combining mark into a picture's last digit. Fixed: the token is
    closed by U+FFFC.
12. **Low.** Slots are the table model's logical slots; a renderer draws some at zero size.
    Stated (§5): at most a false failure.
13. **Low.** Test case 15, "§1's preamble", and a Markdown formatter turning "(default 1) plus"
    into a numbered list in the specification. Fixed.
14. **Low.** The catalogue order of the new codes. Stated (Impact).
