import { readFileSync } from "node:fs";
import { parseArgs } from "node:util";

import { copiesFetcher, verifyAuthorityImport } from "../../src/authority/gate.js";
import { SubmissionRejectedError } from "../../src/contracts/index.js";
import { loadEmaMapping } from "../../src/fhir/mapping.js";

// Re-verifies a recorded authority import from copies of what Zone B fetched, reading no network
// (docs/design/authority-import-contract.md, D1). Run it with the image the run's manifest names
// (runtime.imageDigest), which contains the importer version that ran.
//
//   tsx scripts/authority/verify-import.ts --submission s.json --pages p.json --report r.json \
//     --document document.json --index list.json --fetched-at <ISO time> [--synthetic]

const { values } = parseArgs({
  options: {
    submission: { type: "string" },
    pages: { type: "string" },
    report: { type: "string" },
    document: { type: "string" },
    index: { type: "string" },
    "fetched-at": { type: "string" },
    synthetic: { type: "boolean", default: false },
  },
});
const read = (name: keyof typeof values): string => {
  const value = values[name];
  if (typeof value !== "string") throw new Error(`--${name} is required`);
  return value;
};

const mapping = await loadEmaMapping();
const fetchedAt = read("fetched-at");
try {
  const result = await verifyAuthorityImport(
    {
      submission: JSON.parse(readFileSync(read("submission"), "utf8")) as unknown,
      sourceText: JSON.parse(readFileSync(read("pages"), "utf8")) as unknown,
      fidelityReport: JSON.parse(readFileSync(read("report"), "utf8")) as unknown,
    },
    mapping,
    { allowSyntheticSources: values.synthetic, dryRun: true },
    copiesFetcher({
      document: { url: "copy:document", bytes: readFileSync(read("document")), fetchedAt },
      index: { url: "copy:index", bytes: readFileSync(read("index")), fetchedAt },
    }),
  );
  console.log(`verified: importer ${result.importerVersion} reproduces the recorded submission`);
} catch (error) {
  if (error instanceof SubmissionRejectedError) {
    console.error(`refused: ${error.issues.join("; ")}`);
    process.exit(1);
  }
  throw error;
}
