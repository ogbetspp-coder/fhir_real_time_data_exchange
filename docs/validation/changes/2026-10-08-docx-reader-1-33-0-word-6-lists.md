# Recorded change: Word 6 list labels as Word draws them, `docx-reader/1.33.0`, 2026-10-08

_A change to the label reader (`label-docx-reader/`), whose results Zone A's registries, checks and
the certified Word recompute carry. Recorded as for the shared, evidenced libraries in
`docs/validation/README.md`._

**What changed.** The label reader reports what follows a Word 6 list label (`w:legacy`) as Word
draws it, and refuses what Word's drawing does not cover:

- After a Word 6 level's label with no `w:suff`, Word's "convert numbers to text" writes a tab,
  but Word draws its own gap there: the text starts max(legacyIndent, the label's advance +
  legacySpace) after the label starts, or further where the paragraph hangs further, which can be
  no gap at all ("10.5 mg"). The reader's `suffix` is `tab` only where that rule, from a closed
  table of Word's own font advances, proves a gap of at least the space Word draws after a label
  (569/2048 em) plus 0.2 pt, the rule's agreement with Word's drawing, and only where everything
  else is as Word was recorded drawing it (a whitelist; any other input is `legacy`, which Zone A's
  builder refuses, `list-label`, as before):
  - the label: of characters the table lists, at most four (two in Symbol), in Times New Roman,
    or Symbol but not in bold (Word draws its made-up bold wider), in both Latin slots with no
    other font hint; 16 to 56 half-points; legacySpace 0 to 340 and legacyIndent 0 to 1500
    twips; its level aligned left or not at all; run properties only fonts, size, colour,
    language, `noProof`, bold, italic and their complex-script forms (no character style,
    border, outline, spacing...), set in its level, the paragraph mark or the paragraph's style
    chain, and in the defaults only fonts, sizes and language;
  - its paragraph: outside a table; its properties (its own, its style's and the defaults') only
    those Word drew, each with a value it drew: style, numbering, the mark's run properties;
    aligned left or justified; tab stops only left, clear, `num` or right, from -1985 to 1440
    twips; indents only left and hanging (0 to 1500), right (-29 to 720) and a firstLine of 0,
    hanging at most 360 past the least left; line spacing auto (240 to 480), exact or at least
    (200 to 1200, and no lower than the label), before and after up to 240; keep with next, keep
    lines, contextual
    spacing, page break before (on); widow control (on or off); overflow punctuation, East Asian
    spacing and right-indent adjustment (off); text alignment auto, baseline or centre; outline
    level 0 to 8; clear shading in white or light grey. Its level's own properties only tab
    stops and an indent by left and hanging (and right), or by left and a firstLine of 0.
    Anything else (no `bidi`, no frame, no border, no mirrored indents, no alignment or spacing
    in the level...) is `legacy`;
  - the document: each `docGrid` only a line pitch of 233, 299, 326 or 360 twips (or none),
    every text direction left to right, and either no settings part or one whose compat options
    are one of the eight combinations recorded (the QRD template's, `compatibilityMode` 15 with
    its four other fixed options, `useWord2013TrackBottomHyphenation` 0 or 1, with or without
    `useFELayout` and `doNotUseHTMLParagraphAutoSpacing`), with a `defaultTabStop` of 561, 562,
    567, 708, 720 or 850 and `characterSpacingControl` `doNotCompress`; a settings part without
    them is `legacy`.
  - For a level of any other kind `tab` says what Word writes, not that Word draws a gap.
- A Word 6 level with `w:suff` (with a value or without) is refused (`unsupported-numbering`)
  where a paragraph draws it: Word writes the suffix's character there, and what it draws is not
  on record.
- A numbering part out of the schema's order (picture bullets, definitions, lists, then at most
  one `numIdMacAtCleanup`) is refused. With a `num` before an `abstractNum`, Word numbered every
  list of the case on record as one, where the reader had read each as named.

The independent check (`certify.py`, `conservation-check/1.20.0`) holds its own copy of each rule,
with its own literal copy of the advances, space and margin, held equal to the reader's by a test.
Versions: `docx-reader/1.33.0`, `label-docx-json/1.19.0` (the documented `suffix` values),
`label-epi-json/1.11.15`, `epi-reader/1.3.9`, `word-verifier/1.0.7` (`legacy` is written as a tab;
a Symbol label's space, which Word's text shows as U+F020, is its copy's space).

**Why.** The QRD template's own leaflet dash list uses Word 6 levels, and Zone A refused every
section holding one (`list-label`). The first form of this change (review of #210) reported a
bare `tab` for every Word 6 level from Word's written text alone; the drawn gap was not on record,
so the reader now carries only what Word's drawing proves.

**Evidence (Word 16.113.4 for Mac).**

- `corpus/numbering-cases/legacy-levels` (86 items): Word writes after a Word 6 label exactly what
  `w:suff` says, a tab when it says nothing, for each gap, label, size and `w:legacy` value it
  holds.
- `corpus/numbering-cases/legacy-drawn` (786 rows, `word-gaps.json`): Word saved the case as PDF
  and each page was drawn at six pixels a point (`scripts/word_gaps.py`,
  `scripts/ink_bands.swift`), one row a paragraph on an exact line, label red, text blue. Over 674
  Word 6 rows (dash; Symbol bullet and minus; "1.", "10.", "iii.", "2345.", "6789."; 8 to 28 pt;
  legacyIndent 0 to 1500; legacySpace 0, 144, 340; three indentations) Word's text starts where the
  rule says, to a pixel. The reader names `tab` 489 rows, every one drawn at least a space from its
  text; of the 185 it names `legacy`, Word draws 170 under a space (and the 14 rows of "2345."
  and "6789." are longer than any label drawn at random).
- `corpus/numbering-cases/legacy-drawn-styled` (1,656 rows) and `legacy-drawn-compat-1` to `7`
  (20 rows each): the QRD template's settings and an EMA-like style chain (Times New Roman in all
  four slots, `szCs`, `lang`), in each combination of the three compat options EMA's files vary
  in; and rows with `noProof`, bold, italic and their complex-script forms, eight kinds and places
  of tab stop, justified paragraphs of one line and of two (a justified line before a break is
  stretched), direct, style and level indents, right indents, a level hanging past its left.
  Every condition follows the rule to a pixel but two: a paragraph hanging further than
  legacyIndent moves the text further out (the rule is a floor there), and Word draws Symbol's
  bold wider than the rule (not taken). Over the 3,878 Word 6 rows of the banded cases the
  reader says `tab` 2,785 times, each drawn at least a space wide; of its 1,093 `legacy`, Word
  draws 924 under a space.
- `corpus/numbering-cases/legacy-drawn-styled-bare` (1,656 rows): the styled rows again with no
  settings part; Word draws them as under the QRD template's settings.
- `corpus/numbering-cases/legacy-drawn-sample-0` to `17`: 810 rows drawn at random (seed
  20261008), each on its own page, followed on the next by its label with a space suffix, the
  space Word draws there. What the sampler varies, jointly:
  - every case: the label's characters (one to four in Times New Roman, one or two in Symbol),
    its size (8 to 28 pt), bold, italic, their complex-script forms and `noProof`, legacySpace
    and legacyIndent, the level's and the paragraph's indents and tab stops, the style (none,
    tab stop or indent), alignment (left or justified, of one line or of two), line spacing (auto,
    exact or at least, before and after) and the paragraph's other properties;
  - from case 9, also where each is set: the label's font in its level, the paragraph's mark, its
    style, that style's base or (Times New Roman) the defaults, Symbol in its level or its mark;
    its size, faces, colour, language and `szCs` likewise (its size also from the defaults); the
    mark's own size under a level's (32 rows); the text at a size of its own (154 rows, 8 to 28
    pt); each paragraph property in the paragraph, its style or its base; no line spacing at all
    (the defaults'); a level indented on the right; justified paragraphs of two lines at any
    size; and each case's last five rows plain, legacyIndent 0.2 to 0.5 pt either side of a
    space past the label.
  - Each case is its own document: case 0 with no settings part, 1 to 8 one compat combination
    each (default tab stop 720, no `docGrid`, the defaults 11 pt with no paragraph properties);
    9 to 17 each compat combination again, with the corpus's default tab stops (561, 562, 567,
    708, 720, 850), line pitches (233, 299, 326, 360, none), the defaults' sizes (8 to 14 pt) and
    paragraph properties (none, `after` 160 or 200 with their lines, widow control and East Asian
    spacing off).
  - Of the 810, the reader says `tab` 711 times, each drawn at least as wide as its control's
    space; the 99 others are short of a space by the rule. Of the 45 rows near a space, Word
    draws each within a pixel of the rule against its own space (0.93 px at most): the reader
    says `tab` for the 24 at least 0.2 pt past one, `legacy` for the 21 others. Word's labels
    for cases 9 to 17 (`word.json`) are the reader's, Symbol's mapped where Word names it.
  - Fixed in every drawn row, and so gated: a label's level setting anything but tab stops and
    an indent, or an indent of another shape (`legacy`); a label longer than four characters, or
    two in Symbol (`legacy`: "2345." and "6789." of `legacy-drawn` are no longer taken); Symbol
    from the paragraph's style (`legacy`: Word drew it as from the mark, but names no font on the
    label it writes in, so its label is not on record); the defaults setting a run property
    other than fonts, size and language (`legacy`); an exact or at-least line lower than the
    label (`legacy`); a `docGrid` other than the line pitches above, a default tab stop not
    among those above, or characters other than `doNotCompress` (`legacy`). Fixed and not read:
    the text's font and characters (Times New Roman, "5 mg"), measured against a control with the
    same text, so its glyphs' sides cancel; the label's colour; the page (A4, 0.5 in top and
    bottom, 2 cm left and right) and a single column.
- Every row of every case keeps its ink at least half a point from its band's edges, so no row's
  ink is another's (a two-line justified paragraph with spacing is drawn above its own top: the
  samples stand a page each).
- `corpus/numbering-cases/numbering-num-before-abstract`: the interleaved numbering part.
- Word's answers for the whole numbering-cases set were recorded again under 16.113.4; the 153
  earlier cases answered as under 16.113.3.

**Proof.** `tests/test_word_gaps.py` holds the rule, the space and the reader's suffixes to Word's
drawing; `tests/test_word_oracle.py` the labels and written suffixes; `tests/test_certify.py`, read
through both the reader and the check, each condition of the whitelist on both sides of each bound
and either side of the gap's threshold (no label the whitelist takes is ever exactly on it). The check's mutation record: 4,188 of 4,294 faults killed, 106 recorded as unable to change a result,
none unexplained.

**Impact assessment.**

- **Zone A** (`zone-a/`): the builder (`word_epi.py`) is unchanged; it carries a label followed by a
  tab and refuses `legacy`. The QRD registries and the five ePI label checks move in the reader's
  version names only. `word-drawing/1.2.3`: `zone_a.drawing` locks `label_docx/epi.py`, whose
  epi-reader version moved; its rules do not change. The pinned dev image holds no code (the build
  mounts it), so its digest stands; dev's signed record for `word-drawing/1.2.2` is found under
  that version only, so a dry run answers `recomputed` until dev draws the label again.
- **Certified Word importer** (`src/certified-word/`): 1.3.3, its recompute fixtures and vectors
  moving in the reader's version names only, every outcome unchanged.
- **Stored results** keep the reader version they were read under; nothing earlier is rewritten.

**Measured effect.** EMA's published English Word PI (internal corpus, accepted view, no drawing;
counts only), before and after this change on `920b631`:

|                  | Files | Whole (every section carried) |       Sections carried | `list-label` refusals |
| ---------------- | ----: | ----------------------------: | ---------------------: | --------------------: |
| Leaflets (en-pl) |   286 |                       10 → 14 | 1,455 → 1,526 of 1,666 |               85 → 14 |
| SmPCs (en-smpc)  |   296 |                       18 → 18 | 3,842 → 3,842 of 4,098 |                 7 → 7 |

In 26 leaflets more sections are carried; no file's outcome changed and none lost a section.
Every SmPC entry is unchanged. Reader refusals are unchanged: the new ones (a Word 6 level with a
suffix, a numbering part out of order) stand in none of these files.

Of the Word 6 labels in the documents read whole (87 files), 1,306 of 1,484 are carried (`tab`)
and 178 are not (`legacy`); by level, 100 of 130 are carried in every paragraph, 25 in none. Of
those not carried, 108 stand in documents whose settings are not among those recorded (older
compatibility modes and their options, other default tab stops); the other 70 have a tab stop (24), a run property
(16: a character style, kerning, ligatures, capitals) or another paragraph property (15) outside
those drawn, an indent not drawn (4), no font named (4), a table (4), or a gap Word draws under a
space (3). The narrowing of the last review (where each property is set, the level's own
properties, label lengths, line pitches, default tab stops, character spacing, alignment by
`lvlJc`, lines no lower than the label, Symbol named in the level or the mark) carries the same
1,306 labels as before it: none is lost. (The 90 files and 1,324 of 1,511 recorded before were not
reproduced on the reader before it, which reads 1,306 of 1,484 in 87.)
