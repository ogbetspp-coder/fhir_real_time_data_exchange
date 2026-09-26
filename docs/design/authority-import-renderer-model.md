# T's model output: what the renderer gate compares (roadmap 3a, PR 3c-B)

- Status: proposed, 2026-09-26; second draft, after one design review
- Decides: the output format the renderer note's R3 leaves to "3c-B's addendum": the entries T
  emits for each section, in R3's index space; each property as T models it; how each is compared
  with Chrome's computed style; the fold and waiver fields, and where the scanner offsets and the
  picture list go; the serialisation and its version
- Amends: `docs/design/authority-import-renderer.md` (R3 points here);
  `docs/design/authority-import-t.md` (T1's model gains the properties below that T judged but did
  not model; the importer's version moves to 2.1.0)
- Related: `src/authority/t/`, the renderer note's R1 (the render build's step 2), R2, R3, R8, R9

## What this is for

R3 makes the third code review's one-off comparison a merge gate: for every text node and list
marker of every section T transforms, Chrome's computed style must equal T's model of it, in both
modes, at the named widths, or the section is a refusal of ours. So a gap in T's model (a default
T forgot, a declaration it reads differently from Chrome) fails the gate instead of letting T judge
text in a style it is not drawn in. The judge never loads T's code (R1, R9): T emits its model as
data, and the judge compares that data with what Chrome reports. This note fixes that data's shape
and the comparison, so T's side and the judge's side can be written and reviewed apart.

Nothing here changes what T accepts or refuses. The model output is computed by the same walk T
already makes; where T judged a property without recording it (font weight, font style, the
browser's vertical margins, a table's `border` and `cellspacing` attributes, a cell's vertical
alignment, an element's display, the whitespace text between table parts and list items), the model
now records it, and a test asserts that T(div) and every refusal are unchanged on every T case and
every pinned label. The model covers the sections T and the scanner both accept: T's "one tree"
(T1) holds only with the scanner's own nesting refusals (`misnested-tag` among them; T alone accepts
`<ol><li>a<li>b</li></li></ol>`), and a section either refuses has no model output.

## M1. The index space

One index space per section, the judge's (R3), from the authority's div as served:

- **Elements** are keyed by their position in a pre-order walk of the XML-mode DOM of the div,
  the div itself `0`. T's tree (`readTree`) walks the same elements in the same order: T accepts
  only markup whose nesting and whose HTML tree agree (T1's "one tree"), and an element the HTML
  parser inserts (a `tbody`) is not in the XML-mode DOM and has no key. A test asserts, on every
  carried section of every pinned label and every accepted T case, that T's keys equal the judge's.
- **Text nodes** are the XML-mode DOM's: keyed by their pre-order position among the div's text
  nodes, each covering a half-open range `[start, end)` of **code-point offsets** in the
  concatenation, in that order, of their data: character references decoded, and a CR LF pair or a
  lone CR counted as one LF, as XML parsing normalises it. `readTree` drops the ASCII whitespace
  between table parts and between list items, which the XML-mode DOM keeps as text nodes (the
  first review measured 36 text nodes and 2 326 code points in the DOM of Imatinib Teva's 4.1,
  where T's walk has 25 and 2 315); so `readTree` records those runs too, as text nodes flagged
  `interElement: true`, without changing what it accepts, and T's offsets are counted over them and
  over the normalised line ends. The judge compares no style for an `interElement` node.
- **Markers** are keyed by their `li` element's key.

## M2. The entries

For each section T accepts, the model output is one JSON object:

```json
{
  "format": "t-model/1.0.0",
  "elements": [ { "key": 0, "name": "div", "style": { … } }, … ],
  "text": [ { "key": 0, "element": 3, "start": 0, "end": 14 }, … ],
  "markers": [ { "element": 12, "text": "1.", "style": { … } }, … ],
  "folds": [ { "element": 40, "decision": "sup" }, … ],
  "waivers": [ { "start": 812, "end": 813 }, … ]
}
```

- `elements`: every element of the div, in key order, with its modelled style (M3). A text node's
  style is its parent element's; there is no per-text-node style.
- `text`: every text node (M1), its parent element's key and its range.
- `markers`: every `li`, its marker's text and the style it is drawn in. An `ol`'s marker is the
  scanner's (`listMarker`, "1. " with its space, T3d; type `a`, `A`, `i`, `I`, `start`, zero and
  negative ordinals and 4 000's fall back to decimal all measured equal); a `ul`'s is CSS's bullet
  for its depth, `• ` inside no other list, `◦ ` inside one, `■ ` (U+25A0) deeper (Chrome's
  `disc`, `circle` and `square`, any `ol` or `ul` ancestor counting, through a table cell too). The
  judge reads each marker's text from the accessibility tree (its `ListMarker` node's name) and
  compares it exactly. A marker's style is the `li`'s **inherited** properties only (size, weight,
  style, colours, line height and the backgrounds chain), with `underline` false: Chrome's
  `::marker` takes no decoration from an underlined `li` or ancestor, and draws with its own display,
  box and background, which the judge does not compare; T already treats a marker as a boundary,
  never underlined text.
- `folds`: every element T4 decided, with its decision (`delete`, `sup` or `sub`).
- `waivers`: the ranges, in M1's offsets, of T5's waived plus signs. T's walk indexes its own code
  points, which include boundaries, list markers and U+FFFC for a picture; the emitter maps each
  waived point back to the text node it came from and its offset there.

Two outputs R3 names are **not** in the model:

- **The scanner's offsets.** PR 3c-C, whose underline checks need a code point's neighbours in the
  scanner's text (P5), adds a `scanner` field (for each maximal run of code points the scanner copies
  in order, the run's range in M1's offsets and the offset of its first code point in the scanner's
  text of T(div)) as `t-model/1.1.0`; the judge requires the field from that version. It is absent
  in 1.0.0, never present and empty.
- **The picture list.** R2's per-section picture list is an output of its own (R1's step 2 and R8
  keep it for every section, refused and withheld ones included), not part of the model or of
  `modelSha256`; its shape is R8's.

A section T refuses has no model output (it is drawn once, as served, R2; R3's font and script
checks still run on it).

## M3. The modelled style, and how each property is compared

Each element's `style` object holds the properties below, computed by T's walk (T1: the browser's
defaults, the element's declarations in order, inheritance). Lengths are in points; a length that
depends on a size under `smaller` is a range `{ "lo": …, "hi": … }` (T1's bound, × 0.75 to × 0.9),
and an exact length is a range with `lo` equal to `hi`. Chrome reports lengths in CSS pixels,
serialised to six significant digits; a modelled range `[lo, hi]` points matches Chrome's value `v`
pixels when `lo × 4/3 − t ≤ v ≤ hi × 4/3 + t`, where `t` is one unit in the sixth significant digit
of `|v|` (a whole unit, since a value stored in single precision can land on a rounding boundary).
Every other value matches only when equal. A set of colours is serialised sorted by red, green,
blue.

| Property            | Modelled as                                                                                                     | Chrome's value compared                                                                                                                                         |
| ------------------- | --------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `fontSize`          | range, pt                                                                                                       | `font-size`                                                                                                                                                     |
| `fontWeight`        | an integer, 100 to 900                                                                                          | `font-weight`                                                                                                                                                   |
| `fontStyle`         | `normal`, `italic` or `oblique`                                                                                 | `font-style`                                                                                                                                                    |
| `colours`           | the set of RGB triples the text may be drawn in (both link colours under a link with no author colour since it) | `color`, which must be opaque and in the set                                                                                                                    |
| `lineHeight`        | `normal`, or a range, pt (a number emitted as the number times the element's own size)                          | `line-height` (`normal`, or pixels)                                                                                                                             |
| `underline`         | boolean                                                                                                         | `-webkit-text-decorations-in-effect` is `underline` when true, `none` when false                                                                                |
| `backgrounds`       | the list of painted background colours from the div down to the element, outermost first                        | each ancestor's and the element's own `background-color`, the opaque ones in that order; a transparent one skipped, and one with 0 < alpha < 1 a mismatch       |
| `textIndent`        | range, pt                                                                                                       | `text-indent`                                                                                                                                                   |
| `margin`, `padding` | per side, a range, pt                                                                                           | `margin-*`, `padding-*`                                                                                                                                         |
| `borders`           | per side: `none`, or `{ width, style, colours }` with the width in pt                                           | `border-*-style` equal; for a side not `none`, `border-*-width` against the width **as drawn** (below) and `border-*-color` in the set; a `none` side's width 0 |
| `borderCollapse`    | `collapse` or `separate`                                                                                        | `border-collapse`                                                                                                                                               |
| `borderSpacing`     | pt                                                                                                              | `border-spacing`, as drawn (below), each of its one or two values                                                                                               |
| `display`           | the display the element's name gives (below)                                                                    | `display`                                                                                                                                                       |
| `position`          | `static` or `relative`                                                                                          | `position`                                                                                                                                                      |
| `top`, `bottom`     | `auto`, or a range, pt                                                                                          | `top`, `bottom`                                                                                                                                                 |
| `margin` sides      | a range, pt, or `auto` (below)                                                                                  | not compared where `auto`                                                                                                                                       |
| `verticalAlign`     | `baseline`, `super`, `sub`, `top`, `middle`, `bottom`, or a range, pt                                           | `vertical-align`                                                                                                                                                |

- **Widths as drawn.** Chrome snaps a border's width to whole device pixels, at least one where the
  width is above zero: at a ratio `r`, a modelled width `w` points is drawn as
  `max(1, floor(w × 4/3 × r)) / r` pixels, and `0` stays `0` (measured on chrome-headless-shell
  154: 0.75 pt is 1 px at ratio 1, 1.25 px at 0.8, 0.8 px at 1.25 and 0.761905 px at 2.625).
  `border-spacing` snaps the same way with no minimum: `floor(w × 4/3 × r) / r` (`cellspacing="1"`
  is 0 px at 0.8 and 0.9; the default 2 px is 1.6 px at 1.25 and 1.90476 px at 2.625). The product
  is evaluated exactly (in rationals, or rounded to 1e-9 before the floor): in binary floating
  point 1.2 pt at 1.25 comes to 1.9999999999999998 and floors to 1, where Chrome draws 2. A test
  asserts both rules at every ratio of R2 against the pinned image, and the comparison uses them
  with the tolerance `t`.
- **Weight.** The root is 400. `b` and `strong` are `bolder`; `h1` to `h6` and `th` are 700.
  `bolder` and `lighter`, from the element's own declaration or its default, take the parent's
  weight through CSS's table (`bolder`: below 350 to 400, below 550 to 700, else 900; `lighter`:
  below 550 to 100, below 750 to 400, else 700), and a numeric weight or `normal` (400) or `bold`
  (700) replaces it (measured: `bolder` under 400 is 700, `b` under 700 is 900, `lighter` under 900
  is 700).
- **Style.** The root is `normal`; `i`, `em` and `cite` are `italic`; a declaration replaces it.
- **Vertical margins.** The browser's defaults for the elements T accepts, in em of the element's
  own size: `p` and `blockquote` 1 em top and bottom; `dl`, `ol` and `ul` 1 em, but 0 with any
  `dl`, `ol` or `ul` ancestor (through a `dd` or a table cell too); `h1` 0.67 em, `h2` 0.83 em,
  `h3` 1 em, `h4` 1.33 em, `h5` 1.67 em, `h6` 2.33 em; `hr` 0.5 em; every other element 0; a
  declaration replaces them (measured: `h5` at 13.28 px has 22.1776 px, `hr` 8 px, a `ul` in a `dd`
  0 px). T3b judges horizontal offsets only, so these change no decision of T.
- **`hr`.** Its own colour is gray (`rgb(128, 128, 128)`) unless it declares one, and its 1 px
  `inset` border on every side is drawn in that colour (measured: `hr style="color:red"` has a red
  colour and red borders).
- **A centred table.** A `table` with `align="center"` has `auto` left and right margins, unless it
  declares one side, which keeps its value while the other stays `auto`; Chrome reports the used
  value, which depends on the width (377 px at 813), so the judge does not compare an `auto` side.
- **Tables.** Outside any table, `border-spacing` is 0 and `border-collapse` is `separate`. Each
  `table` resets them: `border-spacing` to 2 px (1.5 pt) or its `cellspacing` attribute's pixels,
  `border-collapse` to `separate` or its declaration; both inherit to its descendants, up to a
  nested table, which resets them again. A `table` `border`
  attribute of `N` other than `0` gives the table `N` px `outset` borders and each of its cells
  1 px `inset` borders, each in the element's own `color` (measured: `rgb(0, 0, 0)` for black text);
  a declared border replaces them side by side. A cell's `padding` is 1 px, or the
  `cellpadding` attribute's pixels, and a declaration replaces it.
- **Vertical alignment.** A `thead`, `tbody` or `tfoot` is its `valign`, else `middle`; a `tr` is
  its own `valign`, else its group's value, else `middle` where its parent is the `table` itself; a
  `td` or `th` is its declaration, else its `valign`, else its row's (a declaration beats `valign`,
  measured); `sup` is `super` and `sub` is `sub`; any other inline element is `baseline` unless it
  declares a length or keyword (T3's shift rows); a block is `baseline`.
- **Display.** `div`, `p`, `h1` to `h6`, `blockquote`, `dl`, `dt`, `dd`, `ol`, `ul` and `hr` are
  `block`; `li` is `list-item`; `table` is `table`; `caption` is `table-caption`; `thead`, `tbody`
  and `tfoot` are `table-header-group`, `table-row-group` and `table-footer-group`; `tr` is
  `table-row`; `td` and `th` are `table-cell`; every other element, `br` and `img` included, is
  `inline` (T refuses `display`).
- **Shifts.** `position` is `relative` exactly where T3 read `position: relative`, with the `top`
  or `bottom` it declared (an `em` length a range under `smaller`, of the element's own size) and
  the other side its negation, as Chrome resolves it (measured: `top:-5pt` has `bottom` 6.66667 px);
  elsewhere both are `auto`.

A test asserts every default in this section (every element T accepts, alone and under each
parent T allows) against the pinned image, so a default this note states wrongly fails the test
before it fails a label.

## M4. What the judge does with it (R3)

For every carried section, in HTML and XML mode, at R2's named widths (813, 360 and 1 240 px) and
every ratio, the judge reads each element's computed style (in an isolated world, scripts disabled)
and each list marker's text from the accessibility tree, and compares them with the model entry of
the same key by M3. Any difference, a missing entry or an extra element, is a refusal of ours
(`model-mismatch`, with the key, the property, T's value and Chrome's), and the record carries it.
Widths are named because a computed style does not depend on the width, but for a centred table's
`auto` margins, which are not compared (R2's widths for geometry are R4's); the ratio matters only
for widths as drawn.

## M5. Serialisation and versions

- The output is canonical JSON (RFC 8785): numbers as ECMAScript serialises them, keys sorted, no
  whitespace. `modelSha256` (R3, R8) is the SHA-256 of those bytes.
- `format` is `t-model/1.0.0`. A field added that the judge then requires (3c-C's `scanner`) is a
  new minor; a change to the shape or the meaning of a field is a new major; the judge refuses a
  format it does not know (`model-format`).
- The emitter is T's code (`src/authority/t/model.ts`), under the importer lock (D10); adding it
  moves the importer to 2.1.0 (`npm run authority:lock`). It computes the model from T's own walk;
  it has no path of its own through the markup.

## Stated residuals

- The model covers the properties R3 lists; a property Chrome draws that R3 does not list (text
  alignment from `align`, `white-space` from `nowrap`, `width`, `height`) is not compared, since T
  does not judge text by it.
- T models an `a` with `href` and a `u` as underlined even where they declare
  `text-decoration: none`, which Chrome draws without an underline; such a section fails the
  comparison, a refusal of ours, which is safe (no pinned label has it). Modelling it would change
  what T accepts, so it waits for a change to T that says so.
- A computed style does not show a layout the style produces; that is R4's.

## Verification

- T(div) and every refusal unchanged, on every T case and every section of every pinned label.
- M1's keys and offsets equal the judge's on every carried section and every accepted T case,
  including one with whitespace between table parts and list items and one with CR LF line ends.
- Every default of M3 asserted against the pinned image; the width rule asserted at every ratio.
- R3 on every accepted T case and every carried section of the pinned labels, in both modes, at the
  named widths and every ratio: no mismatch; and a model entry deliberately altered in a test
  (a colour, a size, a weight, a border width, a vertical alignment) fails with `model-mismatch`.
- The model output reproduces byte for byte across two runs and two machines.

## Reviews

1. **First independent review** (2026-09-26). No High. Medium: the side of a shift not declared is
   the negated offset, not `auto` (4 spans in both Imatinib labels' 4.2); rows inherit their group's
   vertical alignment; nested `dl`, `ol` and `ul` have no block margins; the third bullet is `■`; a
   marker's style is not the `li`'s whole style; `hr` has its own gray colour; `border-spacing` has
   no one-pixel minimum, and is 0 outside tables and reset per table; a centred table's margins are
   `auto`; T's text offsets omit the whitespace between table parts and list items that the XML DOM
   keeps; the picture list belongs outside the model. Low: floating-point flooring, the underline
   gap, the one-tree scope, the comparison's wording, the empty `scanner` field, waiver offsets,
   colour order, residuals. Measured: a rough R3 over 185 carried sections of the five pinned
   labels at eight ratios found only the shift mismatch. Fixed in this draft as found.
