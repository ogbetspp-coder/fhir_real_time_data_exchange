# Demonstration script: the verifiable label

- Status: written 2026-09-20 for the demonstration enablers (delivered, was roadmap item 1c)
- Related: `docs/design/verifiable-answers.md` (section "The demonstration"),
  `docs/design/epi-mcp-query-service.md`, `docs/adr/0002-two-trust-zones-and-canonical-submission.md`
- Data: synthetic throughout. Three invented products, no clinical content, every sentence says
  so in its own words.

The line the whole demonstration exists to land:

> FHIR gives every sentence in a label a stable address — product, version, section — and
> everything here hangs off that address. PDFs do not have addresses.

## What is real today, and what is not

Say this out loud at the start. It is the difference between a demonstration and a pitch.

| Shown                                                                             | Built?                                                                                                                                                                                                                                                                  |
| --------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Canonical submission, ingress gate, fidelity check, EMA transform                 | Yes — `src/`, run by the deployed worker                                                                                                                                                                                                                                |
| Validated resources in the Cloud Healthcare FHIR store                            | Yes                                                                                                                                                                                                                                                                     |
| Near-real-time BigQuery projection of those resources                             | Yes — the store's native ANALYTICS_V2 stream                                                                                                                                                                                                                            |
| Transformation ledger rows with approval and fidelity columns                     | Yes — `ema_flow_ledger_<ENV>.transformation_runs`                                                                                                                                                                                                                       |
| Signed evidence and a Provenance resource per approval                            | Yes                                                                                                                                                                                                                                                                     |
| MCP query tools (`find_product`, `get_section`, `verify_quote`, `get_provenance`) | Yes — deployed as `ema-flow-dev-query`; all four answered live 2026-09-21, one audit record per call                                                                                                                                                                    |
| Gemini Enterprise answering from those tools (assistant rollout step 1)           | Yes — custom MCP connector `epi verified labels`; a person asked for section 4.4 on 2026-09-21 and got the version 2 sentence verbatim                                                                                                                                  |
| The self-checking agent (re-verifies every quote, writes an `AgentTurnRecord`)    | **Deployed, post-check not yet seen live** — `agent/`, Agent Engine `reasoningEngines/6226059359072288768` (europe-west4), redeployed 2026-09-23 with the draft-hold fix (PR #98); it has answered live, but no live turn has yet shown `verify_quote` (roadmap item 1) |

Where a scene below has an assistant window, the Gemini Enterprise version works today through
the connector. The console-only equivalent is kept beside it because it is the version that
shows the hashes, and because it is the fallback if the room's network or the trial licence
misbehaves. The self-checking agent is deployed and registered, but its post-check has not yet
run in a live turn (roadmap item 1 is awaiting that turn). Until it has, the connector path
quotes the tool's text because the tool returns it verbatim, not because anything re-checks the
quote after the answer is composed. Say that distinction, do not blur it.

**The assistant cannot drift to the public web in the demonstration app.** Asked the
paracetamol question on 2026-09-21 with web search on and the connector not selected, Gemini
answered confidently from third-party labels found on the web and never called the service. Web
grounding was then switched off for the whole app (`webGroundingType =
WEB_GROUNDING_TYPE_DISABLED` on the default assistant), so it is a setting of the environment
and not a step for the presenter to remember. Still select `epi verified labels` and name it in
the question ("Using epi verified labels, …"), because that is what makes Gemini call the tools
rather than answer from nothing. The wrong answer is worth showing first, deliberately, in a
different tool — see Scene 0.

## Placeholders

Fill these in before the meeting; every command and query below uses them verbatim.

| Placeholder       | Where it comes from                                                             |
| ----------------- | ------------------------------------------------------------------------------- |
| `<PROJECT_ID>`    | the demonstration project                                                       |
| `<ENV>`           | `dev`, `staging`, …                                                             |
| `<REGION>`        | `terraform -chdir=infra output -raw region`                                     |
| `<DATASET>`       | `ema-flow-<ENV>-fhir-record`                                                    |
| `<STORE>`         | `ema-flow-<ENV>-validated-r5` (the validated target store)                      |
| `<EMA_BUNDLE_ID>` | `targetBundleId` printed by the seeding script for `synthetic-paracetamol`      |
| `<EMA_COMP_ID>`   | `Composition.id` inside that Bundle                                             |
| `<MANIFEST_HASH>` | `manifestHash` printed by the seeding script                                    |
| `<SECTION_HASH>`  | SHA-256 of the section 4.4 narrative `div`, recomputed in front of the audience |
| `<RUN_ID_V1/V2>`  | `runId` printed for version 1 and version 2 of the same product                 |

## Before the meeting: the set is seeded; do not re-seed

The demonstration set is already in `dev`: seeded once on 2026-09-21 through the real document
path, into a store rebuilt first. The one-time deploy order that preceded it (re-ingest after
the worker began recording the approver's role) is spent, and deploying between seeding and
demonstrating is safe: the smoke run publishes `synthetic-smoketest`, which no entitlement names.

A document with two approved versions has two `Provenance` resources. `get_provenance` and
`get_section` answer with the most recently _written_ approval, and state it only for the
document's current version; a request naming an earlier version gets no approval. The rule, and
why it is write order rather than the approval date, is in
`docs/design/epi-mcp-query-service.md` ("An approval is stated only for the current version").

**Do not re-seed without rebuilding the store.** A second seed adds a second `Provenance` per
document rather than replacing the first, because the resource id is derived from the
submission id, and publishes the same content again as further versions. The section below is
for a rebuilt store or a new environment.

Scenes 1 and 2 as written below use the console and BigQuery and do not depend on any of this;
it matters for the `get_provenance` parts of the assistant scenes.

## Seed the set

One command, once per environment (or per rebuilt store). It writes three synthetic products at
version 1 and then the first product at version 2, each through the ordinary `document` run path:
Cloud Storage hand-off, ingress gate, fidelity check, transform, validation, store write, ledger
row.

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

## Scene 0 — the wrong answer first

Sixty seconds, before anything else, in **public Gemini** (`gemini.google.com`, or any
consumer assistant) — the tool the room's staff are already using without anyone's permission.
Ask: _"Find the synthetic paracetamol label and show me section 4.4."_ The demonstration app
can no longer produce this answer, because its web grounding is off; that is the point. On
2026-09-21, with web search on, Gemini answered with a confident, well-formatted section 4.4
assembled from real paracetamol labels by other companies in other countries — hepatotoxicity,
HAGMA, skin reactions, all of it — and none of it from the label that was asked about. It
looked authoritative. It was wrong about the one thing that mattered: which document it came
from.

Then the Gemini Enterprise app, `epi verified labels` selected, the same question. One
sentence, quoted exactly, with the bundle id and version id. **What the audience should see:**
the difference between an answer that sounds right and an answer that can be checked. Everything
after this scene is about how the second kind is produced.

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

In the Gemini Enterprise web app, connector selected: _"Using epi verified labels, show me
section 4.4 of the synthetic paracetamol label and tell me who approved it."_ Gemini calls
`get_section` (the narrative verbatim with `narrativeDivSha256`) and `get_provenance` (the
approver, the approver's role, the source document hash). Proved 2026-09-21: the answer was the
version 2 sentence word for word, with the bundle id and version id. The audit records for
those two calls carry the asking person's own identity, which is the point to make.

Then show the same fact from the systems of record, because the hashes are what the room can
check: the approval behind that same section, from the ledger and from the signed evidence.

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

The tool version of this scene, proved live 2026-09-21: `get_section` for `smpc.4.4` at each
version returns two different `narrativeDivSha256` values, and `verify_quote` with the version
2 sentence answers `match` (offsets 54–109) while the version 1 sentence it replaced answers
`no-match`. In the assistant: _"Using epi verified labels, is this sentence on the paracetamol
label: '<version 1 sentence>'?"_ — the answer is no, and that is the scene.

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

The assistant version, in Gemini Enterprise today: the same question in English, answered from
`find_product` plus `get_section`, each quote carrying its product, version and `sourceKey`. The
connector path does **not** re-check each quote through `verify_quote` after the answer is composed.
The self-checking agent does — it is deployed, but its post-check has not yet been seen in a live
turn, so do not claim it until the audit record shows `verify_quote` under the turn id — and that is
the difference between "Gemini happened to quote correctly" and "the system refused to emit a quote
it could not verify". Say which one is on screen. One honesty point for the room: `find_product`
answers `truncated: true` whenever the answer is shorter than the caller's entitlement holds —
either documents went unsearched (the scan horizon of 200, or the request's read budget) or more
documents matched than `limit` returns. So the assistant is never able to present a short list as a
complete one. With three entitled products and no small `limit` it will be false here. The assistant
is instructed never to say "no such product" when it is true. The promise is narrow and worth
repeating exactly as `docs/design/verifiable-answers.md` states it: every sentence presented as
label content is verbatim, hashed, and re-checked — not that the assistant is right.

## Closing

Three things were on screen and all three were the same number. The store versioned one
resource instead of accumulating documents. A portfolio question was answered by address rather
than by reading. That is what an ePI hub is for.

## What not to claim, in the room

- No GxP, Annex 11, or 21 CFR Part 11 compliance. This produces qualification-supporting
  evidence; validation is a separate exercise (`docs/validation/README.md`).
- Every product, every sentence, and every identifier in this demonstration is invented.
- The query service, the connector and the agent are deployed in the owner's own `dev` tenant
  only, answering synthetic labels; no client tenant has exercised them, and the agent's
  post-check is still awaiting its first live turn. The assistant is an information-retrieval
  aid for trained staff — not a regulatory decision system.
- The fidelity check proves that published narrative matches the approved source document. It
  does not prove the source document is correct; a human approved that, and the Provenance
  resource says who.
