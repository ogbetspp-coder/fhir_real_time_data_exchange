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
  - the label: of characters the table lists, in Times New Roman, or Symbol but not in bold
    (Word draws its made-up bold wider), in both Latin slots with no other font hint; 16 to 56
    half-points; legacySpace 0 to 340 and legacyIndent 0 to 1500 twips; its level aligned left
    or not at all; run properties only fonts, size, colour, language, `noProof`, bold, italic
    and their complex-script forms (no character style, border, outline, spacing...);
  - its paragraph: outside a table, aligned left or justified, with no `bidi` (in the paragraph,
    its style or its level) and no frame; tab stops only left, clear, `num` or right, from -1985
    to 1440 twips; indents only left and hanging (0 to 1500), right (-29 to 720) and a firstLine
    of 0, hanging at most 360 past the least left;
  - the document: no character grid, every text direction left to right, and compat options one
    of the eight combinations recorded (the QRD template's, `compatibilityMode` 15 with its four
    other fixed options, `useWord2013TrackBottomHyphenation` 0 or 1, with or without
    `useFELayout` and `doNotUseHTMLParagraphAutoSpacing`) or none. No other setting is read: the
    QRD template's `defaultTabStop` (720) and `characterSpacingControl` stood in every recorded
    case with settings, and no tab stop moved Word's gap.
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
  rule says, to a pixel. The reader names `tab` 503 rows, every one drawn at least a space from its
  text; of the 171 it names `legacy`, Word draws 170 under a space.
- `corpus/numbering-cases/legacy-drawn-styled` (1,656 rows) and `legacy-drawn-compat-1` to `7`
  (20 rows each): the QRD template's settings and an EMA-like style chain (Times New Roman in all
  four slots, `szCs`, `lang`), in each combination of the three compat options EMA's files vary
  in; and rows with `noProof`, bold, italic and their complex-script forms, eight kinds and places
  of tab stop, justified paragraphs of one line and of two (a justified line before a break is
  stretched), direct, style and level indents, right indents, a level hanging past its left.
  Every condition follows the rule to a pixel but two: a paragraph hanging further than
  legacyIndent moves the text further out (the rule is a floor there), and Word draws Symbol's
  bold wider than the rule (not taken). Over the 2,318 Word 6 rows of all nine cases the reader
  says `tab` 1,679 times, each drawn at least a space wide; of its 639 `legacy`, Word draws 561
  under a space.
- `corpus/numbering-cases/numbering-num-before-abstract`: the interleaved numbering part.
- Word's answers for the whole numbering-cases set were recorded again under 16.113.4; the 153
  earlier cases answered as under 16.113.3.

**Proof.** `tests/test_word_gaps.py` holds the rule, the space and the reader's suffixes to Word's
drawing; `tests/test_word_oracle.py` the labels and written suffixes; `tests/test_certify.py`, read
through both the reader and the check, each condition of the whitelist on both sides of each bound
and at the gap's exact threshold. The check's mutation record: 4,090 of 4,196 faults killed, 106 recorded as unable to change a result,
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

Of the Word 6 labels in the documents read whole (90 files), 1,330 of 1,511 are carried (`tab`) and
181 are not (`legacy`); by level, 104 of 134 are carried in every paragraph, 26 in none. Of those
not carried, 117 stand in documents whose compat options are not among those recorded (older
compatibility modes and their options); the other 64 stand in a table, have a character style,
kerning or ligatures on the label, a tab stop or indent outside the recorded ones, no font named, or
a gap Word draws under a space.
