import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

// Which merges deploy (docs/foundations.md, E2). A merge that touches only paths nothing deployed
// is built from skips the deploy. The failure this guards against is the quiet one: a path that
// does reach the deployed services added to the ignore list, so a real change merges and never
// deploys. Every path the images or the deploy read must stay out of it.

const workflow = readFileSync(".github/workflows/deploy.yml", "utf8");
const ignored = [
  ...(/^ {4}paths-ignore:\n((?: {6}- .+\n)+)/m.exec(workflow)?.[1] ?? "").matchAll(/- "(.+)"/g),
].map((match) => match[1] ?? "");

// A path is deployable if an image copies it or the deploy runs it.
const deployable = [
  "src/pipeline.ts",
  "fhir/mappings/cap-smpc-en.json",
  "fhir/validator-packages.lock",
  "contracts/generated/canonical-submission.schema.json",
  "infra/main.tf",
  "scripts/gcp/deploy.sh",
  "scripts/fhir/select-import-resources.mjs",
  "workflows/epi-pipeline.yaml",
  "Dockerfile",
  "Dockerfile.validator",
  "fhir/standards.lock.json",
  "cloudbuild.images.yaml",
  "package.json",
  "package-lock.json",
  "tsconfig.build.json",
  ".github/workflows/deploy.yml",
];

function matches(pattern: string, file: string): boolean {
  const regex = new RegExp(
    "^" +
      pattern
        .replace(/[.+^${}()|[\]\\]/g, "\\$&")
        .replace(/\*\*\//g, "%DIRS%")
        .replace(/\*\*/g, "%ANY%")
        .replace(/\*/g, "[^/]*")
        .replaceAll("%DIRS%", "(?:.*/)?")
        .replaceAll("%ANY%", ".*") +
      "$",
  );
  return regex.test(file);
}

describe("the deploy trigger", () => {
  it("skips merges that touch only documentation, tests, the agent, Zone A, the QRD registry or assistant settings", () => {
    expect(ignored.length).toBeGreaterThan(5);
    for (const file of [
      "docs/foundations.md",
      "README.md",
      "test/ci/x.test.ts",
      "agent/src/a.py",
      "qrd/registry/cap-smpc-en-10.4.json",
      "qrd/sources.lock.json",
      "labels/ema-epi/sources/brukinsa-smpc-en.json",
      "labels/ema-epi/checks/brukinsa-smpc-en.json",
      ".claude/settings.json",
      ".claude/hooks/shell_guard.py",
      ".cursor/hooks.json",
      ".cursor/rules/fhir-safety.mdc",
    ]) {
      expect([file, ignored.some((pattern) => matches(pattern, file))]).toEqual([file, true]);
    }
  });

  it("never skips a path the deployed services are built from", () => {
    for (const file of deployable) {
      expect([file, ignored.some((pattern) => matches(pattern, file))]).toEqual([file, false]);
    }
  });
});
