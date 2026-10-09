# Recorded change: what Word draws without a character of text, and grid widths, `docx-reader/1.34.0`, 2026-10-08

_A change to the label reader (`label-docx-reader/`), whose results Zone A's registries, checks and
the certified Word recompute carry. Recorded as for the shared, evidenced libraries in
`docs/validation/README.md`._

**What changed.** The label reader reads formatting that Word draws with no character of text by
what Word was recorded drawing, and by nothing else: each rule is a whitelist of the conditions on
record, and any other input is read as before or refused. It also reports each table's grid column
widths.

- **Tab leaders and bar stops** (an accuracy fix, first). A tab in a paragraph's text, or a list
  label followed by a tab (`tab`, or a Word 6 label's `legacy`), is refused
  (`unsupported-formatting`, "a tab with a leader") where any tab stop that applies has a
  `w:leader` other than `none`. Stops apply from:
  - the paragraph itself, its style and every style it is based on;
  - its table style and every conditional part of that style;
  - the document defaults, and its list level.

  A positional tab (`w:ptab`) whose leader is not `none` is refused too. A `w:ptab` must name its
  leader; one that names none is refused.

  A bar stop (`w:val="bar"`) among those stops is refused whether or not the paragraph has a tab
  ("a bar tab stop"): Word draws its rule down the line either way.

  Before this change the reader read every tab as U+0009 and reported no stops. Zone A's
  tab-as-space rules (ADR 0006 decisions 1, 7, 9, 11, 12) therefore could not see a leader Word
  draws.

- **Pictures** (reason `effects` before) are carried in these cases, each only where nothing else
  in the picture is refused:
  - an effect extent of 0 to 952,500 EMU on each side, in a paragraph outside a table and a frame;
  - `rotWithShape` of 0, 1, true or false (a turned or mirrored picture keeps its own reason,
    which comes first);
  - a shape extension list, placed last, holding only a bare `a14:shadowObscured` and no effect
    list;
  - a line of no fill. It may have at most one join (a miter with a limit up to 800,000 or none, a
    round or a bevel), then a bare head end, tail end or both. Its width, up to 190,500 EMU, is
    its only attribute.

  These stay `effects`:
  - an effect extent other than 0 in a table cell or a frame;
  - a line of no fill with any other attribute (it was carried before).

- **Theme shading.** A shading of `background1`, on a run or a paragraph, is resolved where every
  one of these holds:
  - the settings map `bg1` to `light1`;
  - the theme's `lt1` is exactly `a:sysClr` `window` with `lastClr` FFFFFF, or `a:srgbClr` FFFFFF;
  - the shading is `clear`, with no tint;
  - its pattern colour is `auto`, `000000` or none;
  - its stored fill is six hex digits (Word ignores it).

  With no shade it resolves to white. With `themeFillShade` of two upper-case hex digits s, it is
  `shading-ssssss`.

  Any other theme fill keeps its name, now spelt with its tint and shade
  (`shading-THEME-background1-tint80`).

- **White shading over paint.** A white shading (a fill of FFFFFF, or `background1` resolved white)
  is a mark, `shading-FFFFFF`, wherever anything but white is painted under it. That means:
  - the paragraph's own shading;
  - its cell's, a table's or a row's shading, or their table style's;
  - a page colour other than white;
  - the run's own highlight.

  Over white it is left out, as every white fill was before. Before this change a white run over a
  grey paragraph read as the paragraph's grey alone, so the builder would have drawn grey where Word
  paints white.

- **A pattern's theme colour** is spelt into the kind, so it never reads as automatic. For example:
  - `shading-pct15-THEME-accent2-AUTO`;
  - `shading-pct15-THEME-accent2-shadeBF-AUTO`.

  A theme tint or shade with no theme colour to apply to, for the fill or the pattern, is refused.

- **Raised or lowered text** reports its shift and sizes, e.g. `position+2-size22-in22`:
  - the shift is in signed half-points;
  - then the run's `w:sz`;
  - then the size its paragraph's styles and the defaults give its text, in half-points, or 20
    where nothing sets one.

  These are refused:
  - a shift or size that is not a whole number of half-points;
  - shifted complex script, which Word draws at `szCs`.

- **`nil` shading** is no shading, whatever its fill, colour or theme fill. Word paints nothing
  for it (the `bg1-nil` and `nil-*` rows, under a white and a red theme). Both copies had read a
  `nil` with a fill as that fill's paint, which Zone A could have carried as grey; this bug
  predates this release.
- **Grid widths** (asked for `fidelity-norm/3.6.0`). A table's grid carries `widths`: each
  `gridCol`'s `w:w` in twips as stored, in grid order, as the view read stores it. The original
  view of a tracked grid change carries its former grid. A column whose width is not digits
  without a leading zero, from 1 to 31,680, leaves no grid on record, with the new reason
  `bad-width`.

  The widths are what the file stores, not what Word draws. Word lays an autofit table out again
  when it opens or saves it: a one-column table stored 4,000 twips wide was saved 398 to 526 wide
  (`corpus/tracked-cases`). No consumer may take them for drawn widths. `fidelity-norm/3.6.0` no
  longer uses them: its column-width rule was withdrawn after Chrome drew a column at 0 px.

**The independent check** (`certify.py`, `conservation-check/1.21.0`) has its own code for every
rule above but the position's (of which it holds only the kind's form), shares none with the
reader, and uses its own literal bounds:

- the picture whitelist and the extent's place;
- the tab stops that apply, bar stops and positional tabs;
- the grid widths, and in the original view of a tracked grid change, the former grid exactly,
  its widths too (Word's verdict on the views leaves widths out);
- each run's and paragraph's shading mark. This is worked out character by character, including:
  - its own reading of the settings' mapping and the theme;
  - the pattern colour;
  - what is painted under a white shading.

  Shading is now a checked mark, so a result whose shading marks are not the check's own is never
  certified.

**Versions:**

- `docx-reader/1.34.0`;
- `label-docx-json/1.20.0` (the position kind's form, the grid's `widths`, the `bad-width` reason);
- `label-epi-json/1.11.16`;
- `epi-reader/1.3.10`;
- `word-verifier/1.0.8`. Its tracked-change verdict now leaves column widths out of the comparison.
  Word lays an autofit table out again when it saves its Accept All and Reject All files: a
  one-column table stored 4,000 twips wide saved 398 to 526 wide (`corpus/tracked-cases`, whose
  tables now store widths, Word's views made again).

**Known over-refusals.** These refusals are safe, but go beyond Word's drawing:

- A stop with a leader is counted even where a nearer level clears it, or no tab reaches it.
- A leader in any conditional part of a table style is counted, though Word drew none from a
  whole-table part.
- A label tab is refused under a leader stop even where it stops at the hanging indent first.
- A positional tab with leader `none` is refused in a paragraph that also holds a leader stop.
- An effect extent in a wide cell is not carried, though Word drew it alike there.

Not counted, and not drawn: for a nested table with no style of its own, the stops of the outer
table's style and its parts (the nested table takes the default table style, as Word applies a
table's own style to its cells).

**Why.** The brief for 1.34.0 came from the coverage work:

- Pictures refused only for markup that draws nothing, and the template's own `background1`
  shadings, blocked sections that were otherwise exact.
- A pattern's theme colour could pass for the automatic grey.
- The builder needs a position's value before it can tell a nudge from a superscript typed by hand.
- A leader Word draws could be written as a space.

An independent review found two further faults, both fixed here:

- leaders from a table style's conditional parts were missed;
- a white run over a grey paragraph lost its white.

`fidelity-norm/3.6.0` needs the grid widths.

**Evidence (Word 16.113.4 for Mac).** The evidence is `label-docx-reader/corpus/drawing-cases`: 76
synthetic cases written by `scripts/drawing_cases.py`. Word saved each as PDF through
`label_docx.word`'s locked oracle (a copy in Word's sandbox, closed by name).
`scripts/ink_drawn.swift` measured the PDFs at four pixels a point (`scripts/word_drawn.py record`,
`word-drawn.json`).

Every case is drawn twice:

- as written, with no compatibility options (asked once through the oracle, Word captioned such a
  file's window "Compatibility Mode"; that answer is not kept in the record);
- under the QRD template's options (`compatibilityMode` 15 and the four others EMA's labels
  carry), which open as Word's current mode.

Word drew every case alike in both modes. The cases:

- **27 pictures** of a 40 × 20 checkerboard.
  - Each whitelisted form drew the plain picture's 120 × 60 pixels (one SHA-256), with nothing
    round them.
  - A negative extent clips the picture, and a shadow or a filled line draws ink.
  - With a large left extent in a narrow fixed cell, and a large top and bottom extent in a frame
    of exact height, Word drew none of the picture.
  - With an extent in a wide cell, it drew the picture alike.
- **Theme shading.** These are 22 rows in each of four themes and mappings, plus all 256 shades.
  - Under the QRD template's theme, or `lt1` `srgbClr` FFFFFF, Word paints `background1` white
    whatever the stored fill. It paints shade s as #ssssss for every s, in a run or a paragraph.
  - A tint paints the tint alone. Under an `lt1` of FF0000 the same rows are red.
  - 15% of `accent2` is #F6E5E4, not grey.
- **A white run over grey.** Word paints a white or `background1` run over a grey paragraph, and
  over a grey cell. It paints less grey there than with the run unshaded.
- **A theme's shade over trailing spaces.** A shade of 80 over spaces that end a paragraph or a
  cell paints nothing, as an explicit fill of 808080 does. Between words it paints. Zone A's
  builder therefore leaves the resolved shade out where it leaves the fill out (its
  `unpainted()`).
- **Tabs.** Word draws:
  - the leader from the paragraph, its style, the style it is based on, its list level, the
    defaults, its table style and the table style's first-row part;
  - after a list label, in the label's colour;
  - for a positional tab with a dot leader;
  - a bar stop's rule, with a tab or without one.

  Word draws nothing for:
  - a leader of `none`, or no leader named;
  - no stop;
  - a label with no stop;
  - the whole-table part's leader.

**Proof.**

- `tests/test_word_drawn.py` holds the reader to the record in both modes, case by case:
  - pictures carried exactly where Word drew them alone (the wide cell aside);
  - every colour named for a shading is the one Word painted;
  - white marked where Word painted it over grey;
  - a theme shade over trailing spaces painted as a fill's;
  - every tab row refused exactly where Word drew a leader or a rule;
  - the conditional parts refused as listed.
- `tests/test_reader.py` holds each condition of each whitelist on both sides, read through both the
  reader and the check (`PICTURE_CASES`, `PLACED_CASES`). It also covers:
  - every source of a stop;
  - the white rules under each kind of paint;
  - the theme near misses;
  - the position kinds;
  - the grid widths and `bad-width`, ahead of the other reasons.
- `tests/test_certify.py` holds the check on its own:
  - it never certifies a tab under a leader or a bar stop;
  - it works out each shading mark, and every dropped, respelt or added one is refused;
  - grid widths changed, dropped or added are refused.
- `tests/test_tracked.py` covers each tracked view's widths.
- The check's mutation record: 4,592 of 4,700 faults killed, 108 recorded as unable to change a result, none unexplained. A first run over the review's fixes found 47 unheld, each now held by a case.

**Impact assessment.**

- **Zone A** (`zone-a/`). The builder (`word_epi.py`) changes only in its comments, `word-epi/1.5.1`.
  Its rules are unchanged:
  - a resolved `background1` is no mark, or `shading-D9D9D9`, the template grey it already
    carries;
  - over trailing spaces, a resolved shade is left out as a fill is, which is now on record;
  - `shading-FFFFFF` over paint, the other shade greys, and the new position and pattern kinds are
    refused as formatting, with the kind as the detail. They are not refused as "any other": the
    rules that leave marks out over trailing spaces take the solid ones.

  The QRD registries and the five ePI label checks move in the reader's version names only.
  `word-drawing/1.2.4` follows `label_docx/epi.py` and `word_epi.py`. The pinned dev image holds
  no code, so its digest stands. Dev's signed record for `word-drawing/1.2.2` is found under that
  version only.

- **Certified Word importer** (`src/certified-word/`). It moves to 1.3.4. Its recompute fixtures and
  vectors change in the reader's and builder's version names only, and every outcome is unchanged.
- **The label reader's corpus.**
  - `word-authored` is refused now. These are Word's own tables of contents, whose page numbers
    follow a dot leader.
  - In the FDA prescribing-information template, 245 marks of `shading-THEME-background1` are no
    shading now.
  - Every other result changes only in its certificate and its tables' widths.
- **Stored results** keep the reader version they were read under; nothing earlier is rewritten.

**Measured effect.** The corpus is EMA's published English Word PI (internal, accepted view; counts
only). This is the whole-document funnel before (main, `docx-reader/1.33.0`) and after:

|                  | Files | Whole (every section carried) | Sections carried (built parts) | Reader refusals |
| ---------------- | ----: | ----------------------------: | -----------------------------: | --------------: |
| SmPCs (en-smpc)  |   296 |                       18 → 20 |         3,931 → 3,946 of 4,194 |       142 → 143 |
| Leaflets (en-pl) |   286 |                       14 → 16 |         1,715 → 1,723 of 1,870 |         91 → 91 |

No file carries fewer sections. Sections refused, by kind (SmPC, then leaflet):

- a picture's `effects`: 11 → 2 and 9 → 7. Those left hold a drawn outline, an effect list, or
  an extent in a table cell.
- `shading-THEME-background1`: 11 → 0 and 6 → 0. Five SmPC sections freed of these, or of
  `effects`, are now refused for their next mark instead:
  - `shading-BFBFBF`: 1;
  - `shading-FFFFFF`, a white run over paint: 1;
  - a picture's size or colour: 3.
- `position`: 41, plus 3 in headings, are now 21 `position+…` and 23 `position-…`, the same number.

Made whole, by what each file lost:

- SmPC 2: a picture's `effects` in both, a theme shading in one;
- leaflet 2: one of each.

Tab leaders: one SmPC is now refused by the reader (`a tab with a leader`: a list label's tab
under a dotted stop). The builder refused that file whole before. No tab in any file's text
stands under a leader. No file has a bar stop, and no pattern in these files has a theme colour.

Grid widths: every `gridCol` in the corpus, 15,374 in the SmPCs and 3,784 in the leaflets, has a
`w:w` of digits from 2 to 14,833. No table loses its grid.

**What the builder can take next.** Each is a fidelity-norm or owner decision, and none is in this
change:

- `shading-pct15-AUTO-AUTO` and `shading-pct15-AUTO-FFFFFF` as the template's grey. Word paints
  them #D9D9D9 on any background, and a theme-coloured pattern can no longer pass for them.
- The greys a shade gives, as decided: `shading-F2F2F2`, `-E6E6E6`, `-BFBFBF`, `-A6A6A6`,
  `-808080`.
- `shading-FFFFFF` over paint, drawn as white or refused.
- A nudge (`position±1` or `±2` at the paragraph's size) as an owner decision. A smaller raised run
  stays a superscript and is never dropped.
- Nothing, for now, from the grid `widths`: they are stored, not drawn (above).
