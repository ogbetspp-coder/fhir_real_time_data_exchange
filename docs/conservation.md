# Conservation: what is proved for every document, and how

This page states what the service proves about each document it ingests, what that proof rests
on, and what it does not cover. The code is `src/label_docx/certify.py`; the tests are
`tests/test_certify.py` and `tests/test_robustness.py`; the requirements are R-24 to R-27 in
`docs/requirements.md`.

## The statement

For a document `D` and the result `R` the reader made of it, the certificate asserts:

> **R's text is D's text.** Paragraph by paragraph, in document order, the output text is the
> sequence of D's text tokens with the set-aside tokens removed, each remaining token replaced
> by its one character under a fixed table. Every token of D is used exactly once: in the output
> or set aside, never both, never neither.

Written out:

- Let `S = s₁ s₂ … sₙ` be D's text tokens in document order, as `certify.py` reads them with its
  own parser. For a .docx a token is one character of a text element (`w:t`, or `w:instrText`
  for a field's code) or one element that stands for a character (`w:tab`, `w:br`, `w:sym`, a
  picture...). For an ePI it is one character of the section's HTML text, a `br` or an `img`.
- Let `A ⊆ S` be the tokens set aside. Each has a reason from a closed list, and the check works
  the reason out from D itself, not from the reader:
  - a field's instruction: the code, which is never shown;
  - a page number: set by the page layout. Its place must appear in the paragraph's `pages`;
  - a page or column break: layout;
  - for an ePI, whitespace beyond the one space CSS draws for a run of it, and a line break
    ending its paragraph.
- Let `φ` map a token to its character: a text character to itself (or through the closed
  Symbol table where the run is in the Symbol font; see "Nothing is left to the reader's
  choice" below), `w:tab` to U+0009, `w:br` to U+000A, a picture to U+FFFC, and so on.

Then the check verifies:

1. **Sequence.** For every paragraph `p`, `text(p) = φ(sᵢ) φ(sⱼ) …` over the tokens of `p` not
   in `A`, in order. This is equality of strings, checked character by character.
2. **Structure.** The paragraphs are D's paragraphs, in D's order and in D's table cells. Note
   marks and page-number places stand where D has them. Notes and ePI sections are D's, with
   D's titles. An ePI result counts its refused sections truthfully.
3. **Ledger.** The counts balance, and the certificate states them:
   `|text| + |instructions| + |elements| = |output| + field code + page numbers + page breaks +
   hidden whitespace`. For an ePI: output = text characters (non-whitespace) + spaces + line
   breaks + pictures.

If any of these fails, the result is not served. The document is refused with the code
`uncertified`, and the refusal says where the check failed.

## Why this rules out a lost, added or changed character

Equality of sequences is all-or-nothing. Any of the following makes (1) false, or (2) for the
structural ones:

- a dropped, added, changed, repeated or swapped character;
- a character moved to another paragraph;
- a paragraph dropped, repeated, swapped, merged or split;
- a page number or note mark moved.

There is no edit too small to show, because nothing is compared approximately.

`tests/test_certify.py` holds the check to this. Over every corpus document the readers read
(186: 78 Word documents and 108 ePIs), it applies 16 kinds of change at places a seeded
generator chooses. That is 9,192 changed results, and every one is refused. While the test
was being written it found two kinds of change the first version of the check let through: an
ePI paragraph moved to another table cell, and a section emptied and called refused without the
count changing. Both are now checked. Every unchanged result is certified, with a balanced
ledger.

## What the proof rests on

The check is independent of the reader:

- It reads the source bytes again with its own parser and its own walk. For an ePI that is the
  standard library's HTML parser, where the reader uses an XML parser.
- It does not resolve styles, compute labels or verify fields, and it shares no code with the
  reader. It shares one piece of data: the 49-entry Symbol table, which is held to Microsoft
  Word.
- It treats the reader's result as untrusted. A wrong result can only fail the check; it cannot
  make the check pass.

What has to be right for the proof to hold:

- the check's own walk of the source (`certify.py`, small enough to review line by line);
- Python's `zipfile` and `html.parser`. Python's XML parser is no longer a single point: the
  text of every part the check reads is read a second time by a tokenizer written in the check,
  without any XML library (`_raw_texts`: line ends, references, CDATA, comments, namespace
  prefixes as XML defines them). The two readings must agree element by element, or the read
  is not certified (R-34).

A common-mode error would need both the reader and the check to skip the same text in the same
place. The check guards against that in four ways:

- It counts every text element in any namespace (`w:t`, DrawingML `a:t`...) inside a paragraph.
- It refuses any run content it does not know.
- It refuses a picture that holds text (a text box, or WordArt, whose text sits in an
  attribute).
- It lists every other part of the package that holds text.

## Nothing is left to the reader's choice

Every token has exactly one reading, and the check works it out from the document alone. Two
readings depend on a run's formatting:

- **Symbol font.** A run whose `ascii` and `hAnsi` fonts are both Symbol has its text read
  through the Symbol table (`symbolMapped` counts the characters). A run with Symbol in only
  some of its font slots is never certified, because Word chooses the font character by
  character.
- **Hidden.** Hidden whitespace is left out (`hiddenWhitespace`). Hidden text with characters to
  show is never certified.

The check decides both itself, by Word's precedence, written in `certify.py` and not shared
with the reader. The levels are taken in order: the run's own properties, its character style,
its paragraph style, the innermost table's style (each with its `basedOn` chain, and a missing
style meaning the default one), then the document defaults. A font is set by the first level
that names it, and a theme reference resolves to the theme's typeface. A run is hidden if it
says so itself, or, if it is silent, if any level says so. The tests hold each rule
(`test_the_font_is_the_nearest_levels_by_word_precedence`,
`test_hidden_is_the_runs_own_setting_first_then_any_style`). That these rules are Word's is
held to Word itself (`tests/test_word_oracle.py`).

Up to conservation-check/1.0.0 the check allowed both readings of these two cases and counted
which was taken. Since 1.1.0 there is one reading, and so no choice at all: **for every
certified document, the output text is a function of the source alone.**

## The key marks, worked out twice

For a Word document, the check also works out on its own, run by run, the marks that most
change what a label says: bold, italic, capitals, small capitals, strike, double strike,
superscript, subscript and underline (`CHECKED_MARKS`). It uses Word's rules, written in the
check apart from the reader's:

- a run's own setting wins;
- otherwise each kind of style gives its nearest setting, and the kinds cancel in pairs;
- the document defaults then switch a mark on.

Each output character's marks of these kinds must be the check's, or the read is not certified
(R-35). So two separate implementations agree on every document, and the rules they share were
Word's own answers to test cases. On the corpus and on 3,000 generated documents built to mix
every rule (`scripts/fuzz_docx.py`), the two never disagreed. The certificate names the marks it
checked (`marksChecked`). Highlight, shading, faint and raised text are held to Word alone.

List labels and note marks are worked out twice in the same way. The check counts every list
and every footnote and endnote by Word's rules, written apart from the reader's, and each
paragraph's label and each note's mark must be the check's, or the read is not certified
(R-36). The check's own labels and marks are also held directly to Word's recorded answers:
388 labels and 84 note marks in 86 corpus documents, all Word's.

## Beyond the corpus: generated documents

A corpus holds what authors happened to write. Generated documents explore what they could
write:

- **ePI.** `scripts/fuzz_epi.py` builds sections at random from every element, attribute and
  style the reader accepts, with every kind of whitespace and the signs labels hold. Chrome
  shows each one the reader reads, and the two are compared.
  - The first 2,000 found four classes of mark drawn otherwise than Chrome draws them. None was
    in the text and none was in the real ePIs.
  - All four were fixed. Since then, more than 30,000 generated sections have shown no
    difference.
  - 3,000 of them, with Chrome's answers, are held in the tests without a browser (R-33).
- **Word.** `scripts/fuzz_docx.py` builds documents mixing style chains, toggles, lists and
  notes. The first batch Word judged found rules the corpus never exercised. 8 of the first 11
  documents differed: in bold and italic under document defaults, in the default character
  style, and in list restarts shared between lists. Each was shrunk with Word as referee to a
  minimal case and kept in `corpus/numbering-cases`:
  - styles that differ from the defaults turn a toggle over;
  - Word applies no default character style;
  - a restarted level takes the restarting list's override;
  - three restart settings Word draws in ways it does not document are refused.
  The next 30 generated documents: 15 agreed, 12 were refused, 3 differed. After the fixes, the
  3 agree or are refused. Batches since, each with fresh documents:
  - 30: 15 agreed, 15 were refused;
  - 40: 35 agreed, 5 were refused;
  - 60: 50 agreed, 9 were refused, 1 differed. Shrunk with Word as referee and put to Word in
    107 variants, it showed one more rule: a level first reached through a deeper item shows
    its definition's start, but counts on from its list's start override, which stays unused
    (`override-implicit-continued`, `-reused`, `-levels`). Each document whose labels the fix
    changed was put to Word again, and all 12 agree;
  - 60 more: 56 agreed, 4 were refused, none differed;
  - 60 more: 53 agreed, 6 were refused, 1 differed. Shrunk inside the document, it needed a
    table: a level restarted by another list's paragraph takes that list's start override in
    the same row, but not in a later row of the same table (`restart-source-cells`,
    `restart-source-rows`). Word did not show a rule the reader could rely on. So the reader now
    refuses such a level once any table row has ended since the restart. That is stricter than
    Word: it also refuses two layouts where Word keeps the override. No reading changed; 9 of
    580 earlier generated documents are now refused;
  - 60 more, made with tables of several rows: 40 agreed, 18 were refused, 2 differed. Put to
    Word in 18 variants, they showed two more ways a table row changes Word's count:
    - a level that never restarts, counted after a higher paragraph and a row's end, is drawn
      one less in some tables (`restart-never-rows`);
    - a level counted on from another list's override through a deeper paragraph loses it in
      some tables (`override-implicit-rows`).
    Word showed no rule for which tables. So the reader refuses both once a row has ended. No
    label changed in any generated document. 26 of 580 earlier ones are refused in all, and 32
    of these 60. Real and template documents: no reading changed.
  - Every one the reader reads must be certified, key marks included (R-35).
  - Word's own judgment of them (`word_oracle.py compare`) runs in batches on a Mac with Word,
    since Word takes up to a minute a document.

## Strict serving

With `--browser require` or `--word require`, the service serves no read until Chrome or Word has
checked that very document and agrees (R-32). Every answer it then gives has been held, for
that document, to the application that displays it. Without them, every answer is still
certified (R-24), and the marks above are cross-checked.

## What it does not cover

The proof covers the text, not how it is interpreted. The interpretation is held to the
applications themselves:

- **An ePI's marks** (bold, italic, superscript, colour...): held to Chrome for **every ePI
  ingested**, where Chrome is installed. The service has Chrome show the document and compares
  every section, character by character and mark by mark, with the result; a result Chrome shows
  otherwise is never served (R-29, `tests/test_browser_verify.py`). Every corpus ePI is held
  to Chrome as well (`tests/test_browser_oracle.py`).
- **A Word document's marks, list labels and note marks**: held to Microsoft Word for every
  corpus document (`tests/test_word_oracle.py`), and for **every document ingested** on a Mac
  with Word when the service runs with `--word on`. A result Word shows otherwise is never
  served (R-31, `tests/test_word_verify.py`). Word's rules are not published, so this is evidence by
  example, the strongest available, not a proof.
- **An ePI's list numbers and bullets**: the reader draws each item's marker by the
  browser's rules, and every one is held to the marker Chrome draws, read from its
  accessibility tree: for every corpus ePI, and for every ePI ingested where Chrome is
  installed.

The scope is what the reader reads:

- a .docx's body, footnotes, endnotes, headers, footers and comments (a header, footer or
  comment the reader refuses on its own is listed in the certificate under `refused`; the
  result then says so, and holds no text for it);
- an ePI's section titles and divs.

Text elsewhere (the glossary, footnote continuation notices, other narratives in an ePI Bundle)
is not read. The certificate lists it under `notRead` with its size, so nothing is left out
without being named.

## Who checks the checker

The proof is only as good as the check, so the check's tests are themselves tested.
`scripts/mutate_checker.py` makes one small fault at a time in a copy of `certify.py`: a
comparison turned round, `and` made `or`, a number one off, a string changed, a statement or a
refusal removed. It then runs the check's tests against each faulty copy. A fault the tests
catch is "killed". A fault that survives is either a missing test, which is then added, or a
change that cannot alter what the check does, recorded with the reason in `EQUIVALENT`.
The tests are first run on the check as it is, and must pass. One run showed why: after a change
to a test, a test failed on the unaltered check, so every fault looked caught (1,091 of 1,091).
The script now stops rather than record such a run.
`tests/test_checker_mutants.py` holds the record (`docs/checker-mutants.json`) to the code:

- the record must be the run of the current `certify.py`;
- every survivor must have its reason;
- more than 90% of all faults must be killed outright.

The current run made 1,480 faults. The check now also draws every list label and note mark
on its own. The tests killed 1,423 of the faults (96%). The other 57 cannot change what the
check does, and each is recorded with its reason: for example, the `xml` prefix, which can never
name Word's namespace, a length compared just before, or a tenth place for list levels, which
run 0 to 8. None is unexplained.

The first run of this check killed 1,309 of 1,480. Most survivors traced to one gap: the
corpus tests held only the documents the check certified, so a check that wrongly refused a
document went unnoticed. Every corpus document must now be read or refused exactly as locked.
The rest needed tests of list settings no corpus document uses (a number after a space,
legal numbering, Symbol bullets through a style, numbers past "z" or 3999). A few needed small
changes to the check that left nothing to argue: dead code removed, and a placeholder mark
dropped. A run now takes about 1.5 hours, so the script keeps each result as it comes and can
resume (`--budget`). A run it resumes holds only results of the same code, tests and corpus.

The very first run, of an earlier version of the check, killed 629 of 766. The survivors showed what
was missing:

- tests of rare cases (nested tables, East Asian and complex-script fonts, column breaks, `th`
  cells, every way of writing "on" and "off");
- a check on every count in each certificate (now locked per corpus document, beside its
  reading);
- one gap in the check itself: a table standing inside another table but outside its cells was
  passed over, where it must be refused. The reader already refused it.

All three are fixed.

## "Every time"

The same source gives the same result every time. The certificate is part of the result, so it
is the same every time too.

- `tests/test_determinism.py` reads in fresh processes, under other hash seeds and locales, and
  from differently zipped copies.
- The store keeps each result once, under the source's SHA-256 and the reader and format
  versions. It serves a result only with a receipt that holds its SHA-256.
- `label-docx-service verify` reads every kept source again, certificate included, and requires
  the kept result byte for byte.
- `tests/test_robustness.py` damages real documents about 1,000 times over: bytes inside a part,
  bytes of the file, a file cut short. Every outcome is a refusal with a code or a certified
  read, never an error. The reader and the check agree every time: nothing is refused as
  `uncertified`.
