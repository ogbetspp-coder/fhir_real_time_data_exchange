# Conservation: what is proved for every result

Every result the reader serves carries a certificate from an independent check
([`certify.py`](../src/label_docx/certify.py)). A result the check cannot account for is not
served; it becomes the refusal `uncertified`. Requirements R-24 to R-28, R-34 to R-36, R-39 and
R-43 to R-45 in [requirements.md](requirements.md); tests in `tests/test_certify.py`.

## The statement

For a document `D` and the reader's result `R`:

> Paragraph by paragraph, in document order, `R`'s text is `D`'s text tokens with the set-aside
> tokens removed, each remaining token replaced by its one character. Every token of `D` is used
> exactly once: in the output or set aside.

- **Token:** one character of a text element (`w:t`, or `w:instrText` for field code), or one
  element that stands for a character (`w:tab`, `w:br`, `w:sym`, a picture or shape). For an ePI: one
  character of a section's HTML text, a `br` or an `img`.
- **Set aside**, each for a reason the check works out from `D` itself: field code (never
  shown), page numbers (set by layout; their places must be in `pages`), page and column breaks,
  hidden whitespace, floating pictures and shapes (anchored to a paragraph: Word's text shows
  none), and a floating object holding text that nothing elsewhere counts or refers to, whole,
  with its text in every branch of alternate content (below); for an ePI, whitespace CSS
  collapses and a break ending a paragraph.
- **Character:** a text character is itself, or its Symbol-table character in a Symbol-font run;
  `w:tab` is U+0009, `w:br` U+000A, `w:sym` (only in Symbol, by one to four hex digits) its
  Symbol-table character, a picture or shape in line with the text U+FFFC.

The check verifies the sequence (string equality per paragraph), the structure (paragraphs,
table cells, paragraph styles, hidden paragraph marks, notes in the order the body refers to
them, sections and note and page places are `D`'s; every mark is a span of its paragraph's text,
of a kind the format names; each body table's grid is the one `D` stores, read by the check's
own rules; what each U+FFFC stands for is the drawing `D` has there, read by its own rules too)
and the ledger:
`text + instructions + elements = output + field code + page numbers + page breaks + hidden
whitespace + floating objects + unread objects' text`. Equality is all or nothing, so a dropped, added, changed, repeated, swapped or moved
character, and a dropped, split or merged paragraph, all fail it.

`tests/test_certify.py` applies up to four seeded changes of each of its kinds to the result of
every corpus document read (a tracked document's views aside; among them, what a picture stands
for, said otherwise): every changed result is refused,
and every unchanged result is certified. These are samples, not every possible change.

## Why it is independent

- It re-reads the source bytes with its own walk. For an ePI it uses the standard library's
  HTML parser, where the reader uses an XML parser.
- Every text element of every part it reads is read twice: by Python's XML parser and by a
  tokenizer written in the check without any XML library (`_raw_texts`). The two must agree.
- It shares no code with the readers. It shares two tables: the 49-entry Symbol table and the
  Wingdings bullet table, reviewed mappings from Adobe's `symbol.txt` (as the Unicode Consortium
  maps it) and ISO/IEC N4384. Word does not check them: the Word oracle reads Word's stored codes
  through the same tables (`word.as_drawn`, `word.label_as_drawn`).
- It shares rules by design: Word's precedence for fonts and hiding, the toggles, list labels,
  note marks, table grids and what a picture stands for are the reader's rules written again,
  apart. That transcription, in code of its own, catches a slip in the reader's code, not a
  wrong rule: a rule wrong in both is caught only by Word's recorded answers
  (`test_word_oracle.py`), where Word has been asked, and by nothing where it has not.
- It treats the result as untrusted: a wrong result can only fail.
- Each token has one reading. Whether a run is in the Symbol font, or hidden, the check decides
  itself by Word's precedence (run, character style, paragraph style, table style, defaults).
  A run with Symbol in only some font slots or under complex script, right to left or a font
  hint; text in Wingdings or another dingbat or symbol-encoded font; hidden text with
  characters; or a paragraph mark hidden by one reading of its styles and not by Word's toggle
  rule, is never certified.
- It reads a closed list of elements: containers it reads through (content controls, custom
  XML, hyperlinks, smart tags, bidirectional embeddings, simple fields) and properties and place
  markers it passes over, which must hold no text. Anything else (alternate content around
  runs or paragraphs, ruby, a chunk of another format, a chart, a paragraph in a table outside
  its cells, a note defined twice) is never certified.
- It never certifies what Word may show otherwise than the source stores, or what it may read
  in more than one way: a paragraph that ends inside a field's code or page number, a story that
  ends with a field open, a hidden page number; a text element with spaces at an edge and no
  `xml:space="preserve"`, or holding a format character (a bidirectional control, a zero-width
  character...), a control (a tab or line break stored as text), an unassigned or any other
  default-ignorable code point; a content control bound to data, or empty while it names or
  shows a placeholder (one Word shows from its content is its content's text); a style, list,
  list definition or list level defined twice; a part name stored twice in any case, and a
  related part (styles, lists, notes...) named twice; a DOCVARIABLE whose result is not the
  value of the settings' variable of its name, as the check reads them (one variable of that
  name ignoring case, in the field's case, with a value: Word shows the stored result, and that
  value once fields are updated), whose code holds a quote, a field or a switch but
  `\* MERGEFORMAT` or `CHARFORMAT`, or whose result holds anything but text outside Symbol or
  runs past its paragraph; an XML part declaring an encoding other than UTF-8, or ASCII over a
  byte from 0x80.

Beyond the text, the check works out on its own, by Word's rules written apart from the reader's,
the key marks (bold, italic, caps, small caps, strike, double strike, super- and subscript,
underline), every list label and every note mark. The result's must be the check's (R-35, R-36).
So must every body table's grid (R-43), which it reads from `D` itself: the `gridCol` count, each
row's cells laid side by side after its `gridBefore`, each over its `gridSpan`, filling the grid
exactly with its `gridAfter`, each cell's `vMerge` as stored and, for a nested table, the cell it
stands in; or, where a row does not fill its grid, a count is not digits, or a merge is one Word's
drawing of is not on record, no grid and the same first reason the result names.
It draws lists in the body only; a list anywhere else, a label in capitals, hidden or drawn as
a picture, and a custom note mark's echo are never certified.

So must every paragraph's `anchored` (R-45): the check finds each object anchored to a paragraph
on its own, where its run stands outside a field's code or a page number, and its kind: a
picture or a shape holding no text; or one holding a text box (`w:txbxContent`) that floats (a
DrawingML `wp:anchor`, alone or as alternate content's one `wps`, `wpg` or `wpc` choice; VML of
one shape or group positioned absolutely) and holds nothing Word refers to or counts elsewhere,
by its own copy of that condition: no field, note mark, bookmark, comment, section or revision,
no list item by its own properties, a style, a table style or the defaults, no content control
shown from elsewhere, no WordArt or embedded object, no graphic but a picture, shape, group or
canvas, none of Word's own elements outside the text boxes but the drawing and its fallback, and
no field around it. Such an object is set aside whole: its text elements' characters and its
runs' elements, in every branch, are counted in the source and set aside (`unreadObjects`,
`unreadObjectCharacters`), and its paragraphs are not the walk's. Any other object holding text
is never certified. Every entry must be the check's, offset, kind and `read` false: one added,
dropped, moved, of another kind or marked read is never certified, and an object's text put in
its paragraph fails the text's equality. In a tracked document a revision inside a drawing is
never certified (`certify_tracked`): its text is in neither view.

So must every paragraph's `pictures` (R-44), in the body, notes, headers, footers and comments:
for each drawing it reads as U+FFFC, in order, the check finds on its own the image part (the
picture's `blip`, or a VML picture's first `imagedata`, through the story part's own
relationships: one internal relationship of the image type, to a part stored under that very
name), hashes its bytes, tells PNG from JPEG by the signature alone, reads the size from the
PNG's chunks (each whole with its CRC, one IHDR first to IEND at the end, no unknown critical
chunk) or the JPEG's segments (to its scan, one frame of precision 8), at most 100,000 of
either and none kept, and any Exif orientation or colour management; finds where the picture
stands (in a field's result, in a paragraph of exact line height, in a row whose height may be
exact, on a bordered run); and reads the drawing's extent, crop, rotation, flips and every other
element and attribute against its own closed list. The reason it names is the first of a fixed
list (`shape`, `vml`, `field`, `linked`, `no-part`, `not-png-or-jpeg`, `bad-image-header`,
`colour`, `animated`, `orientation`, `bad-number`, `cropped`, `rotated`, `flipped`,
`line-height`, `row-height`, `border`, `effects`); an entry in any other way, one too many or
one missing is never certified. A null reason says only that nothing on those lists was found:
the lists rest on what is known of Word, not on Word's drawing. A picture never refuses a read:
what the reader cannot vouch for it reports with its reason, and the check holds that reason as
it holds every other value. Each grid row's `exactHeight` is the check's own reading too.

## Tracked changes

A tracked document has two views, all changes accepted and all rejected, each certified as above
against its own package. The views are held to the source by `certify_tracked`, again with its own
walk: each run's content is in a view unless a change the view drops wraps it, and stays in its
table cell; a paragraph joins the next exactly where the view drops its mark (past a table the view
drops whole, an empty paragraph only, the paragraph after it; a bookmark's end between the two after
what is joined); a row the view drops goes whole, and a table whose every row it drops; a field's
marks (begin, separator, end) are all kept or all dropped, and a field the view drops goes with
every run inside it; a note goes exactly when the view drops every reference to it; no revision is
left in any part; every other part is the source's byte for byte. In a part with revisions,
everything else is the source's too, element by element: each run element in full with its run's
properties and the elements around it, each paragraph's properties, attributes and place, and
everything outside paragraphs (tables, sections, styles, note ids). Properties a change records are
the current ones in the accepted view; the former ones the original takes are held to Word: for
every case in `corpus/tracked-cases` the reader does not refuse, the reader reads its view exactly
as it reads Word's own Accept All / Reject All file. It refuses 8 of the 50 (`tracked-change`), and
their views are not compared.

## What is held to the applications instead

The proof covers the text. How it is shown is held to the application that shows it:

| What                                              | Held to | Where                                  |
| ------------------------------------------------- | ------- | -------------------------------------- |
| List labels, note marks, fields, text, bold, italic, caps and strike, headers, footers, comments of a .docx | Word | `test_word_oracle.py` (corpus; comments by unit tests only, none in the corpus yet); `--word on` (every ingest) |
| Text, marks and list markers of an ePI section    | Chrome  | `test_browser_oracle.py` (corpus); every ingest with Chrome |
| Tracked views                                     | Word    | `test_tracked.py` (corpus); `--word on` |

Table grids are held to the source alone (the check above): Word has not yet been asked where
it draws each cell. The grid is what the document stores; where Word would have to lay it out by
rules of its own (a row that does not fill it, say), no grid is reported, and the text is read
all the same. So are pictures: which part a picture's bytes are in, and its extent and crop, are
what the document stores; that Word draws nothing else (no recolouring, outline, turn...) rests
on the closed list, not on Word's drawing. Chrome's decoded size of each ePI picture is
recorded by the browser oracle (`browser_sections`, `pictures`), for the builder to hold to the
reader's `pixels`.

Word is asked about emphasis per body paragraph: whether all its letters are bold, italic, in
capitals or struck through, white space's formatting aside, so Word's "no" agrees with a
paragraph partly so; a paragraph with a note reference, a page number or a hidden paragraph mark
is not held to it (`word.emphasis_verdict`). Superscript, subscript, underline, highlight, shading, faint, raised or
lowered and right-to-left text are not asked of Word: they are held by unit tests, and the
first three, with the toggles, by the check's own copy of Word's rules (R-35). Word's text shows
every Symbol character (`w:sym`) as "(", so Word cannot tell them apart: there the reader's
character is held to be one of the Symbol table's (R-38), and this check holds which one.

Word's and Chrome's rules are not published, so this is evidence by example, not proof. With
`--word require` or `--browser require` the service serves nothing the application has not
checked (R-32). Word shows text in capitals by its own rule ("5 µg" in capitals is "5 µG", never
"5 ΜG"): anyone drawing the reader's `caps` marks must do the same.

## Scope

The body, footnotes, endnotes, headers, footers and comments of a .docx; the section titles and
divs of an ePI. A header, footer or comment the reader refuses on its own is listed under
`refused`, and the body is read all the same, whatever stops the check reading that part. Text
anywhere else is listed under `notRead`: for a .docx each part (the glossary, a header no section
names...), note separator or continuation notice, with the size of its text (every text character
and every element that stands for one); for an ePI only the number of other narratives in the
Bundle. The text of a floating object set aside unread is in the parts read, counted under
`setAside` (`unreadObjectCharacters`), not under `notRead`.

## Who checks the checker

`scripts/mutate_checker.py` puts one fault at a time into a copy of `certify.py` (a comparison
turned round, a number one off, a statement removed...) and runs the check's tests against it.
`tests/test_checker_mutants.py` requires the recorded run to be of the current `certify.py`,
its tests and the corpus, at least 90% of faults killed, and every survivor recorded with the
reason it cannot change a result; each reason names one fault. The run works on a copy of the
files taken when it starts, so an edit made meanwhile cannot reach it. Counts are in
`docs/checker-mutants.json`: the last run killed 3,420 of 3,514 faults and recorded 94 as unable
to change a result, with none unexplained. It took 64 minutes on eight cores with other work
running; a run resumes in parts (`--budget`).

## Every time

- Same bytes across processes, hash seeds, locales and zip layouts (`test_determinism.py`).
- The store keeps each result once, under the source's SHA-256 and the versions, and serves it
  only if it hashes to its receipt; `label-docx-service verify` re-reads every source and
  requires the kept result byte for byte.
- 1,200 seeded damaged copies of four corpus documents (an EMA QRD template, an EMA ePI and two
  synthetic test documents) each give a refusal or a certified read, never an error
  (`test_robustness.py`).
