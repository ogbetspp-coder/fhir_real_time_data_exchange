# Design note: finding an SmPC's sections

- Status: Built (`smpc-structure/1.3.0`, `zone-a/src/zone_a/structure.py`); run on the EMA's QRD
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
   count only inside their own section. The mapping's optional named subsections (mapping 1.4.0:
   Pregnancy, Paediatric population, Mechanism of action, ...) are not looked for: their lines
   stay text of their section, since a label may repeat one inside a section (a "Mechanism of
   action" under each substance of a combination) and the EMA's own ePIs code them in some
   labels and not in others.
2. **Candidates, never guesses.** A line that starts with a section's number but goes on
   otherwise, or a paragraph in a heading style that is no QRD heading, is shown to the person.
   The section stays `missing` until the person names its heading (`--assign smpc.4.4=57`). A
   named heading counts for the scan too: the named subsections after it are found inside it
   (`smpc-structure/1.3.0`), so a person names a section once, not each subsection under it.
3. **Text by position.** Every paragraph belongs to the last heading before it; nothing is
   moved, merged or dropped. The SmPC ends at "ANNEX II", the registry's own end.
4. **One status per section:** `mapped`, `assigned`, `missing`, `absent` (optional),
   `duplicate`, `order` or `no-code` (none since mapping 1.4.0, which codes sections 2.1, 2.2,
   11 and 12), each with the mapping's EMA code. The structure is `ready`
   when nothing is left for a person.

## Run it

```
cd zone-a
.uv-bootstrap/bin/uv run --frozen python scripts/structure_label.py LABEL.docx --out structure.json
```

## On the EMA's own template

The QRD template 10.4 structures to 30 sections mapped (again with `smpc-structure/1.2.0` and
mapping 1.4.0, 2026-10-06; 2.1, 2.2, 11 and 12 `absent`). Two (6.5 and 6.6) are `missing` with
their headings as candidates, because the template writes its optional words in angle brackets,
which no label does; assigning them makes it `ready`.

## Next

- Build the ePI's section tree (FHIR Composition sections with these codes) from a `ready`
  structure, held to the fidelity check against the Word text.
- The labelling, from its own template. The package leaflet is found by the same statuses
  (`zone_a.structure.find`), with its own headings: `docs/design/pl-structure.md`.
