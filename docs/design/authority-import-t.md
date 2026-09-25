# T: the authority import's lexical transform (roadmap 3a, PR 3b)

- Status: accepted, 2026-09-24, after fourteen independent reviews
- Decides: ADR 0005 decision 1's "Requirements on T", against the EMA's published Imatinib Teva
  film-coated tablets SmPC; the importer's version 2.0.0
- Amends: ADR 0003 (T5's plus sign in an underlined heading, a stated exception to "false passes
  are not", beside its existing stated residuals); ADR 0005 decision 1 (its statement that T drops
  only what cannot change the drawn page, and its underline requirement, both for that exception;
  font sizes in `em` and `%` and at least 5 pt, where it said absolute units and 2 pt; offsets;
  line height; borders at most 3 pt at 3:1 contrast, where it said thin and dark; the renderer
  cross-check, in HTML and XML mode, made a gate on every import but a synthetic one, PR 3c);
  `docs/design/authority-import-contract.md` (D1: T's lists in PR 3b, the renderer gate in PR 3c,
  and D1's trust statement pointing at the gate; D6's "the renderer cross-check draws the div
  without the authority's viewer" stays true of the gate; D10: the stated check order gains
  `rendering` after `record`, and the gate's evidence is its own versioned store, not importer
  data, so a new publication does not change the importer's version)
- Related: ADR 0003, `docs/design/authority-import-contract.md` (D1, D4, D6, D10),
  `docs/fidelity-normalization.md` (`fidelity-norm/3.1.0`, sections 5 and 7),
  `zone-a/src/zone_a/underline.py`

## What this is for

ADR 0005 lets an authority's published ePI enter the record as clean XHTML: the same words,
paragraphs, lists, tables and raised and lowered text, without the presentation. The page the
fidelity check compares is the scanner's text of T(div), where T is a stated lexical transform of
the authority's div string. PR 2 shipped T with empty lists, so every real section refuses. This
change fills T's lists.

Two reviews of this design showed where a static rule stops. T can decide from the markup alone
what the text is (which elements, which characters, raised or lowered, underlined or not) and
refuse presentation that can hide or alter it in any layout (white text, symbol fonts, a text
pulled out of its cell). It cannot decide from the markup alone whether two glyphs drawn at a
given width, in a given font, touch: a raised digit against a comma in the line above, a
right-aligned number against the next cell. So:

- **T's rules are static and sound on their own terms**: every rule holds for every input, and
  where the markup alone cannot settle a question T refuses (ADR 0003).
- **Geometry is measured, per publication.** An import is accepted only for a publication whose
  exact bytes a pinned browser has drawn, with T's output, and found free of overlapping,
  hidden or displaced text: a renderer evidence record (PR 3c), in its own versioned store, bound to
  the document's and T's output's hashes and to the gate's own version, as D6 already requires
  evidence for pictures. T's geometric rules (T3b, T3c, T4's bounds) are the bounds
  within which that measurement is made, not a proof on their own.

After this change the Imatinib Teva film-coated tablets SmPC (`e107e26d-…`, the owner's choice of
2026-09-24) passes T in every section but 5.1. The import as a whole still stops at the pictures
stage, which comes before T; PR 3c brings pictures and the renderer gate. Imports stay dry-run
until PR 5.

## The label T is built against

The tablets SmPC is pinned in `labels/ema-epi/` as the capsules' SmPC is (bytes, hash, List,
QRD check result; D14). Its 32 sections' divs hold, besides text and character references:

| What                                | Count | Examples                                                                   |
| ----------------------------------- | ----: | -------------------------------------------------------------------------- |
| elements                            |       | `p` 1170, `span` 986, `td` 433, `sup` 172, `tr` 173, `strong` 74, `u` 65,  |
|                                     |       | `em` 41, `li` 28, `sub` 18, `table` 12, `tbody` 12, `ol` 6, `ul` 2, `a` 2, |
|                                     |       | `img` 2, `br` 1                                                            |
| attributes to delete                |       | `style` 2688, `class` 41 (Word's `MsoNormal…`), `valign` 433, `border`,    |
|                                     |       | `cellspacing`, `cellpadding` 12 each, `lang="EN-GB"` 1, `href` 2           |
| CSS properties                      |    32 | `font-size`, `font-family` (only `'Times New Roman', serif`), `margin`,    |
|                                     |       | `line-height`, `color`, `width`, `padding`, borders, `text-align`, …       |
| raised or lowered runs (`position`) |    36 | 18 at `top: .5pt` in 11 pt text; 15 at `-5.0pt` in 7 pt; `-5pt` in 11 pt   |
| underlining elements                |    67 | 65 `u`, 2 `a` with `href` (one also `text-decoration: underline`)          |
| coloured text, shading              |     6 | green 5.1 ×3, a blue link, `background: lightgrey` ×2 (the reporting box)  |
| blocks with a negative offset       |    54 | hanging indents; right margins of −0.05 to −1.15 pt; left −1 pt, −14.6 pt  |

## Decisions

### T1. T edits tags only, over a stated model of the drawn page

T walks the div with the fidelity scanner's own tokeniser grammar (tags, attributes, character
references, text; `src/fidelity/xhtml.ts`). It never touches text or character references. For
each tag it does exactly one of: keep it; keep it with attributes deleted; delete it (unwrapping:
the matching end tag, found by the element stack the scanner's nesting rules make unambiguous, is
deleted too, and the content stays); or rename it (`span` to `sup` or `sub`, start and end tag).
Block tags (`p`, `div`, `li`, `ol`, `ul`, `dl`, `dt`, `dd`, `blockquote`, headings, the table
parts, `br`, `hr`) are never deleted, renamed or added, so every line break and paragraph of the
div is in T(div): ADR 0005's line check, held by construction and asserted (the sequence of block
tags in T(div) equals the div's). Anything not on a list below refuses the section with a closed
reason (T6); the scanner then reads T(div), and a scanner refusal refuses the section.

**One tree.** A browser builds the HTML parser's tree, not the markup's nesting, and T deletes
`span`, `u` and `a` before the scanner reads anything, so the scanner's structural rules never see
where those stood. Where the two trees differ, T's model would judge text in a style it is not
drawn in (`<p style="font-size:11pt">Take <dd>2 tablets</dd>daily</p>` in a 1 pt cell: Chrome
closes the `p` at `dd` and draws "2 tablets daily" at 1 pt). So T holds the **authority's** div,
every tag, deleted or kept, to rules under which the two trees agree, and refuses (`markup`):

- a start tag of any block element (T1's list but `br`, which does not close a `p`, and every other
  element HTML's "close a p element" applies to, `li`, `dd` and `dt` included) while a `p` is open with no `table`, `td`, `th` or
  `caption` between (HTML's button scope);
- a `dd` or `dt` while a `dd` or `dt` is open, and an `a` inside an `a`;
- any element, and any text other than ASCII whitespace, directly inside `table`, `thead`,
  `tbody`, `tfoot` or `tr` other than the table parts the scanner allows there, and anything but
  `li` and whitespace directly inside `ol` or `ul` (a `span` there is moved out of the table, its
  style no longer the cells');
- nesting deeper than the scanner's 32 levels (as the scanner refuses it, and so the importer
  never recurses further);
- more than 20 000 elements in a section (ten times the most a pinned label's section holds, 1 965
  in the Imatinib Teva tablets' 5.1), so T's records of them fit the worker's memory;
- a self-closing tag other than `br`, `hr` and `img`, and those three written otherwise (HTML
  ignores the `/`: `<span style="font-size:1pt"/>` wraps everything after it, and `<u/>` is
  rebuilt into every later block);
- an element or attribute name that is not lower-case (XML mode reads names case-sensitively).

Then, with the scanner's own refusals (an `li` or a table part out of place, a heading inside a
heading), the markup's nesting and the HTML tree agree for everything T accepts. None of the pinned
labels has any of these structures. Because six reviews found such differences one at a time,
the renderer gate also compares T's model with Chrome's computed style for every text node
(below), so a difference the list misses fails the gate instead of passing silently.

**The model.** T computes, for every element and text node, the style a browser draws it with in
standards mode from the inline styles alone (the authority's class stylesheet is not applied, ADR
0005; the renderer gate draws the same, PR 3c). Standards mode because the importer's clean div,
and the EMA's viewer page, are drawn in it; quirks mode resets a table's font and line height.

- **Defaults.** The section's `div` starts at 12 pt, black on white, `line-height: normal`, no
  offsets. The browser's own defaults for the elements T accepts are part of the model: `a` with
  `href` is `#0000EE` (`#551A8B` once visited; T judges both) and underlined; `sup` and `sub` are
  `font-size: smaller` and `vertical-align: super` and `sub`; `small` is `smaller`; `h1`–`h6` are
  2, 1.5, 1.17, 1, 0.83 and 0.67 em; `ol` and `ul` have `padding-left: 40px` (30 pt); `blockquote`
  has 40 px left and right margins and `dd` a 40 px left margin; `td` and `th` have 1 px padding
  (`cellpadding` replaces it) and `table` 2 px `border-spacing` (`cellspacing` replaces it) and
  `text-indent: initial` (an indent does not inherit into a table); a `table` `border` attribute
  other than `0` adds 1 px `inset` borders to its cells, which do not count as drawn (T3e). Chrome
  steps `smaller` down by a keyword table (× 0.77 to × 0.9, or ÷ 1.2), so the model bounds it: in
  every comparison a size under `smaller` takes whichever of × 0.75 and × 0.9 makes the comparison
  harder to pass (a floor on a size or on a ratio to it, × 0.75; a ceiling, × 0.9; in T4, shifts
  compared as magnitudes, the deletion bound and the half-of-parent ceiling × 0.75 and the 0.2
  fold floor × 0.9; T3d's marker width, × 0.9). One computed size takes one factor wherever it
  appears in a comparison, and where a comparison involves several sizes under `smaller`, their
  factors are chosen together, one per size, to make it hardest to pass: where both sides derive from the same size (a line height in `em` or a
  number against that text's size, a shift in `em` against it, a size against the same size), the
  factor cancels and the ratio is exact.
- **Cascade.** One element's declarations apply in order, the last one wins; a shorthand
  (`margin`, `padding`, `border`, `border-*`, `background`) sets every longhand it covers, and a
  later longhand overrides it. Property names and keyword values are ASCII case-insensitive.
  `!important`, `inherit`, `initial` (except `border-image: initial`), `unset`, `revert`,
  `currentcolor`, a comment, an escape, `url(`, and `&` in the attribute's decoded value refuse
  (`css-grammar`).
- **Inheritance.** `color`, `font-size`, `font-family`, `font-style`, `font-weight`,
  `line-height` (a number inherits as the number, a length or percentage as its computed length)
  and `text-indent` inherit. An underline drawn by an element is drawn over every descendant,
  whatever the descendant declares.
- **Values are CSS as Chrome parses it.** CSS reads a `(`, `[` or `{` block, a function and a
  quoted string up to its close, a `;` inside included, and drops a bad string with what follows;
  so T's split at `;` is Chrome's only under this grammar, which `css.ts` implements: printable
  ASCII and CSS whitespace (tab, LF, CR, FF) only, and inside quotes a tab but no LF, CR or FF; no `{`, `}`, `<`, `>`, `@`, `!`, `\` or `/*`; quotes only in `font-family`, each family
  either wholly quoted or unquoted, with no quote, `;` or CR, LF or FF inside; brackets only as a
  balanced `rgb(…)` with no `;`, `(` or `[` inside; anything else refuses (`css-grammar`). A declaration Chrome would drop (and let the element inherit) is
  refused (`css-value`), never read: numbers are CSS numbers (`12`, `12.5`, `.5`; not
  `12.`); a box shorthand (`margin`, `padding`) has 1 to 4 lengths; a `border` shorthand or
  `border-<side>` has each of a width, a style and a colour at most once, in any order, and a
  colour it omits is the element's own `color`; `thin`, `medium` and `thick` are 1, 3 and 5 px; a
  quoted family name is a font's name, so `'serif'` is not the generic.
- **Units.** `pt`, `px` (0.75 pt), `pc`, `in`, `cm`, `mm`. `em` and `%` only where a row says so:
  `font-size` (`em`, `%`, of the parent's size), `line-height` (`em`, `%` and a number, of the
  element's own), `top`, `bottom` and a `vertical-align` length (`em`, of the element's own),
  `width` and `height` (`%`); `margin`, `padding`, `text-indent` and border widths take the
  absolute units only; `0` without
  a unit; any other unitless number refuses, except for `line-height`. `cellpadding` and
  `cellspacing` are unsigned integers of pixels.
- **Text nodes.** Every text node, and every list item's marker (T3d), is judged at its computed
  style. A **block** is an element on T1's list of block tags; every other element is inline.

### T2. Attributes and elements: closed lists

| Attribute                                                                             | Where                                                | T                                                                                                                                                                    |
| ------------------------------------------------------------------------------------- | ---------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `xmlns`, root `lang`/`xml:lang`, `colspan`, `rowspan`, `scope`, `ol@type`, `ol@start` | as the scanner allows                                | kept                                                                                                                                                                 |
| `style`                                                                               | any element                                          | read (T3), then deleted                                                                                                                                              |
| `class`                                                                               | any element                                          | deleted (the class stylesheet is not applied: a stated residual)                                                                                                     |
| `id`, `name` (on `a`)                                                                 | any element                                          | deleted                                                                                                                                                              |
| `title`                                                                               | any element but `abbr`                               | deleted; on `abbr` refused (a browser underlines `abbr[title]`)                                                                                                      |
| `lang`, `xml:lang` below the root                                                     | any element                                          | deleted when the primary subtag, case-insensitively, is one of the EU's 24 official languages' codes; otherwise refused                                              |
| `dir`                                                                                 | any element                                          | deleted when `ltr` and the section holds no code point of bidirectional class R, AL or AN (with right-to-left text, `dir` reorders what is drawn); otherwise refused |
| `valign`                                                                              | `td`, `th`, `tr`, `thead`, `tbody`, `tfoot`          | deleted when `top`, `middle`, `bottom` or `baseline`                                                                                                                 |
| `width`, `height`, `nowrap`                                                           | `table`, `tr`, `td`, `th`, `thead`, `tbody`, `tfoot` | deleted                                                                                                                                                              |
| `align`                                                                               | `td`, `th`, `tr`, `p`, `div`, headings               | deleted when `left`, `right`, `center` or `justify`                                                                                                                  |
| `align`                                                                               | `table`                                              | deleted when `center`; `left` and `right` refused (a floated table draws text beside it)                                                                             |
| `border`, `cellspacing`, `cellpadding`, `summary`                                     | `table`                                              | deleted, after the model has read `border`, `cellspacing` and `cellpadding`                                                                                          |
| `href`                                                                                | `a`                                                  | deleted with the `a`, if T5 accepts its underline                                                                                                                    |
| `src`                                                                                 | `img`                                                | the pictures stage's (D6)                                                                                                                                            |
| anything else (`bgcolor`, `background`, `hidden`, `li@value`, `ol@reversed`, …)       | anywhere                                             | refused (`attribute`)                                                                                                                                                |

Elements: every element the scanner accepts is kept but `code` (Chrome draws it in the monospace
generic, at a size the model does not follow), which refuses; three are deleted (unwrapped):
`span` once T3 has read its `style` and nothing else is left on it; `u`; and `a`, each only if T5
accepts every underline it draws. A `span` T4 folds is renamed `sup` or `sub`. Every other
element the scanner refuses (`font`, `center`, `bdo`, `ruby`, `q`, `ins`, `del`, `s`, `strike`,
`big`, …) refuses the section (`element`).

### T3. CSS: a closed list of properties and values

A property not in this table refuses (`css-property`); a value outside its row refuses
(`css-value`).

| Property                                                                                                                                                                                                                                                                                                                                                                                         | Accepted                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `font-family`                                                                                                                                                                                                                                                                                                                                                                                    | a list whose every family is one of Times New Roman, Times, Arial, Helvetica, Calibri, Cambria, `serif` and `sans-serif`, compared ASCII case-insensitively and exactly otherwise (an unquoted name as CSS reads it, its                                                                                                                                                                                                                                                                                                                                            |
| identifiers joined by one space; a quoted name holding a tab or two consecutive spaces refuses), the Unicode text fonts the renderer gate can draw with pinned metric-compatible fonts (Liberation Serif and Sans, Carlito, Caladea); every other family refuses, among them symbol-encoded fonts, `monospace` and Courier (Chrome draws the monospace generic at 13 px where the model says 16) |
| `font-size`                                                                                                                                                                                                                                                                                                                                                                                      | a length, or `em` or `%` of the parent's; every element's and text node's size, UA defaults included, between 5 pt and 24 pt, and an inline element's or text node's at least half the size of its nearest block ancestor (an element with no text of its own still sets its children's raise and T4's ceiling)                                                                                                                                                                                                                                                     |
| `font-style`, `font-weight`                                                                                                                                                                                                                                                                                                                                                                      | `normal`, `italic`, `oblique`, `bold`, `bolder`, `lighter`, 100–900 (CSS emphasis is dropped, not proved, ADR 0005)                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `color`                                                                                                                                                                                                                                                                                                                                                                                          | a colour of T3a's list; contrast (T3a)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `background`, `background-color`                                                                                                                                                                                                                                                                                                                                                                 | a colour of T3a's list only; contrast (T3a); not on or inside an element with a `position` shift (T4: a positioned box is painted over the text around it); on an inline element only with no padding and a line height of `normal` or at least 1.2 times the largest of its own and its descendants' font sizes (T3c)                                                                                                                                                                                                                                              |
| `margin`, `margin-top`, `-right`, `-bottom`, `-left`, `padding` and the same four longhands, `text-indent`                                                                                                                                                                                                                                                                                       | lengths; on an inline element only `0`; on blocks, T3b; on `ol` and `ul`, no `padding-left` (nor `padding`); on `li`, none negative (T3d)                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `line-height`                                                                                                                                                                                                                                                                                                                                                                                    | `normal`, a number, a length, a percentage; T3c                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `text-align`                                                                                                                                                                                                                                                                                                                                                                                     | `left`, `right`, `center`, `justify`, `start`, `end`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `width`, `height` (not `height` on `caption`, which can draw its text over the first row)                                                                                                                                                                                                                                                                                                        | a length or percentage, not negative, on table parts only                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `border`, `border-*`                                                                                                                                                                                                                                                                                                                                                                             | on table parts only, `border` and the physical longhands alone (`border-top`, `-right`, `-bottom`, `-left` and their `-width`, `-style`, `-color`; `border-width`, `-style`, `-color`), never logical ones or `border-radius`: `none`, or `solid`/`double`/`dotted`/`dashed` at most 3 pt wide in a colour of T3a's list (whether it counts as drawn is T3e's: a border of too little contrast is accepted and counted as not drawn); `border-collapse` on `table` only: `collapse`, `separate` (T3e reads the table's own); `border-image`: `none`, `initial`; T3e |
| `position` with `top` or `bottom`                                                                                                                                                                                                                                                                                                                                                                | on an inline element only: `position: relative` and exactly one of `top`, `bottom`, a length or `em`; T4. `top` or `bottom` without it, or `position` on a block, refuses                                                                                                                                                                                                                                                                                                                                                                                           |
| `vertical-align`                                                                                                                                                                                                                                                                                                                                                                                 | on an inline element other than `sup`/`sub`: `baseline`, `super`, `sub`, a length or `em` (T4); on `td`, `th`: `top`, `middle`, `bottom`, `baseline`; anywhere else refused. `position` and `vertical-align` shifts on one element refuse                                                                                                                                                                                                                                                                                                                           |
| `text-decoration`, `text-decoration-line`                                                                                                                                                                                                                                                                                                                                                        | `none`, `underline` (T5); `line-through` and `overline` refuse                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `page-break-*`, `break-*`, `widows`, `orphans`                                                                                                                                                                                                                                                                                                                                                   | any value (paged media only)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `mso-*`, `tab-stops`                                                                                                                                                                                                                                                                                                                                                                             | any value (Word's own properties, which a browser does not implement)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `text-autospace`                                                                                                                                                                                                                                                                                                                                                                                 | `none` only                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |

Every other property refuses, among them `display`, `visibility`, `opacity`, `clip`, `overflow`,
`content`, `text-transform`, `list-style*`, `letter-spacing`, `word-spacing`, `white-space`,
`direction`, `unicode-bidi`, `left`, `right`, `transform`, `float`, `table-layout`,
`font-variant`, and `text-decoration-*` other than `-line`.

**T3a. Colours and contrast.** Accepted colours are `#rgb`, `#rrggbb`, `rgb(r, g, b)` with integer
channels, the 148 CSS named colours with their CSS RGB values, `transparent` for a background
only (it draws nothing), and the system colour `windowtext` (black). `rgba`, `hsl`, `hsla` and
every other system colour refuse. Every text node's and marker's colour (its computed colour;
where no author colour is declared on the path from an `a` with `href` down to the text, both the
browser's link colours stand in for it) must have a contrast of at least 4.5:1 (WCAG 2) against
**every** background in its ancestor chain and against the root's white: a text pulled out of a
painted box (a hanging indent) is drawn on what lies behind it. T3b keeps every text inside its
own table cell (and the scanner refuses a table inside a table, at any depth), so no other cell's
background can lie behind it; the renderer gate also measures each glyph against the pixels drawn
behind it. In the
label: `#231f20` and black on white (16.3:1 and 21:1), green (5.1:1), `blue` (8.6:1, section 10's
link), `#0000EE` and `#551A8B` on white (9.4:1 and 11.0:1) and on `lightgrey` (6.3:1 and 7.4:1), `#231f20` on `lightgrey` (10.9:1).

**T3b. Offsets.** Text is never pulled out of the box that holds it, and never pushed beyond a
bound.

- Every margin, padding and `text-indent` on a block is at most 144 pt, and every margin at least
  −144 pt; vertical margins and all padding are at least 0, and a `caption`'s margins are not
  negative (its box is not pulled out of its table).
- For a block, `S` is the sum of `margin-left` and `padding-left` of every ancestor block and of
  itself, from the section's `div`'s content box (the `div`'s own margins and paddings must not be
  negative) or, inside a table cell, from the cell's content box; `SR` the
  same on the right; `I` its computed (inherited) `text-indent`. Every line starts at `S`
  (continuation lines) and `S + I` (the first line). All of `S`, `S + I` and `SR` are at most
  144 pt.
- **Inside a table cell** (and inside a `caption`, whose box plays the cell's part): `S`, `S + I`
  and `SR` are all at least 0, and a positive `I` is not
  allowed (it can push a word past the cell's width). The text never enters the cell's own
  padding, so the padding T3e counts between two cells is really between their text.
- **Outside every table** (a `table`'s own margins included): `S ≥ 0` and `S + I ≥ 0` (a hanging
  indent passes: `margin-left: 1cm; text-indent: -1cm`), and `SR ≥ −1.2 pt` (the label's right
  overhangs outside tables are −0.05 to −1 pt): nothing of the
  section stands to the right of such a block, and T3a judges its text against the root's white.

**T3c. Line height.** At every text node and marker, the computed line height must be at least
its font size (`normal` passes). The accepted fonts' ink from the top of an accented capital to
the foot of a descender is about 1.1 em (Times New Roman: ascent 0.89, descent 0.22; Arial 0.91 and
0.21), so lines at 1 em can touch only where a descender meets an accent; whether any glyphs of the publication
touch is measured by the renderer gate. This replaces ADR 0005's "at least normal", which would
refuse the label's body text (13 pt lines in 11 pt, 1.18).

**T3d. Lists.** A list item's marker is text a reader reads, and the scanner writes it into the
page (fidelity §5). It is judged as a text node in its `li`'s colour, background, font size and
line height (T3a, T3c and the font-size row). It is drawn in the list's padding, left of the
`li`'s content box, and must stay there:

- the `li`'s margins and paddings are not negative, its computed `text-indent` is 0, and no block
  inside an `li` has a negative margin, padding or computed `text-indent` (the marker stays where
  the padding puts it; text moved left would slide under it);
- the marker's width `W` is bounded from above: for the widest marker in the list (from `start`
  and the item count, in the list's `type`), each digit or a `-` counts 0.65 em, each letter
  or roman numeral 1 em, and the ". " after it 1.25 em (a monospaced font's full stop and space);
  a `ul`'s bullet counts 1 em; all of the `li`'s font size (wider than any glyph of
  the text fonts T accepts); with `S_li` the `li`'s content start as T3b sums it, `S_li − W` is at
  least 0, outside tables and inside a cell, where `S_li` is taken at the `li`'s border box (its
  content start less its own `padding-left`: a browser hangs the marker off the border box). The label's lists are two `ul` in 4.1 (6 items at
  most) and six `ol` of 2 to 4 items in 4.2's cells, at 11 and 12 pt: "4. " is at most 22.8 pt,
  inside its 30 pt.

**T3e. Tables.** Two cells side by side with neither a drawn border nor space between them read
as one: "Dose 1" beside "5 mg" is drawn "Dose 15 mg". So every vertical edge shared by two cells side by side (above and below is the renderer gate's)
must have a drawn border, or the two cells' facing paddings plus the table's `border-spacing`
(0 under `border-collapse: collapse`) must be at least 0.25 em of the largest font size of a text node or marker in the two cells (with T3b, no text enters its cell's padding). A **drawn border** is a cell's (under
`separate`, a row's or row group's border is not drawn and each cell draws its own; under
`collapse`, only the winner of CSS 2.1 §17.6.2.1's conflict resolution is drawn, and which one
wins turns on widths snapped to device pixels, a 0.5 pt and a 1.4 pt border both drawing one
pixel wide at 96 dpi, where the left cell's then wins: so under `collapse` the edge counts as
drawn only if every facing border other than `none` is) whose style is `solid`, `double`, `dotted` or `dashed`, whose
computed width is above 0 (Chrome draws a width under one pixel as one pixel), and whose colour (an omitted one is the colour the cell's text is drawn in: both link colours under a
link with no author colour declared from the link down, a colour declared above the link
included, else the cell's `color`) has a contrast of at least 3:1 with every background painted under that edge (both
adjacent cells', their rows' and row groups', the table's and its ancestors', and the root's
white). The `border-spacing` property is refused; the `cellspacing` attribute sets it. A horizontal line under text (a cell's, row's, row
group's or table's bottom border, the collapsed top border of the cell below, a rule) can read as
an underline of the last line above it ("<" drawn "≤"); which line is last depends on the width,
so the renderer gate judges it (below). Every table of the label outside 5.1 has 5.4 pt paddings
and borders.

### T4. Raised and lowered runs

A **shift** is a `position: relative` `top` (a negative length raises) or `bottom` (a negative
length lowers), or a `vertical-align` length (a positive length raises), `super` or `sub`, on an
inline element other than `sup` and `sub`. Shifts are measured in points. Each shift is judged
against **every text node under its element**, at that node's font size:

- **Deleted.** A shift declaration is deleted where its element holds at least one text node (a
  text node of whitespace alone counts) and every text node under it has an ancestor-summed
  shift below 0.1 of its font size: the text moves by less than a tenth of a glyph, together with
  everything inside the shifted element (a `sup` inside a `span` shifted 0.5 pt stays raised above
  its neighbours). The judgement is per element.
- **Folded.** A `span` is renamed `sup` (raised) or `sub` (lowered) when all hold:
  - its shift is at least 0.2 of the font size of every text node under it, and at most 0.5 of
    its parent's font size;
  - every text node under it is no larger than its parent's font size;
  - no ancestor and no descendant carries a shift;
  - after T it holds only text: 1 to 4 code points other than whitespace (a footnote mark, an
    exponent, "95%"), and whitespace (U+0020, U+00A0);
  - unshifted text (text under no shift, deleted or not, and in no `sup` or `sub`, so it sits on
    the baseline and the run's offset from it is the run's own) that draws ink (a letter, number,
    punctuation or symbol, General_Category L, N, P or S, and not a picture or a code point the
    fidelity layer draws as a gap: a thin space, a blank glyph such as U+2800 BRAILLE PATTERN
    BLANK, a Default_Ignorable code point) stands directly beside it in the same block: the code
    point before its first non-whitespace code point, or after its last, is a non-whitespace code
    point of unshifted text, or one U+0020 or U+00A0 away from one, with no block boundary, `br`
    or cell between (a neighbour on its own line: `10<span>9</span>/l`, `(CI<span>95%</span>)`,
    `<span>1 </span>Haematological`); a U+0020 can break the line there, which the renderer gate
    measures.

  `vertical-align: super` and `sub` fold under the same conditions, the 0.2 bound excepted; their
  shift, wherever T4 uses one (the deletion bound and the ceiling), is Chrome's: the parent's
  font size ÷ 3 plus 1 px for `super`, ÷ 5 plus 1 px for `sub`.

- **Refused** (`baseline-shift`): anything else, among it a shift between 0.1 and 0.2, a shift
  larger than half the parent's font size, a shifted run with no unshifted neighbour on its line
  (a whole line moved up is not an exponent), a shifted element with no text node or with an
  `img` (a picture moved over text), nested shifts that are not all deleted, a shift on or inside
  `sup` or `sub` that is not deleted, a `sup` or `sub` larger than its parent's text, and any
  `position` declared on `sup` or `sub` (a declared `vertical-align` there is T3's `css-value`,
  since it replaces their raise: `<sup style="vertical-align:baseline">` draws on the line).

Whether a folded run's glyphs reach the line above or below depends on the font and on what the
line above holds at the width drawn; the renderer gate measures it on every publication. The
label's shifts: 0.045 (the 18 at `.5pt`, deleted), 0.21 (a 7 pt "95%" lowered 1.5 pt, 0.14 of its
11 pt parent: `sub`), 0.45 (the 11 pt "9" of `10⁹` raised 5 pt: `sup`), 0.64 and 0.71 (7 pt digits
raised 4.5 and 5 pt, 0.41 and 0.45 of their parents: `sup`).

### T5. Underlines

A browser underlines text under `u`, under `a` with `href`, and under `text-decoration: underline`
(or `-line`) on the element or any ancestor. An underline changes what a reader sees over some
code points: `<` underlined is drawn "≤", `+` "±", `=` "≡", and an underlined lower-case letter
after a number reads as an ordinal ("1ª"). T removes an underline only where ADR 0005's allowlist
accepts the drawn text it covers: `zone_a.underline.underline_changes`, ported to TypeScript as
`src/authority/underline.ts` (done: it answers 123 shared cases as the Python does, among them
every underlined run of the pinned tablets SmPC with its neighbours), with a hyphen allowed
between two letters. T narrows that allowance: the allowlist accepts any dash (`\p{Pd}`) between
two letters, and some dashes already look like "=" or "~" (U+2E40, U+2E17, U+30A0, U+1400,
U+301C), drawn "≡" or "≅" when underlined; so an underlined run holding a dash other than U+002D,
U+2010 or U+2011 refuses before the allowlist is asked.

An **underlined run** is a maximal stretch of the code points the scanner emits (character
references decoded, a raw line feed as a space) under an underline in force, ended by a block
boundary, a `br` or a cell boundary; adjacent underlining elements make one run
(`<u>Posology for Ph+ ALL in </u><u>children</u>`), and a run of whitespace alone is a run. Its
neighbours for the ordinal rule are read in the scanner's text of T(div).

**A plus sign in an underlined heading.** In the label every run passes but three, "Posology for
Ph+ ALL in adult patients" and "Posology for Ph+ ALL in children" (4.2's Posology subsection) and
"Clinical studies in Ph+ ALL" (5.1): an underlined `+` is drawn as `±`. The EMA's own product
information PDF draws the same underline under the same `+`, so it cannot say which is meant, and
it is a later edition. The ePI itself can: every one of the three is a paragraph underlined from
its first drawn code point to its last, which is how the QRD template styles an SmPC's
subheadings, and the token `Ph+` appears 32 times in the same document with no underline, first
in 4.1's definition, "Philadelphia chromosome positive acute lymphoblastic leukaemia (Ph+ ALL)".
So T waives an underlined U+002B PLUS SIGN, and nothing else, where all hold:

- the run covers its whole block: the block is the run's nearest block ancestor, a `p` or `h1`–`h6`
  outside every table and list, holding no other block, and every code point it emits, whitespace
  aside, is in the run (the underline is the subheading's style, not the sign's);
- the `+` directly follows at least two letters (T5's letters, `isUnderlineLetter`), and is
  directly followed by U+0020, U+00A0 or the end of the block, with no mark (Mn, Me) or
  default-ignorable code point next to it; the **token** is the maximal sequence of letters before
  it together with the `+` (`Ph+`);
- the same token, in exactly the same code points, preceded by a code point that is not one of
  T5's letters (`isUnderlineLetter`), a mark or a default-ignorable code point, or by the start
  of a block, and followed as above (a `br` is no end of the block), occurs outside every underline run in the
  scanner's text of a section of the same document that T accepts without this rule (the document
  writes the term with a plain `+` where nothing is drawn under it; in the label, 4.1's
  definition);
- the rest of the run passes the allowlist with that `+` replaced by U+0020 (so the ordinal and
  numero rules read the same neighbours, and no other `+` is waived).

The importer applies it in two passes. The first runs T on every section, without the rule, and
does not stop at a refusal; the second runs T again, with the rule, on the sections the first
refused only for `underline`, reading the evidence from the sections the first accepted, whatever
their order. The import's refusal, if any, is the first refused section's in pre-order, with its
first pass's reason unless the second accepted it.

This is an interpretation, not a mechanical reading of the drawn page: an underlined `+` is drawn
as `±`, and T records `+`. ADR 0003 is amended to state it as an exception to "false passes are
not", beside its existing stated residuals (a raised letter against the same letter on the line;
fidelity-norm 3.1.0's lowered half-life), because the document itself settles the meaning: it writes the same term with a plain `+`, the
underline covers the whole subheading as the QRD template styles every subheading, and `Ph±` is no
term. Its residual: an author who underlines a whole paragraph and, in it, a term the same
document elsewhere writes with a plain `+`, to mean `±` at that one place, is read as having
written `+`. No other sign is waived (`<` and `>` stay refused). The rule is mechanical, reads
only the document being imported, and runs in Zone B's recomputation.

### T6. Refusals

The importer gains a last stage, `rendering`, after `record` (`ImportStage` and D10's stated order
gain it), which refuses every publication (`renderer-evidence-missing`) until PR 3c defines the
renderer evidence and the gate that reads it: T's static rules are never the only guarantee of an
import. A synthetic publication (D7's reserved id block) passes it without evidence: it is never
approved content, and PR 2's positive paths (the synthetic vector, the pipeline and gate tests)
keep running end to end. The exemption is keyed on the request's checked `authority` being
`synthetic` (`checkIdentity` refuses the reserved ids on an EMA request).

Within a section T's checks run in this order, and a section's reason is the first refusal: the
checks tied to a tag (T1's one tree, T2, T3's declarations, T3a, T3b, T3c at each text node and
marker), in document order of the div's tags; then T4's shifts, in document order of the shifted
elements; T3d's lists (an `li`'s negative offsets and its indent are T3d's, reason `list`; T3b's
upper bounds, 144 pt on each margin and padding and on `S`, `S + I` and `SR`, still judge an `li`), T3e's tables,
then T5's underline runs, each in document order (an `li`'s offsets and its marker's T3a and T3c
are judged at the `li`'s tag, a background at its element's end tag, and a `sup` or `sub` larger
than its parent, or with a `position`, at its tag, all with the tag-tied checks); and only then the scanner reads T(div)
(`scanner-*`, then PR 2's `invisible-character` on its text). The
waiver's second pass runs on the sections whose first-pass reason is `underline` (T5's "refused
only for `underline`" means this); the `record` check (`section-draws-nothing`) runs per section, in pre-order, after
both passes.

Each refusal names the stage (`narrative`) and a closed reason: `markup`, `element`, `attribute`,
`css-grammar`, `css-property`, `css-value`, `contrast`, `offset`, `line-height`, `font`,
`font-size`, `list`, `table-edge`, `baseline-shift`, `underline`, and PR 2's `scanner-*` and
`invisible-character`. Never narrative (logs, reports, errors).

### T7. Versions and vectors

- `IMPORTER_VERSION` 2.0.0. Golden vectors (`scripts/authority/generate-vectors.ts`) gain a
  synthetic publication for each rule's both sides and each refusal reason, every review's repros
  among the refused, and record each section's T(div) hash as well as the outcome, so T's output
  is locked where the import as a whole refuses; the pinned labels' expected results move
  (below).
- The contract does not change (`CanonicalSubmission` stays 2.0.0).
- `labels/ema-epi/` gains the tablets SmPC, in the lock with its hash.

### What the pinned labels give after this change

| Label                           | Importer                                                   | T, section by section (stage test)                                                                     |
| ------------------------------- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| Imatinib Teva, tablets          | `pictures`: picture reference without template or evidence | every section but 5.1 passes; 5.1 refuses (`offset`, `table-edge`, `baseline-shift` among its reasons) |
| Imatinib Teva, capsules         | `pictures`, as before                                      | the same; 5.1 also refuses on `scanner-table-shape`                                                    |
| Brukinsa, Jentadueto, Nuvaxovid | as before (titles, shape, pictures)                        | not run (they stop earlier)                                                                            |

## What waits for PR 3c

_Settled by `docs/design/authority-import-renderer.md` (the gate: records drawn and attested by a dedicated build, verified by the image build, looked up by Zone B; its thresholds, the frame tolerance among them, provisional until 3c-C's measured design) and `docs/design/authority-import-withheld.md` (5.1 is withheld, an EMA defect measured in Chrome), 2026-09-25. The list below is kept as the requirements those notes answer._

- **The renderer gate** (ADR 0005's renderer cross-check, made a gate). A pinned headless browser
  draws each publication's sections as the authority serves them (inline styles, no class
  stylesheet) and T's output, and an evidence record of the result, bound to the document's hash
  and T's output hash, is what the `rendering` stage requires. CI draws every pinned publication
  and every accepted golden vector of T. PR 3c's design must settle:
  - **what is measured on which drawing**: geometry on the authority's drawing (T's output has no
    styles, so its geometry proves nothing); text, list numbers, grids and pictures compared
    between the two;
    - **which box each check uses**: ink for the overlap and line checks (content areas overlap at
      T3c's 1 em by design; a combining mark's ink, which can stand far outside its zero-width
      advance, is judged by these two checks only, and its code points are in the record, drawn the
      same there); advance boxes for the frame, the gap between cells and the viewport, vertically by line box in
      each; a run shifted by `position` moves its glyphs, not its line box (a `vertical-align`
      shift enlarges the line box), so its vertical reach is judged by the overlap, line and gap
      checks (on the moved boxes), not by the frame; and the
      tolerances;
    - **T's model against the drawing**: for every text node and marker, in HTML and XML mode,
      Chrome's computed `font-size`, `color`, `line-height`, the text decorations in force (read as
      `-webkit-text-decorations-in-effect`, since `text-decoration-line` does not inherit and is
      `none` on a descendant of `u`) and the backgrounds behind it against T's model (a size under `smaller` against the model's range,
      × 0.75 to × 0.9); a difference fails, whatever its cause;
  - **every drawn line as ink**: decoration lines, borders, rules and the edges of inline
    backgrounds; a line that crosses, or comes within a stated tolerance of, any glyph's ink above
    or below it fails (the tolerance below the label's smallest measured clearance: about 0.5 CSS px
    between a descender or an accented capital and a cell's 1 pt border in its commonest cells;
    in 4.8's reporting box the grey background's top edge stands 1.1 px below a descender), except a run's own underline under unshifted glyphs the allowlist accepted
    (and a `+` T5 waived, under its own run's underline): a raised digit merging into the
    underline of the line above reads as another digit, and refusing `overline` already treats a
    line above a glyph as a change;
  - **glyph size** measured, as well as position;
    - **checks beyond overlap**: the gap between cells' text across every shared vertical edge,
      bordered or not (T3e's 0.25 em, measured: side by side, "Dose 1" and "5 mg" read as one run;
      cells above and below each other are left to the overlap and line checks, since the label's
      rows stand about a pixel apart); a marker
      beside a cell; any horizontal line (a border, a rule, an underline, the top or bottom edge of an
      inline background) under text, judged as an underline: the allowlist is asked about the
      stretch of the line of text above it that the line spans (for a cell's bottom border, the
      whole last line), with its neighbours, so the numero rule still reads "Not" from its "N"; but
      a code point counts as underlined only where its own ink lies within 0.3 em, of its own size,
      above the line (`<` over a cell's bottom border reads "≤"; a `>` 0.39 em above one does not),
      and a `+` T5 waived is excepted under its own run's underline; text a
      reader cannot scroll to (left of or above the initial containing block, or clipped;
      overflow to the right, which the page scrolls, is allowed: the label's fixed-width tables
      are wider than a phone); every glyph inside its frame, horizontally by its advance box (its box as
      the browser lays it out, not its ink, which overhangs: the italic "p" of "Hypericum" starts a
      line in 4.4 1.6 px left of it) and vertically by its line box (a line at T3c's 1 em is shorter
      than the font's content area): the frame is the section `div`'s content box outside tables,
      widened on the right by T3b's 1.2 pt, and in a table its cell's or caption's content box, a
      collapsed border counting half inside the cell (CSS 2.1 §17.6.2), with a tolerance of at least
      1/64 px for sub-pixel layout; and inside its cell's content box in a
      table, and its contrast
      against the pixels drawn behind it (ADR 0005's "each text box's visibility"); raised and
      lowered glyphs against T4's folds;
  - **widths** (at least the EMA viewer's content width and a phone's), device pixel ratio and
    zoom;
    - **fonts**: Times New Roman is not on a Linux image; pinned metric-compatible fonts (T3's list),
      hashed in the evidence, and every clearance and tolerance this list states (the 0.5 px line
      clearance above all) measured again in them before it is set; a family the gate cannot draw that way refuses; ink measured against
      the pinned outlines, with tolerances for the originals' stated;
  - **HTML and XML mode**, as ADR 0005 requires;
  - **the evidence record's schema** (browser and version, fonts, widths, T's version, the gate's
    own version, its checks and tolerances, the document's and T(div)'s hashes, the capture's
    hash) and **its store**: its own versioned store, read by hash, so a new publication's
    evidence does not change the importer's version (D10) and force every earlier import to be
    re-imported; **who draws** a publication and **who may write** the store (a CI job from
    reviewed code, never the producer, whom D1 does not trust); the `rendering` stage requiring a
    record of the current gate version; and the record's hash pinned in the submission or run
    manifest, so D1's re-verification re-checks it;
  - the visited link colour stays judged statically (T3a): a drawing cannot see it.
- **Pictures** (D6): evidence from the EMA's own viewer, or templates and fetched bytes.
- **5.1.** Its tables need a browser to judge: positive indents inside cells, right margins of
  −1 pt in cells without padding, cells without padding side by side, and `At-risk : Events` with
  `margin-left: -14.6pt` in the first cell of a table without padding. PR 3c measures the EMA's
  viewer and these cells, and either T gains a measured, stated allowance, or 5.1 is recorded as
  an EMA defect.

## Stated residuals

- The authority's class stylesheet is not applied (ADR 0005). The label's classes are Word's
  (`MsoNormal…`).
- CSS emphasis (bold, italic) is dropped, not proved; element emphasis is kept, not proved
  (ADR 0005).
- T5's plus sign in an underlined heading (above), a stated exception to ADR 0003.
- The narrowed font list refuses the fonts other EMA SmPCs use without a pinned metric-compatible
  substitute: Verdana (Brukinsa, 718 declarations), Segoe UI and "Times New Roman Bold"
  (Nuvaxovid). False failures, recorded; the list widens as the gate gains fonts.
- The renderer gate measures at the widths it draws, in its pinned fonts; a viewer at another
  width, or one that falls back to another font (a reader without Times New Roman), can wrap and
  draw differently.
- The bounds (0.1, 0.2 and 0.5 in T4; 1 em and 1.2 em in T3c; 144 pt and 1.2 pt in T3b; 0.25 em
  in T3e; 0.65, 1 and 1.25 em in T3d; 5 pt and 24 pt; 4.5:1 and 3:1) are argued from a Latin text font's metrics
  and set no wider than the label needs.

## Verification

- Unit tests and golden vectors for each rule's both sides and each reason, among them every repro
  of every review; the importer's lock.
- The pinned labels' stage tests as in the table above.
- `scripts/check-all.sh`; independent adversarial reviews until one finds nothing Medium or higher.

## Reviews

1. **First independent review** (2026-09-24), of the first draft. High: a shift had no upper
   bound and was not read against a neighbour on its line; `vertical-align` on `sup`/`sub`
   replaces their raise; negative margins on inline elements were unbounded; a 0.15 em tolerance
   per cell covers a full stop, and two cells' tolerances add up; positive offsets were unbounded;
   a 0.8 em line height lets an inline background cover the line above. Medium: UA defaults
   (`abbr[title]`, links, `sup`/`small`, list insets) not modelled; line height and font size
   judged per declaration; human "readings" in the import request against ADR 0002; a
   cross-reading with the ePI reader that could not see the sections that matter; the cascade,
   units and keywords unspecified; `table@align` floats a table. Fixed in the second draft.
2. **Second independent review** (2026-09-24), of the second draft. High: text pulled out of a
   painted box was judged against that box's background (white on white); a positioned element's
   background is painted over the line above; a shift on an element with no text (a picture) was
   deleted vacuously; list markers were not modelled (overprinting the next cell, invisible,
   text sliding under them); cells without padding or border merge ("Dose 15 mg"); a cell's
   bottom border under a lone sign reads "≤"; and the product information PDF could not settle the
   underlined `+` (it draws the same underline, its text layer says what the ePI's does, and it is
   a later edition). Medium: the fold's raise bound ignores line height and glyphs (measured, not
   argued: the renderer gate); folding judged at the span's size, not its text's; positive
   offsets bounded per declaration, not per line; ADR amendments missing. Fixed in this draft:
   contrast against every background (T3a), backgrounds refused on positioned elements, shifts
   need text, lists (T3d), tables (T3e), T4 judged per text node, bounds on sums (T3b), T5's rule
   read from the document itself, and the renderer made a gate on every import (PR 3c), which
   takes geometry out of what static rules claim to prove.
3. **Third independent review** (2026-09-24), of the third draft. High: a block inside a cell
   could cancel the cell's padding with a negative margin, so two cells' text still met ("Dose
   15 mg") without overlapping; a border under a sign from a row, row group or table, or under a
   longer cell text, reads as an underline and overlaps nothing. Medium: list markers could still
   be moved over text (a block inside an `li` pulled left, an inherited `text-indent`, an `ol`
   pulled into the next cell), and 0.6 em undercounts a marker; the plus-sign waiver is an
   interpretation that ADR 0003 must name, and its token was loose (one letter, anything after
   it, evidence from refused sections); the renderer gate was not specified enough to carry the
   guarantee, and until it exists T would be the only one. Low: the font-size ratio could be
   bypassed through an empty element; T4's deletion rule was ambiguous; "drawn border" was
   undefined; an inline background's bound used its text's size, not its own; missing amendments.
   Fixed in this draft: no negative offsets inside cells (T3b); borders under text judged by the
   gate, whose requirements are listed; lists (T3d); the waiver tightened and ADR 0003 amended;
   the `rendering` stage refusing every import until PR 3c; and each Low item.
4. **Fourth independent review** (2026-09-24), of the fourth draft. High: text could leave its
   cell over another cell's background through a nested table (the scanner refuses a table inside
   a table at any depth, so the repro cannot reach the record; the design now says so, and the gate
   measures ink inside its box and against the pixels behind it). Medium: the marker bound was
   below a monospaced full stop; the `rendering` stage would have refused PR 2's synthetic paths
   and left T's output locked by nothing; the waiver's amendments were incomplete and "one
   interpretation rule" inaccurate; the gate's measurements depend on fonts it may not have. Low:
   `code` and `monospace` sizes; `border-spacing` under `collapse` and borders Chrome does not
   draw; the waiver's scope (any wholly underlined block) and pass order; the stage order; the
   label's list counts; the border change not listed. Fixed in this draft: the font list narrowed
   to fonts the gate can draw, `code` refused, the marker widths, synthetic publications past the
   `rendering` stage and T(div) hashes in the vectors, the amendments, the waiver scoped to
   subheadings outside tables and lists with a stated two-pass order, and each Low item.
5. **Fifth independent review** (2026-09-24), of the fifth draft. High: T's model read styles over
   the markup's nesting, but a browser builds the HTML parser's tree, where a block tag closes an
   open `p` (the text after it drawn at the parent's 1 pt while T judged it at 11 pt). Medium:
   values Chrome drops (`12.pt`, a five-value `margin`, a repeated border component) were read as
   if drawn; a border's contrast was judged against the table's background, not what is painted
   under it. Low: `dir="ltr"` over right-to-left text; `smaller`'s bounds, `table`'s
   `text-indent`, the `border` attribute, `caption`, `border-spacing`, keyword widths, quoted
   generics and the link colour's declaration; the two passes' exhaustiveness and reported reason;
   "unshifted"; stale figures; the fonts other SmPCs use. Fixed in this draft: "One tree" (T1),
   values as Chrome parses them, border contrast against every background under the edge and the
   gate measuring every shared edge, and each Low item.
6. **Sixth independent review** (2026-09-24), of the sixth draft. High: three more HTML parser
   behaviours the "One tree" list missed, each letting text be drawn at 1 pt where T's model said
   11 pt, unseen by the scanner (T deletes `span`, `u` and `a` first) and by the gate (it did not
   measure size): `dd`/`dt` closing an open `p`; a `span` directly inside a table container, moved
   out of the table by the parser; a self-closing `span` or `u`, whose `/` HTML ignores. Low:
   upper-case names in XML mode; the link colour's wording; wildcard longhands taking in logical
   properties; whose `border-collapse`. Fixed in this draft: "One tree" applied to the authority's
   div, every tag, with HTML's whole "close a p" set, table and list containers, the void rule and
   lower-case names; the gate compares T's model with Chrome's computed style for every text node
   and measures glyph size, so the class is closed by measurement, not by listing; each Low item.
7. **Seventh independent review** (2026-09-24), of the seventh draft. T's static rules and
   `tree.ts` held; the label passes outside 5.1. Medium: the gate checked no drawn line crossing or
   touching a glyph (a raised "−3" merging into the underline of the line above, read as "10⁻⁵";
   a lowered run under its own underline). Low: the gate as listed would refuse the waived `+`;
   the decorations in force must be read, not `text-decoration-line`; "its box" must be the content
   box; T3's border row left out the `border` shorthand the label uses 102 times; the hyphen
   allowance took in dashes that look like "=" or "~". Fixed in this draft: every drawn line
   judged as ink, with its two exceptions; the rest as the review proposed.
8. **Eighth independent review** (2026-09-24), of the eighth draft. No false pass through T and
   the gate. Medium: T's style grammar split declarations at `;` where CSS reads a bracket or a
   function to its close and drops a bad string (the gate would catch each case, but T's own
   reading must be Chrome's); the evidence's store was stated two ways, and nothing said who may
   write it or why Zone B trusts it; the gate's "inside its own box" would refuse the label's
   hanging indents. Low: `markup` missing from T6; `smaller`'s factor chosen per rule instead of
   per comparison; three wordings (a border's contrast, T3e's font size, T3b's origin); the order
   of checks within a section. Fixed in this draft, and `css.ts` refuses brackets and bad strings.
9. **Ninth independent review** (2026-09-24), of the ninth draft. No false pass through T and the
   gate; `css.ts` and `tree.ts` correct (22 000 generated styles against Chrome 153). Medium: the
   design's grammar text left out `{…}` blocks and quoted strings, which also carry a `;`
   (`css.ts` already refuses them); the `smaller` factor for T4's 0.2 fold floor pointed the lenient
   way. Low: the gate's model comparison must allow `smaller`'s range; T3b's −36 pt right overhang
   outside tables was wider than the label needs and than the gate's frame; a split table row; the
   order of checks not tied to a tag. Fixed in this draft.
10. **Tenth independent review** (2026-09-24), of the tenth draft. No false pass through T and the
    gate; no static claim false; the label passes outside 5.1. Medium: a size on both sides of one
    comparison could take two `smaller` factors (two readings, different outcomes); the gate's frame
    tested glyph ink, which overhangs the box (the label's italic "p" 1.6 px left of it), so it would
    refuse the label. Low: CSS whitespace outside quotes; the scanner's reasons and an `li`'s
    offsets in the check order; the size of a `super` shift. Fixed in this draft: one factor per
    computed size, the frame on advance boxes (ink for the overlap and line checks), and each Low item.
11. **Eleventh independent review** (2026-09-24), of the eleventh draft. Both changes held; no false
    pass; the label passes outside 5.1 in T and in the gate's frame (52 379 glyphs at four widths).
    Medium: `sub`'s shift is the parent's size ÷ 5 plus 1 px, not ÷ 3, and T4's deletion bound
    needs it as well as the ceiling (two readings, different outcomes). Low: the frame vertically
    on line boxes; combining marks' ink; the box each check uses; a stale parenthetical; collapsed
    borders and a sub-pixel tolerance; `smaller` factors chosen together; an `li`'s offsets, a tab
    inside quotes, `caption`'s frame. Fixed in this draft.
12. **Twelfth independent review** (2026-09-24), of the twelfth draft. No false pass; no false
    static claim; the label passes outside 5.1. Medium: the eleventh draft's wording took an `li`
    out of T3b's upper bounds while T3's table still sent it there (two readings, different
    outcomes; the gate refuses the extreme). Low: the vertical box for the gap between cells; the
    vertical frame cannot see a shifted run (the overlap, line and gap checks do); how font
    family names compare. Fixed in this draft.
13. **Thirteenth independent review** (2026-09-24), of the whole note end to end. No false pass.
    Medium: "One tree"'s block list took in `br`, so section 1 would refuse; the gate's cell gap
    measured cells above and below each other, refusing 4.2 and 4.8; the gate's sign-over-line
    check asked the allowlist about single code points (the numero rule on "Not known") and did
    not except the waived `+`; which rows take `em`, and unquoted family names, read two ways.
    Low: the line tolerance against the label's 0.5 px clearance; `vertical-align` enlarges the
    line box; a split table row and two misnested bullets; "the trees agree" relies on the
    scanner's refusals. Fixed in this draft.
14. **Fourteenth independent review** (2026-09-24). T's static rules hold; no false pass. Medium,
    all in the gate's wording and two refusing the label as measured: judging a cell's whole last
    line under its border refused a `>` 0.39 em above it in 4.2's Posology; "text outside the
    viewport" refused the fixed-width tables of 4.2 and 4.8 at a phone's width; whether an inline
    background's edge is a line under text read two ways. Low: which size's 0.3 em; the 0.5 px
    clearance was measured in the system's fonts, not the pinned ones. Fixed in this draft.
15. **First code review** (2026-09-25), of the implementation. High, where the code or this note was
    wrong: a cell's, caption's or root's own `text-indent` was not bounded; a border's omitted colour
    was taken before the element's final `color`; border styles outside T3's list were accepted,
    and under `collapse` this note said either cell's border draws the edge where CSS draws only
    the winner; a list marker hangs off the `li`'s border box, not its content box; right-to-left
    text written as references escaped the `dir` rule. Medium: a malformed `colspan` hung the
    importer and huge spans exhausted memory; a malformed `start` crashed it; a `+` T raises by
    folding counted as plain evidence. Low: the check order for `li` offsets, markers and
    backgrounds; T3e's scope; a background on a block inside a positioned inline; pictures
    invisible to the waiver; tabs in a folded run; a `sup` larger than its parent; the lock not
    covering the scanner files T reads. Fixed, the note amended (T3d, T3e, T4, T6), and each repro
    a case.
16. **Second code review** (2026-09-25). High: T4's neighbour rule took a tab, an invisible code
    point or a picture as the unshifted neighbour, and a line feed by reference was not a space as
    the scanner emits it; an element with no text of its own was never size-checked, so a 200 pt
    `b` let a 7 pt digit be raised 90 pt as a superscript; a `caption`'s own margins were
    unbounded (off the page); an omitted border colour under a link is a link colour, and two
    collapsed widths within a pixel draw either border. Medium: deep nesting overflowed the stack,
    and several steps were quadratic on crafted input (a 40 000-run section took minutes); a
    `caption`'s `height` overprints the first row. Low: the check order of a `sup` or `sub`'s own
    refusals. Fixed, the note amended, each repro a case, and a cost test on the crafted inputs.
17. **Third code review** (2026-09-25). It compared T's model with Chrome's computed style on
    1 894 random accepted sections (7 136 text nodes): size, colour, underline and backgrounds
    agreed on every node. High: an omitted border colour under a link took a colour declared
    above the link, not the link colour; under `collapse`, widths more than 0.75 pt apart can
    snap to the same pixel width (0.5 pt white beside 1.4 pt black draws the white), so the
    within-a-pixel rule was unsound; two regular expressions (a CSS function name, the pictures
    stage's `img` tag) were quadratic on crafted input. Medium: T4's neighbour could itself be
    shifted by a deleted shift or sit in a `sup`, so a fold stood 0.1 em from it, or nothing was
    on the baseline. Low: the slot bound was per table where the scanner's is per section; the
    plus-sign evidence took a `+` before a `br`, and after a mark. Fixed: under `collapse` every
    facing border must be drawn, a neighbour is under no shift at all, and the rest as found;
    each repro a case or a cost test.
18. **Fourth code review** (2026-09-25). It found the fixes of reviews 15–17 sound. High: T4's
    neighbour could be a code point drawn blank that is neither whitespace nor default-ignorable
    (U+2800 BRAILLE PATTERN BLANK, the blank Mongolian and Yi glyphs, a private-use code point),
    so a raised digit alone on its line was folded; a 4 MiB section of small elements took more
    than 1 GiB of heap, past the worker's memory, where it should refuse. Fixed: a neighbour is a
    letter, number, punctuation or symbol that is no gap, and a section holds at most 20 000
    elements; T's per-code-point records are shared, so a 4 MiB section imports within a 384 MB
    heap (peak resident memory 690 MB, from 940 MB).
19. **Fifth code review** (2026-09-25). Nothing Medium or higher. It held review 18's refactor
    behaviour-preserving by differential testing against the code before it (90 000 random
    sections, no difference beyond the new neighbour rule, which only adds refusals), drew every
    letter, number, punctuation and symbol code point in Chrome in T's eight font stacks (the only
    blank ones are those the fidelity layer lists as gaps, and U+FFFC), and imported 4 MiB
    sections of eight shapes within a 256 MB heap. Low: two formatting faults in T4, fixed.
