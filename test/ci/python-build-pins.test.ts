import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

// Zone A, the agent and the label reader are built by hatchling on every `uv sync`. Its version was
// pinned; its own dependencies were resolved afresh each time, unpinned (audit B07, S-5). Every
// project now pins the whole build closure in [tool.uv] build-constraint-dependencies, recorded in
// uv.lock.

// hatchling's runtime dependencies on Python 3.14 (tomli only applies below 3.11).
const HATCHLING_CLOSURE = ["packaging", "pathspec", "pluggy", "tomlkit", "trove-classifiers"];

function list(text: string, key: string): string[] {
  const body = new RegExp(`^${key} = \\[([^\\]]*)\\]`, "m").exec(text)?.[1] ?? "";
  return [...body.matchAll(/"([^"]+)"/g)].map((match) => match[1] ?? "");
}

describe.each(["zone-a", "agent", "label-docx-reader"])("%s's build", (project) => {
  const pyproject = readFileSync(`${project}/pyproject.toml`, "utf8");
  const lock = readFileSync(`${project}/uv.lock`, "utf8");
  const constraints = list(pyproject, "build-constraint-dependencies");

  it("pins the build backend at the version [build-system] requires", () => {
    const requires = list(pyproject, "requires");
    expect(requires).toHaveLength(1);
    expect(requires[0]).toMatch(/^hatchling==\d+\.\d+\.\d+$/);
    expect(constraints).toContain(requires[0]);
  });

  it("pins every package of the backend's closure to an exact version", () => {
    for (const constraint of constraints) expect(constraint).toMatch(/^[a-z0-9-]+==[\w.]+$/);
    expect(constraints.map((constraint) => constraint.split("==")[0]).sort()).toEqual(
      ["hatchling", ...HATCHLING_CLOSURE].sort(),
    );
  });

  it("is recorded in uv.lock, so a change to the pins without a relock fails", () => {
    const recorded = /^\[manifest\]\nbuild-constraints = \[\n([\s\S]*?)\n\]/m.exec(lock)?.[1] ?? "";
    const pairs = [...recorded.matchAll(/\{ name = "([^"]+)", specifier = "==([^"]+)" \}/g)].map(
      ([, name, version]) => `${name}==${version}`,
    );
    expect(pairs.sort()).toEqual([...constraints].sort());
  });
});
