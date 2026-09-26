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

  // The renderer image installs no Node dependency: the render build and CI mount a workspace
  // installed from the lockfile outside it, read-only (docs/design/authority-import-renderer.md,
  // R1), so the image holds only the browser and its fonts, and must not run npm at all.
  it("keep npm out of the renderer image", () => {
    expect(readFileSync("Dockerfile.renderer", "utf8")).not.toMatch(/\bnpm\b/);
  });

  it("copy the lockfile into every Node image by name, so a missing one fails the build", () => {
    const node = readdirSync(".").filter(
      (name) =>
        /^Dockerfile(\.|$)/.test(name) &&
        name !== "Dockerfile.renderer" &&
        /^FROM\s+node:/m.test(readFileSync(name, "utf8")),
    );
    expect(node.length).toBeGreaterThan(1);
    for (const name of node) {
      const text = readFileSync(name, "utf8");
      expect([name, text.includes("COPY package.json package-lock.json ./")]).toEqual([name, true]);
      expect([name, /^RUN npm ci --no-audit --no-fund$/m.test(text)]).toEqual([name, true]);
    }
  });
});
