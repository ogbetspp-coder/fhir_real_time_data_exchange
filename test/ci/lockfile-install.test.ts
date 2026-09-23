import { readdirSync, readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

// Every build and deploy installs from package-lock.json and fails without it. An `npm install`
// fallback resolves versions afresh when the lockfile is absent, so an image could be built from
// dependencies nobody reviewed and the OSV scan (scripts/ci/vuln-scan.sh) never saw.

const files = [
  ...readdirSync(".").filter(
    (name) => /^Dockerfile(\.|$)/.test(name) || /^cloudbuild.*\.ya?ml$/.test(name),
  ),
  "scripts/gcp/deploy.sh",
  ...readdirSync(".github/workflows").map((name) => `.github/workflows/${name}`),
];

describe("dependency installs", () => {
  it("never fall back to npm install", () => {
    for (const file of files) {
      const text = readFileSync(file, "utf8")
        .split("\n")
        .filter((line) => !/^\s*#/.test(line))
        .join("\n");
      expect([file, /\bnpm\s+(install|i)\b/.test(text)]).toEqual([file, false]);
    }
  });

  it("copy the lockfile into every Node image by name, so a missing one fails the build", () => {
    const node = readdirSync(".").filter(
      (name) => /^Dockerfile(\.|$)/.test(name) && /^FROM\s+node:/m.test(readFileSync(name, "utf8")),
    );
    expect(node.length).toBeGreaterThan(1);
    for (const name of node) {
      const text = readFileSync(name, "utf8");
      expect([name, text.includes("COPY package.json package-lock.json ./")]).toEqual([name, true]);
      expect([name, /^RUN npm ci --no-audit --no-fund$/m.test(text)]).toEqual([name, true]);
    }
  });
});
