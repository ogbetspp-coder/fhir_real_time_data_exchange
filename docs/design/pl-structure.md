# Design note: finding a package leaflet's sections

- Status: Built (`pl-structure/1.0.0`, `zone-a/src/zone_a/leaflet.py`); run on the EMA's QRD
  template, on synthetic leaflets and, counts only, on the EMA's published Word product
  information (internal corpus). No company label is in the repository. Zone B carries a leaflet
  to the EMA's leaflet ePI Bundle since 2026-10-07 ("Zone B", below).
- Date: 2026-10-06; Zone B 2026-10-07
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

## Zone B

A leaflet in the canonical record, a Type 2 graph or a certified Word source's Type 1 record, is
transformed to an EMA ePI Bundle as an SmPC is, by its own manifest
(`docs/validation/changes/2026-10-07-leaflet-zone-b.md`):

- **The manifest, by the document type.** The worker loads both manifests and takes, for each
  source, the one its `Composition.type` names: our document type `smpc` or `pl`, or the EMA's
  (CodeSystem 100000155531 of the pinned EUePI package: `100000155532` and `100000155538`, read
  from the package by `test/official/profile-slots.test.ts`). A source that names none, two, or
  one no manifest maps is refused, and so is one that names another document than the manifest
  it is given (`src/fhir/mapping.ts`, `mappingFor`).
- **The EMA document.** Its Composition is typed `100000155538` "Package Leaflet" and claims the
  four leaflet profiles (`EUEpiComposition`, `EUEpiCompositionPackageLeaflet`,
  `EUEpiCompositionCAP`, `EUQRD-CAP-template-new-Package-Leaflet-en`); the EMA preflight holds
  both. The List names it, as an SmPC's does.
- **The canonical sections** are a code system of our package,
  `https://khs.dev/fhir/CodeSystem/canonical-pl-sections`, with a ConceptMap to the EMA's codes,
  generated from the mapping as the SmPC's are. The mapping (1.1.0) carries the EMA's display
  where it is not the title, and its keys hold no hyphen (`pl.2.donottake`, `pl.3.toomuch`,
  `pl.6.othersources`), since a contract `SourceKey` has none.
- **Titles.** A certified Word leaflet's are its heading lines as written, the medicine's name for
  X included (ADR 0006 decision 4, D6). A Type 2 leaflet's follow the template rule, which keeps a
  source heading only where it is the mapping's title: since that title writes X and both choices
  ("2. What you need to know before you take use X"), a Type 2 leaflet is published with the
  template's headings, never with a name put in for X.
- **Required sections.** Every section the profile requires must be there (the root, the six
  numbered sections and ten named ones), each coded; an optional one is carried where the source
  has it (a Type 2 graph: Zone A finds only the required ones, "What it does", 4).
- **The product, for a certified Word leaflet** (`src/certified-word/import.ts`): its name is the
  structure's `name`, which must stand for X in section 1's heading as the label writes it, the
  mapping's form exactly; its holder is the first line of section 6's holder section, exactly; and
  it states no EU authorisation number, so a person confirms none and its Type 1 record has no
  RegulatedAuthorization (the Type 1 preflight allows that for a leaflet only). Each refusal is
  closed: `name-not-in-section-1`, `holder-not-in-section-6`, `section-6-begins-with-no-text`,
  `eu-numbers-not-in-leaflet`.
- **Validated.** The synthetic leaflet product (`synthetic-exampline`, EU/1/24/9999/001) with every
  optional section, as its drawn submission carries it, and the certified Word leaflet, each with
  its Provenance, pass the pinned official validator with no error, every warning the SmPC's
  own kind with the SmPC's reason.

What Zone B does not do for a leaflet yet: the query service and the signer load the SmPC's
manifest only (the signer's crosswalk refuses a leaflet's review; the query service answers
`section-not-found` for a leaflet's keys), and the StructureMap twin is the SmPC's alone
(`docs/design/structuremap-twin.md`, "The package leaflet: not twinned").

## Next

- The leaflet's own refusals. The commonest (101 sections of ready leaflets) is a list whose level
  is Word 6 numbering (`w:legacy`), as the template's own dash list is: the reader reports its
  suffix as `legacy`, and the builder refuses it, since ISO 29500 (17.9.5) says the text starts
  exactly `legacySpace` after the label, and these levels set `legacySpace` to 0 with a
  `legacyIndent` of 360. Whether Word draws a gap there is Word's answer to record with the
  reader's Word oracle, not a rule to infer. Then a tab (44), nested lists (26) and shading (35).
- Candidates for a named section a person must confirm (a line in its section that starts with
  the template's wording), for the review screen.
- The query service and the signer, by the record's manifest.
- Who the leaflet is for (section 6: what it contains, the holder) for the product proposal.
- The optional named sections, once a rule tells a repeated line from a heading.
- Annex II, the labelling and other languages, each from its own template.
