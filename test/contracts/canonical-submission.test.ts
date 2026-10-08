import { beforeAll, describe, expect, it } from "vitest";

import {
  CanonicalSubmissionSchema,
  MAX_NARRATIVE_LENGTH,
  MAX_SPANS,
  MAX_SPANS_PER_SECTION,
  SubmissionRejectedError,
  approvedContent,
  verifyDocumentSubmission,
  type CanonicalSubmission,
  type SectionProvenance,
} from "../../src/contracts/index.js";
import {
  NORMALIZATION_VERSION,
  verifyNarrativeFidelity,
  type SourceDocumentText,
} from "../../src/fidelity/index.js";
import { loadEmaMapping, type EmaMapping } from "../../src/fhir/mapping.js";
import {
  createSyntheticSubmission,
  type SyntheticSubmission,
} from "../../src/fixtures/synthetic-submission.js";
import { sha256 } from "../../src/lib/hash.js";
import { SYNTHETIC, attested } from "../support/submission.js";

let mapping: EmaMapping;
let fixture: SyntheticSubmission;

beforeAll(async () => {
  mapping = await loadEmaMapping();
  fixture = createSyntheticSubmission(mapping);
});

function clone(): CanonicalSubmission {
  return structuredClone(fixture.submission);
}

// Re-hashes a mutated submission so the tampering under test is the only invariant that fails.
function seal(submission: CanonicalSubmission): CanonicalSubmission {
  submission.bundleSha256 = sha256(submission.bundle);
  submission.approval.approvedContentSha256 = sha256(approvedContent(submission));
  return submission;
}

function firstSection(submission: CanonicalSubmission): SectionProvenance {
  const [section] = submission.provenance.sections;
  if (section === undefined) throw new Error("Synthetic submission requires provenance sections");
  return section;
}

function reject(
  submission: unknown,
  sourceText: SourceDocumentText = fixture.sourceText,
): SubmissionRejectedError {
  let caught: unknown;
  try {
    verifyDocumentSubmission(
      { submission, fidelityReport: fixture.fidelityReport, sourceText },
      mapping.sourceCodeSystem,
      SYNTHETIC,
    );
  } catch (error) {
    caught = error;
  }
  expect(caught).toBeInstanceOf(SubmissionRejectedError);
  if (!(caught instanceof SubmissionRejectedError)) throw new Error("expected a rejection");
  return caught;
}

function rejectedByParse(submission: unknown, message: string): void {
  const parsed = CanonicalSubmissionSchema.safeParse(submission);
  expect(parsed.success).toBe(false);
  expect(reject(submission).issues).toContain(message);
}

describe("canonical submission contract", () => {
  it("accepts the synthetic Zone A hand-off", () => {
    const parsed = CanonicalSubmissionSchema.safeParse(fixture.submission);

    expect(parsed.success).toBe(true);
    expect(fixture.submission.schemaVersion).toBe("3.0.0");
    expect(fixture.submission.provenance.fidelity.status).toBe("passed");
    expect(() =>
      verifyDocumentSubmission(
        {
          submission: fixture.submission,
          fidelityReport: fixture.fidelityReport,
          sourceText: fixture.sourceText,
        },
        mapping.sourceCodeSystem,
        SYNTHETIC,
      ),
    ).not.toThrow();
  });

  it("rejects an unknown top-level key", () => {
    const smuggled: Record<string, unknown> = { ...clone(), extraneous: "content" };

    rejectedByParse(smuggled, 'Unrecognized key: "extraneous"');
  });

  it("rejects a bundleSha256 that does not match the Bundle", () => {
    const submission = clone();
    submission.bundleSha256 = "0".repeat(64);

    rejectedByParse(submission, "bundleSha256 does not match the Bundle");
  });

  it("rejects an approval hash that does not match the approved content", () => {
    const submission = clone();
    submission.approval.approvedContentSha256 = "0".repeat(64);

    rejectedByParse(
      submission,
      "approval.approvedContentSha256 does not match the submitted content",
    );
  });

  it("rejects a failed fidelity status", () => {
    const submission = clone();
    submission.provenance.fidelity.status = "failed";

    rejectedByParse(seal(submission), "fidelity.status must be passed");
  });

  it("rejects a fidelity summary whose matched count is short of the checked count", () => {
    const submission = clone();
    submission.provenance.fidelity.sectionsMatched =
      submission.provenance.fidelity.sectionsChecked - 1;

    rejectedByParse(
      seal(submission),
      "fidelity.sectionsMatched must equal fidelity.sectionsChecked",
    );
  });

  it("rejects a code-mapped decision without a terminology reference", () => {
    const submission = clone();
    submission.provenance.decisions = submission.provenance.decisions.map((decision) =>
      decision.action === "code-mapped"
        ? { target: decision.target, action: decision.action }
        : decision,
    );

    rejectedByParse(
      seal(submission),
      "provenance.decisions[32]: code-mapped requires terminologyRef",
    );
  });

  it("rejects code-mapped decisions when no terminology service is declared", () => {
    const submission = clone();
    const { extractionRunId, serviceVersion, parser } = submission.provenance.extraction;
    submission.provenance.extraction = { extractionRunId, serviceVersion, parser };

    expect(submission.provenance.decisions.some(({ action }) => action === "code-mapped")).toBe(
      true,
    );
    rejectedByParse(
      seal(submission),
      "extraction.terminologyService is required when any decision is code-mapped",
    );
  });

  it("rejects a human-edited decision without an editor id", () => {
    const submission = clone();
    const position = submission.provenance.decisions.length;
    submission.provenance.decisions.push({
      target: "Composition.title",
      action: "human-edited",
      reason: "metadata-correction",
    });

    rejectedByParse(
      seal(submission),
      `provenance.decisions[${position}]: human-edited requires editorId`,
    );
  });

  it("rejects duplicate provenance sourceKeys", () => {
    const submission = clone();
    const [first, second] = submission.provenance.sections;
    if (first === undefined || second === undefined) {
      throw new Error("Synthetic submission requires at least two provenance sections");
    }
    second.sourceKey = first.sourceKey;

    rejectedByParse(seal(submission), "provenance.sections contains duplicate sourceKey");
  });

  it("rejects an orphan provenance entry", () => {
    const submission = clone();
    submission.provenance.sections.push({
      ...structuredClone(firstSection(submission)),
      sourceKey: "smpc.99.orphan",
    });
    submission.provenance.fidelity.sectionsChecked = submission.provenance.sections.length;
    submission.provenance.fidelity.sectionsMatched = submission.provenance.sections.length;

    const parsed = CanonicalSubmissionSchema.safeParse(seal(submission));
    expect(parsed.success).toBe(true);
    expect(reject(submission).issues).toContain("Orphan provenance for section smpc.99.orphan");
  });

  it("rejects a Bundle section whose provenance entry was removed", () => {
    const submission = clone();
    const removed = firstSection(submission);
    submission.provenance.sections = submission.provenance.sections.filter(
      ({ sourceKey }) => sourceKey !== removed.sourceKey,
    );
    submission.provenance.fidelity.sectionsChecked = submission.provenance.sections.length;
    submission.provenance.fidelity.sectionsMatched = submission.provenance.sections.length;

    const parsed = CanonicalSubmissionSchema.safeParse(seal(submission));
    expect(parsed.success).toBe(true);
    expect(reject(submission).issues).toContain(
      `Missing provenance for section ${removed.sourceKey}`,
    );
  });

  it("rejects an approverId that looks like an e-mail address", () => {
    const submission = clone();
    attested(submission).approverId = "reviewer@example.com";

    rejectedByParse(
      submission,
      "approval.approverId: approverId must be an opaque principal identifier, not an e-mail address",
    );
  });

  it("rejects a superseded normalization version", () => {
    const submission = clone();
    submission.provenance.fidelity.normalizationVersion = "fidelity-norm/0.9.0";

    rejectedByParse(seal(submission), "fidelity.normalizationVersion must be fidelity-norm/3.5.0");
  });

  // Audit 2026-09-27 (F-1): the spans the gate re-executes are bounded, per section and in all.
  it("bounds the spans of a section and of the whole provenance", () => {
    const submission = clone();
    const first = firstSection(submission);
    const [span] = first.spans;
    if (span === undefined) throw new Error("Synthetic provenance requires a span");
    first.spans = Array.from({ length: MAX_SPANS_PER_SECTION + 1 }, () => ({ ...span }));
    rejectedByParse(
      seal(submission),
      `provenance.sections[0] has more than ${MAX_SPANS_PER_SECTION} spans`,
    );

    const many = clone();
    const template = firstSection(many);
    const sections = Math.ceil((MAX_SPANS + 1) / MAX_SPANS_PER_SECTION);
    many.provenance.sections = Array.from({ length: sections }, (_, position) => ({
      ...structuredClone(template),
      sourceKey: `smpc.99.${position}`,
      spans: Array.from({ length: MAX_SPANS_PER_SECTION }, () => ({ ...span })),
    }));
    const parsed = CanonicalSubmissionSchema.safeParse(seal(many));
    expect(parsed.success).toBe(false);
    expect(parsed.error?.issues.map(({ message }) => message)).toContain(
      `provenance.sections have more than ${MAX_SPANS} spans`,
    );
  });

  // Audit 2026-09-27 (F-3): a narrative is scanned several times at this gate, so its length is
  // bounded before the first scan.
  it("bounds a narrative's length before scanning it", () => {
    const submission = clone();
    const composition = submission.bundle.entry[0]?.resource as
      { section?: { text?: { div: string } }[] } | undefined;
    const section = composition?.section?.find(({ text }) => text !== undefined);
    if (section?.text === undefined) throw new Error("Synthetic bundle requires a narrative");
    section.text.div = `<div xmlns="http://www.w3.org/1999/xhtml"><p>${"a".repeat(MAX_NARRATIVE_LENGTH)}</p></div>`;

    const issues = reject(seal(submission)).issues;
    expect(issues.filter((issue) => issue.includes("narrative exceeds"))).toEqual([
      `Composition.section[${composition?.section?.indexOf(section) ?? -1}] narrative exceeds ${MAX_NARRATIVE_LENGTH} UTF-16 code units`,
    ]);
  });

  it("rejects source text that does not match sourceDocument.extractedText.sha256", () => {
    const altered = structuredClone(fixture.sourceText);
    const [page] = altered.pages;
    if (page === undefined) throw new Error("Synthetic source text requires pages");
    page.text = page.text.replace("demonstration", "demonstratiom");

    expect(reject(fixture.submission, altered).issues).toContain(
      "Extracted source text does not match sourceDocument.extractedText.sha256",
    );
  });

  it("rejects source text altered by one character even when its hash is re-declared", () => {
    const altered = structuredClone(fixture.sourceText);
    const [page] = altered.pages;
    if (page === undefined) throw new Error("Synthetic source text requires pages");
    page.text = page.text.replace("demonstration", "demonstratiom");

    const submission = clone();
    submission.provenance.sourceDocument.extractedText.sha256 = sha256(altered);

    const parsed = CanonicalSubmissionSchema.safeParse(seal(submission));
    expect(parsed.success).toBe(true);
    expect(reject(submission, altered).issues).toContain(
      "Re-executed fidelity check does not reproduce the declared report",
    );
  });

  it("rejects prose in a non-narrative Bundle string by word count, not only by length", () => {
    const submission = clone();
    const composition = submission.bundle.entry[0]?.resource as { title?: string } | undefined;
    if (composition === undefined) throw new Error("Synthetic bundle requires a Composition");
    composition.title =
      "Take one tablet twice daily with food and do not exceed two tablets in any twenty four hour period at all";

    const parsed = CanonicalSubmissionSchema.safeParse(seal(submission));
    expect(parsed.success).toBe(true);
    expect(reject(submission).issues).toContain("Unverified free text at entry[0].resource.title");
  });

  it("rejects prose smuggled as a property name", () => {
    const submission = clone();
    const composition = submission.bundle.entry[0]?.resource as Record<string, unknown> | undefined;
    if (composition === undefined) throw new Error("Synthetic bundle requires a Composition");
    composition["take one tablet twice daily"] = "x";

    const parsed = CanonicalSubmissionSchema.safeParse(seal(submission));
    expect(parsed.success).toBe(true);
    expect(reject(submission).issues).toContain(
      "Unverified free text in a property name at entry[0].resource",
    );
  });

  // Each key is an identifier, but 5,000 of them carried ~290,000 characters past the 40,000
  // aggregate budget while it counted strings only.
  it("counts property names toward the aggregate budget", () => {
    const submission = clone();
    const organization = submission.bundle.entry[1]?.resource as
      Record<string, unknown> | undefined;
    if (organization === undefined) throw new Error("Synthetic bundle requires a second entry");
    organization.note = Object.fromEntries(
      Array.from({ length: 5_000 }, (_, index) => [
        `TakeOneTabletTwiceDailyWithFoodAndPlentyOfWater${index}`,
        true,
      ]),
    );

    const parsed = CanonicalSubmissionSchema.safeParse(seal(submission));
    expect(parsed.success).toBe(true);
    expect(reject(submission).issues).toEqual([
      "Unverified strings and property names exceed 40000 characters in total",
    ]);
  });

  // JSON.parse makes an own `__proto__` member; zod's parse drops it, so every hash the gate
  // checked was over a view without it, while the stored object kept it.
  it("rejects an own __proto__ member the parse would drop", () => {
    const text = JSON.stringify(fixture.submission).replace(
      '"bundle":{',
      '"bundle":{"__proto__":{"note":"Take one tablet twice daily with food, and never more."},',
    );
    const submission = JSON.parse(text) as CanonicalSubmission;
    expect(Object.keys(submission.bundle)).toContain("__proto__");
    expect(sha256(submission.bundle)).not.toBe(submission.bundleSha256);

    expect(reject(submission).issues).toEqual([
      "submission carries the reserved property name __proto__",
    ]);
  });

  // A report that failed, and says so consistently: its hash recomputes, the submission names it,
  // and re-executing the check reproduces it. Only its status stands between it and acceptance.
  it("rejects a self-consistent fidelity report that failed", () => {
    const { narrativeSections } = verifyDocumentSubmission(
      {
        submission: fixture.submission,
        fidelityReport: fixture.fidelityReport,
        sourceText: fixture.sourceText,
      },
      mapping.sourceCodeSystem,
      SYNTHETIC,
    );
    const sourceText = structuredClone(fixture.sourceText);
    for (const page of sourceText.pages) page.text = page.text.replace(/[a-z]/g, "q");
    const submission = clone();
    const failed = verifyNarrativeFidelity({
      normalizationVersion: NORMALIZATION_VERSION,
      source: sourceText,
      sections: narrativeSections,
      provenance: submission.provenance.sections,
    });
    expect(failed.status).toBe("failed");
    expect(failed.summary.verified).toBe(0);
    submission.provenance.sourceDocument.extractedText.sha256 = sha256(sourceText);
    submission.provenance.fidelity.reportSha256 = failed.reportHash;

    let caught: unknown;
    try {
      verifyDocumentSubmission(
        { submission: seal(submission), fidelityReport: failed, sourceText },
        mapping.sourceCodeSystem,
        SYNTHETIC,
      );
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(SubmissionRejectedError);
    expect((caught as SubmissionRejectedError).issues).toEqual([
      "Fidelity report status must be passed",
    ]);
  });

  it("rejects a narrative outside the verified sections", () => {
    const submission = clone();
    const composition = submission.bundle.entry[0]?.resource as Record<string, unknown> | undefined;
    if (composition === undefined) throw new Error("Synthetic bundle requires a Composition");
    composition.text = {
      status: "generated",
      div: '<div xmlns="http://www.w3.org/1999/xhtml">Not for clinical use.</div>',
    };

    const parsed = CanonicalSubmissionSchema.safeParse(seal(submission));
    expect(parsed.success).toBe(true);
    expect(reject(submission).issues).toEqual([
      "Narrative outside verified sections at entry[0].resource.text.div",
    ]);
  });

  it("rejects prose in a provenance identifier field", () => {
    const submission = clone();
    submission.provenance.extraction.parser.name = "take one tablet twice daily";

    rejectedByParse(
      seal(submission),
      "provenance.extraction.parser.name: Invalid string: must match pattern /^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$/",
    );
  });

  it("rejects prose in approverId and recordRef", () => {
    const withApprover = clone();
    attested(withApprover).approverId = "患者は本剤を一日二回服用すること";
    rejectedByParse(
      withApprover,
      "approval.approverId: approverId must be an opaque principal identifier, not an e-mail address",
    );

    const withRecord = clone();
    attested(withRecord).recordRef = "take_one_tablet_twice_daily_with_food_<script>";
    const parsed = CanonicalSubmissionSchema.safeParse(withRecord);
    expect(parsed.success).toBe(false);
    expect(reject(withRecord).issues.some((issue) => issue.startsWith("approval.recordRef"))).toBe(
      true,
    );
  });

  it("rejects URL fields that carry whitespace or unbounded paths", () => {
    const withSystem = clone();
    const decision = withSystem.provenance.decisions.find(
      ({ terminologyRef }) => terminologyRef !== undefined,
    );
    if (decision?.terminologyRef === undefined)
      throw new Error("fixture needs a code-mapped decision");
    decision.terminologyRef.system = "https://example.org/take one tablet twice daily";
    expect(
      reject(seal(withSystem)).issues.some((issue) => issue.includes("terminologyRef.system")),
    ).toBe(true);

    const withUri = clone();
    withUri.provenance.sourceDocument.extractedText.uri = `gs://evidence/${"word_".repeat(200)}`;
    expect(reject(seal(withUri)).issues.some((issue) => issue.includes("extractedText.uri"))).toBe(
      true,
    );
  });

  it("rejects an aggregate of short strings that adds up to a document", () => {
    const submission = clone();
    const organization = submission.bundle.entry[1]?.resource as
      Record<string, unknown> | undefined;
    if (organization === undefined) throw new Error("Synthetic bundle requires a second entry");
    organization.alias = Array.from({ length: 3_001 }, (_, index) => `alias-${index}`);

    const parsed = CanonicalSubmissionSchema.safeParse(seal(submission));
    expect(parsed.success).toBe(true);
    expect(reject(submission).issues).toContain("Bundle carries more than 3000 unverified strings");
  });

  it("counts words in scripts without inter-word spaces", () => {
    const submission = clone();
    const organization = submission.bundle.entry[1]?.resource as
      Record<string, unknown> | undefined;
    if (organization === undefined) throw new Error("Synthetic bundle requires a second entry");
    organization.name =
      "本剤は肝機能障害のある患者には慎重に投与すること。重篤な肝障害が報告されているため、投与開始前および投与中は定期的に肝機能検査を実施すること。";

    const parsed = CanonicalSubmissionSchema.safeParse(seal(submission));
    expect(parsed.success).toBe(true);
    expect(reject(submission).issues).toContain("Unverified free text at entry[1].resource.name");
  });

  it("rejects pathological nesting before anything recursive touches it", () => {
    const submission = clone() as unknown as Record<string, unknown>;
    let nested: unknown = "x";
    for (let depth = 0; depth < 5_000; depth += 1) nested = [nested];
    (submission.bundle as Record<string, unknown>).extension = nested;

    expect(reject(submission).issues).toContain("submission nesting exceeds depth 48");
  });

  it("rejects malformed source text as a contract issue rather than a type error", () => {
    const malformed = { extractorVersion: "x", pages: "not-an-array" };

    const { issues } = reject(fixture.submission, malformed as unknown as SourceDocumentText);
    expect(issues.some((issue) => issue.startsWith("sourceText.pages"))).toBe(true);
  });

  it("folds a structurally unusable re-execution into the rejection", () => {
    const duplicated = structuredClone(fixture.sourceText);
    const [page] = duplicated.pages;
    if (page === undefined) throw new Error("Synthetic source text requires pages");
    duplicated.pages.push(structuredClone(page));

    const submission = clone();
    submission.provenance.sourceDocument.extractedText.sha256 = sha256(duplicated);

    const parsed = CanonicalSubmissionSchema.safeParse(seal(submission));
    expect(parsed.success).toBe(true);
    expect(reject(submission, duplicated).issues).toContain(
      "Fidelity re-execution: Duplicate page number 1",
    );
  });

  it("keeps every rejection reason free of narrative text", () => {
    const submission = clone();
    submission.provenance.fidelity.status = "failed";

    for (const issue of reject(seal(submission)).issues) {
      expect(issue).not.toContain("Synthetic demonstration content");
    }
  });
});
