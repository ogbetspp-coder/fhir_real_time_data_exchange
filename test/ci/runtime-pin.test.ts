import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

// ADR 0003 pins the runtime to one Node version because NFC, step 3 of the fidelity
// normalisation, depends on the runtime's Unicode tables: a different Node can move every
// NFC-dependent hash. test/runtime.test.ts checks the Unicode version of whatever Node runs the
// tests — CI's own install — which says nothing about the Node inside the images that are
// deployed. On 2026-09-21 an automated update proposed Node 26 for every image and passed every
// gate. This is the test that would have failed.

const adr = readFileSync(
  path.join("docs/adr", readdirSync("docs/adr").find((name) => name.startsWith("0003")) ?? ""),
  "utf8",
);
const pinned = /runtime image \(`node:(\d+\.\d+\.\d+)`/.exec(adr)?.[1];

const dockerfiles = readdirSync(".")
  .filter((name) => /^Dockerfile(\.|$)/.test(name))
  .map((name) => ({ name, text: readFileSync(name, "utf8") }));
const workflows = readdirSync(".github/workflows")
  .filter((name) => name.endsWith(".yml"))
  .map((name) => ({ name, text: readFileSync(path.join(".github/workflows", name), "utf8") }));

describe("the Node runtime ADR 0003 pins", () => {
  it("is named by the ADR", () => {
    expect(pinned).toMatch(/^\d+\.\d+\.\d+$/);
  });

  it("is the version of every Node base image", () => {
    const images = dockerfiles.flatMap(({ name, text }) =>
      [...text.matchAll(/^FROM\s+node:(\d+\.\d+\.\d+)/gm)].map((match) => [name, match[1]]),
    );
    expect(images.length).toBeGreaterThan(0);
    for (const [name, version] of images) expect([name, version]).toEqual([name, pinned]);
  });

  it("is the version every workflow tests and builds with", () => {
    const versions = workflows.flatMap(({ name, text }) =>
      [...text.matchAll(/node-version:\s*"?([\d.]+)"?/g)].map((match) => [name, match[1]]),
    );
    expect(versions.length).toBeGreaterThan(0);
    for (const [name, version] of versions) expect([name, version]).toEqual([name, pinned]);
  });

  it("is the version .nvmrc selects for local development", () => {
    expect(readFileSync(".nvmrc", "utf8").trim()).toBe(pinned);
  });

  // package.json said `>=22.14`, npm@10.9.7 and types for Node 26 (audit B07, S-5): each allowed
  // or type-checked against a runtime other than the one ADR 0003 pins.
  it("is the only Node package.json accepts, with the npm it bundles and its own types", () => {
    const manifest = JSON.parse(readFileSync("package.json", "utf8")) as {
      engines: { node: string };
      packageManager: string;
      devDependencies: Record<string, string>;
    };
    expect(manifest.engines.node).toBe(pinned);
    // The npm that Node 22.22.0 ships (https://nodejs.org/dist/index.json).
    expect(manifest.packageManager).toBe("npm@10.9.4");
    const types = /^~(\d+)\.\d+\.\d+$/.exec(manifest.devDependencies["@types/node"] ?? "");
    expect(types?.[1]).toBe(pinned?.split(".")[0]);
  });

  it("is enforced where the gate and the images install, not only declared", () => {
    expect(readFileSync("Dockerfile", "utf8")).toMatch(/^RUN npm ci [^\n]*--engine-strict/m);
    const ci = readFileSync(".github/workflows/ci.yml", "utf8");
    expect(ci).toMatch(
      /Install Node dependencies\n\s+run: npm ci --no-audit --no-fund --engine-strict\n/,
    );
  });

  it("is never moved by the update bot, which may only refresh its digest", () => {
    const dependabot = readFileSync(".github/dependabot.yml", "utf8");
    const node = /-\s*dependency-name:\s*"node"\s*\n\s*update-types:\s*\n?\s*\[([^\]]*)\]/.exec(
      dependabot,
    )?.[1];
    for (const type of ["semver-major", "semver-minor", "semver-patch"]) {
      expect(node).toContain(`version-update:${type}`);
    }
  });
});
