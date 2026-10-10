# Nested lists in the certified-Word builder

- Status: proposed, 2026-10-10. A design note: nothing here is built. Recommendation R2 needs an
  owner decision (it amends ADR 0006 decision 6).
- Decides: what `zone_a.word_epi` does with a section whose list paragraphs stand at more than
  one Word list level, today refused whole as `list-level` ("lists at two levels").
- Related: ADR 0006 decisions 3 and 6; `docs/fidelity-normalization.md` §5 (lists) and §7 (the
  certified Word rule); `zone_a.word_epi` (`_lists`, `_list`, `_flow`, `text_labels`, the level
  check in `_section`); `zone_a.drawing` (markers in order, labels written as text read as the
  start of their line); `zone-a/scripts/scoreboard.py`.

## Summary

Every nested list in the corpus's refused sections is a bulleted one, and none uses the markers
an HTML list draws at depth two or three (`◦`, `■`). So no refused section can be written as a
nested HTML list that Chrome draws as Word does. 0 of the 43 nested runs could, and that holds
before the FHIR and stylesheet limits on `ol` even come into play.

What does carry them exactly is decision 6's existing form: each item is a `p` of its label, a
space and its text. That form already passes the scanner, the page and the drawing check. It
loses indentation, which no check proves today. It keeps meaning where the label alone tells an
item's level. That holds when no family of labels (the disc bullets, the dashes, the squares,
"o"...) is used at two levels of one section.

On the EMA English cuts at main `eafd7e2a`, this carries 14 of the 42 `list-level` sections:

- SmPC whole files go from 21 to 22;
- leaflet whole files go from 20 to 24;
- converted characters go up by 50,605, from 15.76% to 15.89% of the corpus.

Headless Chrome drew all 14 as read. The other 28 sections stay refused:

- 17 use one label family at two levels, which only indentation tells apart;
- 11 are refused for something else as well.

## 1. What occurs in the corpus

Measured with `scoreboard.py coverage` (docx-reader 1.34.0, word-epi 1.7.0, fidelity-norm 3.7.0,
SmPC and leaflet cuts, 296 + 286 files) and a classification script kept in the scratchpad. The
counts below are counts only, with no names and no text. A section is counted when `list-level`
is among its blockers:

|                                | SmPC | PL  |
| ------------------------------ | ---- | --- |
| Files                          | 11   | 16  |
| Sections                       | 11   | 31  |
| `list-level` the first refusal | 6    | 25  |
| `list-level` the only blocker  | 6    | 25  |

A _run_ is a sequence of consecutive list paragraphs in one flow (outside tables, or one table
cell) with no unlabelled paragraph between them. A _level change_ is two consecutive list
paragraphs of one flow at different Word levels (`w:ilvl`). It is _adjacent_ when no unlabelled
paragraph stands between them.

**Level structure** (sections):

| Pattern                                                         | SmPC | PL  |
| --------------------------------------------------------------- | ---- | --- |
| Word levels {0, 1}                                              | 7    | 25  |
| Levels {0, 2}                                                   | 1    | 5   |
| Levels {0, 1, 2}                                                | 2    | 1   |
| Levels {0, 3}                                                   | 1    | 0   |
| Levels not contiguous (a level skipped in the section)          | 2    | 5   |
| Nesting: at least one adjacent level change                     | 10   | 24  |
| Separated only: levels change only across text or between flows | 1    | 7   |
| A run that starts deeper than it later goes (outdent first)     | 1    | 10  |
| An adjacent step of two levels (0 → 2)                          | 0    | 3   |
| A run of three levels                                           | 0    | 1   |

**Label kinds per level** (the 43 nested runs, outermost level first). "o" is the letter Word
draws for its default second-level bullet, not U+25E6:

| Labels by depth                                     | SmPC | PL  |
| --------------------------------------------------- | ---- | --- |
| `•` → `o`                                           | 4    | 12  |
| `•` → `•`                                           | 4    | 3   |
| `•` → `-` or `−`                                    | 0    | 5   |
| `-` → `▪`                                           | 0    | 4   |
| `▪` → `▪`                                           | 0    | 3   |
| `▪` → `o` (one run mixes `▪` into its second level) | 0    | 2   |
| `−` (U+2212) → `-`                                  | 2    | 0   |
| `-` → `-`                                           | 1    | 2   |
| `•` → `o` → `▪`                                     | 0    | 1   |
| Any numbered level ("1.", "a)", "i.")               | 0    | 0   |

**Other patterns** (sections):

| Pattern                                                                                | SmPC | PL  |
| -------------------------------------------------------------------------------------- | ---- | --- |
| Numbering that continues or restarts across a nested sub-list                          | 0    | 0   |
| Bullet and numbered levels in one section (in separate lists, the numbers consecutive) | 0    | 1   |
| A level change inside a table cell                                                     | 0    | 2   |
| Items with several paragraphs (text between items of two levels, nesting present)      | 3    | 4   |

Every label in the nested runs is followed by a tab.

## 2. What is exact as a nested HTML list

The candidates were drawn by headless Chrome 154.0.8037.98, through `label_docx.browser`'s
`browser_markers` and `browser_sections` (the drawing check's own oracle, default style sheet).
They were also run through the §5 scanner and the official validator (validator_cli 6.10.4,
R5 core).

**(a) FHIR and §5.**

- FHIR's txt-1 allows a `ul` or `ol` inside an `li` (HTML 4's lists chapter). The validator
  passes nested `ul`, an `ol` in a `ul`'s item, and an `li` holding several `p`.
- The validator refuses `start` and `type` on `ol` ("Invalid attribute name"), as ADR 0006
  recorded.
- The validator passes an inline `style` with `list-style-type` (`circle`, `lower-alpha`, and
  even a string value). It does not read the CSS.
- §5 already accepts nesting as it stands: `li` content is unrestricted, and at most six `ul`,
  `ol`, `blockquote` and `dd` may be open at once. Every candidate below scanned. §5 refuses
  `style` on anything but `span` (`forbidden-attribute`).

**(b) What Chrome draws** (the markers in document order, read from the accessibility tree):

| Narrative                                          | Markers                                                                         |
| -------------------------------------------------- | ------------------------------------------------------------------------------- |
| `ul > ul`                                          | `• `, `◦ `                                                                      |
| `ul > ul > ul`, and deeper                         | `• `, `◦ `, `■ ` (U+25A0), `■ `                                                 |
| `ol > ol`                                          | `1. `, then `1. 2. ` inside, and the outer list goes on at `2. `                |
| `ul > ol`, `ol > ul`                               | the `ol` decimal from 1 in each; the inner `ul` `◦ ` (any list ancestor counts) |
| A `ul` in a table cell inside a list item          | `◦ `: depth counts through the cell                                             |
| `li` holding `p`, `p`, `ul`                        | `• ` before the first line, `◦ ` before the sub-item                            |
| An empty `li` holding a sub-list (to start deeper) | `• `, `◦ `: an extra marker on a line of its own                                |
| `ul style="list-style-type: disc;"` nested         | `• `, `• `                                                                      |
| `ol style="list-style-type: lower-alpha;"` nested  | `a. `, `b. `                                                                    |

So, without `style`, a nested HTML list draws Word's labels exactly only for these:

- bullets `•`, `◦`, `■` by depth;
- decimal sub-lists that restart at 1 under every parent;
- an outer `ol` whose numbers run on across its items' sub-lists.

It cannot draw:

- `o`, `▪`, a dash, `a)` or `(i)`;
- the same `•` at two depths;
- a sub-list numbered on from one parent to the next (that needs `start`);
- a run that starts deeper than it goes (the empty `li` draws an extra marker, which the
  drawing check's marker comparison would catch);
- a level jump.

The corpus has none of the drawable shapes: **0 of 43 nested runs**.

ADR 0006 decision 6 records that EMA's stylesheet draws every `ul` with discs. If so, EMA's
viewer would draw a nested `ul` as `•` where Chrome's default draws `◦`. That would be a viewer
residual the drawing check (default style sheet) cannot see. EMA's own published ePIs nest lists
in 12 of the 111 in `label-docx-reader/corpus/ema-epi`. Of their 21 nested lists, 16 are
`ul > ul` with no style and 2 are `ul > ul` with an inline `list-style-type: circle`.

**(c) The page and the drawing check.** For the drawable shapes, the scanner's text and the §7
page agree without change:

- an `ol` item's marker is emitted as the page writes Word's label;
- a `ul` marker is emitted as nothing, and §3 step 4 removes `•`, `◦` and `■` (U+2022, U+25E6,
  U+25A0) from the page's line starts.

The drawing check already compares every marker in document order, so a nested `ul` whose Word
label is `o` or `▪` would be refused `drawn-otherwise`, never carried wrong. Nesting itself
(indentation) is compared by neither check, here as today.

**Rejected widenings.**

- _Allowing `style="list-style-type: ..."` on `ul` and `ol`_ is FHIR-valid and is EMA's own
  practice. It would draw `•` at depth two, and `a.` or `i.` numbering. It still cannot draw
  `a)`, `o`, `▪` or a dash, and it unlocks 0 corpus sections: the two adjacent `•` → `•` runs
  both start deeper than they go.
- _A string `list-style-type`_ draws any glyph, but it puts text in an attribute value, which §5
  exists to forbid.
- _Taking Word's `o` as `◦`_ is a glyph substitution. It would change how 16 runs are drawn
  (indented markers instead of text), but would unlock nothing that R2 does not.

## 3. What to do with each pattern

**R2, labels written as text, where each label family keeps to one level.** A section with list
paragraphs at two or more levels is carried when both of these hold:

- every list label in it belongs to a closed family;
- no family occurs at two Word levels of the section.

Each run that holds two levels is then written whole as decision 6 writes a list HTML cannot
draw: every item a `p` of its label, U+0020 and its text. Its `•` items are written that way too,
never as a `ul`, so a sub-item is never drawn to the left of its parent. A run of one level is
written as today.

The families are a closed list, judged as a reader sees them, not by code point:

- disc: U+2022, U+25CF;
- circle: `o`, U+25E6, U+25CB;
- square: U+25AA, U+25A0, U+25AB, U+25A1;
- dash: U+002D, U+2010 to U+2014, U+2212;
- decimal (any punctuation);
- lower-case letters, alphabetic and Roman together ("i." is either);
- upper-case letters likewise.

A label outside every family refuses a multi-level section.

Meaning is preserved because each item's level is a function of its label. A reader of the
lines "• A", "o B", "o C", "• D" recovers that B and C belong under A, which is what Word's
indentation says. Two things are lost:

- the indentation itself, which no check proves today;
- the hanging indent of a wrapped item, as with decision 6 today.

| Pattern                                                                                 | Sections (SmPC / PL)   | Recommendation                                                                                                                                                     | Owner decision              |
| --------------------------------------------------------------------------------------- | ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------- |
| Nested, a distinct family per level (`•`→`o`, `•`→dash, dash→`▪`, `▪`→`o`, `•`→`o`→`▪`) | 2 / 8 carried          | R2: labels as text                                                                                                                                                 | **Yes** (amends decision 6) |
| Separated (levels change only across text), a distinct family per level                 | 0 / 3 carried          | R2, the same rule section-wide                                                                                                                                     | **Yes** (same decision)     |
| Starts deeper, or jumps a level, with distinct families                                 | 0 / 1 carried (a jump) | R2: the label carries the level, so no empty item is needed                                                                                                        | **Yes** (same decision)     |
| One family at two levels: `•`/`•`, `-`/`-`, `−`/`-`, `▪`/`▪`                            | 4 / 13                 | Keep refusing: only indentation tells the levels apart. The refusal names the paragraph, so the label team can fix the source with a distinct bullet per level     | No                          |
| Nested HTML lists (§2)                                                                  | 0 / 0                  | Do not build now. Revisit if a corpus run shows Word labels Chrome draws (`◦`, decimal restarting)                                                                 | No                          |
| Numbered levels, numbering continued or restarted                                       | 0 / 0 nested           | Covered by R2 if they occur: the labels are Word's own numbers, so continuation is exact by construction. One family at two levels ("1." under "1.") stays refused | With R2                     |
| Level change in a table cell                                                            | 0 / 2                  | R2 applies. A `•` written as text in a cell stays refused (decision 6), and both sections are refused for other causes too                                         | No                          |
| Items with several paragraphs                                                           | 3 / 4                  | Unlabelled paragraphs stay `p`, as today. Under R2 a continuation paragraph is not attached to an item, which the read cannot do without indentation               | With R2                     |

The owner decision asked for is this: _amend ADR 0006 decision 6 so that a section with list
paragraphs at more than one level is carried, as labels written as text, where each closed label
family keeps to one level. The families are as listed above._ The family list is part of the
decision, because "a reader can tell these apart" is a judgment, not a measurement.

## 4. Effect and cost

These are measured, not estimated. The prototype patched `_lists` and the level check in a
scratch copy. It built every section with `list-level` among its blockers, checked the scanner
against the page, and ran `zone_a.drawing.check` in Chrome 154:

|                                                                                             | SmPC    | PL      |
| ------------------------------------------------------------------------------------------- | ------- | ------- |
| Sections carried and drawn as read                                                          | 2       | 12      |
| Their paragraphs written as labels' text                                                    | 16      | 113     |
| Whole files                                                                                 | 21 → 22 | 20 → 24 |
| Characters converted                                                                        | +11,827 | +38,778 |
| Still `list-level` (one family at two levels)                                               | 4       | 13      |
| Refused for another cause (`tab`, `formatting`, `picture`, `anchored-object`, `list-label`) | 5       | 6       |

Taken by code point instead of by family, the rule would carry 2 more SmPC sections (`−` over
`-`) and no more whole files. The family rule refuses them.

Lifting `list-level` entirely would make at most PL 28 and SmPC 22 whole. So the same-family
refusals cost at most 4 leaflet files.

**Cascade for R2.**

- `zone_a.word_epi`, at word-epi/1.8.0:
  - `_lists` takes a run at two levels as one block, before its split by `num_id`;
  - `_flow` and `text_labels` write that block as text labels (both go through `_lists`, so
    the drawing check's heads follow);
  - the level check becomes the family check;
  - about 40 lines;
  - tests: `test_a_list_that_changes_level_or_misses_a_number_is_refused` and
    `test_one_list_level_in_a_section` change, since "1." over "•" is now carried.
- `zone_a.drawing`: no logic change. Its docstring ("the builder refuses two levels") and
  `DRAWING_VERSION` (a patch) change, since what it compares as text labels grows.
- `scoreboard.blockers` re-implements the level check and must mirror the family rule, or the
  unlock ranking misleads.
- `fidelity-normalization.md` §7: in the certified Word rule's refusals, "two list levels in the
  section" becomes "one label family at two list levels", and the text-label sentence gains the
  multi-level run. §5 and the scanner are unchanged. This is a minor version (the next free one,
  3.8.0, with the withheld-section design moving on as at each release). Per §8 it brings:
  - every norm constant (TS, Python, agent) and every lock that embeds it, as at 3.7.0
    (registry, qrd-check, the structurers, product, the authority and certified-Word
    importers);
  - the vectors regenerated (`contracts:check`): only version-bearing hashes move in the TS
    golden vectors;
  - two new certified-Word verify vectors (a carried two-family run, a refused same-family
    section);
  - a change record, the ADR 0006 amendment and the version in `AGENTS.md`;
  - re-approval of anything approved under 3.7.0.
- Operations: dev's drawing record re-drawn. The agent redeploy waits on the owner's hold. No
  reader change, so no mutation record.

## 5. Risks and near misses to test

- One family at two levels, refused:
  - `•` over `•` (one `num_id` and two);
  - `●` over `•`;
  - `−` over `-`;
  - `o` over `◦`;
  - `▪` over `■`;
  - "1." over "1)";
  - "i." over "a.";
  - the same in two separated lists of one section.
- A glyph outside every family in a multi-level section, refused (`➢` over `►`).
- A run at two levels whose `•` items would have made a `ul`: the narrative has no `ul` for it,
  and `text_labels` (the drawing check) names every item of it, including across a `num_id`
  change inside the run.
- A dash label at level 1 before an item that begins with a number: still `list-label` (`joins`).
- A run at two levels in a table cell with a `•` item: still `list-label`.
- A run that starts at level 1 and outdents to 0, and a jump from 0 to 2: carried by R2, with
  labels and text in order.
- A level whose label is empty (Word draws nothing): it is no list paragraph, here as today.
- Numbered levels: "1.", "2." at 0 with "a)" sub-items between: written as Word's labels, with
  no `ol` that would restart at 1. A sub-list numbered on across parents ("a, b", then "c")
  likewise.
- The drawing check over every case above, in Chrome. A label written as text must read as the
  start of its line, never as a marker.
- The scanner against the page for every case. `•` written as text at a line start is removed by
  §3 step 4 on both sides; `o`, `▪` and dashes are not, on either side.
