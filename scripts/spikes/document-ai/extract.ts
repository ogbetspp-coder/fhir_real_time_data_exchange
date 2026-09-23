// Part A/B step 2: send one PDF to the Document AI Layout Parser processor and record the raw
// response. Nothing of the document's text reaches stdout, a log line, or an error message —
// only a path, counts, and the processor version identifier.
//
// Usage (from the repository root):
//
//   export DOCUMENT_AI_PROCESSOR="projects/<p>/locations/eu/processors/<id>"
//   npx tsx scripts/spikes/document-ai/extract.ts [path/to/input.pdf]
//
// The response is written to .cache/spikes/document-ai/<sha256-of-pdf>.response.json, which is
// git-ignored. `DOCUMENT_AI_PROCESSOR` is the only name read for the processor; there is no
// alias, and scripts/spikes/document-ai/README.md uses the same name.

import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

import { DocumentProcessorServiceClient, type protos } from "@google-cloud/documentai";

const CACHE_DIR = path.join(".cache", "spikes", "document-ai");
const DEFAULT_PDF = path.join(CACHE_DIR, "synthetic-smpc.pdf");
const ADC_COMMAND = "gcloud auth application-default login";
const PROCESSOR_PATTERN = /^projects\/[^/]+\/locations\/([^/]+)\/processors\/[^/]+(\/.+)?$/;

type ProcessResponse = protos.google.cloud.documentai.v1.IProcessResponse;

class SpikeError extends Error {
  public constructor(
    message: string,
    public readonly hint?: string,
  ) {
    super(message);
    this.name = "SpikeError";
  }
}

function processorName(): { name: string; location: string } {
  const name = process.env.DOCUMENT_AI_PROCESSOR ?? "";
  if (name === "") {
    throw new SpikeError(
      "DOCUMENT_AI_PROCESSOR is not set",
      'export DOCUMENT_AI_PROCESSOR="projects/<project>/locations/eu/processors/<id>" (no processor is deployed; see scripts/spikes/document-ai/README.md, "Infrastructure")',
    );
  }
  const matched = PROCESSOR_PATTERN.exec(name);
  const location = matched?.[1];
  if (location === undefined) {
    throw new SpikeError(
      "DOCUMENT_AI_PROCESSOR is not a processor resource name",
      "expected projects/<project>/locations/<location>/processors/<id>",
    );
  }
  return { name, location };
}

// True for the failure modes that mean "there are no Application Default Credentials here",
// rather than "the call was rejected". Matched on the client library's own wording.
function looksLikeMissingCredentials(error: unknown): boolean {
  const message = error instanceof Error ? error.message : "";
  return (
    message.includes("Could not load the default credentials") ||
    message.includes("Could not refresh access token") ||
    message.includes("application default credentials") ||
    message.includes("GOOGLE_APPLICATION_CREDENTIALS")
  );
}

function errorLabel(error: unknown): string {
  if (error instanceof Error) {
    const code = (error as { code?: unknown }).code;
    return typeof code === "number" || typeof code === "string"
      ? `${error.name}/${String(code)}`
      : error.name;
  }
  return "unknown-error";
}

function pageCountOf(document: protos.google.cloud.documentai.v1.IDocument): number {
  const declared = document.pages?.length ?? 0;
  const spanned = (document.documentLayout?.blocks ?? []).reduce((highest, block) => {
    const span = block.pageSpan;
    const end = typeof span?.pageEnd === "number" ? span.pageEnd : 0;
    const start = typeof span?.pageStart === "number" ? span.pageStart : 0;
    return Math.max(highest, end, start);
  }, 0);
  return Math.max(declared, spanned);
}

function processorVersionId(document: protos.google.cloud.documentai.v1.IDocument): string {
  const revision = (document.revisions ?? []).find(
    (entry) => typeof entry.processor === "string" && entry.processor.length > 0,
  );
  const segments = (revision?.processor ?? "unknown-processor-version").split("/");
  return segments[segments.length - 1] ?? "unknown-processor-version";
}

async function main(): Promise<void> {
  const { name, location } = processorName();
  const pdfPath = process.argv[2] ?? DEFAULT_PDF;

  let content: Buffer;
  try {
    content = await readFile(pdfPath);
  } catch {
    throw new SpikeError(
      "input PDF could not be read",
      `generate it first: npx tsx scripts/spikes/document-ai/generate-pdf.ts (looked for ${pdfPath})`,
    );
  }
  const pdfSha256 = createHash("sha256").update(content).digest("hex");

  const client = new DocumentProcessorServiceClient({
    apiEndpoint: `${location}-documentai.googleapis.com`,
  });

  let response: ProcessResponse;
  try {
    [response] = await client.processDocument({
      name,
      rawDocument: { content, mimeType: "application/pdf" },
      skipHumanReview: true,
    });
  } catch (error) {
    if (looksLikeMissingCredentials(error)) {
      throw new SpikeError("Application Default Credentials are missing", ADC_COMMAND);
    }
    throw new SpikeError(`Document AI rejected the request (${errorLabel(error)})`);
  } finally {
    await client.close();
  }

  const document = response.document;
  if (document === undefined || document === null) {
    throw new SpikeError("Document AI returned no document");
  }

  await mkdir(CACHE_DIR, { recursive: true });
  const responsePath = path.join(CACHE_DIR, `${pdfSha256}.response.json`);
  await writeFile(responsePath, `${JSON.stringify(response)}\n`, "utf8");

  console.log(`responsePath=${responsePath}`);
  console.log(`pdfSha256=${pdfSha256}`);
  console.log(`pages=${pageCountOf(document)}`);
  console.log(`blocks=${(document.documentLayout?.blocks ?? []).length}`);
  console.log(`processorVersionId=${processorVersionId(document)}`);
}

try {
  await main();
} catch (error) {
  if (error instanceof SpikeError) {
    console.error(`extract.ts failed: ${error.message}`);
    if (error.hint !== undefined) console.error(`  ${error.hint}`);
  } else {
    console.error(`extract.ts failed: ${errorLabel(error)}`);
  }
  process.exitCode = 1;
}
