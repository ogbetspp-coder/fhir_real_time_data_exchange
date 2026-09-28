#!/usr/bin/env node
// The validator image's offline start, judged (cloudbuild.images.yaml, validator-offline-verdict):
//
//   node scripts/ci/validator-offline.mjs <container log>
//
// Exits 1 with the reasons, and the lines that show them, when offlineStartVerdict in
// validator-pins.mjs refuses the log; otherwise prints the configuration lines it proved. Node
// built-ins only: it runs in the pinned node image with nothing installed.
import { readFileSync } from "node:fs";

import { offlineStartVerdict } from "./validator-pins.mjs";

const [logPath] = process.argv.slice(2);
if (logPath === undefined) {
  console.error("Usage: node scripts/ci/validator-offline.mjs <container log>");
  process.exit(2);
}
const lines = readFileSync(logPath, "utf8").split(/\r?\n/);
const verdict = offlineStartVerdict(lines);
for (const line of [...verdict.installs, ...verdict.other]) console.error(line.slice(0, 200));
for (const failure of verdict.failures) console.error(failure);
if (!verdict.ok) {
  if (!lines.some((line) => line.includes("FHIR Validator HTTP Service started"))) {
    for (const line of lines.slice(-40)) console.error(line.slice(0, 200));
  }
  process.exit(1);
}
for (const line of verdict.evidence) console.log(line);
