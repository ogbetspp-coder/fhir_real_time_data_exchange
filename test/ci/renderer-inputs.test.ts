import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { NOT_INPUTS, rendererInputsChanged } from "../../scripts/ci/renderer-inputs.mjs";
import { MOUNTS } from "../../scripts/render/run.mjs";

// Which pull requests skip CI's Renderer job's checks (scripts/ci/renderer-inputs.mjs). The
// failure this guards against is the quiet one: a path a renderer check reads classed as not an
// input, so a change to it merges with the checks skipped. Every file the image's run mounts, and
// every file the image and the job are built from, must stay an input.

function files(at: string): string[] {
  if (!statSync(at).isDirectory()) return [at];
  return readdirSync(at).flatMap((entry) => files(path.join(at, entry)));
}

const workflow = readFileSync(".github/workflows/ci.yml", "utf8");

describe("the renderer's inputs", () => {
  it("are every file the image's run mounts, and every file the image and the job are built from", () => {
    const read = MOUNTS.filter((mounted) => mounted !== "node_modules").flatMap(files);
    expect(read.length).toBeGreaterThan(100);
    for (const file of [
      ...read,
      "Dockerfile.renderer",
      "src/render/image/fonts.conf",
      "package-lock.json",
      ".dockerignore",
      ".github/workflows/ci.yml",
      "scripts/ci/renderer-inputs.mjs",
      "scripts/render/run.mjs",
      "test/render/new.test.ts",
      "anything-new.json",
    ]) {
      // Markdown is never read by a check (a label's README is the lock's, not the scripts').
      if (file.endsWith(".md")) continue;
      expect([file, rendererInputsChanged([file])]).toEqual([file, true]);
    }
  });

  it("skip only documentation, the Python deployables, the infrastructure, assistant settings and Markdown", () => {
    expect(NOT_INPUTS).toHaveLength(7);
    for (const file of [
      "docs/design/authority-import-renderer.md",
      "README.md",
      "labels/ema-epi/README.md",
      "agent/src/a.py",
      "zone-a/tests/test_x.py",
      "infra/main.tf",
      ".claude/settings.json",
      ".cursor/rules/x.mdc",
    ]) {
      expect([file, rendererInputsChanged([file])]).toEqual([file, false]);
    }
    expect(rendererInputsChanged(["docs/x.md", "src/render/page.ts"])).toBe(true);
  });

  it("run every check when nothing is known to have changed", () => {
    expect(rendererInputsChanged([])).toBe(true);
  });

  it("gate every step of the Renderer job after the one that decides", () => {
    const job = /^ {2}renderer:\n([\s\S]*?)(?=^ {2}[a-z-]+:\n)/mu.exec(workflow)?.[1] ?? "";
    const steps = job.split(/^ {6}- /mu).slice(1);
    const decides = steps.findIndex((step) => step.includes("id: inputs"));
    expect(steps[decides]).toContain("node scripts/ci/renderer-inputs.mjs");
    expect(steps.length - decides).toBeGreaterThan(7);
    for (const step of steps.slice(decides + 1)) {
      expect(step).toContain("if: steps.inputs.outputs.run == 'true'");
    }
  });
});
