import { z } from "zod";

import { Count, NormalizationVersion, PositiveInt, Sha256Hex, SourceKey, Token } from "./common.js";

// The extractor's page text. It contains narrative, so it travels by reference and is never
// logged or placed in a submission; it is validated here because it is untrusted input to the
// Zone B re-execution of the fidelity check.
export const SourceDocumentTextSchema = z
  .strictObject({
    extractorVersion: Token,
    pages: z
      .array(
        z.strictObject({
          page: PositiveInt,
          text: z.string().max(500_000),
          bodyStart: Count,
          bodyEnd: Count,
        }),
      )
      .min(1)
      .max(2_000),
  })
  .meta({ id: "SourceDocumentText" });

// Wire schema for the FidelityReport that Zone A produces and Zone B re-verifies. It mirrors
// `src/fidelity/verify.ts`'s FidelityReport type field for field so that a report crossing the
// zone boundary is validated like every other contract object, not merely typed.

export const SectionStatus = z
  .enum([
    "verified",
    "mismatch",
    "span-not-found",
    "invalid-provenance",
    "missing-provenance",
    "malformed-narrative",
  ])
  .meta({ id: "SectionStatus" });

export const DiffHintSchema = z
  .strictObject({
    expectedLength: Count,
    actualLength: Count,
    firstDifferingOffset: Count,
    commonSuffixLength: Count,
    expectedWordCount: Count,
    actualWordCount: Count,
    expectedSha256: Sha256Hex,
    actualSha256: Sha256Hex,
  })
  .meta({ id: "DiffHint", description: "Lengths, offsets, counts, and hashes only; never text." });

export const SectionResultSchema = z
  .strictObject({
    sourceKey: SourceKey,
    path: z.string().regex(/^Composition\.section\[[0-9]+\](?:\.section\[[0-9]+\])*$/),
    status: SectionStatus,
    spanCount: Count,
    reason: z
      .string()
      .regex(/^[a-z][a-z-]{0,40}$/)
      .optional(),
    details: DiffHintSchema.optional(),
    normalizedTextSha256: Sha256Hex.optional(),
  })
  .meta({ id: "SectionResult" });

export const FidelityReportSchema = z
  .strictObject({
    reportVersion: z.literal("1.0.0"),
    normalizationVersion: NormalizationVersion,
    extractedTextSha256: Sha256Hex,
    narrativeBindingSha256: Sha256Hex,
    status: z.enum(["passed", "failed"]),
    sections: z.array(SectionResultSchema),
    issues: z.array(z.string().max(256)),
    summary: z.strictObject({ total: Count, verified: Count }),
    coverage: z.strictObject({
      pageCodePoints: Count,
      bodyCodePoints: Count,
      coveredCodePoints: Count,
      uncoveredGaps: Count,
    }),
    reportHash: Sha256Hex,
  })
  .meta({ id: "FidelityReport" });
