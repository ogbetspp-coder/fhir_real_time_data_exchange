import { readFile } from "node:fs/promises";
import path from "node:path";

import { beforeAll, describe, expect, it } from "vitest";

import {
  FidelityError,
  NORMALIZATION_VERSION,
  NormalizationError,
  XhtmlError,
  computeNarrativeBinding,
  hasDrawnText,
  normalizeText,
  verifyNarrativeFidelity,
  verifyReportHash,
  xhtmlToText,
  type FidelityInput,
  type FidelityReport,
} from "../src/fidelity/index.js";
import { canonicalJson, sha256Utf8 } from "../src/lib/hash.js";
import { growth } from "./support/growth.js";
import {
  buildSource,
  normalizationCases,
  paragraphs,
  spanFor,
  throwCases,
  verifyCases,
  xhtmlCases,
} from "./fixtures/fidelity/cases.js";

type Vectors = {
  normalizationVersion: string;
  normalization: { name: string; input: string; expected: unknown }[];
  xhtml: { name: string; input: string; expected: unknown }[];
  verify: {
    name: string;
    input: Parameters<typeof verifyNarrativeFidelity>[0];
    expected: FidelityReport;
  }[];
};

let vectors: Vectors;

beforeAll(async () => {
  const file = path.resolve("test/fixtures/fidelity/vectors.json");
  vectors = JSON.parse(await readFile(file, "utf8")) as Vectors;
});

function tryNormalize(input: string): unknown {
  try {
    return normalizeText(input);
  } catch (error) {
    if (error instanceof NormalizationError) return { error: error.code };
    throw error;
  }
}

function tryXhtml(input: string): unknown {
  try {
    return xhtmlToText(input);
  } catch (error) {
    if (error instanceof XhtmlError) return { error: error.code };
    throw error;
  }
}

// Small seeded PRNG so property-style cases are reproducible without a library.
function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

describe("golden vectors", () => {
  it("are generated for the current normalization version", () => {
    expect(vectors.normalizationVersion).toBe(NORMALIZATION_VERSION);
    expect(vectors.normalization.map(({ name }) => name)).toEqual(
      normalizationCases.map(({ name }) => name),
    );
    expect(vectors.xhtml.map(({ name }) => name)).toEqual(xhtmlCases.map(({ name }) => name));
    expect(vectors.verify.map(({ name }) => name)).toEqual(verifyCases.map(({ name }) => name));
  });

  it("normalization vectors reproduce byte-for-byte", () => {
    for (const vector of vectors.normalization) {
      expect(tryNormalize(vector.input), vector.name).toEqual(vector.expected);
    }
  });

  it("xhtml vectors reproduce byte-for-byte", () => {
    for (const vector of vectors.xhtml) {
      expect(tryXhtml(vector.input), vector.name).toEqual(vector.expected);
    }
  });

  it("verify vectors reproduce the exact report and reportHash", () => {
    for (const vector of vectors.verify) {
      const report = verifyNarrativeFidelity(vector.input);
      expect(canonicalJson(report), vector.name).toBe(canonicalJson(vector.expected));
      expect(report.reportHash, vector.name).toBe(vector.expected.reportHash);
      expect(verifyReportHash(report), vector.name).toBe(true);
    }
  });
});

describe("normalization", () => {
  it("matches the case expectations and is idempotent", () => {
    for (const testCase of normalizationCases) {
      const result = tryNormalize(testCase.input);
      expect(result, testCase.name).toEqual(testCase.expected);
      if (typeof result === "string") expect(normalizeText(result), testCase.name).toBe(result);
    }
  });
});

// A structured source (section 7): one page per section, the page exactly the scanner's text
// for the section's div, the whole page the body and the span. The accepted vectors that draw
// text and hold no section 3 step 1 invisible character (the extractor refuses those) must verify
// one section at a time and all together, a page each.
const INVISIBLE = /[\u00ad\u200b\ufeff\u2060]/u;

function structuredSource(divs: string[]): FidelityInput {
  const texts = divs.map((div) => xhtmlToText(div));
  return {
    normalizationVersion: NORMALIZATION_VERSION,
    source: {
      extractorVersion: "structured-source-property/1.0.0",
      pages: texts.map((text, index) => ({
        page: index + 1,
        text,
        bodyStart: 0,
        bodyEnd: Array.from(text).length,
      })),
    },
    sections: divs.map((div, index) => ({
      sourceKey: `section.${String(index + 1)}`,
      path: `Composition.section[${String(index)}]`,
      div,
    })),
    provenance: texts.map((text, index) => ({
      sourceKey: `section.${String(index + 1)}`,
      spans: [
        {
          page: index + 1,
          startOffset: 0,
          endOffset: Array.from(text).length,
          textSha256: sha256Utf8(text),
        },
      ],
      narrativeDivSha256: sha256Utf8(divs[index] ?? ""),
      normalizedTextSha256: "0".repeat(64),
    })),
  };
}

describe("structured source (section 7)", () => {
  const accepted = xhtmlCases
    .filter(({ expected }) => typeof expected === "string")
    .map(({ input }) => input)
    .filter((div) => {
      const text = xhtmlToText(div);
      return !INVISIBLE.test(text) && hasDrawnText(normalizeText(text));
    });

  it("verifies every accepted vector as a page of its own", () => {
    expect(accepted.length).toBeGreaterThan(50);
    for (const div of accepted) {
      expect(verifyNarrativeFidelity(structuredSource([div])).status, div).toBe("passed");
    }
  });

  // The other direction: a narrative verifies against another's structured page if and only if
  // the two read the same after normalisation. Every pair of accepted vectors is verified (tens
  // of thousands of runs), so the test has a timeout of its own: on a loaded machine or runner it
  // takes longer than the default five seconds.
  it(
    "verifies a narrative against another's page only when both read the same",
    { timeout: 60_000 },
    () => {
      for (const page of accepted) {
        const input = structuredSource([page]);
        const expected = normalizeText(xhtmlToText(page));
        for (const narrative of accepted) {
          const section = input.sections[0];
          if (section === undefined) throw new Error("fixture");
          const report = verifyNarrativeFidelity({
            ...input,
            sections: [{ ...section, div: narrative }],
          });
          const same = normalizeText(xhtmlToText(narrative)) === expected;
          expect(report.status === "passed", `${narrative} against ${page}`).toBe(same);
        }
      }
    },
  );

  it("verifies every accepted vector together, one page per section", () => {
    const report = verifyNarrativeFidelity(structuredSource(accepted));
    expect(report.sections.filter(({ status }) => status !== "verified")).toEqual([]);
    expect(report.status).toBe("passed");
  });
});

describe("xhtml scanner", () => {
  it("matches the case expectations", () => {
    for (const testCase of xhtmlCases) {
      expect(tryXhtml(testCase.input), testCase.name).toEqual(testCase.expected);
    }
  });

  // fidelity-norm/3.0.0: the tables of one narrative cover at most 50 000 slots. The accepted side
  // is pinned here rather than as a vector, whose text would be 150 000 code points long.
  it("accepts a grid of exactly the slot limit and refuses one slot more", () => {
    // One row of 1000 single cells (so every column has one) and 49 rows spanning them all.
    const grid = `<tr>${"<td>a</td>".repeat(1000)}</tr>${'<tr><td colspan="1000">a</td></tr>'.repeat(49)}`;
    const div = (extra = ""): string =>
      `<div xmlns="http://www.w3.org/1999/xhtml"><table>${grid}</table>${extra}</div>`;
    expect(typeof tryXhtml(div())).toBe("string");
    expect(tryXhtml(div("<table><tr><td>b</td></tr></table>"))).toEqual({ error: "table-size" });
  });

  // The cost tests below assert growth, not time (test/support/growth.ts): each input a quarter
  // of the size and then the whole, where linear grows about 4 times and quadratic about 16.
  // Until audit B15 they were wall-clock bounds, which failed under concurrent runs.

  // Review round 3: the grid is kept sparse, so a row costs only the slots it covers. Twenty
  // thousand empty rows under a 50 000-slot row took 20 s here and 79 s in Python before. The row
  // and the rows grow together, so a row that cost the grid's width would grow quadratically.
  it("scans empty rows under a wide row in linear time", () => {
    const table = (cells: number): string => {
      const wide = `<tr>${'<td colspan="1000">a</td>'.repeat(cells)}</tr>`;
      return `<div xmlns="http://www.w3.org/1999/xhtml"><table>${wide}${"<tr></tr>".repeat(400 * cells)}</table></div>`;
    };
    expect(tryXhtml(table(50))).toEqual({ error: "table-shape" });
    const ratio = growth((cells) => {
      const div = table(cells);
      return () => tryXhtml(div);
    }, 12);
    expect(ratio).toBeLessThan(10);
  }, 120_000);

  // fidelity-norm/3.1.0 review round 2: the mark rule reads each run of ignorables once, and
  // the lowered-half rule looks only at adjacent pieces. Twenty thousand tags before twenty
  // thousand word joiners (a div of 160 057 code points) took about 72 s here before.
  it("checks marks after many tags and many lowered halves in linear time", () => {
    const root = (body: string): string =>
      `<div xmlns="http://www.w3.org/1999/xhtml"><p>${body}</p></div>`;
    const tags = (count: number): string =>
      root(`t${"<b></b>".repeat(count)}${"\u2060".repeat(count)}x`);
    const halves = (count: number): string => root("t<sub>\u00bd</sub> ".repeat(count));
    expect(typeof tryXhtml(tags(20_000))).toBe("string");
    expect(typeof tryXhtml(halves(20_000))).toBe("string");
    for (const make of [tags, halves]) {
      const ratio = growth((count) => {
        const div = make(count);
        return () => tryXhtml(div);
      }, 5_000);
      expect(ratio).toBeLessThan(10);
    }
  }, 120_000);

  // Audit 2026-09-27 (F-5): an error's offset is in code points into the div, whatever the code.
  // A supplementary letter before the refused markup counts once, as it does in Python.
  it("reports every error at a code point offset into the div", () => {
    const letter = String.fromCodePoint(0x1d6fc);
    const offsetOf = (div: string): number | undefined => {
      try {
        xhtmlToText(div);
      } catch (error) {
        if (error instanceof XhtmlError) return error.offset;
        throw error;
      }
      return undefined;
    };
    const root = (inner: string): string =>
      `<div xmlns="http://www.w3.org/1999/xhtml"><p>${inner}</p></div>`;
    const tagAt = Array.from(root(letter)).length - "</p></div>".length;
    expect(offsetOf(root(`${letter}<q>x</q>`))).toBe(tagAt);
    expect(offsetOf(root(`${letter}&bogus;`))).toBe(tagAt);
    // The tag a combining mark follows, not the mark's place in the scanned text.
    expect(offsetOf(root(`${letter}e<b>${String.fromCodePoint(0x0301)}</b>`))).toBe(tagAt + 1);
    expect(offsetOf(root(`${letter}${String.fromCodePoint(0xfffe)}`))).toBe(tagAt);
  });

  // Section 2 applies to the div as decoded from JSON (RFC 8259). The vectors are written by
  // JSON.stringify, which never escapes a valid pair, so the escaped form is pinned here.
  it("reads an escaped surrogate pair in JSON as one code point", () => {
    const fromJson = (inner: string): string => {
      const opening = JSON.stringify('<div xmlns="http://www.w3.org/1999/xhtml"><p>');
      return JSON.parse(`${opening.slice(0, -1)}${inner}</p></div>"`) as string;
    };
    expect(tryXhtml(fromJson("\\ud835\\udefc"))).toBe("\n\n\u{1d6fc}\n\n");
    for (const rejected of ["\\ud835<b></b>\\udefc", "&#xD835;&#xDEFC;", "\\ud835"]) {
      expect(tryXhtml(fromJson(rejected)), rejected).toEqual({ error: "forbidden-character" });
    }
  });
});

describe("narrative fidelity verification", () => {
  it("matches every semantic expectation in the adversarial matrix", () => {
    for (const testCase of verifyCases) {
      const report = verifyNarrativeFidelity(testCase.input);
      expect(report.status, testCase.name).toBe(testCase.expect.status);
      for (const [key, status] of Object.entries(testCase.expect.sections ?? {})) {
        const section = report.sections.find(({ sourceKey }) => sourceKey === key);
        expect(section?.status, `${testCase.name}/${key}`).toBe(status);
      }
      for (const [key, reason] of Object.entries(testCase.expect.reasons ?? {})) {
        const section = report.sections.find(({ sourceKey }) => sourceKey === key);
        expect(section?.reason, `${testCase.name}/${key}`).toBe(reason);
      }
      if (testCase.expect.issueCount !== undefined) {
        expect(report.issues, testCase.name).toHaveLength(testCase.expect.issueCount);
      }
    }
  });

  it("produces byte-identical reports for the same input", () => {
    for (const testCase of verifyCases) {
      const first = verifyNarrativeFidelity(testCase.input);
      const second = verifyNarrativeFidelity(structuredClone(testCase.input));
      expect(canonicalJson(first), testCase.name).toBe(canonicalJson(second));
    }
  });

  it("throws FidelityError for structurally unusable input", () => {
    for (const testCase of throwCases) {
      expect(() => verifyNarrativeFidelity(testCase.input), testCase.name).toThrow(FidelityError);
    }
  });

  // Audit 2026-09-27 (F-5): a key or a version that is not a string is refused before it is
  // written into any string, the same way in both languages (Python writes `True` where
  // JavaScript writes `true`, and merges the keys `1` and `true`).
  it("refuses a source key or a normalisation version that is not a string", () => {
    const [valid] = verifyCases;
    if (valid === undefined) throw new Error("no verify case");
    const issuesOf = (input: unknown): string[] => {
      try {
        verifyNarrativeFidelity(input as FidelityInput);
      } catch (error) {
        if (error instanceof FidelityError) return error.issues;
        throw error;
      }
      return [];
    };
    const { input } = valid;
    expect(issuesOf({ ...input, normalizationVersion: true })).toEqual([
      `Expected ${NORMALIZATION_VERSION}, received a value that is not a string`,
    ]);
    expect(
      issuesOf({
        ...input,
        sections: input.sections.map((section, position) =>
          position === 0 ? { ...section, sourceKey: 1 } : section,
        ),
        provenance: [{ ...input.provenance[0], sourceKey: true }, ...input.provenance.slice(1)],
      }),
    ).toEqual(["Source section 0 has no string key", "Provenance entry 0 has no string key"]);
  });

  // Audit 2026-09-27 (F-1): the line a gap is on used to be read to both its ends for every gap.
  // Twenty thousand one-space spans across one whitespace line, and as many one-word spans on one
  // line of words, took about 50 s here before.
  it("verifies many spans on one long line in linear time", () => {
    // One-word spans on a line of words, and one-space spans on a line of spaces.
    const cases = (count: number): { text: string; div: string; spans: number[][] }[] => {
      const words = Array.from({ length: count }, () => "a").join(" ");
      return [
        {
          text: `${words}\n`,
          div: paragraphs(words),
          spans: Array.from({ length: count }, (_, position) => [2 * position, 2 * position + 1]),
        },
        {
          text: `x\n${" ".repeat(2 * count)}\n`,
          div: paragraphs("x"),
          spans: [
            [0, 1],
            ...Array.from({ length: count }, (_, position) => [2 + 2 * position, 3 + 2 * position]),
          ],
        },
      ];
    };
    const input = ({ text, div, spans }: { text: string; div: string; spans: number[][] }) => ({
      normalizationVersion: NORMALIZATION_VERSION,
      source: {
        extractorVersion: "synthetic-linear/1.0.0",
        pages: [{ page: 1, text, bodyStart: 0, bodyEnd: text.length }],
      },
      sections: [{ sourceKey: "s", path: "Composition.section[0]", div }],
      provenance: [
        {
          sourceKey: "s",
          spans: spans.map(([start = 0, end = 0]) => ({
            page: 1,
            startOffset: start,
            endOffset: end,
            textSha256: sha256Utf8(text.slice(start, end)),
          })),
          narrativeDivSha256: sha256Utf8(div),
          normalizedTextSha256: "0".repeat(64),
        },
      ],
    });
    for (const which of [0, 1]) {
      const full = cases(20_000)[which];
      if (full === undefined) throw new Error("fixture");
      expect(verifyNarrativeFidelity(input(full)).status).toBe("passed");
      const ratio = growth((count) => {
        const one = cases(count)[which];
        if (one === undefined) throw new Error("fixture");
        const prepared = input(one);
        return () => verifyNarrativeFidelity(prepared);
      }, 5_000);
      expect(ratio).toBeLessThan(10);
    }
  }, 120_000);

  it("binds the narratives it scanned, as computeNarrativeBinding does", () => {
    for (const testCase of verifyCases) {
      const report = verifyNarrativeFidelity(testCase.input);
      expect(report.narrativeBindingSha256, testCase.name).toBe(
        computeNarrativeBinding(testCase.input.sections).sha256,
      );
    }
  });

  it("never places narrative text in a report", () => {
    for (const testCase of verifyCases) {
      const report = verifyNarrativeFidelity(testCase.input);
      const serialized = JSON.stringify(report);
      for (const section of testCase.input.sections) {
        const extracted = tryXhtml(section.div);
        if (typeof extracted !== "string") continue;
        const normalized = tryNormalize(extracted);
        if (typeof normalized !== "string" || normalized.length < 12) continue;
        for (let start = 0; start + 12 <= normalized.length; start += 6) {
          expect(serialized, `${testCase.name}/${section.sourceKey}`).not.toContain(
            normalized.slice(start, start + 12),
          );
        }
      }
      for (const section of report.sections) {
        if (section.details === undefined) continue;
        expect(section.details.expectedSha256).toMatch(/^[0-9a-f]{64}$/);
        expect(section.details.actualSha256).toMatch(/^[0-9a-f]{64}$/);
      }
    }
  });

  it("binds the report to the Bundle narratives and detects tampering", () => {
    const passing = verifyCases.find(({ name }) => name === "exact-pass");
    if (passing === undefined) throw new Error("exact-pass case missing");
    const report = verifyNarrativeFidelity(passing.input);
    expect(computeNarrativeBinding(passing.input.sections).sha256).toBe(
      report.narrativeBindingSha256,
    );
    expect(verifyReportHash({ ...report, status: "failed" })).toBe(false);
    const altered = structuredClone(passing.input);
    const target = altered.sections[0];
    if (target === undefined) throw new Error("fixture");
    target.div = target.div.replace("clinical", "veterinary");
    expect(computeNarrativeBinding(altered.sections).sha256).not.toBe(
      report.narrativeBindingSha256,
    );
  });

  it("reports page totals next to body totals and bounds what a body may exclude", () => {
    const full = verifyCases.find(({ name }) => name === "exact-pass");
    const shrunken = verifyCases.find(({ name }) => name === "excluded-text-budget");
    if (full === undefined || shrunken === undefined) throw new Error("fixture");
    const baseline = verifyNarrativeFidelity(full.input);
    expect(baseline.coverage.pageCodePoints).toBeGreaterThan(baseline.coverage.bodyCodePoints);
    const report = verifyNarrativeFidelity(shrunken.input);
    expect(report.status).toBe("failed");
    expect(report.issues.some((issue) => issue.endsWith("excluded-text"))).toBe(true);
  });

  it("verifies random word-aligned substrings of any page body", () => {
    const source = buildSource();
    const random = mulberry32(20260919);
    for (let iteration = 0; iteration < 200; iteration += 1) {
      const page = source.pages[Math.floor(random() * source.pages.length)];
      if (page === undefined) throw new Error("fixture");
      const body = Array.from(page.text).slice(page.bodyStart, page.bodyEnd).join("");
      const words = body.split(/\s+/).filter((word) => word.length > 0 && !word.includes("­"));
      const start = Math.floor(random() * words.length);
      const end = start + 1 + Math.floor(random() * Math.min(6, words.length - start));
      const needle = words.slice(start, end).join(" ");
      if (needle.length < 8) continue;
      const occurrences = body.split(needle).length - 1;
      if (occurrences !== 1) continue;
      const span = spanFor(source, page.page, needle);
      const report = verifyNarrativeFidelity({
        normalizationVersion: NORMALIZATION_VERSION,
        source,
        sections: [
          { sourceKey: "smpc.1", path: "Composition.section[0]", div: paragraphs(needle) },
        ],
        provenance: [
          {
            sourceKey: "smpc.1",
            spans: [span],
            narrativeDivSha256: sha256Utf8(paragraphs(needle)),
            normalizedTextSha256: sha256Utf8(normalizeText(needle)),
          },
        ],
      });
      expect(report.status, needle).toBe("passed");
    }
  });

  it("rejects random single-character mutations of the narrative and of the span", () => {
    const passing = verifyCases.find(({ name }) => name === "exact-pass");
    if (passing === undefined) throw new Error("exact-pass case missing");
    const random = mulberry32(19092026);
    const alphabet = "abcdefghijklmnopqrstuvwxyz0123456789";
    for (let iteration = 0; iteration < 200; iteration += 1) {
      const input = structuredClone(passing.input);
      const index = Math.floor(random() * input.sections.length);
      const section = input.sections[index];
      if (section === undefined) throw new Error("fixture");
      const open = section.div.indexOf("<p>") + 3;
      const close = section.div.indexOf("</p>");
      if (open < 3 || close <= open) continue;
      const text = Array.from(section.div.slice(open, close));
      const position = Math.floor(random() * text.length);
      const current = text[position] ?? "";
      if (!/[a-z0-9]/i.test(current)) continue;
      let replacement = alphabet[Math.floor(random() * alphabet.length)] ?? "x";
      if (replacement === current.toLowerCase()) replacement = replacement === "a" ? "b" : "a";
      const mode = random();
      if (mode < 0.34) text[position] = replacement;
      else if (mode < 0.67) text.splice(position, 1);
      else text.splice(position, 0, replacement);
      section.div = `${section.div.slice(0, open)}${text.join("")}${section.div.slice(close)}`;
      const report = verifyNarrativeFidelity(input);
      expect(report.sections[index]?.status, `narrative mutation ${iteration}`).not.toBe(
        "verified",
      );

      const spanInput = structuredClone(passing.input);
      const entry = spanInput.provenance[index];
      const span = entry?.spans[0];
      if (entry === undefined || span === undefined) throw new Error("fixture");
      const digest = Array.from(span.textSha256);
      const hexPosition = Math.floor(random() * digest.length);
      digest[hexPosition] = digest[hexPosition] === "0" ? "1" : "0";
      span.textSha256 = digest.join("");
      const spanReport = verifyNarrativeFidelity(spanInput);
      expect(spanReport.sections[index]?.status, `span mutation ${iteration}`).toBe(
        "span-not-found",
      );
    }
  });
});
