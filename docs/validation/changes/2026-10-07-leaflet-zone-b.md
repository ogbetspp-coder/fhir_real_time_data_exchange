# Recorded change: the package leaflet through Zone B, leaflet mapping 1.1.0, certified Word importer 1.2.0, `dev.khs.fhir.epi#0.6.0`, 2026-10-07

_ADR 0006 owner decision 8 (`docs/design/pl-structure.md`, "Zone B"). No contract changes; the
reason is under "Contracts" below._

**What changed.**

1. **The leaflet's mapping** (`fhir/mappings/cap-pl-en.json`, `mappingVersion` 1.0.0 → 1.1.0):
   - a `display` on the 13 rules whose title is not the EMA's own display for its code, each the
     display the pinned EUePI package's CodeSystem 200000029659 gives
     (`2. What you need to know before you [take] [use] X`, `Children [and adolescents]`, ...); the
     validator refuses a coding with any other display. `test/official/profile-slots.test.ts`
     now reads that code system and holds every rule of both mappings to it;
   - three keys renamed, their hyphens removed: `pl.2.do-not-take` → `pl.2.donottake`,
     `pl.3.too-much` → `pl.3.toomuch`, `pl.6.other-sources` → `pl.6.othersources`. The contract's
     `SourceKey` (`^[a-z0-9]+(?:\.[a-z0-9]+)*$`) has no hyphen, so no submission could carry a
     leaflet: "Do not take X" is a required section. Removing the hyphen is mechanical, and keeps
     the contract as it is (see "Contracts").

   The leaflet's QRD registry (`qrd/registry/cap-pl-en-10.4.json`) is built from the mapping and
   was generated again: its `mappingVersion` and the three keys changed, nothing else. Its
   `registryVersion` stays 1.3.0: the SmPC's registry, which shares the version, is unchanged, and
   every recompute names the registry's and the mapping's versions together, so the leaflet's
   registry is named by the pair.

2. **Our package, `dev.khs.fhir.epi#0.6.0`** (`scripts/fhir/generate-artifacts.ts`): the
   CodeSystem `https://khs.dev/fhir/CodeSystem/canonical-pl-sections` (the URL the leaflet
   mapping names) and its value set, generated as the SmPC's are, versioned with the mapping
   (1.1.0); the ConceptMap `canonical-to-ema-cap-pl-en`, every rule `equivalent`, the custom
   subsection slot `noMap`, its target scope the EMA's leaflet section codes
   (`EUepiplqrdcodesVs`); and the document type code system gains `pl`, "Package Leaflet". The
   SmPC's code system, value set and ConceptMap are byte for byte as before; the resources
   versioned with the package move to 0.6.0. Re-pinned in `Dockerfile.validator` and
   `fhir/standards.lock.json`.

3. **Zone B takes the manifest by the source's document type** (`src/fhir/mapping.ts`):
   `loadEmaMappings` loads both manifests; `mappingFor` takes the one whose document the source's
   `Composition.type` names, in our document type code system (`smpc`, `pl`) or the EMA's
   (`100000155532`, `100000155538`, in either form of its URL), and none where it names none, two,
   or one no manifest maps. The worker loads both (`src/app.ts`); `runPipeline` takes one manifest
   (the caller's choice, as every test and script passes) or several, of which the source picks
   one, before the gate from the record the submission says it carries and again after it from the
   record the gate passed; a source with no manifest is refused (`crosswalk-refused`). The EMA
   document type codes are read from the pinned package's CodeSystem 100000155531, and
   `test/official/profile-slots.test.ts` holds them to it and to the code each EUEpiComposition
   profile fixes.

4. **The crosswalk and the preflights** (`src/fhir/transform.ts`, `src/fhir/preflight.ts`):
   - the EMA Composition's `type` is the mapping's document's EMA code and display (the SmPC's
     output is byte for byte as before);
   - the crosswalk refuses a source whose `Composition.type` names another document than its
     manifest's (a source that names none is held to the manifest by its sections, as before);
   - the EMA preflight refuses an EMA Composition not typed as its manifest's document;
   - the Type 1 preflight lets a package leaflet's record have no RegulatedAuthorization: its
     QRD template has no place for an authorisation number. An SmPC's record still needs one.

5. **The certified Word importer, 1.1.1 → 1.2.0** (`src/certified-word/`, locked): it carries
   `document: "pl"`, refused until now at binding (`document-not-carried`, removed). The
   leaflet's product check (docs/design/certified-word-import.md, "The leaflet"):
   - **the name** is the structure's `name` (`zone_a.leaflet`: the same in every section 1 line of
     the leaflet, its list of sections included), which must stand for X in section 1's heading as
     the label writes it, the mapping's form exactly, character for character, and must not end
     in punctuation (`name-not-in-section-1`);
   - **the holder** is the first line of section 6's holder section, exactly
     (`holder-not-in-section-6`, `section-6-begins-with-no-text`);
   - **no EU number**: a leaflet states none, so a person confirms none
     (`eu-numbers-not-in-leaflet`), and the record has no RegulatedAuthorization.

   An SmPC's request with no EU number is still refused as `request-shape`, now by the importer
   rather than the request's schema (not a contract), which a leaflet's passes. The decisions name where each value stands (`pl.1`,
   `pl.6.holder`), and the terminology service the leaflet's mapping (`cap-pl-en`).

6. **Fixtures.**
   - The synthetic leaflet product `synthetic-exampline` (`src/fixtures/synthetic-products.ts`,
     not among the demonstration or smoke products), its EU authorisation number
     `EU/1/24/9999/001` on its RegulatedAuthorization and `EU/1/24/9999` on its product, every
     narrative marked; built with the leaflet's mapping by the SmPC's builder, which now takes
     the document type from the mapping (the SmPC fixtures are byte for byte as before).
   - The certified Word leaflet (`zone-a/scripts/certified_word_fixtures.py`): its headings name
     the product ("1. What Synthetic Exampline is and what it is used for"), its holder section
     begins with the holder, and it is confirmed with no EU number. Regenerated with the
     recompute's results; the SmPC labels' are unchanged.
   - The importer's vectors (`test/fixtures/certified-word/vectors.json`): the leaflet is now
     imported, and three leaflet refusals are added (a retyped name, a retyped holder, a confirmed
     number). Every SmPC import's submission, page text and report hash moved, because the
     importer's version is in the extractor token; their outcomes did not.

7. **Official validation** (`scripts/ci/emit-validation-set.ts`): two more cases, each through
   the leaflet's mapping and with its Provenance: the leaflet product's Type 2 record with every
   optional section, as its drawn submission carries it, and the certified Word leaflet's Type 1
   record, its titles as written. The 24 new warnings are the SmPC cases' own, each allowlisted
   with its SmPC twin's message and reason (22 `dom-6`, 2 UCUM `mg`; no other kind).

**Contracts.** None changed, checked first:

- `document: "pl"` is already in `RecomputeRequest`'s enum (`ingestion-provenance` 3.0.0), and in
  the importer's result schema; no enum gains a member.
- `Composition.type` and the section codes are not in the contract (`CanonicalBundle` is loose).
- The section keys are: `SourceKey` has no hyphen. Widening it would be a major of
  `CanonicalSubmission` (taking the 4.0.0 the renderer and withheld designs reserve), of the
  fidelity report, the approval contracts and `query-tools`, and of the run manifest; renaming the
  leaflet's three keys is a mapping change. The reservation of `CanonicalSubmission` 4.0.0 stands
  where it is stated.
- The run manifest's `standards.mappingVersion` of a leaflet run is the leaflet mapping's
  version; its `validation.profiles` names the leaflet's profiles, which tells the two mappings
  apart. Lineage names `cap-pl-en#1.1.0`.

**Found, not fixed: two SmPC sections no submission can carry.** Mapping 1.4.0's
`smpc.4.6.breast-feeding` and `smpc.5.2.pk-pd` (both optional) are outside `SourceKey`: a
submission holding either is refused at the contract (`createSyntheticSubmission` with every
optional section shows it). It fails closed. `test/leaflet.test.ts` lists the two so the list
cannot grow unseen; the fix is either a contract major (`SourceKey` with a hyphen) or the two keys
renamed (SmPC mapping 1.5.0), for the owner to choose.

**Why.** ADR 0006 decision 8 put the leaflet in scope with the SmPC; `zone_a.leaflet` finds its
sections, and Zone B carried the SmPC only.

**Impact assessment (step 0).**

- The worker (`src/pipeline.ts`, `src/app.ts`): loads both mappings and takes one per run. The
  SmPC's output, every id and every hash of it, is unchanged; `test/` passes unchanged but for
  the tests of what changed.
- The signer and the query service load the SmPC mapping only: a leaflet's review is refused by
  the crosswalk (another document than the mapping's), and the query service does not resolve a
  leaflet's sections. Neither persists or serves a leaflet from a certified Word source, which
  runs dry only.
- Zone A: the recompute's versions name mapping 1.1.0 for a leaflet, so a leaflet request made
  with 1.0.0 is refused (`versions`); no leaflet submission was ever made, as the importer refused
  every one. `zone_a.leaflet` and `zone_a.word_epi` are unchanged; three keys in its tests.
- The agent: unaffected (`query-tools` unchanged).
- Evidence: the contract fixtures, the fidelity and authority vectors and the frozen run
  manifests are unchanged; the certified Word vectors moved as item 6 says.

**Steps 1–7.** 1: the mapping 1.1.0, the package 0.6.0, the importer 1.2.0 (no contract
version). 2: `uv run --frozen python scripts/generate_qrd_registry.py` and
`scripts/certified_word_fixtures.py` in `zone-a/`, `npm run artifacts:generate`,
`npm run certified-word:vectors`, `npm run certified-word:lock`; `npm run contracts:check` reports
every generated file current (no contract, contract fixture or fidelity vector changed). 3: every
changed vector reviewed: the leaflet's import, its three refusals, and the SmPC imports' hashes
(their outcomes unchanged). 4: tests below. 5: ADR 0002 and ADR 0006 amended. 6: UR-02, UR-46 and
UR-57. 7: a certified Word SmPC submission made by importer 1.1.1 is refused by the gate (another
importer version) and must be made again; none was persisted (dry run only).

**Tests.** `test/leaflet.test.ts` (the mapping a source takes; a Type 2 leaflet through the
crosswalk, with and without its optional sections; through the worker's pipeline from the fixture
route and as an approved drawn submission; the refusals; the Type 1 rule; the section keys
against `SourceKey`); `test/certified-word/import.test.ts` ("a package leaflet");
`test/certified-word/recompute.test.ts` (a leaflet through the worker's pipeline, the gate making
it again from its upload, with the real Python in CI's Zone A job); `test/ci/validation-set.test.ts`;
`test/synthetic-only.test.ts` (the leaflet product); `test/official/profile-slots.test.ts` (the
document types and every display, from the package).

**Measured** (2026-10-07, this branch): `npm run validate:official`: 0 errors across 70 resources,
65 warnings, every one allowlisted, none stale (before: 0 errors across 57 resources, 41
warnings). `npm run test:official`: 17 tests passed (13 before).

**Blast radius.** Synthetic content only. A leaflet from a certified Word source runs dry only, as
an SmPC does. A source that names another document than its manifest's, or none where the worker
has several, is refused where it was carried before; no route produced one.

**Approval (step 8).** Not obtained: the author and the releaser are the same identity (see
"Release criteria").
