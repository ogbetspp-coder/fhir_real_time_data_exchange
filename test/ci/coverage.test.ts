import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import config from "../../vitest.config.js";

// The coverage floors are only a gate if the gate runs them. These tests keep the chain intact:
// `npm run check` runs the suite with coverage, CI's Check job and the deploy's quality gate run
// `npm run check`, every directory named below has a floor, and the report lands somewhere git
// never sees.

const manifest = JSON.parse(readFileSync("package.json", "utf8")) as {
  scripts: Record<string, string>;
  devDependencies: Record<string, string>;
};
const lock = JSON.parse(readFileSync("package-lock.json", "utf8")) as {
  packages: Record<string, { version?: string }>;
};
const coverage = config.test?.coverage as
  | {
      provider?: string;
      include?: string[];
      reportsDirectory?: string;
      thresholds?: Record<string, unknown>;
    }
  | undefined;

describe("coverage", () => {
  it("is part of the local gate", () => {
    expect(manifest.scripts["test:coverage"]).toBe("vitest run --coverage");
    const steps = (manifest.scripts.check ?? "").split(" && ");
    expect(steps).toContain("npm run test:coverage");
    // The plain suite is not run a second time.
    expect(steps).not.toContain("npm run test");
  });

  it("is run by CI's Check job and by the deploy's quality gate", () => {
    const ci = readFileSync(".github/workflows/ci.yml", "utf8");
    const checkJob = /\n {2}check:\n([\s\S]*?)\n {2}[a-z-]+:\n/.exec(ci)?.[1] ?? "";
    expect(checkJob).toMatch(/^ {8}run: npm run check$/m);
    const deploy = readFileSync(".github/workflows/deploy.yml", "utf8");
    const gateJob = /\n {2}gate:\n([\s\S]*?)\n {2}[a-z-]+:\n/.exec(deploy)?.[1] ?? "";
    expect(gateJob).toMatch(/^ {8}run: npm run check$/m);
  });

  it("uses the v8 provider at exactly the installed vitest version", () => {
    expect(coverage?.provider).toBe("v8");
    const vitest = lock.packages["node_modules/vitest"]?.version;
    expect(vitest).toBeDefined();
    expect(manifest.devDependencies["@vitest/coverage-v8"]).toBe(vitest);
    expect(lock.packages["node_modules/@vitest/coverage-v8"]?.version).toBe(vitest);
  });

  it("measures src/ and holds a floor for every directory it reports on", () => {
    expect(coverage?.include).toEqual(["src/**"]);
    const thresholds = coverage?.thresholds ?? {};
    for (const metric of ["lines", "statements", "functions", "branches"]) {
      expect(typeof thresholds[metric]).toBe("number");
    }
    for (const directory of ["fidelity", "contracts", "query", "fhir", "gcp", "lib"]) {
      const floor = thresholds[`src/${directory}/**`] as Record<string, unknown> | undefined;
      expect([directory, floor]).toEqual([
        directory,
        {
          lines: expect.any(Number) as number,
          statements: expect.any(Number) as number,
          functions: expect.any(Number) as number,
          branches: expect.any(Number) as number,
        },
      ]);
    }
  });

  it("writes its report into a directory git, prettier and eslint ignore", () => {
    const directory = coverage?.reportsDirectory ?? "coverage";
    expect(readFileSync(".gitignore", "utf8").split("\n")).toContain(`${directory}/`);
    expect(readFileSync(".prettierignore", "utf8").split("\n")).toContain(`${directory}/`);
    expect(readFileSync("eslint.config.mjs", "utf8")).toContain(`"${directory}/**"`);
  });
});
