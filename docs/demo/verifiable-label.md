# Demonstration script: the verifiable label

- Status: written 2026-09-20 for roadmap item 1c
- Related: `docs/design/verifiable-answers.md` (section "The demonstration"),
  `docs/design/epi-mcp-query-service.md`, `docs/adr/0002-two-trust-zones-and-canonical-submission.md`
- Data: synthetic throughout. Three invented products, no clinical content, every sentence says
  so in its own words.

The line the whole demonstration exists to land:

> FHIR gives every sentence in a label a stable address — product, version, section — and
> everything here hangs off that address. PDFs do not have addresses.

## What is real today, and what is not

Say this out loud at the start. It is the difference between a demonstration and a pitch.

| Shown                                                                             | Built?                                                                                                                               |
| --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| Canonical submission, ingress gate, fidelity check, EMA transform                 | Yes — `src/`, run by the deployed worker                                                                                             |
| Validated resources in the Cloud Healthcare FHIR store                            | Yes                                                                                                                                  |
| Near-real-time BigQuery projection of those resources                             | Yes — the store's native ANALYTICS_V2 stream                                                                                         |
| Transformation ledger rows with approval and fidelity columns                     | Yes — `ema_flow_ledger_<ENV>.transformation_runs`                                                                                    |
| Signed evidence and a Provenance resource per approval                            | Yes                                                                                                                                  |
| MCP query tools (`find_product`, `get_section`, `verify_quote`, `get_provenance`) | **Built and tested, not deployed** — roadmap item 1, `src/query/`, `test/query/`; no `terraform apply` has created the service yet   |
| An assistant that answers from those tools                                        | **Built and tested, not deployed** — roadmap item 1b, `agent/`; the Agent Engine deploy is scripted in `agent/deploy/`, not executed |

Where a scene below has an assistant window, it is marked _(item 1/1b — built, not deployed)_
and a console-only equivalent that works today is given beside it. Nothing in this script
requires the undeployed parts; do not show a local run of them as if it were the deployed
service.

## Placeholders

Fill these in before the meeting; every command and query below uses them verbatim.

| Placeholder       | Where it comes from                                                             |
| ----------------- | ------------------------------------------------------------------------------- |
| `<PROJECT_ID>`    | the demonstration project                                                       |
| `<ENV>`           | `dev`, `staging`, …                                                             |
| `<REGION>`        | `terraform -chdir=infra output -raw region`                                     |
| `<DATASET>`       | `ema-flow-<ENV>-dataset`                                                        |
| `<STORE>`         | `ema-flow-<ENV>-validated-r5` (the validated target store)                      |
| `<EMA_BUNDLE_ID>` | `targetBundleId` printed by the seeding script for `synthetic-paracetamol`      |
| `<EMA_COMP_ID>`   | `Composition.id` inside that Bundle                                             |
| `<MANIFEST_HASH>` | `manifestHash` printed by the seeding script                                    |
| `<SECTION_HASH>`  | SHA-256 of the section 4.4 narrative `div`, recomputed in front of the audience |
| `<RUN_ID_V1/V2>`  | `runId` printed for version 1 and version 2 of the same product                 |

## Before the meeting: deploy in this order, then seed

**The order matters, and getting it wrong breaks scene 1 quietly.** The worker's Provenance
projection now writes the approver's role on the attester agent (`src/fhir/provenance.ts`), and
`get_provenance` reads only that coding — it never infers a role. Any document already in the
validated store was written before that change, so `get_provenance` answers `unavailable` for
it: no approver, no role, no error that explains why.

There is no worker-only or query-only deploy to order: `scripts/gcp/deploy.sh` runs one
untargeted `terraform apply` that reconciles both Cloud Run services together. What has to be
ordered is the re-ingest, which must land after the new worker is serving and before anyone
asks `get_provenance` anything.

1. Deploy this branch — the `Deploy to Google Cloud` workflow, or `bash scripts/gcp/deploy.sh`.
   Worker and query service both come up from this commit, and until step 2 the query service
   answers `unavailable` from `get_provenance` for every document already in the store.
2. **Re-ingest** with the seeding script below. That writes a second `Provenance` resource for
   the document rather than replacing the first, because the resource id is derived from the
   submission id.
3. Call `get_provenance` for the document you plan to show and confirm an approver role comes
   back. A document with two approved versions has two `Provenance` resources, and since
   2026-09-21 the one you get is the most recent approval by construction: the search asks the
   store for `recorded` descending and the answer is chosen under a total order, ties broken by
   resource id. Re-seeding therefore no longer changes which record answers.

Scenes 1 and 2 as written below use the console and BigQuery and do not depend on any of this;
it matters for the `get_provenance` parts, which are marked _(item 1 — built, not deployed)_.

## Seed the set

One command, once per environment. It writes three synthetic products at version 1 and then the
first product at version 2, each through the ordinary `document` run path: Cloud Storage
hand-off, ingress gate, fidelity check, transform, validation, store write, ledger row.

Both exports are required: the script reads them when it loads and exits immediately without
them. `terraform output` needs the real backend — a working copy initialised with
`-backend=false` answers `Error: Backend initialization required` — so run
`terraform -chdir=infra init -input=false -backend-config="bucket=<PROJECT_ID>-ema-flow-tfstate" -backend-config="prefix=terraform/state"`
first.

Minting the token below needs a one-time grant that project ownership does not include.
Impersonating the workflow account as the project owner was refused with
`Permission 'iam.serviceAccounts.getAccessToken' denied` (reproduced 2026-09-20), so do this
first, once per environment:

```bash
gcloud iam service-accounts add-iam-policy-binding \
  "ema-flow-workflow-<ENV>@<PROJECT_ID>.iam.gserviceaccount.com" \
  --member="user:<you>@<your-domain>" \
  --role="roles/iam.serviceAccountTokenCreator" \
  --project="<PROJECT_ID>"
```

```bash
export SUBMISSION_BUCKET="$(terraform -chdir=infra output -raw submission_bucket)"
export WORKER_URL="$(terraform -chdir=infra output -raw cloud_run_service_uri)"

# An ID token for $WORKER_URL, minted as a service account that holds run.invoker on the
# worker. A human's Application Default Credentials cannot mint one; see the README. The
# assignment fails loudly rather than exporting an empty token, which the script would read
# as "not set" and silently fall back to ADC for.
WORKER_ID_TOKEN="$(gcloud auth print-identity-token \
  --impersonate-service-account="ema-flow-workflow-<ENV>@<PROJECT_ID>.iam.gserviceaccount.com" \
  --audiences="$WORKER_URL" \
  --include-email)" || { echo 'token mint failed — do the grant above first' >&2; return 1; }
export WORKER_ID_TOKEN

npx tsx scripts/demo/seed.ts --dry-run   # rehearse: prints locations and hashes, writes nothing
npx tsx scripts/demo/seed.ts
```

The bucket is `<PROJECT_ID>-ema-flow-<ENV>-submissions` and the evidence bucket beside it is
`<PROJECT_ID>-ema-flow-<ENV>-evidence`; take both from the Terraform outputs rather than typing
them.

Authentication is a Google-signed ID token whose `aud` is `$WORKER_URL`. Application Default
Credentials of type `authorized_user` — what `gcloud auth application-default login` leaves —
cannot produce one: `google-auth-library` ignores the requested audience for that credential
type and returns a token minted for the ADC OAuth client instead, which Cloud Run refuses
(reproduced 2026-09-20). Mint it by impersonation as above and pass it in `WORKER_ID_TOKEN`,
which needs `roles/iam.serviceAccountTokenCreator` on that service account — project owner does
not include it. `--dry-run` needs no token at all, only the two exports. The full recipe,
including the one-time grant, is in the README under "Re-ingesting with
`scripts/demo/seed.ts`".

It prints one line per run — product id, version, run id, status, manifest hash, EMA Bundle id —
and nothing else. Keep that output: it is the placeholder table above, filled in.

Re-running publishes the same content again, which the store records as further versions of the
same resources. Seed once; rehearse with `--dry-run`.

The three products, all synthetic:

| Product id              | Label                                | MA number   | Section 4.3 mentions          |
| ----------------------- | ------------------------------------ | ----------- | ----------------------------- |
| `synthetic-paracetamol` | Synthetic Paracetamol 500 mg tablets | EU/SYN/0001 | a synthetic placeholder       |
| `synthetic-demoxetine`  | Synthetic Demoxetine 10 mg tablets   | EU/SYN/0002 | **severe hepatic impairment** |
| `synthetic-placebolol`  | Synthetic Placebolol 25 mg tablets   | EU/SYN/0003 | a synthetic excipient allergy |

Version 2 exists for `synthetic-paracetamol` only, and differs from version 1 by exactly one
sentence in section 4.4.

## Scene 1 — one truth, three windows

**What to say.** "This is one section of one label. I am going to show it to you three times, in
three different systems, and the same hash will be on the screen every time. There is no copy of
this text anywhere — there is one resource and three windows onto it."

### Window 1 — the validated EMA ePI

Cloud Console → Healthcare → Datasets → `<DATASET>` → FHIR stores → `<STORE>`. Open the resource
browser and read `Bundle/<EMA_BUNDLE_ID>`, or from a terminal:

```bash
curl -s -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://healthcare.googleapis.com/v1/projects/<PROJECT_ID>/locations/<REGION>/datasets/<DATASET>/fhirStores/<STORE>/fhir/Bundle/<EMA_BUNDLE_ID>" \
  > bundle.json
```

Point at three things in the document Bundle: the EMA profile on `Bundle.meta`, the QRD template
version extension on the Composition, and section 4.4 — EMA code `200000029806` in code system
`http://ema.europa.eu/fhir/CodeSystem/200000029659` — carrying the narrative `div` verbatim.

Then recompute its hash in front of them:

```bash
python3 - <<'PY'
import hashlib, json
bundle = json.load(open("bundle.json"))
def walk(sections):
    for section in sections:
        for coding in section.get("code", {}).get("coding", []):
            if coding.get("code") == "200000029806":
                yield section
        yield from walk(section.get("section", []))
div = next(walk(bundle["entry"][0]["resource"]["section"]))["text"]["div"]
print(hashlib.sha256(div.encode("utf-8")).hexdigest())
PY
```

That is `<SECTION_HASH>`. **What the audience should see:** a number they watched being computed
from the text on screen.

### Window 2 — the BigQuery row that appeared seconds after the write

Cloud Console → BigQuery → `<PROJECT_ID>` → `ema_flow_fhir_<ENV>` → `Composition`. This dataset is
written by the FHIR store itself (native ANALYTICS_V2 streaming); no pipeline of ours copies
anything into it.

```sql
SELECT
  c.id AS composition_id,
  s3.title,
  TO_HEX(SHA256(s3.text.div)) AS narrative_sha256
FROM `<PROJECT_ID>.ema_flow_fhir_<ENV>.Composition` AS c,
  UNNEST(c.section) AS s1,
  UNNEST(s1.section) AS s2,
  UNNEST(s2.section) AS s3
WHERE c.id = '<EMA_COMP_ID>'
  AND s3.code.coding[SAFE_OFFSET(0)].code = '200000029806';
```

The section tree is nested as the QRD template nests it (root → clause 4 → 4.4), which is why
there are three `UNNEST`es; confirm the column layout in the table's schema tab if the stream's
recursion depth has been changed since. **What the audience should see:** `narrative_sha256`
equal, character for character, to `<SECTION_HASH>` from window 1 — computed by BigQuery, over
bytes the store streamed, with no code of ours in between.

### Window 3 — the answer with its receipt

_(Items 1 and 1b — built, not deployed. Show the design note and the test names, not a mock.)_
The tool call this becomes is `get_section` with the document reference, the language, and
`sourceKey` `smpc.4.4`, returning the narrative verbatim with `narrativeDivSha256`, plus
`get_provenance` for the approver, the approver's role, and the source document hash — as
`test/query/acceptance.test.ts` "verbatim with citations" and "prove where it came from" assert.

What is real today, and worth showing instead: the approval behind that same section, from the
ledger and from the signed evidence.

```sql
SELECT run_id, completed_at, status, fidelity_status, manifest_hash, approval_hash,
       ingestion_source_hash
FROM `<PROJECT_ID>.ema_flow_ledger_<ENV>.transformation_runs`
WHERE run_id = '<RUN_ID_V1>';
```

Then open `gs://<PROJECT_ID>-ema-flow-<ENV>-evidence/runs/<RUN_ID_V1>/` in the console: the signed run
manifest, whose `manifestHash` is `<MANIFEST_HASH>`, and the per-section hashes in its ingestion
evidence — one of which is `<SECTION_HASH>` again. **What the audience should see:** the same
number for a third time, this time inside something a human approved and a KMS key signed.

## Scene 2 — change one word, watch the world know

**What to say.** "Version 2 of that same label differs by one sentence in section 4.4. Nobody
edited anything in the store: a new approved submission went through the same gate. Watch what
the systems do with it."

The seeded version 2 belongs to `synthetic-paracetamol`, and because the Type 2 document keeps
its identifier across versions, the transform derives the same EMA Bundle id — so this is a new
version of one resource, not a second document.

### The store holds both versions

```bash
BASE="https://healthcare.googleapis.com/v1/projects/<PROJECT_ID>/locations/<REGION>/datasets/<DATASET>/fhirStores/<STORE>/fhir"
TOKEN="$(gcloud auth print-access-token)"

# Every version of the document Bundle, newest first.
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/Bundle/<EMA_BUNDLE_ID>/_history"

# A specific version, read back exactly as it was written.
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/Bundle/<EMA_BUNDLE_ID>/_history/1"
```

**What the audience should see:** two entries, one id, `meta.versionId` 1 and 2, and the version 1
read returning the old sentence intact. Nothing was overwritten and nothing was lost.

### The ledger shows two approvals

```sql
SELECT run_id, completed_at, source_kind, fidelity_status,
       source_hash, manifest_hash, approval_hash
FROM `<PROJECT_ID>.ema_flow_ledger_<ENV>.transformation_runs`
WHERE run_id IN ('<RUN_ID_V1>', '<RUN_ID_V2>')
ORDER BY completed_at;
```

**What the audience should see:** two rows, `source_kind = document` in both, `fidelity_status =
passed` in both, and every hash different — because the content is different — while the
resource they describe is the same one.

### One sentence, one changed hash

```sql
SELECT
  c.meta.versionId AS version,
  s3.code.coding[SAFE_OFFSET(0)].code AS ema_code,
  TO_HEX(SHA256(s3.text.div)) AS narrative_sha256
FROM `<PROJECT_ID>.ema_flow_fhir_<ENV>.Composition` AS c,
  UNNEST(c.section) AS s1,
  UNNEST(s1.section) AS s2,
  UNNEST(s2.section) AS s3
WHERE c.id = '<EMA_COMP_ID>'
  AND s3.code.coding[SAFE_OFFSET(0)].code IN ('200000029805', '200000029806')
ORDER BY version, ema_code;
```

**What the audience should see:** section 4.3's hash identical across both versions and section
4.4's hash different. One sentence changed, and exactly one address changed with it. This is the
sentence to say here: "a diff over a PDF tells you a byte moved; this tells you which regulated
section of which version of which product changed, and nothing else did."

_(Item 1 — built, not deployed.)_ The tool version of this scene: `get_section` for `smpc.4.4`
at each version returns two different `narrativeDivSha256` values, and `verify_quote` with the
version 1 sentence answers match against version 1 and no-match against version 2. That is the
demonstration the design note asks for once the query service is deployed; until then the two
queries above make the same point with the systems that are deployed.

## Scene 3 — a question a regulator cannot ask a PDF

**What to say.** "Across the portfolio, which products name hepatic impairment in
contraindications? With PDFs, that is a person and an afternoon. Here it is a query, and it
returns citations rather than an opinion."

Section 4.3 is EMA code `200000029805`.

```sql
SELECT
  c.id AS composition_id,
  c.title,
  TO_HEX(SHA256(s3.text.div)) AS narrative_sha256
FROM `<PROJECT_ID>.ema_flow_fhir_<ENV>.Composition` AS c,
  UNNEST(c.section) AS s1,
  UNNEST(s1.section) AS s2,
  UNNEST(s2.section) AS s3
WHERE s3.code.coding[SAFE_OFFSET(0)].code = '200000029805'
  AND CONTAINS_SUBSTR(s3.text.div, 'hepatic impairment')
ORDER BY composition_id;
```

**What the audience should see:** exactly one of the three synthetic labels — Synthetic
Demoxetine — with the hash of the very sentence that matched. Then run it again without the
`CONTAINS_SUBSTR` line to show all three products' 4.3 sections are there and coded, so the
answer is a filter over structured content and not a search over text.

Say plainly what this is not: the narrative is still human-written regulated text; the query
found it because the section is coded, not because anything understood it.

_(Items 1 and 1b — built, not deployed.)_ The assistant version: the same question in English,
answered from `find_product` plus `get_section`, every quoted sentence followed by its product,
version, `sourceKey` and hash, and each quote re-checked through `verify_quote` after the answer
is composed. One honesty point for the room: `find_product` answers `truncated: true` whenever
the answer is shorter than the caller's entitlement holds — either documents went unsearched
(the scan horizon of 200, or the request's read budget) or more documents matched than `limit`
returns. So the assistant is never able to present a short list as a complete one. With three
entitled products and no small `limit` it will be false here. The assistant is instructed
never to say "no such product" when it is true. The promise is narrow and worth repeating exactly as
`docs/design/verifiable-answers.md` states it: every sentence presented as label content is
verbatim, hashed, and re-checked — not that the assistant is right.

## Closing

Three things were on screen and all three were the same number. The store versioned one
resource instead of accumulating documents. A portfolio question was answered by address rather
than by reading. That is what an ePI hub is for.

## What not to claim, in the room

- No GxP, Annex 11, or 21 CFR Part 11 compliance. This produces qualification-supporting
  evidence; validation is a separate exercise (`docs/validation/README.md`).
- Every product, every sentence, and every identifier in this demonstration is invented.
- The query service and the assistant are built and tested but not deployed; no live tenant
  has exercised them. When deployed, the assistant is an information-retrieval aid for trained
  staff — not a regulatory decision system.
- The fidelity check proves that published narrative matches the approved source document. It
  does not prove the source document is correct; a human approved that, and the Provenance
  resource says who.
