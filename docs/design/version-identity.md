# Design note: an ePI's versions, and product identity by EU number

- Status: Decided and built (2026-10-06). The owner approved following HL7's version model before
  records accumulate; this note settles which model that is, from the pinned packages.
- Related: ADR 0001, ADR 0002 (its amendment of 2026-10-06), ADR 0006 decision 5,
  `docs/design/epi-mcp-query-service.md`, `zone-a/src/zone_a/product.py`

## The question

Two statements disagreed. A research spike said that `Bundle.identifier` and `Bundle.timestamp`
persist across versions while `Composition.identifier` changes with each version. Base FHIR R5,
as recalled, says the opposite: `Composition.identifier` is version-independent, and a document
Bundle's identifier names one document instance. Both are right, about different HL7 artifacts.

## What the sources say

Every quotation below is exact, read from the pinned files on 2026-10-06 (a run of spaces is
shown as one). The packages are pinned by SHA-256 in `fhir/validator-packages.lock` (R5 core) and
in `fhir/standards.lock.json` and `Dockerfile.validator` (Global ePI, EUePI);
`node scripts/ci/official-validate.mjs --seed-only` verified each tarball's checksum before it was
read.

### Base R5 (`hl7.fhir.r5.core#5.0.0`, StructureDefinitions Bundle and Composition)

| Element                  | Definition                                                                                                                                                                                                        |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Bundle.identifier`      | "A persistent identifier for the bundle that won't change as a bundle is copied from server to server." Comment: "For Documents the .identifier SHALL be populated such that the .identifier is globally unique." |
| `Bundle.timestamp`       | "The date/time that the bundle was assembled - i.e. when the resources were placed in the bundle." Comment, for a document: "the date the document was created."                                                  |
| `Composition.identifier` | Short: "Version-independent identifier for the Composition". Definition: "A version-independent identifier for the Composition. This identifier stays constant as the composition is changed over time."          |
| `Composition.version`    | "An explicitly assigned identifer of a variation of the content in the Composition."                                                                                                                              |
| `Composition.date`       | "The composition editing time, when the composition was last logically changed by the author."                                                                                                                    |

The R5 Documents page has no pinned copy. Fetched on 2026-10-06 from
`https://hl7.org/fhir/R5/documents.html` (SHA-256
`1ef1059daa9142a9fc20f6442d6d5f5168c31710ba6f820d119a1d6baf790d39`), it says:

> The document identifier (mandatory). This is found in Bundle.identifier and is globally unique
> for this instance of the document, and is never re-used, including for other documents derived
> from the same composition
>
> The Composition identifier (optional). This is found in Composition.identifier, and is the same
> for all documents that are derived from this composition

and "Any additional documents derived from the same Composition SHALL have a different
Bundle.identifier."

### HL7 Global ePI (`hl7.fhir.uv.emedicinal-product-info#1.0.0`, Bundle-uv-epi, Composition-uv-epi)

| Element                        | Definition                                                                                                                                                                                                                                                                                                                                                 |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Bundle.identifier` (1..)      | "Persistent identifier that remains the same for all versions of this ePI. The identifier remains the same regardless of any changes to the Bundle and regardless of any changes made to the Resources within the Bundle. This purpose of this identifier is to ensure all versions of an ePI can be collected as a set under a common parent identifier." |
| `Bundle.timestamp` (1..)       | Short: "Persistent original date of approval". Definition: "Original date in which this ePI document received its first authorization. As with the identifier, this date persists across versions."                                                                                                                                                        |
| `Composition.identifier` (1..) | Short: "Unique identifier only for this version of the Composition". Definition: "Unlike the Bundle identifier which persists, the Composition identifier does not persist across versions. Each new version of the Composition receives a new identifier."                                                                                                |
| `Composition.version`          | Short: "An explicitly assigned identifer of a variation of the content in the ePI"                                                                                                                                                                                                                                                                         |
| `Composition.date`             | Short: "Date of last revision for this version of the authorized ePI."                                                                                                                                                                                                                                                                                     |

### EMA EU ePI (`EUePI#1.0.0`, EUEpiBundle, EUEpiComposition, EUEpiList)

| Element                  | Definition                                                                                                                                                                                                                                                                                                                                                                               |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Bundle.identifier`      | Short: "Persistent business identifier for the Bundle." (the definition is base R5's)                                                                                                                                                                                                                                                                                                    |
| `Bundle.timestamp` (1..) | Short: "Persistent original date of approval." Definition: "Original date in which this ePI document received its authorization."                                                                                                                                                                                                                                                        |
| `Composition.identifier` | Short: "Unique identifier only for this version of the Composition." Definition: "Unlike the List identifier which persists, the Composition identifier does not persist across versions. Each new version of the Composition document receives a new identifier."                                                                                                                       |
| `Composition.date`       | Short: "Date of last revision for this version of the authorized EPI document."                                                                                                                                                                                                                                                                                                          |
| `List.identifier` (1..1) | Short: "Persistent identifier for the EPI master list". Definition: "Persistent identifier that remains the same for all versions of an EPI. The identifier remains the same regardless of changes to the Bundle and changes made to the Resources within the Bundle. Purpose of this identifier is to ensure all versions of an EPI can be collected under a common parent identifier." |
| `ext-epi-version-number` | "Version number extension used in List resource." (context List, `valueString`)                                                                                                                                                                                                                                                                                                          |
| `ext-epi-eu-number`      | "EU number extension used in List resource." (context List, `valueIdentifier` 1..1)                                                                                                                                                                                                                                                                                                      |

The EMA's commented sample (pinned, `EMA EPI-23-1022 English commented sample`, non-normative)
disagrees with its own profiles in places: its List identifier is commented "Internal identifier
of an ePI version. In the PLM portal, each ePI version is assigned a unique EPI ID upon creation.";
its document Bundle's timestamp "Date in which this ePI document was last updated."; and its
Composition has no identifier. It is where the EU number's identifier system appears:
`ext-epi-eu-number` with `system` `http://ema.europa.eu/fhir/euNumber/` and `value`
`EU/1/24/9999`, under the comment "If a CAP ePI is linked to an authorisation product in IRIS, the
EMA number and EU number can be added as extension in the List." The package defines no system.

## Decision: the version model

The two HL7 artifacts disagree. Base R5 and its Documents page make `Composition.identifier` the
version-independent one and `Bundle.identifier` one per document instance. Global ePI 1.0.0
reverses both, and EUePI 1.0.0 follows Global ePI on every element both define. The spike quoted
Global ePI; the recollection was base R5.

We follow the profiles the pipeline is validated against: Global ePI for the source, EUePI for
the output (ADR 0001 makes the first the interchange baseline and the second the output contract).
They are the definitions written for ePI, they agree with each other, and the store already keeps
one logical resource per document with its versions as resource versions. Whether a profile may
redefine a base element this way is a question for HL7, not settled here. Neither model is
machine-checkable: these are definitions in prose, not constraints, so no validator can tell a
record that follows one from a record that follows the other.

| Element                                        | Across versions | What the pipeline does                                                                                                                               |
| ---------------------------------------------- | --------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Source `Bundle.identifier`                     | constant        | Every persisted id derives from it (unchanged).                                                                                                      |
| Source `Bundle.timestamp`                      | constant        | The ePI's original approval date; copied to the output.                                                                                              |
| Source `Composition.identifier`                | per version     | Not carried: the output has its own.                                                                                                                 |
| EMA `Bundle.id`, `Bundle.identifier`           | constant        | Derived from the source identifier (unchanged).                                                                                                      |
| EMA `Bundle.timestamp`                         | constant        | Copied from the source.                                                                                                                              |
| EMA `Composition.id`                           | constant        | The store versions one Composition (unchanged).                                                                                                      |
| EMA `Composition.identifier`                   | per version     | New system `https://khs.dev/fhir/identifier/ema-composition-version`; the value is derived from the source identifier and the Composition's content. |
| `Composition.version`                          | per version     | Carried as the source writes it; never minted. No producer writes one today.                                                                         |
| `Composition.date`                             | per version     | Carried from the source.                                                                                                                             |
| EMA `List.id`, `List.identifier`               | constant        | `ema-` and the source identifier (unchanged).                                                                                                        |
| EMA `ext-epi-version-number`                   | per version     | Not written: the sample shows the EMA assigning it, and no source states one.                                                                        |
| Every other persisted id (entries, Provenance) | constant        | Unchanged.                                                                                                                                           |

**The EMA Composition's identifier** (`src/fhir/transform.ts`). Until now it was
`https://khs.dev/fhir/identifier/ema-composition` with the Composition's id as its value: the same
for every version, which the profiles define as one per version. It is now
`stableUuid("ema-composition-version", <source identifier> + ":" + sha256(<the Composition without
its identifier>))`. So any change to the Composition gives a new identifier, and the same content
gives the same one, which keeps a republication idempotent (the deploy's smoke run republishes the
smoke product on every deploy). It is minted, never copied from the source, for the reason the
Bundle's identifier is: a producer can reuse an identifier (the synthetic version 2 reused version
1's until this change). A version that returns to an earlier version's content exactly, date
included, gets that version's identifier again. The StructureMap twin (`fhir/maps/`) writes the new system and, having
no SHA-256, leaves the value to the implementation, as it does every minted id.

**The synthetic sources.** Version 2 of each synthetic label now has its own
`Composition.identifier` (`…-v2`) and its own `Composition.date` (its approval day, 2026-09-20),
and keeps version 1's `Bundle.identifier` and `Bundle.timestamp`. The `-v1` suffix of every
synthetic `Bundle.identifier` stays: it is a frozen literal (`src/fixtures/synthetic-products.ts`,
rule 1), the code already treats the value as constant across versions, and renaming it would move
every EMA id the dev store holds and every Bundle id in the query service's entitlement map.

## Decision: no silent overwrite

The run reads the version of its EMA document Bundle that the target store holds, before it signs
its manifest, and makes it the transaction's precondition on the document Bundle's entry:
`request.ifMatch: W/"<versionId>"`, or `request.ifNoneMatch: "*"` when the store answers 404
(`src/gcp/healthcare.ts`, `src/pipeline.ts`). The signed hash covers it. A 410 or any other refusal
fails the run before anything is written. One precondition guards the whole transaction: every
resource in it has an id derived from the same source identifier as the Bundle's, every run that
writes them writes the Bundle in the same transaction, and a transaction is atomic. `ifNoneExist`
does not fit: it is conditional create (a POST with a server-assigned id), and our ids are fixed by
derivation.

What Google documents (pages fetched 2026-10-06):

- `fhir.executeBundle` reference: "Implements the FHIR standard batch/transaction interaction
  (DSTU2, STU3, R4, R5). Supports all interactions within a bundle, except search. This method
  accepts Bundles of type batch and transaction, processing them according to the batch processing
  rules (DSTU2, STU3, R4, R5) and transaction processing rules (DSTU2, STU3, R4, R5)."
- "Managing FHIR resources using FHIR bundles": "If an operation fails when the bundle type is
  transaction, the Cloud Healthcare API stops executing operations and rolls back the transaction."
- `fhir.update` reference: "The conditional update interaction If-None-Match is supported,
  including the wildcard behaviour, as defined by the R5 spec. This functionality is supported in
  R4 and R5."
- "Prevent FHIR resource conflicts using ETags": with `If-Match`, "If the ETag doesn't match, the
  update fails with a 412 Precondition Failed error", and "The ETag matches the Meta.versionId in
  the FHIR resource."

R5 defines the entry fields (pinned): `Bundle.entry.request.ifMatch`, "Only perform the operation
if the Etag value matches."; `Bundle.entry.request.ifNoneMatch`, "If the ETag values match, return
a 304 Not Modified status. See the API documentation for "Conditional Read"". R5's http page
(unpinned, fetched 2026-10-06, SHA-256
`fcf508c5e0b7cb704d7f2c394d3addfaaad88fb0ee45e153fd8dcdc0c2a61d41`) says a transaction's entries
"are subject to the normal processing for each, including the meta element, verification and
version aware updates, and transactional integrity", and defines `If-None-Match: *` on an update
"to indicate where no existing versions of the resource exist".

**Needs a live check** (none was run; nothing here touched the cloud):

1. No Google page says that `executeBundle` honours `request.ifMatch` on an entry. It says it
   follows R5's transaction rules, which include version-aware updates. The first deploy after
   this change tests the success path: its smoke run republishes the smoke product, which exists,
   with `ifMatch`. If the store refuses the field, every persisting run fails before anything is
   written (`healthcare-execute-bundle-refused`).
2. No page says that `request.ifNoneMatch: "*"` on a PUT entry means "create only if absent"; Google
   says so for `fhir.update`'s header, and R5 defines the entry field for reads only. It is used only
   when a document is first published.
3. The status a failed precondition inside a transaction answers (412 or 400) is not documented. The
   worker reports `healthcare-execute-bundle-refused` either way.
4. A stale `ifMatch` should be refused. Checking that needs a transaction sent by hand with an old
   version against the dev store, which is the owner's to run.

Stated residuals:

- A write to a resource other than the Bundle by anything but the pipeline (a person with edit
  rights on the store) is not detected; the next run overwrites it.
- Republishing an older version after a newer one is not refused. It is a new store version,
  recorded in the ledger and with its own Provenance, not silent.
- An authority import's record is keyed on the EMA document's id. Whether the EMA keeps a
  document's id across its versions is not established: each pinned EMA document is one version.
  Imports run dry and persist nothing.

## Decision: product identity by EU number

ADR 0006 decision 5 and owner decision 2 of 2026-10-05; the formats are `zone_a.product`'s.

- **Systems.** `https://khs.dev/fhir/identifier/eu-authorisation-number` holds
  `EU/1/YY/NNN/PPP` (`^EU/1/[0-9]{2}/[0-9]{3,4}/[0-9]{3}$`); `…/eu-product-number` holds
  `EU/1/YY/NNN` (`^EU/1/[0-9]{2}/[0-9]{3,4}$`). A run as section 8 may write it
  (`EU/1/YY/NNN/PPP-PPP`) names several numbers and is refused as one.
- **Rules.** One RegulatedAuthorization per EU authorisation number: none carries two, no two carry
  one, and only a RegulatedAuthorization carries one. The MedicinalProductDefinition carries the
  product numbers, exactly the authorisation numbers' products. A Type 1 record may therefore have
  several RegulatedAuthorizations, each linked to the product and the holder; the List's holder,
  regulator and procedure are taken only when they all state the same ones, and refused otherwise.
- **Where they are enforced.** `src/fhir/preflight.ts` (`euNumberIssues`, both graph types), and
  the package's profile `https://khs.dev/fhir/StructureDefinition/eu-product-identity`, whose
  invariants `khs-eu-1` to `khs-eu-4` state the same rules for the official validator. The pipeline
  validates the source and the EMA document Bundle against it (`officialValidationTargets`); the
  store's `$validate` is not asked, since the store does not import the package. Run on 2026-10-06
  with the pinned validator over ten graphs: the two valid ones (two authorisations and their
  product; no EU number) had 0 errors, and each of the eight invalid ones failed with exactly the
  invariants the preflight names (malformed number, a run, two numbers on one authorisation, one
  number on two, no product number, an extra product number, a malformed product number, an
  authorisation number on the holder).
- **The EMA output.** EUePI 1.0.0 defines `ext-epi-eu-number` on the List (verified in the pinned
  package). The transform writes it when the graph states exactly one EU product number, with the
  system the EMA's sample writes, `http://ema.europa.eu/fhir/euNumber/`, since the package defines
  none. That system rests on a non-normative source. Two product numbers refuse the run: the List
  carries one (0..1). `ext-epi-ema-number` is not written: no source states an EMA number.
- **No fixture carries an EU number.** The synthetic fixtures keep their synthetic systems (no
  synthetic identifier may read as a real authorisation), so no run today writes one. The tests use
  the EMA's own example number, `EU/1/24/9999`.
- **Naming systems.** `dev.khs.fhir.epi` 0.3.0 has a NamingSystem for every identifier system the
  pipeline and its fixtures write (26, one of them retired: the old `ema-composition`).
  `test/version-identity.test.ts` finds every system they write and requires an active one.

## What the dev store needs

No migration. No id changes, so the entitlement map, the query service's four tools, the ledger
and `scripts/demo/seed*.ts` work as before. Nothing reads `Composition.identifier`: the query
service reads a document Bundle by id and a section by id. The versions already stored keep the
old identifier (`…/ema-composition`, now retired), and the next publication of each document writes
a new version with the new one, conditioned on the version it read. For the smoke product that is
the next deploy's smoke run; for the demo documents, only a new publication. The stored version 2
of the default product keeps the bytes it was seeded with, without the new `…-v2` Composition
identifier and date: re-seeding is not needed, and would add a version.
