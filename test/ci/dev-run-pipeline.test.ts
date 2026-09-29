import { spawnSync } from "node:child_process";
import { chmodSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  cleanHead,
  localEnvironment,
  outsideRepository,
  REPOSITORY_ROOT,
  RUNTIME_VARIABLES,
} from "../../scripts/dev/local-runtime.js";

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

function run(deployed: Record<string, string>, args: string[], cwd = process.cwd()): Result {
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
    cwd,
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

  // Review of #148, M1: the deployed worker names its own commit, images and revision. A local run
  // copying them would sign a manifest naming code and images that did not run.
  it("names this checkout in the manifest, never the deployment's commit, images or revision", () => {
    const deployedRuntime = {
      GIT_COMMIT: "d".repeat(40),
      IMAGE_DIGEST: `sha256:${"e".repeat(64)}`,
      VALIDATOR_IMAGE_DIGEST: `sha256:${"f".repeat(64)}`,
      WORKFLOW_REVISION: "deployed-workflow-7",
      K_REVISION: "ema-flow-dev-worker-00042-abc",
    };
    const result = run({ ALLOW_SYNTHETIC_SOURCES: "true", ...deployedRuntime }, []);
    expect(result.stderr).not.toMatch(/FAILED/);
    expect(result.status).toBe(0);
    for (const value of Object.values(deployedRuntime)) expect(result.stdout).not.toContain(value);
    // The answer is the one indented JSON object; the log lines around it are one line each.
    const printed = JSON.parse(/^\{\n[\s\S]*?\n\}$/m.exec(result.stdout)?.[0] ?? "{}") as {
      runtime: Record<string, string>;
    };
    expect(printed.runtime).toEqual({
      sourceCommit: cleanHead() ?? "development",
      imageDigest: "development",
      validatorImageDigest: "development",
      workflowRevision: "development",
    });
  });

  // The deployed values are never read, so one the local configuration would refuse is no reason
  // not to run.
  it("runs whatever the deployment's runtime values are", () => {
    const result = run({ ALLOW_SYNTHETIC_SOURCES: "true", GIT_COMMIT: "local" }, []);
    expect(result.stderr).not.toMatch(/FAILED/);
    expect(result.status).toBe(0);
  });
});

describe("scripts/dev/local-runtime.ts", () => {
  const deployed = new Map<string, string>([
    ["GOOGLE_CLOUD_PROJECT", "p"],
    ...RUNTIME_VARIABLES.map((name): [string, string] => [name, "deployed"]),
  ]);

  it("drops every variable that names what ran, and names the local commit when there is one", () => {
    expect(localEnvironment(deployed, undefined)).toEqual({ GOOGLE_CLOUD_PROJECT: "p" });
    expect(localEnvironment(deployed, "a".repeat(40))).toEqual({
      GOOGLE_CLOUD_PROJECT: "p",
      GIT_COMMIT: "a".repeat(40),
    });
  });

  it("names HEAD only for a clean checkout, and nothing without git", () => {
    const head = "0123456789abcdef0123456789abcdef01234567";
    const answers =
      (status: string) =>
      (args: string[]): string =>
        args[2] === "status" ? status : `${head}\n`;
    expect(cleanHead(answers(""))).toBe(head);
    expect(cleanHead(answers(" M src/pipeline.ts\n"))).toBeUndefined();
    expect(cleanHead(answers("?? new.ts\n"))).toBeUndefined();
    expect(
      cleanHead(() => {
        throw new Error("not a git repository");
      }),
    ).toBeUndefined();
    expect(cleanHead((args) => (args[2] === "status" ? "" : "not-a-commit"))).toBeUndefined();
  });

  // Review of #148, round 2, L-2: the commit named is the repository of the code that runs, not
  // whatever repository the current directory is in.
  it("asks git about the scripts' own repository, wherever it is run from", () => {
    const asked: string[][] = [];
    cleanHead((args) => {
      asked.push(args);
      return args.includes("status") ? "" : `${"a".repeat(40)}\n`;
    });
    expect(asked).toEqual([
      ["-C", REPOSITORY_ROOT, "status", "--porcelain", "--untracked-files=all"],
      ["-C", REPOSITORY_ROOT, "rev-parse", "HEAD"],
    ]);
    expect(realpathSync(REPOSITORY_ROOT)).toBe(realpathSync(process.cwd()));
  });

  it("runs only from the repository's root, whose mapping and locks it reads", () => {
    expect(outsideRepository()).toBeUndefined();
    expect(outsideRepository(path.join(REPOSITORY_ROOT, "scripts"))).toMatch(/repository's root/);
    expect(outsideRepository(path.join(bin, "no-such-directory"))).toMatch(/repository's root/);
    const elsewhere = run({ ALLOW_SYNTHETIC_SOURCES: "true" }, [], bin);
    expect(elsewhere.status).toBe(1);
    expect(elsewhere.stderr).toContain("Not running: run this from the repository's root");
  });
});
