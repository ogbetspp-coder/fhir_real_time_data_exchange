# Design note: the QRD template registry

- Status: Built for the centrally authorised SmPC in English, QRD template 10.4
- Date: 2026-09-23
- Related: `docs/roadmap.md` item 8 (the engine), `docs/design/extractor-spike.md`,
  `fhir/mappings/cap-smpc-en.json`, `AGENTS.md` ("Fail closed on missing, duplicate, or
  ambiguous mandatory QRD sections")

## Why

The engine (roadmap item 8) has to find the sections of a label and tell the text the EMA
prescribes from the text a company wrote. Both need the template as data: which headings exist,
how each may be written, which are optional and when, and which sentences are standard
statements that must be used as written. Until now the repository held only the headings'
titles (`fhir/mappings/cap-smpc-en.json`), typed by hand, with no record of which template they
came from.

The registry is that data, derived mechanically from the EMA's own files and pinned by hash, so
every entry can be traced to the byte it came from, and a new EMA release is a visible,
reviewed change rather than a silent one.

## The sources

`qrd/sources/` holds byte copies of four documents from the EMA's QRD page, and
`qrd/sources.lock.json` records each one's URL, SHA-256, size, EMA reference number and the
dates the page gives. The files are committed, unlike the FHIR packages under `fhir/vendor/`,
because CI must rebuild the registry from them without depending on the EMA website, and they are
small (135 KB together).

| File                                  | EMA reference       | Last updated | Used for                                                |
| ------------------------------------- | ------------------- | ------------ | ------------------------------------------------------- |
| QRD product-information template 10.4 | —                   | 2024-02-29   | The SmPC section tree, headings and standard statements |
| Appendix I (SmPC 4.6)                 | EMA/215345/2023     | 2023-06-23   | The pregnancy and lactation statement menu              |
| Appendix II (SmPC 4.8)                | EMA/295934/2018 v.7 | 2026-03-25   | The frequency convention and system organ classes       |
| Appendix III (SmPC 6.4)               | EMEA/29277/2003 v.6 | 2026-03-25   | The storage statement menu                              |

The Word files are the source, not PDFs: the EMA publishes the base template and its appendices
only as Word files, and the PDFs in circulation are conversions. On the retrieval date the QRD
page listed version 10.4 as current and the version 11 draft as "consultation closed"; version
11 is not in the registry.

Two checks keep the pin honest. `zone-a/tests/test_qrd_registry.py` compares every committed
file with its lock entry, offline, in CI. `zone-a/scripts/check_qrd_sources.py` downloads each
URL and compares hashes on demand, never in CI; a difference means the EMA has published a new
version, and adopting it is a reviewed change: new bytes, new lock entry, regenerated registry.

## The reader

`zone-a/src/zone_a/docx/reader.py` reads the text of a Word body and refuses a document whose
text it cannot produce exactly. It is the first component of the engine and is written to the
same rule as the fidelity check: false refusals are acceptable, silent changes are not. Its
module docstring lists every rule and every refusal. What the EMA files and the review forced:

1. **Symbol-font glyphs.** Appendix II writes the "≥" of "Very common (≥ 1/10)" as
   `<w:sym w:font="Symbol" w:char="F0B3"/>` (four times) and Appendix III writes the "°" of
   "25 °C" the same way (21 times). In the template, the Czech local representative's
   placeholders "{Název}", "CZ {město}>" and "Tel: +{telefonní číslo}" have Symbol-font braces,
   which is why a PDF text layer of the same template shows "Název". A reader that collects only
   `<w:t>` text returns "( 1/10)" and "25 C" and reports nothing. The reader maps every character
   of a run whose two Latin font slots (`ascii` and `hAnsi`) are both Symbol — set directly, by
   a style, by the document defaults or through the theme — through a closed table: Adobe's
   Symbol encoding as the Unicode Consortium's `symbol.txt` maps it, checked entry by entry
   against that file. Where `symbol.txt` gives two characters for one code, the table takes one
   and says so (0x6D is U+03BC GREEK SMALL LETTER MU, not U+00B5 MICRO SIGN). A run with Symbol
   in only some of its font slots, or with a font hint or a complex-script or right-to-left
   property, is refused: Word picks the font character by character there. Dingbat fonts, and
   any font the document's font table declares symbol-encoded, are refused.
2. **Formatting that changes what a reader sees.** Superscript, subscript, raised text,
   capitals, strike-through, highlight (with its colour), shading (on the run or the
   paragraph), right-to-left text and faint text (white, under two points, or scaled under a
   fifth) are reported as marks on the exact characters (`Paragraph.marks`), because `text`
   alone flattens "10" with a superscript "9" to "109". A caller that uses `text` must look at
   the marks. Other appearance (colour, size, bold, italic, underline) is not reported. A
   picture is U+FFFC OBJECT REPLACEMENT CHARACTER where it stands: the black triangle of the
   additional-monitoring statement is a picture in the template; a U+FFFC typed as text is
   refused.
3. **Fields.** A field keeps its stored result and drops its instruction, however deeply
   nested; a paragraph that ends inside an instruction is refused, and so is a field with no
   stored result (a form checkbox, a SYMBOL field without a result, an empty simple field) or
   one marked for update, because what Word shows for those is computed.
4. **Styles and hidden text.** Run properties are looked up on the run, its character style,
   its paragraph style and its table style (each through its `basedOn` chain, falling back to
   the document's default style of that kind when the id is absent or unknown, as Word does),
   then the document defaults. A run with text that any of these levels hides is refused
   unless the run itself says it is visible. A hidden paragraph mark, direct or through the
   paragraph's style, is reported (`mark_hidden`): Word shows such a paragraph run on into the
   next. A table whose effective style has conditional formatting (first row, banded rows) is
   refused, because the reader does not apply it; the template defines one such style and
   never uses it.
5. **The package.** The main part is found through the package relationships, not by name; a
   part name that occurs twice (ignoring case), a part that is not UTF-8 and any DTD are
   refused.

It also refuses any revision anywhere in the body (including formatting changes and deleted
paragraph marks), text boxes, footnote references, embedded objects, charts and other
non-picture drawings, alternate content, content controls bound to data, text in a vertically
merged-away cell, text whose whitespace is not preserved or that holds a raw tab or line break,
and any element or container it does not know. List numbering, direct or through a style, is reported
as metadata and never rendered into the text. Headers, footers, footnotes and comments are
separate parts and are not read. The rule for field instructions was prompted by Appendix V's
header, which carries `DOCPROPERTY DM_emea_doc_ref_id \* MERGEFORMAT` next to its displayed
value; Appendix V is not pinned and headers are not read, so that rule is tested on synthetic
files only.

## The grammar

The QRD templates state their convention once: `{text}` is to be filled in, `<text>` is to be
selected or deleted, and `[text]` is guidance that is not part of the product information.
`zone-a/src/zone_a/qrd/pattern.py` parses a template string into tokens and renders them back,
and every parsed item in the registry renders back to its source exactly (a test checks this).
Two rules are read from the EMA's usage rather than its text: a `<` followed by whitespace is a
less-than sign (Appendix II: "<Common (≥ 1/100 to < 1/10)>"), and a `>` with nothing open is a
greater-than sign. The second can misread text such as "<patients > 65 years>", so the registry
build refuses any item whose parse leaves a literal `>` in its text; none of the pinned sources
has one. A paragraph that leaves a bracket open is joined with the next until the
brackets balance; that is how "<Traceability" and the sentence under it become one optional
block.

## The registry

`qrd/registry/cap-smpc-en-10.4.json`, generated by `zone-a/scripts/generate_qrd_registry.py`
and compared byte for byte with a fresh build in `zone-a/tests/test_qrd_registry.py`. It holds:

- **sections**: 32 headings from "1. NAME OF THE MEDICINAL PRODUCT" to "12. INSTRUCTIONS FOR
  PREPARATION OF RADIOPHARMACEUTICALS", each with its number, level, the heading as the
  template writes it, the title as tokens (so "6.5 Nature and contents of container <and special
  equipment for use, administration or implantation>" keeps its optional segment), whether the
  whole heading is optional (2.1, 2.2, 11 and 12), and the template's own guidance where it
  gives one ("For advanced therapy products only" on 2.1 and 2.2);
- **items** under each section, in order, each classified as a subheading, a statement, a
  fill-in or guidance, and marked optional when the whole item is in `<…>`. The classification
  is mechanical: guidance if it is only `[…]`; a fill-in if it is only `{…}`; a statement if it
  holds a fill-in, ends in sentence punctuation, contains ". " or spans paragraphs; otherwise a
  subheading. Trailing footnote markers (`…>*`, `…Appendix V.*`) and Appendix III's " or"
  between alternatives are split off into `note` and `connector`, with the exact characters in
  `trailer`. The template's light-grey highlight and its shading, which mean "not in the
  printed material", are kept as `marks` (`highlight-lightGray`, `shading`). Any other mark on a
  source paragraph the build reads is refused (capitals only where they change a letter), and so
  is a hidden paragraph mark on a paragraph with text;
- **documentStatements**: the additional-monitoring statement before section 1 and the
  "Detailed information on this medicinal product is available on the website…" statement at
  the end;
- **appendices**: Appendix I's statements by id (`pregnancy.1`–`pregnancy.9`,
  `lactation.1`–`lactation.3`), Appendix II's rows by the EMA's own codes (`001`–`006` for
  frequency, `007`–`033` for system organ classes), and Appendix III's twelve SmPC storage
  statements with their five footnotes, each attached to the section it serves (4.6, 4.8, 6.4).
  Appendix II must be a single table of two cells per row under a "Ref | EN" header.

`zone-a/src/zone_a/qrd/headings.py` recognises an SmPC heading in a line of label text that is
already known to be Annex I (the labelling and the leaflet reuse lines such as "1. NAME OF THE
MEDICINAL PRODUCT"): after runs of
space, tab and no-break space are collapsed, the line must equal one of the forms the registry
allows (the number as the template writes it, then the title with each optional segment present
or absent). Case and every other character must match. It never guesses.

## What the EMA's own files got wrong

The registry records these rather than hiding them.

- **An unbalanced statement in the adopted template.** Section 4.2's "<There is no relevant use
  of {X} <in the paediatric population> <in children aged {x to y} <years> <months> […] <for the
  indication of...>.>" opens one more `<` than it closes; the version 11 draft repeats it. The
  registry applies one erratum (`ERRATA` in `zone_a/qrd/registry.py`): it closes the bracket
  after the guidance, as in the sibling statement "<{X} should not be used in children aged …
  […] because of …>", keeps the verbatim source beside the corrected pattern, and says why in
  the item's `erratum` field. The build refuses an erratum that does not apply to exactly one
  item, so a new template that fixes the text makes the correction fail loudly instead of
  lingering.
- **Appendix I does not keep its own convention.** In four of its twelve entries
  (`pregnancy.1`, `.2`, `.3` and `.6`) the `<` that opens the statement is never closed, and
  `pregnancy.3` has a stray ")" inside a bracket. Rather than invent four corrections, those
  entries keep their paragraphs verbatim with `bracketsBalanced: false` and no pattern.
- **An unclosed brace in the package leaflet.** The Polish local representative's "<{Adres:"
  never closes its `{`. It is outside the SmPC and does not reach the registry.
- **Drift in the template's own text.** "5.1 \tPharmacodynamic properties" has a space before the
  tab; "8.\tMARKETING AUTHORISATION NUMBER(S) " ends in a space; Appendix I writes "[1]<Based"
  and "[2] <Based", and "should not be used<during". The registry keeps each source string as
  written and matches headings after collapsing whitespace, so none of these changes a result.

## What this shows about the current mapping

Every numbered heading in `fhir/mappings/cap-smpc-en.json` (28) is a registry heading, and every
named subsection (Posology, Method of administration, Reporting of suspected adverse reactions)
is a registry subheading; a test keeps it so. One question it raises, not answered here: the
mapping's 6.5 title includes the segment "and special equipment for use, administration or
implantation", which the annotated template marks as "for advanced therapy medicinal products
only", and the crosswalk writes the rule's title as the heading of every document it
publishes. Whether that matches the EMA ePI code system's display for 6.5 has to be checked
against the pinned EMA package before anything changes.

## Known limits

- Only the SmPC (Annex I), in English, from the centralised template. Annex II, the labelling,
  the package leaflet, the national-procedure template 4.2, the ATMP template 1.1 and other
  languages are not in the registry.
- The two optional ATMP subsections 2.1 and 2.2 are followed in the template by "<Excipient(s)
  with known effect>" and "<For the full list of excipients, see section 6.1.>", which apply to
  section 2 of every product. The registry files them under 2.2 because that is where they
  stand; a matcher must not read that as "ATMP only".
- Statement patterns are stored, not yet matched against label text. Matching them, and
  recording which Appendix I, II or III option a label uses, is the next step.
- The classification into subheading and statement is mechanical and could be wrong for a
  future template; the committed JSON shows every classification, so a change is visible in
  review.
