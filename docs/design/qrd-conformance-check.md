# Design note: the QRD conformance check

- Status: Built and run on three EMA-published English SmPCs
- Date: 2026-09-23
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

Three summaries of product characteristics, as the EMA itself publishes them through its ePI
API (`labels/ema-epi/`, pinned by SHA-256 in `labels/ema-epi/sources.lock.json`):

| Product    | Kind                                          | Composition date |
| ---------- | --------------------------------------------- | ---------------- |
| Jentadueto | small molecule (linagliptin / metformin)      | 2024-04-22       |
| Nuvaxovid  | biological medicine (vaccine), black triangle | 2024-06-14       |
| Brukinsa   | small molecule (zanubrutinib), black triangle | 2024-04-08       |

Only five products on the API have an English SmPC, all from the EMA's 2023–2024 pilot, which
the portal describes as "for pilot purposes only, and may not be up-to-date". A difference from
template 10.4 may therefore be a difference of template version; the results say which version
they were checked against. EPAR PDFs and the EMA's tracked-changes Word files are not used:
`AGENTS.md` does not allow them yet.

## The reader

`zone-a/src/zone_a/epi/reader.py` reads an ePI Bundle into sections of paragraphs, with the
Word reader's `Paragraph` and `Mark` model. It is written to the Word reader's rule: the text a
browser shows, exactly, or a refusal with a reason; its module docstring lists every rule. It is
not the fidelity scanner, which is the contract for narrative this repository publishes and
rightly refuses the EMA's divs (inline CSS on nearly every element).

Each section is read on its own, so a section the reader cannot vouch for is refused without
losing the rest. CSS is checked against a closed list: layout properties are ignored, text
colour, background and tiny text are marked, and anything else (`display`, `visibility:
hidden`, an unknown property) refuses the section. Word comment markup refuses the section,
because the comment's text would otherwise read as label text.

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
  consecutive paragraphs. Struck-through and faint text never counts as a match.
- **Deviations.** A statement that does not match but resembles a paragraph of its section
  (difflib's word-level ratio of at least 0.85, against the closest choice of its optional
  segments) is reported with the word-level differences,
  up to the end of the sentence; text in the place of a fill-in is not a difference, and
  struck or faint words show as "[struck or faint text]". Text that an exact match of a sibling
  statement explains is not compared again; the rest of the paragraph is. 0.85 is a proposal
  threshold, set on these three labels; a lower one would find more and be wrong more often.
- **Refused parts.** A statement not found in a section with a part the reader refused is
  `not-checked`, not `absent`. A resemblance in the readable part is still a deviation.
- **Appendices.** Appendix I's statements against 4.6, Appendix II's frequency convention and
  system organ classes against 4.8, Appendix III's storage statements against 6.4.

`labels/ema-epi/checks/<product>.json` holds each result; `zone-a/scripts/check_labels.py`
writes them and a test compares them with a fresh run.

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
- **Not valid FHIR (all three).** The API serialises `resourceType`, `language` and `status` of
  the Composition as the number 0, and names `http://www.test.com` as the author's identifier
  system.
- **Wording that differs from the template.**
  - All three link to `http://www.ema.europa.eu`; template 10.4 has
    `https://www.ema.europa.eu`.
  - Nuvaxovid adds "and include batch/Lot number if available" to the reporting statement in
    4.8. That is common for vaccines, and a person should confirm it.
  - Nuvaxovid writes the paediatric deferral statement in 5.1 with a comma where the template
    has brackets.
  - Brukinsa writes "no or negligible influence in the ability to drive and use machines"; the
    template has "on".
- **Formatting.** Blue text (an email address and the EMA link) and grey table shading in
  Brukinsa, besides the template's intended grey shading of "the national reporting system
  listed in Appendix V" in all three. Red, yellow and other colours in the source fall only on
  pictures or spaces, which show no text differently, and nearly black text (`#0d0d0d`) reads
  as black; neither is reported.

The mapping lacks three EMA codes the labels use (Pregnancy, Breast-feeding and Fertility under
4.6); the check reports them as `unmapped-code`, for information.

## Known limits

- Pilot data from 2024, checked against template 10.4.
- The similarity threshold proposes deviations; it can miss a heavily reworded statement and
  can flag a legitimate product-specific variant. Both are for a person to judge.
- A statement with fewer than 12 characters of required literal text is not checked.
- Only the SmPC in English. Annex II, the labelling and the package leaflet are not checked.
- The check does not yet record which Appendix I option a label uses when the EMA's own entry
  does not balance its brackets (four entries); those are reported as not checkable.
