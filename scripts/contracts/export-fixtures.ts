import { mkdir, readdir, unlink, writeFile } from "node:fs/promises";
import path from "node:path";

import { format, resolveConfig } from "prettier";

import type { RunRequest } from "../../src/contracts/index.js";
import { loadEmaMapping } from "../../src/fhir/mapping.js";
import {
  SYNTHETIC_SUBMISSION_URI,
  createSyntheticSubmission,
} from "../../src/fixtures/synthetic-submission.js";
import { sha256 } from "../../src/lib/hash.js";

// Exports the four contract instances a Zone A re-implementation needs as its oracle: the
// canonical submission, its fidelity report, its extracted page text, and the run request that
// names it. They are synthetic by construction (src/fixtures/synthetic-submission.ts builds
// them from the synthetic Type 2 fixture and nothing else), so committing them leaks nothing.
//
// Deterministic like scripts/contracts/generate-schemas.ts: no clock, no randomness, Prettier
// formatting resolved from the repository config so `format:check` and `contracts:check` agree
// byte-for-byte. Every file is rewritten on every run, which is what scripts/ci/check-generated.mjs
// requires of a generator.

const output = path.resolve("test/fixtures/contracts");
await mkdir(output, { recursive: true });
const prettierOptions = { ...(await resolveConfig(output)), parser: "json" as const };

const mapping = await loadEmaMapping();
const { submission, fidelityReport, sourceText } = createSyntheticSubmission(mapping);

// A `document` run request naming the submission by location and hash, exactly as ADR 0002
// requires: by reference, never inline.
const runRequest: RunRequest = {
  source: "document",
  submissionRef: { uri: SYNTHETIC_SUBMISSION_URI, sha256: sha256(submission) },
};

const files: Record<string, unknown> = {
  "canonical-submission.json": submission,
  "fidelity-report.json": fidelityReport,
  "source-document-text.json": sourceText,
  "run-request.json": runRequest,
};

for (const [file, value] of Object.entries(files)) {
  await writeFile(
    path.join(output, file),
    await format(JSON.stringify(value, null, 2), prettierOptions),
  );
}

// A removed fixture must not leave its old file behind for a re-implementation to read.
for (const name of await readdir(output)) {
  if (name.endsWith(".json") && files[name] === undefined) {
    await unlink(path.join(output, name));
  }
}

console.log(`Exported ${Object.keys(files).length} contract fixtures in ${output}`);
