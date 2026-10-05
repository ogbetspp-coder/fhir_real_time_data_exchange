# Design note: the CCDS implementation check

- Status: Built (`implementation-check/1.1.0`, `zone-a/src/zone_a/implementation.py`); tested on synthetic documents
  only. No company CCDS or local label is in the repository.
- Date: 2026-10-05; 1.1.0 adds wording in other languages and deadlines
- Related: the UI proposal (Claude Docs, "Label Intake — UI proposal"), `zone-a/src/zone_a/certified.py`
  (the reads it works on), `docs/design/qrd-conformance-check.md` (the other check on certified reads)

## Why

A labeling team's first question is which markets have not carried a change to the company core
data sheet (CCDS) into their labels, and how late they are. Today that is answered from trackers:
records people fill in. Failing to keep reference safety information current was the most common
critical finding in MHRA's 2013–14 pharmacovigilance inspections (42%). This check answers the
question from the labels' own text, read exactly by the label reader, and answers only what the
text shows: wording that differs is for a person to judge, never matched by resemblance.

## What it does

1. **Find what changed.** Two certified reads of the CCDS are compared paragraph by paragraph and
   then word by word. Each change is a run of unchanged, inserted and deleted segments that gives
   back both texts character by character, or the comparison fails. Same text with other marks
   (bold, superscript) is reported as a `formatting` change, never dropped.
2. **Turn each change into wording to look for.** Each run of changed words, with four words of
   context on each side, is an edit with its `old` and `new` wording. The wording must occur
   exactly once in its own CCDS: the context grows until it does, and an edit that cannot be made
   unique is not checked (`ambiguous-wording`).
3. **Judge each label by its text.** For each label and edit: `implemented` (new wording there,
   old not), `pending` (old there, new not), `both`, `absent` (neither: a local wording, a
   deviation or a change that does not apply, for a person to decide) or `not-checked` with the
   reason (`language`, `refused`, `refused-part`, `ambiguous-wording`). Every place a wording is
   found is reported as a paragraph index and character offset. A wording that would match if its
   spaces were plain spaces is noted (`spacing`) but not matched.

The module docstring of `zone_a.implementation` states every rule exactly.

## Run it

```
cd zone-a
.uv-bootstrap/bin/uv run --frozen python scripts/check_implementation.py OLD.docx NEW.docx labels.json --out report.json
```

`labels.json` lists the labels, each `{"file": "...", "language": "en"}` and optionally its
`"market"` and `"due"` date, as .docx or ePI Bundles. The report names the check's version, the
reader and format versions it read with, and the SHA-256 of both CCDS files, and it ends with a
summary per edit: labels by status, how many are late, and in which markets.

### Other languages

```
.uv-bootstrap/bin/uv run --frozen python scripts/check_implementation.py OLD.docx NEW.docx --template de,fr --out wordings.json
```

writes a file with each edit's CCDS wording and an empty old/new pair per language. Affiliates
fill in their language's wording; `--wordings wordings.json` then checks their labels against it.
The file is refused whole when anything in it is wrong: an edit these CCDS versions do not have,
a reference wording from other versions, a missing or extra side, an empty or unchanged wording.

### Deadlines

`--as-of YYYY-MM-DD` is the day lateness is counted on, and it is required once any label has a
date: it is never read from a clock, so the same inputs give the same report. A label past its
date is late unless it is `implemented`; `pending`, `both` and `absent` all lack the evidence. A
`not-checked` label's lateness is unknown.

## Limits, on purpose

- **Translations are given, not inferred.** A label in another language is checked only against
  the wording its affiliate (or the EMA's published PRAC wording) gives for that language.
- **Within one paragraph.** A wording split over two paragraphs is not found.
- **Text, not formatting.** A label with the right words in the wrong formatting is
  `implemented`; formatting changes in the CCDS are listed for a person.
- **Deletions are never `implemented`.** The text alone cannot tell a removal from a label that
  never had the wording.
- **Body text only for .docx.** The certified read refuses footnotes and page numbers in the body
  (`note-reference`, `page-number`); such a label is `not-checked`.

## Next

- An endpoint in the gateway, so the Changes screen reads this report (a plan to approve first:
  the gateway is a new deployed service).
