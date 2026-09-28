import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  carriedMismatch,
  enumerateSections,
  MARKED,
  pinnedLabels,
  ROOT,
  sectionDivs,
  type PinnedLabel,
} from "../../src/render/sections.js";

// What every check of the renderer image draws (src/render/sections.ts): one enumeration, which
// fails on anything it would otherwise skip.

const div = (inner: string): string => `${ROOT}${inner}${MARKED}`;
const label = (file: string, composition: unknown): PinnedLabel => ({
  file,
  document: Buffer.from(JSON.stringify({ entry: [{ resource: composition }] })),
});

let directory: string | undefined;
afterEach(() => {
  if (directory !== undefined) rmSync(directory, { recursive: true, force: true });
  directory = undefined;
});

// A labels directory holding `bytes` as a.json, its lock pinning `pinned` (the bytes' own hash
// and length unless changed).
function labels(bytes: string, lock: (entry: Record<string, unknown>) => unknown = (e) => e) {
  directory = mkdtempSync(path.join(tmpdir(), "renderer-labels-"));
  mkdirSync(path.join(directory, "sources"));
  writeFileSync(path.join(directory, "sources", "a.json"), bytes);
  const entry = {
    file: "a.json",
    sha256: createHash("sha256").update(bytes).digest("hex"),
    bytes: Buffer.byteLength(bytes),
    product: "kept",
  };
  writeFileSync(
    path.join(directory, "sources.lock.json"),
    JSON.stringify({ sources: [lock(entry)] }),
  );
  return directory;
}

describe("the pinned labels", () => {
  it("reads every label the lock pins, held to its bytes and hash", () => {
    const pinned = pinnedLabels("labels/ema-epi");
    expect(pinned.length).toBeGreaterThanOrEqual(5);
    expect(pinnedLabels(labels("{}"))).toEqual([{ file: "a.json", document: Buffer.from("{}") }]);
  });

  it("refuses a label whose bytes or hash are not the lock's, and a lock of another shape", () => {
    expect(() => pinnedLabels(labels("{}", (entry) => ({ ...entry, bytes: 3 })))).toThrow(
      /the lock pins 3 bytes/,
    );
    expect(() =>
      pinnedLabels(labels("{}", (entry) => ({ ...entry, sha256: "0".repeat(64) }))),
    ).toThrow(/a\.json: 2 bytes, SHA-256/);
    expect(() =>
      pinnedLabels(labels("{}", (entry) => ({ ...entry, file: "../a.json" }))),
    ).toThrow();
    expect(() =>
      pinnedLabels(labels("{}", (entry) => ({ ...entry, sha256: undefined }))),
    ).toThrow();
  });
});

describe("a document's sections", () => {
  it("walks the Composition's sections in pre-order, with their paths and the Binaries it contains", () => {
    const { sections, contained } = sectionDivs({
      entry: [
        {
          resource: {
            section: [
              { text: { div: "a" }, section: [{ title: "no div" }, { text: { div: "c" } }] },
              { text: { div: 4 } },
            ],
            contained: [
              { resourceType: "Binary", id: "p", contentType: "image/png", data: "AAEC" },
              { resourceType: "Binary", id: "q" },
              { resourceType: "Binary" },
              { resourceType: "Media", id: "m" },
            ],
          },
        },
      ],
    });
    expect(sections).toEqual([
      { path: "Composition.section[0]", div: "a" },
      { path: "Composition.section[0].section[0]", div: undefined },
      { path: "Composition.section[0].section[1]", div: "c" },
      { path: "Composition.section[1]", div: undefined },
    ]);
    expect([...contained]).toEqual([
      ["p", { body: Buffer.from([0, 1, 2]), contentType: "image/png" }],
      ["q", { body: Buffer.alloc(0), contentType: "application/octet-stream" }],
    ]);
    expect(sectionDivs(null)).toEqual({ sections: [], contained: new Map() });
  });
});

describe("the enumeration", () => {
  const accepted = { name: "kept", inner: "<p>x</p>" };

  it("carries every accepted case and every section T and the scanner accept, and reports the rest", () => {
    const { sections, broken } = enumerateSections({
      tCases: [accepted],
      modelCases: [{ name: "model", inner: "<p><b>y</b></p>" }],
      labels: [
        label("a.json", {
          section: [
            { text: { div: div("<p>z</p>") } },
            { title: "heading only" },
            { text: { div: div("<p><font>x</font></p>") } },
            // A left-to-right mark: T keeps it, the scanner refuses it.
            { text: { div: div("<p>a\u200eb</p>") } },
          ],
        }),
      ],
    });
    expect(broken).toEqual([]);
    expect(sections.map(({ name, carried }) => [name, carried])).toEqual([
      ["t-case kept", true],
      ["model-case model", true],
      ["a.json Composition.section[0]", true],
      ["a.json Composition.section[2]", false],
      ["a.json Composition.section[3]", false],
    ]);
    expect(sections[4]).toEqual(expect.objectContaining({ refused: "the scanner" }));
    expect(sections[3]).toEqual(expect.objectContaining({ refused: "T: element" }));
    const first = sections[0];
    expect(first?.div).toBe(div("<p>x</p>"));
    expect(first?.carried === true ? first.model.format : undefined).toBe("t-model/1.0.0");
  });

  it("is broken by a case T refuses, an empty lock and a label with no sections", () => {
    expect(
      enumerateSections({
        tCases: [
          { name: "refused", inner: "<p><font>x</font></p>" },
          { name: "scanned", inner: "<p>a\u200eb</p>" },
        ],
        modelCases: [{ name: "model", inner: "<p><code>x</code></p>" }],
        labels: [],
      }).broken,
    ).toEqual([
      "t-case refused: accepted in t-cases.ts, refused by T (element)",
      "t-case scanned: accepted in t-cases.ts, refused by the scanner",
      "model-case model: a model case, refused by T (element)",
      "the lock pins no label",
    ]);
    expect(
      enumerateSections({ tCases: [], modelCases: [], labels: [label("b.json", {})] }).broken,
    ).toEqual(["b.json: no sections"]);
  });
});

describe("a check's count of the carried sections", () => {
  const { sections } = enumerateSections({
    tCases: [
      { name: "one", inner: "<p>1</p>" },
      { name: "two", inner: "<p>2</p>" },
    ],
    modelCases: [],
    labels: [label("a.json", { section: [{ text: { div: div("<p><font>x</font></p>") } }] })],
  });

  it("passes a check that judged each carried section once", () => {
    expect(carriedMismatch("R3", ["t-case two", "t-case one"], sections)).toBeUndefined();
  });

  it("fails a check that skipped one, or judged one twice", () => {
    expect(carriedMismatch("R3", ["t-case one"], sections)).toBe(
      "R3: judged 1 carried sections (1 distinct), not 2; missing t-case two",
    );
    expect(carriedMismatch("fonts", ["t-case one", "t-case one"], sections)).toBe(
      "fonts: judged 2 carried sections (1 distinct), not 2; missing t-case two",
    );
  });
});
