# Design note: finding a package leaflet's sections

- Status: Built (`pl-structure/1.0.0`, `zone-a/src/zone_a/leaflet.py`); run on the EMA's QRD
  template, on synthetic leaflets and, counts only, on the EMA's published Word product
  information (internal corpus). No company label is in the repository.
- Date: 2026-10-06
- Related: `docs/design/smpc-structure.md` (the same step for the SmPC, whose statuses this
  shares), `docs/design/qrd-registry.md` (the headings), `fhir/mappings/cap-pl-en.json` (the tree
  and the EMA codes), ADR 0006 (owner decision 8)

## Why

Every EU ePI is at least an SmPC and a package leaflet. The leaflet is the patient's document and
the second one a Word import must carry. Its template differs from the SmPC's in three ways that
matter for finding its sections: its headings name the medicine ("What X is and what it is used
for"), it lists its own six sections before giving them, and one Word file of the product
information may hold several leaflets, one per presentation.

## What it does

1. **The tree, from the EMA's profile.** `fhir/mappings/cap-pl-en.json` is the profile
   `EUQRD-CAP-template-new-Package-Leaflet-en` of the pinned EUePI 1.0.0 package, written out:
   the root "PACKAGE LEAFLET", six numbered sections, twenty named ones, each with its code, its
   title as the profile writes it and whether the profile requires it, and the custom subsection
   slot as unmapped. CI holds it to the profile slot by slot (`test/official/profile-slots.test.ts`,
   as for the SmPC).
2. **The headings, from the template.** The registry's leaflet file
   (`qrd/registry/cap-pl-en-10.4.json`) holds the root line "B. PACKAGE LEAFLET", the line that
   opens a leaflet, the six numbered headings and, for each named section of the mapping, the one
   template paragraph of its parent section whose title, flattened as the profile flattens it
   (brackets dropped, fill-ins kept), is the mapping's title. All twenty are found once.
3. **The name.** "X" in a heading stands for the medicine's name (the annotated template 10.4).
   A leaflet's name is what stands for X in its section 1 heading; every section 1 line of the
   leaflet (its list of sections included) must name the same one, or no heading holding X is
   recognised and a person names them.
4. **Headings, exactly.** As for the SmPC: a line is a heading only when it is one of the
   template's forms (of two or more optional segments standing together, "<take> <use>", at least
   one), with three readings, each the EMA's own:
   - an optional segment that begins with a comma is written without the space before it
     ("Pregnancy, breast-feeding and fertility"), and a non-breaking hyphen is the hyphen it
     draws;
   - a heading whose title ends in the date completed at printing ("This leaflet was last revised
     in <{MM/YYYY}><{month YYYY}>.") is found by its text before the date, followed by nothing, a
     date ("06/2026", "June 2026") or the placeholder still as written, and at most a full stop;
   - "Marketing Authorisation Holder" alone heads the section the template calls "Marketing
     Authorisation Holder and Manufacturer": the annotated template allows the combined heading
     only where holder and manufacturer are the same, and otherwise has each stated "and
     identified as such".

   Only the named sections the profile requires are looked for (ten of twenty); an optional one
   ("If you take more X than you should") stays text of its section, as for the SmPC.

5. **Lists are not headings.** Lines next to each other (blank paragraphs aside), each starting
   with a section number from 1 to 6, the numbers rising by one, are a list. One that starts with
   section 1's heading is the leaflet's list of its sections: none of its lines is a heading,
   since two sections never stand next to each other with no text between. Any other is numbered
   steps, whose lines are not candidates, but a section's heading among them ("3. Dispose of the
   pen." then "4. Possible side effects") stays a heading.
6. **Where it ends.** At the product information's next annex ("ANNEX IV", in 10 of the corpus's
   files) or the end of the document.
7. **Several leaflets.** Each line that opens a leaflet ("Package leaflet: Information for the
   patient") after the first starts the next one; each is its own ePI, with the root line they
   share as its root heading, as for the SmPCs of one Annex I (owner decision 3). A leaflet opened
   by a line the template does not write ("Package Leaflet: ...") is not split off, so its
   headings are each found twice, which is for a person: a boundary the template's lines do not
   settle is never guessed.

The output has the SmPC structure's shape and statuses, so `zone_a.word_epi` builds a ready
leaflet's sections as it builds an SmPC's.

## Run it

```
cd zone-a
.uv-bootstrap/bin/uv run --frozen python scripts/epi_from_word.py LABEL.docx --document pl --view accepted --out leaflet.json
.uv-bootstrap/bin/uv run --frozen python scripts/scoreboard.py FOLDER --document pl --view accepted --no-drawing
```

## Measured

On the EMA's published Word product information in English (internal corpus, counts only;
2026-10-06, accepted view, no drawing check), cut at "B. PACKAGE LEAFLET":

- **Read:** 193 of 286 files. The reader's refusals are its own (tracked-change edge cases, symbol
  fonts and the like), as for the SmPC cuts.
- **Found:** the 193 hold 289 leaflets; every one has a single name. 111 are ready with no
  person. 9 more are ready once a person names the numbered headings the template does not word
  ("How X is given", "What you need to know before you are given X"); their named sections are
  then found inside them. Of the other 169, a person confirms one named section in 100 of them and
  two in 47. What is left is wording the template does not have: "You must not be given X", "Do
  not take X:", "Pregnancy" alone, "Marketing Authorisation Holder:", or a revision date written
  as "{date}".
- **Built:** in the 68 files whose every leaflet is ready (97 leaflets), 1,406 of 1,649
  sections are carried. The rest are refused section by section on the builder's closed lists
  (list-label 83, formatting 45, tab 35, list-level 24, picture 19, table-shape 15,
  anchored-object 12, heading-in-table 9, underline 1); one file is refused whole.

## Next

- The leaflet's own refusals. The commonest (101 sections of ready leaflets) is a list whose level
  is Word 6 numbering (`w:legacy`), as the template's own dash list is: the reader reports its
  suffix as `legacy`, and the builder refuses it, since ISO 29500 (17.9.5) says the text starts
  exactly `legacySpace` after the label, and these levels set `legacySpace` to 0 with a
  `legacyIndent` of 360. Whether Word draws a gap there is Word's answer to record with the
  reader's Word oracle, not a rule to infer. Then a tab (44), nested lists (26) and shading (35).
- Candidates for a named section a person must confirm (a line in its section that starts with
  the template's wording), for the review screen.
- Zone B: the leaflet's EMA Bundle from its sections, as the SmPC's.
- Who the leaflet is for (section 6: what it contains, the holder) for the product proposal.
- The optional named sections, once a rule tells a repeated line from a heading.
- Annex II, the labelling and other languages, each from its own template.
