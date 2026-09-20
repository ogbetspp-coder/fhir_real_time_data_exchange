import { readFile } from "node:fs/promises";
import path from "node:path";

import { beforeAll, describe, expect, it } from "vitest";

import {
  FidelityError,
  NORMALIZATION_VERSION,
  NormalizationError,
  XhtmlError,
  computeNarrativeBinding,
  normalizeText,
  verifyNarrativeFidelity,
  verifyReportHash,
  xhtmlToText,
  type FidelityReport,
} from "../src/fidelity/index.js";
import { canonicalJson, sha256Utf8 } from "../src/lib/hash.js";
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

describe("xhtml scanner", () => {
  it("matches the case expectations", () => {
    for (const testCase of xhtmlCases) {
      expect(tryXhtml(testCase.input), testCase.name).toEqual(testCase.expected);
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
