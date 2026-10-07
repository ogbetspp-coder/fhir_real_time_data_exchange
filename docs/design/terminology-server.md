# Proposal: a local terminology server for the codes offline validation cannot check

- Status: proposal. Nothing is built.
- Date: 2026-10-06
- Related: `Dockerfile.validator` (the sidecar's flags), `scripts/ci/official-validate.mjs`,
  `docs/validation/changes/2026-10-06-product-graph-terminology-and-epi-topic.md`

## What the pinned validator checks offline

Measured on 2026-10-06 with the pinned `validator_cli.jar` 6.10.4, the five pinned packages and
the sidecar's flags (`-tx n/a -no-http-access`), one test resource per case:

- **A code system a pinned package holds with `content` `complete`** (the three EMA lists in
  `EUePI#1.0.0`: `100000000004` medicine domain, 3 codes; `100000155531` document type, 9;
  `200000029659` QRD sections, 135; the eight base R5 systems the product graph uses, in
  `hl7.fhir.r5.core#5.0.0`; our own): checked. An unknown code is an error ("Unknown code ... in
  the CodeSystem"); a wrong display is an error ("Wrong Display Name").
- **UCUM**: not checked. A valid `mg` and an invalid `mgxq` get the same warning: "Unable to
  validate code ... without terminology services".
- **SNOMED CT**: not checked, and not reported. A valid `26643006` and an invalid `26643007` get no
  message at any level.
- **A system no package defines**, such as the SPOR form
  `https://spor.ema.europa.eu/v1/lists/100000155531/terms/`: a warning, "A definition for
  CodeSystem ... could not be found".
- **No system**: a warning, "Coding has no system".

The pinned packages cannot close the two gaps: `hl7.terminology.r5` 5.0.0 and 6.2.0 define UCUM
and SNOMED CT with `content` `not-present`.

The CI gate fails on every warning its allowlist does not name, so a new unchecked code shows up
on a pull request. In the deployed pipeline a warning does not stop a write: only `error` and
`fatal` do (`docs/architecture.md`, "Validation model").

## What remains unchecked today

- UCUM: `mg` on the ingredient's strength, in the source and the EMA Bundle (two allowlisted
  warnings).
- SNOMED CT: the route, `26643006` "Oral route", the code and display the Global ePI package's own
  example uses. Nothing reports it. `test/type2-conformance.test.ts` keeps a list of the systems
  that are not checked, so a new one has to be added there by hand.
- The SPOR form of the EMA document type the authority import keeps (one allowlisted warning).
  Its list is in `EUePI#1.0.0`, but under another URL, and the import's display differs from the
  package's.

## Why a cache is not enough

The validator can keep terminology answers in a cache (`-txCache`). On 2026-10-06 a cache was
recorded against `tx.fhir.org` and replayed with the network closed. The validator stopped at
start: "Error fetching the server's conformance statement". It needs a server that answers.

## What a server would check

The same test resources, validated once against `tx.fhir.org` on 2026-10-06 (synthetic codes
only):

- `mgxq`: error, "Unknown code 'mgxq' in the CodeSystem 'http://unitsofmeasure.org' version '2.2'";
  `mg`: no issue.
- `26643007`: error, "Unknown code '26643007' in the CodeSystem 'http://snomed.info/sct' version
  'http://snomed.info/sct/900000000000207008/version/20250201' (International Edition)";
  `26643006` "Oral route": no issue.

## The smallest pinned option

FHIRsmith (`HealthIntersections/FHIRsmith`, BSD-3, the terminology server `tx.fhir.org` runs;
latest release `v0.14.2`, 2026-10-06; images on `ghcr.io`). It loads each code system from a
source list:

- `ucum:` the UCUM essence XML. This covers UCUM, the two warnings above.
- `snomed:` a SNOMED CT edition, built by its importer from a full snapshot download (2 to 6
  hours, its documentation says). This would cover the route. The download is SNOMED CT's
  release, under its licence. Leave it out until the owner decides.

Pinned: the image by digest, the essence file by SHA-256, both in the repository like the
validator's packages. Run beside the validator in CI's Official validation job, and as a second
sidecar if the deployed pipeline should check the same codes.

## Open questions

1. **The network switch.** `-no-http-access` refuses a server on `127.0.0.1` too ("Access to the
   internet is not allowed by local security policy", tested 2026-10-06). The sidecar would need
   the validator's SSRF protection instead, allowing one host through `fhir-settings.json`. Not
   tested.
2. **CI only, or the deployed pipeline too.** Only the deployed pipeline sees real content.
3. **SNOMED CT.** A licence, and a choice of edition.
4. **UCUM alone.** If SNOMED CT stays out, a UCUM library in the worker (for example
   `@lhncbc/ucum-lhc`, 7.1.10 on npm) is smaller than a server. It would be our own check beside
   the validator, not the validator's.

Not covered by any of these: EMA lists that no pinned package publishes as a FHIR code system.
