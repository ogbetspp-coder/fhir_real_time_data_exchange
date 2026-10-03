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

  Word takes about half a minute a document, mostly to open and convert it rather than for its
  size. So `--chapters 10` packs ten generated documents' worth into one, each chapter with its
  own styles and lists. Each chapter is one the reader reads and the check certifies on its
  own: a refused chapter would refuse the whole document and leave Word nothing to judge. Word
  then judges about 25 times as many list items a minute, as exactly as before. Tables now have
  up to four rows.
  - 6 packed documents: all 6 agreed, 1,219 list items, in 4 minutes;
  - 30 packed documents: all 30 agreed, 5,805 list items, in 16 minutes.

  `--fields` adds captions numbered by SEQ in every number format, headings and STYLEREF, and
  REF and NOTEREF to bookmarked captions and note references, with placeholder results. Word
  then updates every field and saves the document (`word_oracle.py update`), so each result is
  Word's own, in Word's own XML, as in a real label. The reader must compute each one as Word
  did: a result it calls stale is a difference.
  - 20 packed documents with 556 fields (265 SEQ, 82 REF, 171 STYLEREF, 38 NOTEREF): all read,
    every field as Word computed it, and all 20 agreed.

  `--stories` makes each chapter a section with its own headers and footers (default, first
  page, even pages; some shared, some left to the section before), holding styled text, page
  numbers and tables, and puts comments by several authors on body text. The first 10 packed
  documents showed three things to correct in Word's side of the comparison, none in the
  reader. Each was confirmed by asking Word directly, and each correction was checked to still
  catch built changes:
  - a paragraph that closes a section ends, in Word's text, with a section break instead of a
    paragraph mark, which the label check took for part of the next paragraph;
  - Word's text shows text in capitals as capitals, where the reader keeps the letters and marks
    them: the reader's capitals marks are now applied and so held to Word as well;
  - a table cell's end mark can follow its last paragraph without a paragraph mark.
  After these, all 10 agreed.
  - 20 packed documents with everything at once (fields updated by Word, then headers,
    footers, comments and lists): 3,100 list items, 581 headers and footers, 569 comments;
    all 20 agreed.

  Labels are not only English, so generated text now holds Greek, Cyrillic and accented words,
  units and signs (µg, °C, ≤, ±, ®, ™, ½), no-break spaces and hyphens, soft hyphens, tabs, line
  breaks and Symbol characters. The first 20 such documents showed two things:
  - **Capitals.** Word shows text in capitals (w:caps) by its own rule, not Unicode's: the
    micro sign stays "µ" where Unicode would make it the Greek capital "Μ" (so "5 µg" shows as
    "5 µG", never "5 ΜG"). ß, ŉ and ﬁ stay as they are, as do small roman numerals; ΐ and ΰ lose
    their tonos. The reader keeps the letters as stored and marks the capitals, so its text is
    right. The comparison now shows capitals by Word's rule, taken from Word's answers. Anyone
    who draws the reader's capitals marks must draw them by the same rule: turning a "µg" dose
    into "ΜG" changes a unit.
  - **STYLEREF copies a heading otherwise than REF does.** Asked for each character, Word's
    STYLEREF shows a no-break space as a space, a no-break hyphen as a hyphen, and leaves out
    soft hyphens and Symbol characters. REF copies them all as they are. The reader had
    copied the heading as is, so it refused one Word-updated document as stale: safe, but not
    Word. It now applies the three conversions (`fields-styleref-characters`). It refuses a
    STYLEREF to a heading with a Symbol character (`fields-styleref-symbol`), since its text
    does not keep which characters those were.

  With both corrected, all 20 agreed.

  The first of them showed a fault in Word's side of the comparison. The emphasis check took a
  paragraph's whole range, which in Word holds each field's hidden code, with formatting of its
  own. So a paragraph struck through looked "not struck" wherever a field's code was not.
  Asked for each field's result alone, Word showed what the reader read. The check now measures
  only what a paragraph shows: the text between fields, and each field's result. Built cases
  confirm it still tells struck from not struck in either place. It also measures every
  paragraph of a document up to 600, not 150 sampled. Word's recorded answers for every corpus
  document with fields were taken again with it, and none changed.
  - Every one the reader reads must be certified, key marks included (R-35).
  - Word's own judgment of them (`word_oracle.py compare`) runs in batches on a Mac with Word,
    since Word takes up to a minute a document.

## Strict serving

With `--browser require` or `--word require`, the service serves no read until Chrome or Word has
checked that very document and agrees (R-32). Every answer it then gives has been held, for
that document, to the application that displays it. Without them, every answer is still
certified (R-24), and the marks above are cross-checked.

## Tracked changes: two texts, each proved

A document with tracked changes holds two texts: every change accepted, and every change
rejected (the original). The reader never picks one (R-39). It writes each as a package of its
own, reads both by every rule above, and certifies each against its own package. That proves
each view's text is the view package's, but the views are the reader's own making, so they are
held to the source twice more:

- **By the check, on its own.** `certify_tracked` walks the source again with its own rules: a
  run's content belongs to a view unless a change the view drops wraps it; a paragraph whose
  mark the view drops joins the next one. Each view must hold exactly that, token by token and
  paragraph by paragraph. No revision may be left in any part of either view, and every part
  without revisions must be the source's, byte for byte. A view swapped for the other, a
  character changed, a paragraph split where it was joined, a revision left behind, or an
  untouched part rewritten is caught (`tests/test_tracked.py`).
- **By Word.** For each case in `corpus/tracked-cases`, Word accepted every change and saved
  the document, then rejected every change and saved it; the reader must read its own view
  exactly as it reads Word's file: text, marks, list labels, note marks, headers, footers and
  comments. This is where the rules come from: a joined paragraph keeps the next one's
  properties, a list counts without the items a view drops, a changed font is the former font
  in the original ("50 μg", not "50 mg"). On a Mac with Word, `--word on` does the same for
  every document ingested, and also holds Word's files to what Word shows.

Word-made tracked edits on seven EMA templates (43 documents, 1,839 changes: insertions,
deletions, joined and split paragraphs, bold and style changes): 38 agree view for view; 5 are
refused, 3 of them because Word's own view is one the reader refuses too, 2 for the cases
below.
What the reader cannot undo exactly is refused (`tracked-change`): a change holding only part
of a field (Word then drops the whole field result), a paragraph mark joined to a table, and
changes to tables, sections and numbering definitions.

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
- **A Word document's body text, as Word shows it**: the conservation check proves every
  character of the source's text is in the result. Word is now also asked what it *shows*,
  paragraph by paragraph, and the reader's text must be that (R-38). Word's own codes are
  mapped: no-break and soft hyphens, line breaks, note references, and "/" for an inline
  picture. The reader's capitals marks are applied, and page numbers go where the reader sets
  them aside. A page break, which Word's text shows like a section break, may join two of
  Word's pieces only as often as the source has a page break inside a paragraph. A Symbol
  character shows as "(": there the reader's character must be one of the Symbol table's, as
  many times as the body has Symbol characters; which one, the check holds to the table. Text
typed in the Symbol font shows as its stored code (U+F000 plus the code) and is mapped through
the same table, as list bullets are. Built
  alterations of each kind are caught: a letter, a word, a paragraph dropped or swapped, a
  space made non-breaking, a digit shown as a symbol, a paragraph split without a break. Every
  corpus document read agrees. Four EMA templates first showed differences, all in Word's
  codes rather than the reader: line breaks, and one page break inside a paragraph.
- **A Word document's headers, footers and comments**: held to Word the same way (R-37). Each
  header and footer a section names must be the text Word has for that section and type. The
  page numbers Word shows go where the reader sets them aside. Word's "/" for an inline picture
  goes where the reader writes one character, a character the check holds to a picture in the
  source. Each comment's author and text must be Word's. All 22 corpus documents with any of
  them agree, but one the reader refuses whole. Four of EMA's templates first showed a
  difference: the EMA logo in a first-page header, which Word's text shows as "/". Emphasis is
  now measured on every paragraph, however long the document: a long template had been
  measured on a sample.
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

The current run made 1,569 faults. The check now also draws every list label and note mark
on its own, and holds the views of a tracked document to their source. The tests killed 1,511
of the faults (96%). The other 58 cannot change what the check does, and each is recorded with its reason: for example, the `xml` prefix, which can never
name Word's namespace, a length compared just before, or a tenth place for list levels, which
run 0 to 8. None is unexplained. The first run with the tracked-change check left 38 faults in it alive:
no test left each kind of revision behind in a view, changed an unrevised part, held a picture
part, or counted the elements; each now has one.

The first run of this check killed 1,309 of 1,480. Most survivors traced to one gap: the
corpus tests held only the documents the check certified, so a check that wrongly refused a
document went unnoticed. Every corpus document must now be read or refused exactly as locked.
The rest needed tests of list settings no corpus document uses (a number after a space,
legal numbering, Symbol bullets through a style, numbers past "z" or 3999). A few needed small
changes to the check that left nothing to argue: dead code removed, and a placeholder mark
dropped.

A run must not cost so much that it is put off. Every fault was once run against every test,
about 70 minutes. Now each result is kept as it comes, so a run can resume (`--budget`). A
resumed run holds only results of the same code, tests and corpus. The test that last killed a
fault is also tried first, alone. A fault survives only if every test passes, so a hint can
save time but never a fault. Each corpus document is now read when a test first needs it, not
all of them before any test runs. A run now takes about 20 minutes. Runs with and without
these changes gave the same verdict on every one of the 1,480 faults.

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
