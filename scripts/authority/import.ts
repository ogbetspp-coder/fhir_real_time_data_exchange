import { randomUUID } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";

import { defaultFetcher } from "../../src/authority/fetch.js";
import { ImportRefusedError, importPublication } from "../../src/authority/import.js";
import type { ImportRequest } from "../../src/contracts/index.js";
import { loadEmaMapping } from "../../src/fhir/mapping.js";

// The producer (docs/design/authority-import-contract.md, D1): fetches an authority's document
// and List, runs the importer, and writes the submission, its page text and report, and copies
// of the fetched files. Its identity is not trusted: Zone B fetches and recomputes it all again.
//
//   tsx scripts/authority/import.ts --authority EMA --document <GUID> --index <GUID> \
//     --requested-by <principal> --bucket <gs://bucket/prefix> --out <directory>

const { values } = parseArgs({
  options: {
    authority: { type: "string" },
    document: { type: "string" },
    index: { type: "string" },
    "requested-by": { type: "string" },
    bucket: { type: "string" },
    out: { type: "string" },
  },
});
const read = (name: keyof typeof values): string => {
  const value = values[name];
  if (typeof value !== "string") throw new Error(`--${name} is required`);
  return value;
};
const authority = read("authority");
if (authority !== "EMA" && authority !== "synthetic") throw new Error("--authority EMA|synthetic");

const request: ImportRequest = {
  authority,
  documentId: read("document"),
  indexId: read("index"),
  language: "en",
};
const mapping = await loadEmaMapping();
const fetcher = defaultFetcher(mapping, authority === "synthetic");
const document = await fetcher.fetch(authority, { kind: "document", id: request.documentId });
const index = await fetcher.fetch(authority, { kind: "index", id: request.indexId });
const now = new Date().toISOString();
const out = read("out");
const bucket = read("bucket").replace(/\/$/u, "");
const stem = `${authority.toLowerCase()}-${request.documentId}`;
try {
  const result = importPublication(
    request,
    { document: document.bytes, index: index.bytes },
    mapping,
    {
      submissionId: randomUUID(),
      createdAt: now,
      extractionRunId: randomUUID(),
      serviceVersion: "scripts/authority/import.ts",
      requestedBy: read("requested-by"),
      requestedAt: now,
      sourceTextUri: `${bucket}/${stem}.pages.json`,
      fidelityReportUri: `${bucket}/${stem}.fidelity-report.json`,
    },
  );
  mkdirSync(out, { recursive: true });
  const write = (name: string, value: unknown): void => {
    writeFileSync(path.join(out, name), `${JSON.stringify(value, null, 2)}\n`);
  };
  write(`${stem}.submission.json`, result.submission);
  write(`${stem}.pages.json`, result.sourceText);
  write(`${stem}.fidelity-report.json`, result.fidelityReport);
  writeFileSync(path.join(out, `${stem}.document.json`), document.bytes);
  writeFileSync(path.join(out, `${stem}.list.json`), index.bytes);
  console.log(`imported ${stem} into ${out}`);
} catch (error) {
  if (error instanceof ImportRefusedError) {
    console.error(`refused at ${error.stage}: ${error.reason}`);
    process.exit(1);
  }
  throw error;
}
