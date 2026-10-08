# Recorded change: what Word draws without a character of text, `docx-reader/1.34.0`, 2026-10-08

_A change to the label reader (`label-docx-reader/`), whose results Zone A's registries, checks and
the certified Word recompute carry. Recorded as for the shared, evidenced libraries in
`docs/validation/README.md`._

**What changed.** The label reader reads five kinds of formatting that draw without a character of
text by what Word was recorded drawing, and by nothing else (each a whitelist of the conditions on
record; any other input is read as before or refused):

- **Tab leaders** (an accuracy fix, first). A tab in a paragraph's text, a list label followed by
  a tab (`tab`, or a Word 6 label's `legacy`) and a positional tab (`w:ptab`) are refused,
  `unsupported-formatting` "a tab with a leader", where a tab stop of the paragraph, its style and
  every style it is based on, its list level, its table style or the document defaults has a
  `w:leader` other than `none` (a stop a nearer level clears counts all the same), or the
  `w:ptab` names one. The reader read every tab as U+0009 and reported no stops, so Zone A's
  tab-as-space rules (ADR 0006 decisions 1, 7, 9, 11, 12) could not see a leader Word draws.
- **Pictures** (reason `effects` before), each where nothing else in the picture is refused: an
  effect extent of 0 to 952,500 EMU on each side (space round the picture); `rotWithShape` 0, 1,
  true or false (a turned or mirrored picture keeps its own reason, which comes first); a shape
  extension list, last, holding only `a14:shadowObscured` (bare) and no effect list; and a line of
  no fill, then at most one join (a miter with a limit up to 800,000 or none, a round, a bevel),
  then a bare head end, tail end or both, its width up to 190,500 EMU its only attribute (a line
  of no fill with any other attribute is now `effects`, where it was carried).
- **Theme shading.** A shading of `background1` (run or paragraph) is resolved where the settings
  map `bg1` to `light1` and the theme's `lt1` is `a:sysClr` `window` with `lastClr` FFFFFF or
  `a:srgbClr` FFFFFF (and nothing else), the shading `clear`, with no tint, a pattern colour of
  `auto`, `000000` or none and a stored fill of six hex digits, which Word ignores: with no shade
  it is no shading, with `themeFillShade` of two upper-case hex digits s it is `shading-ssssss`
  (no shading for FF). A tint, another theme colour or theme, no mapping, or a pattern keeps
  today's `shading-THEME-<name>`.
- **A pattern's theme colour** (`w:themeColor` with `themeTint`/`themeShade` on a patterned
  `w:shd`) is spelt into the kind: `shading-pct15-THEME-accent2-AUTO`,
  `shading-pct15-THEME-accent2-shadeBF-AUTO`. It was read as `shading-pct15-AUTO-AUTO`.
- **Raised or lowered text** reports its shift and sizes: `position+2-size22-in22` (the shift in
  signed half-points, the run's `w:sz` and the size its paragraph's styles and the defaults give
  its text, in half-points, 20 where nothing sets one). A shift or size not in whole half-points
  is refused. It was `position`, the value dropped.

The independent check (`certify.py`, `conservation-check/1.21.0`) holds its own copy of the picture
whitelist, with its own literal bounds, and accepts the position kind only in that form (`R-36`'s
independence: nothing shared with the reader). The tab leaders, theme shading and pattern colour are
the reader's alone, as all its other formatting refusals and shading marks are (the check holds a
shading kind to its form only). Versions: `docx-reader/1.34.0`, `label-docx-json/1.20.0` (the
position kind's form), `label-epi-json/1.11.16`, `epi-reader/1.3.10`.

**Why.** The brief for 1.34.0 (from the coverage work): pictures refused only for markup that
draws nothing, and the template's own `background1` shadings, blocked otherwise exact sections; a
pattern's theme colour could pass for the automatic grey; a position's value is needed before the
builder can tell a nudge from a superscript typed by hand; and a leader Word draws could be
written as a space.

**Evidence (Word 16.113.4 for Mac).** `label-docx-reader/corpus/drawing-cases`: 58 synthetic
cases (`scripts/drawing_cases.py`), each saved as PDF by Word through `label_docx.word`'s locked
oracle (a copy in Word's sandbox, closed by name) and measured by `scripts/ink_drawn.swift` at four
pixels a point (`scripts/word_drawn.py record`, `word-drawn.json`). Every picture, the shading
rows and the tab rows were drawn twice, in Word's own mode and in the QRD template's
compatibility options (`compatibilityMode` 15 and the four others EMA's labels carry), and Word
drew each alike in both:

- 23 pictures of a 40 × 20 red and blue checkerboard: the plain one, and each whitelisted form
  (effect extents of 19,050 each side, 95,250 left, 190,500 right and 95,250 below, 952,500 each
  side; `rotWithShape` 0, 1, true, false; the hidden shadow; lines of no fill with a miter of limit
  800,000, 0 or none, with width 9,525 and 190,500, a round, a bevel, a head or tail end alone;
  all at once) drawn with the same 120 × 60 pixels (one SHA-256), nothing 3 to 12 pixels round
  them and no coloured pixel elsewhere. The near misses differ: a negative extent clips the
  picture (120 × 59), a negative bottom moves its pixels, a shadow and a filled line draw ink round
  it.
- Theme shading, 22 rows in each of four themes and mappings, and the 256 shades: under the QRD
  template's theme (and `lt1` `srgbClr` FFFFFF) Word paints `background1` white whatever the stored
  fill, and its shade s as #ssssss for every s from 00 to FF, in a run or a paragraph; a tint
  paints the tint alone (with a shade too); `nil` paints nothing; `light1`, `accent1`, `text1`
  their theme colours. Under an `lt1` of FF0000 the same rows are red (FF0000, D90000, BF0000):
  the theme decides, so nothing else is resolved. Without a mapping Word paints as with it; the
  reader does not take that, as the brief has it. 15% of `accent2` is #F6E5E4, not grey; 15% of
  `auto` is #D9D9D9.
- Tabs, 15 rows and two cases: Word draws the leader (dots, a line, hyphens, middle dots, a heavy
  line) across the gap of a tab under a stop with a leader set in the paragraph, its style, the
  style that is based on, its list level, its table style and the defaults; after a list label, in
  the label's colour; for a positional tab with a dot leader; and none for a leader of `none`, no
  leader named, no stop, or a label with no stop.

**Proof.** `tests/test_word_drawn.py` holds the reader to the record: a picture carried exactly
where Word drew its pixels alone, every colour the reader names for a shading the colour Word
painted, every theme fill resolved only where it was, and a tab refused exactly where Word drew a
leader (each tab row read alone). `tests/test_reader.py`, read through both the reader and the
check (`PICTURE_CASES`), each condition of each whitelist on both sides: every bound and its
next value, attributes, children, order, namespaces, near misses of each mapping and theme; the
leader from every source and the cases with no leader; the position kind and its refusals.
`tests/test_certify.py` the position kind's form, and a page number in another field's result
(Word's own tables of contents, the corpus's only such case, are refused now; the mutation run
found the check's rule for it otherwise unheld). The check's mutation record: 4,275 of 4,381
faults killed, 106 recorded as unable to change a result, none unexplained.

**Impact assessment.**

- **Zone A** (`zone-a/`): the builder (`word_epi.py`) is unchanged. A resolved `background1` is no
  shading, or `shading-D9D9D9`, the template's grey it already carries; other greys
  (`shading-BFBFBF`, `shading-F2F2F2`...) and the new position and pattern kinds are refused as
  any other formatting (`formatting`, the kind its detail). The QRD registries and the five ePI
  label checks move in the reader's version names only. `word-drawing/1.2.4`: `zone_a.drawing`
  locks `label_docx/epi.py`, whose epi-reader version moved; its rules do not change. The pinned dev
  image holds no code (the build mounts it), so its digest stands.
- **Certified Word importer** (`src/certified-word/`): 1.3.4, its recompute fixtures and vectors
  moving in the reader's version names only, every outcome unchanged.
- **The label reader's corpus**: `word-authored` (Word's own tables of contents, page numbers
  after a dot leader) is refused now; in the FDA prescribing-information template 245 marks of
  `shading-THEME-background1` are no shading now. Every other result changes in its certificate
  only.
- **Stored results** keep the reader version they were read under; nothing earlier is rewritten.

**Measured effect.** EMA's published English Word PI (internal corpus, accepted view; counts only),
the whole-document funnel before (`docx-reader/1.33.0`, #210's head) and after:

|                  | Files | Whole (every section carried) | Sections carried (built parts) | Reader refusals |
| ---------------- | ----: | ----------------------------: | -----------------------------: | --------------: |
| SmPCs (en-smpc)  |   296 |                       18 → 20 |         3,931 → 3,947 of 4,194 |       142 → 143 |
| Leaflets (en-pl) |   286 |                       14 → 16 |         1,715 → 1,723 of 1,870 |         91 → 91 |

No file carries fewer sections. Sections refused by kind, SmPC and leaflet: a picture's `effects`
11 → 1 and 9 → 7 (in those 8 files, 11 pictures with a drawn outline and one with an effect list),
`shading-THEME-background1` 11 → 0 and 6 → 0 (one SmPC section now refused for `shading-BFBFBF`;
the others' next refusal, if any, shows: a picture's size or colour 3, a table's shape 1);
`position` 41 + 3 in headings, now 21 `position+…` and 23 `position-…`, unchanged in number. Made
whole: SmPC 2 (a picture's effects, a theme shading), leaflet 2 (one each). Tab leaders: one SmPC
refused by the reader now (`a tab with a leader`: a list label's tab under a dotted stop), which
the builder refused whole before; no tab in the text of any file stands under a leader. No pattern
in these files has a theme colour, so that change frees nothing yet.

**What the builder can take next** (each a fidelity-norm or owner decision, none in this change):
`shading-pct15-AUTO-AUTO` and `shading-pct15-AUTO-FFFFFF` as the template's grey (Word paints
#D9D9D9 on any background; the theme-coloured pattern can no longer pass for them); the greys a
shade gives (`shading-F2F2F2`, `-E6E6E6`, `-BFBFBF`, `-A6A6A6`, `-808080`), as decided; and a
nudge (`position±1`/`±2` at the paragraph's size) as an owner decision, a smaller raised run kept a
superscript and never dropped.
