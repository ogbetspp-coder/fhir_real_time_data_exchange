import { spawnSync } from "node:child_process";
import {
  chmodSync,
  existsSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterAll, describe, expect, it } from "vitest";

// Every operator script refuses a command line it does not take, and answers --help, before it
// touches anything (audit B08 follow-up, review round 3). Until then api-trim.sh, deploy-identity.sh
// and key-guard.sh, and agent/deploy/grant-invoker.sh and register.sh, read any argument but
// --check as "apply": `bash scripts/gcp/api-trim.sh --help` would have disabled up to nine APIs.
// Each script runs here with stand-ins for every cloud CLI on PATH, each recording its call, and no
// project or environment set: a refusal or a usage text must come before any of them.

const scripts = [
  ...readdirSync("scripts/gcp").map((name) => `scripts/gcp/${name}`),
  ...readdirSync("agent/deploy").map((name) => `agent/deploy/${name}`),
].filter((file) => file.endsWith(".sh") && !file.endsWith("/common.sh"));

const stub = mkdtempSync(path.join(tmpdir(), "operator-arguments-"));
afterAll(() => rmSync(stub, { recursive: true, force: true }));
for (const cli of ["gcloud", "bq", "gsutil", "terraform", "curl"]) {
  writeFileSync(path.join(stub, cli), `#!/bin/sh\necho "${cli} $*" >>"${stub}/calls"\nexit 3\n`);
  chmodSync(path.join(stub, cli), 0o755);
}

function run(script: string, args: string[], cwd = process.cwd()) {
  rmSync(path.join(stub, "calls"), { force: true });
  const result = spawnSync("bash", [path.relative(cwd, script), ...args], {
    cwd,
    encoding: "utf8",
    env: { PATH: `${stub}:${process.env.PATH ?? ""}`, HOME: stub },
  });
  return { ...result, called: existsSync(path.join(stub, "calls")) };
}

describe("every operator script's command line", () => {
  it("covers the scripts it is about", () => {
    expect(scripts).toEqual(
      expect.arrayContaining([
        "scripts/gcp/api-trim.sh",
        "scripts/gcp/deploy-identity.sh",
        "scripts/gcp/key-guard.sh",
        "scripts/gcp/deploy.sh",
        "agent/deploy/grant-invoker.sh",
        "agent/deploy/register.sh",
        "agent/deploy/authorization.sh",
      ]),
    );
  });

  it.each(scripts)("%s answers --help with its usage, touching nothing", (script) => {
    const result = run(script, ["--help"]);
    expect([result.status, result.called, result.stderr]).toEqual([0, false, ""]);
    expect(result.stdout.trim()).not.toBe("");
  });

  it.each(scripts)("%s refuses an argument it does not take, touching nothing", (script) => {
    for (const args of [["--no-such-option"], ["--help", "extra"]]) {
      const result = run(script, args);
      expect([args, result.status, result.called]).toEqual([args, 2, false]);
      expect(result.stderr).toContain("Usage: bash");
    }
  });

  it("fail when they stop early, under bash 3.2 as under bash 5", () => {
    // Under macOS's /bin/bash 3.2, which runs the owner's `bash scripts/...`, any EXIT trap turns
    // an unset variable or a failed ${VAR:?} into exit 0. Every script's trap is common.sh's
    // ema_flow_on_exit, which fails a run that did not reach ema_flow_finish.
    const shells = ["bash", ...(existsSync("/bin/bash") ? ["/bin/bash"] : [])];
    const prelude =
      "set -euo pipefail\nsource scripts/gcp/common.sh\ncleanup() { :; }\nema_flow_on_exit cleanup\n";
    for (const shell of shells) {
      for (const [ending, status] of [
        ['echo "$NOT_SET"\nema_flow_finish', 1],
        ['x="${NOT_SET:?needed}"\nema_flow_finish', 1],
        ["echo done\nema_flow_finish", 0],
        ["false", 1],
      ] as const) {
        const result = spawnSync(shell, ["-c", `${prelude}${ending}`], { encoding: "utf8" });
        expect([shell, ending, result.status]).toEqual([shell, ending, status]);
      }
    }
    for (const script of [...scripts, "scripts/gcp/common.sh"]) {
      const text = readFileSync(script, "utf8");
      // No trap of their own on EXIT, only common.sh's; and each that sets one finishes.
      const own = text.split("\n").filter((line) => /^\s*trap .* EXIT$/.test(line));
      expect([script, own]).toEqual([script, []]);
      if (/^\s*ema_flow_on_exit \w+$/m.test(text)) {
        expect([script, /^ema_flow_finish$/m.test(text)]).toEqual([script, true]);
        expect([script, /^\s*exit 0\b/m.test(text)]).toEqual([script, false]);
      }
    }
  });

  it.each(scripts)("%s answers --help run from its own directory", (script) => {
    // Review of the follow-up: `cd scripts/gcp && bash deploy.sh --help` named itself relative to
    // a directory the script had already left, and failed.
    const result = run(script, ["--help"], path.dirname(path.resolve(script)));
    expect([result.status, result.called, result.stderr]).toEqual([0, false, ""]);
    expect(result.stdout.trim()).not.toBe("");
  });

  it.each(scripts)("%s never echoes an argument it refuses, which may be a secret", (script) => {
    // An OAuth client secret was exposed on a command line on 2026-09-22. The shapes are built at
    // run time, so this file holds no string a secret scanner reads as a credential.
    const body = (length: number) => "x".repeat(length);
    const secrets = [
      ["GOCSPX", body(28)].join("-"),
      ["ya29", body(20)].join("."),
      ["1", "", body(20)].join("/"),
      `--token=${Buffer.from(body(30)).toString("base64")}`,
    ];
    const result = run(script, secrets);
    expect([result.status, result.called]).toEqual([2, false]);
    for (const secret of secrets) expect(result.stderr).not.toContain(secret);
    expect(result.stderr).toContain("argument 1 (");
  });

  it("refuses a --secret-file that does not exist, before any call", () => {
    const result = run("agent/deploy/authorization.sh", ["--secret-file", "/no/such/secret.txt"]);
    expect([result.status, result.called]).toEqual([2, false]);
    expect(result.stderr).not.toContain("/no/such/secret.txt");
  });

  it("names the Python deployer's own parser, which exits 0 on --help and 2 on anything else", () => {
    // agent/deploy/deploy_agent_engine.py needs the agent's environment to import; argparse
    // gives it the same contract, and it is not run here.
    expect(readFileSync("agent/deploy/deploy_agent_engine.py", "utf8")).toMatch(
      /parser\.parse_args\(argv\[1:\]\)/,
    );
  });
});
