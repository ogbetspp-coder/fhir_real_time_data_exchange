import { describe, expect, it } from "vitest";

import { transformSection, TRefusal } from "../../src/authority/t/transform.js";
import { T_CASES } from "../fixtures/authority/t-cases.js";

// T's cases: each rule's both sides and each refusal reason (docs/design/authority-import-t.md).

const ROOT = '<div xmlns="http://www.w3.org/1999/xhtml">';
// Every committed narrative is synthetic and says so (test/synthetic-only.test.ts).
const MARKED = "<p>not for clinical use</p></div>";

function run(
  inner: string,
  evidence?: string[],
  root = ROOT,
): { div: string } | { refused: string } {
  try {
    const result = transformSection(
      `${root}${inner}${MARKED}`,
      evidence === undefined ? undefined : new Set(evidence),
    );
    return { div: result.div.slice(result.div.indexOf(">") + 1, -MARKED.length) };
  } catch (error) {
    if (error instanceof TRefusal) return { refused: error.reason };
    throw error;
  }
}

describe("T", () => {
  for (const testCase of T_CASES) {
    it(testCase.name, () => {
      expect(run(testCase.inner, testCase.evidence, testCase.root)).toEqual(testCase.expected);
    });
  }
});
