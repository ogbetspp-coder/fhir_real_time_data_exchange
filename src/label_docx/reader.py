r"""A fail-closed reader for the text of a Word (.docx) label.

The reader turns a document's body, notes, headers, footers and comments into paragraphs of text,
and refuses a document whose text it cannot produce exactly: a reader that goes on past what it
does not understand is how a label loses a character unnoticed (EMA's QRD Appendix II writes the
"≥" of "Very common (≥ 1/10)" as a Symbol-font ``w:sym``, which a reader of ``w:t`` alone drops).

Every character in the output has a source in the document, and every text-bearing node of each
part the reader reads is accounted for: each run is read by the reader exactly once, run content
(``<w:t>``, ``<w:sym>``, a break...) stands only inside a run, and no character data stands
outside ``<w:t>`` and ``<w:instrText>``. A document where any of that fails is refused, so no text
the document holds can be passed over in silence.

What a paragraph carries:

- ``text``: the characters as stored. ``<w:t>`` text is copied as is; nothing is normalised,
  straightened or trimmed. ``<w:tab/>`` and ``<w:ptab/>`` are U+0009; ``<w:br/>`` and
  ``<w:cr/>`` are U+000A, except a page or column break, which is layout and emits nothing;
  ``<w:noBreakHyphen/>`` is U+2011, ``<w:softHyphen/>`` U+00AD. A picture in line with the text
  is U+FFFC OBJECT REPLACEMENT CHARACTER at the place it stands: a DrawingML picture
  (``w:drawing``), a VML picture (``w:pict`` of one image, as documents from before Word 2007
  hold it), or alternate content whose choice is a DrawingML picture or shape (``wps``: a line,
  a box) and which holds no text. A DrawingML shape outside alternate content, and a VML shape,
  are refused. One anchored to the paragraph (floating) is not in the text: Word's text shows
  none, and the check counts it (``floatingObjects``).
- ``marks``: ranges of ``text`` whose appearance changes what a reader sees or means, set on the
  run, its styles or the document defaults (``Mark`` lists the kinds): bold, italic, superscript,
  subscript, raised or lowered text, capitals and small capitals, single and double strike-through,
  highlight with its colour, shading with its fill (or its pattern, colour and fill) on the run or
  the paragraph, right-to-left (the run's own ``rtl``, a ``dir`` embedding, a ``bidi``
  paragraph), and faint text (a colour whose contrast with what is painted under it, the run's
  highlight or shading, else the paragraph's, the cell's or the page, is below 1.33:1; under two
  points in any unit; or scaled under a fifth), and underline of any style (an underlined "<" is
  how "≤" is often typed). ``text`` alone flattens "10" with a superscript "9" to "109"; a
  caller that uses ``text`` must look at ``marks``. Bold, italic, capitals and strike-through
  are toggles, and reported as Word shows them: two kinds of style that both set one cancel (see
  ``_Properties.shown``). Other appearance (other colours, font size, borders) is not reported.
- ``mark_hidden``: the paragraph mark is hidden (``vanish`` or ``specVanish``, directly or
  through the paragraph's styles), so Word shows this paragraph run on into the next one.
  ``specVanish`` on a run with text does not hide it: Word shows and prints it.
- ``numbering``: the list the paragraph belongs to, directly, through its style or the paragraph
  defaults (Word draws a list set there), and the list label Word draws before it
  (``Numbering.text``, with ``suffix`` naming what separates it from the paragraph). The label is
  computed, not stored, so it is never put into ``text``; see "List labels" below.
- ``table``: ``(table, row, cell)`` counted from zero in document order, else ``None``. A nested
  table's paragraphs carry the outermost cell; cells are counted as ``<w:tc>`` elements, not
  grid columns.
- ``pages``: where in ``text`` Word draws a page number (a table of contents' page, a PAGE
  field). Word sets it from the layout when it prints, so it is never in ``text``.
- ``notes``: the footnote and endnote marks in the paragraph (``NoteReference``): where each
  stands in ``text``, which note it refers to, and the mark Word draws there. Like a list label,
  the mark is computed and never put into ``text``; see "Notes" below.

Styles. Run properties are looked up on the run, then its character style, its paragraph
style, its table style (inside a table only) and the document defaults, each style with its
``basedOn`` chain. An absent or unknown paragraph or table style id falls back to the document's
default style of that kind (the last one marked default), as Word does. A character style does
not: a run with no character style, or an unknown one, takes none, since Word does not apply the
default character style to text. A reference to a style of another kind is refused. A paragraph
style based on a character style takes nothing from it, as Word draws it; any other ``basedOn``
naming a style of another kind is refused. Paragraph shading and right-to-left are looked up the
same way through the paragraph properties.

A table style's conditional formatting (``tblStylePr``) is applied as Word applies it, each rule
Word's answer to a case in ``corpus/numbering-cases`` (table-style-*). A part applies where the
table's look turns it on (an attribute over the ``val`` bits; with no look, the first row only):
the first or last row (header rows at the top are first rows), column, a corner (both its looks
on), or a band, only where the style sets a band size and counted past the first row or column
only where the style defines that part; the whole-table part never. Nearest first: corner, row,
column, vertical band, horizontal band; together they stand over the table style's own run
properties at its level, so they toggle with the character and paragraph styles as any style
does and the run's own setting wins. Bold, italic, capitals and strike are applied. A part's
fonts, sizes, colours and spacing are not on record: whether text is faint or in Symbol must come
out the same with and without them, and the layout bounds must hold both ways. Any other run
property in a part is refused. What Word was not asked is refused over text
(``_TableLayout``): a look or band size it has no answer for, a corner without both looks, the
last row or column under banding where the style defines it, parts through ``basedOn``, and a
part over a row off the grid, merged cells, a nested table or a table outside the body. Cell
shading a style may paint is taken as possibly under every cell, for faint text, and Symbol
text under a part's fonts is refused.

Symbol fonts. A run whose effective ``ascii`` and ``hAnsi`` fonts (set directly, by a style, by the
document defaults or through the theme) are both Symbol, by that exact name, with no complex-script
or right-to-left property and no font hint other than ``default`` (which sends ambiguous characters
to the ``hAnsi`` font, Symbol here), has every character mapped through ``SYMBOL_FONT``; a
character the table does not hold is refused. ``<w:sym>`` in the Symbol font is mapped the same
way. Any other run with Symbol in one of its four font slots is refused, because Word picks the
font per character and the reader cannot be sure which characters it draws in Symbol. A dingbat
font (Wingdings, Webdings, Zapf Dingbats, Marlett, MT Extra, Monotype Sorts), another spelling of
Symbol ("SymbolMT", "symbol", "Bookshelf Symbol 7": Word's answer is not on record), or any font
the document's font table declares symbol-encoded (charset 02, or the symbol code page in
``csb0``), embeds, or replaces when missing by a symbol or dingbat font (``altName``), is refused.

Fields keep their stored result and drop their instruction, however deeply nested, so ``DOCPROPERTY
... MERGEFORMAT`` never reaches the text. Fields whose stored result is what Word shows and prints
are read: HYPERLINK, DOCPROPERTY and TOC (a table of contents, whose entries Word prints as stored
until someone updates it). Page numbers (PAGEREF, as in a table of contents' entries, PAGE,
NUMPAGES, SECTIONPAGES) Word sets from the page layout when it prints; their stored text is left out
of ``text`` and their place recorded in ``pages``. Only the switches Word has answered are placed
(PAGEREF's ``\h``, and ``\*`` MERGEFORMAT, CHARFORMAT or Arabic; ``\p`` shows "above", ``\#`` a
picture's text), a PAGEREF only to a bookmark REF could read, and never a locked one (``fldLock``),
whose stored text Word shows. SEQ (caption numbers), STYLEREF (a heading's number or text), REF (a
cross-reference: a bookmark's text) and NOTEREF (the mark of the note a bookmark holds) Word shows
as stored but recomputes when it prints or saves as PDF, so the reader computes them as Word does
and reads them only where the stored result is the computed one; otherwise screen and print
disagree, and the document is refused (``stale-field``). A REF or NOTEREF to a bookmark that is not
there (Word prints an error), that runs across paragraphs, or over a note mark (REF) is refused;
NOTEREF counts only a note mark between the bookmark's start and end, not one next to it (Word
prints an error). SEQ counts each identifier in document order: one more than the last, ``\r`` n
sets the count, ``\c`` repeats it, ``\h`` counts and shows nothing, ``\s`` n restarts it after any
paragraph in a built-in style "heading 1" to "heading n" (Word goes by the style's name, not its
outline level), and ``\*`` shows it in ARABIC, ROMAN, roman, ALPHABETIC or alphabetic. STYLEREF
finds the nearest paragraph of the style (a number n is "heading n") before the field, else after
it, and shows its text, or with ``\s`` its list label without the final period. Each rule is Word's
answer to a case in ``corpus/numbering-cases``. Other switches, a computed field (SEQ, STYLEREF, REF
or NOTEREF) or a PAGEREF in a note, any of the four with a field in its own code or nested in
another field's code, and a result that runs past its paragraph are refused. Any other field whose
result would be shown (DATE, IF, a formula...) is refused, because Word recomputes it on display or
print. The code is the first word of the instruction; a field nested in the instruction ahead of or
inside that word makes the code unknown, and the field is refused. So are a field with no stored
result (no ``separate``, as a form checkbox or a SYMBOL field, but a hidden SEQ; or an empty
``fldSimple``), a form field, a field marked for update, any field in a document whose settings ask
Word to update fields on open, and field code outside an instruction.

List labels. Word draws "4.8", "b)" or a bullet before a numbered paragraph from the numbering part;
the reader computes that label by Word's rules, each of which is Word's own answer to a case in
``corpus/numbering-cases`` (``word.json``; ``tests/test_word_oracle.py``). A paragraph's ``numId``
names a ``w:num``, which names an ``abstractNum``; a level of the ``w:num``'s ``lvlOverride``
replaces the abstract level's look (format, text, font), not its start. An ``abstractNum`` with a
``numStyleLink`` takes its levels from the one the numbering style names, which must name the style
back (``styleLink``). Counters belong to the ``abstractNum`` the ``w:num`` names: every list naming
it shares them, so a second list continues the first. A paragraph at level ``L`` restarts every
deeper level (``lvlRestart`` 0 never restarts it; ``lvlRestart`` ``n`` restarts it only after a
level up to ``n - 1``), counts every higher level not yet counted as that level's start, and counts
its own level: its list's ``startOverride`` the first time that list reaches the level, else one
more than the shared count, else (after a restart) the ``startOverride`` of the list whose paragraph
restarted it, or the ``abstractNum`` level's ``w:start``, 0 when there is none. ``lvlText`` is
copied, with ``%1`` to ``%9`` replaced by the counter of that level in that level's format (under
``isLgl`` all decimal, but decimalZero, which keeps its zero): decimal, decimalZero, upper and lower
roman (1 to 3999), upper and lower letter (a to z, then aa, bb...), or none; a bullet level's text
is its bullet. The label is drawn in the level's run properties over the paragraph mark's (with the
mark's character style), so its fonts are placed as a run's are: a Symbol bullet (U+F0B7) is mapped
to "•", a Wingdings bullet through ``WINGDINGS_BULLETS`` (U+F0A7 to "▪"), and a bullet in any other
dingbat font is refused. ``suffix`` is ``tab``, ``space`` or
``nothing`` (``w:suff``), or ``legacy`` for a Word 6 level, where the gap is layout and not a
character.

Notes. ``read_document`` returns the footnotes and endnotes with the body, each note's paragraphs
read by every rule above, in the order the body refers to them; ``read_docx`` returns the body
alone. A note's mark is its section's ``numStart`` plus the number of notes of its kind before it,
in the document or, where the section restarts them (``numRestart`` ``eachSect``), in the section;
it is drawn in the section's format: decimal, roman, letters, or symbols (``chicago``: *, †, ‡, §,
then each doubled, as far as ††, Word's answers). The section's ``footnotePr`` and ``endnotePr``
decide this; Word ignores the settings part's. Footnotes default to decimal, endnotes to lower
roman, and each kind counts apart. A note with a custom mark takes no number; its mark is the
stored text that follows the reference. Every rule is Word's answer to a case in
``corpus/numbering-cases``, held by ``tests/test_word_oracle.py``. Every note must be referred to
exactly once, and every reference must name a note.

What it refuses (``DocxRefusedError.code``):

- ``tracked-change``: any revision, in the body, a note, a header, a footer, a comment, a style
  or a list. Such a document has more than one text: ``tracked`` makes its two views, each read
  by these rules, and refuses what it cannot undo (see ``tracked``).
- ``hidden-text``: hidden text other than whitespace (hidden whitespace is left out), or a hidden
  note mark, comment mark or page number, hidden directly or at any level of the style hierarchy
  (hiding is treated as a fact as soon as any level asserts it, unless the run itself says it is
  visible); and a run with text or a paragraph mark hidden by some level where Word's toggle rule
  (``_Properties.shown``: a nearer style turns it off, or two kinds of style cancel) shows it,
  since what Word then shows is not on record.
- ``unmapped-symbol``: a Symbol-font code the table does not hold (or, in a Symbol run, a
  character above U+00FF outside U+F000 to U+F0FF), a ``w:sym`` without a hex code or in a font
  other than Symbol, or a Wingdings list bullet the table does not hold.
- ``symbol-font``: text or a list label in a dingbat or symbol-encoded font, Symbol in only some
  of the font slots or under a table style's conditional fonts, or a theme font with no theme
  part or not in it.
- ``private-use-character``: a private-use code point outside a Symbol-font run.
- ``format-character``: in ``<w:t>``, an invisible formatting character (category Cf: zero-width
  characters, bidirectional controls, a soft hyphen, which Word writes as ``w:softHyphen``), any
  other code point Unicode says to ignore (Default_Ignorable_Code_Point, as the ePI reader), or a
  C1 control.
- ``unassigned-character``: in ``<w:t>``, a code point Unicode 16.0 does not assign (Cn).
- ``reserved-character``: U+FFFC in ``<w:t>``, which the reader uses for a picture.
- ``unpreserved-whitespace``: ``<w:t>`` text with leading or trailing spaces without
  ``xml:space="preserve"`` (a consumer may drop them), or a tab or line break inside ``<w:t>``
  (Word writes those as elements).
- ``unbalanced-field``: a paragraph that ends inside a field instruction, field code
  (``instrText``) outside an instruction, a computed field's result or a page number that runs
  past its paragraph, or a field character out of place: a second separator, a separator or end
  with no field open, or an unknown kind.
- ``field-without-result``: a field with no ``separate`` other than a hidden SEQ (``\h``), or an
  empty ``fldSimple``.
- ``computed-field``: a shown field whose value Word computes rather than stores (any but those
  read above), a computed field (SEQ, STYLEREF, REF or NOTEREF) the reader cannot compute, one
  with a field in its own code or nested in another field's code, a computed field or a PAGEREF
  in a note, header, footer or comment, or any field in a document set to update fields on open.
- ``stale-field``: a field marked for update, or a computed field (SEQ, STYLEREF, REF or
  NOTEREF) whose stored result is not what Word prints.
- ``unsupported-element``: anything that can carry text and is not read above, and any element
  the reader does not know: text boxes, a drawing that is not a picture (a chart, a shape
  outside alternate content) or neither in line nor anchored, a hidden picture in line
  (``wp:docPr hidden``), a ``w:pict`` that is not one visible picture in line or positioned
  absolutely, alternate content that is not a picture or shape with no text, embedded objects and
  ActiveX controls, math, ``altChunk``, form fields, a run or paragraph property in a namespace
  that is neither WordprocessingML nor one of Word's extensions (w14 on), alternate content in
  the styles, theme, font table, settings or lists (but a list level's own child), content
  marked for markup compatibility processing (``mc:ProcessContent``, ``mc:MustUnderstand``) in
  any part, content controls bound to data (in any namespace), an empty content control naming a
  placeholder building block (Word shows the placeholder; one with none shows nothing, as read),
  a note or comment mark in a field code, a note mark outside the body, a note's echo of its mark
  outside that note, a comment mark in a comment, a comment's echo of its mark outside it, a note
  of a type other than normal (separators aside), conditional table formatting whose effect on
  the text or a list label is not on record (see "Styles"), text, whitespace, a list
  label, or a note, comment or page mark in a vertically merged-away cell (Word draws none of it
  and does not count the label; a horizontally merged one is read as its own cell, as Word shows
  it), a bidirectional override (``bdo``) or an embedding (``dir``) of no direction, a style
  reference that names a style of another kind, and a ``basedOn`` that does (but a paragraph
  style's on a character style).
- ``unsupported-formatting``: formatting or layout whose effect on what is shown is not on
  record: complex script (right-to-left or ``cs`` in force, a ``dir`` embedding, or Hebrew,
  Arabic, Indic, Thai... characters) whose ``b`` and ``bCs``, or ``i`` and ``iCs``, differ
  (Word draws the second, its Font object reports the first); right-to-left set by a style or
  the defaults (Word does not allow it there); a Word 2010 text fill (``w14:textFill``); a
  colour, highlight or theme colour the reader cannot resolve, or text faint over one colour
  that may be under it and not over another; and layout that may clip or overdraw text: a row of
  exact height lower than its cell's lines (each its largest text or mark size), exact line
  spacing lower than the text, line spacing under 0.8 lines, a frame or floating table more than
  an inch before or 22 inches past its anchor, a frame of exact height lower than its text, a
  paragraph or table indented more than an inch outward, ``fitText``, and characters condensed
  by more than a quarter of their size. These are bounds, not a layout engine: wrapped lines in
  an exact row, say, are a stated residual.
- ``invalid-package``: not one whole zip archive (a PDF or a Word 97-2003 document is named as
  one; bytes before or after the archive are refused), not one main document part, a part name
  that occurs twice (ignoring case), a related part that is missing, duplicated or not of its
  kind, a relationship to a part the reader reads of a type other than the one Word writes (one
  only ending in its kind), a relationship Id repeated in one part's relationships, a part that
  cannot be read (bad checksum, truncated, encrypted), any part damaged, read or not, parts over
  ``MAX_PACKAGE_BYTES`` or ``MAX_ELEMENTS`` together, a part over ``MAX_PART_BYTES``, an XML part,
  read or not, that is not well-formed, not UTF-8, declares another encoding or a DTD, or nests
  over ``MAX_DEPTH`` deep, no ``w:body``, a number (an id, a level, a start, a table look) that is
  not a number, a list level outside 0 to 8 in the numbering part, a style, list, list level, note
  or comment defined twice, a section naming two headers or footers of one type, a note referred
  to twice, and a mark of a note or comment that is not there, or a comment's mark that stands
  twice.
- ``stray-text``: character data in a WordprocessingML element of a part the reader reads,
  outside ``<w:t>`` and ``<w:instrText>`` (whitespace between elements aside), or an element
  inside either of them.
- ``unread-content``: a run the reader did not reach (inside section, paragraph or cell
  properties, say), run content standing outside a run, a note nothing refers to, or a comment
  nothing anchors (Word does not show them; their text is in the file all the same).
- ``unsupported-numbering``: a list label the reader cannot draw exactly: a ``numId`` or level
  with no definition (or no numbering part), a paragraph's level outside 0 to 8, a format or
  suffix other than those above (ordinal and text formats depend on the language), a custom
  format, a level with no ``lvlText`` or one over ``MAX_LEVEL_TEXT`` characters, a picture
  bullet, a level holding anything else the reader does not know (alternate content, say), a
  negative ``lvlRestart``, an ``lvlRestart`` at the paragraph's level or above that restarts it
  after the level directly above (written out), itself or a deeper one (Word draws such a level
  empty), a ``%`` in ``lvlText`` that names no level or a deeper or undefined one, a bullet level
  that shows a counter or is shown in another's, ``isLgl`` showing a level of format none, a
  number past a format's range, a label in capitals or small capitals with letters in it, a
  numbering-style link the reader cannot follow (no ``styleLink`` back, or to a list with
  overrides), a list (or its level) set by a table style, a list in a note, header, footer or
  comment, a custom note number format or one other than those above, a note symbol past ††,
  or a note ``numRestart`` other than ``continuous``, ``eachSect`` or ``eachPage``.
- ``ambiguous-numbering``: a label drawn hidden (the paragraph mark is hidden at any level, its
  character style included, whatever the list level says, or the level is hidden) or a
  numbered paragraph run on after a hidden paragraph mark, for which Word's list API reports a
  label but not whether or where it is drawn; a label that shows a level whose start the
  reader cannot find (only a ``lvlOverride`` defines it); a level that never restarts
  (``lvlRestart`` 0) shown in a deeper level's label; a higher level first counted by a deeper
  paragraph when it has an ``lvlRestart`` of its own, or after a paragraph of a list with a
  ``startOverride`` for it restarted it; after a table row ends, a level counted on from a
  ``startOverride`` taken through a deeper paragraph, a level that never restarts counted again
  after a higher paragraph, or a level restarted as above (Word counts these differently in
  different tables); note numbers that restart on each page, which depends on layout; or the
  echo of a custom mark inside its note, where Word draws the number the next note will take.

Headers, footers and comments. ``read_document`` also reads every header and footer part the
sections refer to (``Story``: each part once, in the order referred to, with the (section,
type) uses that name it; which one Word shows on a page is layout), and every comment of the
comments part (``Comment``: its author, initials and date as stored), each paragraph by every
rule above. A comment's mark in a paragraph is placed in ``comments`` (``CommentReference``),
never in ``text``; every comment must be anchored exactly once. Fields the reader computes
(SEQ, STYLEREF, REF, NOTEREF) and lists are refused there, since how Word counts them outside
the body is not on record; PAGE, NUMPAGES and SECTIONPAGES are placed, PAGEREF refused. A
header, footer or comment the reader cannot read exactly is refused on its own
(``Story.refusal``, ``Comment.refusal``): the body is read all the same. A header or footer Word
shows on no page is not read and is marked ``never-shown``, which is no refusal (the certificate
lists its size under ``notRead``): a ``first`` part shows only in a section with ``titlePg``, an
``even`` part only with the settings' ``evenAndOddHeaders``, and a section naming no part of a
type takes the one before it. The glossary (building blocks) is not read.
"""

from __future__ import annotations

import colorsys
import io
import itertools
import posixpath
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any

# The version of the rules above; versions.lock.json ties it to this file (tests/test_locks.py).
READER_VERSION = "docx-reader/1.24.0"

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
# The relationship types Word writes for the parts the reader reads, each this and the kind.
_RELATIONSHIPS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
_PROCESSING = {f"{{{MC}}}ProcessContent", f"{{{MC}}}MustUnderstand"}
PICTURE_URI = "http://schemas.openxmlformats.org/drawingml/2006/picture"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
OBJECT = "\ufffc"

MAX_PART_BYTES = 20 * 1024 * 1024
MAX_DEPTH = 200
# The parts of one package together, as their headers declare them, before any is unpacked.
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
# The XML elements of one package's parts together, each part parsed whole and kept for the read
# (the longest label on hand, a US pembrolizumab label, has 238,434).
MAX_ELEMENTS = 2_000_000


def _w(tag: str) -> str:
    return f"{{{W}}}{tag}"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# Adobe's Symbol encoding as the Unicode Consortium maps it (MAPPINGS/VENDORS/ADOBE/symbol.txt),
# limited to the codes a product-information text can be expected to use. Word stores a Symbol
# glyph either at its code or at U+F000 plus its code. The table is closed on purpose: a code
# missing here is refused, and adding one is a reviewed change with a test. Where symbol.txt
# gives two Unicode characters for one code, the choice is noted.
SYMBOL_FONT: dict[int, str] = {
    0x20: " ",  # symbol.txt also gives U+00A0; the ordinary space is taken
    0x21: "!",
    0x23: "#",
    0x25: "%",
    0x26: "&",
    0x28: "(",
    0x29: ")",
    0x2B: "+",
    0x2C: ",",
    0x2D: "\u2212",  # MINUS SIGN
    0x2E: ".",
    0x2F: "/",
    **{code: chr(code) for code in range(0x30, 0x3A)},  # digits
    0x3A: ":",
    0x3B: ";",
    0x3C: "<",
    0x3D: "=",
    0x3E: ">",
    0x3F: "?",
    0x5B: "[",
    0x5D: "]",
    0x5F: "_",
    0x61: "\u03b1",  # GREEK SMALL LETTER ALPHA
    0x62: "\u03b2",  # GREEK SMALL LETTER BETA
    0x64: "\u03b4",  # GREEK SMALL LETTER DELTA
    0x67: "\u03b3",  # GREEK SMALL LETTER GAMMA
    0x6D: "\u03bc",  # GREEK SMALL LETTER MU; symbol.txt also gives U+00B5 MICRO SIGN
    0x7B: "{",
    0x7C: "|",
    0x7D: "}",
    0xA3: "\u2264",  # LESS-THAN OR EQUAL TO
    0xA5: "\u221e",  # INFINITY
    0xAE: "\u2192",  # RIGHTWARDS ARROW
    0xB0: "\u00b0",  # DEGREE SIGN
    0xB1: "\u00b1",  # PLUS-MINUS SIGN
    0xB3: "\u2265",  # GREATER-THAN OR EQUAL TO
    0xB4: "\u00d7",  # MULTIPLICATION SIGN
    0xB7: "\u2022",  # BULLET
    0xB9: "\u2260",  # NOT EQUAL TO
    0xBB: "\u2248",  # ALMOST EQUAL TO
}

# Wingdings list bullets as ISO/IEC JTC1/SC2/WG2 N4384 maps them to Unicode (its normative
# "source references" table, index 1000 plus the code). Closed like the Symbol table: only the
# bullets labels are found to use, each a reviewed change with a test. Bullets only: text in
# Wingdings is refused.
WINGDINGS_BULLETS: dict[int, str] = {
    0xA7: "\u25aa"  # BLACK SMALL SQUARE (w-1167), Word's default third-level bullet
}

# Fonts drawn as symbols, by a part of their name (lower case, no spaces): any spelling of
# Symbol but the exact one, and symbol-encoded fonts Word ships (Bookshelf Symbol 7, Monotype
# Sorts...), refused even where no font table declares them.
_DINGBAT_FONTS = ("wingdings", "webdings", "dingbat", "marlett", "mtextra", "symbol", "sorts")

# Unicode's Default_Ignorable_Code_Point (Unicode 16.0, DerivedCoreProperties.txt): code points
# drawn as nothing. The ePI reader's table (epi.DEFAULT_IGNORABLE), kept equal by a test.
DEFAULT_IGNORABLE = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)
_IGNORABLE = frozenset(code for low, high in DEFAULT_IGNORABLE for code in range(low, high + 1))
# Word's own extensions (w14 and later): namespaces Word knows, whose properties change no text.
_WORD_EXTENSIONS = "{http://schemas.microsoft.com/office/word/"

_TRACKED = {
    _w(name)
    for name in (
        "ins",
        "del",
        "moveFrom",
        "moveTo",
        "delText",
        "delInstrText",
        "moveFromRangeStart",
        "moveToRangeStart",
        "rPrChange",
        "pPrChange",
        "sectPrChange",
        "tblPrChange",
        "tblPrExChange",
        "tblGridChange",
        "trPrChange",
        "tcPrChange",
        "numberingChange",
        "cellIns",
        "cellDel",
        "cellMerge",
        "customXmlInsRangeStart",
        "customXmlDelRangeStart",
        "customXmlMoveFromRangeStart",
        "customXmlMoveToRangeStart",
    )
}
# Markers that carry no text, allowed wherever they occur.
_MARKERS = {
    _w(name)
    for name in (
        "bookmarkStart",
        "bookmarkEnd",
        "commentRangeStart",
        "commentRangeEnd",
        "permStart",
        "permEnd",
        "proofErr",
    )
}
# Run children that carry no text of their own.
_RUN_SILENT = {_w(name) for name in ("rPr", "lastRenderedPageBreak")}
# A note's mark in the body, and its echo at the start of the note's text.
_NOTE_REFERENCES = {
    _w(name) for name in ("footnoteReference", "endnoteReference", "footnoteRef", "endnoteRef")
}
# Paragraph-level containers whose children are read as the paragraph's own. fldSimple's
# children are the field's displayed result; its instruction is an attribute and is dropped.
_INLINE_TRANSPARENT = {_w(name) for name in ("hyperlink", "smartTag", "customXml")}
# Properties of a paragraph, a content control, a smart tag or custom XML: no text of their own.
_PROPERTIES = {_w(name) for name in ("pPr", "sdtPr", "smartTagPr", "customXmlPr")}
# The elements whose character data is read. Character data in any other element of the main
# document part is refused rather than passed over.
_TEXT_ELEMENTS = {_w("t"), _w("instrText")}
_XML_WHITESPACE = " \t\r\n"
# What a run holds that stands for characters or a field. Each must be the child of a run, and
# every run must be one the reader read. <w:tab> is left out: it also names a tab stop in the
# paragraph properties, and a run's tab is covered by the run being read.
_RUN_CONTENT = {
    _w(name)
    for name in (
        "t",
        "instrText",
        "sym",
        "br",
        "cr",
        "ptab",
        "noBreakHyphen",
        "softHyphen",
        "drawing",
        "fldChar",
        "footnoteReference",
        "endnoteReference",
        "footnoteRef",
        "endnoteRef",
    )
}

# Toggle properties reported as marks, by the mark kind each is reported as.
_TOGGLE_MARKS = {
    "b": "bold",
    "i": "italic",
    "caps": "caps",
    "smallCaps": "smallCaps",
    "strike": "strike",
    "dstrike": "dstrike",
}


class DocxRefusedError(Exception):
    """The document holds something whose text the reader cannot produce exactly."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Numbering:
    """The list a paragraph belongs to. ``num_id`` 0 means "not in a list".

    ``text`` is the label Word draws before the paragraph ("4.8.", "b)", "•", or "" for a level
    that shows nothing) and ``suffix`` what follows it: ``tab``, ``space``, ``nothing`` or
    ``legacy``. Both are None when ``num_id`` is 0.
    """

    num_id: int
    level: int
    text: str | None = None
    suffix: str | None = None


@dataclass(frozen=True)
class Mark:
    """``text[start:end]`` is shown as ``kind``.

    One of bold, italic, superscript, subscript, position, caps, smallCaps, strike, dstrike,
    ``highlight-<colour>`` (Word's colour name, e.g. ``highlight-lightGray``),
    ``shading-<FILL>`` (e.g. ``shading-D9D9D9``) or ``shading-<pattern>-<COLOUR>-<FILL>``, rtl
    (right-to-left), faint (a contrast under 1.33:1 with what is painted under it, under two
    points, or scaled under a fifth) and underline. Marks of one kind that touch or overlap are
    merged; marks of different kinds may overlap.
    """

    start: int
    end: int
    kind: str


@dataclass(frozen=True)
class NoteReference:
    """A footnote or endnote mark in a paragraph: Word draws ``mark`` before ``text[offset]``.

    ``kind`` is ``footnote`` or ``endnote`` and ``id`` the note's id. ``mark`` is the number or
    symbol Word draws ("1", "iv", "*"), or None for a note with a custom mark, whose characters
    are stored, and so read, in ``text``. The mark is computed, not stored, so it is never put
    into ``text``. A note's own text repeats its mark where the note's ``footnoteRef`` or
    ``endnoteRef`` stands, and that paragraph carries a reference to the note itself.
    """

    offset: int
    kind: str
    id: int
    mark: str | None = None


@dataclass(frozen=True)
class Paragraph:
    """One paragraph of the body or of a note, as the reader produced it.

    The module docstring describes ``text``, ``marks``, ``mark_hidden``, ``numbering``,
    ``table`` and ``notes``; ``style`` is the paragraph style id written on the paragraph, if
    any.
    """

    text: str
    style: str | None
    numbering: Numbering | None
    table: tuple[int, int, int] | None
    marks: tuple[Mark, ...] = ()
    mark_hidden: bool = False
    notes: tuple[NoteReference, ...] = ()
    # Where Word draws a page number (PAGEREF, PAGE...): set by the layout, so never in ``text``.
    pages: tuple[int, ...] = ()
    # Where a comment's mark stands, and which comment it is.
    comments: tuple[CommentReference, ...] = ()


@dataclass(frozen=True)
class CommentReference:
    """A comment's mark in a paragraph: the comment ``id`` is anchored before ``text[offset]``."""

    offset: int
    id: int


@dataclass(frozen=True)
class Comment:
    """A comment: its id, author, initials and date as stored, and its paragraphs."""

    id: int
    author: str | None
    initials: str | None
    date: str | None
    paragraphs: tuple[Paragraph, ...]
    # Why the reader would not read the comment's text (code, detail), which is then empty.
    refusal: tuple[str, str] | None = None


@dataclass(frozen=True)
class Story:
    """A header or footer part: its name, the sections that use it and how, its paragraphs.

    ``uses`` lists each (section, type) whose reference names this part, sections counted from
    0 in document order, the type ``default``, ``first`` or ``even``.
    """

    kind: str
    part: str
    uses: tuple[tuple[int, str], ...]
    paragraphs: tuple[Paragraph, ...]
    # Why the reader would not read the part's text (code, detail), which is then empty.
    refusal: tuple[str, str] | None = None


@dataclass(frozen=True)
class Note:
    """A footnote or endnote: its id, the mark its references draw, and its paragraphs."""

    kind: str
    id: int
    mark: str | None
    paragraphs: tuple[Paragraph, ...]


@dataclass(frozen=True)
class Document:
    """A document's text: its body, notes, headers, footers and comments.

    The footnotes and endnotes are in the order the body refers to them, the headers and footers
    in the order the sections refer to them, and the comments as stored.
    """

    body: tuple[Paragraph, ...]
    footnotes: tuple[Note, ...] = ()
    endnotes: tuple[Note, ...] = ()
    headers: tuple[Story, ...] = ()
    footers: tuple[Story, ...] = ()
    comments: tuple[Comment, ...] = ()


# --- package -------------------------------------------------------------------------------


def _decode(name: str, data: bytes) -> bytes:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise DocxRefusedError("invalid-package", f"{name} is not UTF-8")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise DocxRefusedError("invalid-package", f"{name} is not UTF-8") from error
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise DocxRefusedError("invalid-package", f"{name} declares a DTD")
    declared = re.match(r"\s*<\?xml[^>]*?encoding\s*=\s*[\"']([^\"']*)[\"']", text)
    if declared is not None and declared.group(1).lower().replace("_", "-") not in (
        "utf-8",
        "utf8",
    ):
        # The parser would honour the declaration and decode the UTF-8 bytes as something else.
        raise DocxRefusedError("invalid-package", f"{name} declares {declared.group(1)}")
    return text.encode("utf-8")


_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_LOCAL_HEADER = b"PK\x03\x04"
_END_RECORD = b"PK\x05\x06"


def _whole_archive(data: bytes) -> None:
    """Refuse data that is not one zip archive from its first byte to its last.

    zipfile opens an archive found anywhere in the data: it skips bytes before it and does not
    look past its end record. A Word 97-2003 document (.doc, an OLE compound file) holds a small
    zip of its theme, and one holding an embedded .docx would be read as that other document.
    EMA's own site serves a .doc under a .docx name.
    """
    if data.startswith(_OLE):
        raise DocxRefusedError("invalid-package", "a Word 97-2003 document (.doc), not a .docx")
    if data.lstrip(b"\x00\t\n\r ")[:5] == b"%PDF-":
        # A PDF holds glyphs placed on a page, not the text and structure Word holds: what it
        # shows can be drawn from a font with no record of the characters meant.
        raise DocxRefusedError(
            "invalid-package", "a PDF, not a .docx: its text cannot be read exactly"
        )
    if not data.startswith(_LOCAL_HEADER):
        raise DocxRefusedError("invalid-package", "not a zip archive from its first byte")
    end = data.rfind(_END_RECORD)
    comment = int.from_bytes(data[end + 20 : end + 22], "little") if end >= 0 else 0
    if end < 0 or len(data) != end + 22 + comment:
        raise DocxRefusedError("invalid-package", "bytes after the zip archive's end record")


class _Package:
    def __init__(self, data: bytes) -> None:
        _whole_archive(data)
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
        except Exception as error:  # zipfile raises many types for a damaged archive
            raise DocxRefusedError("invalid-package", "not a readable zip archive") from error
        if min((info.header_offset for info in self.zip.infolist()), default=0) != 0:
            # The central directory places the first part after the start of the data.
            raise DocxRefusedError("invalid-package", "bytes before the zip archive")
        if sum(info.file_size for info in self.zip.infolist()) > MAX_PACKAGE_BYTES:
            raise DocxRefusedError("invalid-package", f"parts over {MAX_PACKAGE_BYTES} bytes")
        for info in self.zip.infolist():
            name = info.filename
            # zipfile reads a name other than the stored one from an Info-ZIP Unicode Path field
            # (0x7075), and cuts one at a NUL: another zip reader finds another part there.
            if name != info.orig_filename:
                raise DocxRefusedError("invalid-package", "a part name other than the stored one")
            # A backslash, a leading "/", or an empty, "." or ".." segment: not a part name, and
            # maybe the same part as another to another zip reader (ECMA-376 Part 2).
            if (
                "\\" in name
                or name.startswith("/")
                or posixpath.normpath(name) != name
                or ".." in name.split("/")
            ):
                raise DocxRefusedError("invalid-package", "a name that is not a part name")
            # A package's parts are stored or deflated (ECMA-376 Part 2, its ZIP appendix).
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise DocxRefusedError(
                    "invalid-package", f"a part compressed by method {info.compress_type}"
                )
        try:
            # Every part unpacked and its checksum compared, the ones the reader reads and the
            # ones it does not: a damaged package is not the document its author saved.
            damaged = self.zip.testzip()
        except Exception as error:  # zipfile raises many types for a damaged entry
            raise DocxRefusedError("invalid-package", "a part cannot be unpacked") from error
        if damaged is not None:
            raise DocxRefusedError("invalid-package", f"{damaged} is damaged (bad checksum)")
        names = self.zip.namelist()
        # Part names in a package are compared without regard to case (ECMA-376 Part 2).
        if len({name.lower() for name in names}) != len(names):
            raise DocxRefusedError("invalid-package", "a part name occurs twice")
        self.names = set(names)
        # Each part parsed once. The reader never changes a parsed part (``tracked`` writes its
        # views from deep copies of the parsed parts), so the one parse serves every use.
        self.parsed: dict[str, ET.Element] = {}
        self.elements = 0
        self.by_id: dict[str, dict[str | None, tuple[ET.Element, str]]] = {}
        # Every XML part, read or not, must be one the reader could read: UTF-8, no DTD,
        # well-formed. A part Word could not open is not the document its author saved.
        for name in sorted(names):
            if name.endswith((".xml", ".rels")):
                root = self.part(name)
                ids = (
                    [r.get("Id") for r in root]
                    if name.endswith(".rels") and root is not None
                    else []
                )
                if len(set(ids)) != len(ids):
                    # Which one a reference names is not on record; OPC Ids are unique.
                    raise DocxRefusedError("invalid-package", f"{name} repeats a relationship Id")
        if "[Content_Types].xml" not in self.names:
            raise DocxRefusedError("invalid-package", "no [Content_Types].xml")

    def part(self, name: str) -> ET.Element | None:
        if name not in self.names:
            return None
        if name in self.parsed:
            return self.parsed[name]
        info = self.zip.getinfo(name)
        if info.file_size > MAX_PART_BYTES:
            raise DocxRefusedError("invalid-package", f"{name} is over {MAX_PART_BYTES} bytes")
        try:
            data = self.zip.read(name)
        except Exception as error:
            # A bad checksum, a truncated or encrypted entry, an unsupported compression, or a
            # damaged directory: zipfile raises many types, and every one is a refusal.
            raise DocxRefusedError("invalid-package", f"{name} cannot be read") from error
        text = _decode(name, data)
        # Parsed a slice at a time, so depth and size are checked as the tree is built, not after:
        # a few kilobytes of compressed "<b/>" unpack to millions of elements.
        parser: ET.XMLPullParser[ET.Element] = ET.XMLPullParser(("start", "end"))

        def events() -> Iterator[Any]:  # ("start" or "end", element), with the events asked
            for start in range(0, len(text), 1 << 16):
                parser.feed(text[start : start + (1 << 16)])
                yield from parser.read_events()
            parser.close()
            yield from parser.read_events()

        roots: list[ET.Element] = []
        depth = 0
        try:
            for event, element in events():
                if event == "end":
                    depth -= 1
                    continue
                if depth == 0:
                    roots.append(element)
                depth += 1
                self.elements += 1
                # The readers walk a part element by element; no Word document comes near this
                # depth (the deepest in the corpus and 300 generated documents is 19).
                if depth > MAX_DEPTH:
                    raise DocxRefusedError("invalid-package", f"{name} nests over {MAX_DEPTH} deep")
                if self.elements > MAX_ELEMENTS:
                    raise DocxRefusedError(
                        "invalid-package", f"parts over {MAX_ELEMENTS} XML elements"
                    )
        except ET.ParseError as error:
            raise DocxRefusedError("invalid-package", f"{name} is not well-formed") from error
        root = roots[0]
        stack = [root]
        while stack:
            element = stack.pop()
            if not _PROCESSING.isdisjoint(element.keys()):
                # Content a consumer must process or understand by rules the reader does not
                # apply (ECMA-376 Part 3).
                raise DocxRefusedError("unsupported-element", f"{name}: markup compatibility")

            if element.tag in (_w("rPr"), _w("pPr")) and any(
                not c.tag.startswith((f"{{{W}}}", _WORD_EXTENSIONS)) for c in element
            ):
                # A property Word does not know could hide or change the text; Word does not
                # open a document with one it must understand.
                raise DocxRefusedError("unsupported-element", f"an unknown property in {name}")
            stack.extend(element)
        self.parsed[name] = root
        return root

    def _relationships(self, source: str) -> Iterator[tuple[ET.Element, str]]:
        """Each of ``source``'s relationships, with the part name its target resolves to."""
        folder, base = posixpath.split(source)
        rels = self.part(posixpath.join(folder, "_rels", base + ".rels"))
        for rel in [] if rels is None else rels.findall(f"{{{PR}}}Relationship"):
            target = rel.get("Target", "")
            resolved = target[1:] if target.startswith("/") else posixpath.join(folder, target)
            yield rel, posixpath.normpath(resolved)

    @staticmethod
    def _is(rel: ET.Element, kind: str) -> bool:
        """Whether ``rel`` is an internal ``kind`` relationship, of the type Word writes.

        A type that only ends in ``kind`` (another namespace, Strict's) is refused: what Word
        does with it is not on record.
        """
        found = rel.get("Type", "")
        if found.endswith("/" + kind) and found != _RELATIONSHIPS + kind:
            raise DocxRefusedError("invalid-package", f"a relationship of type {found!r}")
        return rel.get("TargetMode") != "External" and found == _RELATIONSHIPS + kind

    def target(self, source: str, relationship: str | None, kind: str) -> str:
        """The part ``source``'s relationship ``relationship`` names; refused unless a ``kind``."""
        if source not in self.by_id:
            # The first relationship of each Id, looked up once per source part.
            by_id: dict[str | None, tuple[ET.Element, str]] = {}
            for rel, name in self._relationships(source):
                by_id.setdefault(rel.get("Id"), (rel, name))
            self.by_id[source] = by_id
        found = self.by_id[source].get(relationship)
        if found is None:
            raise DocxRefusedError("invalid-package", f"no relationship {relationship}")
        if not self._is(found[0], kind):
            raise DocxRefusedError("invalid-package", f"{relationship} is not a {kind} part")
        return found[1]

    def related(self, source: str, kind: str) -> list[str]:
        """Target part names of ``source``'s internal relationships whose type ends in ``kind``."""
        return [name for rel, name in self._relationships(source) if self._is(rel, kind)]


# --- styles --------------------------------------------------------------------------------


@dataclass
class _Style:
    kind: str
    based_on: str | None
    rpr: ET.Element | None
    ppr: ET.Element | None
    # Why the style's formatting for its first row, banded rows and the like could change what
    # the reader produces (the reader does not apply it), or None if it cannot.
    conditional: str | None = None
    # Whether that formatting sets fonts, which could override a Symbol font beneath it.
    conditional_fonts: bool = False
    # A table style's conditional parts (``tblStylePr``) by type (``firstRow``...), and the band
    # sizes its ``tblPr`` sets (``tblStyleRowBandSize``, ``tblStyleColBandSize``), if any.
    parts: dict[str, ET.Element] = field(default_factory=dict)
    bands: tuple[str | None, str | None] = (None, None)
    # A table style's shading of the table and its cells, its parts' (firstRow...) included.
    shadings: tuple[ET.Element, ...] = ()
    # The style's name (w:name), which Word's heading levels and STYLEREF go by.
    name: str | None = None


@dataclass
class _Styles:
    styles: dict[str, _Style] = field(default_factory=dict)
    default_rpr: ET.Element | None = None
    default_ppr: ET.Element | None = None
    # settings.xml asks Word to update every field when the document opens.
    update_fields: bool = False
    defaults: dict[str, str] = field(default_factory=dict)
    theme_fonts: dict[str, str] = field(default_factory=dict)
    has_theme: bool = False
    # Fonts, by lower-case name, the font table says Word may draw as symbols: declared
    # symbol-encoded (charset 02, or the symbol code page in csb0) other than Symbol and
    # Wingdings, embedded (its glyphs may be any), or replaced when missing (altName) by a
    # symbol or dingbat font.
    symbol_encoded: set[str] = field(default_factory=set)
    # _Properties' style levels by (run style, paragraph style, table style, label mark,
    # conditional parts).
    inherited: dict[
        tuple[str | None, str | None, str | None, ET.Element | None, tuple[ET.Element, ...]],
        tuple[list[list[ET.Element | None]], list[ET.Element]],
    ] = field(default_factory=dict)
    # The theme's colours by slot (dk1, lt1, accent1...), and the names (background1, text1...)
    # the settings map onto them.
    theme_colours: dict[str, _Rgb] = field(default_factory=dict)
    colour_names: dict[str, str] = field(default_factory=lambda: dict(_COLOUR_NAMES))
    # What may be painted under the text where nothing nearer is: the white page, and the
    # page colour (w:background), which Word shows on screen and does not print by default.
    page: tuple[_Rgb, ...] = ((0xFF, 0xFF, 0xFF),)

    def colours(
        self, element: ET.Element, value: str, theme: str, tint: str, shade: str
    ) -> list[_Rgb]:
        """The colour an element names; none for ``auto`` or none named.

        ``value``, or the theme colour ``theme`` with its tint or shade, which wins (ECMA-376
        17.3.2.6).
        """
        named = element.get(_w(theme))
        if named is not None:
            slot = self.colour_names.get(named, named)
            colours = self.theme_colours or _SYSTEM_COLOURS
            rgb = colours.get(slot)
            if rgb is None:
                raise DocxRefusedError("unsupported-formatting", f"theme colour {named!r}")
            return [_tinted(rgb, element.get(_w(tint)), element.get(_w(shade)))]
        stated = element.get(_w(value))
        if stated is None or stated.lower() == "auto":
            return []
        if not re.fullmatch(r"[0-9A-Fa-f]{6}", stated):
            raise DocxRefusedError("unsupported-formatting", f"colour {stated!r}")
        return [_rgb(stated)]

    def painted(self, shading: ET.Element | None) -> list[_Rgb]:
        """What a shading element paints; none where it is clear.

        One colour, or more where the reader cannot say which is under a character (stripes).
        """
        if shading is None:
            return []
        pattern = shading.get(_w("val"))
        fill = self.colours(shading, "fill", "themeFill", "themeFillTint", "themeFillShade")
        if pattern in (None, "clear", "nil"):
            return fill
        # A pattern's automatic colour is black over a fill that is otherwise the page.
        colour = self.colours(shading, "color", "themeColor", "themeTint", "themeShade")
        colour, fill = colour or [(0, 0, 0)], fill or [(0xFF, 0xFF, 0xFF)]
        if pattern == "solid":
            return colour
        share = re.fullmatch(r"pct([0-9]{1,3})", pattern)
        if share is not None and int(share.group(1)) <= 100:
            part = int(share.group(1)) / 100
            return [
                (round(a[0] * part + b[0] * (1 - part)), round(a[1] * part + b[1] * (1 - part)),
                 round(a[2] * part + b[2] * (1 - part)))
                for a in colour for b in fill
            ]  # fmt: skip
        return colour + fill

    def theme_font(self, theme: str) -> str:
        """The typeface a theme font reference (``minorHAnsi``...) names; refused if none."""
        if not self.has_theme:
            raise DocxRefusedError("symbol-font", f"theme font {theme} without a theme")
        for prefix in ("major", "minor"):
            if theme.startswith(prefix):
                script = {"HAnsi": "Latin", "Ascii": "Latin"}.get(theme[len(prefix) :])
                key = prefix + (script or theme[len(prefix) :])
                if key in self.theme_fonts:
                    return self.theme_fonts[key]
        raise DocxRefusedError("symbol-font", f"theme font {theme} is not in the theme")

    def effective(self, style_id: str | None, kind: str) -> str | None:
        """``style_id``, or the default style of ``kind`` when it is absent or unknown.

        That is how Word falls back. A reference to a style of another kind is refused: what Word
        does with it is not documented.
        """
        if style_id is None or style_id not in self.styles:
            return self.defaults.get(kind)
        if self.styles[style_id].kind != kind:
            raise DocxRefusedError("unsupported-element", f"{kind} style {style_id!r} is not one")
        return style_id

    def resolve(self, style_id: str | None, kind: str) -> list[_Style]:
        return self.chain(self.effective(style_id, kind))

    def resolve_named(self, style_id: str | None, kind: str) -> list[_Style]:
        """The chain of ``style_id`` if it names a style there, else none (no default)."""
        return self.resolve(style_id, kind) if style_id in self.styles else []

    def chain(self, style_id: str | None) -> list[_Style]:
        """``style_id``'s style and those its ``basedOn`` names in turn.

        A paragraph style based on a character style takes nothing from it, as Word draws it
        [Word's answer, 2026-10]; any other ``basedOn`` naming a style of another kind is refused.
        """
        out: list[_Style] = []
        seen: set[str] = set()
        while style_id is not None and style_id not in seen and style_id in self.styles:
            seen.add(style_id)
            style = self.styles[style_id]
            out.append(style)
            style_id = style.based_on
            based = self.styles.get(style_id) if style_id is not None else None
            if based is not None and based.kind != style.kind:
                if (style.kind, based.kind) != ("paragraph", "character"):
                    raise DocxRefusedError(
                        "unsupported-element", f"a {style.kind} style based on a {based.kind} one"
                    )
                break
        return out


def _styles(
    root: ET.Element | None,
    theme: ET.Element | None,
    fonts: ET.Element | None,
    settings: ET.Element | None = None,
) -> _Styles:
    styles = _Styles()
    mapping = None if settings is None else settings.find(_w("clrSchemeMapping"))
    for name, mapped in [] if mapping is None else mapping.attrib.items():
        # Word writes bg1="light1" t1="dark1"...: the names text and background take.
        short = {"bg1": "background1", "t1": "text1", "bg2": "background2", "t2": "text2"}
        if _local(name) in short:
            styles.colour_names[short[_local(name)]] = _COLOUR_NAMES.get(mapped, mapped)
    scheme = None if theme is None else theme.find(f".//{{{A}}}clrScheme")
    for slot in [] if scheme is None else list(scheme):
        given = next(iter(slot), None)
        value = (
            None
            if given is None
            else given.get("val")
            if _local(given.tag) == "srgbClr"
            else given.get("lastClr")
        )
        if value is not None and re.fullmatch(r"[0-9A-Fa-f]{6}", value):
            styles.theme_colours[_local(slot.tag)] = _rgb(value)
    if fonts is not None:
        for entry in fonts.findall(_w("font")):
            charset = entry.find(_w("charset"))
            name = entry.get(_w("name"), "")
            pages = entry.find(_w("sig"))
            csb0 = "" if pages is None else pages.get(_w("csb0"), "")
            encoded = (charset is not None and charset.get(_w("val"), "").upper() == "02") or (
                re.fullmatch(r"[0-9A-Fa-f]{1,8}", csb0) is not None and int(csb0, 16) >> 31 == 1
            )
            substitute = entry.find(_w("altName"))
            if (
                (encoded and _font_class(name) == "text")
                or any(child.tag.startswith(_w("embed")) for child in entry)
                or (substitute is not None and _font_class(substitute.get(_w("val"))) != "text")
            ):
                styles.symbol_encoded.add(name.lower())
    if theme is not None:
        styles.has_theme = True
        for prefix in ("major", "minor"):
            font = theme.find(f".//{{{A}}}{prefix}Font")
            if font is None:
                continue
            for script, child in (("Latin", "latin"), ("EastAsia", "ea"), ("Bidi", "cs")):
                element = font.find(f"{{{A}}}{child}")
                if element is not None:
                    styles.theme_fonts[prefix + script] = element.get("typeface", "")
    if root is None:
        return styles
    styles.default_rpr = root.find(f"{_w('docDefaults')}/{_w('rPrDefault')}/{_w('rPr')}")
    styles.default_ppr = root.find(f"{_w('docDefaults')}/{_w('pPrDefault')}/{_w('pPr')}")
    for style in root.findall(_w("style")):
        style_id = style.get(_w("styleId"))
        if style_id is None:
            continue
        if style_id in styles.styles:
            raise DocxRefusedError("invalid-package", f"style {style_id!r} is defined twice")
        based = style.find(_w("basedOn"))
        named = style.find(_w("name"))
        kind = style.get(_w("type"), "paragraph")
        styles.styles[style_id] = _Style(
            kind=kind,
            based_on=based.get(_w("val")) if based is not None else None,
            name=named.get(_w("val")) if named is not None else None,
            rpr=style.find(_w("rPr")),
            ppr=style.find(_w("pPr")),
            conditional=_conditional(style, styles),
            conditional_fonts=any(
                part.find(f"{_w('rPr')}/{_w('rFonts')}") is not None
                for part in style.findall(_w("tblStylePr"))
            ),
            shadings=tuple(
                shd
                for path in ("tblPr", "tcPr", "tblStylePr/tblPr", "tblStylePr/tcPr")
                for shd in style.findall(f"{'/'.join(_w(n) for n in path.split('/'))}/{_w('shd')}")
            ),
            parts={part.get(_w("type"), ""): part for part in style.findall(_w("tblStylePr"))},
            bands=(_band_size(style, "Row"), _band_size(style, "Col")),
        )
        if style.get(_w("default")) in ("1", "true", "on"):
            # With more than one default of a kind, the last one is used (ECMA-376 17.7.4.17).
            styles.defaults[kind] = style_id
    return styles


def _band_size(element: ET.Element, way: str) -> str | None:
    """The band size a table style's or table's ``tblPr`` sets for rows or columns, if any."""
    size = element.find(f"{_w('tblPr')}/{_w(f'tblStyle{way}BandSize')}")
    return None if size is None else size.get(_w("val"), "")


# What a table style's conditional formatting may set: bold, italic, capitals and strike, which
# the reader applies where Word does (``_TableLayout``); fonts, sizes and colours checked below to
# be ordinary text, held where applied to what the reader reads without them (``_ParagraphReader.
# run``); and run and paragraph properties it does not report. Cell, row and table properties
# (shading, borders) are not reported either.
_CONDITIONAL_RUN = {
    _w(name)
    for name in (
        "b",
        "bCs",
        "i",
        "iCs",
        "caps",
        "strike",
        "rFonts",
        "sz",
        "szCs",
        "color",
        "kern",
        "spacing",
        "lang",
        "noProof",
    )
}
_CONDITIONAL_PARAGRAPH = {
    _w(name)
    for name in (
        "spacing",
        "jc",
        "ind",
        "keepNext",
        "keepLines",
        "contextualSpacing",
        "widowControl",
        "tabs",
        "suppressAutoHyphens",
        "pBdr",
        "snapToGrid",
    )
}


def _conditional(style: ET.Element, styles: _Styles) -> str | None:
    """Why a table style's conditional formatting could change what the reader produces.

    None when every property it sets is bold, italic, capitals or strike (applied where Word
    applies them), one the reader does not report, or a font, size or colour that cannot make
    text faint or Symbol (held, where applied, to what the reader reads without it; a Symbol
    font beneath it is refused, ``_in_symbol``). A part defined twice is not on record.
    """
    seen: set[str] = set()
    for part in style.findall(_w("tblStylePr")):
        kind = part.get(_w("type"), "")
        if kind in seen:
            return f"conditional table formatting ({kind}) defined twice"
        seen.add(kind)
        rpr = part.find(_w("rPr"))
        for child in [] if rpr is None else list(rpr):
            if child.tag not in _CONDITIONAL_RUN:
                return f"conditional table formatting ({kind}) sets {_local(child.tag)}"
            if child.tag == _w("rFonts"):
                for slot, theme in _THEME_ATTRIBUTE.items():
                    name = child.get(_w(theme))
                    name = styles.theme_font(name) if name else child.get(_w(slot))
                    if _font_kind(styles, name) != "text":
                        return f"conditional table formatting ({kind}) sets the font {name}"
            elif child.tag in (_w("sz"), _w("szCs")) and _tiny(child.get(_w("val"))):
                return f"conditional table formatting ({kind}) sets a tiny size"
            elif child.tag == _w("color") and any(
                _contrast(c, (0xFF, 0xFF, 0xFF)) < _FAINT_CONTRAST
                for c in styles.colours(child, "val", "themeColor", "themeTint", "themeShade")
            ):
                return f"conditional table formatting ({kind}) sets a faint colour"
        ppr = part.find(_w("pPr"))
        for child in [] if ppr is None else list(ppr):
            if child.tag not in _CONDITIONAL_PARAGRAPH:
                return f"conditional table formatting ({kind}) sets {_local(child.tag)}"
    return None


def _on(element: ET.Element | None) -> bool | None:
    if element is None:
        return None
    value = element.get(_w("val"))
    return value is None or value.lower() not in {"0", "false", "off"}


class _Properties:
    """The run properties in force for one run, from the run outwards.

    ``mark``, for a list label, is the paragraph mark's run properties, which the label's level
    properties (``direct``) sit over. ``conditional`` is the run properties of the table style's
    conditional parts Word applies to the cell, nearest first (``_TableLayout``): they stand over
    the table style's own at its level, as Word's answers have it (table-style-base-and-row).
    """

    def __init__(
        self,
        styles: _Styles,
        direct: ET.Element | None,
        paragraph_style: str | None,
        table_style: str | None,
        mark: ET.Element | None = None,
        conditional: tuple[ET.Element, ...] = (),
    ) -> None:
        self.styles = styles
        self.direct = direct
        run_style = None
        if direct is not None:
            element = direct.find(_w("rStyle"))
            run_style = element.get(_w("val")) if element is not None else None
        self.mark = mark
        # The same for every run with these styles, so worked out once per read (a refusal is
        # never kept: it is raised again).
        key = (run_style, paragraph_style, table_style, mark, conditional)
        if key not in styles.inherited:
            # Each kind of style with its basedOn chain, nearest first: the character, paragraph
            # and (inside a table only; ``table_style`` is already resolved) table style.
            chains = [
                # A run naming no character style, or one not there, takes none: Word does not
                # apply the default character style to text [default-character-style].
                [style.rpr for style in styles.resolve_named(run_style, "character")],
                [style.rpr for style in styles.resolve(paragraph_style, "paragraph")],
                [*conditional, *(style.rpr for style in styles.chain(table_style))],
            ]
            # The mark's own character style, as for a run: Word draws the label in it (Sym
            # gives Symbol, Caps capitals) [Word's answer, 2026-10].
            marked = None if mark is None else mark.find(_w("rStyle"))
            mark_style = None if marked is None else marked.get(_w("val"))
            levels: list[ET.Element | None] = [
                mark,
                *(style.rpr for style in styles.resolve_named(mark_style, "character")),
                *(rpr for chain in chains for rpr in chain),
            ]
            levels.append(styles.default_rpr)
            styles.inherited[key] = (chains, [level for level in levels if level is not None])
        self.chains, self.inherited = styles.inherited[key]

    def toggle(self, name: str) -> bool:
        """True when the run asserts it, or when it is silent and any level asserts it.

        The cautious reading, for hiding: it may find a property where Word's rules cancel it
        (``shown``), never miss one.
        """
        direct = _on(self.direct.find(_w(name))) if self.direct is not None else None
        if direct is not None:
            return direct
        return any(_on(level.find(_w(name))) for level in self.inherited)

    def shown(self, name: str) -> bool:
        """Whether Word shows a toggle property (bold, italic, caps, strike...) on this run.

        Word's rules, each its answer to a case in corpus/numbering-cases (emphasis-toggles,
        emphasis-defaults, emphasis-defaults-off): the run's own setting wins, on or off;
        otherwise it starts as the document defaults set it (off where they say nothing), and
        each kind of style (character, paragraph, table) whose nearest setting in its basedOn
        chain differs from that turns it over, so two such kinds cancel. With the defaults off,
        each style that turns it on turns it over; with them on, each that turns it off. A list
        label (``mark``) is read the cautious way.
        """
        if self.mark is not None:
            return self.toggle(name)
        direct = _on(self.direct.find(_w(name))) if self.direct is not None else None
        if direct is not None:
            return direct
        default = self.styles.default_rpr
        shown = bool(default is not None and _on(default.find(_w(name))))
        start = shown
        for chain in self.chains:
            setting = next(
                (
                    v
                    for rpr in chain
                    if rpr is not None and (v := _on(rpr.find(_w(name)))) is not None
                ),
                None,
            )
            if setting is not None and setting != start:
                shown = not shown
        return shown

    def levels(self) -> list[ET.Element]:
        """Every level's run properties, the run's own first."""
        return [level for level in [self.direct, *self.inherited] if level is not None]

    def value(self, name: str, attribute: str = "val") -> str | None:
        for level in [self.direct, *self.inherited]:
            if level is None:
                continue
            element = level.find(_w(name))
            if element is not None and element.get(_w(attribute)) is not None:
                return element.get(_w(attribute))
        return None

    def element(self, name: str) -> ET.Element | None:
        """The nearest level's ``name`` element, whole, so its attributes stay together."""
        for level in [self.direct, *self.inherited]:
            if level is not None and (found := level.find(_w(name))) is not None:
                return found
        return None

    def font(self, slot: str) -> str | None:
        """The effective font for ``ascii``, ``hAnsi``, ``eastAsia`` or ``cs``."""
        for level in [self.direct, *self.inherited]:
            if level is None:
                continue
            fonts = level.find(_w("rFonts"))
            if fonts is None:
                continue
            theme = fonts.get(_w(_THEME_ATTRIBUTE[slot]))
            if theme is not None:
                return self.styles.theme_font(theme)
            name = fonts.get(_w(slot))
            if name is not None:
                return name
        return None


_THEME_ATTRIBUTE = {
    "ascii": "asciiTheme",
    "hAnsi": "hAnsiTheme",
    "eastAsia": "eastAsiaTheme",
    "cs": "cstheme",
}


def _font_class(name: str | None) -> str:
    if name is None:
        return "text"
    # Each exactly: another spelling ("SymbolMT", "symbol") is refused below, not guessed.
    if name == "Symbol":
        return "symbol"
    if name == "Wingdings":
        return "wingdings"
    if name == "Segoe UI Symbol":  # a Unicode font: Word draws each character as stored
        return "text"
    key = name.lower().replace(" ", "")
    if any(part in key for part in _DINGBAT_FONTS):
        return "dingbat"
    return "text"


def _font_kind(styles: _Styles, name: str | None) -> str:
    if name is not None and name.lower() in styles.symbol_encoded:
        return "dingbat"
    return _font_class(name)


def _in_symbol(styles: _Styles, properties: _Properties, table_style: str | None) -> bool:
    """Whether every character with these properties is drawn in Symbol; refused if unsure."""
    kinds = {
        slot: _font_kind(styles, properties.font(slot))
        for slot in ("ascii", "hAnsi", "eastAsia", "cs")
    }
    if "dingbat" in kinds.values() or "wingdings" in kinds.values():
        raise DocxRefusedError("symbol-font", "a run in a dingbat or symbol-encoded font")
    symbol = "symbol" in kinds.values()
    if symbol and (
        kinds["ascii"] != "symbol"
        or kinds["hAnsi"] != "symbol"
        or properties.toggle("cs")
        or properties.toggle("rtl")
        or properties.value("rFonts", "hint") not in (None, "default")
    ):
        # Word chooses the font per character from these slots; the reader maps a run only
        # when every Latin character is certain to be drawn in Symbol.
        raise DocxRefusedError("symbol-font", "Symbol set for only some characters")
    if symbol and any(s.conditional_fonts for s in styles.chain(table_style)):
        # The table style's conditional formatting, which the reader does not apply, may set
        # another font over it.
        raise DocxRefusedError("symbol-font", "Symbol under conditional table fonts")
    return symbol


def _label_font(styles: _Styles, properties: _Properties, table_style: str | None) -> str:
    """The font a list label is drawn in: ``symbol``, ``wingdings`` or ``text``; refused if unsure.

    As for a run (``_in_symbol``): a symbol font only where both Latin slots name it, with no
    complex-script, right-to-left or hint to send a character elsewhere, and no conditional
    table font over it.
    """
    kinds = {
        slot: _font_kind(styles, properties.font(slot))
        for slot in ("ascii", "hAnsi", "eastAsia", "cs")
    }
    if "dingbat" in kinds.values():
        raise DocxRefusedError("symbol-font", "a list label in a dingbat or symbol-encoded font")
    for font in ("symbol", "wingdings"):
        if font not in kinds.values():
            continue
        if (
            kinds["ascii"] != font
            or kinds["hAnsi"] != font
            or properties.toggle("cs")
            or properties.toggle("rtl")
            or properties.value("rFonts", "hint") not in (None, "default")
        ):
            raise DocxRefusedError("symbol-font", f"{font} set for only some of a list label")
        if any(s.conditional_fonts for s in styles.chain(table_style)):
            raise DocxRefusedError("symbol-font", f"{font} under conditional table fonts")
        return font
    return "text"


def _bullet(code: int) -> str:
    low = code - 0xF000 if 0xF000 <= code <= 0xF0FF else code
    if low not in WINGDINGS_BULLETS:
        raise DocxRefusedError("unmapped-symbol", f"Wingdings code {code:#06x}")
    return WINGDINGS_BULLETS[low]


def _characters(text: str, symbol: bool) -> str:
    """``text`` as drawn: mapped through the Symbol table in a Symbol run, else as stored."""
    out: list[str] = []
    for character in text:
        code = ord(character)
        if symbol:
            if code > 0xFF and not 0xF000 <= code <= 0xF0FF:
                raise DocxRefusedError("unmapped-symbol", f"U+{code:04X} in a Symbol run")
            out.append(_symbol(code, "w:t"))
        elif _private_use(code):
            raise DocxRefusedError("private-use-character", f"U+{code:04X}")
        elif character == OBJECT:
            raise DocxRefusedError("reserved-character", "U+FFFC stands for a picture")
        elif unicodedata.category(character) in ("Cf", "Cc") or code in _IGNORABLE:
            # Drawn as nothing, or reordering what is drawn around it (a bidirectional control),
            # or a control drawn as a blank or a box; Word writes a soft hyphen as w:softHyphen.
            raise DocxRefusedError("format-character", f"U+{code:04X}")
        elif unicodedata.category(character) == "Cn":
            raise DocxRefusedError("unassigned-character", f"U+{code:04X}")
        else:
            out.append(character)
    return "".join(out)


def _symbol(code: int, where: str) -> str:
    low = code - 0xF000 if 0xF000 <= code <= 0xF0FF else code
    if low not in SYMBOL_FONT:
        raise DocxRefusedError("unmapped-symbol", f"{where}: Symbol code {code:#06x}")
    return SYMBOL_FONT[low]


# Scripts Word draws as complex script whatever the run says: Hebrew, Arabic, Syriac, Thaana,
# N'Ko and their neighbours; the Indic scripts; Thai, Lao, Tibetan, Myanmar, Khmer; and the
# Hebrew and Arabic presentation forms.
_COMPLEX_SCRIPT = (
    (0x0590, 0x08FF),
    (0x0900, 0x0DFF),
    (0x0E00, 0x109F),
    (0x1780, 0x17FF),
    (0xFB1D, 0xFDFF),
    (0xFE70, 0xFEFF),
)


def _complex_script(character: str) -> bool:
    return any(low <= ord(character) <= high for low, high in _COMPLEX_SCRIPT)


def _private_use(code: int) -> bool:
    return 0xE000 <= code <= 0xF8FF or 0xF0000 <= code <= 0x10FFFF


# --- paragraphs ----------------------------------------------------------------------------


def _drawing(element: ET.Element) -> str:
    """A picture, placed by ``_placed``; one that can hold text, or is not a picture, is refused."""
    for node in element.iter():
        local = _local(node.tag)
        if local in ("t", "txbx", "txbxContent"):
            raise DocxRefusedError("unsupported-element", "drawing with text")
        if local == "graphicData" and node.get("uri") != PICTURE_URI:
            raise DocxRefusedError("unsupported-element", f"drawing of {node.get('uri')}")
    return _placed(element)


def _placed(drawing: ET.Element) -> str:
    """U+FFFC for a drawing in line with the text; nothing for one anchored to the paragraph.

    Word's text shows a drawing in line ("/") and none anchored, which floats apart from the
    text [drawing-inline-picture, drawing-anchored-picture, drawing-anchored-line].
    """
    frames = [c.tag for c in drawing]
    if frames == [f"{{{WP}}}inline"]:
        properties = drawing[0].find(f"{{{WP}}}docPr")
        if properties is not None and properties.get("hidden") in ("1", "true"):
            # Not drawn; whether Word's text shows it is not on record.
            raise DocxRefusedError("unsupported-element", "a hidden drawing")
        return OBJECT
    if frames == [f"{{{WP}}}anchor"]:
        return ""
    raise DocxRefusedError("unsupported-element", "drawing neither in line nor anchored")


def _vml_picture(element: ET.Element) -> str:
    """A VML picture (``w:pict`` of one image): in line, one U+FFFC; positioned absolutely, none.

    Word writes pictures this way in documents from before Word 2007 and when saving for them.
    Word's text shows one in line ("/") and none positioned absolutely, which floats apart from
    the text [drawing-vml-inline-picture, drawing-vml-floating-picture]. A text box, WordArt, an
    embedded object or control (any WordprocessingML element inside), a drawn shape, a group, a
    hidden shape or any other position is refused.
    """
    locals_ = {_local(node.tag) for node in element.iter()}
    if locals_ & {"textbox", "txbxContent", "textpath", "t", "OLEObject"} or any(
        node.tag.startswith(f"{{{W}}}") for node in element.iter() if node is not element
    ):
        # Any of Word's own elements in it (an ActiveX control, w:control) can carry text.
        raise DocxRefusedError("unsupported-element", "pict with text or an embedded object")
    holders = [n for n in element.iter() if any(_local(c.tag) == "imagedata" for c in n)]
    if len(holders) != 1 or "group" in locals_:
        raise DocxRefusedError("unsupported-element", "pict that is not one picture")
    style = {
        key.strip().lower(): value.strip().lower()
        for key, _, value in (
            part.partition(":") for part in holders[0].get("style", "").split(";")
        )
        if key.strip()
    }
    if style.get("visibility", "visible") != "visible":
        raise DocxRefusedError("unsupported-element", "pict that is hidden")
    position = style.get("position")
    if position is None:
        return OBJECT
    if position == "absolute":
        return ""
    raise DocxRefusedError("unsupported-element", f"pict positioned {position}")


_SHAPE_URI = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
_ALTERNATE = f"{{{MC}}}AlternateContent"


def _alternate(element: ET.Element) -> str:
    """Alternate content in a run: a drawing that holds no text, read as a picture is (``_placed``).

    Word draws the choice it supports (a DrawingML shape, ``wps``: a line, a box) and keeps the
    fallback (VML) for older versions. Read only where no branch can hold text and the drawn
    branch is one picture or shape; a text box, WordArt or anything else is refused.
    [drawing-inline-shape, drawing-anchored-shape, drawing-anchored-line]
    """
    choices = [c for c in element if c.tag == f"{{{MC}}}Choice"]
    if len(choices) != 1 or choices[0].get("Requires") != "wps":
        raise DocxRefusedError("unsupported-element", "AlternateContent")
    texts = {"t", "txbx", "txbxContent", "textbox", "textpath", "OLEObject", "AlternateContent"}
    # Of Word's own elements, only the drawing and its VML fallback: no run content in any branch.
    pictures = {_w("drawing"), _w("pict")}
    if any(
        _local(n.tag) in texts or (n.tag.startswith(f"{{{W}}}") and n.tag not in pictures)
        for n in element.iter()
        if n is not element
    ):
        raise DocxRefusedError("unsupported-element", "AlternateContent that can hold text")
    drawn = list(choices[0])
    graphics = [n for n in choices[0].iter() if _local(n.tag) == "graphicData"]
    # Each graphic is what its uri names, in that namespace: a picture or a Word shape.
    if (
        [c.tag for c in drawn] != [_w("drawing")]
        or not graphics
        or any(
            g.get("uri") not in (PICTURE_URI, _SHAPE_URI)
            or [c.tag for c in g]
            != [f"{{{g.get('uri')}}}{'pic' if g.get('uri') == PICTURE_URI else 'wsp'}"]
            for g in graphics
        )
    ):
        raise DocxRefusedError("unsupported-element", "AlternateContent that is not a drawing")
    return _placed(drawn[0])


class _ParagraphReader:
    def __init__(
        self,
        styles: _Styles,
        paragraph_style: str | None,
        table_style: str | None,
        runs: set[ET.Element],
        story: tuple[str, int] | None,
        carried: int = 0,
        under: tuple[_Rgb, ...] = (),
        conditional: tuple[ET.Element, ...] = (),
    ) -> None:
        self.styles = styles
        # The table style's conditional parts Word applies to the paragraph's cell (_Properties).
        self.conditional = conditional
        # Fields an earlier paragraph left open in their results, which an end here may close.
        self.carried = carried
        # What may be painted under the paragraph's text (its shading, its cell's, the page).
        self.under = under or styles.page
        # The largest size the paragraph's text is drawn at, in points.
        self.line = 0.0
        self.paragraph_style = paragraph_style
        self.table_style = table_style
        # Every run read, shared across the story, for the accounting in _check_accounted.
        self.runs = runs
        # The story being read (a note, header, footer or comment, with its id or index), or None
        # in the body.
        self.story = story
        self.notes: list[NoteReference] = []
        self.custom: set[tuple[str, int]] = set()
        # Whether any character is read through the Symbol table (w:sym, or text in Symbol).
        self.symbolic = False
        # Where each comment's mark stands, and which comment it is.
        self.comments: list[CommentReference] = []
        # Fields whose result the reader checks against its own computation: the instruction and
        # where the stored result stands in the text. ``results`` follows ``fields``: the start
        # of each open field's result if it is one of those, else None.
        self.computed: list[tuple[str, int, int]] = []
        self.results: list[int | None] = []
        # Where a page number stands, and how many layout fields' results are open: their text
        # is the page number when Word last laid the document out, not what it prints.
        self.pages: list[int] = []
        self.layout = 0
        self.layout_open: list[bool] = []
        # Whether each open field is locked (fldLock): Word does not update it.
        self.locked: list[bool] = []
        # Bookmark starts (id, name, offset) and ends (id, offset), for REF and NOTEREF, each
        # with the number of note marks before it: a mark has no width, so its offset alone
        # cannot tell inside a bookmark from next to it.
        self.bookmark_starts: list[tuple[str, str, int, int]] = []
        self.bookmark_ends: list[tuple[str, int, int]] = []
        self.parts: list[str] = []
        self.length = 0
        self.marks: list[Mark] = []
        # Where in ``marks`` the last mark of each kind is.
        self.last_mark: dict[str, int] = {}
        # One entry per open field: True while in its instruction, False once in its result.
        self.fields: list[bool] = []
        # The instruction text of each open field, collected while in its instruction.
        self.instructions: list[list[str]] = []
        self.rtl = 0
        # How many embeddings (w:dir) hold the run, of either direction.
        self.embedded = 0

    def in_instruction(self) -> bool:
        return True in self.fields

    def container(self, element: ET.Element) -> None:
        for child in element:
            tag = child.tag
            if tag == _w("r"):
                self.run(child)
            elif tag == _w("fldSimple"):
                if self.styles.update_fields:
                    raise DocxRefusedError("computed-field", "the document updates fields on open")
                if child.get(_w("dirty")) in ("1", "true", "on"):
                    raise DocxRefusedError("stale-field", "a field marked for update")
                instruction = child.get(_w("instr"), "")
                before = self.length
                code = self._shown(instruction, self.in_instruction(), _locked(child), before)
                if code in _LAYOUT_FIELDS and not self.layout:
                    self.pages.append(self.length)
                    self.layout += 1
                    self.container(child)
                    self.layout -= 1
                    continue
                self.container(child)
                if self.length == before:
                    raise DocxRefusedError("field-without-result", "a simple field shows nothing")
                if code in _COMPUTED_FIELDS:
                    self.computed.append((instruction, before, self.length))
            elif tag == _w("bdo"):
                # An override draws every character in one order ("10 mg" as "gm 01"), which
                # no mark says; what Word draws is not on record.
                raise DocxRefusedError("unsupported-element", "a bidirectional override (bdo)")
            elif tag == _w("dir"):
                if child.get(_w("val")) not in ("rtl", "ltr"):
                    raise DocxRefusedError("unsupported-element", "an embedding of no direction")
                rtl = child.get(_w("val")) == "rtl"
                self.rtl += rtl
                self.embedded += 1
                self.container(child)
                self.embedded -= 1
                self.rtl -= rtl
            elif tag in _INLINE_TRANSPARENT:
                self.container(child)
            elif tag == _w("sdt"):
                _content_control(child)
                content = child.find(_w("sdtContent"))
                if content is not None:
                    self.container(content)
            elif tag == _w("bookmarkStart"):
                self.bookmark_starts.append(
                    (
                        child.get(_w("id"), ""),
                        child.get(_w("name"), ""),
                        self.length,
                        len(self.notes),
                    )
                )
            elif tag == _w("bookmarkEnd"):
                self.bookmark_ends.append((child.get(_w("id"), ""), self.length, len(self.notes)))
            elif tag in _PROPERTIES or tag in _MARKERS:
                continue
            else:
                raise DocxRefusedError("unsupported-element", _local(tag))

    def run(self, run: ET.Element) -> None:
        self.runs.add(run)
        rpr = run.find(_w("rPr"))
        properties = _Properties(
            self.styles, rpr, self.paragraph_style, self.table_style, None, self.conditional
        )
        symbol = _in_symbol(self.styles, properties, self.table_style)
        # The same without the conditional parts: their fonts, sizes, colours and spacing are not
        # on record (Word answered bold, italic, capitals and strike), so the reader reads only
        # what they cannot change.
        plain = (
            _Properties(self.styles, rpr, self.paragraph_style, self.table_style)
            if self.conditional
            else properties
        )
        if plain is not properties and _in_symbol(self.styles, plain, self.table_style) != symbol:
            raise DocxRefusedError("symbol-font", "a font set by conditional table formatting")
        if symbol and self.embedded:
            # Drawn as complex script, as under rtl: Word may draw it in another font.
            raise DocxRefusedError("symbol-font", "Symbol in a bidirectional embedding")
        # A hidden run's text is whitespace (or refused) and dropped, so a mark or field
        # character in it stands where the run starts.
        hidden = properties.toggle("vanish")
        emitted: list[str] = []

        def here() -> int:
            return self.length if hidden else self.length + sum(len(part) for part in emitted)

        # Page-number text, left out of the text but still drawn: it must not be hidden.
        placed: list[str] = []
        references: list[NoteReference] = []
        comments: list[CommentReference] = []
        for child in run:
            tag = child.tag
            if tag == _w("commentReference"):
                if self.story is not None and self.story[0] == "comment":
                    raise DocxRefusedError("unsupported-element", "a comment mark in a comment")
                if self.in_instruction():
                    raise DocxRefusedError("unsupported-element", "a comment mark in a field code")
                if hidden:
                    raise DocxRefusedError("hidden-text", "a hidden comment mark")
                comments.append(
                    CommentReference(here(), _int(child.get(_w("id"), ""), "comment id"))
                )
                continue
            if tag == _w("annotationRef"):
                # A comment's echo of its own mark: drawn by Word, no text.
                if self.story is None or self.story[0] != "comment":
                    raise DocxRefusedError("unsupported-element", "annotationRef outside a comment")
                continue
            if tag in _NOTE_REFERENCES:
                if self.in_instruction():
                    raise DocxRefusedError("unsupported-element", "a note mark in a field code")
                references.append(self._note(child, here()))
                continue
            if tag == _w("fldChar"):
                self._field(child, here())
                continue
            if tag == _w("instrText"):
                if not (self.fields and self.fields[-1]):
                    raise DocxRefusedError("unbalanced-field", "field code outside an instruction")
                self.instructions[-1].append(child.text or "")
                continue
            if tag in _RUN_SILENT:
                continue
            if tag == _w("t"):
                text = child.text or ""
                _check_whitespace(child, text)
                produced = _characters(text, symbol)
                self.symbolic = self.symbolic or (symbol and bool(text))
            else:
                produced = self._special(child)
            if not self.in_instruction():
                (placed if self.layout else emitted).append(produced)
            elif self.fields[-1]:
                self.instructions[-1].append(produced)
        text = "".join(emitted)
        if references and hidden:
            raise DocxRefusedError("hidden-text", "a hidden note mark")
        if "".join(placed).strip() and hidden:
            raise DocxRefusedError("hidden-text", "a hidden page number")
        self.notes += references
        self.comments += comments
        if not text:
            return
        if hidden:
            if not properties.shown("vanish"):
                # Hidden by the cautious reading, shown by Word's toggle rule (a nearer style
                # turns it off, or two kinds of style cancel): what Word shows is not on record.
                raise DocxRefusedError("hidden-text", "hiding that Word's toggle rule cancels")
            if text.strip():
                raise DocxRefusedError("hidden-text", "a hidden run carries text")
            return
        complex_script = (
            self.rtl
            or properties.toggle("rtl")
            or properties.toggle("cs")
            or any(_complex_script(c) for c in text)
        )
        if complex_script and any(
            properties.shown(latin) != properties.shown(latin + "Cs") for latin in ("b", "i")
        ):
            # Word draws complex script with bCs and iCs (MS-OI29500 2.1.68-2.1.81), and its
            # Font object reports b and i: what is drawn where they differ is not on record.
            raise DocxRefusedError(
                "unsupported-formatting", "complex script whose b and bCs, or i and iCs, differ"
            )
        for each in (properties,) if plain is properties else (properties, plain):
            size = _size(each)
            condensed = _twips(each.element("spacing"), "val")
            if each.element("fitText") is not None or (
                condensed is not None and -condensed / 20 > size / 4
            ):
                # Text squeezed into a width, or condensed by more than a quarter of its size:
                # its characters may be drawn over one another.
                raise DocxRefusedError("unsupported-formatting", "text drawn over itself")
            self.line = max(self.line, size)
        if plain is not properties and _faint(plain, self.under) != _faint(properties, self.under):
            raise DocxRefusedError(
                "unsupported-element", "conditional table formatting may make text faint or not"
            )
        start = self.length
        self.parts.append(text)
        self.length += len(text)
        self._mark(properties, start, self.length)

    def _note(self, child: ET.Element, offset: int) -> NoteReference:
        """A note reference in the body, or a note's echo of its own mark in the note."""
        tag = _local(child.tag)
        kind = "footnote" if tag.startswith("footnote") else "endnote"
        if tag.endswith("Ref"):
            # footnoteRef or endnoteRef: where a note repeats the mark that refers to it.
            if self.story is None or self.story[0] != kind:
                raise DocxRefusedError("unsupported-element", f"{tag} outside a {kind}")
            return NoteReference(offset, kind, self.story[1])
        if self.story is not None:
            raise DocxRefusedError("unsupported-element", f"{tag} inside a {self.story[0]}")
        note = _int(child.get(_w("id"), ""), f"{tag} id")
        if child.get(_w("customMarkFollows")) in ("1", "true", "on"):
            self.custom.add((kind, note))
        return NoteReference(offset, kind, note)

    def _field(self, child: ET.Element, offset: int) -> None:
        if len(child):
            raise DocxRefusedError("unsupported-element", "form field")
        if child.get(_w("dirty")) in ("1", "true", "on"):
            raise DocxRefusedError("stale-field", "a field marked for update")
        kind = child.get(_w("fldCharType"))
        if kind == "begin":
            if self.styles.update_fields:
                raise DocxRefusedError("computed-field", "the document updates fields on open")
            if self.fields and self.fields[-1]:
                # Word puts this field's result into the enclosing instruction, so the reader
                # no longer knows that instruction's code; a NUL keeps it from matching one.
                self.instructions[-1].append("\x00")
            self.fields.append(True)
            self.instructions.append([])
            self.results.append(None)
            self.layout_open.append(False)
            self.locked.append(_locked(child))
        elif kind == "separate" and self.fields and self.fields[-1]:
            # The result is shown, so it must be one Word shows as stored, one the reader
            # computes and checks, or a page number.
            instruction = "".join(self.instructions[-1])
            code = self._shown(instruction, any(self.fields[:-1]), self.locked[-1], offset)
            if code in _COMPUTED_FIELDS:
                self.results[-1] = offset
            elif code in _LAYOUT_FIELDS and not self.layout:
                self.pages.append(offset)
                self.layout += 1
                self.layout_open[-1] = True
            self.fields[-1] = False
        elif kind == "end" and not self.fields and self.carried:
            self.carried -= 1
        elif kind == "end" and self.fields:
            if self.fields[-1]:
                # No separate: the field stores no result, and what Word shows is computed. A
                # hidden SEQ (\h, as WordPerfect conversions leave "SEQ CHAPTER \h \r 1") shows
                # nothing and counts: it is checked and counted as any SEQ, its result empty.
                instruction = "".join(self.instructions[-1])
                words = instruction.upper().split()
                if any(self.fields[:-1]) or words[:1] != ["SEQ"] or "\\H" not in words:
                    raise DocxRefusedError("field-without-result", "a field with no stored result")
                _check_field(instruction)
                self.results[-1] = offset
                self.fields[-1] = False
            start = self.results.pop()
            if start is not None:
                self.computed.append(("".join(self.instructions[-1]), start, offset))
            if self.layout_open.pop():
                self.layout -= 1
            self.fields.pop()
            self.instructions.pop()
            self.locked.pop()
        else:
            # A second separator would show text no check covers; a stray one, or a kind Word
            # does not write, leaves the field's extent unknown.
            raise DocxRefusedError("unbalanced-field", f"a field character {kind!r} out of place")

    def _shown(self, instruction: str, nested: bool, locked: bool, offset: int) -> str | None:
        """The code of a field whose result is shown, or None for one in another field's code.

        A field in another's code shows nothing, so one the reader computes there would go
        uncounted and is refused. A PAGEREF's bookmark is checked with the computed fields.
        """
        if nested:
            if _code(instruction) in _COMPUTED_FIELDS:
                raise DocxRefusedError("computed-field", "a computed field in another field's code")
            return None
        code = _check_field(instruction)
        if locked and code in _LAYOUT_FIELDS:
            # Word shows a locked field's stored text; what it prints is not on record.
            raise DocxRefusedError("computed-field", f"a locked {code} field")
        if code == "PAGEREF":
            self.computed.append((instruction, offset, offset))
        return code

    def _special(self, child: ET.Element) -> str:
        tag = child.tag
        if tag in (_w("tab"), _w("ptab")):
            return "\t"
        if tag == _w("br"):
            return "" if child.get(_w("type")) in ("page", "column") else "\n"
        if tag == _w("cr"):
            return "\n"
        if tag == _w("noBreakHyphen"):
            return "\u2011"
        if tag == _w("softHyphen"):
            return "\u00ad"
        if tag == _w("sym"):
            if _font_kind(self.styles, child.get(_w("font"))) != "symbol":
                raise DocxRefusedError("unmapped-symbol", f"w:sym in {child.get(_w('font'))!r}")
            char = child.get(_w("char"), "")
            if not re.fullmatch(r"[0-9A-Fa-f]{1,4}", char):
                raise DocxRefusedError("unmapped-symbol", "w:sym without a hex code")
            self.symbolic = True
            return _symbol(int(char, 16), "w:sym")
        if tag == _w("drawing"):
            return _drawing(child)
        if tag == _w("pict"):
            return _vml_picture(child)
        if tag == _ALTERNATE:
            return _alternate(child)
        raise DocxRefusedError("unsupported-element", _local(tag))

    def _mark(self, properties: _Properties, start: int, end: int) -> None:
        kinds: list[str] = []
        vertical = properties.value("vertAlign")
        if vertical in ("superscript", "subscript"):
            kinds.append(vertical)
        if properties.value("position") not in (None, "0"):
            kinds.append("position")
        kinds += [kind for name, kind in _TOGGLE_MARKS.items() if properties.shown(name)]
        highlight = properties.value("highlight")
        if highlight not in (None, "none"):
            kinds.append(f"highlight-{highlight}")
        shading = _shading(properties.element("shd"))
        if shading is not None:
            kinds.append(shading)
        if (
            properties.toggle("rtl")
            and _on(None if properties.direct is None else properties.direct.find(_w("rtl")))
            is None
        ):
            # Word does not allow rtl in styles or the defaults (MS-OI29500 17.7.5.4, 17.7.9.1);
            # whether it draws such text right to left is not on record.
            raise DocxRefusedError("unsupported-formatting", "right-to-left set by a style")
        if self.rtl or properties.toggle("rtl"):
            kinds.append("rtl")
        if any(level.find(f"{{{W14}}}textFill") is not None for level in properties.levels()):
            # Word 2010's text effects fill the glyphs (with nothing, white, a gradient...);
            # whether the text can be seen is not on record.
            raise DocxRefusedError("unsupported-formatting", "a text fill (w14:textFill)")
        if _faint(properties, self.under):
            kinds.append("faint")
        if properties.value("u") not in (None, "none"):
            kinds.append("underline")
        for kind in kinds:
            index = self.last_mark.get(kind)
            if index is not None and self.marks[index].end == start:
                self.marks[index] = Mark(self.marks[index].start, end, kind)
            else:
                self.last_mark[kind] = len(self.marks)
                self.marks.append(Mark(start, end, kind))


def _shading(element: ET.Element | None) -> str | None:
    """The mark kind a shading element gives, or None for no shading or a white one.

    ``shading-<fill>`` for a plain fill, ``shading-<pattern>-<colour>-<fill>`` for a pattern.
    """
    if element is None:
        return None
    pattern = element.get(_w("val"))
    fill = (element.get(_w("fill")) or "auto").upper()
    if element.get(_w("themeFill")) is not None:
        fill = "THEME-" + (element.get(_w("themeFill")) or "")
    if pattern not in (None, "clear", "nil"):
        return f"shading-{pattern}-{(element.get(_w('color')) or 'auto').upper()}-{fill}"
    if fill in ("AUTO", "FFFFFF"):
        return None
    return f"shading-{fill}"


_POINTS = {"pt": 1.0, "pc": 12.0, "pi": 12.0, "in": 72.0, "cm": 72 / 2.54, "mm": 72 / 25.4}


type _Rgb = tuple[int, int, int]

# Text is faint where its colour's contrast (WCAG 2's ratio) with what is painted under it is
# below 1.33:1, as the ePI reader has it: white on white, yellow (FFFF00) on white, black on
# black shading.
_FAINT_CONTRAST = 1.33
# The names a colour may take by its role, and the slots they name by default.
_COLOUR_NAMES: dict[str, str] = {
    "background1": "lt1",
    "text1": "dk1",
    "background2": "lt2",
    "text2": "dk2",
    "light1": "lt1",
    "dark1": "dk1",
    "light2": "lt2",
    "dark2": "dk2",
    "hyperlink": "hlink",
    "followedHyperlink": "folHlink",
}
# Without a theme part, Word's window colours.
_SYSTEM_COLOURS: dict[str, _Rgb] = {"lt1": (0xFF, 0xFF, 0xFF), "dk1": (0, 0, 0)}
_HIGHLIGHTS: dict[str, _Rgb] = {
    "black": (0, 0, 0),
    "blue": (0, 0, 0xFF),
    "cyan": (0, 0xFF, 0xFF),
    "green": (0, 0xFF, 0),
    "magenta": (0xFF, 0, 0xFF),
    "red": (0xFF, 0, 0),
    "yellow": (0xFF, 0xFF, 0),
    "white": (0xFF, 0xFF, 0xFF),
    "darkBlue": (0, 0, 0x80),
    "darkCyan": (0, 0x80, 0x80),
    "darkGreen": (0, 0x80, 0),
    "darkMagenta": (0x80, 0, 0x80),
    "darkRed": (0x80, 0, 0),
    "darkYellow": (0x80, 0x80, 0),
    "darkGray": (0x80, 0x80, 0x80),
    "lightGray": (0xC0, 0xC0, 0xC0),
}


def _rgb(value: str) -> _Rgb:
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def _tinted(rgb: _Rgb, tint: str | None, shade: str | None) -> _Rgb:
    """A theme colour lightened (``themeTint``) or darkened (``themeShade``) in its lightness."""
    hue, light, saturation = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
    for value, lighten in ((tint, True), (shade, False)):
        if value is None:
            continue
        if not re.fullmatch(r"[0-9A-Fa-f]{2}", value):
            raise DocxRefusedError("unsupported-formatting", f"theme tint or shade {value!r}")
        share = int(value, 16) / 255
        light = light * share + (1 - share) if lighten else light * share
    red, green, blue = colorsys.hls_to_rgb(hue, light, saturation)
    return (round(red * 255), round(green * 255), round(blue * 255))


def _contrast(first: _Rgb, second: _Rgb) -> float:
    """WCAG 2's contrast ratio of two colours."""

    def luminance(rgb: _Rgb) -> float:
        linear = [
            c / 255 / 12.92 if c / 255 <= 0.03928 else ((c / 255 + 0.055) / 1.055) ** 2.4
            for c in rgb
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def _points(size: str) -> float | None:
    """A font size in points (half-points, or with a unit), or None if it cannot be parsed."""
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(pt|pc|pi|in|cm|mm)?", size)
    if match is None:
        return None
    unit = match.group(2)
    return float(match.group(1)) * _POINTS[unit] if unit else float(match.group(1)) / 2


def _tiny(size: str | None) -> bool:
    """Whether a font size is under two points; one the reader cannot parse counts as tiny."""
    if size is None:
        return False
    points = _points(size)
    return points is None or points < 2


def _size(properties: _Properties) -> float:
    """The largest size the run's text may be drawn at, in points (Word's default 10 pt)."""
    sizes = [properties.value(name) for name in ("sz", "szCs")]
    return max((_points(size) or 0.0 for size in sizes if size is not None), default=10.0)


# Layout bounds, in twentieths of a point: a frame, a floating table or an indent placed more
# than an inch off its anchor's start, or more than 22 inches (the largest page) away.
_INCH = 1440
_FAR = 22 * _INCH


def _twips(element: ET.Element | None, name: str) -> int | None:
    """An integer measure of ``element``, or None where it is absent; refused if malformed."""
    value = None if element is None else element.get(_w(name))
    if value is None:
        return None
    if not re.fullmatch(r"-?[0-9]{1,7}", value):
        raise DocxRefusedError("unsupported-formatting", f"{name} {value!r}")
    return int(value)


def _layout(levels: list[ET.Element | None], line: float) -> None:
    """Refuse a paragraph whose layout may draw its text clipped, overdrawn or off the page.

    ``levels`` are its paragraph properties, nearest first; ``line`` the largest size its text
    is drawn at, in points. Each setting is taken from the nearest level that makes it.
    """

    def nearest(name: str, attribute: str) -> int | str | None:
        for level in levels:
            found = None if level is None else level.find(_w(name))
            if found is not None and found.get(_w(attribute)) is not None:
                return (
                    found.get(_w(attribute))
                    if attribute.endswith("Rule")
                    else _twips(found, attribute)
                )
        return None

    spacing, rule = nearest("spacing", "line"), nearest("spacing", "lineRule") or "auto"
    if isinstance(spacing, int) and (
        (rule == "exact" and spacing / 20 < line) or (rule == "auto" and spacing < 192)
    ):
        # An exact line lower than the text clips it; under 0.8 lines, lines overprint.
        raise DocxRefusedError("unsupported-formatting", "line spacing that clips or overprints")
    left = next((v for n in ("start", "left") if isinstance(v := nearest("ind", n), int)), 0)
    right = next((v for n in ("end", "right") if isinstance(v := nearest("ind", n), int)), 0)
    hanging, first = nearest("ind", "hanging"), nearest("ind", "firstLine")
    opening = (
        left - hanging
        if isinstance(hanging, int)
        else left + (first if isinstance(first, int) else 0)
    )
    if min(left, right, opening) < -_INCH:
        raise DocxRefusedError("unsupported-formatting", "an indent more than an inch outward")
    frame = next(
        (
            f
            for level in levels
            if level is not None and (f := level.find(_w("framePr"))) is not None
        ),
        None,
    )
    if frame is not None:
        height = _twips(frame, "h")
        if any(
            v is not None and not -_INCH <= v <= _FAR
            for v in (_twips(frame, "x"), _twips(frame, "y"))
        ) or (frame.get(_w("hRule")) == "exact" and height is not None and height / 20 < line):
            # A frame off the page, or too low for its text, which Word clips.
            raise DocxRefusedError(
                "unsupported-formatting", "a frame that may clip or lose its text"
            )


def _faint(properties: _Properties, under: tuple[_Rgb, ...]) -> bool:
    """Whether text with these properties is easy not to see.

    Text whose colour's contrast with what is painted under it is below ``_FAINT_CONTRAST``,
    text under two points, or text scaled under a fifth is. ``under`` is what may be painted
    beneath the paragraph; the run's highlight, else its shading, lies over it. Automatic
    colour is never faint: Word draws it black or white against what is under it. Where what
    is under the text could be one of several colours and the answer differs between them, it
    is refused. A size or scale the reader cannot parse counts as faint.
    """
    highlight = properties.value("highlight")
    if highlight not in (None, "none"):
        if highlight not in _HIGHLIGHTS:
            raise DocxRefusedError("unsupported-formatting", f"highlight {highlight!r}")
        under = (_HIGHLIGHTS[highlight],)
    else:
        under = tuple(properties.styles.painted(properties.element("shd"))) or under
    color = properties.element("color")
    drawn = (
        []
        if color is None
        else properties.styles.colours(color, "val", "themeColor", "themeTint", "themeShade")
    )
    verdicts = {_contrast(text, back) < _FAINT_CONTRAST for text in drawn for back in under}
    if len(verdicts) > 1:
        raise DocxRefusedError("unsupported-formatting", "text faint on one colour under it only")
    if verdicts == {True}:
        return True
    # szCs sizes complex-script text; the reader does not know which script a character is
    # drawn as, so either size being tiny counts.
    if any(_tiny(size) for size in (properties.value("sz"), properties.value("szCs"))):
        return True
    scale = properties.value("w")
    if scale is not None:
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)%?", scale)
        return match is None or float(match.group(1)) < 20
    return False


# Fields whose stored result is what Word shows and prints until someone updates them by hand
# (corpus/numbering-cases records it for DOCPROPERTY and HYPERLINK). Word recomputes others when
# it lays out or prints the page (PAGE, NUMPAGES, DATE, TIME, AUTONUM, LISTNUM, IF, formulas...),
# so their stored result may not be what a reader sees.
_STORED_FIELDS = {"HYPERLINK", "DOCPROPERTY", "TOC"}
# Page numbers, which Word sets from the layout when it prints: never known from the file.
_LAYOUT_FIELDS = {"PAGEREF", "PAGE", "NUMPAGES", "SECTIONPAGES"}
# Fields Word recomputes when it prints or saves as PDF, which the reader computes too and reads
# only where the stored result is what Word prints (see "Fields" in the module docstring). REF
# and NOTEREF are here because Word reprints them (corpus/numbering-cases, fields-ref-stale and
# fields-noteref-stale): a cross-reference stored as one text prints as another.
_COMPUTED_FIELDS = {"SEQ", "STYLEREF", "REF", "NOTEREF"}


def _code(instruction: str) -> str:
    words = instruction.split()
    return words[0].upper() if words else ""


def _locked(element: ET.Element) -> bool:
    return element.get(_w("fldLock")) in ("1", "true", "on")


def _check_field(instruction: str) -> str:
    r"""The field's code, if its result is one the reader can vouch for; refused otherwise.

    A field nested in the code of one the reader computes or places (a NUL stands for it) makes
    its arguments unknown. A page number is placed only with the switches Word has answered:
    PAGEREF's ``\h``, and ``\*`` MERGEFORMAT, CHARFORMAT or Arabic; ``\p`` shows "above" or
    "below", ``\#`` a picture's text and other formats words.
    """
    code = _code(instruction)
    if code not in _STORED_FIELDS | _COMPUTED_FIELDS | _LAYOUT_FIELDS:
        raise DocxRefusedError("computed-field", f"a {code or 'blank'} field")
    if code in _COMPUTED_FIELDS | _LAYOUT_FIELDS and "\x00" in instruction:
        raise DocxRefusedError("computed-field", f"a {code} field with a field in its code")
    if code in _LAYOUT_FIELDS:
        flags = {"h"} if code == "PAGEREF" else set()
        arguments, switches = _switches(_tokens(instruction)[1:], set(), flags)
        if len(arguments) != (code == "PAGEREF") or switches.get("*", "ARABIC").upper() != "ARABIC":
            raise DocxRefusedError("computed-field", f"a {code} field the reader cannot place")
    return code


def _check_whitespace(element: ET.Element, text: str) -> None:
    if any(c in text for c in "\t\r\n"):
        # Word writes a tab or a break as an element; one inside the text is not what it shows.
        raise DocxRefusedError("unpreserved-whitespace", "a tab or line break inside w:t")
    if element.get(XML_SPACE) != "preserve" and text != text.strip(" "):
        raise DocxRefusedError("unpreserved-whitespace", "w:t without xml:space=preserve")


# What in a content control's content shows a character (or a note mark) where it stands.
_SHOWN = {
    _w(name)
    for name in (
        "sym",
        "tab",
        "ptab",
        "br",
        "cr",
        "noBreakHyphen",
        "softHyphen",
        "drawing",
        "pict",
        "footnoteReference",
        "endnoteReference",
    )
} | {_ALTERNATE}


def _content_control(element: ET.Element) -> None:
    properties = element.find(_w("sdtPr"))
    if properties is not None and any(_local(c.tag) == "dataBinding" for c in properties):
        # The stored content is a cache; Word shows the bound data.
        raise DocxRefusedError("unsupported-element", "content control bound to data")
    content = element.find(_w("sdtContent"))
    if (
        properties is not None
        and properties.find(f"{_w('placeholder')}/{_w('docPart')}") is not None
        and not any(
            (node.tag == _w("t") and node.text) or node.tag in _SHOWN
            for node in ([] if content is None else content.iter())
        )
    ):
        # Word shows the placeholder's building block (the glossary, not read) in an empty
        # control. With no placeholder part it shows nothing, as read.
        raise DocxRefusedError("unsupported-element", "an empty content control's placeholder")


def _int(value: str, where: str) -> int:
    if not re.fullmatch(r"-?[0-9]{1,9}", value):
        raise DocxRefusedError("invalid-package", f"{where} is not a number: {value!r}")
    return int(value)


def _numbering(levels: list[ET.Element | None]) -> Numbering | None:
    """The numId and ilvl, each from the nearest paragraph-properties level that sets it.

    An ilvl set nowhere is 0, even where a list level names the paragraph's style (``w:pStyle``):
    Word draws such a paragraph at level 0 (corpus/numbering-cases, style-tied-deeper-level).
    """
    found: dict[str, int] = {}
    for source in levels:
        numpr = source.find(_w("numPr")) if source is not None else None
        if numpr is None:
            continue
        for name in ("numId", "ilvl"):
            element = numpr.find(_w(name))
            if name not in found and element is not None:
                found[name] = _int(element.get(_w("val"), "0"), name)
    if not found:
        return None
    return Numbering(num_id=found.get("numId", 0), level=found.get("ilvl", 0))


@dataclass(frozen=True)
class _Context:
    """What the list-label pass needs of a paragraph besides the paragraph itself."""

    # The paragraph style in force (after falling back to the default), its table's style, and
    # the paragraph mark's run properties, which a list label is drawn over.
    style: str | None
    table_style: str | None
    mark: ET.Element | None
    # The conditional parts of the table style Word applies to the paragraph's cell.
    conditional: tuple[ET.Element, ...] = ()
    # The section the paragraph ends in or belongs to, counted from 0 (body only), and the
    # notes whose marks are custom.
    section: int = 0
    custom: frozenset[tuple[str, int]] = frozenset()
    # The fields the reader computes, and PAGEREFs: instruction, and the stored result's start
    # and end in text (a PAGEREF's result is set aside: both are its place).
    fields: tuple[tuple[str, int, int], ...] = ()
    # Bookmark starts (id, name, offset, notes before) and ends (id, offset, notes before).
    bookmark_starts: tuple[tuple[str, str, int, int], ...] = ()
    bookmark_ends: tuple[tuple[str, int, int], ...] = ()
    # Whether any of its characters is read through the Symbol table.
    symbolic: bool = False
    # How many table rows, in any table, ended before the paragraph in its story.
    rows_ended: int = 0
    # How many fields are open in their results when the paragraph ends (a table of contents).
    fields_open: int = 0
    # The height of its tallest character, at least (its text's and its mark's size), in points.
    line: float = 0.0


def _paragraph(
    element: ET.Element,
    styles: _Styles,
    table: tuple[int, int, int] | None,
    table_style: str | None,
    runs: set[ET.Element],
    story: tuple[str, int] | None = None,
    section: int = 0,
    carried: int = 0,
    under: tuple[_Rgb, ...] = (),
    conditional: tuple[ET.Element, ...] = (),
) -> tuple[Paragraph, _Context]:
    ppr = element.find(_w("pPr"))
    style = None
    if ppr is not None:
        style_element = ppr.find(_w("pStyle"))
        style = style_element.get(_w("val")) if style_element is not None else None
    mark_rpr = ppr.find(_w("rPr")) if ppr is not None else None
    mark = _Properties(styles, mark_rpr, style, table_style, None, conditional)
    mark_hidden = mark.toggle("vanish") or mark.toggle("specVanish")
    if mark.toggle("vanish") and not mark.shown("vanish") and not mark.toggle("specVanish"):
        # Whether Word runs the paragraph on is not on record (see ``run``).
        raise DocxRefusedError("hidden-text", "a paragraph mark Word's toggle rule may show")
    # The paragraph's properties, then its style's, its table style's and the defaults.
    table_levels = [s.ppr for s in styles.chain(table_style) if s.ppr is not None]
    levels = [
        ppr,
        *(s.ppr for s in styles.resolve(style, "paragraph")),
        *table_levels,
        styles.default_ppr,
    ]
    shading = next(
        (e for level in levels if level is not None and (e := level.find(_w("shd"))) is not None),
        None,
    )
    painted = tuple(styles.painted(shading)) or under
    reader = _ParagraphReader(
        styles, style, table_style, runs, story, carried, painted, conditional
    )
    reader.container(element)
    if reader.in_instruction():
        raise DocxRefusedError("unbalanced-field", "a paragraph ends inside a field instruction")
    if any(start is not None for start in reader.results):
        raise DocxRefusedError(
            "unbalanced-field", "a computed field's result runs past its paragraph"
        )
    if reader.layout:
        raise DocxRefusedError("unbalanced-field", "a page number runs past its paragraph")
    numbering = _numbering(levels)
    if numbering != _numbering([level for level in levels if level not in table_levels]):
        # A list, or its level, from the table style: what Word draws is not on record.
        raise _refuse_numbering("a list from a table style")
    # The mark's size with and without the conditional parts (_ParagraphReader.run).
    line = max(reader.line, _size(mark), _size(_Properties(styles, mark_rpr, style, table_style)))
    if reader.length:
        _layout(levels, line)
    context = _Context(
        line=line,
        style=styles.effective(style, "paragraph"),
        table_style=table_style,
        mark=mark_rpr,
        conditional=conditional,
        section=section,
        custom=frozenset(reader.custom),
        symbolic=reader.symbolic,
        fields=tuple(reader.computed),
        bookmark_starts=tuple(reader.bookmark_starts),
        bookmark_ends=tuple(reader.bookmark_ends),
        fields_open=reader.carried + len(reader.fields),
    )
    return Paragraph(
        text="".join(reader.parts),
        style=style,
        numbering=numbering,
        table=table,
        marks=_paragraph_marks(reader, levels),
        mark_hidden=mark_hidden,
        notes=tuple(reader.notes),
        pages=tuple(reader.pages),
        comments=tuple(reader.comments),
    ), context


def _paragraph_marks(reader: _ParagraphReader, levels: list[ET.Element | None]) -> tuple[Mark, ...]:
    """The runs' marks, and the shading and right-to-left marks of the paragraph itself.

    Shading or right-to-left set on the paragraph (or its style) covers every character.
    """
    marks = list(reader.marks)

    def nearest(name: str) -> ET.Element | None:
        return next(
            (
                e
                for level in levels
                if level is not None and (e := level.find(_w(name))) is not None
            ),
            None,
        )

    shading = _shading(nearest("shd"))
    if reader.length and shading is not None:
        marks.append(Mark(0, reader.length, shading))
    if reader.length and _on(nearest("bidi")):
        marks.append(Mark(0, reader.length, "rtl"))
    # Marks of one kind that overlap or touch are one mark (``Mark``).
    merged: list[Mark] = []
    for mark in sorted(marks, key=lambda m: (m.kind, m.start, m.end)):
        last = merged[-1] if merged else None
        if last is not None and last.kind == mark.kind and mark.start <= last.end:
            merged[-1] = Mark(last.start, max(last.end, mark.end), mark.kind)
        else:
            merged.append(mark)
    return tuple(sorted(merged, key=lambda m: (m.start, m.end, m.kind)))


# --- list labels ---------------------------------------------------------------------------

_LEVELS = range(9)
_FORMATS = {
    "decimal",
    "decimalZero",
    "upperRoman",
    "lowerRoman",
    "upperLetter",
    "lowerLetter",
    "none",
    "bullet",
}
# What a list level may hold. Anything else (a picture bullet, alternate content carrying a
# custom format...) makes the level one the reader cannot draw.
_LEVEL_CHILDREN = {
    _w(name)
    for name in (
        "start",
        "numFmt",
        "lvlRestart",
        "pStyle",
        "isLgl",
        "suff",
        "lvlText",
        "lvlJc",
        "pPr",
        "rPr",
        "legacy",
    )
}
_PLACEHOLDER = re.compile(r"(%[1-9])")
# The longest lvlText drawn: a label is rebuilt for every list item, and Word's answer for a
# longer one is not on record.
MAX_LEVEL_TEXT = 255
_ROMAN = (
    (1000, "M"),
    (900, "CM"),
    (500, "D"),
    (400, "CD"),
    (100, "C"),
    (90, "XC"),
    (50, "L"),
    (40, "XL"),
    (10, "X"),
    (9, "IX"),
    (5, "V"),
    (4, "IV"),
    (1, "I"),
)


@dataclass(frozen=True)
class _Level:
    start: int | None
    format: str
    text: str | None
    restart: int | None
    legal: bool
    suffix: str
    rpr: ET.Element | None
    # Why the level cannot be drawn, if it cannot; refused only when a paragraph uses it.
    unsupported: str | None


def _level(element: ET.Element) -> _Level:
    def value(name: str) -> str | None:
        found = element.find(_w(name))
        return None if found is None else found.get(_w("val"))

    unsupported = next(
        (f"{_local(c.tag)} in a list level" for c in element if c.tag not in _LEVEL_CHILDREN), None
    )
    number_format = element.find(_w("numFmt"))
    fmt = value("numFmt") or "decimal"
    if number_format is not None and number_format.get(_w("format")) is not None:
        unsupported = unsupported or "a custom number format"
    elif fmt not in _FORMATS:
        unsupported = unsupported or f"the number format {fmt}"
    text_element = element.find(_w("lvlText"))
    text = None
    if text_element is not None:
        null = text_element.get(_w("null")) in ("1", "true", "on")
        text = "" if null else text_element.get(_w("val"), "")
        if len(text) > MAX_LEVEL_TEXT:
            unsupported = unsupported or f"a list level text over {MAX_LEVEL_TEXT} characters"
    legacy = element.find(_w("legacy"))
    suffix = value("suff") or "tab"
    if legacy is not None and legacy.get(_w("legacy")) not in ("0", "false", "off"):
        # A Word 6 level: the gap after the label is set by legacySpace and legacyIndent.
        suffix = "legacy"
    elif suffix not in ("tab", "space", "nothing"):
        unsupported = unsupported or f"the suffix {suffix}"
    start = value("start")
    restart = value("lvlRestart")
    return _Level(
        start=None if start is None else _int(start, "start"),
        format=fmt,
        text=text,
        restart=None if restart is None else _int(restart, "lvlRestart"),
        legal=bool(_on(element.find(_w("isLgl")))),
        suffix=suffix,
        rpr=element.find(_w("rPr")),
        unsupported=unsupported,
    )


def _ilvl(element: ET.Element) -> int:
    level = _int(element.get(_w("ilvl"), ""), "ilvl")
    if level not in _LEVELS:
        raise DocxRefusedError("invalid-package", f"list level {level}")
    return level


@dataclass(frozen=True)
class _Num:
    abstract: int | None
    starts: dict[int, int]
    levels: dict[int, _Level]


@dataclass(frozen=True)
class _Abstract:
    levels: dict[int, _Level]
    # numStyleLink: this abstractNum takes its levels from the one a numbering style names.
    link: str | None
    # styleLink: this abstractNum is the one that numbering style names.
    linked_from: str | None


@dataclass
class _Counters:
    """The counters of one abstractNum, shared by every list (numId) that names it."""

    values: list[int | None] = field(default_factory=lambda: [None] * len(_LEVELS))
    # A level whose start the reader cannot find (its abstractNum does not define it).
    unknown: list[bool] = field(default_factory=lambda: [False] * len(_LEVELS))
    # The (numId, level) pairs whose startOverride has been applied.
    applied: set[tuple[int, int]] = field(default_factory=set)
    # For a level restarted by a higher one, the startOverride, for that level, of the list
    # whose paragraph restarted it (None: it has none, or the level was never restarted).
    restart_from: list[int | None] = field(default_factory=lambda: [None] * len(_LEVELS))
    # For a level counted only through a deeper paragraph, the number it shows until a paragraph
    # at the level counts it: its abstractNum's start, whatever its count (None: its count).
    implied: list[int | None] = field(default_factory=lambda: [None] * len(_LEVELS))
    # For a level counted only through a deeper paragraph, how many table rows had ended then.
    implied_rows: list[int] = field(default_factory=lambda: [0] * len(_LEVELS))
    # For a level restarted by a higher one, how many table rows had ended at the restart.
    restart_rows: list[int] = field(default_factory=lambda: [0] * len(_LEVELS))
    # For a level that never restarts, how many table rows had ended at the last paragraph of
    # a higher level since the level was last counted (None: none since).
    higher_rows: list[int | None] = field(default_factory=lambda: [None] * len(_LEVELS))


def _refuse_numbering(detail: str) -> DocxRefusedError:
    return DocxRefusedError("unsupported-numbering", detail)


class _Lists:
    def __init__(self, root: ET.Element | None, styles: _Styles) -> None:
        self.styles = styles
        self.present = root is not None
        self.abstracts: dict[int, _Abstract] = {}
        self.nums: dict[int, _Num] = {}
        self.counters: dict[int, _Counters] = {}
        if root is None:
            return
        for element in root.findall(_w("abstractNum")):
            key = _int(element.get(_w("abstractNumId"), ""), "abstractNumId")
            if key in self.abstracts:
                raise DocxRefusedError("invalid-package", f"abstractNum {key} is defined twice")
            levels: dict[int, _Level] = {}
            for lvl in element.findall(_w("lvl")):
                level = _ilvl(lvl)
                if level in levels:
                    raise DocxRefusedError("invalid-package", f"list level {level} twice")
                levels[level] = _level(lvl)
            link = element.find(_w("numStyleLink"))
            linked_from = element.find(_w("styleLink"))
            self.abstracts[key] = _Abstract(
                levels,
                link=None if link is None else link.get(_w("val")),
                linked_from=None if linked_from is None else linked_from.get(_w("val")),
            )
        for element in root.findall(_w("num")):
            key = _int(element.get(_w("numId"), ""), "numId")
            if key in self.nums:
                raise DocxRefusedError("invalid-package", f"numId {key} is defined twice")
            abstract = element.find(_w("abstractNumId"))
            starts: dict[int, int] = {}
            overridden: dict[int, _Level] = {}
            seen: set[int] = set()
            for override in element.findall(_w("lvlOverride")):
                level = _ilvl(override)
                if level in seen:
                    raise DocxRefusedError("invalid-package", f"list level {level} twice")
                seen.add(level)
                start = override.find(_w("startOverride"))
                if start is not None:
                    starts[level] = _int(start.get(_w("val"), ""), "startOverride")
                redefined = override.find(_w("lvl"))
                if redefined is not None:
                    overridden[level] = _level(redefined)
            self.nums[key] = _Num(
                abstract=None
                if abstract is None
                else _int(abstract.get(_w("val"), ""), "abstractNumId"),
                starts=starts,
                levels=overridden,
            )

    def definitions(self, num_id: int) -> tuple[int, _Num, dict[int, _Level], dict[int, _Level]]:
        """What counts and draws ``num_id``'s labels.

        The abstractNum whose counters it shares (the one it names, even when that one links to
        another), the num, the abstractNum's levels (through the link), and those levels with the
        num's level overrides over them.
        """
        if not self.present:
            raise _refuse_numbering("a list with no numbering part")
        num = self.nums.get(num_id)
        if num is None or num.abstract is None or num.abstract not in self.abstracts:
            raise _refuse_numbering(f"numId {num_id} is not defined")
        abstract = self.abstracts[num.abstract]
        if abstract.link is not None:
            abstract = self.abstracts[self._linked(abstract.link)]
        return num.abstract, num, abstract.levels, {**abstract.levels, **num.levels}

    def _linked(self, name: str) -> int:
        """The abstractNum a numbering style names, which must name the style back.

        Word draws an empty label for a link with no ``styleLink`` back (corpus/numbering-cases,
        numbering-style-link-one-way); the reader refuses it.
        """
        style = self.styles.styles.get(name)
        if style is None or style.kind != "numbering":
            raise _refuse_numbering(f"numbering style {name!r} is not defined")
        numbering = _numbering([style.ppr])
        linked = self.nums.get(numbering.num_id) if numbering is not None else None
        if (
            linked is None
            or linked.abstract is None
            or linked.starts
            or linked.levels
            or linked.abstract not in self.abstracts
            or self.abstracts[linked.abstract].link is not None
            or self.abstracts[linked.abstract].linked_from != name
        ):
            raise _refuse_numbering(f"numbering style {name!r} names no list the reader can use")
        return linked.abstract

    def label(self, numbering: Numbering, context: _Context) -> Numbering:
        """``numbering`` with the label Word draws, counted in document order."""
        level = numbering.level
        if level not in _LEVELS:
            raise _refuse_numbering(f"list level {level}")
        key, num, base, levels = self.definitions(numbering.num_id)
        definition = levels.get(level)
        if definition is None:
            raise _refuse_numbering(f"level {level} of numId {numbering.num_id} is not defined")
        for upper in range(level + 1):
            restart = levels[upper].restart if upper in levels else None
            if restart is not None and (
                restart < 0 or (restart != 0 and restart >= upper + (upper == 0))
            ):
                # Restarting after the level directly above, written out (Word never writes it,
                # it is the default), or after itself or a deeper one: Word draws the level
                # empty [restart-level-above]. A negative one is not on record.
                raise _refuse_numbering(f"lvlRestart {restart} on level {upper}")
        for shown in {int(n) - 1 for n in re.findall(r"%([1-9])", definition.text or "")}:
            if shown != level and shown in levels and levels[shown].restart == 0:
                # A level that never restarts, shown in a deeper level's label: Word draws its
                # start, then one less after a higher paragraph, while it counts on
                # [restart-skipped-ancestor, restart-never-shown-deeper].
                raise DocxRefusedError(
                    "ambiguous-numbering", f"level {shown}, which never restarts, in a deeper label"
                )
        counters = self.counters.setdefault(key, _Counters())
        self._count(counters, numbering.num_id, num, level, base, levels, context.rows_ended)
        text = self._draw(counters, level, levels, definition, context)
        return replace(numbering, text=text, suffix=definition.suffix)

    @staticmethod
    def _base_start(counters: _Counters, base: dict[int, _Level], level: int) -> None:
        """Start ``level`` at its abstractNum's start (0 when it sets none), or mark it unknown."""
        definition = base.get(level)
        if definition is None:
            counters.unknown[level] = True
        else:
            counters.values[level] = 0 if definition.start is None else definition.start

    def _count(
        self,
        counters: _Counters,
        num_id: int,
        num: _Num,
        level: int,
        base: dict[int, _Level],
        levels: dict[int, _Level],
        rows: int,
    ) -> None:
        """Count a paragraph of list ``num_id`` at ``level``, as Word does.

        ``rows`` is how many table rows have ended before the paragraph.

        Each rule is Word's answer to a case in corpus/numbering-cases, named in brackets.
        """
        for deeper in range(level + 1, len(_LEVELS)):
            definition = levels.get(deeper)
            restart = definition.restart if definition is not None else None
            if restart is not None and restart < 0:
                # Whether it restarts the level is not on record.
                raise _refuse_numbering(f"lvlRestart {restart} on level {deeper}")
            # lvlRestart n restarts the level after a paragraph at a level up to n - 1; 0 never.
            # A value that is not a higher level is ignored, and then any higher level restarts
            # [restart-never, restart-after-first].
            if restart == 0:
                counters.higher_rows[deeper] = rows
            if restart is None or level < restart or restart - 1 >= deeper:
                counters.values[deeper] = None
                counters.unknown[deeper] = False
                counters.implied[deeper] = None
                # It restarts as the list of this paragraph says, whichever list counts it
                # next [restart-source-override, restart-source-plain, restart-source-unused].
                counters.restart_from[deeper] = num.starts.get(deeper)
                counters.restart_rows[deeper] = rows
        for higher in range(level):
            # A higher level not counted yet shows its abstractNum's start, not a list's
            # startOverride [ancestor-never-counted, ancestor-two-levels,
            # override-implicit-ancestor]; but it counts on from this paragraph's list's
            # startOverride, if it has one, used up or not, and the override is not used up by it
            # [override-implicit-continued, override-implicit-reused, override-implicit-levels].
            if counters.values[higher] is None and not counters.unknown[higher]:
                if counters.restart_from[higher] is not None:
                    # Restarted to another list's startOverride, then counted first by a deeper
                    # level: Word's answer is not on record.
                    raise DocxRefusedError(
                        "ambiguous-numbering",
                        f"level {higher}, restarted by another list, never counted",
                    )
                if levels.get(higher) is not None and levels[higher].restart is not None:
                    # A higher level that restarts by its own lvlRestart, counted for the first
                    # time by a deeper one: Word draws it otherwise than its start
                    # [restart-skipped-ancestor].
                    raise DocxRefusedError(
                        "ambiguous-numbering",
                        f"level {higher}, which has lvlRestart, never counted",
                    )
                self._base_start(counters, base, higher)
                if not counters.unknown[higher]:
                    counters.implied[higher] = counters.values[higher]
                    counters.values[higher] = num.starts.get(higher, counters.values[higher])
                    counters.implied_rows[higher] = rows
        implied = counters.implied[level]
        if (
            implied is not None
            and implied != counters.values[level]
            and counters.implied_rows[level] != rows
        ):
            # Counting on from a list's startOverride taken through a deeper paragraph, after a
            # table row ended: Word does in some tables, not in others [override-implicit-rows].
            raise DocxRefusedError(
                "ambiguous-numbering", f"level {level} counted from an override past a row"
            )
        counters.implied[level] = None
        never = counters.higher_rows[level]
        counters.higher_rows[level] = None
        if never is not None and never != rows:
            # A level that never restarts, counted after a higher paragraph and the end of a
            # table row: Word draws it one less than its count in some tables and not in others
            # [restart-never-rows].
            raise DocxRefusedError(
                "ambiguous-numbering", f"level {level}, which never restarts, past a row"
            )
        current = counters.values[level]
        if level in num.starts and (num_id, level) not in counters.applied:
            # A startOverride sets the count the first time its list reaches the level, whatever
            # the shared count was [start-override-restart, override-ancestor, override-return].
            counters.applied.add((num_id, level))
            counters.values[level] = num.starts[level]
            counters.unknown[level] = False
        elif counters.unknown[level]:
            return
        elif current is not None:
            # Lists of one abstractNum share its count [shared-continue, return-after-restart,
            # plain-after-restart, level-override-shared].
            counters.values[level] = current + 1
        elif counters.restart_from[level] is not None:
            if counters.restart_rows[level] != rows:
                # Restarted by another list's paragraph, then counted after a table row ended:
                # Word takes that list's startOverride after a table, but not in a later row of
                # the same table [restart-source-rows]; the reader does not tell them apart.
                raise DocxRefusedError(
                    "ambiguous-numbering", f"level {level} restarted by another list, past a row"
                )
            # Restarted by a paragraph of a list with a startOverride for this level: that
            # override, whichever list counts it now [override-restart-within,
            # restart-source-override, restart-source-unused].
            counters.values[level] = counters.restart_from[level]
        else:
            # A level override's own w:start is not used [level-override-first,
            # level-override-start]; a level with no w:start starts at 0 [missing-start].
            self._base_start(counters, base, level)

    def _draw(
        self,
        counters: _Counters,
        level: int,
        levels: dict[int, _Level],
        definition: _Level,
        context: _Context,
    ) -> str:
        if definition.unsupported is not None:
            raise _refuse_numbering(definition.unsupported)
        if definition.text is None:
            raise _refuse_numbering("a list level with no lvlText")
        pieces: list[str] = []
        for piece in _PLACEHOLDER.split(definition.text):
            if not _PLACEHOLDER.fullmatch(piece):
                if "%" in piece:
                    raise _refuse_numbering("a % in lvlText that names no level")
                pieces.append(piece)
                continue
            shown = int(piece[1]) - 1
            source = levels.get(shown)
            if shown > level or source is None:
                raise _refuse_numbering(f"lvlText shows level {shown} from level {level}")
            if definition.format == "bullet" or source.format == "bullet":
                raise _refuse_numbering("a bullet level in a list label's number")
            if source.unsupported is not None:
                raise _refuse_numbering(source.unsupported)
            if definition.legal and source.format == "none":
                raise _refuse_numbering("legal numbering of a level that shows no number")
            implied = counters.implied[shown]
            value = counters.values[shown] if implied is None else implied
            if counters.unknown[shown] or value is None:
                raise DocxRefusedError("ambiguous-numbering", f"the count of list level {shown}")
            # isLgl draws every level in decimal, but a decimalZero one keeps its zero ("1.01",
            # Word's answer, 2026-10).
            legal = definition.legal and source.format != "decimalZero"
            pieces.append(_number(value, "decimal" if legal else source.format))
        properties = _Properties(
            self.styles,
            definition.rpr,
            context.style,
            context.table_style,
            context.mark,
            context.conditional,
        )
        if properties.toggle("vanish") or properties.toggle("specVanish"):
            raise DocxRefusedError("ambiguous-numbering", "a hidden list label")
        font = _label_font(self.styles, properties, context.table_style)
        drawn = "".join(pieces)
        if font == "wingdings":
            label = "".join(_bullet(ord(character)) for character in drawn)
        else:
            label = _characters(drawn, font == "symbol")
        if (properties.toggle("caps") or properties.toggle("smallCaps")) and label.upper() != label:
            raise _refuse_numbering("a list label in capitals")
        return label


def _number(value: int, fmt: str) -> str:
    """``value`` in the number format ``fmt``."""
    if fmt == "none":
        return ""
    if fmt in ("decimal", "decimalZero"):
        if value < 0:
            raise _refuse_numbering(f"the number {value}")
        return f"{value:02d}" if fmt == "decimalZero" else str(value)
    if fmt in ("upperRoman", "lowerRoman"):
        if not 1 <= value <= 3999:
            raise _refuse_numbering(f"the number {value} in roman")
        out: list[str] = []
        rest = value
        for amount, numeral in _ROMAN:
            count, rest = divmod(rest, amount)
            out.append(numeral * count)
        roman = "".join(out)
        return roman if fmt == "upperRoman" else roman.lower()
    # upperLetter or lowerLetter: a to z, then aa to zz, and so on, each letter repeated.
    if not 1 <= value <= 780:
        raise _refuse_numbering(f"the number {value} in letters")
    letter = chr(ord("A") + (value - 1) % 26) * ((value - 1) // 26 + 1)
    return letter if fmt == "upperLetter" else letter.lower()


def _labelled(
    paragraphs: list[Paragraph], contexts: list[_Context], lists: _Lists
) -> list[Paragraph]:
    """``paragraphs`` with the label of every numbered one, counted in document order."""
    out: list[Paragraph] = []
    hidden_before = False
    for paragraph, context in zip(paragraphs, contexts, strict=True):
        numbering = paragraph.numbering
        if numbering is None or numbering.num_id == 0:
            out.append(paragraph)
        elif hidden_before:
            # Word runs this paragraph on after the previous one; where it draws the label, if
            # it draws one, is not documented.
            raise DocxRefusedError("ambiguous-numbering", "a list item run on after a hidden mark")
        elif paragraph.mark_hidden:
            # Its mark is hidden, by any level, whatever the list level says: whether Word draws
            # the label is not documented.
            raise DocxRefusedError("ambiguous-numbering", "a list item whose mark is hidden")
        else:
            out.append(replace(paragraph, numbering=lists.label(numbering, context)))
        hidden_before = paragraph.mark_hidden
    return out


# --- notes ---------------------------------------------------------------------------------

_NOTE_KINDS = ("footnote", "endnote")
# Word's defaults when neither the settings nor the section set a format.
_NOTE_DEFAULT_FORMAT = {"footnote": "decimal", "endnote": "lowerRoman"}
_NOTE_FORMATS = {"decimal", "upperRoman", "lowerRoman", "upperLetter", "lowerLetter", "chicago"}
_CHICAGO = ("*", "\u2020", "\u2021", "\u00a7")
# Separators and continuation notices: layout, not notes, and never referenced.
_NOTE_LAYOUT = {"separator", "continuationSeparator", "continuationNotice"}


@dataclass(frozen=True)
class _NoteRules:
    format: str
    start: int
    restart: str


def _note_rules(kind: str, section: ET.Element | None) -> _NoteRules:
    """How ``kind`` notes are numbered in a section, from its sectPr alone.

    Word ignores the footnotePr and endnotePr of the settings part, even with the separators it
    lists there (corpus/numbering-cases, notes-document-format), and applies the section's.
    """
    properties = section.find(_w(f"{kind}Pr")) if section is not None else None
    values: dict[str, str] = {}
    for name in ("numFmt", "numStart", "numRestart"):
        element = properties.find(_w(name)) if properties is not None else None
        if element is None:
            continue
        if name == "numFmt" and element.get(_w("format")) is not None:
            raise _refuse_numbering(f"a custom {kind} number format")
        if element.get(_w("val")) is not None:
            values[name] = element.get(_w("val"), "")
    return _NoteRules(
        format=values.get("numFmt", _NOTE_DEFAULT_FORMAT[kind]),
        start=_int(values.get("numStart", "1"), "numStart"),
        restart=values.get("numRestart", "continuous"),
    )


def _note_mark(value: int, fmt: str) -> str:
    """The mark for the ``value``-th note in the note number format ``fmt``."""
    if fmt not in _NOTE_FORMATS:
        raise _refuse_numbering(f"the note number format {fmt}")
    if fmt != "chicago":
        return _number(value, fmt)
    if not 1 <= value <= 6:
        # Word's answers go to the sixth (††) [notes-chicago, notes-section-chicago].
        raise _refuse_numbering(f"the note number {value} in symbols")
    # *, †, ‡, §, then each doubled.
    return _CHICAGO[(value - 1) % 4] * ((value - 1) // 4 + 1)


def _note_marks(
    paragraphs: list[Paragraph], contexts: list[_Context], sections: list[ET.Element | None]
) -> dict[tuple[str, int], str | None]:
    """The mark of every note the body refers to, by Word's rules.

    Each rule is Word's answer to a case in corpus/numbering-cases, named in brackets. A note's
    number is its section's numStart plus the number of notes of its kind before it: in the
    document [notes-continuous, notes-section-start-continuous], or in its section when the
    section restarts them [notes-each-section]; footnotes and endnotes count apart
    [notes-mixed]. It is drawn in its section's format [notes-section-format,
    notes-section-chicago]. A note with a custom mark takes no number [notes-custom-mark].
    """
    marks: dict[tuple[str, int], str | None] = {}
    before: dict[tuple[str, int | None], int] = {}
    for paragraph, context in zip(paragraphs, contexts, strict=True):
        for reference in paragraph.notes:
            kind, key = reference.kind, (reference.kind, reference.id)
            if key in marks:
                raise DocxRefusedError(
                    "invalid-package", f"{kind} {reference.id} referred to twice"
                )
            if key in context.custom:
                marks[key] = None
                continue
            rules = _note_rules(kind, sections[context.section])
            if rules.restart == "eachPage":
                # The count restarts on each page, which depends on how Word lays the pages out.
                raise DocxRefusedError("ambiguous-numbering", f"{kind} numbers restart each page")
            if rules.restart not in ("continuous", "eachSect"):
                raise _refuse_numbering(f"{kind} numbers restart {rules.restart}")
            scope = (kind, context.section if rules.restart == "eachSect" else None)
            marks[key] = _note_mark(rules.start + before.get(scope, 0), rules.format)
            # Every note counts in the document and in its section, whichever rule a later
            # section follows.
            for counted in {(kind, None), (kind, context.section)}:
                before[counted] = before.get(counted, 0) + 1
    return marks


def _read_notes(
    root: ET.Element | None, kind: str, styles: _Styles
) -> dict[int, tuple[Paragraph, ...]]:
    """The paragraphs of every note in a footnotes or endnotes part, by id."""
    if root is None:
        return {}
    _check_part(root)
    notes: dict[int, tuple[Paragraph, ...]] = {}
    for element in root:
        if element.tag != _w(kind):
            raise DocxRefusedError("unsupported-element", f"{_local(element.tag)} in {kind}s")
        if element.get(_w("type"), "normal") in _NOTE_LAYOUT:
            continue
        if element.get(_w("type"), "normal") != "normal":
            raise DocxRefusedError(
                "unsupported-element", f"a {kind} of type {element.get(_w('type'))}"
            )
        note = _int(element.get(_w("id"), ""), f"{kind} id")
        if note in notes:
            raise DocxRefusedError("invalid-package", f"{kind} {note} is defined twice")
        reader = _Body(styles, (kind, note))
        reader.blocks(element, None, None)
        _check_accounted(element, reader.runs)
        if any(c.fields for c in reader.contexts):
            # Whether Word counts a SEQ in a note with the body's is not yet on record.
            raise DocxRefusedError("computed-field", f"a computed field or PAGEREF in a {kind}")
        if any(p.numbering is not None and p.numbering.num_id for p in reader.out):
            # Whether a list in a note counts with the body's lists is not yet on record.
            raise _refuse_numbering(f"a list in a {kind}")
        notes[note] = tuple(reader.out)
    return notes


def _with_marks(paragraph: Paragraph, marks: dict[tuple[str, int], str | None]) -> Paragraph:
    if not paragraph.notes:
        return paragraph
    return replace(
        paragraph, notes=tuple(replace(n, mark=marks[(n.kind, n.id)]) for n in paragraph.notes)
    )


# --- computed fields -----------------------------------------------------------------------

# SEQ's number formats (\\*), as list formats; ARABIC is matched without regard to case.
_SEQ_FORMATS = {
    "ARABIC": "decimal",
    "ROMAN": "upperRoman",
    "roman": "lowerRoman",
    "ALPHABETIC": "upperLetter",
    "alphabetic": "lowerLetter",
}
# \\* switches that change how the result is formatted, not what it says.
_FORMATTING = {"MERGEFORMAT", "CHARFORMAT"}
_HEADING = re.compile(r"heading ([1-9])")
_FIELD_TOKENS = re.compile(r'"([^"]*)"|(\S+)')


def _tokens(instruction: str) -> list[str]:
    return [quoted or bare for quoted, bare in _FIELD_TOKENS.findall(instruction)]


def _switches(
    tokens: list[str], with_argument: set[str], flags: set[str]
) -> tuple[list[str], dict[str, str]]:
    """The arguments and the switches of a field after its code; refused if unknown."""
    arguments: list[str] = []
    switches: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("\\"):
            arguments.append(token)
        elif token[1:] == "*":
            index += 1
            value = tokens[index] if index < len(tokens) else ""
            if value.upper() not in _FORMATTING:
                if "*" in switches:
                    raise DocxRefusedError("computed-field", "a field with two number formats")
                switches["*"] = value
        elif token[1:] in with_argument:
            index += 1
            switches[token[1:]] = tokens[index] if index < len(tokens) else ""
        elif token[1:] in flags:
            switches[token[1:]] = ""
        else:
            raise DocxRefusedError("computed-field", f"a field switch {token}")
        index += 1
    return arguments, switches


def _verify_fields(
    paragraphs: list[Paragraph], contexts: list[_Context], styles: _Styles, loose: set[str]
) -> None:
    """Refuse a computed field whose stored result is not what Word prints.

    Word shows a field's stored result on screen and recomputes SEQ, STYLEREF, REF and NOTEREF
    when it prints or saves as PDF (corpus/numbering-cases, fields-stale, fields-ref-stale,
    fields-noteref-stale); a stored result that differs is a
    document whose screen and print disagree. Each rule is Word's answer to a case there, named
    in brackets.
    """
    names = [
        (styles.styles[c.style].name or "").lower() if c.style in styles.styles else ""
        for c in contexts
    ]
    # A built-in heading style's level: Word goes by the name, not the outline level.
    levels = [int(m.group(1)) if (m := _HEADING.fullmatch(name)) else None for name in names]
    bookmarks = _bookmarks(contexts, loose)
    # Per SEQ identifier: its value, and the paragraph of its last field.
    counted: dict[str, tuple[int, int]] = {}
    for index, (paragraph, context) in enumerate(zip(paragraphs, contexts, strict=True)):
        for instruction, start, end in context.fields:
            tokens = _tokens(instruction)
            code = tokens[0].upper()
            if code == "PAGEREF":
                # A bookmark REF could read; Word's print of one it cannot find is not on record.
                (name,), _ = _switches(tokens[1:], set(), {"h"})
                if bookmarks.get(name) is None:
                    raise DocxRefusedError(
                        "computed-field", "a PAGEREF to a bookmark it cannot read"
                    )
                continue
            if code == "SEQ":
                shown = _seq(tokens[1:], index, counted, levels)
            elif code == "STYLEREF":
                shown = _styleref(tokens[1:], index, paragraphs, names, contexts)
            else:
                shown = _reference(code, tokens[1:], paragraphs, bookmarks)
            stored = paragraph.text[start:end]
            if stored != shown:
                raise DocxRefusedError(
                    "stale-field", f"a {code} field shows {stored!r}; Word prints {shown!r}"
                )


type _Span = tuple[int, int, int, slice]


def _bookmarks(contexts: list[_Context], loose: set[str]) -> dict[str, _Span | None]:
    """Each bookmark's paragraph, start, end and the notes it holds; None for one REF cannot read.

    That is one that starts and ends in different paragraphs or between them, has no end, or
    shares its name with another.
    """
    starts: dict[str, tuple[str, int, int, int]] = {}
    ends: dict[str, tuple[int, int, int]] = {}
    for index, context in enumerate(contexts):
        for key, name, offset, notes in context.bookmark_starts:
            starts[key] = (name, index, offset, notes)
        for key, offset, notes in context.bookmark_ends:
            ends[key] = (index, offset, notes)
    spans: dict[str, _Span | None] = {}
    for key, (name, index, start, first) in starts.items():
        end = ends.get(key)
        whole = key not in loose and end is not None and end[0] == index and name not in spans
        spans[name] = (index, start, end[1], slice(first, end[2])) if whole and end else None
    return spans


def _reference(
    code: str, tokens: list[str], paragraphs: list[Paragraph], bookmarks: dict[str, _Span | None]
) -> str:
    """What REF (the bookmark's text) or NOTEREF (its note's mark) prints."""
    arguments, switches = _switches(tokens, set(), {"h", "f"} if code == "NOTEREF" else {"h"})
    if len(arguments) != 1 or "*" in switches:
        raise DocxRefusedError("computed-field", f"a {code} field the reader cannot compute")
    name = arguments[0]
    if name not in bookmarks:
        # Word prints "Error! Reference source not found." [fields-ref-missing].
        raise DocxRefusedError("computed-field", f"a {code} to a bookmark that is not there")
    span = bookmarks[name]
    if span is None:
        raise DocxRefusedError("computed-field", f"a {code} to a bookmark it cannot read")
    index, start, end, held = span
    paragraph = paragraphs[index]
    inside = [n for n in paragraph.notes if start <= n.offset <= end]
    if code == "NOTEREF":
        # The mark of the note referred to in the bookmark [fields-noteref]: one between its
        # start and end, not one next to it (Word prints an error for that).
        inside = list(paragraph.notes[held])
        if len(inside) != 1 or inside[0].mark is None:
            raise DocxRefusedError("computed-field", "a NOTEREF to a bookmark without one note")
        return inside[0].mark
    if inside:
        raise DocxRefusedError("computed-field", "a REF to a bookmark holding a note mark")
    # The bookmark's text [fields-ref].
    return paragraph.text[start:end]


def _seq(
    tokens: list[str], index: int, counted: dict[str, tuple[int, int]], levels: list[int | None]
) -> str:
    arguments, switches = _switches(tokens, {"r", "s"}, {"c", "n", "h"})
    if len(arguments) != 1:
        raise DocxRefusedError("computed-field", "a SEQ field without one identifier")
    identifier = arguments[0]
    value, last = counted.get(identifier, (0, -1))
    if "s" in switches:
        level = _int(switches["s"], "SEQ \\s")
        # A heading of that level or higher since the last field restarts the count
        # [fields-chapter-reset, fields-chapter-reset-level-2].
        if any(lv is not None and lv <= level for lv in levels[last + 1 : index + 1]):
            value = 0
    if "r" in switches:
        value = _int(switches["r"], "SEQ \\r")
    elif "c" in switches:
        if identifier not in counted:
            raise DocxRefusedError("computed-field", "a SEQ \\c field before any count")
    else:
        # \\n, or no switch: the next number [fields-seq, fields-seq-switches].
        value += 1
    counted[identifier] = (value, index)
    if "h" in switches:
        # Counted, and shown as nothing [fields-seq-switches].
        return ""
    fmt = switches.get("*", "ARABIC")
    key = "ARABIC" if fmt.upper() == "ARABIC" else fmt
    if key not in _SEQ_FORMATS:
        raise DocxRefusedError("computed-field", f"a SEQ number format {fmt}")
    return _number(value, _SEQ_FORMATS[key])


def _styleref(
    tokens: list[str],
    index: int,
    paragraphs: list[Paragraph],
    names: list[str],
    contexts: list[_Context],
) -> str:
    arguments, switches = _switches(tokens, set(), {"s"})
    if len(arguments) != 1:
        raise DocxRefusedError("computed-field", "a STYLEREF field without one style")
    wanted = arguments[0].lower()
    if wanted.isdigit() and len(wanted) == 1 and wanted != "0":
        wanted = f"heading {wanted}"
    if names[index] == wanted:
        raise DocxRefusedError("computed-field", "a STYLEREF field in a paragraph of its style")
    # The nearest paragraph of the style before the field, else the nearest after
    # [fields-chapter-captions, fields-styleref-forward].
    nearest = next((i for i in range(index - 1, -1, -1) if names[i] == wanted), None)
    if nearest is None:
        nearest = next((i for i in range(index + 1, len(names)) if names[i] == wanted), None)
    if nearest is None:
        raise DocxRefusedError(
            "computed-field", f"a STYLEREF to {wanted!r}, which no paragraph has"
        )
    target = paragraphs[nearest]
    if target.notes:
        raise DocxRefusedError("computed-field", "a STYLEREF to a paragraph with a note mark")
    if "s" not in switches:
        if contexts[nearest].symbolic:
            # Word leaves a Symbol character out of the result [fields-styleref-symbol]; which
            # of the paragraph's characters were Symbol ones, its text does not keep.
            raise DocxRefusedError("computed-field", "a STYLEREF to a Symbol character")
        # Word copies the text but a no-break space as a space, a no-break hyphen as a hyphen,
        # and no soft hyphen [fields-styleref-characters].
        return target.text.replace("\u00a0", " ").replace("\u2011", "-").replace("\u00ad", "")
    label = target.numbering.text if target.numbering is not None else None
    if label is None or not re.fullmatch(r"[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*\.?", label):
        raise DocxRefusedError("computed-field", "a STYLEREF \\s to a label it cannot read")
    # The label without its final period [fields-chapter-dotted].
    return label.removesuffix(".")


# --- blocks and tables ---------------------------------------------------------------------


class _Body:
    """Reads the blocks of one story: the body, a note, a header, a footer or a comment."""

    def __init__(self, styles: _Styles, story: tuple[str, int] | None = None) -> None:
        self.styles = styles
        self.story = story
        self.out: list[Paragraph] = []
        self.contexts: list[_Context] = []
        self.tables = 0
        self.rows_ended = 0
        # Every run read, so that the part's every run is known to be accounted for.
        self.runs: set[ET.Element] = set()
        # The sectPr closing each section so far; a paragraph holding one ends its section.
        self.sections: list[ET.Element] = []
        # Ids of bookmarks that start or end between paragraphs, which REF cannot be read from.
        self.loose_bookmarks: set[str] = set()

    def blocks(
        self,
        element: ET.Element,
        table: tuple[int, int, int] | None,
        table_style: str | None,
        under: tuple[_Rgb, ...] = (),
        conditional: tuple[ET.Element, ...] = (),
    ) -> None:
        for child in element:
            tag = child.tag
            if tag == _w("p"):
                paragraph, context = _paragraph(
                    child,
                    self.styles,
                    table,
                    table_style,
                    self.runs,
                    self.story,
                    len(self.sections),
                    self.contexts[-1].fields_open if self.contexts else 0,
                    under,
                    conditional,
                )
                self.out.append(paragraph)
                self.contexts.append(replace(context, rows_ended=self.rows_ended))
                closing = child.find(f"{_w('pPr')}/{_w('sectPr')}")
                if closing is not None:
                    self.sections.append(closing)
            elif tag == _w("tbl"):
                self.table(child, table, under)
            elif tag == _w("sdt"):
                _content_control(child)
                content = child.find(_w("sdtContent"))
                if content is not None:
                    self.blocks(content, table, table_style, under, conditional)
            elif tag == _w("customXml"):
                self.blocks(child, table, table_style, under, conditional)
            elif tag in (_w("bookmarkStart"), _w("bookmarkEnd")):
                self.loose_bookmarks.add(child.get(_w("id"), ""))
            elif tag in (_w("sectPr"), _w("tcPr")) or tag in _PROPERTIES or tag in _MARKERS:
                continue
            else:
                raise DocxRefusedError("unsupported-element", _local(tag))

    def table(
        self, element: ET.Element, outer: tuple[int, int, int] | None, under: tuple[_Rgb, ...] = ()
    ) -> None:
        index = self.tables
        self.tables += 1
        style_element = element.find(f"{_w('tblPr')}/{_w('tblStyle')}")
        table_style = self.styles.effective(
            style_element.get(_w("val")) if style_element is not None else None, "table"
        )
        problem = next(
            (s.conditional for s in self.styles.chain(table_style) if s.conditional), None
        )
        if problem is not None:
            # Formatting for the first row, banded rows and the like that could hide or change
            # text, other than what the reader applies (_TableLayout).
            raise DocxRefusedError("unsupported-element", problem)
        floating = element.find(f"{_w('tblPr')}/{_w('tblpPr')}")
        indent = _twips(element.find(f"{_w('tblPr')}/{_w('tblInd')}"), "w")
        if any(
            v is not None and not -_INCH <= v <= _FAR
            for v in (_twips(floating, "tblpX"), _twips(floating, "tblpY"), indent)
        ):
            # A table placed off the page, or more than an inch outward.
            raise DocxRefusedError("unsupported-formatting", "a table placed off the page")
        rows: list[ET.Element] = []
        _collect(element, _w("tr"), rows, {_w("tblPr"), _w("tblGrid")})
        chain = self.styles.chain(table_style)
        # The conditional parts that set what the reader reports, by type, and from which style.
        active = [
            {kind: rpr for kind, part in s.parts.items() if (rpr := _applied(part)) is not None}
            for s in chain
        ]
        cells_of: list[list[ET.Element]] = []
        for row in rows:
            cells_of.append([])
            _collect(row, _w("tc"), cells_of[-1], {_w("trPr"), _w("tblPrEx")})
        layout = (
            _TableLayout(
                element, rows, cells_of, chain, active, self.story is not None or bool(outer)
            )
            if any(active)
            else None
        )
        # Under a cell without shading of its own: the table's, its style's (any part of it,
        # as the reader does not apply the parts), or what is under the table.
        shadings = [element.find(f"{_w('tblPr')}/{_w('shd')}")]
        shadings += [row.find(f"{_w('tblPrEx')}/{_w('shd')}") for row in rows]
        shadings += [shd for style in chain for shd in style.shadings]
        # ponytail: every one of them may be under any cell (the union); a cell's own place in
        # the table and the parts tblLook turns on would narrow it, if refusals call for it.
        painted = [c for shd in shadings for c in self.styles.painted(shd)]
        table_under = tuple(dict.fromkeys([*painted, *(under or self.styles.page)]))
        for row_index, (row, cells) in enumerate(zip(rows, cells_of, strict=True)):
            for cell_index, cell in enumerate(cells):
                start, first = len(self.out), len(self.contexts)
                own = tuple(self.styles.painted(cell.find(f"{_w('tcPr')}/{_w('shd')}")))
                applied, unknown = (
                    ((), None) if layout is None else layout.cell(row_index, cell_index)
                )
                self.blocks(
                    cell,
                    outer or (index, row_index, cell_index),
                    table_style,
                    own or table_under,
                    () if unknown else applied,
                )
                height = row.find(f"{_w('trPr')}/{_w('trHeight')}")
                if (
                    height is not None
                    and height.get(_w("hRule")) == "exact"
                    and any(p.text for p in self.out[start:])
                    and (_twips(height, "val") or 0) / 20
                    < sum(c.line for c in self.contexts[first:])
                ):
                    # A row of exact height lower than its lines: Word clips what does not fit.
                    raise DocxRefusedError("unsupported-formatting", "a row too low for its text")
                if unknown is not None and any(
                    p.text or (p.numbering and p.numbering.num_id) for p in self.out[start:]
                ):
                    # What Word applies to the text or label there is not on record.
                    raise DocxRefusedError(
                        "unsupported-element", f"conditional table formatting {unknown}"
                    )
                merge = cell.find(f"{_w('tcPr')}/{_w('vMerge')}")
                continued = merge is not None and merge.get(_w("val")) in (None, "continue")
                if continued and any(
                    p.text
                    or p.notes
                    or p.comments
                    or p.pages
                    or (p.numbering and p.numbering.num_id)
                    for p in self.out[start:]
                ):
                    # Word does not draw a merged-away cell: a list label there is neither
                    # drawn nor counted (Word draws "1.", "1.", "2." for top, merged, after), and
                    # what it does with text, whitespace or a mark there is not on record.
                    raise DocxRefusedError(
                        "unsupported-element", "text, a label or a mark in a merged-away cell"
                    )
            self.rows_ended += 1


# tblLook's settings (ECMA-376 17.4.56), each an attribute or a bit of val.
_LOOK = {
    "firstRow": 0x20,
    "lastRow": 0x40,
    "firstColumn": 0x80,
    "lastColumn": 0x100,
    "noHBand": 0x200,
    "noVBand": 0x400,
}
_TABLE_PARTS = {
    "wholeTable",
    "firstRow",
    "lastRow",
    "firstCol",
    "lastCol",
    "band1Horz",
    "band2Horz",
    "band1Vert",
    "band2Vert",
    "nwCell",
    "neCell",
    "swCell",
    "seCell",
}
# The parts by Word's precedence, nearest first (corner, row, column; then the vertical and the
# horizontal band), each with where it stands and the looks that turn it on.
_REGIONS = (
    ("nwCell", ("top", "left"), ("firstRow", "firstColumn")),
    ("neCell", ("top", "right"), ("firstRow", "lastColumn")),
    ("swCell", ("bottom", "left"), ("lastRow", "firstColumn")),
    ("seCell", ("bottom", "right"), ("lastRow", "lastColumn")),
    ("firstRow", ("top",), ("firstRow",)),
    ("lastRow", ("bottom",), ("lastRow",)),
    ("firstCol", ("left",), ("firstColumn",)),
    ("lastCol", ("right",), ("lastColumn",)),
)
# What a conditional part sets that the reader applies (bold, italic, capitals, strike) or holds
# to what it reads without it (colour, size, spacing); a part setting none of these changes
# nothing the reader produces.
_APPLIED = {
    _w(name)
    for name in ("b", "bCs", "i", "iCs", "caps", "strike", "color", "sz", "szCs", "spacing")
}


def _applied(part: ET.Element) -> ET.Element | None:
    """A conditional part's run properties, if they set anything the reader applies or holds."""
    rpr = part.find(_w("rPr"))
    return rpr if rpr is not None and any(c.tag in _APPLIED for c in rpr) else None


def _looks(table: ET.Element, rows: list[ET.Element]) -> dict[str, bool | None]:
    """Each of the table's looks: on, off, or None where Word's answer is not on record.

    An attribute wins over the ``val`` bits (table-style-attr-vs-val-row, -attr-off-val-on);
    without one the bit decides (table-style-look-val-only); with neither, not on record. With
    no ``tblLook`` the first row is on (table-style-no-look) and the rest not on record. A row's
    own look (``tblPrEx``) that says otherwise than the table's makes that look not on record.
    """
    looks = [_look(table.find(f"{_w('tblPr')}/{_w('tblLook')}"))]
    looks += [
        _look(found)
        for row in rows
        if (found := row.find(f"{_w('tblPrEx')}/{_w('tblLook')}")) is not None
    ]
    return {
        name: looks[0][name] if all(x[name] == looks[0][name] for x in looks) else None
        for name in _LOOK
    }


def _look(look: ET.Element | None) -> dict[str, bool | None]:
    """What one ``tblLook`` says of each look (``_looks``)."""
    if look is None:
        return {name: True if name == "firstRow" else None for name in _LOOK}
    raw = look.get(_w("val"))
    if raw is not None and not re.fullmatch(r"[0-9A-Fa-f]{1,4}", raw):
        raise DocxRefusedError("invalid-package", f"tblLook {raw!r} is not a number")
    out: dict[str, bool | None] = {}
    for name, bit in _LOOK.items():
        stated = (look.get(_w(name)) or "").lower()
        if stated:
            out[name] = True if stated in ("1", "true", "on") else (
                False if stated in ("0", "false", "off") else None
            )  # fmt: skip
        else:
            out[name] = None if raw is None else bool(int(raw, 16) & bit)
    return out


class _TableLayout:
    """Where a table style's conditional parts stand in one table, as Word applies them.

    Word's answers, each a case in ``corpus/numbering-cases`` (table-style-*): a part applies
    where its look turns it on (``_looks``); the first row is row 0 and every row marked
    ``tblHeader`` at the top; a corner needs both its row's and its column's look on; the
    whole-table part is never applied; bands only where the style sets a band size (n rows or
    columns to a band) and the look does not turn them off, counted past the first row (column)
    only where its look is on and the style defines that part. Nearest first: corner, row,
    column, vertical band, horizontal band. ``cell`` names what is not on record, which is
    refused over text: a look or band size not on record; a corner without both looks on, or on
    a header row past the first; the last row or column under banding where the style defines
    it; first and last row (or column, or two corners) over one cell; header rows below a row
    that is none, or past the first under banding; parts or band sizes from a ``basedOn`` style,
    or the table's own band size; a part of no known type; and, where a part applies, a row off
    the grid (``gridBefore``, ``gridAfter``), merged cells, a nested table, or a table in a
    note, header, footer, comment or another table.
    """

    def __init__(
        self,
        table: ET.Element,
        rows: list[ET.Element],
        cells: list[list[ET.Element]],
        chain: list[_Style],
        active: list[dict[str, ET.Element]],
        outside: bool,
    ) -> None:
        self.parts = active[0]
        self.below = {kind for found in active[1:] for kind in found}
        self.defined = set(chain[0].parts)
        self.defined_below = {kind for style in chain[1:] for kind in style.parts}
        # The band sizes (rows, columns), and whether one is set otherwise than by the style.
        self.sizes = chain[0].bands
        self.sizes_elsewhere = [
            _band_size(table, way) is not None or any(s.bands[i] is not None for s in chain[1:])
            for i, way in enumerate(("Row", "Col"))
        ]
        self.looks = _looks(table, rows)
        self.cells = cells
        headers = [bool(_on(r.find(f"{_w('trPr')}/{_w('tblHeader')}"))) for r in rows]
        # How many rows at the top are header rows, and the header rows below a row that is none.
        self.headers = headers.index(False) if False in headers else len(headers)
        self.stray = [header and i > self.headers for i, header in enumerate(headers)]
        self.shifted = [
            any(r.find(f"{_w('trPr')}/{_w(n)}") is not None for n in ("gridBefore", "gridAfter"))
            for r in rows
        ]
        self.merged = any(
            c.find(f"{_w('tcPr')}/{_w('vMerge')}") is not None
            or c.find(f"{_w('tcPr')}/{_w('hMerge')}") is not None
            or (
                (span := c.find(f"{_w('tcPr')}/{_w('gridSpan')}")) is not None
                and span.get(_w("val")) != "1"
            )
            for row in cells
            for c in row
        )
        self.outside = outside

    def cell(self, row: int, column: int) -> tuple[tuple[ET.Element, ...], str | None]:
        """The run properties of the parts Word applies to a cell, and what is not on record.

        The run properties nearest first; what is not on record None if nothing is.
        """
        parts = self.parts.keys() | self.below
        count = len(self.cells[row])
        # Where the cell stands; anywhere, for a row off the grid or merged cells.
        anywhere = self.shifted[row] or self.merged
        place = {
            "top": row < max(self.headers, 1) or self.stray[row] or self.merged,
            "bottom": row == len(self.cells) - 1 or self.merged,
            "left": column == 0 or anywhere,
            "right": column == count - 1 or anywhere,
        }
        applied: list[str] = []
        unknown = [f"({kind}) of no known type" for kind in sorted(parts - _TABLE_PARTS)]
        for kind, where, names in _REGIONS:
            if kind not in parts or not all(place[p] for p in where):
                continue
            said = [self.looks[name] for name in names]
            if all(said) and not (kind in ("nwCell", "neCell") and row and not self.merged):
                applied.append(kind)
            elif len(names) > 1 or None in said:
                unknown.append(f"({kind}) under a look or header row not on record")
        for way, index, number in (("Vert", column, count), ("Horz", row, len(self.cells))):
            if {f"band1{way}", f"band2{way}"} & parts:
                band, why = self._band(way, index, number, anywhere)
                unknown += [why] if why else []
                applied += [band] if band in parts else []
        if {"firstRow", "lastRow"} <= set(applied) or {"firstCol", "lastCol"} <= set(applied):
            unknown.append("(first and last) over one cell")
        if len({"nwCell", "neCell", "swCell", "seCell"} & set(applied)) > 1:
            unknown.append("(corners) over one cell")
        if applied or unknown:
            nested = any(t.tag == _w("tbl") for t in self.cells[row][column].iter())
            for problem, why in (
                (bool(set(applied) & self.below), "set through basedOn"),
                (self.stray[row], "over a header row below a row that is none"),
                (self.shifted[row], "in a row off the grid"),
                (self.merged, "in a table with merged cells"),
                (nested, "over a nested table"),
                (self.outside, "in a nested table, a note, header, footer or comment"),
            ):
                if problem:
                    unknown.append(why)
        rprs = tuple(self.parts[kind] for kind in applied if kind in self.parts)
        return rprs, (unknown[0] if unknown else None)

    def _band(self, way: str, index: int, number: int, anywhere: bool) -> tuple[str, str | None]:
        """The band a row (``Horz``) or column (``Vert``) stands in, or why it is not on record.

        The band "" where it stands in none.
        """
        size = self.sizes[way == "Vert"]
        off = self.looks["noVBand" if way == "Vert" else "noHBand"]
        if self.sizes_elsewhere[way == "Vert"]:
            return "", f"(band{way}) under a band size not on record"
        if size is None or off is True:
            # No band size, no bands (table-style-bands-on-1H, -default-1H).
            return "", None
        if off is None or not re.fullmatch(r"[1-9][0-9]{0,3}", size):
            return "", f"(band{way}) under a look or band size not on record"
        if anywhere:
            return "", f"(band{way}) in a row off the grid or among merged cells"
        first, last, look_first, look_last = (
            ("firstCol", "lastCol", "firstColumn", "lastColumn")
            if way == "Vert"
            else ("firstRow", "lastRow", "firstRow", "lastRow")
        )
        skip = 0
        if first in self.defined | self.defined_below:
            on = self.looks[look_first]
            if (
                first not in self.defined
                or on is None
                or (on and way == "Horz" and self.headers > 1)
            ):
                return "", f"(band{way}) past a {first} not on record"
            # Counted past the first row (column) the style defines (table-style-sized-row-vs-band,
            # -col-vs-vband); from it where it does not (-hband-first-last, -vband-firstcol).
            skip = int(on)
        if (
            last in self.defined | self.defined_below
            and self.looks[look_last] is not False
            and index == number - 1
        ):
            # Whether banding passes over a last row (column) the style defines: not on record.
            # Where it defines none, banding goes on (table-style-hband-lastrow, -vband-lastcol).
            return "", f"(band{way}) over a {last} not on record"
        position = index - skip
        if position < 0:
            return "", None
        return f"band{position // int(size) % 2 + 1}{way}", None


def _collect(element: ET.Element, wanted: str, out: list[ET.Element], silent: set[str]) -> None:
    """The ``wanted`` children of a table or row, through content controls and custom XML."""
    for child in element:
        tag = child.tag
        if tag == wanted:
            out.append(child)
        elif tag == _w("sdt"):
            _content_control(child)
            content = child.find(_w("sdtContent"))
            if content is not None:
                _collect(content, wanted, out, silent)
        elif tag == _w("customXml"):
            _collect(child, wanted, out, silent)
        elif tag in silent or tag in _PROPERTIES or tag in _MARKERS:
            continue
        else:
            raise DocxRefusedError("unsupported-element", _local(tag))


def read_docx(data: bytes) -> list[Paragraph]:
    """Every body paragraph of a .docx, in document order, or ``DocxRefusedError``."""
    return list(read_document(data).body)


def read_document(data: bytes) -> Document:
    """The body, notes, headers, footers and comments of a .docx, or ``DocxRefusedError``."""
    package = _Package(data)
    with package.zip:
        mains = package.related("", "officeDocument")
        if len(mains) != 1:
            raise DocxRefusedError("invalid-package", f"{len(mains)} main document parts")
        document = package.part(mains[0])
        if document is None:
            raise DocxRefusedError("invalid-package", f"no {mains[0]}")
        parts: list[ET.Element | None] = []
        for kind in ("styles", "theme", "fontTable", "settings", "numbering", *_NOTE_KINDS):
            targets = package.related(mains[0], kind + "s" if kind in _NOTE_KINDS else kind)
            if len(targets) > 1:
                raise DocxRefusedError("invalid-package", f"more than one {kind} part")
            part = package.part(targets[0]) if targets else None
            if targets and part is None:
                raise DocxRefusedError("invalid-package", f"no {targets[0]}")
            parts.append(part)
        for definitions in (parts[0], parts[4]):
            # A tracked change to a style or a list gives the document two texts (``tracked``).
            if definitions is not None and any(e.tag in _TRACKED for e in definitions.iter()):
                raise DocxRefusedError("tracked-change", f"in {_local(definitions.tag)}")
        for definitions in parts[:5]:
            # Alternate content in the styles, theme, fonts, settings or lists: Word applies one
            # branch, which the reader would not. Only a list level's own child is left to the
            # level, which a paragraph cannot draw (``_level``).
            if definitions is None:
                continue
            levels = set(definitions.iter(_w("lvl")))
            if any(
                element not in levels and any(c.tag == _ALTERNATE for c in element)
                for element in definitions.iter()
            ):
                raise DocxRefusedError(
                    "unsupported-element", f"AlternateContent in {_local(definitions.tag)}"
                )
        styles = _styles(*parts[:4])
        if parts[3] is not None:
            styles.update_fields = bool(_on(parts[3].find(_w("updateFields"))))
        lists = _Lists(parts[4], styles)
        even = parts[3] is not None and bool(_on(parts[3].find(_w("evenAndOddHeaders"))))
        stories = _story_parts(package, mains[0], document, even)
        comment_parts = package.related(mains[0], "comments")
        if len(comment_parts) > 1:
            raise DocxRefusedError("invalid-package", "more than one comments part")
        comments_root = package.part(comment_parts[0]) if comment_parts else None
        if comment_parts and comments_root is None:
            raise DocxRefusedError("invalid-package", f"no {comment_parts[0]}")
    background = document.find(_w("background"))
    if background is not None:
        page = styles.colours(background, "color", "themeColor", "themeTint", "themeShade")
        styles.page = tuple(dict.fromkeys([*styles.page, *page]))
    _check_part(document)
    body = document.find(_w("body"))
    if body is None:
        raise DocxRefusedError("invalid-package", "no w:body")
    reader = _Body(styles)
    reader.blocks(body, None, None)
    _check_accounted(document, reader.runs)
    paragraphs = _labelled(reader.out, reader.contexts, lists)
    sections: list[ET.Element | None] = [*reader.sections, body.find(_w("sectPr"))]
    marks = _note_marks(paragraphs, reader.contexts, sections)
    # NOTEREF prints a note's mark, so the fields are checked once the marks are known.
    _verify_fields(
        [_with_marks(p, marks) for p in paragraphs], reader.contexts, styles, reader.loose_bookmarks
    )
    notes = {
        kind: _read_notes(part, kind, styles)
        for kind, part in zip(_NOTE_KINDS, parts[5:], strict=True)
    }
    for kind, key in marks:
        if key not in notes[kind]:
            raise DocxRefusedError("invalid-package", f"a reference to {kind} {key}, not defined")
    for (kind, key), mark in marks.items():
        if mark is None and any(n.kind == kind for p in notes[kind][key] for n in p.notes):
            # Word draws the next note's number there, which no reference shows
            # (corpus/numbering-cases, notes-custom-mark).
            raise DocxRefusedError("ambiguous-numbering", f"the mark in custom-marked {kind} {key}")
    for kind in _NOTE_KINDS:
        unreferenced = sorted(key for key in notes[kind] if (kind, key) not in marks)
        if unreferenced:
            # Word does not show a note nothing refers to; its text is in the file all the same.
            raise DocxRefusedError("unread-content", f"{kind} {unreferenced[0]}, never referred to")
    order = [(n.kind, n.id) for paragraph in paragraphs for n in paragraph.notes]

    def in_order(kind: str) -> tuple[Note, ...]:
        return tuple(
            Note(
                kind,
                key,
                marks[(kind, key)],
                tuple(_with_marks(p, marks) for p in notes[kind][key]),
            )
            for k, key in order
            if k == kind
        )

    footnotes, endnotes = in_order("footnote"), in_order("endnote")
    read_stories = {
        kind: tuple(
            _story(kind, name, tuple(uses), root, index, styles)
            if shown
            # Word shows it on no page; its text is in the file all the same.
            else Story(kind, name, tuple(uses), (), ("never-shown", f"a {kind} Word never shows"))
            for index, (name, uses, root, shown) in enumerate(found)
        )
        for kind, found in stories.items()
    }
    comments = _read_comments(comments_root, styles)
    # Every comment is anchored exactly once, in the body, a note, a header or a footer.
    anchored = [
        reference.id
        for paragraph in (
            *paragraphs,
            *(p for note in (*footnotes, *endnotes) for p in note.paragraphs),
            *(
                p
                for story in (*read_stories["header"], *read_stories["footer"])
                for p in story.paragraphs
            ),
        )
        for reference in paragraph.comments
    ]
    known = {comment.id for comment in comments}
    for comment_id in anchored:
        if comment_id not in known:
            raise DocxRefusedError(
                "invalid-package", f"a mark of comment {comment_id}, not defined"
            )
    if len(set(anchored)) != len(anchored):
        raise DocxRefusedError("invalid-package", "a comment's mark stands twice")
    unanchored = sorted(known - set(anchored))
    if unanchored:
        # Word shows no comment nothing anchors; its text is in the file all the same.
        raise DocxRefusedError("unread-content", f"comment {unanchored[0]}, anchored nowhere")
    return Document(
        body=tuple(_with_marks(p, marks) for p in paragraphs),
        footnotes=footnotes,
        endnotes=endnotes,
        headers=read_stories["header"],
        footers=read_stories["footer"],
        comments=comments,
    )


def _story_parts(
    package: _Package, main: str, document: ET.Element, even: bool
) -> dict[str, list[tuple[str, list[tuple[int, str]], ET.Element, bool]]]:
    """The header and footer parts the sections refer to, each once, in the order referred to.

    Each with the (section, type) uses that name it, sections counted in document order, and
    whether Word shows it on any page: a section naming no part of a type takes the one before
    it; its ``first`` part is shown only where it turns its first page on (``titlePg``), its
    ``even`` part only where the settings turn even pages on (``even``,
    ``evenAndOddHeaders``); otherwise Word prints the default one there.
    """
    found: dict[str, list[tuple[str, list[tuple[int, str]], ET.Element, bool]]] = {
        "header": [],
        "footer": [],
    }
    # The part each (kind, type) names in the section, a section naming none taking the one
    # before it; and the parts some section shows.
    current: dict[tuple[str, str], str] = {}
    shown: set[str] = set()
    for section, properties in enumerate(document.iter(_w("sectPr"))):
        named: set[tuple[str, str]] = set()
        for reference in properties:
            kind = next((k for k in found if reference.tag == _w(f"{k}Reference")), None)
            if kind is None:
                continue
            name = package.target(main, reference.get(f"{{{R}}}id"), kind)
            use = (section, reference.get(_w("type"), "default"))
            if (kind, use[1]) in named:
                # Not allowed (ECMA-376 17.10.5); which one Word shows is not on record.
                raise DocxRefusedError("invalid-package", f"a section names two {use[1]} {kind}s")
            named.add((kind, use[1]))
            current[(kind, use[1])] = name
            entry = next((e for e in found[kind] if e[0] == name), None)
            if entry is None:
                root = package.part(name)
                if root is None:
                    raise DocxRefusedError("invalid-package", f"no {name}")
                found[kind].append((name, [use], root, False))
            else:
                entry[1].append(use)
        title = bool(_on(properties.find(_w("titlePg"))))
        for (_, kind), name in current.items():
            if {"first": title, "even": even}.get(kind, True):
                shown.add(name)
    return {
        kind: [(name, uses, root, name in shown) for name, uses, root, _ in entries]
        for kind, entries in found.items()
    }


def _story(
    kind: str,
    name: str,
    uses: tuple[tuple[int, str], ...],
    root: ET.Element,
    index: int,
    styles: _Styles,
) -> Story:
    """A header or footer read, or refused on its own: the body is read all the same."""
    try:
        return Story(kind, name, uses, _read_blocks(root, (kind, index), styles))
    except DocxRefusedError as refused:
        return Story(kind, name, uses, (), (refused.code, refused.detail))


def _read_blocks(
    root: ET.Element, story: tuple[str, int], styles: _Styles
) -> tuple[Paragraph, ...]:
    """The paragraphs of a header, a footer or a comment, by every rule of the body.

    Fields the reader computes and lists are refused there: how Word counts them outside the
    body is not on record. PAGE, NUMPAGES and SECTIONPAGES are placed; a PAGEREF is refused.
    """
    _check_part(root)
    reader = _Body(styles, story)
    reader.blocks(root, None, None)
    _check_accounted(root, reader.runs)
    if any(c.fields for c in reader.contexts):
        raise DocxRefusedError("computed-field", f"a computed field or PAGEREF in a {story[0]}")
    if any(p.numbering is not None and p.numbering.num_id for p in reader.out):
        raise _refuse_numbering(f"a list in a {story[0]}")
    return tuple(reader.out)


def _read_comments(root: ET.Element | None, styles: _Styles) -> tuple[Comment, ...]:
    """Every comment of the comments part, in the order stored."""
    if root is None:
        return ()
    comments: list[Comment] = []
    seen: set[int] = set()
    for element in root:
        if element.tag != _w("comment"):
            raise DocxRefusedError("unsupported-element", f"{_local(element.tag)} in comments")
        comment_id = _int(element.get(_w("id"), ""), "comment id")
        if comment_id in seen:
            raise DocxRefusedError("invalid-package", f"comment {comment_id} is defined twice")
        seen.add(comment_id)
        stored = (element.get(_w("author")), element.get(_w("initials")), element.get(_w("date")))
        try:
            text = _read_blocks(element, ("comment", comment_id), styles)
        except DocxRefusedError as refused:
            # Refused on its own: the body is read all the same.
            comments.append(Comment(comment_id, *stored, (), (refused.code, refused.detail)))
            continue
        comments.append(Comment(comment_id, *stored, text))
    return tuple(comments)


def _check_part(root: ET.Element) -> None:
    """Refuse a part with tracked changes, alternate content not in a run, or stray text."""
    in_runs = {id(c) for run in root.iter(_w("r")) for c in run if c.tag == _ALTERNATE}
    for element in root.iter():
        if element.tag in _TRACKED:
            raise DocxRefusedError("tracked-change", _local(element.tag))
        if element.tag == _ALTERNATE and id(element) not in in_runs:
            raise DocxRefusedError("unsupported-element", "AlternateContent")
    _check_character_data(root)


def _check_character_data(document: ET.Element) -> None:
    """Refuse character data the reader would not read.

    Only ``<w:t>`` and ``<w:instrText>`` hold text; elsewhere in WordprocessingML character data
    is whitespace between elements. Elements of other namespaces (DrawingML positions, say) hold
    values, not text, and are left to the rules for their containers.
    """
    for element in document.iter():
        if not element.tag.startswith(f"{{{W}}}"):
            continue
        if element.tag in _TEXT_ELEMENTS:
            if len(element):
                raise DocxRefusedError("stray-text", f"an element inside {_local(element.tag)}")
        elif (element.text or "").strip(_XML_WHITESPACE):
            raise DocxRefusedError("stray-text", f"character data in {_local(element.tag)}")
        if any((child.tail or "").strip(_XML_WHITESPACE) for child in element):
            raise DocxRefusedError("stray-text", f"character data in {_local(element.tag)}")


def _check_accounted(document: ET.Element, runs: set[ET.Element]) -> None:
    """Refuse a run the reader did not read, or run content that is not in a run.

    The reader walks the body by its own rules and passes over some containers whole (section,
    paragraph and cell properties). This checks that walk against every element of the part, so
    a run in a place the walk does not go is refused instead of lost.
    """
    stack = [document]
    while stack:
        element = stack.pop()
        if element.tag == _ALTERNATE:
            continue  # read whole, as one character (_alternate)
        if element.tag == _w("r") and element not in runs:
            raise DocxRefusedError("unread-content", "a run the reader did not reach")
        if element.tag != _w("r"):
            for child in element:
                if child.tag in _RUN_CONTENT:
                    raise DocxRefusedError("unread-content", f"{_local(child.tag)} outside a run")
        stack.extend(element)


# --- tracked changes -----------------------------------------------------------------------

# The changes each view keeps; it drops the other two.
_VIEW_KEEPS = {"accepted": ("ins", "moveTo"), "original": ("del", "moveFrom")}
_CHANGES = ("ins", "del", "moveFrom", "moveTo")
# Elements whose children are runs: a change there wraps runs (a paragraph mark's change is a
# marker in the mark's properties instead).
_RUN_HOLDERS = {
    _w(name)
    for name in (
        "p",
        "hyperlink",
        "smartTag",
        "customXml",
        "sdtContent",
        "fldSimple",
        "dir",
        "bdo",
        *_CHANGES,
    )
}
_MOVE_RANGES = {
    _w(name)
    for name in ("moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd")
}


@dataclass(frozen=True)
class Change:
    """One tracked change as stored: the part it is in, what it is, and its id, author, date.

    ``kind`` is ``insert``, ``delete``, ``move-from`` or ``move-to`` (runs), the same with
    ``-paragraph-mark`` (a paragraph's end, so two paragraphs are one in the other view),
    ``insert-row`` or ``delete-row``, ``format`` (a run's properties), ``format-paragraph``,
    ``format-table``, ``format-row``, ``format-cell`` or ``format-section``.
    """

    part: str
    kind: str
    id: str | None
    author: str | None
    date: str | None


_CHANGE_KINDS = {"ins": "insert", "del": "delete", "moveFrom": "move-from", "moveTo": "move-to"}


def tracked(data: bytes) -> tuple[bytes, bytes, tuple[Change, ...]]:
    """A .docx with tracked changes as its two views (accepted, original) and the changes.

    The views are .docx without tracked changes, every change accepted and every one rejected,
    each the package with only its revised parts written again. Each rule is Word's answer to a
    case in ``corpus/tracked-cases``, as far as those cases go:

    - A run change is kept (its runs stand in its place; a deletion's ``delText`` is ``t``
      again) or dropped whole; a move's range markers go.
    - A paragraph mark a view drops joins the paragraph to the next one in document order (past
      a table, its first paragraph), which keeps its own properties. A section so joined takes
      the next section's properties (recorded for two bare sections only).
    - A row the view drops goes whole, and a table whose every row it drops; a footnote or
      endnote whose reference it drops goes with it.
    - Changed run, paragraph, table, row, cell, section and style properties are the current
      ones in the accepted view and the stored former ones in the original.

    Refused (``tracked-change``): a change holding part of a field (Word drops the whole field
    result), a dropped mark at the end of a table cell (Word dissolves the table) or of the
    document, ending a section with headers or footers or whose note settings or first page
    differ from the next section's, or before a table whose row holding the paragraph it joins,
    at any depth, the view drops (Word cannot accept it), a join into an empty table or anything
    but a paragraph
    or table, a change to a list definition (Word's Reject All rewrites the styles instead), a
    change without its former properties, a content control a view empties (Word shows
    placeholder spaces), and a view with any revision left (cells inserted, deleted or merged).

    The changes are listed in the order stored; a run formatting change a split run holds twice
    (equal to the paragraph's last one, former properties and all) is listed once.
    """
    package = _Package(data)
    with package.zip:
        entries = [(info, package.zip.read(info)) for info in package.zip.infolist()]
        changes: list[Change] = []
        sources: dict[str, tuple[ET.Element, bytes]] = {}
        # Each view's parts written again, by name.
        written: dict[str, dict[str, ET.Element]] = {"accepted": {}, "original": {}}
        for info, raw in entries:
            root = package.part(info.filename) if info.filename.endswith(".xml") else None
            if root is None:
                continue
            sources[info.filename] = (root, raw)
            if not any(e.tag in _TRACKED or e.tag in _MOVE_RANGES for e in root.iter()):
                continue
            if root.tag in _DEFINITIONS:
                # Word's Reject All does not restore a list definition; it rewrites the styles
                # (corpus/tracked-cases, list-definition-changed).
                raise DocxRefusedError(
                    "tracked-change", f"a change to a definition in {info.filename}"
                )
            # A run split in two carries its formatting change twice: listed once. A copy is a
            # run formatting change equal to the last one in its paragraph, former properties
            # and all; any other change is listed as often as it is stored.
            last: dict[ET.Element, tuple[Change, bytes]] = {}
            for change, paragraph, former in _changes(info.filename, root):
                if paragraph is not None and former is not None:
                    if last.get(paragraph) == (change, former):
                        continue
                    last[paragraph] = (change, former)
                changes.append(change)
            for view, parts in written.items():
                copy = deepcopy(root)
                _view(copy, view, _revised(copy))
                left = next((e for e in copy.iter() if e.tag in _TRACKED), None)
                if left is not None:
                    raise DocxRefusedError(
                        "tracked-change", f"{_local(left.tag)} in {info.filename}"
                    )
                parts[info.filename] = copy
    for parts in written.values():
        _drop_notes(sources, parts)
    out: list[bytes] = []
    for view in ("accepted", "original"):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for info, raw in entries:
                part = raw
                if info.filename in written[view]:
                    text = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                    tree = written[view][info.filename]
                    part = (text + ET.tostring(tree, encoding="unicode")).encode()
                # The stored dates, so the same document always gives the same views.
                entry = zipfile.ZipInfo(info.filename, info.date_time)
                archive.writestr(entry, part, zipfile.ZIP_DEFLATED)
        out.append(buffer.getvalue())
    return out[0], out[1], tuple(changes)


def _drop_notes(sources: dict[str, tuple[ET.Element, bytes]], parts: dict[str, ET.Element]) -> None:
    """Drop from a view each note whose reference the view drops, as Word does.

    A footnote or endnote referred to in the source body but not in the view's goes with its
    reference (corpus/tracked-cases, footnote-reference-deleted and -inserted).
    """
    roots = {root.tag: (name, root, raw) for name, (root, raw) in sources.items()}
    if _w("document") not in roots:
        return
    main, source, _ = roots[_w("document")]
    body = parts.get(main, source)
    for kind in ("footnote", "endnote"):
        reference = _w(f"{kind}Reference")
        gone = {e.get(_w("id")) for e in source.iter(reference)} - {
            e.get(_w("id")) for e in body.iter(reference)
        }
        if not gone or _w(f"{kind}s") not in roots:
            continue
        name, _, raw = roots[_w(f"{kind}s")]
        notes = parts.get(name)
        if notes is None:
            notes = parts[name] = ET.fromstring(_decode(name, raw))
        for note in [n for n in notes if n.tag == _w(kind) and n.get(_w("id")) in gone]:
            notes.remove(note)


# Parts whose tracked changes are refused (``tracked``).
_DEFINITIONS = {_w("numbering")}

# Properties a change records the former set of: (the change, what the properties keep of their
# own, whether the former set goes after those). A row's and a paragraph mark's own changes,
# a cell's own changes and a section's header and footer references are not properties.
_FORMER = {
    _w("rPr"): (_w("rPrChange"), {_w(n) for n in _CHANGES}, False),
    _w("pPr"): (_w("pPrChange"), {_w("rPr"), _w("sectPr")}, True),
    _w("tblPr"): (_w("tblPrChange"), set(), False),
    _w("tblPrEx"): (_w("tblPrExChange"), set(), False),
    _w("trPr"): (_w("trPrChange"), {_w(n) for n in _CHANGES}, False),
    _w("tcPr"): (_w("tcPrChange"), {_w(n) for n in ("cellIns", "cellDel", "cellMerge")}, False),
    _w("tblGrid"): (_w("tblGridChange"), set(), False),
    _w("sectPr"): (_w("sectPrChange"), {_w("headerReference"), _w("footerReference")}, False),
}
_FORMAT_KINDS = {
    "rPrChange": "format",
    "pPrChange": "format-paragraph",
    "tblPrChange": "format-table",
    "tblPrExChange": "format-table",
    "tblGridChange": "format-table",
    "trPrChange": "format-row",
    "tcPrChange": "format-cell",
    "sectPrChange": "format-section",
}


def _changes(part: str, root: ET.Element) -> list[tuple[Change, ET.Element | None, bytes | None]]:
    """Each change in document order, with the paragraph and former properties of a run's.

    The paragraph and the former properties as stored are given for a run formatting change
    only, so ``tracked`` can tell a split run's copy; for any other change they are None.
    """
    found: list[tuple[Change, ET.Element | None, bytes | None]] = []

    def visit(element: ET.Element, parent: ET.Element | None, paragraph: ET.Element | None) -> None:
        name = _local(element.tag)
        kind = None
        if element.tag.startswith(f"{{{W}}}") and name in _CHANGE_KINDS:
            kind = _CHANGE_KINDS[name]
            if parent is not None and parent.tag == _w("rPr"):
                kind += "-paragraph-mark"
            elif parent is not None and parent.tag == _w("trPr"):
                kind += "-row"
        elif element.tag.startswith(f"{{{W}}}") and name in _FORMAT_KINDS:
            kind = _FORMAT_KINDS[name]
        if kind is not None:
            change = Change(
                part,
                kind,
                element.get(_w("id")),
                element.get(_w("author")),
                element.get(_w("date")),
            )
            former = b"".join(ET.tostring(c) for c in element) if kind == "format" else None
            found.append((change, paragraph, former))
        for child in element:
            visit(child, element, element if element.tag == _w("p") else paragraph)

    visit(root, None, None)
    return found


def _revised(root: ET.Element) -> set[ET.Element]:
    """The elements ``_view`` changes: each revision, and table with no rows, and all around it.

    Anything else ``_view`` leaves as it is, so it is not walked.
    """
    out: set[ET.Element] = set()

    def visit(element: ET.Element) -> bool:
        found = (
            element.tag in _TRACKED
            or element.tag in _MOVE_RANGES
            or (element.tag == _w("tbl") and next(element.iter(_w("tr")), None) is None)
        )
        for child in element:
            found = visit(child) or found
        if found:
            out.add(element)
        return found

    visit(root)
    return out


def _view(element: ET.Element, view: str, revised: set[ET.Element]) -> None:
    """``element`` as the view has it, changed in place; only ``revised`` elements are walked."""
    keep = {_w(name) for name in _VIEW_KEEPS[view]}
    drop = {_w(name) for name in _CHANGES} - keep
    runs = element.tag == _w("sdtContent") and _runs_in(element)
    for here, following in itertools.pairwise(element):
        mark = here.find(f"{_w('pPr')}/{_w('rPr')}") if here.tag == _w("p") else None
        if following.tag != _w("tbl") or mark is None or not any(c.tag in drop for c in mark):
            continue
        # The rows on the way to the paragraph the mark joins (``_first_paragraph``), at any
        # depth: those before it in document order are the rows that hold it.
        rows: list[ET.Element] = []
        for node in following.iter():
            if node.tag == _w("p"):
                break
            if node.tag == _w("tr"):
                rows.append(node)
        if any(c.tag in drop for row in rows for c in row.findall(f"{_w('trPr')}/*")):
            # Word's Accept All leaves such a mark: it cannot join a row it removes
            # (corpus/tracked-cases, mark-deleted-before-table-first-row-deleted); in a nested
            # table its answer is not on record.
            raise DocxRefusedError("tracked-change", "a paragraph mark joins a row the view drops")
    children: list[ET.Element] = []
    for child in element:
        if child.tag in _MOVE_RANGES:
            continue
        if element.tag in _RUN_HOLDERS and child.tag in keep | drop:
            _whole_fields(child)
        if element.tag in _RUN_HOLDERS and child.tag in drop:
            continue
        row = child.find(_w("trPr")) if child.tag == _w("tr") else None
        if row is not None and any(c.tag in drop for c in row):
            # A row the view drops goes whole, as Word's does (corpus/tracked-cases).
            continue
        if child in revised:
            _view(child, view, revised)
        if child.tag == _w("tbl") and not any(True for _ in child.iter(_w("tr"))):
            # A table whose every row the view drops goes with them: Word has no empty table.
            continue
        if element.tag in _RUN_HOLDERS and child.tag in keep:
            if view == "original":
                for node in child.iter():
                    if node.tag == _w("delText"):
                        node.tag = _w("t")
                    elif node.tag == _w("delInstrText"):
                        node.tag = _w("instrText")
            children.extend(child)
        else:
            children.append(child)
    if element.tag in _FORMER:
        change, own, after = _FORMER[element.tag]
        former = element.find(change)
        if former is not None:
            stored = former.find(element.tag)
            # The former set is properties of the same kind, holding no change of its own and,
            # for a paragraph, not its mark or section. (A mark's former properties may hold the
            # mark's own change, as Word writes them: dealt with as the current ones are.)
            if (
                stored is None
                or any(c.tag == change for c in stored)
                or (element.tag == _w("pPr") and any(c.tag in own for c in stored))
            ):
                raise DocxRefusedError("tracked-change", f"{_local(change)} without its properties")
            if view == "original":
                kept = [c for c in children if c.tag in own]
                children = list(stored) + kept if after else kept + list(stored)
            else:
                children = [c for c in children if c is not former]
    if element.tag == _w("trPr"):
        # The row's changes this view keeps are no longer changes (one it drops dropped the row).
        children = [c for c in children if c.tag not in keep]
    if element.tag == _w("pPr"):
        mark = next((c for c in children if c.tag == _w("rPr")), None)
        if mark is not None:
            # The mark's changes this view keeps are no longer changes; one it drops stays as
            # the sign that the paragraph joins the next (_join).
            for marker in [c for c in mark if c.tag in keep]:
                mark.remove(marker)
    if element.tag == _w("sdtContent") and runs and not any(_runs_in(c) for c in children):
        # Word shows an emptied content control's placeholder, which is not in the document.
        raise DocxRefusedError("tracked-change", "a content control left empty")
    element[:] = children
    _join(element, drop)


def _runs_in(element: ET.Element) -> bool:
    return next(element.iter(_w("r")), None) is not None


def _whole_fields(change: ET.Element) -> None:
    """Refuse a change that holds part of a field: its begin, separator or end alone.

    Word accepts a deleted separator by removing the field's result with it (corpus/
    tracked-cases), not by what the change holds; only whole fields are taken or left.
    """
    depth = 0
    for node in change.iter(_w("fldChar")):
        kind = node.get(_w("fldCharType"))
        if kind == "begin":
            depth += 1
        elif depth == 0 or kind not in ("separate", "end"):
            raise DocxRefusedError("tracked-change", f"a change holds part of a field ({kind})")
        elif kind == "end":
            depth -= 1
    if depth:
        raise DocxRefusedError("tracked-change", "a change holds part of a field (begin)")


def _join(element: ET.Element, drop: set[str]) -> None:
    """Join each paragraph whose mark the view drops to the paragraph after it.

    The paragraph after it is the next in document order: past a table, its first cell's first
    paragraph (Word's answer, corpus/tracked-cases). A paragraph that ends a section joins the
    next section's first paragraph and the section ends there no more, unless the section has
    headers or footers of its own; one at the end of a table cell or of the document is refused.
    """
    children = list(element)
    out: list[ET.Element] = []
    carried: list[ET.Element] = []
    for index, child in enumerate(children):
        if carried:
            target = _first_paragraph(child)
            properties = target.find(_w("pPr"))
            at = 1 if properties is not None and target[0] is properties else 0
            target[at:at] = carried
            carried = []
        mark = child.find(f"{_w('pPr')}/{_w('rPr')}") if child.tag == _w("p") else None
        if mark is not None and any(c.tag in drop for c in mark):
            following = children[index + 1] if index + 1 < len(children) else None
            if following is None or following.tag not in (_w("p"), _w("tbl")):
                raise DocxRefusedError(
                    "tracked-change", "a paragraph mark joins what is not a paragraph"
                )
            section = child.find(f"{_w('pPr')}/{_w('sectPr')}")
            if section is not None and any(
                c.tag in (_w("headerReference"), _w("footerReference")) for c in section
            ):
                raise DocxRefusedError("tracked-change", "a section with headers or footers ends")
            after = next((n for c in children[index + 1 :] for n in c.iter(_w("sectPr"))), None)
            if section is not None and any(
                _shape(section.find(_w(name)))
                != _shape(None if after is None else after.find(_w(name)))
                for name in ("footnotePr", "endnotePr", "titlePg")
            ):
                # Which section's note numbering (or first page) the joined section keeps is not
                # on record: corpus/tracked-cases has only two bare sections.
                raise DocxRefusedError(
                    "tracked-change", "a section ends between sections that differ"
                )
            carried = [c for c in child if c.tag != _w("pPr")]
            continue
        out.append(child)
    element[:] = out


def _shape(element: ET.Element | None) -> object:
    """An element's name, attributes and children, to compare two as Word would read them."""
    if element is None:
        return None
    return (element.tag, sorted(element.attrib.items()), [_shape(c) for c in element])


def _first_paragraph(block: ET.Element) -> ET.Element:
    """The first paragraph of a paragraph or a table, in document order, or a refusal."""
    first = block if block.tag == _w("p") else next(block.iter(_w("p")), None)
    if first is None:
        raise DocxRefusedError("tracked-change", "a paragraph mark joins an empty table")
    return first
