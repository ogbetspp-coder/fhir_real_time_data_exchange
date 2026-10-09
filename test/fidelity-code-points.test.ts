import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { NORMALIZATION_VERSION, hasInvisibleFormatting } from "../src/fidelity/normalize.js";
import { sha256 } from "../src/lib/hash.js";
import { codePointVectors, type CodePointVectors } from "./fixtures/fidelity/code-points.js";

// The `codePoints` vector family (test/fixtures/fidelity/code-points.ts): every code point's
// classes under every closed list. Zone A (zone-a/tests/test_code_points.py) and the agent
// (agent/tests/test_quote_edge_code_points.py) hold their own copies of the lists to the same
// table.

const vectors = JSON.parse(readFileSync("test/fixtures/fidelity/vectors.json", "utf8")) as {
  codePoints: CodePointVectors;
};

// The table each normalisation version was released with. A change to a closed list moves the
// table, and so fails here until the change is given a version of its own
// (docs/fidelity-normalization.md section 8): a list is part of the specification, and dropping
// one entry from it (an ignorable, a thin space) would otherwise pass every worked vector.
const RELEASED_TABLES: Readonly<Record<string, string>> = {
  "fidelity-norm/3.1.0": "61927ab9e2345524a694ed2770de1fa29c25b7bfe0ac54d56ed258a1b1b5d8ae",
  // 3.2.0 qualifies a source kind (section 7) and changes no list.
  "fidelity-norm/3.2.0": "61927ab9e2345524a694ed2770de1fa29c25b7bfe0ac54d56ed258a1b1b5d8ae",
  // 3.3.0 allows one attribute value (section 5) and changes no list.
  "fidelity-norm/3.3.0": "61927ab9e2345524a694ed2770de1fa29c25b7bfe0ac54d56ed258a1b1b5d8ae",
  // 3.4.0 widens section 7's certified Word rule (a typed label's tab) and changes no list.
  "fidelity-norm/3.4.0": "61927ab9e2345524a694ed2770de1fa29c25b7bfe0ac54d56ed258a1b1b5d8ae",
  // 3.5.0 widens section 7's certified Word rule (grids, tabs, marks over nothing) and changes no
  // list.
  "fidelity-norm/3.5.0": "61927ab9e2345524a694ed2770de1fa29c25b7bfe0ac54d56ed258a1b1b5d8ae",
  // 3.6.0 widens section 5's lowered-half rule (the half-life's letters) and changes no list.
  "fidelity-norm/3.6.0": "61927ab9e2345524a694ed2770de1fa29c25b7bfe0ac54d56ed258a1b1b5d8ae",
};

describe("the code point vectors", () => {
  const computed = codePointVectors();

  it("reproduce every class of every code point", () => {
    expect(computed.classes).toEqual(vectors.codePoints.classes);
    expect(computed.runs).toEqual(vectors.codePoints.runs);
    expect(computed.scriptFolds).toEqual(vectors.codePoints.scriptFolds);
  });

  it("are the table this normalisation version was released with", () => {
    expect(sha256(computed)).toBe(RELEASED_TABLES[NORMALIZATION_VERSION]);
  });

  it("move when one entry leaves a list", () => {
    // U+205F MEDIUM MATHEMATICAL SPACE is a thin space and U+180B a variation selector the
    // renderer draws as nothing: the entries the review found no worked vector reached.
    const bitsAt = (codePoint: number): number => {
      let bits = 0;
      for (const [start, value] of computed.runs) {
        if (start > codePoint) break;
        bits = value;
      }
      return bits;
    };
    const gap = 1 << computed.classes.indexOf("gap");
    const ignorable = 1 << computed.classes.indexOf("defaultIgnorable");
    expect(bitsAt(0x205f) & gap).toBe(gap);
    expect(bitsAt(0x180b) & ignorable).toBe(ignorable);
    expect(bitsAt(0x1d173) & ignorable).toBe(ignorable);
    expect(bitsAt(0x2060) & gap).toBe(gap);
    expect(bitsAt(0x0041) & gap).toBe(0);
  });

  it("hold the importer's invisible-character check to step 1's list", () => {
    // src/authority/import.ts refuses a structured page that holds a character step 1 removes
    // (fidelity section 7), through hasInvisibleFormatting: the same list, not a copy of it.
    const removed = 1 << computed.classes.indexOf("removedByStep1");
    const found: number[] = [];
    let run = 0;
    for (let codePoint = 0; codePoint <= 0x10ffff; codePoint += 1) {
      if (codePoint >= 0xd800 && codePoint <= 0xdfff) continue;
      while ((computed.runs[run + 1]?.[0] ?? Infinity) <= codePoint) run += 1;
      const bits = computed.runs[run]?.[1] ?? 0;
      const invisible = hasInvisibleFormatting(String.fromCodePoint(codePoint));
      if (invisible !== ((bits & removed) === removed)) found.push(codePoint);
    }
    expect(found).toEqual([]);
  });
});
