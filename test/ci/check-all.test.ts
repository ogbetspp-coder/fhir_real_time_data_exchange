import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

// scripts/check-all.sh claims to run every gate .github/workflows/ci.yml runs. This holds it to
// that: every `run:` command in the workflow must appear verbatim in the script, except the two
// that are deliberately left to the caller or to CI.

const workflow = readFileSync(".github/workflows/ci.yml", "utf8");
const script = readFileSync("scripts/check-all.sh", "utf8");

// Installing Node dependencies is the caller's `npm ci`; the official HL7 validator needs Java
// and ~200 MB of downloads and stays its own CI job (the script's header says so).
// A local checkout already has origin/main; CI's shallow checkout fetches the importer lock's
// base. The renderer image needs Docker and ~200 MB of downloads, and is its own CI job too, with
// the step that decides whether it runs. So is the build of the deployed images (audit B07).
const NOT_RUN_LOCALLY = new Set([
  "npm ci --no-audit --no-fund",
  "npm ci --no-audit --no-fund --engine-strict",
  "bash scripts/ci/build-images.sh",
  "npm run validate:official",
  "bash scripts/ci/lock-base.sh",
  'node scripts/ci/renderer-inputs.mjs >> "$GITHUB_OUTPUT"',
  "npm run renderer:image",
  "npm run renderer:smoke",
  "npm run renderer:record",
  "npm run renderer:check",
  "npm run renderer:fonts",
  "npm run renderer:boxes",
  "npm run renderer:drawings",
]);

const commands = [...workflow.matchAll(/^[ \t]+run:[ \t]*(\S.*)$/gm)].map((match) =>
  (match[1] ?? "").trim(),
);

describe("scripts/check-all.sh", () => {
  it("finds the CI steps it is compared with", () => {
    expect(commands.length).toBeGreaterThan(10);
    for (const command of NOT_RUN_LOCALLY) expect(commands).toContain(command);
  });

  it("runs every command the CI workflow runs", () => {
    const missing = commands.filter(
      (command) => !NOT_RUN_LOCALLY.has(command) && !script.includes(command),
    );
    expect(missing).toEqual([]);
  });

  it("does not run the steps it documents as left out", () => {
    expect(script).not.toMatch(/^\s*npm ci\b/m);
    expect(script).not.toMatch(/^\s*npm run validate:official\b/m);
    expect(script).not.toMatch(/^\s*npm run renderer:/m);
  });
});
