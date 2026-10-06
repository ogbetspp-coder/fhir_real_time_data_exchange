# Design note: finding an SmPC's sections

- Status: Built (`smpc-structure/1.0.6`, `zone-a/src/zone_a/structure.py`); run on the EMA's QRD
  template and on synthetic labels. No company label is in the repository.
- Date: 2026-10-05
- Related: the UI proposal (Claude Docs, "Label Intake — UI proposal"), `docs/design/qrd-registry.md`
  (the headings), `fhir/mappings/cap-smpc-en.json` (the tree and the EMA codes)

## Why

The review screen shows a label's Word text beside its sections, so that a person confirms the
structure before it becomes an ePI. This step finds those sections. It finds only what the text
shows exactly, and leaves every judgement to the person, as the reader does.

## What it does

1. **Headings, exactly.** A paragraph is a section's heading only when its line (Word's list
   label and its text) is one of the forms the QRD registry allows for that section. The named
   subsections (Posology, Method of administration, Reporting of suspected adverse reactions)
   count only inside their own section.
2. **Candidates, never guesses.** A line that starts with a section's number but goes on
   otherwise, or a paragraph in a heading style that is no QRD heading, is shown to the person.
   The section stays `missing` until the person names its heading (`--assign smpc.4.4=57`).
3. **Text by position.** Every paragraph belongs to the last heading before it; nothing is
   moved, merged or dropped. The SmPC ends at "ANNEX II", the registry's own end.
4. **One status per section:** `mapped`, `assigned`, `missing`, `absent` (optional),
   `duplicate`, `order` or `no-code`, each with the mapping's EMA code. The structure is `ready`
   when nothing is left for a person.

## Run it

```
cd zone-a
.uv-bootstrap/bin/uv run --frozen python scripts/structure_label.py LABEL.docx --out structure.json
```

## On the EMA's own template

The QRD template 10.4 structures to 30 sections mapped. Two (6.5 and 6.6) are `missing` with
their headings as candidates, because the template writes its optional words in angle brackets,
which no label does; assigning them makes it `ready`.

## Next

- Build the ePI's section tree (FHIR Composition sections with these codes) from a `ready`
  structure, held to the fidelity check against the Word text.
- The package leaflet and the labelling, from their own templates.
