import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

// The deploy's smoke run judges the worker's answer with the Python verdict in phase_smoke
// (scripts/gcp/deploy.sh), run here exactly as written: 0 persisted, 3 skipped, 1 failed. With
// approval enforcement on, an unsigned fixture run is refused (docs/design/approval.md), and the
// smoke run skips rather than fail the deploy; any other refusal still fails it.

const deploy = readFileSync("scripts/gcp/deploy.sh", "utf8");
const verdict =
  /python3 - "\$http_code" "\$body_file" <<'PY' \|\| verdict=\$\?\n([\s\S]*?)\nPY\n/.exec(
    deploy,
  )?.[1];

function judge(code: string, body: unknown): number | null {
  const directory = mkdtempSync(path.join(tmpdir(), "smoke-verdict-"));
  const script = path.join(directory, "verdict.py");
  const answer = path.join(directory, "body.json");
  writeFileSync(script, verdict ?? "");
  writeFileSync(answer, JSON.stringify(body));
  return spawnSync("python3", [script, code, answer], { encoding: "utf8" }).status;
}

describe("the deploy's smoke verdict", () => {
  it("is found in phase_smoke", () => {
    expect(verdict).toContain("sys.exit(3)");
  });

  it("skips a run refused under approval enforcement, and a disabled source", () => {
    expect(judge("422", { error: "not-approved", reason: "ungated-source" })).toBe(3);
    expect(judge("422", { error: "source-disabled" })).toBe(3);
  });

  it("fails any other refusal, and passes a persisted run", () => {
    expect(judge("422", { error: "not-approved", reason: "no-head" })).toBe(1);
    expect(judge("500", { error: "pipeline-failed", reason: "unclassified" })).toBe(1);
    expect(judge("200", { status: "persisted", runId: "r" })).toBe(0);
  });
});
