import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

// The Terraform configuration's interface, kept in one place and documented (infra/README.md):
// every input is declared in variables.tf, every input and output says what it is, and the
// environment is always named by the caller rather than defaulted.

const infraFiles = readdirSync("infra")
  .filter((name) => name.endsWith(".tf"))
  .sort()
  .map((name) => ({ name, text: readFileSync(path.join("infra", name), "utf8") }));

function blocks(kind: "variable" | "output"): { file: string; name: string; body: string }[] {
  const found: { file: string; name: string; body: string }[] = [];
  for (const { name: file, text } of infraFiles) {
    for (const match of text.matchAll(
      new RegExp(`^${kind} "([^"]+)" \\{\\n([\\s\\S]*?)^\\}`, "gm"),
    )) {
      found.push({ file, name: match[1] ?? "", body: match[2] ?? "" });
    }
  }
  return found;
}

describe("the Terraform interface", () => {
  it("declares every input in variables.tf", () => {
    const misplaced = blocks("variable").filter(({ file }) => file !== "variables.tf");
    expect(misplaced.map(({ file, name }) => `${file}: ${name}`)).toEqual([]);
  });

  it("describes every input and every output", () => {
    const undescribed = [...blocks("variable"), ...blocks("output")].filter(
      ({ body }) => !/^\s*description\s*=/m.test(body),
    );
    expect(undescribed.map(({ file, name }) => `${file}: ${name}`)).toEqual([]);
    expect(blocks("output").length).toBeGreaterThan(10);
  });

  it("documents every input and output in infra/README.md", () => {
    const readme = readFileSync("infra/README.md", "utf8");
    for (const { name } of [...blocks("variable"), ...blocks("output")]) {
      expect([name, readme.includes(`\`${name}\``)]).toEqual([name, true]);
    }
  });

  it("has no default environment, and the deploy passes it to every plan, apply and import", () => {
    const environment = blocks("variable").find(({ name }) => name === "environment");
    expect(environment?.body).not.toMatch(/^\s*default\s*=/m);
    const deploy = readFileSync("scripts/gcp/deploy.sh", "utf8");
    expect(deploy).toMatch(
      /tf_common_vars=\(\n(?:[^\n]*\n)*?\s*-var="environment=\$\{ENVIRONMENT\}"/,
    );
    const calls = [...deploy.matchAll(/terraform -chdir=infra (plan|apply|import)\b[\s\S]*?\n\n/g)];
    // phase_apis's import and targeted apply, phase_apply's apply, sync_dashboard's replacement,
    // phase_plan's plan.
    expect(calls.length).toBe(5);
    for (const [call] of calls) {
      expect(/"\$\{tf_common_vars\[@\]\}"|"\$\{TF_DEPLOY_VARS\[@\]\}"/.test(call)).toBe(true);
    }
    expect(deploy).toMatch(/TF_DEPLOY_VARS=\(\n\s+"\$\{tf_common_vars\[@\]\}"/);
  });
});
