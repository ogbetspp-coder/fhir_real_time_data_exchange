# `fidelity-norm/3.0.0`: numbered lists, table grids and pictures, seen as a reader sees them

_Proposal, 2026-09-23, amended after two independent design reviews and thirty-two reviews of the
implementation (findings listed at the end), and implemented. `docs/fidelity-normalization.md`
(3.0.0) is the normative text; where this note and it differ, the specification wins. Prompted by roadmap item 3a (ADR 0005): the first real
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
- A row that covers a slot but in which no cell spanning one row starts is `table-shape` at its
  `</tr>`, and a column in which no cell spanning one column starts is `table-shape` at
  `</table>` (third review, finding 1): a renderer draws such a row at zero height and such a
  column at zero width, so a cell starting there reads, drawn, against its neighbours only.

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
  each ⟦row⟧ and ⟦/table⟧ each start a line, and outside a table cell so does a paragraph,
  heading or list item (inside a cell they stay on the cell's line). A title printed above a
  table is a paragraph before ⟦table⟧ unless the document marks it as a caption (a Word caption
  bound to the table, a tagged PDF's `Caption` element). Outside a cell a `ul` item's bullet is
  emitted as §3 step 4 removes it, or not at all; inside a cell, not at all.
- **Page breaks.** A table that continues across a page break is written as its logical text:
  one ⟦table⟧, the caption, the rows in order, each row's slots in order with each cell's text
  whole (a cell spanning rows in the row it starts in), one ⟦/table⟧, and nothing else between
  them. A repeated header row is written where the table first has it, a repeated footer row where
  it last has it, a continuation label not at all, and a page footnote drawn between the table's
  parts after ⟦/table⟧. The logical text is split once per break, at a cell or row boundary,
  whitespace in a cell, or a hyphenated word; the later page then begins with U+0009, ⟦row⟧, or
  the rest of the word. Any allowed split gives the same normalised text (the spec's §7 is the
  full text; rounds 4 to 7 below found the cases this rule now covers).
- **Structured sources.** A structured source is an FHIR ePI document Bundle with XHTML
  narratives. Its extractor emits one page per source section, holding exactly §5's scanner text
  of T(div), where T is ADR 0005's lexical transform (closed-list deletions, raised and lowered
  runs rewritten as `sup`/`sub`, referenced pictures replaced or deleted); a section the scanner
  refuses, or whose text holds a soft hyphen or another invisible character, refuses. The
  drawn-document rules do not apply, and §1 and §6 apply unchanged (the spec's §7 is the full
  text).

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
  `transform.ts` decides whether a narrative is present by a rule of its own (§5). The §5 precedence
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
17. One row of 1000 single cells and 49 rows of `<td colspan="1000">` are accepted; one more
    slot is `table-size` (50 rows of the spanning cell alone are `table-shape`: no single cell
    starts in columns 1 to 999).
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

## Third review (2026-09-23, of the implementation): findings and what changed

The third review fuzzed the two implementations against each other with 40 000 cases of its
own (no divergence), drew 800 random span tables in Chrome, and applied its own mutations.

1. **High.** A row in which cells start but all span down is drawn at zero height, and a column
   no single-column cell starts in at zero width, so "600 mg" spanning an Adults row and a
   Children row was drawn against Children only while the text read both. Second review finding
   12 had called this a false failure only. Fixed: both are `table-shape` (B), and §5 states that
   a thin row or column (only empty single cells) is still drawn, a few pixels high.
2. **Medium.** Work grew with rows × table width: 20 000 empty rows under a 50 000-slot row took
   20 s (TypeScript) and 79 s (Python). Fixed: the grid is sparse in both, so a row costs only
   the slots it covers (32 ms and 90 ms), with a timed test on each side.
3. **Medium.** Three precedence rules of §5 (forbidden before reserved, a missing `src` before
   `void-element`, overlap before `table-size`) were pinned by no vector. Fixed: three vectors
   and a differential class.
4. **Medium.** `get_section`'s contract caps a `div` at 200 000 code points, so a section with a
   large picture is `unavailable`. Recorded in the change record; the query contract changes in
   roadmap item 3a's publishing step, and the Imatinib Teva import carries no picture.
5. **Medium.** A picture alone satisfied a mandatory section. Fixed: the crosswalk ignores
   pictures when a mandatory section must carry text, and counts them when an uncoded section
   would otherwise be dropped.
6. **Low.** "The same rule" decides presence was false. Fixed: §5 states the crosswalk's
   stricter rule.
7. **Low.** Change-record counts and claims. Fixed.
8. **Low.** §7 did not say how a table crossing a page break is written, or which stored image
   forms are a PNG or JPEG file. Fixed.

## Fourth review (2026-09-23, of the implementation): findings and what changed

The fourth review drew about 5 700 accepted random span tables in Chrome (standards, quirks and
XML modes; default and bordered cells; two viewport widths) and found none whose drawn grid
differed from the text, and fuzzed the two implementations with 20 000 cases of its own (no
divergence).

1. **High.** A table cell continued across a page break: §1 ends a page body with a line feed,
   so the rest of the cell opened a line without U+0009, and §3 step 4 removed a leading bullet
   on the page side only (`2` / `• 10` verified against `2 10`). Fixed in §7: the text that
   continues a row after a page break begins with U+0009; two verify vectors pin it.
2. **Medium.** `verify_quote` matched a quote carrying grid markers across two rows, and a quote
   of a lone marker. Fixed: such a quote is `invalid-request`.
3. **Medium.** The Python port takes up to 1.5 s on 2 MB narratives (linear). Recorded.
4. **Low.** §5's wording on empty rows. Fixed.
5. **Low.** A bare list number counts as mandatory narrative. Kept, and recorded.
6. **Low.** `get_section`'s cap also applies to the normalised text, which a grid lengthens.
   Recorded.

## Fifth review (2026-09-23, of the implementation): findings and what changed

The fifth review ran every page-break and section-boundary case of tables, lists and pictures
through both verifiers (identical report hashes) and found no false pass in the verifier's own
rules.

1. **High.** Round 4's rule (a row's continuation after a page break begins with U+0009) stopped
   a word hyphenated at the break inside a cell from joining, since step 1 removes U+00AD U+000A
   but not the U+0009 after it: "impair ment" verified and "impairment" did not. Fixed in §7: when
   the body before the break ends with U+00AD U+000A, the continuation begins with the rest of the
   word. Two verify vectors pin it.
2. **Medium.** The agent's test double still answered `match` for a quote carrying grid markers,
   which the service now refuses, and the change record understated the agent's behaviour. Fixed:
   the double refuses what the service refuses (tested), and the record says that until PR 5 a
   block quoted from a section with a table or picture is unverified.
3. **Low.** A tagged PDF does mark captions; repeated footer rows and continuation labels; stale
   wording in ADR 0003 and the validation README. Fixed. §3 step 4's and §6's table-row examples
   predate the grid; they describe the U+0009-line rule, which still applies, and a 3.0.0 table
   row additionally starts with U+FDD2.

## Sixth review (2026-09-23, of the implementation): findings and what changed

The sixth review ran 67 cases over 22 page-break scenarios through both verifiers (identical
report hashes), fuzzed 30 000 narratives against the §7 line invariant (no violation), and
compared the agent's test double with the service over every code point.

1. **High.** §7 gave no conforming output for a row broken across a page in more than one cell
   (Word's default lets rows break): a per-page extractor wrote "10 mg" on page 1 and the rest of
   "Adults with renal / impairment" on page 2, and a narrative moving "impairment" into the "10
   mg" cell verified. Fixed in §7: a row's text is emitted in slot order, each cell whole; the
   text after the break in the row's first continued cell, and every later slot, go on the later
   page, or the extractor refuses the document. Two verify vectors pin it.
2. **Medium.** The agent's test double accepted quotes the service refuses because they normalise
   to nothing (a soft hyphen, U+200B, U+2060, U+FEFF, a line-start bullet). Fixed, and checked
   against the service's normalisation on all 11 110 strings of up to four characters drawn from
   those classes.
3. **Medium.** `zone-a/README.md` still named 2.0.0 and 411 vectors. Fixed.
4. **Low.** Wording: a tagged PDF's captions in this note, the crosswalk's rule in §5 ("its own",
   not "stricter"; U+FEFF named), the §7 invariant limited to body text, and the line-layout and
   `ul`-bullet rules limited to text outside a table cell. Fixed. The review also noted that ADR
   0003's Unicode-version bullet reads more strongly than its amended Decision; that wording is on
   `main` already and is left for the next change to that ADR.

## Seventh review (2026-09-23, of the implementation): findings and what changed

The seventh review ran 54 page-break cases through both verifiers (identical report hashes),
compared the agent's test double with the service on 2 962 083 quotes (no divergence), and
checked 120 004 list counters in both languages (identical).

1. **High.** A page footnote drawn between a table's two parts read as the last cell's text, so a
   narrative moving the footnote into that cell verified.
2. **Medium.** Where a spanning cell's overflow goes was open to a page-by-page reading that let
   a word move into the next row.
3. **Medium.** A repeated footer row was to be written "where the table first has it", mid-table.
4. **Low.** "First continued cell" was undefined when that cell has no text on the earlier page.
5. **Low.** The line-layout wording in this note, and the zone-a README's count of added vectors.

Rounds 4 to 7 each found one more page-break case, so §7 was rewritten from a list of cases to
one rule: a table across pages is its logical text (slot order, each cell whole, a spanning cell
in the row it starts in, nothing else inside it; a repeated header where the table first has it,
a repeated footer where it last has it, a footnote after the table), split once per break at a
boundary, whitespace or a hyphenated word. Six verify vectors pin the three new cases.

## Eighth review (2026-09-23, of the implementation): findings and what changed

The eighth review built the logical text of 450 random tables and applied every allowed split
point, with and without running headers, excluded labels and repeated rows, in one- and
two-section layouts and three-page double splits: 105 247 whole-section cases, identical in both
languages. Every case verified except a caption split before a bullet (finding 1).

1. **High.** A caption continued after a page break began without U+0009, so a bullet at its
   start was removed on the page side only. Fixed: the extractor inserts U+0009 before a
   caption's continuation, as before a cell's.
2. **High (present since 2.0.0).** A paragraph wrapped, or broken at a page, just before a
   mid-line bullet read "Take 2 10 mg": §1 makes every page break a line feed, and §7 said
   nothing about wrapped lines. Fixed in §7: a continuation line of a paragraph, heading, list
   item or caption that begins with a bullet glyph and whitespace is written with a leading
   U+0009, or the extractor refuses the document.
3. **Medium.** A table drawn inside a source's table cell could be flattened into the outer cell.
   Fixed: it has no logical text, and the extractor refuses the document.
4. **Medium.** "After the table" for a between-parts footnote had two readings. Fixed:
   immediately after U+FDD1, in page order and then reading order.
5. **Low.** A continuation label is excluded like a repeated row, and repeated copies are known
   by the document's structure, never by matching text. Wording of the split point (U+FDD1 at a
   page end, §3 step 5 whitespace, the extractor inserting U+0009). Formatting of the seventh
   review's list. Fixed. Seven verify vectors pin findings 1, 2 and the repeated header row.

Rounds 4 to 8 found every remaining case in the extractor contract (§7), not in the scanner or
the verifier: their logic has not changed since round 3, and the two implementations have agreed
on every input every round has tried.

## Ninth review (2026-09-23, of the implementation): findings and the decision it led to

The ninth review ran 60 targeted cases through both verifiers (identical report hashes) and found
four more cases in how a drawn document's extractor must write its text, and three Medium ones.
Like every finding since round 4, all are in §7, the extractor contract; the scanner and verifier
logic has not changed since round 3, and the two implementations have agreed on every input any
round has tried.

**Decision.** Rounds 4 to 9 each found further drawn-document layout cases, several of them
present since 2.0.0. Reading a laid-out page into text is a design problem of its own, and it
belongs with the drawn-document extractor (roadmap: the engine, Word drafts), not with this
change, whose purpose is the structured-source import of ADR 0005. A structured source avoids these cases
only because §7 now defines its page as exactly the scanner's text for the section's div once
ADR 0005's droppable presentation is removed, and refuses a section the scanner refuses or that
holds a soft hyphen or another invisible character (round 10 found that a looser reading, "as a
renderer draws it", let a soft hyphen or a typed bullet through on a structured page). So 3.0.0 qualifies structured-source
extraction, and says in §7 that drawn-document extraction is not qualified and may not support an
approval until a later version closes the items below. No drawn-document extractor exists in the
repository.

## Drawn documents: open items

Each item is a case where text a conforming drawn-document extractor may write lets a narrative
verify that the document does not draw, or admits two outputs with different verdicts, with the
fix its review proposed. They are the starting list for the drawn-document contract.

1. **Wrapped lines (round 9, High; since 2.0.0).** §7 does not say how a line the document wraps
   inside a block is written. Written with U+000A, a wrap at the space between the groups of a
   number (`10` / `000 IU`) or after a dash (`1–` / `2 tablets`) is a token boundary to §6, so a
   section can end at "is 10" or start at "2 tablets". Proposed: join a wrap with U+0020 where the
   document has whitespace and with nothing after a hard hyphen or dash (U+000A only for a block
   boundary or a drawn line break; U+00AD U+000A for a discretionary hyphen), and restrict a page
   split inside a block to whitespace that does not separate the groups of a number and never
   directly after a hard hyphen or dash. This also confines the round-8 continuation rule to page
   breaks. §6's sentence on "a number at the end of one line and a number at the start of the
   next" then covers drawn line breaks only.
2. **Undrawn soft hyphens (round 9, High).** A U+00AD the text layer holds at the end of a block
   (a Word optional hyphen) is written before the block's line break, and step 1 joins the two
   blocks ("The dose is 1" / "0 mg daily." reads "The dose is 10 mg daily."), also across a page
   break. Proposed: U+00AD is followed by a line break only where the document breaks a word
   across those lines; any other U+00AD there is not written.
3. **Bullets at line starts (round 9, High, and round 8).** The continuation rule triggers on the
   raw text, but step 4 reads text after steps 1 to 3: an invisible character before or after the
   bullet, leading whitespace, or a lone bullet at the line's end escapes it. Proposed: trigger on
   a line that step 4 would read as starting a list item. And the rule's two sentences disagree on
   a line after a drawn line break and on a paragraph that starts with a typed bullet. Proposed: a
   block's first line and a line after a drawn line break are written without U+0009, so the
   bullet is list structure on both sides (as the vector `bullet-after-br-is-a-list-item`
   expects). A vector for a paragraph continued across a page break with a bullet at the page
   head is missing.
4. **Text outside the block flow (round 9, Medium).** A page footnote, margin text or a
   watermark drawn inside a continued paragraph or list is written in reading order, mid-sentence.
   Proposed: text or pictures drawn between two parts of a block, and not part of it, are written
   immediately after the block ends, in page order and then reading order; other out-of-flow text
   is excluded within §1's bound or the document refused.
5. **A caption split just after U+FDD0 (round 9, Medium).** The table rule does not say how the
   later page begins when the split falls between U+FDD0 and the caption. Proposed: with the
   caption on its own line, without U+0009.
6. **Hard or discretionary hyphen (round 9, Low).** An extractor that cannot tell whether a
   line-end hyphen is drawn as a hard hyphen or is discretionary should write it verbatim.
7. **A section starting at a continuation line (round 9, Low).** It can never be matched by a
   narrative (a false failure §6 does not list).

## Tenth review (2026-09-23, of the scoped version): findings and what changed

The tenth review checked the structured-source path as scoped (one page per section; §1 and §6
held for every scanner output it tried, 1 685 TS/Python replays identical), confirmed that
deferring the gate's enforcement to PR 2 is safe (the only producer is the synthetic builder, and
`AGENTS.md`'s synthetic-only rule bars real drawn content), and found:

1. **High.** The structured-source rule read two ways ("the scanner's own reading" and "as a
   renderer draws it under the rules of this section"); under the second, a soft hyphen before a
   `br` or at a block end, and a typed bullet after a `br`, let a narrative verify that the source
   does not draw. Fixed: the page is exactly the scanner's text for the div once ADR 0005's
   droppable presentation is removed; a section the scanner refuses, or that holds a soft hyphen
   or another invisible character, refuses; the drawn-document rules do not apply.
2. **High.** ADR 0005 dropped "every style", with only a few exceptions named, so a symbol font
   (`m` drawn as `μ`), `text-transform`, `list-style-type`, `vertical-align: top` on inline text or
   a large margin could change the drawn text unseen. Fixed: ADR 0005 has a closed list of
   droppable presentation; symbol fonts, hidden text and every unlisted property refuse.
3. **Medium.** "Structured source" was an example, not a definition. Fixed: an FHIR ePI document
   Bundle with XHTML narratives; every other source, any Word document included, is drawn.
4. **Medium.** `AGENTS.md`, UR-09 and the demo script claimed the check without its scope. Fixed.
5. **Medium.** No test covered the qualified path. Fixed: a property test on each side verifies
   every accepted XHTML vector as a structured page of its own and all together.
6. to 8. **Low.** A wrong cross-reference in §7, an unstated order-dependent false failure (closed
   by refusing soft hyphens), the change-record index, the roadmap's engine item and the
   change record's interim control. Fixed.

## Eleventh review (2026-09-23, of the scoped version): findings and what changed

The eleventh review found the scanner, the verifier and the two implementations sound (804 divs
derived from the four pinned EMA labels gave identical texts and codes in both languages; Chrome's
drawn text of 555 accepted EMA divs matched the scanner's words), and found the remaining gaps in
ADR 0005, the import's rules, which PR 3 implements:

1. **High.** Decision 1 had no rule for HTML attributes and contradicted itself (`dir="rtl"`
   reorders "10 mg or 20 mg"; `bgcolor` hides a cell; refusing every attribute refuses every EMA
   table).
2. **High.** Its CSS list admitted text hidden or overprinted (inline padding with a
   white background, borders, `line-height: 0`, negative indents and margins, `height: 0`,
   `baseline` on a `sup`, relative font sizes, transparent colours, a family list ending in a
   listed font).
3. **Medium.** "Exactly the scanner's text once presentation is removed" could
   not express the rewrites the ADR requires (raised runs, referenced pictures).
4. **Medium.** How
   presentation is removed, and the cross-check's parse mode, were unstated.
5. **Medium.** The
   pinned Imatinib Teva label cannot be imported under the rules as written: six sections refuse
   in the scanner and three more under the colour rules.
6. **Medium.** A stale sentence in this
   note's E.
7. to 10. **Low.** Decoded text, the demo script, a pairwise property, empty sections.

**Decision.** Each round of review of ADR 0005's detailed lists found more, because those lists
decide what may be dropped from real markup and can only be settled against the real label.
ADR 0005 now states the principle (the page is the scanner's text of T(div), a lexical transform
of the div string that deletes only what cannot change the drawn page, rewrites raised runs and
resolves pictures) and precise requirements for T, the renderer cross-check (HTML and XML modes,
visibility included) and empty sections; the closed lists themselves, and the label's refusals
(including whether `½` and `∞` in `sub` need a minor version of this contract), are PR 3's, with
its own reviews. §7 matches. Findings 6 to 10 are fixed here, with a pairwise property test.

## Twelfth review (2026-09-23, of the scoped version): findings and what changed

The twelfth review found no TS/Python divergence (every probe gave identical report hashes) and:

1. **High (since 1.0.0).** U+1680 OGHAM SPACE MARK is drawn as a stroke ("Take 2▬10 mg"), but §3
   step 5 normalised it as a space, so a page with it verified against a narrative with a space.
   Fixed: it is content; the crosswalk counts it as drawn; three vectors.
2. **High (since 2.0.0).** §5 did not state the `href` grammar (ports, user information, an
   underscore, host length, empty segments, a trailing slash). Fixed: the whole regular
   expression; nine boundary vectors.
3. **Medium.** ADR 0005 said T does three things and also unwrapped spans and links; §7 did not
   list unwrapping. Fixed: a fourth, closed operation in both.
4. **Medium.** Raised offsets were judged run by run; nested small offsets add up to a
   superscript. Fixed: the total baseline shift per glyph, and the cross-check compares it.
5. **Medium.** "Draws nothing" was undefined and an uncovered page could hold text. Fixed:
   §5's `empty-narrative` test on the page, and every page without a span must be blank
   (importer and PR 2's gate).
6. **Medium.** Picture sizes were unbounded. Fixed: a stated lower bound, and the cross-check
   compares each picture's drawn box.
7. **Medium.** A picture that "cannot be fetched" depended on who fetched. Fixed: resolve against
   the authority's published base URL; delete only on pinned evidence; any other failure fails.
8. **Medium.** §7 hashed canonical base64 while ADR 0005 carried an embedded picture as it is.
   Fixed: a structured picture's token is the hash of `src` as T(div) holds it.
9. **Low.** The cross-check's stylesheet wording, a relative faint-text bound, section 10's
   negative margin, the pinned Unicode version, the change record's review count, and this
   note's case 17. Fixed. The pairwise property test is a tautology for whole-page spans, as the
   review noted; its value is the pairing of narratives, and the refusal paths belong to the
   importer's tests (PR 3).

## Thirteenth review (2026-09-23): findings and what changed

The thirteenth review swept the whole specification, old rules and new, against Chrome 153 in
HTML and XML modes, and found four false passes in rules older than 3.0.0:

1. **High.** Past 512 open elements Chrome's HTML parser attaches new nodes elsewhere
   ("Platelets 10/l⁹"). Fixed: at most 32 elements open below the root (`nesting-depth`).
2. **High.** U+200A HAIR SPACE is drawn about a pixel wide, so "2" U+200A "10" looks like "210",
   and U+2006, U+2009 and U+202F are barely wider. Fixed: all four are content, not whitespace;
   §6's joiner wording follows.
3. **High.** NFC joins a combining mark after an inline tag to the letter before it, while
   Chrome draws them apart (`&lt;<b>&#x338;</b>` drawn "</", read "≮"). Fixed: a composition
   across inline markup rejects (`combining-across-markup`), tested with the normaliser's own
   steps 1 to 3 over a window on each side.
4. **High.** A soft hyphen or zero-width space inside a number is a break a narrow viewer takes
   ("2-" / "10 mg", or "2" / "10 mg"). Fixed: narrative rejects both (`invisible-character`, a
   second one-sided rule), which withdraws `soft-hyphen-at-boundary`.
5. **Medium.** `]]>` in text, which an XML renderer refuses, drawing nothing. Fixed: `cdata`.
6. **Medium.** Nested `small`, and headings nested in XML mode, shrink text below legibility.
   Fixed: `nesting-depth`.
7. **Medium.** Nested indenting containers push text off a narrow page. Fixed: at most six.
8. **Medium.** §5 claimed raising a letter does not change what it says; `10<sup>n</sup>` reads
   "10n". Stated as a residual.
9. **Medium.** §7 still said an unfetchable picture draws nothing. Fixed to match ADR 0005.
10. to 12. **Low.** ADR 0005's wording on T's exceptions, a fetched picture's media type and size,
    and the root language tag's font change (stated in §5). Fixed.

Every new rule is pinned by vectors on both sides of its limit, and a break of each is caught by
the vectors (the differential catches the frequent ones; the rare ones are masked at some seeds by
an earlier error in the same generated document).

## Fourteenth review (2026-09-23): findings and what changed

The fourteenth review tested the thirteenth's fixes against Chrome and found them incomplete:

1. **High.** The composition rule compared NFC on each side of a tag, but a combining mark that
   NFC does not compose (U+0301 after "q", U+0338 after most letters) is still drawn apart from
   its letter after an inline tag, and read with it. Fixed: the first code point emitted after
   an inline tag must not be of general category M; the NFC window stays for the Hangul jamo,
   which compose without being marks. Its offset is stated to be in the emitted text.
2. **High.** Making U+2009 and U+202F content broke section 6's number rule: "10" U+2009 " 000"
   no longer read as one number, so "10" could be quoted from it; the quote-edge rule in the
   query service and the agent had the same gap. Fixed: a gap (section 3 whitespace, the thin
   spaces, and Default_Ignorable code points, drawn as nothing) is defined once, and the digit
   rule and the quote-edge rule read past every gap.
3. **Medium.** U+205F MEDIUM MATHEMATICAL SPACE is four eighteenths of an em, drawn as narrow as
   a thin space. Fixed: content, with the others; the criterion is stated (narrower than a
   quarter of an em).
4. **Medium.** `code`, `h5` and `h6` shrink text as `small` does, and nested together reach seven
   pixels. Fixed: at most one of the four open at once.
5. **Low.** How a void element counts towards the depth bound was unstated. Fixed: at its own
   start tag.

Each fix is pinned by vectors on both sides, and a break of each is caught by them.

## Fifteenth review (2026-09-23): findings and what changed

1. **High.** A word joiner, byte-order mark or zero-width joiner between an inline tag and a mark
   got past the round-14 mark rule, which tested only the first code point after the tag;
   Chrome draws `q<b>&#x2060;&#x301;</b>` "q ´". Fixed: the rule reads past Default_Ignorable
   code points that are not themselves marks.
2. **High (since 1.0.0).** An underline turns a sign into another: `<u>&lt;</u>` is drawn "≤",
   `&gt;` "≥", `+` "±", `=` "≡", and a link (`a`) is underlined too. Fixed: a mathematical
   symbol (Sm) or a dash (Pd) inside `u` or `a` rejects (`underlined-sign`). A source that
   writes "≤" as an underlined "<" is refused, a false failure.
3. **High.** U+2800 BRAILLE PATTERN BLANK is drawn as a blank as wide as a letter, so "10" U+2800
   " 000" is one number to a reader, yet it was not a gap. Fixed: a gap, in the specification
   and in every consumer.
4. **High (test double).** The agent's test double still listed U+205F as whitespace. Fixed.
5. **Medium.** Narrative of gaps alone (a thin space, U+2063, U+2800) was verified, and the
   crosswalk counted a thin space as narrative in a mandatory section. Fixed: `empty-narrative`,
   and the crosswalk uses the same test; U+1680, drawn as a stroke, is still drawn.
6. **Medium.** U+205F, `h5` in the shrink bound, spacing and enclosing marks, and the
   supplementary-plane ignorable code points had no vector. Fixed: each is pinned, and a break of
   each fails a vector.
7. **Medium.** A quote of gaps alone was accepted and could match between the groups of a
   number. Fixed: `invalid-request`, in the service and the test double.
8. **Medium.** The query service's design document did not describe the gap reading. Fixed.
9. to 11. **Low.** Default_Ignorable code points are "said not to be drawn" (a few fonts draw
   some); the zone-a README's description of the vectors; a note that the agent's chunk cut
   reads further than the service, which only refuses more. Fixed.

## Sixteenth review (2026-09-23): findings and what changed

1. **High.** Round 15's `underlined-sign` refused mathematical symbols and dashes under `u` and
   `a`, but a look-alike of another category passes: U+02C2 (a modifier letter) underlined is
   drawn exactly "≤", Canadian syllabics U+1438 and U+1433 "≤" and "≥", and underlined letters
   draw ordinal indicators (`1<u>a</u>` "1ª"). No closed list of code points bounds what an
   underline changes. Fixed: `u` and `a` are refused (`unknown-element`), `href` goes with `a`,
   and `underlined-sign` is withdrawn; item 2 of the fifteenth review is superseded. ADR 0005
   now requires T to unwrap a link or a `u` only when its text is on a closed allowlist PR 3
   sets against the renderer, since unwrapping `<u>&lt;</u>` would keep "<" and lose the "≤" a
   reader sees.
2. **High.** A sweep of every assigned code point in Chrome found blank glyphs outside the gap
   set: U+FFF9–U+FFFB (drawn as a blank in every face) and seven Mongolian and Yi letters the
   default serif face lacks (U+1878, U+18AA, U+A4A2, U+A4A3, U+A4B4, U+A4C1, U+A4C5). "Take 10"
   could be quoted from "Take 10 " U+FFF9 "000", and a narrative of them alone was drawn text.
   Fixed: U+FFF9–U+FFFB are refused in section 2; the seven letters are gaps, in every
   consumer; the method is recorded in `scripts/fidelity/blank-glyph-sweep.md`.
3. **High.** Whether the underline rule tested a sign before or after `sup`/`sub` folding was
   ambiguous. Moot with item 1.
4. **Medium.** No vector pinned that an ignorable code point that is itself a mark (U+034F,
   U+FE0F) is not skipped by the mark rule, and none pinned the precedence of the underline
   rule. Fixed: vectors for the first; the second is moot.
5. **Low.** The spec said a renderer underlines `a`; it does so only with a target. Moot.
6. **Low.** The prepended concatenation marks (U+0600–U+0605, U+06DD, U+070F, U+0890, U+0891,
   U+08E2, U+110BD, U+110CD) draw across the digits after them (U+070F puts a bar over "000").
   Fixed: refused in section 2.

## Seventeenth review (2026-09-23): findings and what changed

1. **High.** A renderer draws a row's cells side by side with a gap about a space wide, so
   "10" | "000 IU" is drawn "10 000 IU" (pixel-identical to the paragraph), "<" | "5 mg" reads
   "< 5 mg", and so does a number with an empty cell between or beside a cell spanning rows; the
   quote-edge rule matched either half. Fixed in the service and the agent: a quote beginning or
   ending at a cell's edge is held to the digit and sign rules against the nearest cell with
   text on that side, the grid rebuilt from the markers through spans, in every row its cell
   covers. §6's span rules do not rebuild the grid; for a structured source a span covers the
   whole page, which §6 now says. The agent's answer splitter reads only the text after its last
   cut, so it cannot see a table's grid; today no chunk carrying a grid marker is ever sent (the
   service refuses one), and quoting a table is the publishing step's work (PR 5), which must
   give the splitter the whole table.
2. **High.** An `hr` in a cell or caption is as narrow as its column, so `<td>1<hr/>2</td>` is
   drawn as the fraction ½ while the text says "1 2". Fixed: `table-content`; the one older
   vector with an `hr` in a cell drops it.
3. **Medium.** ADR 0005's underline requirement covered `u` and links but not CSS underlines,
   and did not say that adjacency is judged on the drawn text (`1<u>a</u>` "1ª"). Fixed.
4. **Medium.** The ePI reader read an underlined "<" as "<". Fixed: both readers mark
   underlines; the QRD registry accepts them only over text an underline cannot change.
5. **Medium.** §5's `empty-narrative` wording and the query design omitted the seven blank
   letters. Fixed: both refer to the gaps of §6.
6. to 10. **Low.** A stale mutation row; stated lists with unpinned entries (now enumerated on
   every side), and no page-side vector for a 3.0.0 §2 code point (added); `find_product` made
   unavailable by one refused stored name (now a non-match); stale comments in the Python port
   and ADR 0003; the sweep's zero-width criterion, and the platform the renderer claims were
   checked on (Chrome on macOS; stated in §5).

## Eighteenth review (2026-09-23): findings and what changed

1. **High.** A renderer centres a cell's lines, so any line of a multi-line cell can sit level
   with a line of the next cell: "Up to 10" | "once" / "000 IU" / "weekly" draws "Up to 10 000
   IU", and "if" / "CrCl <" / "then" | "30 ml/min" draws "CrCl < 30 ml/min". The round-17 rule
   read only the nearest cell's edge. Fixed: a quote at a word boundary inside a cell is held to
   the digit and sign rules against every word of every cell on its side, in each row its cell
   covers. The text cannot say which line a word is on, so this refuses more than a reader
   would (a number beside another cell's number cannot be quoted up to the cell edge), a false
   failure the query design states.
2. **High.** The sign rule read only the code point before the space, so "CrCl <" U+2063 " 30
   ml/min" (drawn exactly as "CrCl < 30 ml/min") and "CrCl < (30 ml/min)" let "30 ml/min"
   match. Fixed: the sign is read past every gap, and after an opening bracket too.
3. **Medium.** The round-17 grid was rebuilt per occurrence: a 50 000-slot table took minutes,
   inside a synchronous call. Fixed: once per search, with a timing test (tens of
   milliseconds).
4. **Medium.** No vector pinned the `hr` rule's place after the parent check. Fixed: two
   vectors, and a mutation row.
5. **Medium.** The QRD check ignored underline marks; the ePI reader missed a border on inline
   text (drawn as an underline); the registry judged an underline only inside its text. Fixed:
   one rule (`zone_a.underline`, ADR 0005's allowlist, judged on the drawn text) used by the
   registry and the check, which now reports an underline over what it can change; the reader
   marks inline borders; both readers' versions moved to 1.1.0. The check finds three such
   underlines in Brukinsa (">1", ">5", ">2", drawn "≥") and one in Jentadueto.
6. **Medium.** Two rows of the changed-vectors table were stale. Fixed.
7. and 8. **Low.** Rows, captions and tables are separate lines to the quote rule, now stated;
   "nearest cell with text" wording, the adjacent-number refusal and `find_product`'s handling
   of a refused stored name are stated in the query design.

## Nineteenth review (2026-09-23): findings and what changed

1. **High.** The sign rule read past gaps but not past an opening bracket with a space inside
   it: "CrCl < ( 30 ml/min )", "ClCr ≥ « 30 ml/min »" (French spacing), and a cell ending
   "<(" before "30 ml/min)". Fixed: the sign is read past gaps and opening punctuation
   together, in plain text and in a cell's words; the digit rule still reads past gaps only
   ("10 (000" is not a number).
2. **High.** The sign list missed look-alikes a renderer draws as a comparator (U+02C2 exactly
   as "<", which §5 itself names, the small and fullwidth forms) and the negated and combined
   comparators ("<" with U+0338 normalises to "≮"). Fixed: added, and listed by name in the
   query design; a look-alike from another script (a letter) is a stated residual.
3. **Medium.** The query design understated the table rule's cost. Measured on the three
   pinned SmPCs: 185 of 789 whole-cell quotes are refused by it alone, 40 starting with a
   word. Stated in the query design and UR-22; quoting a table with its structure is PR 5's.
4. **Medium.** The change record and UR-22 described the round-17 rule. Fixed.
5. **Medium.** Three branches of the rule had no example (the cross-cell sign for a quote
   beginning with a word, the cell check after an opening bracket, a spanning cell's later
   rows); each now has one, and breaking each fails a test.
6. **Medium.** `underline_changes` missed Cyrillic and Greek look-alikes of an ordinal "a" or
   "o", a digit behind a code point drawn as nothing, and an underlined "o" after "N" ("Nº").
   Fixed, with tests; an underlined "o" before "C" after a number ("20ºC") counts too.
7. to 11. **Low.** Side borders are their own mark, `border`, reported by the check; the
   checker's version is `qrd-check/1.1.0`; the changed-vectors table gains the two thin-space
   vectors whose narrative changed; text beside a picture still matches (stated, PR 5); the
   check's comment on pictures under an underline is corrected.

## Twentieth review (2026-09-24): findings and what changed

1. **High.** A sign followed by a combining mark NFC leaves apart ("<" U+0332, drawn "≤") and
   the negated forms outside the list (≉, ≁, ≴) were not signs. Fixed with item 2.
2. **High.** The closed sign list missed real comparators and ASCII spellings ("<=", "×",
   "+/-", "≪", "⋜", "‹"). Fixed: a sign is any mathematical symbol (category Sm) or a named
   look-alike of a comparator, read past gaps, combining marks and opening punctuation, with the
   whole run of symbols before the space ("+/-" through its "+"); an opening mark that is itself
   a sign ("‹30") joins the quote.
3. **High.** The number rule counted decimal digits only, so "1 ½" and "10" | "₀₀₀" let a half
   be quoted. Fixed: any code point of category N.
4. **Medium.** The ePI reader skipped a border declaration holding any zero or none token, so
   `border-width: 0 0 1px 0` under a ">" was missed. Fixed: the shorthands are expanded per side
   and cascaded as a browser does; a bottom border is an underline, another side a border.
5. **Medium.** `underline_changes` missed ordinal look-alikes (small capital O, Greek alpha, a
   digit of another script) and "N" look-alikes. Fixed: any underlined lower-case letter after a
   number of any script, read past code points drawn as nothing but not past a space (an
   underlined "mg" after "10 " is a unit), and Greek and fullwidth "N".
6. **Medium.** Stated sub-rules without a test. Fixed: each has one.
7. **Low.** Reading back through a long run of brackets and spaces was quadratic. Fixed: one
   pass per search in the service; in the agent a bounded walk falling back to that pass, and a
   bounded walk that counts as a cut in the answer splitter (the safe side).
8. **Low.** Wording (the check's design, ADR 0005, the module docstring, "10 (000"), the
   parity test reading comments as entries, a border over a sign not reported, and the 192 of
   789 figure now reproducible (`agent/scripts/measure_table_quotes.py`).

## Twenty-first review (2026-09-24): findings and what changed

1. **High.** The agent's double read a long cell word with the splitter's bounded walk, which
   counts an over-long walk as a sign, so it refused a quote the service matched. Fixed: a cell
   word is read whole and exactly.
2. **High.** The design said the sign is read past "opening punctuation"; the code skipped a
   fixed list, so "CrCl < （ 30 ml/min ）" matched. Fixed by item 3: an opening mark outside
   the list is now a sign itself.
3. **High.** Look-alikes outside category Sm and outside the named list ("❮", "⧼", "⟪", "➕",
   "˖", "⁓", a middle dot, a spaced slash, "<" with U+02CD) were not signs. Fixed by inverting
   the rule: a sign is anything but a letter (modifier letters are signs), a number, a gap, a
   combining mark, an opening mark, a scanner marker, a dash or plain punctuation. Reference
   marks (`* † ‡ § ¶ #`) are plain; postfix signs (`% ‰ ‱ ° ′ ″ ℃ ℉`) count only after a number;
   and a quote ending in a number before a space and a sign is cut ("100" of "100 × 10⁹/l",
   "30" of "30 %"), which the right edge had not read. Measured on the pinned SmPCs the table
   figure is 196 of 789 (it would be 295 with "%" and the reference marks read as signs before a
   quote, which is why they are not).
4. **Medium.** The ePI reader applied a later declaration over an `!important` one, read an
   unparseable border value as none, and ignored `border-image`. Fixed: declarations are
   applied normal first, then important; a border value that is not whole (a function, the
   wrong number of values, a token that is no width, style or colour) refuses the section; a
   border image is drawn on every side.
5. **Medium.** The check skipped faint or struck text over a sign. Fixed.
6. **Medium.** Reading past combining marks and the "‹30" join had no service test. Fixed.
7. to 10. **Low.** A sign after the quote (now a cut after a number, item 3); "nothing
   precedes" stated; a bottom border on a block or cell stated as a residual of the check;
   the script's wording aligned ("beginning with a letter").

## Twenty-second review (2026-09-24): findings and what changed

The review found no disagreement between the service, the agent and an independent oracle over
every code point and 167 000 fuzzed quotes, and measured the inverted rule on the pinned SmPCs'
paragraphs: it refuses 4 of 846 paragraphs, 4 of 1 332 sentences and 4 of 2 446 clauses, as before
(adjacent numbers across a paragraph break), and 20 more of 20 186 five-word windows, each a
number before a sign, as intended.

1. **High.** The right edge read the code point after the space past gaps only, while the left
   reads past combining marks and opening marks too: "Up to 10" of "10 " U+0332 "000 IU" and
   "100" of "100 (× 10⁹/l)" matched. Fixed: both edges, the cells' first code points and the
   agent's answer splitter read past the same.
2. **Medium.** No test isolated a cell that starts with a sign. Fixed.
3. **Medium.** The reader accepted border values a browser drops (a five- or seven-digit colour,
   `none auto`, a keyword among other values) and read `inherit` as none. Fixed: colours and
   CSS-wide keywords are read as a browser reads them, and `inherit` refuses.
4. **Medium.** Shading over a sign was not reported. Fixed.
5. **Medium.** The reader accepted any font, a symbol font included. Fixed: a closed list of
   Unicode text fonts, as ADR 0005 states.
6. to 9. **Low.** Wording ("‹" is a sign, the scanner markers and modifier letters in UR-22),
   the bit constants' comment, pins for the rules that only loosen, and residuals (letters drawn
   like digits, « and », dashes drawn like signs, a spaced ratio colon).

## Twenty-third review (2026-09-24): findings and what changed

1. **High.** A combining mark between a number and the space ("Give 10" U+0332 " 000 IU",
   drawn "10 000" with the last 0 underlined) hid the number before the space from the digit
   rule: round 22 read the far side past marks, not the near side. Fixed: a number is read past
   gaps and combining marks on both sides, in text, in cell words and in the agent's splitter.
2. **Medium.** Round 22 read numbers past opening marks too, which the query design did not
   say and which refused "0.52" before "(95% CI", a pack line "(28 × 1 …)" after a procedure
   number, and 108 more five-word windows on the pinned SmPCs (round 22's note said 20; that
   figure was measured before its fix). Fixed: numbers are read past gaps and marks only; signs
   still past opening marks. The windows are back to 220 of 20 186 and the table figure to 196
   of 789; paragraphs, sentences and clauses are unchanged.
3. **Medium.** The ePI reader read layout that draws one text over another as plain text. Fixed:
   a negative margin on inline text or at a block's top or bottom, padding on inline text over a
   background, a height outside table parts and pictures, and a line height below normal refuse
   the section. The pinned labels refuse nothing more (their heights are on table rows, their
   line heights 12.65 pt or 107% and up, their negative margins at a paragraph's side).
4. to 6. **Low.** Stale comments and docstrings; the right-edge wording in the query design and
   UR-22 aligned; pins for "‰" and "…" as not signs before a quote.

## Twenty-fourth review (2026-09-24): findings and what changed

The review found the quote-edge rule clean: the service, the agent's port and its test double
and an oracle rebuilt from the query design agree over every code point (51 probes) and 175 086
fuzzed quotes, and the stated costs hold (4 paragraphs, 4 sentences, 4 clauses, 220 of 20 186
five-word windows, 196 of 789 whole-cell quotes). Its findings were in the ePI reader's layout
bounds and in missing pins:

1. to 4. **Medium.** Layout the round-23 refusals missed: a large font under a percentage line
   height (a 40pt run under a 115% line hides the line above), padding in a unit the reader
   cannot place (read as zero), vertical padding lifting an inline border over the line above
   ("<" underlined, "≤"), and a negative margin on a picture. Fixed: a line height of 12pt, 100%
   or 1em at least, a font of 14pt at most (the pinned labels set 12pt at most and 12.65pt or
   107% at least), an unplaceable padding counted as non-zero, vertical padding
   on inline text refused, a picture's margins read as inline text's.
2. **Medium.** A block overflowing its table cell (a negative side margin, a narrow width)
   overprints the next cell; Nuvaxovid sets negative side margins inside cells, so it cannot be
   refused outright. Stated as a residual of the check, with the rest of what bounds cannot
   catch: the QRD check's design says the bounds are not a layout engine, and ADR 0005's
   renderer cross-check is what secures the import.
3. and 7. **Medium, Low.** Pins: a cell's first number read past a combining mark (a mutant
   survived), and not past an opening mark; the number before the space not past one either.
4. to 10. **Low.** The splitter's docstring (an answer's own ends are not cuts), a combining
   mark on a space stated as a residual, and wording (numbers and opening marks, a stale
   comment, the QRD design's "layout properties are ignored", a round-22 sentence).

## Twenty-fifth review (2026-09-24): findings and what changed

The review found the quote-edge rule still clean (112 720 fresh quotes and every code point, the
service, the agent's port and the oracle identical) and the reader's bounds short again:

1. **High.** A border on inline text wider than a hairline paints a band over the lines around it
   (an empty span with a 24pt white top border blanks the line above). Fixed: refused.
2. **Medium.** A percentage or em line height is computed from the font of the element that sets
   it and inherited as a length, so a 2pt paragraph's 100% line holds 12pt spans 2pt apart. Not
   fixed by another bound: the reader does not lay the page out, and each round has found
   another way CSS draws one text over another. Its claim now says so: the reader is exact for
   text and marks, refuses the layout cases it lists, and states the rest (this one, a block
   overflowing its table cell, a margin drawing a list item over its number, text at the bounds'
   edge) as a residual of the check; ADR 0005's renderer cross-check draws each page and
   compares, and is what secures the import.
3. **Medium.** A regression: "1rem" and "x%" broke the reading with an uncaught error instead of
   refusing the section. Fixed, with tests.
4. **Medium.** "12.65pt or 115% at least" was wrong (Brukinsa sets 107%). Fixed.
5. to 8. **Low.** Wording (the font bound's units, the refusal list, this note's numbering), the
   splitter's docstring, the list-number overprint named in the residual, and a splitter mutant
   no test can kill (equivalent).

## Twenty-sixth review (2026-09-24): findings and what changed

The review found the quote-edge rule clean again (272 728 quotes and every code point), the
scanner's two ports agreeing on 22 000 fuzzed divs, and the reader crash-free over 360 000 fuzzed
style attributes. Its findings were in the reader:

1. **High.** The reader bounded each margin or indent to an inch, but nested margins and an
   inherited indent add up: a doctored Jentadueto 4.7 drew "has no or negligible" off the page.
   Fixed: the blocks' left margins and the inherited first-line indent are summed down the walk
   (an em counted at 14pt; a table cell starting again), and text more than 12pt left of its
   container's start refuses; the pinned labels never go below 0, or -9pt in a cell.
2. **High (a crash; fails closed).** Deep nesting raised a recursion error. Fixed: elements
   nested deeper than 128 refuse the section, and a Bundle nested too deeply to read refuses the
   document.
3. and 4. **Low.** The documents now point to the reader's docstring as the one list of bounds
   and residuals, which names text moved far to the right, off a printed page.

## Twenty-seventh review (2026-09-24): findings and what changed

The review found the quote-edge rule and the fidelity contract clean again (86 649 quotes; the
scanner's ports identical over 12 000 divs) and the round-26 offset sum short of the cascade:

1. **High.** The sum took the last declaration, so an `!important` pull to the left, or a later
   value a browser drops as invalid, hid it. Fixed: each block's margin and indent is read as
   the most negative value any of its declarations names.
2. **High.** An indent on a table row or row group reaches its cells in CSS, and the reader
   reset it at the cell. Fixed: carried into the cell.
3. **High.** An indent inherited through an inline element was dropped. Fixed: applied.
4. **High (a crash, fails closed, older).** A Bundle of the wrong JSON shape raised an error.
   Fixed: refused as `invalid-bundle`.
5. to 9. **Low.** Chained tables each pulling left now add up; an em is 14pt in both bounds;
   the docstring lists every refusal (the root, content in `br` or `img`, the document's own)
   and the bottom border on a block among the residuals, and counts a table's row group and row
   toward the nesting bound; a test pins the reading refused as nested too deeply to read; the
   change record's TypeScript timing is stated as measured.

With the fix, a differential of 1 800 random nested cases against Chrome gave no false pass.

## Twenty-eighth review (2026-09-24): findings and what changed

The review found the quote-edge rule and the fidelity contract clean (fresh fuzz, 0 differences)
and the reader-against-Chrome offset differential at 0 false passes over 2 400 cases; its findings,
found by hand, were in the reader:

1. **High.** A positive margin in em was credited at 14pt although the element's font may be far
   smaller. Fixed: a positive em counts nothing.
2. **High.** Values a browser drops as invalid still counted (`margin-left: 72pt 72pt`,
   `text-indent: auto`). Fixed: refused.
3. **High.** The style was split on ";" alone, so a quote, a comment, an escape or a bracket let
   the reader read a declaration the browser absorbs or drops (a border kept under "<", drawn
   "≤"). Fixed: a style outside plain ASCII punctuation, with a comment, a bracket outside
   `rgb()` or a quote outside a font family name refuses; the pinned labels use none of these.
4. **High.** The reader follows the XML tree where an HTML parser rebuilds it (a block in an
   open `p`, an `li` in an `li`, an `a` in an `a`, content in `hr`), so moved text kept an offset
   it no longer has. Fixed: refused as `malformed-xhtml`.
5. **High (a crash).** A section code that is a list or an object crashed the check. Fixed:
   refused, and so is a div that is not a string.
6. **Low.** The docstring's border refusal is on inline text; said so.

The fix was prototyped by the reviewer and checked to leave the committed QRD checks unchanged.
The pairwise structured-source test in `test/fidelity.test.ts`, which verifies every pair of
accepted vectors, was given a 60 s timeout of its own: under machine load it passed 5 s.

## Twenty-ninth review (2026-09-24): findings and what changed

The fidelity contract and the quote-edge rule were clean for the sixth round running. Every
finding was in the ePI reader and shared one cause: the reader parses the EMA's div as XML, a
browser as HTML. A processing instruction or an abrupt comment hid text the browser shows; a
self-closing `span` or `sup` stayed open over what followed; a namespace-prefixed `div` was
credited as a block; an unclosed `rgb(` swallowed the declarations after it; a row group's style
was dropped from its cells' marks. Each is fixed as the reviewer prototyped and checked (the
committed QRD checks unchanged), with tests, together with a table part outside a table,
`</br>`, a reference to U+0080–U+009F, and the round-28 sub-rules no test pinned.

Most of these predate this change: they are the merged QRD reader's, not regressions of
3.0.0. A closed list of refusals cannot finish them, so the reader's claim now states it: where
XML and HTML parsing differ, it refuses the cases it lists, and the rest is a stated residual;
reading with an HTML5 parser, as a browser does, is a tracked follow-up (roadmap item 3a). The
reviews of this change continue on its own scope: the fidelity contract, the quote-edge rule,
and what this change did to the reader.

## Thirtieth review (2026-09-24): findings and what changed

Scoped to this change (the fidelity contract, the quote-edge rule, and what this change did to
the reader), the review found no false pass, divergence or crash:

1. **Medium.** The reader's refusal of a reference to U+0080–U+009F missed a zero-padded decimal
   reference (`&#0150;`). Fixed, with tests for the decimal and hexadecimal forms.
2. **Medium.** Several stated reader rules had no test of their own (a well-formed `</br>`,
   horizontal padding over a background, the nesting bound itself and a table's row group and
   row counted toward it, a row's own indent, the bounds at their edge). Pinned; a mutant of
   each now fails.
3. and 4. **Low.** Wording (self-closing elements other than `br`, `hr` and `img`; ASCII
   letters), and a literal C1 control named among the reader's residuals.

## Thirty-first review (2026-09-24): findings and what changed

Scoped to this change, the review found no false pass, divergence or crash in the fidelity
contract or the quote-edge rule (fresh fuzz and differential, 0 differences):

1. **Medium.** The reader refused a `br`, `img` or `hr` with content only when the content held
   something other than whitespace, U+00A0 included, which an HTML parser keeps as text. Fixed:
   any content refuses.
2. **Medium.** Stated reader rules without a test of their own (a style limited to ASCII, a
   negative bottom margin, vertical padding at the bottom, a border with two colours). Pinned,
   with the hairline and line-height bounds at their edge, a zero-padded hexadecimal C1
   reference, a root that is not a div and a bare "<" before a digit.
3. **Medium.** The reader's residual wording for a literal C1 control was wrong: HTML remaps a
   character reference, not a literal one, which stays itself and is drawn as a blank or a box.
   Fixed, and the same rationale in section 2 of the specification (the rule is unchanged).
4. and 5. **Low.** "Maps through windows-1252" holds for all but five of U+0080–U+009F; a lone
   surrogate in a div, which raised an error, now refuses the section.

## Thirty-second review (2026-09-24): findings and what changed

Scoped to this change, the review found no false pass, divergence or crash, and no refusal of a
pinned label:

1. **Medium.** An `img` holding a child element had no test (a mutant reading past the child
   survived). Pinned.
2. to 5. **Low.** An `hr` holding a space pinned; each bound now tested just past its edge, with
   the bound itself accepted (round 31 had tested near the edge, not at it); the section 2
   rationale reworded (all but five C1 references are remapped; a renderer shows something the
   check does not see); a lone surrogate anywhere in a Bundle, which a UTF-8 writer cannot write,
   refuses the document.
