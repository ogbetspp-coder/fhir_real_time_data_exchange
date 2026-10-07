import { existsSync, readdirSync, readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { dockerArgs, IMAGE, ISOLATION, MOUNTS } from "../../scripts/render/run.mjs";

// The renderer image's isolation, which Chrome's sandbox being off rests on (R1, R6): every
// script run in the image goes through scripts/render/run.mjs, and its `docker run` carries every
// flag the isolation needs and mounts only what the scripts read, read-only.

const scripts = (
  JSON.parse(readFileSync("package.json", "utf8")) as { scripts: Record<string, string> }
).scripts;

describe("the renderer image's run", () => {
  it("is the one way every renderer script runs, and every script under scripts/render has one", () => {
    const runs = Object.entries(scripts).filter(
      ([name]) => name.startsWith("renderer:") && name !== "renderer:image",
    );
    expect(runs.length).toBeGreaterThanOrEqual(6);
    for (const [name, command] of runs) {
      expect([name, command]).toEqual([
        name,
        expect.stringMatching(
          /^node scripts\/render\/run\.mjs scripts\/render\/[a-z-]+\.ts( --[a-z]+)*$/u,
        ),
      ]);
    }
    expect(scripts["renderer:image"]).toBe(
      `docker build --file Dockerfile.renderer --target renderer --tag ${IMAGE} .`,
    );
    // Every script under scripts/render that draws is run by one of them.
    const run = new Set(runs.map(([, command]) => command.split(" ")[2]));
    const drawing = readdirSync("scripts/render")
      .filter((file) => file.endsWith(".ts") && file !== "sections.ts")
      .map((file) => `scripts/render/${file}`);
    expect(drawing.filter((file) => !run.has(file))).toEqual([]);
    // And each runs a script that exists: a deleted one fails here, not only in the image.
    for (const target of run) expect([target, existsSync(target ?? "")]).toEqual([target, true]);
    // Every renderer step of CI's workflow names one of them.
    const workflow = readFileSync(".github/workflows/ci.yml", "utf8");
    const steps = [...workflow.matchAll(/run: npm run (renderer:[a-z]+)$/gmu)].map(
      ([, name]) => name ?? "",
    );
    expect(steps.length).toBeGreaterThanOrEqual(7);
    for (const name of steps) expect([name, name in scripts]).toEqual([name, true]);
  });

  it("carries every flag the isolation needs, and mounts only what the scripts read, read-only", () => {
    const args = dockerArgs("/repo", "scripts/render/check.ts", ["--ratios", "1"]);
    for (const flag of [
      "--read-only",
      "--cap-drop=ALL",
      "--security-opt=no-new-privileges",
      "--pids-limit=2048",
      "--memory=6g",
    ]) {
      expect(args).toContain(flag);
    }
    const pairs = (flag: string): string[] =>
      args.flatMap((arg, index) => (arg === flag ? [args[index + 1] ?? ""] : []));
    expect(pairs("--network")).toEqual(["none"]);
    expect(pairs("--tmpfs")).toEqual(["/tmp"]);
    expect(pairs("--env")).toEqual(["RENDERER_NO_SANDBOX=1"]);
    expect(pairs("--volume")).toEqual(
      MOUNTS.map((mounted) => `/repo/${mounted}:/work/${mounted}:ro`),
    );
    expect(args.slice(0, 2 + ISOLATION.length)).toEqual(["run", "--rm", ...ISOLATION]);
    expect(args.slice(-7)).toEqual([
      IMAGE,
      "node",
      "--import",
      "tsx",
      "scripts/render/check.ts",
      "--ratios",
      "1",
    ]);
    for (const mounted of MOUNTS) {
      expect([mounted, existsSync(mounted)]).toEqual([mounted, true]);
      expect(mounted).not.toMatch(/^\.?$|\.\./u);
    }
  });

  it("runs nothing but a script of scripts/render", () => {
    expect(() => dockerArgs("/repo", "scripts/ci/secret-scan.sh")).toThrow(/not a script/);
    expect(() => dockerArgs("/repo", "scripts/render/../ci/x.ts")).toThrow(/not a script/);
  });
});
