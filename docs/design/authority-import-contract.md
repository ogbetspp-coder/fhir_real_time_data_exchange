# The canonical contract for an authority's published ePI (roadmap 3a, PR 2)

_Design, 2026-09-24. Sixth revision, amended with the sixth review's Lows: five independent
reviews found fifteen, twenty, fifteen, thirteen and nine findings (three, three, one, one and one
High), each settled below; the sixth found nothing above Low and judged it sound to build._

## What this is for

ADR 0005 decided that an authority's published ePI may enter the canonical record: its text
carried exactly (ADR 0005's T and the fidelity contract), its product identity taken only from
the authority's structured index, its approval being the authority's publication. The contract
cannot hold such a record today: `CanonicalSubmission` 1.0.0 accepts a PDF or Word source, spans
on the pages of a drawn document, a Type 2 graph of nine resource types, and an approval attested
by a person.

Two reviews shaped this design. The first showed that every fact about an import would have been
the producer's own claim. The second showed that recomputing the import from the authority's
bytes (the answer to the first) only moves the trust onto the bytes, and that nothing then tied a
document to its product's index or kept a synthetic publication out of production. So:

- **Zone B fetches the authority's bytes itself and recomputes the import** (D1). What is trusted
  is the authority's HTTPS server, not whoever wrote the submission.
- **A document is imported only with the index that lists it** (D5).
- **A synthetic publication is a closed, separate authority, refused unless the deployment is
  declared synthetic** (D7).

And it closes the two duties `fidelity-norm/3.0.0` left to it: no drawn-document extractor
supports an approval (D7), and every page without a span is blank (D4).

## What the sources say (checked 2026-09-24)

- **HL7 Global ePI 1.0.0 (pinned).** The Type 1 example holds a Composition and its authoring
  Organization; Type 2 adds the product graph; all use `Bundle-uv-epi`. The profiles require
  `Bundle.language`, `Bundle.identifier`, `Composition.identifier`, `.status`, `.type`, `.date`,
  `.author`, `.title`, every section's `title` and `code`, `MedicinalProductDefinition.identifier`
  and `.name`, `Organization.identifier`, and `RegulatedAuthorization.identifier`, `.subject` and
  `.holder`.
- **EMA EUePI 1.0.0 (pinned `package.tgz`).** `EUEpiList` carries the product's identity as List
  extensions (`valueIdentifier`: holder, procedure number, regulatory agency, EU number, EMA
  product number; `valueString`: holder and agency display, authorisation type, version number);
  `List.title` is the product name. The EMA's English sample writes the EMA product number and EU
  number, and says of the product number that the link to it "cannot be imported but needs to be
  created in the PLM portal".
- **The EMA's live data for Imatinib Teva** (fetched 2026-09-24, identical bytes on two fetches):
  - The List (`EPI/24/35`, `versionNumber` 2, `status` current, `title` "Imatinib Teva") puts its
    extensions on `List.subject` under other URLs, codes the holder and agency as `valueCoding`,
    writes the variation procedure `EMEA/H/C/002585/IA/0056` as the procedure number, carries no
    EU or EMA product number, and codes the List with no system. Eleven entries reference document
    Bundles by `Bundle/<id>`, two of them English SmPCs (hard capsules, the one ADR 0005 pinned,
    and film-coated tablets), distinguishable only by their text.
  - The document Bundle is not valid FHIR JSON as served: the Composition's `resourceType`,
    `language` and `status` are the integer `0`; its one entry's `fullUrl` is
    `http://ema.europa.eu/fhir`; the Composition has no identifier; its `author` is
    `{system: "http://www.test.com", value: "systemuser"}`; its section codes use
    `https://spor.ema.europa.eu/v1/lists/200000029659/terms/`; two of its titles omit the QRD's
    optional wording ("6.5 Nature and contents of container", "6.6 Special precautions for
    disposal"). Neither file carries a signature; the document carries no link to its product
    other than its title.
- **Two EMA editions both labelled 1.0.0** (`eu-epi-profiles-1.0.0.zip` and `package.tgz`) differ
  outside the SmPC and CAP codes; validation stays on the package.
- **Pictures in the EMA's samples** are a `Binary` contained in the Composition, named by an `img`
  `src` of `#` and its id; Imatinib Teva's two pictures are URL references (`~/_entity/…`).

## Decisions

### D1. Zone B fetches the authority's bytes and recomputes the import

An authority import is `import(inputs, request)`: a pure, deterministic TypeScript function
(`src/authority/`) of the authority's document Bundle, its List, the pictures it references (D6),
and a request (D2). The producer is a script that runs the same function and writes the
submission; the gate runs it again:

1. **Fetch.** Zone B builds each URL from the submission's ids by a fixed template for the
   authority (EMA: `https://epi.ema.europa.eu/consuming/api/fhir/Bundle/{id}` and `…/List/{id}`,
   where `id` must be a lower-case GUID, `^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$`), checks the
   resolved URL's scheme and host (`https`, `epi.ema.europa.eu`), and fetches it with exactly
   `Accept: application/fhir+json` (the EMA serves XML, or an error, for other values), no
   redirects followed (a redirect refuses), a 30-second timeout and a 4 MiB limit on the decoded
   body. Only HTTP 200 is accepted; `Content-Type` and its charset are ignored (the EMA echoes the
   request's `Accept`). The decoded bytes' SHA-256 and length must equal the submission's pins.
   A different or missing response (the authority changed, withdrew or cannot serve the file)
   refuses the run: a false failure, acceptable, and the answer is a new import. The producer's
   copies serve only the committed labels and the round trip (PR 4); the gate never trusts them.
2. **Keep.** Zone B writes the bytes it fetched to the evidence bucket (CMEK, immutable, as the
   run's other evidence) and records their URIs, hashes, the fetch time and the worker image
   digest in the run manifest. Re-verification later is that same image run as a command
   (`scripts/authority/verify-import.ts`, given the recorded submission, page text, report and the
   copies of the fetched files) that reads only those copies and never the network. In PR 2
   an import runs only dry, which writes no evidence, manifest or ledger row, so this step and
   D12's import evidence first run in PR 5; its test (`test/authority/gate.test.ts`) re-verifies a
   recorded import from copies.
3. **Recompute** with the importer version the submission names, which must be the version this
   build contains (an older import is re-imported, ADR 0005 decision 4).
4. **Compare.** Every field of the submission is in one of three classes (D3's table): recomputed
   (must equal the recomputation, by canonical hash), checked (a stated rule) or free (grammar only,
   carries no content). The Bundle, the page text, the page map, the structuring decisions, the
   fidelity report and the graph type are recomputed.
5. **Everything else** runs as today, the fidelity re-execution included.

What remains trusted is that `epi.ema.europa.eu`, reached over HTTPS at gate time, serves the
authority's publication, and the EMA says of that service that its ePIs are "for pilot purposes
only; they may not be up to date and are not to be used as medicines information". So the
approval (D8), the Provenance and the run manifest carry `authorityStatus: pilot`, and an EMA
outage refuses runs. Until PR 5 an authority import runs only as a dry run: the gate refuses an
authority-publication source when `DRY_RUN` is false, so nothing is persisted and nothing can be
entitled to the query service or reach the agent. PR 5 adds the pilot status to their answers,
then lifts the refusal. Recomputation proves that the named importer ran on those bytes, not
that the importer is right: that rests on its tests, its golden vectors (D10) and ADR 0005's
renderer cross-check, made a gate of reproduced records whose hash the run manifest pins
(`docs/design/authority-import-renderer.md`, PR 3c).

**Bytes and JSON.** The gate hashes raw bytes after HTTP content decoding, never a re-serialised
value (an exception to ADR 0002's hash-the-JSON-value convention, stated in its amendment). It
decodes strict UTF-8 (invalid bytes refuse; a byte-order mark refuses), parses JSON with a parser
that refuses duplicate keys, and the importer uses no `Date`, locale or `Intl`: times are carried
as the strings the authority wrote (`2025-02-06T12:37:15.580712+00:00`).

**What PR 2 ships of the importer.** T, ADR 0005's lexical transform, starts with empty closed
lists: it accepts a div with nothing to remove and refuses every other. PR 3 fills the lists
against the real label with the renderer cross-check; the contract already carries PR 3's inputs
(D6), so PR 3 changes the importer version and its vectors, not the contract. This moves the
importer from Zone A (the plan's Python) to shared TypeScript; the ePI reader stays in Zone A for
the QRD conformance check.

### D2. A human requests every import, and the request is content

ADR 0002's rule that no document content reaches the store without a hash-bound human decision
stays true. For an import the decision is which publication to import and in which language (for
Imatinib Teva, which of the two English SmPCs), not whether its words are right, which D1 proves.
The **request**, `{ authority, documentId, indexId, language }`, is an input to the import and
part of the approved content (in the provenance); the **approval** (D8) names who requested it.
A re-import after a version change is a new request.

### D3. The source record, and every field's class

`provenance.sourceDocument` becomes a union on `kind`: `drawn` (today's fields) or
`authority-publication`:

| Field                                                        | Grammar                                       | Class                                                                                            |
| ------------------------------------------------------------ | --------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `mediaType`                                                  | `application/fhir+json`                       | checked                                                                                          |
| `authority`                                                  | `EMA` \| `synthetic` (D7)                     | checked against the request                                                                      |
| `request`                                                    | D2                                            | an input to recomputation, checked (the approval's publication must name it); approved content   |
| `document`, `index`                                          | `{ id, sha256, byteLength }`                  | checked by the gate's fetch (D1)                                                                 |
| `index.epiId`, `.versionNumber`, `.metaVersionId`, `.status` | from the List                                 | recomputed                                                                                       |
| `pictures`                                                   | D6                                            | recomputed from the document; each fetched picture's hash and length checked by the gate's fetch |
| `sectionPages`                                               | `{ page, path, code }` per section, pre-order | recomputed                                                                                       |
| `extractedText`                                              | `{ uri, sha256, extractorVersion }`           | `sha256` and version recomputed; `uri` free                                                      |

The producer's fetch times are not recorded; the gate records its own (D1).

Elsewhere in the submission:

| Field                                                                                  | Class                                                                                                           |
| -------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `schemaVersion`, `graphType`                                                           | checked (2.0.0; `type1`)                                                                                        |
| `submissionId`, `createdAt`, `extraction.extractionRunId`, `extraction.serviceVersion` | free                                                                                                            |
| `bundle`, `bundleSha256`                                                               | recomputed                                                                                                      |
| `provenance.sections` (spans, `narrativeDivSha256`, `normalizedTextSha256`)            | recomputed                                                                                                      |
| `provenance.decisions` (with `sourceField`, `terminologyRef`)                          | recomputed, one decision per element instance the D9 table names; `terminologyRef.lookupId` is `mapping:<code>` |
| `extraction.parser`                                                                    | recomputed (`authority-import`, this build's version)                                                           |
| `extraction.model`, `extraction.promptTemplate`                                        | must be absent                                                                                                  |
| `extraction.terminologyService`                                                        | recomputed: the mapping manifest, `{ name: cap-smpc-en, version, snapshotSha256 }`                              |
| `provenance.fidelity`                                                                  | recomputed, except `reportUri` (free)                                                                           |
| `approval`                                                                             | D8: `requestedBy`, `requestedAt` free by grammar; the rest checked; `approvedContentSha256` checked as today    |

Free fields are identifiers, URIs or times by grammar, so they carry no content. The submission
reader still fetches the producer's source text and report by their URIs and hashes, as today;
for an import their contents must then equal the recomputation, so a wrong file refuses. `SourceDocumentText` and the verifier do not
change: the page map lives in the provenance, since the fidelity vectors hash the text object.

### D4. The document must be the mapping's tree, and its pages are its sections

The document's section tree must equal the mapping's rule tree: the same sections, each with
exactly one code, under the same parent, in the same order, and nothing else. An unmapped,
uncoded or doubly coded section refuses, empty or not (amending ADR 0005 decision 3, which named
only an unmapped code carrying narrative). The EMA's SPOR system URI
(`https://spor.ema.europa.eu/v1/lists/200000029659/terms/`) and the mapping's target system are
the same code system: a closed alias, stated in the mapping.

**Titles.** Each rule lists the titles the QRD template permits for it (with and without its
optional wording), a minor version of the mapping, checked by hand against the pinned template
10.4 and by a test against the QRD registry. The document's title must be one of them; the record
and the EMA output carry the document's title, and the EMA preflight accepts any permitted title.

**Pages** (fidelity §7): one per section at every depth, in pre-order; the whole page is the
body; it holds exactly the scanner's text for T(div), or nothing for a section without `text`
(`""`; one sentence added to §7, which is silent on it: documentation of existing behaviour, no
vector changes, not a new normalisation version). Then:

- a section whose scanner text is `empty-narrative` (the EMA's `<div>&#160;</div>` headings)
  carries no `text` in the record; every other section carries T(div) and exactly one span, on
  its own page, covering the whole body;
- the re-executed report's `coverage.uncoveredGaps` is 0, so no page without a span holds
  anything a reader would see;
- a section that draws nothing refuses where the mapping requires narrative, and a leaf section
  that draws nothing refuses anywhere (FHIR `cmp-1`; an `emptyReason` would be a placeholder).

Real SmPCs these rules refuse, as acceptable false failures: of the four pinned labels only
Imatinib Teva and Nuvaxovid have the mapping's tree (Jentadueto has about eighty uncoded
subheadings of its holder's and a subsection coded with another's code; Brukinsa keeps the
template's `<…>` brackets in 6.5 and 6.6); and national-procedure Lists whose title is
title-cased differ from their document's title (D5).

### D5. A document is imported only with the index that lists it

The importer, and the gate as a defence, require: exactly one List entry references
`Bundle/<documentId>`; that entry's display equals the document type's display; the document's
`Composition.title` equals `List.title`; the List's `status` is `current`. So one product's name,
holder and procedure cannot be joined to another product's text.

**The authority's live form, closed.** The importer reads the EMA's document and List by the
closed shape in Appendix A, enumerated from the four EMA SmPCs and four Lists this change pins in
`labels/ema-epi/` by hash (Imatinib Teva's hard-capsule SmPC and its List, and the three
QRD-check labels' Lists beside their already pinned SmPCs), and refuses anything else; a test
reads each pinned file and requires Appendix A to accept it exactly as served. The EMA serialises FHIR
codes as integers of its own enumerations: `language` is `0` for English, `5` Danish, `6` Dutch,
`24` Spanish, `25` Swedish in the live data, so an English import requires `0`; `resourceType`
and `status` are `0` on every document, and what the EMA's `0` status means is unknown. The
record's `Composition.status` is therefore `final` by rule from the publication (a List `current`
served by the EMA's consuming API), and that the EMA's own status value is unread is a stated
residual. Every non-narrative string the importer carries refuses a lone surrogate, a control
character and a format character (`List.title` becomes the product name).

Not provable, and stated: that no other List also lists the document (the EMA's search ignores
unknown parameters, so uniqueness cannot be asked).

### D6. Pictures, as the contract carries them from 2.0.0

A document names pictures in four forms, and `pictures` (recomputed from the document) lists
the ones that are not in the document's own bytes:

| Form                                                                                        | Where the bytes are                                                | In `pictures`                                                                                                        |
| ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------- |
| `data:` URI in the div (Brukinsa)                                                           | the document                                                       | no                                                                                                                   |
| `#id` naming a `Binary` contained in the Composition (the EMA's samples)                    | the document; T writes the Binary's decoded bytes as a `data:` URI | no                                                                                                                   |
| a reference the authority's viewer resolves (Imatinib Teva's `~/_entity/annotation/<guid>`) | fetched                                                            | `{ reference, url, sha256, byteLength }`, the URL built by the importer's template for the reference's exact grammar |
| a reference the authority's viewer draws as nothing                                         | none                                                               | `{ reference, evidence }`                                                                                            |

A `#id` naming no contained Binary, or a `src` in none of these grammars (the EMA's Spanish
sample writes `SIGRE`), refuses the section. The gate fetches each fetched picture by D1's rules, except that it sends `Accept: */*`, and
requires its pinned bytes.

A deletion is never proven by a failed fetch: the EMA's service answers the same "Resource not
found" for every unknown path, its own root included. Nor by the renderer cross-check, which draws
the div without the authority's viewer. A deletion is proven only by an evidence record in the
importer's versioned data (`src/authority/evidence/`), one per picture occurrence:
`{ authority, documentId, documentSha256, sectionPath, occurrence, reference, viewerUrl,
resolvedUrl, httpStatus, drawnBox, renderer (name@version), captureSha256 }`, made by loading the
authority's own viewer page for that document in a pinned browser, with the capture committed
beside it and re-checked against `captureSha256` in CI. The rule for a reference in the
viewer-resolved grammar: a matching record means the picture is deleted; otherwise it is fetched by
the importer's template for that grammar, whose allowed host is part of the template's data; with
no template, the section refuses. The gate recomputes `pictures`, so a deletion without a record
refuses. Evidence records and templates are importer data under D10's version lock.

Which of Imatinib Teva's two pictures are drawn is not settled: ADR 0005 decision 3 took them as
deleted on a not-found and a zero-size draw, and `docs/design/fidelity-norm-3-0-0.md` said the
import carries no picture; the not-found proves nothing, so PR 3 measures them in the EMA's viewer
and reconciles both documents. §7 already carries a structured source's referenced picture as its
bytes. PR 2's T refuses every picture.

### D7. Extractors and synthetic sources

- An authority import's extractor is `authority-import`, the importer, at this build's version;
  D1 re-executes it. Its version names every component whose output reaches the text (§7); the
  importer's version is that set.
- **Synthetic sources are refused unless the deployment is declared to accept them.** The marks,
  exactly, by source kind:

  | Source                | A synthetic submission carries all of                                                                                                                                                               | A non-synthetic one carries none of                                 |
  | --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
  | drawn                 | an extractor named `synthetic-*`; a terminology service named `synthetic-*` if any; a Bundle identifier value `synthetic-*`; the marker "not for clinical use" in every narrative that carries text | the same                                                            |
  | authority publication | the `synthetic` authority; the derived Bundle identifier value `authority-import:synthetic:<guid>`; the marker in every narrative that carries text                                                 | the same, and its identifier value is `authority-import:ema:<guid>` |

  The identifier segment is a closed table: authority `EMA` → `ema`, `synthetic` → `synthetic`.
  The synthetic authority keeps the EMA's live form: its ids and document identifier are GUIDs
  from a reserved block (`00000000-5979-4e74-8000-` followed by twelve hex digits), its holder,
  agency and procedure values begin `SYNTHETIC-` in the EMA's and SPOR's own systems, and the gate
  "fetches" it from the code that builds it (`src/authority/synthetic.ts`), an origin allowed only
  when the flag is on.
  The marker matches as a case-sensitive substring of the scanner's text of each narrative that
  carries text. The importer refuses a synthetic publication that carries a real value (an id
  outside the reserved block, a holder, agency or procedure value not beginning `SYNTHETIC-`).

  `ALLOW_SYNTHETIC_SOURCES` defaults to false in `config.ts` and in Terraform. When it is false,
  the gate refuses any of those marks, `ENABLED_RUN_SOURCES` defaults to `document` only, and the
  worker refuses to start if `fixture` or `healthcare-api` is enabled (both bypass the gate);
  Terraform's `enabled_run_sources` then defaults to `["document"]`, and a validation refuses the
  two variables disagreeing. Local `npm run dev` becomes document-only unless the flag is set.

- **The authority-import namespace is reserved, and every persisted id derives from it.** The
  identifier value prefix `authority-import:` is written only by the importer; every other path
  (the drawn gate, and the ungated `fixture` and `healthcare-api` sources) refuses a source Bundle
  whose identifier value has it. That alone is not enough, because today the crosswalk derives
  only the List, Bundle and Composition ids from the identifier value and copies every other entry
  (the MedicinalProductDefinition, Organization, RegulatedAuthorization, and in Type 2 the rest)
  with the id its source chose, and the Provenance id derives from `submissionId`. So from 2.0.0,
  for every source kind: the crosswalk gives each copied entry a new id,
  `stableUuid("ema-entry:" + resourceType, identifierValue + ":" + position)`, and a new
  `urn:uuid` fullUrl, rewrites every `Reference.reference` that names a copied entry's fullUrl to
  the new one, and refuses any other reference; and the Provenance id is
  `stableUuid("ingestion-provenance", identifierValue + ":" + submissionId)` (one per approval,
  so two approvals of the same content keep both), and its targets are built only from the
  output (`Bundle/<id>`, `Composition/<id>`), never from a source fullUrl. `Bundle.identifier.value`
  becomes required for every source kind, ungated ones included, and the crosswalk's fallback to
  `Bundle.id` or a hash is deleted, so the reserved-prefix check reads the key the crosswalk uses.
  The reference rewrite applies to the Composition and to every copied entry; it treats every
  string-valued `reference` key; it matches a copied entry's fullUrl literally, so a relative,
  versioned, `#contained` or external reference refuses (acceptable false failures, stated); an
  identifier-only reference (D9's regulator) is kept; references the crosswalk writes itself (the
  List's entry) are its own. Every PUT a run makes, and every reference in every resource it
  persists, the Provenance's included, is then a function of its checked identifier value, so a
  run can write only into its own namespace. The output Bundle carries only `resourceType`,
  `id`, `meta`, `language`, `identifier`, `type`, `timestamp` and `entry` (each entry only
  `fullUrl` and `resource`), and its `meta`, like the Composition's, is the profile alone; a
  source with any other Bundle or entry element (a signature, a link, a request), any `meta`
  element on any resource but `versionId`, `lastUpdated` and `profile` (an extension, a tag, a
  source), or a contained resource or implicit rules on any resource is refused. Other
  address-like values (a Composition's `url`, an extension's `valueUri`) are carried as the source
  wrote them, a stated residual. Tests build, for
  every source kind, a Bundle that
  reuses an import's entry ids, fullUrls and `submissionId`, and require its PUT URLs and
  persisted references to be disjoint from the import's; and a copied entry that references
  another namespace refuses.

### D8. The approval: a person's attestation, or an authority import request

`approval` becomes a union on `method`:

- `api-attestation` | `manual-record`: today's fields.
- `authority-publication`: `meaning: authority-publication-imported`; `authority`;
  `authorityStatus` (`pilot` for the EMA's ePI service, D1); `publication`: `{ epiId, documentId,
indexId, versionNumber, procedureNumber, authorityTimestamp }` (`authorityTimestamp` is the Bundle's `timestamp`, when the authority
  assembled it, not a publication date); `requestedBy`; `requestedAt`; `approvedContentSha256`.

The method is required exactly when the source is an authority publication, and every
publication field must equal the source's and the graph's. `requestedBy` is a placeholder, like
`approverId`, until roadmap item 2 binds it to an identity token (`docs/design/approval.md`'s amendment of
2026-09-25 replaces the method: its fields move into the source record, and the request statement's
signer replaces `requestedBy` and `requestedAt`).

Stated residuals: which import is current when a later one supersedes it, and how a withdrawal
reaches the record, belong to roadmap item 2's head and withdrawal (`docs/design/approval.md`,
amended 2026-09-25: one List per product at a time until supersession is designed); a withdrawal
after an import is not noticed until a re-import. Corrected 2026-09-26: the EMA keeps serving older
Lists and documents as current after a new List version (Brukinsa's EPI/23/1009 has two current
Lists), so an older pinned publication can still be imported after the authority has replaced it;
the approval design's List rule, not the gate's fetch, keeps it from displacing a newer import.

This amends ADR 0005 decision 4: a re-import after a normalisation change is possible only while
the authority still serves the same bytes for the document and its List (the List changes when
any document of the product changes); otherwise it is a new import of the current publication,
and the earlier record is withdrawn under roadmap item 2 (supersession across Lists awaits its own
design; `docs/design/approval.md`, amended 2026-09-25: one List per product at a time).

### D9. The Type 1 record: every value and its origin

`graphType` is part of the approved content; `type1` only with an authority publication, and such
a source is always `type1` in 2.0.0. The record has exactly one Composition (first),
MedicinalProductDefinition, Organization and RegulatedAuthorization, and nothing else.

| Value                                                      | Origin                                                                                                                                                                                                                                                                                                | Decision                                                                                                                         |
| ---------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `Bundle.identifier`, `Composition.identifier`              | ours: system `https://khs.dev/fhir/identifier/authority-import`, value `authority-import:<authority>:<document id>` for the Bundle and the same with `:composition` for the Composition (D7's reserved namespace); the record is not the EMA's Bundle                                                 | defaulted-by-rule                                                                                                                |
| `Composition.relatesTo`                                    | `derived-from` the EMA's document, by the EMA's own document identifier (`http://ema.europa.eu/fhir/epiDocument`, its value as served, which is not always the Bundle's `id`)                                                                                                                         | extracted-verbatim                                                                                                               |
| `Bundle.type`                                              | `document`                                                                                                                                                                                                                                                                                            | defaulted-by-rule                                                                                                                |
| section `text.status`                                      | `additional` (the EMA writes `generated`, which says the text was generated from structured data)                                                                                                                                                                                                     | defaulted-by-rule                                                                                                                |
| `Bundle.language`, `Composition.language`                  | the request's language, required to match the EMA's language integer (`0` → `en`), and the document type's and the List entry's displays by a closed table (`… (English)` → `en`); the mapping is the English CAP SmPC's                                                                              | defaulted-by-rule                                                                                                                |
| `Bundle.timestamp`, `Composition.date`                     | the document's strings (the timestamp is when the EMA assembled the document, stated)                                                                                                                                                                                                                 | extracted-verbatim                                                                                                               |
| `Composition.status`                                       | `final`, from the publication (D5)                                                                                                                                                                                                                                                                    | defaulted-by-rule                                                                                                                |
| `Composition.type`, `.title`                               | the document's                                                                                                                                                                                                                                                                                        | extracted-verbatim                                                                                                               |
| `Composition.subject`, `.author`                           | the MedicinalProductDefinition; the Organization (the EMA's author is a system user)                                                                                                                                                                                                                  | defaulted-by-rule                                                                                                                |
| entry `fullUrl`s (`urn:uuid`), resource ids, section `id`s | the record's own: `stableUuid` of the resource type and the Bundle identifier value (`authority-import:<segment>:<document id>`); a section's of that value and its path (the EMA's section `id` is not required and not used); the crosswalk then re-derives its output ids from the same value (D7) | defaulted-by-rule                                                                                                                |
| section codes                                              | the mapping, through the stated system alias                                                                                                                                                                                                                                                          | code-mapped (the mapping manifest is the terminology source, by version and hash)                                                |
| section titles                                             | the document's, required in the mapping's permitted set                                                                                                                                                                                                                                               | extracted-verbatim                                                                                                               |
| `MedicinalProductDefinition.identifier`                    | the List's identifier (`http://ema.europa.eu/fhir/epiId`, `EPI/24/35`): the ePI's product scope                                                                                                                                                                                                       | extracted-verbatim                                                                                                               |
| `MedicinalProductDefinition.name.productName`              | `List.title`                                                                                                                                                                                                                                                                                          | extracted-verbatim                                                                                                               |
| `RegulatedAuthorization.identifier`, `.case.identifier`    | the procedure number (`http://ema.europa.eu/fhir/procedureIdentifierNumber`); the profile requires an identifier and the authority states no authorisation number in structured form, so the procedure stands in, stated                                                                              | extracted-verbatim                                                                                                               |
| `RegulatedAuthorization.subject`, `.holder`                | references to the MedicinalProductDefinition's and the Organization's `fullUrl`                                                                                                                                                                                                                       | defaulted-by-rule                                                                                                                |
| `RegulatedAuthorization.regulator`                         | a reference by identifier (the agency's SPOR id) with display, not a resource                                                                                                                                                                                                                         | extracted-verbatim (the code and display of the EMA's `valueCoding`, carried as an Identifier's value and a Reference's display) |
| `Organization.identifier`, `.name`                         | the holder's SPOR id and display                                                                                                                                                                                                                                                                      | extracted-verbatim (likewise from a `valueCoding`)                                                                               |
| `meta.profile` on each resource                            | the HL7 ePI profiles                                                                                                                                                                                                                                                                                  | defaulted-by-rule                                                                                                                |

Nothing is derived from another value's content: the EMA product number (the procedure number's
prefix under the EMA's grammar) is not read out, because the authority does not state it and the
EMA says its link must be made in its own portal; the EU numbers, strengths and forms are in the
narrative only. The MedicinalProductDefinition is the ePI's product scope (both English SmPCs of
Imatinib Teva share it). Not carried: the EMA's `Composition.text`, `contained`, `author` and its
`documentType` extension.

Structuring decisions gain `sourceField`, a pointer to the field read in a new grammar-limited
primitive (`SourcePath`: dotted names with `[n]` or `[name]` selectors, for example
`List.subject.extension[procedureNumber]`), required on `extracted-verbatim` for an import; a
picture reference is a new primitive too (its grammars, D6). There is exactly one decision per
element instance of the table above (it is recomputed anyway). A `SourcePath` starts at the
resource it reads (`List.`, `Bundle.`, `Composition.`); `sectionPages[].code` is the EMA's section
code as served. `graphType: type1` is the
declaration ADR 0005 calls "declared not supplied" for packs, ingredients and substances.
`validateType2Preflight` becomes `validateCanonicalPreflight(bundle, graphType)`, with the Type 1
set closed by resource type and count; Type 2 stays exactly as strict.

### D10. Shared code and its change control (amends ADR 0004)

`src/authority/` joins ADR 0004 decision 2's shared pure code: it decides what text enters the
record. Golden import vectors (synthetic publications in, submissions out) are generated under
`contracts:check`, and a lock file maps each importer version to the hash of `src/authority/`
(code and data: T's lists, templates, evidence records, the language table), from importer 2.0.0
also of the fidelity scanner's files T reads with (`src/fidelity/xhtml.ts`, `normalize.ts`), and of
its vectors: CI
fails when either changes while the version does not, so a change of behaviour or data must
change the version. The version is the reviewed label of the importer; the worker image digest in
the manifest (D1) is its complete identity, including `src/fidelity/`, the hash library, the
mapping and the dependencies, which the lock covers only where the vectors exercise them. The importer checks in a stated order, and a refusal names the first check that fails: the fetch
and bytes (D1), Appendix A, D5, D4's tree, D4's titles, pictures (D6), then T and the scanner per
section in pre-order, the record's checks, and last the renderer gate's `rendering` stage
(`docs/design/authority-import-t.md`, amending this list). The vectors include the pinned real labels as expected-refusal cases with
the reasons the importer actually produces, recorded when it is built (in PR 2 the pictures stage
should refuse Imatinib Teva, whose two `~/_entity` pictures in 5.1 have neither template nor
evidence; Jentadueto fails the closed shape (its uncoded subheadings), Brukinsa its titles, Nuvaxovid its
pictures), and a
stage test that Imatinib Teva passes Appendix A, D5, and D4's tree and titles. The
producer runs the same code; its identity is not trusted, which is D1's point.

### D11. The crosswalk carries the product's identity into the EMA List

From 2.0.0 the crosswalk writes the `EUEpiList` extensions the graph states, each selected by its
path and identifier system, refusing where more than one candidate matches: holder (the
`RegulatedAuthorization.holder` Organization's identifier in the SPOR organisations system) and
its display (that Organization's name); regulatory agency and display (`regulator`); procedure
number (`case.identifier` in the procedure system, as the EMA's live List writes it). It writes
no EMA product number, EU number, authorisation type, domain or version number, and never a
`defaulted-by-rule` value into an extension. The synthetic Type 2 graph has no value in those
systems, so its List changes only in its title, which becomes the product name. The QRD template
version the crosswalk writes ("10.4", a parameter of `transformType2ToEma`) states the template
the output's sections follow; it is not taken from the authority, and is stated. The mapping's version moves (D4, D11), with the
generated artefacts, fixtures and validation set.

### D12. Evidence, Provenance, ledger, query

- **FHIR Provenance** for an import: activity `authority-import`; two source entities per fetched
  file (one `what` by the authority's identifier, one by hash), the authority as their agent; the
  importer as `assembler`; `requestedBy` as `enterer`; `recorded` is the gate's fetch time.
- **Run manifest** ingestion evidence: the source kind, graph type, approval method and fields,
  the fetched URLs with their hashes, the importer version, and the synthetic flag; the ledger's
  column descriptions (Terraform) say "approved content"; the approval columns are defined for
  the new method.
- **Query service**: nothing, until PR 5: an import is dry-run only (D1), so the query service
  never sees one. PR 5 extends `get_section`, `find_product` and `get_provenance` for imports, with
  the pilot status.
- **Lineage**: the run's source FQN for an import is `custom:authority-import.<segment>.<document
id>`, beside the three kinds `src/pipeline.ts` has.
- **Times**: `requestedAt` must not be later than the gate's fetch time; `Provenance.recorded` is
  the gate's fetch time.
- **Validation set**: the synthetic Type 1 record and its EMA List, Bundle and Composition join
  CI's official-validator set.

### D13. Versions

| Contract                                                                     | Version     | Why                                                                                                                                           |
| ---------------------------------------------------------------------------- | ----------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `CanonicalSubmission`                                                        | 2.0.0       | union members Zone B branches on; `graphType` and the request in the approved content; the `Type2Bundle` definition renamed `CanonicalBundle` |
| `RunManifest`                                                                | next major  | its ingestion approval becomes a union; `contractVersion` follows the submission's constant; old manifests stay readable                      |
| `SourceDocumentText`, fidelity vectors                                       | unchanged   | D3                                                                                                                                            |
| mapping manifest `cap-smpc-en`                                               | next minor  | permitted titles, the system alias, the List's new content                                                                                    |
| Zone A Python models, contract fixtures, demo seed, agent vendored contracts | regenerated |                                                                                                                                               |

### D14. Pins, the context copy, documents

- `fhir/standards.lock.json` pins the HL7 Type 1 example and the EMA's
  `eu-epi-profiles-1.0.0.zip`, `epi-24-129-sample-spanish.zip` and
  `medicine-PL-sample-all-languages.zip` (reference material).
- `labels/ema-epi/` pins Imatinib Teva's hard-capsule SmPC and the four Lists by hash (D5): the
  lock (`sources.lock.json`) gains a List file and hash per source (a minor version of its
  schema), Imatinib Teva joins the QRD check set (its check result committed and compared in CI
  like the others, and `check_label_sources.py` compares the Lists too), and the folder's README
  names its second purpose, the importer's evidence.
- `.gitignore` ignores `/context*/`, the owner's local copy of the EMA's downloads.
- Migration, in the change record: re-seeding the demo writes the re-identified graph beside the
  old resources in the target store and BigQuery (append-only), and new Provenance ids beside the
  old; the shared synthetic Organization becomes one per document. The tests that pin ids
  (`test/provenance.test.ts`, the query acceptance tests, the fixtures) regenerate.
- Amended: ADR 0002 (the contract; recomputation and the gate's fetch as ingress invariants; the
  raw-bytes hash exception; the import request as the human decision; the ungated sources beside
  authority imports, D7), ADR 0004 (D10), ADR 0005
  (D1's move to shared TypeScript, D4's tree and titles, D5, D6, D9's record, and decision 4's
  re-import, D8), fidelity §7 (the
  empty page, D4), `docs/architecture.md`, `docs/design/approval.md`, `docs/roadmap.md`, and
  `docs/validation/README.md` (UR-11, UR-14 and the requirements this touches; a change record).
  The fidelity design note's two deferred duties are marked closed.

## Stated residuals

- The gate trusts the authority's HTTPS server at gate time (D1); the authority serves no
  signature.
- Recomputation proves the importer ran, not that it is right (D1, D10).
- The two English SmPCs of a product are told apart only by the request (D2).
- A withdrawal after an import is not noticed until a re-import (D8).
- `RegulatedAuthorization.identifier` holds the procedure number, which changes with every
  variation, so it is not stable across imports (D9).
- The EMA's ePI service is a pilot (D1); an EMA outage refuses runs.
- What the EMA's integer `0` status means is unknown; the record's status is from the publication
  (D5).
- No other List can be shown not to list the document (D5).
- A picture's deletion rests on an evidence record of the authority's viewer, reviewed into the
  importer's data, not on anything the gate observes (D6).
- The QRD template version in the EMA output ("10.4") is ours, not the authority's (D11).
- An authority import is dry-run only until PR 5 (D1).
- `requestedBy` is a placeholder until roadmap item 2 (D8).
- The EMA's language integers are read from observation, not from a published table (D5).
- The MedicinalProductDefinition's identifier is the ePI's id, not a product id (D9).
- Address-like values other than `Reference.reference` (a Composition's `url`, an extension's
  `valueUri`) are carried as a source wrote them; no route that is not synthetic reaches the
  crosswalk with one today (D7).
- The importer lock reads main's first-parent history; a rewritten history of main is not seen,
  and a branch merged by rebase or fast-forward would release every intermediate lock entry it
  carried, failing every later run (fails closed; pull requests merge with a merge commit or a
  squash, AGENTS.md) (D10).

## Not in this change

T's closed lists, the renderer cross-check, pictures enabled and the real label (PR 3); the round
trip and a real-label validation case (PR 4); publishing, the query service's provenance and the
agent (PR 5).

## Verification

- A synthetic publication (document and List in the EMA's live form, under the synthetic
  authority) is imported by the script, passes the gate's own fetch and recomputation with the
  flag on, is refused with the flag off, runs through the pipeline in dry-run, and its EMA output
  passes the official validator in CI.
- One test per rule: a record differing from the recomputation in one value, section, page or
  decision; a fetched file differing from its pin, or unreachable; an older importer version; a
  document the List does not list, or whose title or type differs from the List's; sections
  swapped between pages; words moved to an empty section's page; an uncovered page that is not
  blank; an unmapped, uncoded, doubly coded or misplaced section; a title outside the permitted
  set; a duplicate JSON key, invalid UTF-8, a byte-order mark; an unknown key or a status the
  schema refuses; a Type 1 graph with a pack or a second Organization; a synthetic marker, extractor
  or authority with the flag off; a start with the `fixture` source enabled and the flag off;
  disagreeing extractor names; an approval that does not fit the source or names another
  publication; a List not `current`.
- Type 2 runs as before except the List title; schemas, Python models and fixtures regenerate;
  the Zone A parity tests pass.

## Appendix A. The EMA's live shape the importer accepts

Enumerated from the four pinned SmPCs and four pinned Lists (D5); every other key, value type or
value refuses. Marked required where D5 or D9 read it.

| Element                  | Keys (all optional unless marked) and accepted values                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| document `Bundle`        | `resourceType` `Bundle` (required), `id` (GUID, required, equal to the request), `meta` {`versionId`, `lastUpdated`}, `identifier` {`system` `http://ema.europa.eu/fhir/epiDocument`, `value` a lower-case GUID, not always equal to `id`} (required), `type` `document` (required), `timestamp` (required), `entry` (exactly one)                                                                                                                                                                                                                                                                                                                                                                |
| `entry`                  | `fullUrl` (any; not carried), `resource` (required)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `Composition`            | `resourceType` `0` (required); `language` `0` for an English import (required); `status` `0` (required); `text` {`status`, `div`} (not carried); `extension`: `https://ema.europa.eu/fhir/extension/documentType` (at most once) and `http://ema.europa.eu/fhir/extension/imageReference` (any number), each with `valueReference` (not carried); `type` {`coding` [one, `system` the EMA document-type system (required), `code` `100000155532` (required), `display` (required)]} (required); `date` (required); `author` [one, `{identifier}`] (not carried); `title` (required); `contained` [`Binary` {`resourceType`, `id`, `contentType`, `data`}, all required, D6]; `section` (required) |
| `section`                | `id`, `title` (required), `code` {`coding` [exactly one, `system` the SPOR section system (required), `code` (required), `display`]} (required), `text` {`status`, `div`}, `section`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `List`                   | `resourceType` `List` (required), `id` (GUID, required, equal to the request), `meta` {`versionId` (required), `lastUpdated`} (required), `text` {`status`, `div`} (not carried), `identifier` [one, `system` `http://ema.europa.eu/fhir/epiId`, `value` (required)] (required), `status` `current` (required), `mode` `working`, `title` (required), `code` {`coding` [one, `display` only]}, `subject` {`display`, `extension`} (required), `entry` [{`item` {`reference` `Bundle/<GUID>` (required), `display` (required)}}] (required)                                                                                                                                                        |
| `List.subject.extension` | exactly these URLs, at most once each: `…/procedureNumber` (`valueIdentifier` {`system` `http://ema.europa.eu/fhir/procedureIdentifierNumber`, `value`}), `…/marketingAuthorisationHolder` and `…/regulatoryAgency` (`valueCoding` {`system` SPOR organisations, `code`, `display`, all required}), `…/domain` (`valueCoding`; not carried), `…/authorisationType` and `…/versionNumber` (`valueString`), all under `https://ema.europa.eu/fhir/extension/`; the procedure number, holder, agency and version number required                                                                                                                                                                     |
