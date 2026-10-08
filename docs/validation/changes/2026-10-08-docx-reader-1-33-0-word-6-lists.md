# Recorded change: Word 6 list labels as Word draws them, `docx-reader/1.33.0`, 2026-10-08

_A change to the label reader (`label-docx-reader/`), whose results Zone A's registries, checks and
the certified Word recompute carry. Recorded as for the shared, evidenced libraries in
`docs/validation/README.md`._

**What changed.** The label reader reports what follows a Word 6 list label (`w:legacy`) as Word
draws it, and refuses what Word's drawing does not cover:

- After a Word 6 level's label with no `w:suff`, Word's "convert numbers to text" writes a tab,
  but Word draws its own gap there: the text starts max(legacyIndent, the label's advance +
  legacySpace) after the label starts, which can be no gap at all ("10.5 mg"). The reader's
  `suffix` is `tab` only where that rule, from a closed table of Word's own font advances
  (Times New Roman and Symbol), proves a gap of at least the space Word draws after a label
  (569/2048 em) plus 0.2 pt, the rule's agreement with Word's drawing; else `legacy`, which Zone
  A's builder refuses (`list-label`), as before.
- A Word 6 level with `w:suff` is refused (`unsupported-numbering`): Word writes the suffix's
  character there, and what it draws is not on record.
- A numbering part out of the schema's order (picture bullets, definitions, lists, then at most
  one `numIdMacAtCleanup`) is refused. With a `num` before an `abstractNum`, Word numbered every
  list of the case on record as one, where the reader had read each as named.

The independent check (`certify.py`, `conservation-check/1.20.0`) holds its own copy of each rule.
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
- `corpus/numbering-cases/numbering-num-before-abstract`: the interleaved numbering part.
- Word's answers for the whole numbering-cases set were recorded again under 16.113.4; the 153
  earlier cases answered as under 16.113.3.

**Proof.** `tests/test_word_gaps.py` holds the rule, the space and the reader's suffixes to Word's
drawing; `tests/test_word_oracle.py` the labels and written suffixes; `tests/test_reader.py` and
`tests/test_certify.py` each condition of both copies of the rule, at its exact threshold. The
check's mutation record: 3,936 of 4,042 faults killed, 106 recorded as unable to change a result,
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
| Leaflets (en-pl) |   286 |                       10 → 13 | 1,455 → 1,521 of 1,666 |               85 → 19 |
| SmPCs (en-smpc)  |   296 |                       18 → 18 | 3,842 → 3,842 of 4,098 |                 7 → 7 |

In 28 leaflets more sections are carried; no file's outcome changed and none lost a section.
Every SmPC entry is unchanged. Reader refusals are unchanged: the new ones (a Word 6 level with a
suffix, a numbering part out of order) stand in none of these files.

Of the Word 6 labels in the documents read whole (90 files), 1,275 of 1,511 are carried (`tab`)
and 236 are not (`legacy`); by level, 82 of 134 are carried in every paragraph, 14 in none. Most
of those not carried have bold or italic set in the label's run properties (or their
complex-script forms, `bCs` and `iCs`), which Word's recorded drawing does not cover yet; a few
have kerning or no font named, or a gap Word draws under a space. Recording bold and italic is
the next step.
