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
  Symbol table, see "What the reader may choose" below), `w:tab` to U+0009, `w:br` to U+000A,
  a picture to U+FFFC, and so on.

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

- the check's own parse of the source (`certify.py`, about 860 lines with its comments, small
  enough to review line by line);
- Python's `zipfile`, `xml.etree` and `html.parser`.

A common-mode error would need both the reader and the check to skip the same text in the same
place. The check guards against that in four ways:

- It counts every text element in any namespace (`w:t`, DrawingML `a:t`...) inside a paragraph.
- It refuses any run content it does not know.
- It refuses a picture that holds text (a text box, or WordArt, whose text sits in an
  attribute).
- It lists every other part of the package that holds text.

## What the reader may choose, and is counted

The check allows exactly two choices, both closed, both counted in the certificate:

| Choice                                                                                   | Certificate field  | Held by                       |
| ---------------------------------------------------------------------------------------- | ------------------ | ----------------------------- |
| A run whose formatting names the Symbol font: read through the Symbol table, or as stored | `symbolMapped`     | Word (`test_word_oracle.py`)  |
| Whitespace in a run whose formatting includes hidden: left out                            | `hiddenWhitespace` | Word                          |

In the corpus neither choice arises: no run's fonts include Symbol (the corpus's Symbol glyphs
are `w:sym` elements, whose mapping is fixed), and no hidden whitespace occurs. So for every
corpus document, the output is fully determined by the source under the check's own rules.

## What it does not cover

The proof covers the text, not how it is interpreted. These are held to the applications
themselves, empirically, document by document:

- **Marks** (bold, italic, superscript...): held to Word (`tests/test_word_oracle.py`) and
  Chrome (`tests/test_browser_oracle.py`).
- **List labels** (computed, never in the text): held to Word.
- **Note marks** (computed, never in the text): held to Word.

The scope is what the reader reads:

- a .docx's body, footnotes and endnotes;
- an ePI's section titles and divs.

Text elsewhere (headers, footers, comments, footnote continuation notices, other narratives in
an ePI Bundle) is not read. The certificate lists it under `notRead` with its size, so nothing
is left out without being named.

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
