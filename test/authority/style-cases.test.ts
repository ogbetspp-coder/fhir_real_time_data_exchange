import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { transformDocument } from "../../src/authority/t/document.js";

// T and the QRD check's ePI reader (zone_a.epi.reader) read the same EMA divs, each against a
// closed CSS list of its own. zone-a/scripts/generate_style_cases.py writes the shared cases
// with the reader's answer, and T's where the two differ on purpose, with why; T must give the
// reader's answer on every other case. A change to T's closed lists that moves a case fails here
// until the case is recorded there (and a change to the reader changes the file).

type Case = {
  element: string;
  style: string;
  div: string;
  reader: string;
  marks?: string[];
  t?: string;
  why?: string;
};

const cases = JSON.parse(
  readFileSync("test/fixtures/authority/style-cases.json", "utf8"),
) as Case[];

function answer(div: string): string {
  const [outcome] = transformDocument([div]);
  if (outcome === undefined) throw new Error("a case without a div");
  return "div" in outcome ? "accepted" : `refused:${outcome.refused}`;
}

describe("the CSS cases T shares with the QRD check's reader", () => {
  it("has cases, both kinds of answer, and a reason for every divergence", () => {
    expect(cases.length).toBeGreaterThan(50);
    expect(cases.some((c) => c.reader === "read")).toBe(true);
    expect(cases.some((c) => c.reader.startsWith("refused:"))).toBe(true);
    for (const c of cases) {
      expect([c.element, c.style, c.t === undefined, c.why === undefined]).toEqual([
        c.element,
        c.style,
        c.t === undefined,
        c.t === undefined,
      ]);
    }
  });

  it("accepts what the reader reads and refuses what it refuses, but where recorded", () => {
    for (const c of cases) {
      const t = answer(c.div);
      if (c.t !== undefined) {
        // A recorded divergence: T's own answer, reason and all.
        expect([c.element, c.style, t]).toEqual([c.element, c.style, c.t]);
      } else {
        expect([c.element, c.style, t === "accepted"]).toEqual([
          c.element,
          c.style,
          c.reader === "read",
        ]);
      }
    }
  });
});
