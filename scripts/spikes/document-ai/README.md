# Document AI extractor spike

Runs the spike described in `docs/design/extractor-spike.md`: Part A is a controlled round
trip on a synthetic SmPC PDF; Part B is a counts-only characterisation of one real, publicly
published EMA SmPC PDF. This spike's code is disposable; what survives it is the written
verdict (appended to `docs/design/extractor-spike.md`), any amendment to the extractor
contract or `docs/fidelity-normalization.md`, and the recorded Document AI response for the
synthetic PDF committed as a test fixture.

## Human steps (do these first)

1. **Application Default Credentials.** The Document AI client libraries need ADC; being
   logged in with the `gcloud` CLI is not enough on its own.

   ```
   gcloud auth application-default login
   ```

2. **Approve Part B.** Part B runs one real, publicly published EMA SmPC PDF through the same
   processor and adapter and reports counts only (see "What gets written" below) — no narrative
   from it is written anywhere. This is a judgment call per the design note, so nothing fetches
   that PDF for you: Part B runs only when you have fetched it by hand into
   `.cache/spikes/document-ai/` and passed its path explicitly (see below). Skipping Part B runs
   Part A only; the verdict is weaker without Part B but Part A still stands on its own.

## Infrastructure

The Document AI Layout Parser processor is managed in `infra/documentai.tf`, in the multi-region
`eu` (Document AI does not offer per-region processors such as `europe-west4`). It is created by
the same `terraform apply` as everything else (`scripts/gcp/deploy.sh`).

Get the processor's full resource name one of two ways:

```
# after `terraform apply` in this environment
terraform -chdir=infra output -raw document_ai_processor

# or directly from Document AI, without going through Terraform state
gcloud documentai processors list --location=eu
```

Export it for the scripts below. `DOCUMENT_AI_PROCESSOR` is the only name `extract.ts` reads:

```
export DOCUMENT_AI_PROCESSOR="$(terraform -chdir=infra output -raw document_ai_processor)"
```

## Part A — controlled round trip

Run in order from the repository root:

```
npx tsx scripts/spikes/document-ai/generate-pdf.ts
npx tsx scripts/spikes/document-ai/extract.ts
npx tsx scripts/spikes/document-ai/report.ts
```

`generate-pdf.ts` builds the synthetic SmPC PDF from `src/fixtures/synthetic-submission.ts`
deterministically; `extract.ts` sends it to the Document AI processor named by
`DOCUMENT_AI_PROCESSOR` and records the raw response; `report.ts` adapts that response to
`SourceDocumentText`, runs `verifyNarrativeFidelity`, and prints the per-section pass/fail with
the failing rule where relevant.

## Part B — real-world characterisation

Requires the human approval step above. Nothing here fetches anything: you download the PDF by
hand into `.cache/spikes/document-ai/` (git-ignored, never committed) and then name it
explicitly on both commands.

```
# 1. by hand: save one public EMA SmPC PDF as .cache/spikes/document-ai/real-smpc.pdf
npx tsx scripts/spikes/document-ai/extract.ts .cache/spikes/document-ai/real-smpc.pdf
npx tsx scripts/spikes/document-ai/report.ts --characterise .cache/spikes/document-ai/real-smpc.pdf
```

`extract.ts` records the raw response as `<sha256-of-pdf>.response.json` in the same directory;
`--characterise` reads back the response recorded for exactly that PDF and fails if there is
none. `--characterise` has no default path: it characterises the PDF you name and nothing else.

The report diffs Document AI's text against the PDF's own embedded text layer (extracted
independently via pdf.js) and prints counts only: pages; blocks by type; header/footer
classification; line-end hyphens and how many are ambiguous; ligature glyphs; rejected
characters; table/cell structure; and character-level agreement with the embedded text layer,
both as a multiset ratio and as an order-sensitive one. No narrative text, and no fragment of
it, is written to any output, log, or fixture — only integers and hashes leave the script.

## What gets written where

- `.cache/spikes/document-ai/` — the generated synthetic PDF, the hand-fetched Part B PDF, the
  raw Document AI response(s), and any intermediate working files. Already covered by the
  repository's `.cache/` ignore rule; **nothing under it is committed.**
- `docs/design/extractor-spike.md` — the one artifact this spike is meant to produce: the
  go / conditional / no-go verdict, appended by hand once the numbers are in.
- `test/spikes/document-ai-adapter.test.ts` plus its recorded fixture — the **synthetic** PDF's
  Document AI response only, checked in as the regression fixture for the eventual real parser.
  The real-world PDF from Part B, and Document AI's response to it, are never committed.

## Verdict rubric

See `docs/design/extractor-spike.md` ("Verdict rubric") for what Go / Conditional / No-go each
require. The adapter (`adapt.ts`) is reviewed adversarially — attacking the "no character
changed" and "no narrative in any output" claims specifically — before the verdict is written.
