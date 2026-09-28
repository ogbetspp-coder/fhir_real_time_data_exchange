import { spawnSync } from "node:child_process";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

// `scripts/dev/run-pipeline.ts` is a script with a top-level body, so it is exercised the way an
// operator meets it: by running it. `gcloud` is a stand-in on PATH that describes a worker with
// the environment each case gives it, and the run is a dry run (the script forces DRY_RUN unless
// --persist), so nothing here reaches Google.

const TSX = path.resolve("node_modules/.bin/tsx");
const SCRIPT = path.resolve("scripts/dev/run-pipeline.ts");

let bin: string;
let runs = 0;

beforeAll(() => {
  bin = mkdtempSync(path.join(tmpdir(), "dev-run-pipeline-"));
  const gcloud = path.join(bin, "gcloud");
  writeFileSync(gcloud, '#!/bin/sh\ncat "$FAKE_SERVICE_DESCRIPTION"\n');
  chmodSync(gcloud, 0o755);
});

afterAll(() => {
  rmSync(bin, { recursive: true, force: true });
});

type Result = { status: number; stdout: string; stderr: string };

function run(deployed: Record<string, string>, args: string[]): Result {
  runs += 1;
  const description = path.join(bin, `service-${runs.toString()}.json`);
  const env = Object.entries({ NODE_ENV: "test", ...deployed }).map(([name, value]) => ({
    name,
    value,
  }));
  writeFileSync(
    description,
    JSON.stringify({ spec: { template: { spec: { containers: [{ env }] } } } }),
  );
  const result = spawnSync(TSX, [SCRIPT, ...args], {
    encoding: "utf8",
    env: {
      ...process.env,
      PATH: `${bin}${path.delimiter}${process.env.PATH ?? ""}`,
      FAKE_SERVICE_DESCRIPTION: description,
      GOOGLE_APPLICATION_CREDENTIALS: path.resolve("test/ci/no-such-credentials.json"),
    },
  });
  return { status: result.status ?? -1, stdout: result.stdout, stderr: result.stderr };
}

describe("scripts/dev/run-pipeline.ts", () => {
  it("names the product it sent as the manifest's source, and takes its usage line's product", () => {
    const result = run({ ALLOW_SYNTHETIC_SOURCES: "true" }, [
      "--product",
      "synthetic-demoxetine",
      "--version",
      "2",
    ]);
    expect(result.stderr).not.toMatch(/FAILED|must be one of/);
    expect(result.status).toBe(0);
    expect(result.stdout).toContain('"resource": "fixture:synthetic-demoxetine"');
  });

  it("refuses a fixture run against a deployment that enables only document", () => {
    const result = run({}, []);
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("FAILED Error: Run source is disabled");
  });
});
