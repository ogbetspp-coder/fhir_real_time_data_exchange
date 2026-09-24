import { beforeAll, describe, expect, it, vi } from "vitest";

import { loadConfig, type AppConfig } from "../src/config.js";
import type { SectionProvenance, SourceSpan } from "../src/contracts/index.js";
import {
  NORMALIZATION_VERSION,
  collectNarrativeSections,
  normalizeText,
  verifyNarrativeFidelity,
  xhtmlToText,
  type NarrativeSection,
  type SourceDocumentText,
} from "../src/fidelity/index.js";
import { loadEmaMapping, type EmaMapping } from "../src/fhir/mapping.js";
import { toProvenanceResource } from "../src/fhir/provenance.js";
import { isComposition } from "../src/fhir/types.js";
import { createSyntheticSubmission } from "../src/fixtures/synthetic-submission.js";
import { createSyntheticType2Bundle } from "../src/fixtures/synthetic.js";
import { sha256, sha256Utf8 } from "../src/lib/hash.js";
import { runPipeline } from "../src/pipeline.js";

// ADR 0002/0003: evidence, provenance, and logs carry hashes, counts, enums, ids, and offsets
// only. This suite scans everything the document path emits for the narrative it processed.

const DOCUMENT_RUN_ID = "33333333-3333-4333-a333-333333333333";
const WINDOW_SIZE = 40;
const WINDOW_STEP = 20;

let mapping: EmaMapping;
let config: AppConfig;

beforeAll(async () => {
  mapping = await loadEmaMapping();
  config = loadConfig({ NODE_ENV: "test", DRY_RUN: "true", GCP_LOCATION: "europe-west4" });
});

type NarrativeWindow = { sourceKey: string; offset: number; text: string };

function slidingWindows(sourceKey: string, text: string): NarrativeWindow[] {
  if (text.length === 0) return [];
  if (text.length <= WINDOW_SIZE) return [{ sourceKey, offset: 0, text }];
  const found: NarrativeWindow[] = [];
  for (let offset = 0; offset + WINDOW_SIZE <= text.length; offset += WINDOW_STEP) {
    found.push({ sourceKey, offset, text: text.slice(offset, offset + WINDOW_SIZE) });
  }
  return found;
}

function narrativeWindows(sections: NarrativeSection[]): NarrativeWindow[] {
  return sections.flatMap(({ sourceKey, div }) =>
    slidingWindows(sourceKey, normalizeText(xhtmlToText(div))),
  );
}

// Reports where a leak was found, never the narrative that leaked: a failing assertion here
// must not itself print clinical text.
function firstLeak(haystack: string, candidates: NarrativeWindow[]): string | undefined {
  const leaked = candidates.find(({ text }) => haystack.includes(text));
  return leaked === undefined ? undefined : `${leaked.sourceKey}@${String(leaked.offset)}`;
}

function captureLines(): { lines: string[]; restore: () => void } {
  const lines: string[] = [];
  const push = (...args: unknown[]): void => {
    for (const arg of args) lines.push(typeof arg === "string" ? arg : JSON.stringify(arg));
  };
  const outSpy = vi.spyOn(console, "log").mockImplementation(push);
  const errSpy = vi.spyOn(console, "error").mockImplementation(push);
  return {
    lines,
    restore: () => {
      outSpy.mockRestore();
      errSpy.mockRestore();
    },
  };
}

function syntheticNarrativeSections(): NarrativeSection[] {
  const bundle = createSyntheticType2Bundle(mapping);
  const composition = bundle.entry[0]?.resource;
  if (composition === undefined || !isComposition(composition)) {
    throw new Error("Synthetic Type 2 fixture must have Composition as its first entry");
  }
  return collectNarrativeSections(composition, mapping.sourceCodeSystem);
}

describe("document path narrative containment", () => {
  it("emits no narrative into evidence, provenance, the fidelity report, or logs", async () => {
    const { submission, fidelityReport, sourceText } = createSyntheticSubmission(mapping);
    // The sections scanned for must be exactly the sections that were submitted.
    expect(sha256(createSyntheticType2Bundle(mapping))).toBe(submission.bundleSha256);
    const sections = syntheticNarrativeSections();
    const candidates = narrativeWindows(sections);
    expect(sections).toHaveLength(32);
    expect(candidates.length).toBeGreaterThan(sections.length);

    const capture = captureLines();
    let result;
    try {
      result = await runPipeline(
        {
          runId: DOCUMENT_RUN_ID,
          sourceKind: "document",
          submission,
          fidelityReport,
          sourceText,
          sourceResource: "document:synthetic-smpc",
        },
        mapping,
        config,
      );
    } finally {
      capture.restore();
    }

    // A vacuous log scan would prove nothing: the run must actually have logged.
    expect(capture.lines.length).toBeGreaterThanOrEqual(2);
    expect(result.evidence.manifest.ingestion?.fidelity.status).toBe("passed");

    const scanned: [string, string][] = [
      ["evidence", JSON.stringify(result.evidence)],
      ["fidelityReport", JSON.stringify(fidelityReport)],
      ["ingestionProvenance", JSON.stringify(submission.provenance)],
      [
        "provenanceResource",
        JSON.stringify(
          toProvenanceResource(submission, fidelityReport, { bundleId: "b", compositionId: "c" }),
        ),
      ],
      ["logs", capture.lines.join("\n")],
    ];
    for (const [name, haystack] of scanned) {
      expect([name, firstLeak(haystack, candidates)]).toEqual([name, undefined]);
    }

    // Self-check: the same scan detects a single injected window, so the clean results above
    // are evidence of containment and not of a scan that can never match.
    const probe = candidates[0];
    if (probe === undefined) throw new Error("expected at least one narrative window");
    const poisoned = JSON.stringify({
      ...result.evidence,
      manifest: {
        ...result.evidence.manifest,
        source: { ...result.evidence.manifest.source, resource: probe.text },
      },
    });
    expect(firstLeak(poisoned, candidates)).toBe(`${probe.sourceKey}@${String(probe.offset)}`);
  });
});

const HASH_LIKE = "deadbeef".repeat(8);
const ADVERSARIAL_DIV =
  '<div xmlns="http://www.w3.org/1999/xhtml"><p>Batch sha256 ' +
  HASH_LIKE +
  " recorded for synthetic demonstration content; not for clinical use.</p></div>";

function codePointLength(text: string): number {
  return Array.from(text).length;
}

// A non-empty body ends with its own line terminator (spec section 1), so the page carries one
// after the text the span covers.
function singlePageSource(body: string): SourceDocumentText {
  const text = `${body}\n`;
  return {
    extractorVersion: "adversarial-fixture/1.0.0",
    pages: [{ page: 1, text, bodyStart: 0, bodyEnd: codePointLength(text) }],
  };
}

function wholePageSpan(body: string): SourceSpan {
  return {
    page: 1,
    startOffset: 0,
    endOffset: codePointLength(body),
    textSha256: sha256Utf8(body),
  };
}

function adversarialCase(body: string): {
  sections: NarrativeSection[];
  provenance: SectionProvenance[];
  source: SourceDocumentText;
} {
  const sections: NarrativeSection[] = [
    { sourceKey: "smpc", path: "Composition.section[0]", div: ADVERSARIAL_DIV },
  ];
  const provenance: SectionProvenance[] = [
    {
      sourceKey: "smpc",
      spans: [wholePageSpan(body)],
      narrativeDivSha256: sha256Utf8(ADVERSARIAL_DIV),
      normalizedTextSha256: sha256Utf8(normalizeText(xhtmlToText(ADVERSARIAL_DIV))),
    },
  ];
  return { sections, provenance, source: singlePageSource(body) };
}

describe("hash-like narrative containment", () => {
  it("keeps hash-shaped narrative out of a passing fidelity report", () => {
    const body = normalizeText(xhtmlToText(ADVERSARIAL_DIV));
    const { sections, provenance, source } = adversarialCase(body);

    const report = verifyNarrativeFidelity({
      normalizationVersion: NORMALIZATION_VERSION,
      source,
      sections,
      provenance,
    });
    const serialized = JSON.stringify(report);

    expect(report.status).toBe("passed");
    expect(report.summary).toEqual({ total: 1, verified: 1 });
    expect(serialized.includes(HASH_LIKE)).toBe(false);
    expect(firstLeak(serialized, narrativeWindows(sections))).toBeUndefined();
  });

  it("keeps hash-shaped narrative out of a failing report's diff hint", () => {
    const body = `${normalizeText(xhtmlToText(ADVERSARIAL_DIV))} Amended wording.`;
    const { sections, provenance, source } = adversarialCase(body);

    const report = verifyNarrativeFidelity({
      normalizationVersion: NORMALIZATION_VERSION,
      source,
      sections,
      provenance,
    });
    const serialized = JSON.stringify(report);

    expect(report.status).toBe("failed");
    expect(report.sections[0]?.status).toBe("mismatch");
    expect(report.sections[0]?.details?.expectedSha256).toBe(sha256Utf8(body));
    expect(serialized.includes(HASH_LIKE)).toBe(false);
    expect(
      firstLeak(serialized, [
        ...narrativeWindows(sections),
        ...slidingWindows("source-page", body),
      ]),
    ).toBeUndefined();
  });
});
