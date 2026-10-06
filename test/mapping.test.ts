import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { loadEmaMapping, type SectionRule } from "../src/fhir/mapping.js";

const MANIFEST = path.resolve("fhir/mappings/cap-smpc-en.json");

let directory: string | undefined;

afterEach(() => {
  if (directory !== undefined) rmSync(directory, { recursive: true, force: true });
  directory = undefined;
});

type Manifest = { root: SectionRule };

function rules(root: SectionRule): SectionRule[] {
  return [root, ...(root.children ?? []).flatMap(rules)];
}

function rule(root: SectionRule, sourceKey: string): SectionRule {
  const found = rules(root).find((candidate) => candidate.sourceKey === sourceKey);
  if (found === undefined) throw new Error(`Manifest has no rule ${sourceKey}`);
  return found;
}

// The published manifest, altered in memory and written to a scratch file for the loader.
function writeManifest(alter: (manifest: Manifest) => void): string {
  const manifest = JSON.parse(readFileSync(MANIFEST, "utf8")) as Manifest;
  alter(manifest);
  directory = mkdtempSync(path.join(tmpdir(), "mapping-manifest-"));
  const file = path.join(directory, "cap-smpc-en.json");
  writeFileSync(file, JSON.stringify(manifest));
  return file;
}

describe("mapping manifest loader", () => {
  it("loads the published manifest", async () => {
    await expect(loadEmaMapping()).resolves.toMatchObject({ root: { sourceKey: "smpc" } });
  });

  it("requires narrative above the subsections of 4.8 and of no other section with children", async () => {
    const mapping = await loadEmaMapping();
    const flagged = rules(mapping.root)
      .filter(({ narrative }) => narrative === "required")
      .map(({ sourceKey }) => sourceKey);

    expect(flagged).toEqual(["smpc.4.8"]);
  });

  it("rejects an unmapped slot that shares a key or a code with a rule", async () => {
    const file = writeManifest((manifest) => {
      Object.assign(manifest, {
        unmapped: [
          {
            sourceKey: "smpc.4.3",
            targetCode: "200000029805",
            title: "4.3 Contraindications",
            reason: "x",
          },
        ],
      });
    });

    await expect(loadEmaMapping(file)).rejects.toThrow(
      /Duplicate sourceKey smpc\.4\.3 in mapping manifest; Duplicate targetCode 200000029805/,
    );
  });

  it("rejects a narrative flag other than required", async () => {
    const file = writeManifest((manifest) => {
      Object.assign(rule(manifest.root, "smpc.4.8"), { narrative: "optional" });
    });

    await expect(loadEmaMapping(file)).rejects.toThrow(/narrative/);
  });

  it("rejects a sourceKey that two rules share", async () => {
    // The 4.4 rule keyed smpc.4.3 would publish the contraindications under the special-warnings
    // heading as well as under their own.
    const file = writeManifest((manifest) => {
      rule(manifest.root, "smpc.4.4").sourceKey = "smpc.4.3";
    });

    await expect(loadEmaMapping(file)).rejects.toThrow("Duplicate sourceKey smpc.4.3");
  });

  it("rejects a targetCode that two rules share", async () => {
    const file = writeManifest((manifest) => {
      rule(manifest.root, "smpc.4.4").targetCode = rule(manifest.root, "smpc.4.3").targetCode;
    });

    await expect(loadEmaMapping(file)).rejects.toThrow("Duplicate targetCode 200000029805");
  });
});
