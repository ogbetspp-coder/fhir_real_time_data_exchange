# Design note: finding a package leaflet's sections

- Status: Built (`pl-structure/1.0.0`, `zone-a/src/zone_a/leaflet.py`); run on the EMA's QRD
  template, on synthetic leaflets and, counts only, on the EMA's published Word product
  information (internal corpus). No company label is in the repository. Zone B carries a
  certified Word leaflet to the EMA's leaflet ePI Bundle since 2026-10-07, in a dry run only
  ("Zone B", below).
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
- **Built** (`word-epi/1.5.0`, `docx-reader/1.33.0`, 2026-10-08): in the 69 files whose every
  leaflet is ready (98 leaflets), 1,526 of 1,666 sections are carried, every one in 14 files. The
  rest are refused section by section on the builder's closed lists (formatting 38, list-level 25,
  tab 21, picture 20, list-label 14, anchored-object 12, heading-in-table 9, underline 1); one
  file is refused whole.

## Zone B

A leaflet from a certified Word source (its Type 1 record, its titles as written) is transformed
to an EMA ePI Bundle as an SmPC is, by its own manifest, in a dry run
(`docs/validation/changes/2026-10-07-leaflet-zone-b.md`):

- **The manifest, by the document type.** The worker loads both manifests and takes, for each
  source, the one its `Composition.type` names: our document type `smpc` or `pl`, or the EMA's
  (CodeSystem 100000155531 of the pinned EUePI package: `100000155532` and `100000155538`, read
  from the package by `test/official/profile-slots.test.ts`; `src/fhir/mapping.ts`,
  `mappingFor`). There a source that names none, two, or one no manifest maps is refused. A caller
  that gives one manifest (the signer and `scripts/dev/run-pipeline.ts`, the SmPC's) maps a source
  that names none by that manifest's sections, as before; the crosswalk refuses, whoever calls it,
  a source that names another document than its manifest's.
- **The EMA document.** Its Composition is typed `100000155538` "Package Leaflet" and claims the
  four leaflet profiles (`EUEpiComposition`, `EUEpiCompositionPackageLeaflet`,
  `EUEpiCompositionCAP`, `EUQRD-CAP-template-new-Package-Leaflet-en`); the EMA preflight holds
  both. The List names it, as an SmPC's does.
- **The canonical sections** are a code system of our package,
  `https://khs.dev/fhir/CodeSystem/canonical-pl-sections`, with a ConceptMap to the EMA's codes,
  generated from the mapping as the SmPC's are. The mapping (1.1.0) carries the EMA's display
  where it is not the title, and its keys hold no hyphen (`pl.2.donottake`, `pl.3.toomuch`,
  `pl.6.othersources`), since a contract `SourceKey` has none.
- **Titles, as written only.** A certified Word leaflet's are its heading lines as written, the
  medicine's name for X included (ADR 0006 decision 4, D6). Under the template's rule a leaflet
  would be published with the mapping's titles, which write X and keep the template's choices
  ("Do not take use X", "This leaflet was last revised in {MM/YYYY}{month YYYY}."): text the
  label never says. So the crosswalk and the EMA preflight refuse a leaflet under the template's
  rule (`leaflet-titles-not-carried`): a Type 2 leaflet, whose titles take that rule, is not
  carried. The synthetic Type 2 leaflet (`synthetic-exampline`) is that refusal's fixture.
- **Required sections.** Every section the profile requires must be there (the root, the six
  numbered sections and ten named ones), each coded; an optional one would be carried where the
  record has it, which a certified Word record does not yet (Zone A finds only the required ones,
  "What it does", 4).
- **The product, for a certified Word leaflet** (`src/certified-word/import.ts`): its name is the
  structure's `name`, which must stand for X in section 1's heading as the label writes it, the
  mapping's form exactly; its holder is the first line of section 6's holder section, exactly; and
  it states no EU authorisation number, so a person confirms none and its Type 1 record has no
  RegulatedAuthorization (the Type 1 preflight allows that for a leaflet only). Each refusal is
  closed: `name-not-in-section-1`, `holder-not-in-section-6`, `section-6-begins-with-no-text`,
  `eu-numbers-not-in-leaflet`.
- **Validated.** The certified Word leaflet's record and its EMA output, with its Provenance, pass
  the pinned official validator with no error, every warning the SmPC's own kind with the SmPC's
  reason.
- **No leaflet persists.** The query service and the signer load the SmPC's manifest only, so
  until both read a leaflet the worker refuses a leaflet's run that is not a dry run, whatever
  approvals say, before anything is read (`leaflet-not-readable`). A dry run still runs. As a
  defence, the query service answers `document-not-found` for a stored document whose type is not
  its manifest's, never a partial answer (it used to answer `verify_quote` with `no-match` over no
  section and list the product in `find_product` with no sections). The signer's crosswalk refuses
  a leaflet's review. The StructureMap twin is the SmPC's alone
  (`docs/design/structuremap-twin.md`, "The package leaflet: not twinned").

**Residuals, each to close before a leaflet persists:**

- **Two Lists, one code.** Each document has its own EMA List, coded `100000155539` "Combined File
  of all Documents" (`src/fhir/transform.ts`), so a product's SmPC and leaflet would be two Lists
  each claiming to be the whole set. The fix: one List per product, indexing every document of it,
  its identity the product's, not a document's.
- **The run manifest's mapping version.** `standards.mappingVersion` records the version alone,
  and the leaflet's 1.1.0 could be read as the SmPC mapping's old 1.1.0; today only the manifest's
  `validation.profiles` tells them apart. The fix: name the mapping in the manifest, a change of
  its contract.
- **The holder under the combined heading.** Under "Marketing Authorisation Holder and
  Manufacturer" the importer takes the section's first line as the holder. The annotated template
  allows that heading only where the two are one company, so a label that breaks the rule, its
  manufacturer first, would pass the manufacturer as the holder a person confirmed. No narrow rule
  on the leaflet alone tells the two apart; P5's cross-check against the product's confirmed
  SmPC holder (section 7) closes it.

## Next

- The leaflet's own refusals. A list whose level is Word 6 numbering (`w:legacy`), as the template's
  own dash list is, was the commonest (`list-label`, 85 sections). Word's drawing of it is now on
  record (`label-docx-reader/corpus/numbering-cases/legacy-drawn*`, Word 16.113.4): the text starts
  max(legacyIndent, the label's advance + legacySpace) after the label, or further where the
  paragraph hangs further, which can be no gap at all ("10.5 mg"). The reader reports `tab` only
  where that gap is at least the space Word draws after a label and the label is as Word was
  recorded drawing it, and `legacy`, which the builder still refuses, elsewhere
  (`docx-reader/1.33.0`; `docs/validation/changes/2026-10-08-docx-reader-1-33-0-word-6-lists.md`).
  14 sections are still refused for a list label. Then formatting (38), nested lists (25) and a tab
  (21).
- Candidates for a named section a person must confirm (a line in its section that starts with
  the template's wording), for the review screen.
- The query service and the signer, by the record's manifest.
- Who the leaflet is for (section 6: what it contains, the holder) for the product proposal.
- The optional named sections, once a rule tells a repeated line from a heading.
- Annex II, the labelling and other languages, each from its own template.
