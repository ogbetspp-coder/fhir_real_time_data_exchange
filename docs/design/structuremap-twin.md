# Design note: the StructureMap twin

- Status: Built. Run in CI's Official validation job on every pull request
  (`npm run test:official`).
- Date: 2026-10-06
- Related: `src/fhir/transform.ts` (the crosswalk), `fhir/mappings/cap-smpc-en.json` (the
  manifest), `docs/architecture.md` ("Deterministic data flow")

## What it is

The crosswalk written a second time, in the FHIR mapping language, and executed by the pinned HL7
validator's own transform engine. It is a differential twin: two implementations of one
transform, run on the same sources, must give the same document Bundle.

- `fhir/maps/type2-to-ema-cap-smpc-en.map`: the FML source.
- `fhir/maps/StructureMap-type2-to-ema-cap-smpc-en.json`: the StructureMap the pinned validator
  (6.10.4) compiles from it, offline (`npm run map:compile`, Java 21). The script sets `id` and
  `version`, which the validator's FML metadata does not carry. `scripts/fhir/generate-artifacts.ts`
  copies it into `fhir/generated/` and the repository's own package, `dev.khs.fhir.epi`.
- `fhir/maps/TwinRunner.java`: a short program that builds the validator's engine with no
  terminology server, loads the sidecar's five packages and transforms each source. It runs as a
  single source file on the pinned, checksummed `validator_cli.jar`, so there is no compiled class
  to pin. It refuses any other validator version.
- `test/official/structuremap-twin.test.ts`: compiles the FML again and requires the committed
  StructureMap; runs both transforms on every case; fails on any difference but the one reviewed
  below.

## What it compares

The document Bundle, as canonical JSON (sorted keys), once each output has had removed what the
map leaves to the implementation: the Bundle's id and identifier value, every entry's fullUrl and
resource id, the Composition's identifier values, every section's id and every
`Reference.reference`. The List is not part of the map.

The cases, 168 in all (2026-10-06):

- 156 fixtures the crosswalk accepts: the four synthetic products at both versions, with the
  mandatory sections only and with every optional section (16); the headings the template permits
  without their optional wording, one it does not permit, and a missing one (2); Composition
  elements the crosswalk keeps or replaces (1); the authority import's Type 1 record, with and
  without optional subsections of 4.6 (2); every narrative the fidelity scanner accepts in its
  vectors (94) and every narrative the importer's T writes in its vectors (41), each on the root
  section.
- 7 sources both must refuse: not a document, a first entry that is not a Composition, a key the
  mapping leaves unmapped, an unknown key, two canonical codes, an uncoded section with narrative,
  and narrative that is not well-formed.
- The 5 EMA ePIs pinned in `labels/ema-epi/sources/`, their coded sections recoded to canonical
  keys and their uncoded sections left out. The crosswalk refuses all five (its fidelity scanner
  forbids their style attributes, among other reasons), so there is nothing to compare; the twin
  must refuse each too, or carry every section's narrative byte for byte under the mapped code.
  It carries Brukinsa and both Imatinib Teva labels whole (32 sections each) and refuses
  Jentadueto and Nuvaxovid by its narrative check.

## The package leaflet: not twinned

The crosswalk maps the package leaflet since 2026-10-07 (`docs/design/pl-structure.md`, "Zone
B"), by its own manifest. The twin was not extended to it. A leaflet's twin would be this map again
with the leaflet's data written into it: its 27 keys in the `check` list, its ConceptMap, its
section-code value set, its document type and its four profiles; and, since 13 of its titles are
not the EMA's displays (the displays bracket the template's choices), a heading per code where
this map writes the display. That restates the manifest a second time in FML; it is not a second
implementation of anything the leaflet adds. The leaflet adds no transform logic. The code it runs
is the SmPC's, which the twin holds on every case above (a coding's display other than its title
included: 6.5 and 6.6), and what differs is data, which is held elsewhere:

- `test/official/profile-slots.test.ts` holds the leaflet's manifest to the EMA's profile slot by
  slot, every display to the EMA's section code system and its document type to the package;
- the official validator checks the leaflet's EMA output against the leaflet's template profile,
  whose closed slicing fixes every code at its place, and `EUEpiCompositionPackageLeaflet`, which
  fixes the type (`scripts/ci/emit-validation-set.ts`, two leaflet cases);
- `test/leaflet.test.ts` and the EMA preflight hold the tree, the order and the type.

Reconsider when the crosswalk gains logic that only a leaflet runs.

## Where it fails closed

A `check` clause aborts the transform on a source that is not a document, a first entry that is
not a Composition, a section without exactly one canonical code, a code outside the 59 the
manifest maps (the list is written out in the map; the custom subsection keys are not on it), an
uncoded section with narrative or subsections, and narrative without a div.

## What it cannot express, and leaves to TypeScript and validation

- The ids. They are SHA-256-derived (`src/lib/hash.ts` `stableUuid`), and FML has no hash. The
  map documents the Bundle identifier's derivation and sets no value.
- The rewrite of every `Reference.reference` to the new fullUrls: FML has no generic walk.
- The allow-lists: an element of a Bundle, an entry, a `meta` or a section that the crosswalk does
  not carry, `contained` and `implicitRules`.
- The manifest's tree: a mandatory section missing, a section under the wrong parent or out of
  order, two sections with one key. The map keeps the source's order and nesting.
- The narrative's visibility (the fidelity scanner's reading), the language checks, the mapping
  decisions and their hashes, and the List.

## What running it showed about the validator's engine (6.10.4, measured 2026-10-06)

- The CLI's `compile` exits 0 when it could not read the map; `npm run map:compile` reads the
  file it writes instead. Its `transform` will not run without a terminology server.
- `translate()` gives a code and system but no display. For a code the ConceptMap does not list
  it gives nothing and raises no error of its own; for a `noMap` key it throws ("found no
  translation"). The map takes the display from the EMA's own value set of SmPC section codes
  (`c()` on `EUepismpcqrdcodesVs`, expanded offline), and checks the code against its list before
  it translates.
- A `check` on `src.entry first` is applied to every entry before `first` picks one: a check
  that the entry holds a Composition refused the synthetic source, whose first entry does. The
  map checks the first entry on the Bundle instead.
- Its StructureMap checker reports `translate` and `c` as "not checked yet" (two warnings,
  reviewed into `scripts/ci/official-validation-warnings.json`), and rejected `evaluate` with two
  parameters, which the engine runs; the map writes the heading as a FHIRPath expression instead.
- The element model drops a narrative div its XHTML parser cannot read, and the transform goes
  on without it. It dropped 7 of Jentadueto's 101 divs and Nuvaxovid's root div. The map's
  narrative check turns that into a refusal.

The one reviewed difference: the fidelity vector `accepts-ascii-whitespace-in-tags` writes ASCII
whitespace inside its tags (`<p\t>`, `</p >`), which XML allows and the fidelity scanner reads.
The validator's XHTML parser does not, so the twin refuses that source where the crosswalk
carries it. Whether the official validator accepts such a div in a persisted resource has not
been checked.
