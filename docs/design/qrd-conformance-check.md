# Design note: the QRD conformance check

- Status: Built and run on every EMA-published English SmPC pinned in
  `labels/ema-epi/sources.lock.json` (five)
- Date: 2026-09-23; updated 2026-09-28 (`qrd-check/1.2.0`, `epi-reader/1.2.0`) and 2026-10-04
  (`qrd-check/1.3.0`, the label reader's `epi-reader/1.3.0`); the checks on record are of
  `qrd-check/1.4.2` and `epi-reader/1.3.3` (`labels/ema-epi/checks/`)
- Related: `docs/design/qrd-registry.md` (the registry it checks against), `docs/roadmap.md`
  item 8, `AGENTS.md` (the scoped exception for authority-published ePI)

## Why

The QRD registry holds the EMA's SmPC template as data. The first use of that data is to check a
real SmPC against it: are the headings the template's, in its order, under the right codes; is
each standard statement worded as the template words it; is anything in the document that a
reviewer would send back. That is what the EMA's QRD review raises by hand, and it is the first
thing the engine can do for a label without changing a word of it.

The check proposes; a person decides. Every finding says what was compared and how, and the
check never corrects, completes or rewrites the label's text.

## The labels

Every summary of product characteristics pinned in `labels/ema-epi/sources.lock.json`, as the
EMA itself publishes it through its ePI API (`labels/ema-epi/`, pinned by SHA-256). Today that
is five:

| Product                            | Kind                                          | Composition date |
| ---------------------------------- | --------------------------------------------- | ---------------- |
| Jentadueto                         | small molecule (linagliptin / metformin)      | 2024-04-22       |
| Nuvaxovid                          | biological medicine (vaccine), black triangle | 2024-06-14       |
| Brukinsa                           | small molecule (zanubrutinib), black triangle | 2024-04-08       |
| Imatinib Teva, hard capsules       | small molecule (imatinib)                     | 2025-02-06       |
| Imatinib Teva, film-coated tablets | small molecule (imatinib)                     | 2025-02-06       |

The first three were pinned for this check; the two Imatinib Teva SmPCs, under one product List,
were pinned for the authority importer (roadmap item 3a) and are checked too. Only these five
products on the API have an English SmPC, from the EMA's pilot, which the portal describes as
"for pilot purposes only, and may not be up-to-date". A difference from template 10.4 may
therefore be a difference of template version; the results say which version they were checked
against. EPAR PDFs and the EMA's tracked-changes Word files are not used: `AGENTS.md` does not
allow them yet. `zone-a/scripts/check_label_sources.py` and `check_qrd_sources.py` compare the
pinned files with what the EMA serves, weekly (`.github/workflows/ema-drift.yml`, which opens an
issue on a change and never gates a pull request); the label check fetches as the import gate
does.

## The reader

The label reader's ePI reader (`label-docx-reader/src/label_docx/epi.py`) reads an ePI Bundle
into sections of paragraphs, with the Word reader's `Paragraph` and `Mark` model. The check takes
only its certified read (`label_docx.epi_output.read`, through `zone-a/src/zone_a/certified.py`):
the reader's independent check (`label_docx.certify`) accounts for every character of the result,
a result it cannot account for is refused (`uncertified`) like any other refusal, and the
sections the check reads are rebuilt from that certified JSON, field for field. It is written to
the Word reader's rule: the text a browser shows, exactly, or a refusal with a reason, for the text and its marks; its module
docstring lists every rule. It does not lay the page out: CSS that places or paints one text
over another is refused only in the cases the reader lists, and the rest is a stated residual of
the check (below). Nor does it parse as a browser does: it parses the div as XML, a browser as
HTML, and where the two build different trees it refuses the cases it lists (processing
instructions, comments, prefixed elements, self-closing elements other than `br`, `hr` and `img`,
a block in an open paragraph
and the rest); any other difference is a stated residual, and reading with an HTML5 parser is a
tracked follow-up. It is
not the fidelity scanner, which is the contract for narrative this repository publishes and
rightly refuses the EMA's divs (inline CSS on nearly every element).

Each section is read on its own, so a section the reader cannot vouch for is refused without
losing the rest. CSS is checked against a closed list: layout properties are ignored within the bounds the reader's module docstring lists (the one list; in short: lines of text kept apart, nothing drawn more than 12pt left of its container's start, no band of padding, border or background over a line), text colour, background and tiny text are marked, and anything else (`display`, `visibility:
hidden`, an unknown property) refuses the section. Word comment markup refuses the section,
because the comment's text would otherwise read as label text.

The importer's T reads the same divs against a closed list of its own, and the two differ on
purpose where their jobs differ: T redraws the text and must know how it is drawn, the reader
needs only the characters a browser shows. `test/fixtures/authority/style-cases.json`
(`zone-a/scripts/generate_style_cases.py`) holds the shared cases: each style with the reader's
answer and, where T answers otherwise, T's answer and why (contrast under 4.5:1, fonts beyond the
renderer's, borders outside tables, struck text, some shifts, a few Word-only
properties); `test/authority/style-cases.test.ts` holds T to the reader's answer on every other
case, so a change to either list shows until it is recorded. From `epi-reader/1.2.0` the reader
takes Word's `tab-stops` and a `position: relative` shift as T does (a shift of a point or more
marked as a superscript or subscript, bounded at 6pt; as T4, no shift inside another or inside
a superscript, a subscript or a `vertical-align`, and none of a point or more around one, since
each is bounded only on its own; a shift under a point may hold a superscript, where T drops a
shift only under 0.1 of the smallest text beneath it, so the two differ between that bound and
a point, a divergence the shared cases record), judges faint text by its contrast with the background painted
under it (text on its own colour cannot be seen; white on black can), refuses a colour or
background keyword a browser drops (`color: none`, `background-color: auto`, which leave the
declaration before them in force: "color: black; color: none" on black is hidden), and refuses a
private-use, unassigned or default-ignorable code point (a Symbol font's U+F0B3 is drawn "≥").

One defect is read through by a stated rule rather than refused: a `<` that cannot open a tag
is text, as the HTML tokenizer reads it, and each section where that happened is noted. The
EMA writes "GFR < 60 mL/min" that way in Jentadueto.

## The matching

`zone-a/src/zone_a/qrd/check.py`; its module docstring states each rule.

- **Headings.** Each section's EMA code is looked up in `fhir/mappings/cap-smpc-en.json`. The
  section must carry that code's heading: for a numbered section one of the forms the registry
  allows (`zone_a/qrd/headings.py`), for a named subsection the mapping's title.
- **Statements.** The registry's patterns are matched against the text of their section,
  after whitespace is collapsed: literal text word for word, with the template's spaces and
  paragraph breaks (the space or break before an optional segment belongs to it), a fill-in as
  any text within one paragraph, an optional segment present or absent, "(s)" as singular or
  plural, guidance and footnote markers dropped. A test writes every statement out the ways a
  person would (optional segments all in, none, every other one) and checks each is found. A
  statement over two paragraphs ("Traceability" and the sentence under it) is matched against
  consecutive paragraphs. Struck-through and faint text never counts as a match. A subheading
  matches its line exactly, "(s)" as singular or plural ("Pharmacokinetic/pharmacodynamic
  relationships"). A statement's first letter may be either case where it opens the statement,
  also after optional segments the label leaves out.
- **Alternatives.** A statement with less than 12 characters of required literal text is too
  little to tell on its own. Where it is made of alternatives (Appendix I's `lactation.1`, all of
  whose sentences are optional; Appendix III's "<Do not <refrigerate> <or> <freeze>.>"), each
  optional segment is taken as required in turn, and one still too short takes each of its own
  segments in turn; each form long enough is matched, and the result names the ones used
  (`"alternatives": ["3", "4.1"]`: the third segment, and the first within the fourth) or, for a
  deviation, the one it resembles (`"alternative"`).
- **Deviations.** A statement that does not match but resembles a stretch of its section is
  reported with its differences, as a proposal for a person to judge. Resemblance is an
  alignment of the statement with the text, token by token (words and single punctuation
  marks): a fill-in takes any run of words, an optional segment is taken or skipped, and each
  substituted, missing or inserted token costs one (a word for a punctuation mark, or the reverse, costs two). A deviation needs at least 85% of matched
  tokens against matched tokens plus changes, and either six matched words or every word the
  statement requires. The differences are exact ("https" against "http", "," against ";"); the
  same tokens with other spaces or paragraph breaks, fewer or more, are a `layout` difference
  ("2°C" against "2 °C"; "Cardiac" and "disorders" in two paragraphs of a table). Text an exact match of a sibling statement explains is not compared again, and where
  two resemblances overlap only the closer is reported. 85% and six words are proposal
  thresholds, set on the first three labels.
- **Refused parts.** A statement not found in a section with a part the reader refused is
  `not-checked`, not `absent`. A resemblance in the readable part is still a deviation.
- **Appendices.** Appendix I's statements against 4.6, Appendix II's frequency convention and
  system organ classes against 4.8, Appendix III's storage statements against 6.4.
- **Every item has a status.** Each of the registry's 126 statements and subheadings has exactly
  one: `used`, `deviation`, `absent`, `not-checked` with a `reason` (`refused-part`,
  `section-absent`: its section is not in the document, `section-not-mapped`: the mapping has no
  code for it, as for section 12, for radiopharmaceuticals) or `not-checkable` with a `reason`
  (`too-little-text`, `unbalanced-brackets`: four Appendix I entries). An optional subsection the
  mapping does not list is read as part of its section: the template files "For the full list of
  excipients, see section 6.1." and "Excipient(s) with known effect" under the ATMP-only 2.2, but
  they apply to section 2 of every product, so they are matched in section 2 (keeping their
  `smpc.2.2#n` identifiers).

`labels/ema-epi/checks/<product>.json` holds each result; `zone-a/scripts/check_labels.py`
writes them and a test compares them with a fresh run, by digest (a difference is named by JSON
pointer, never quoted). Each result names the reader and checker versions, the registry and
mapping versions, and the SHA-256 of the exact source, registry and mapping bytes it was
computed from; `zone-a/versions.lock.json` ties each version to the hash of the code that decides
it, and a test refuses a code change without a bump (`zone-a/scripts/lock_versions.py`).

## What it found

Every finding below was confirmed by reading the source div.

- **Wrong section codes (Jentadueto).** "Special populations" carries the code of "Method of
  administration" (200000029803), and "Summary of the safety profile" the code of "Reporting of
  suspected adverse reactions" (200000029818). A system that reads the ePI by code files those
  paragraphs under the wrong heading.
- **Template brackets left in headings (Brukinsa).** "6.5 Nature and contents of container <and
  special equipment for use, administration or implantation>" and "6.6 Special precautions for
  disposal <and other handling>": the template's optional segments were published with their
  brackets.
- **A reviewer comment in the published text (Brukinsa).** Section 4.8 carries Word comment
  markup (`msocomanchor`, `msocomtxt`). The reader refuses the section rather than read the
  comment as label text.
- **Broken black-triangle markup (Nuvaxovid).** The additional-monitoring picture is written
  `annotationsrc="…""`, which is not well-formed; the opening section is refused.
- **Invalid XHTML (Jentadueto).** Unescaped `<` in seven sections.
- **A section the reader refuses (both Imatinib Teva SmPCs).** 5.1 sets a line height of 11.7pt,
  under the reader's 12pt bound, so nothing in 5.1 is checked. Before `epi-reader/1.2.0` the
  reader also refused 4.2's Posology and 5.1 for `position: relative` (the raised "9" of
  "10⁹/l") and the tablets' section 1 for Word's `tab-stops`, all of which the importer's T reads.
- **Not valid FHIR (all five).** The API serialises `resourceType`, `language` and `status` of
  the Composition as the number 0, and names `http://www.test.com` as the author's identifier
  system.
- **Wording that differs from the template.**
  - Jentadueto, Nuvaxovid and Brukinsa link to `http://www.ema.europa.eu`; template 10.4 has
    `https://www.ema.europa.eu`. Both Imatinib Teva SmPCs have the `https` address and leave off
    the closing statement's full stop.
  - Brukinsa leaves off the full stop of "For the full list of excipients, see section 6.1."
    in section 2 (found from `qrd-check/1.2.0`, which checks section 2's standard statements).
  - Both Imatinib Teva SmPCs write the 4.2 statement on paediatric data as "published" data
    "summarised" in section 5.1 where the template has "described", and write the frequency
    convention in 4.8 with commas and other line breaks than Appendix II, and "not known" without
    "frequency".
  - Jentadueto writes "breast-feeding" where Appendix I's `lactation.1` has "breast feeding".
  - Nuvaxovid adds "and include batch/Lot number if available" to the reporting statement in
    4.8. That is common for vaccines, and a person should confirm it.
  - Nuvaxovid writes the paediatric deferral statement in 5.1 with a comma where the template
    has brackets.
  - Brukinsa writes "no or negligible influence in the ability to drive and use machines"; the
    template has "on".
  - Brukinsa's paediatric waiver in 5.1 names the conditions with "for the treatment of …"
    where the template has "in {condition}", and its 4.2 paediatric statement reads "in
    children and adolescents below 18 years of age" where the template has "in children aged {x
    to y}". Both are common, and a person should confirm them.
  - Nuvaxovid writes the frequency convention in 4.8 with thousands commas ("1/1,000") where
    Appendix II has a space ("1/1 000"), and both Nuvaxovid and Jentadueto write "not known
    (cannot be estimated from the available data)" without "frequency".
  - Nuvaxovid writes "Store in a refrigerator (2°C – 8°C)" where Appendix III has "2 °C – 8
    °C" (a `layout` difference).
- **Formatting.** Blue text (an email address and the EMA link) in Brukinsa and both Imatinib
  Teva SmPCs, besides the template's intended grey shading of "the national reporting system
  listed in Appendix V" in all five. Both Imatinib Teva SmPCs write much of 4.2 in `#231f20`, a
  near-black just past the reader's nearly-black bound (every channel at most `#20`), reported as
  colour for a person to dismiss, and underline the "Ph+ ALL" subheadings of 4.2. Red, yellow and other colours in the source fall only on pictures or spaces, which
  show no text differently; nearly black text (`#0d0d0d`) reads as black and nearly white
  shading (`#e6e6e6` behind a table heading) as none; none of these is reported. From
  `epi-reader/1.1.0` and `qrd-check/1.1.0` an underline over text it can change
  (`zone_a.underline`) is a formatting finding too, and so is a border beside or over inline
  text (the reader's `border` mark, read side by side as a browser cascades the styles). From
  `qrd-check/1.3.0` the colour a browser draws a link in (`#0000ee`: the label reader marks an `a`
  with an `href` in it, under the link's underline) is the browser's, not the label's, and is not
  a finding; that colour where no underline covers it whole still is. Bold and italic, which the
  label reader marks, are not findings either. Brukinsa writes ">1", ">5" and ">2" in 5.1 with
  the ">" underlined, which the
  EMA's viewer draws as "≥" while the text says ">", and Jentadueto underlines a 5.1 heading
  holding "≥". Underlines over words, digits, e-mail addresses and plain punctuation (the
  Brukinsa 4.5 subheadings, for example) change nothing and are not reported. Faint and
  struck text, and shaded text (dark or same-colour shading hides a sign), is reported over a
  sign as well as a word (a white "-" before "20 °C" reads
  "-20 °C" to the text and "20 °C" to a reader). From `epi-reader/1.2.0` text whose colour has
  a contrast under 1.33:1 with the background under it (black on black, navy on navy) is faint
  too, so it never matches a statement, while white text on a dark background is read. A bottom border on a block or a table cell is
  layout and is not marked: under a lone sign in a narrow cell or block it draws "≤", which is a
  stated residual of the check (ADR 0005's closed CSS list governs the import). The reader
  refuses a section naming a font outside a closed list of Unicode text fonts (a symbol font
  draws other glyphs: Wingdings "J" is a smiling face) and a border value a browser would not
  accept whole or that inherits from the parent, and layout that draws one text over another (a
  negative margin on inline text or at a block's top or bottom, vertical padding on inline text, padding on it over a background, a border on it wider than a hairline, a height outside table parts and pictures, a line height or font outside the
  bounds above). Those are bounds, not a layout engine. What they do not catch is a stated residual of the check, listed in the reader's module docstring (a line height computed from a smaller font than the text it holds, a block overflowing its table cell, a list item drawn over its number, text at the bounds' edge, a combining mark on a space drawn as a stroke, text moved far to the right, off a printed page, text shifted by up to 6pt over the line above or below). ADR 0005's renderer cross-check, which draws each page and compares, is what secures the import.

The mapping lacks three EMA codes that one pinned label uses (Jentadueto: Pregnancy,
Breast-feeding and Fertility under 4.6); the check reports them as `unmapped-code`, for
information.

## Known limits

- A statement matched exactly is `used` even when an optional sentence beside it, which the
  label also carries, has a typo: the optional sentence is simply taken as absent. So is a
  statement of alternatives once one alternative matches: a second alternative the label also
  uses (one from each group of `lactation.1`) is not compared when it differs.
- An alternative's form keeps its short sibling segments optional ("Do not refrigerate <or>
  <freeze>."), so it also matches a combination the template does not mean ("Do not or
  freeze."), which is reported `used`: a stated residual of the check.
- A resemblance is a proposal. A short statement (fewer than six words) resembles text only
  when every word it requires is there, so a changed word in "No data are available." is not
  found; a heavily reworded statement is not found either.
- Pilot data from 2024 and 2025, checked against template 10.4.
- The similarity threshold proposes deviations; it can miss a heavily reworded statement and
  can flag a legitimate product-specific variant. Both are for a person to judge.
- A statement with fewer than 12 characters of required literal text, and none of its
  alternatives with as many, is `not-checkable` (`too-little-text`: "<None.>" in 6.1, the shelf
  lives in 6.3).
- Only the SmPC in English. Annex II, the labelling and the package leaflet are not checked.
- The check does not record which Appendix I option a label uses when the EMA's own entry does
  not balance its brackets (four entries); those are `not-checkable` (`unbalanced-brackets`).
