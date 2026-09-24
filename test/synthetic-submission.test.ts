import { beforeAll, describe, expect, it } from "vitest";

import { CanonicalSubmissionSchema, verifyDocumentSubmission } from "../src/contracts/index.js";
import { collectNarrativeSections } from "../src/fidelity/index.js";
import type { EmaMapping } from "../src/fhir/mapping.js";
import { loadEmaMapping } from "../src/fhir/mapping.js";
import { transformType2ToEma } from "../src/fhir/transform.js";
import { isComposition } from "../src/fhir/types.js";
import {
  SCENE_THREE_PHRASE,
  SYNTHETIC_PRODUCT_IDS,
  SYNTHETIC_VERSIONS,
  VERSIONED_SOURCE_KEY,
  type SyntheticProductId,
  type SyntheticVersion,
} from "../src/fixtures/synthetic-products.js";
import { createSyntheticSubmission } from "../src/fixtures/synthetic-submission.js";
import { createSyntheticType2Bundle } from "../src/fixtures/synthetic.js";
import { canonicalJson, sha256 } from "../src/lib/hash.js";
import { SYNTHETIC, drawn } from "./support/submission.js";

const CONTRAINDICATIONS_KEY = "smpc.4.3";

let mapping: EmaMapping;

beforeAll(async () => {
  mapping = await loadEmaMapping();
});

function narrativeDivs(
  product: SyntheticProductId,
  version: SyntheticVersion,
): Map<string, string> {
  const composition = createSyntheticType2Bundle(mapping, { product, version }).entry[0]?.resource;
  if (composition === undefined || !isComposition(composition)) {
    throw new Error("Synthetic Type 2 fixture must have Composition as its first entry");
  }
  return new Map(
    collectNarrativeSections(composition, mapping.sourceCodeSystem).map(({ sourceKey, div }) => [
      sourceKey,
      div,
    ]),
  );
}

describe("synthetic canonical submission fixture", () => {
  it("is byte-identical across calls", () => {
    const first = createSyntheticSubmission(mapping);
    const second = createSyntheticSubmission(mapping);

    expect(canonicalJson(first)).toBe(canonicalJson(second));
    expect(first.submission.bundleSha256).toBe(second.submission.bundleSha256);
    expect(first.fidelityReport.reportHash).toBe(second.fidelityReport.reportHash);
  });

  // Parameterising the fixture must not have moved the default by one byte: the committed
  // contract fixtures and every hash in the repository were computed over it.
  it("produces the same default whether or not options are passed", () => {
    const implicit = sha256(createSyntheticSubmission(mapping));

    expect(sha256(createSyntheticSubmission(mapping, {}))).toBe(implicit);
    expect(
      sha256(createSyntheticSubmission(mapping, { product: "synthetic-paracetamol", version: 1 })),
    ).toBe(implicit);
    expect(sha256(createSyntheticType2Bundle(mapping, {}))).toBe(
      sha256(createSyntheticType2Bundle(mapping)),
    );
  });

  it("satisfies the canonical submission contract and its fidelity gate", () => {
    const { submission, fidelityReport, sourceText } = createSyntheticSubmission(mapping);

    expect(() => CanonicalSubmissionSchema.parse(submission)).not.toThrow();
    expect(fidelityReport.status).toBe("passed");
    expect(fidelityReport.summary.total).toBe(32);
    expect(fidelityReport.summary.verified).toBe(32);
    expect(submission.provenance.sections).toHaveLength(32);
    expect(submission.provenance.decisions).toHaveLength(33);
    expect(sourceText.pages).toHaveLength(3);
    expect(submission.provenance.sections.some(({ spans }) => spans[0]?.page !== 1)).toBe(true);
  });
});

describe("the demonstration set", () => {
  it("passes the ingress gate for every product and version", () => {
    for (const product of SYNTHETIC_PRODUCT_IDS) {
      for (const version of SYNTHETIC_VERSIONS) {
        const fixture = createSyntheticSubmission(mapping, { product, version });
        expect([product, version, fixture.fidelityReport.status]).toEqual([
          product,
          version,
          "passed",
        ]);
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
      }
    }
  });

  it("gives every product its own identity", () => {
    const submissions = SYNTHETIC_PRODUCT_IDS.map(
      (product) => createSyntheticSubmission(mapping, { product, version: 1 }).submission,
    );
    const values = (pick: (submission: (typeof submissions)[number]) => string): string[] =>
      submissions.map(pick);

    expect(new Set(values(({ submissionId }) => submissionId)).size).toBe(submissions.length);
    expect(new Set(values(({ bundleSha256 }) => bundleSha256)).size).toBe(submissions.length);
    expect(new Set(values(({ bundle }) => String(bundle.id))).size).toBe(submissions.length);
    expect(new Set(values(({ bundle }) => bundle.identifier.value)).size).toBe(submissions.length);
    expect(new Set(values((submission) => drawn(submission).filename)).size).toBe(
      submissions.length,
    );
  });

  // Scene 2: version 2 of a label differs from version 1 by one sentence, in one section, and
  // the hashes say exactly that.
  it("changes exactly one section and one span between version 1 and version 2", () => {
    for (const product of SYNTHETIC_PRODUCT_IDS) {
      const first = createSyntheticSubmission(mapping, { product, version: 1 }).submission;
      const second = createSyntheticSubmission(mapping, { product, version: 2 }).submission;

      const before = new Map(
        first.provenance.sections.map((section) => [section.sourceKey, section]),
      );
      const changedNarratives = second.provenance.sections.filter(
        (section) =>
          before.get(section.sourceKey)?.narrativeDivSha256 !== section.narrativeDivSha256,
      );
      const changedSpans = second.provenance.sections.filter(
        (section) =>
          before.get(section.sourceKey)?.spans[0]?.textSha256 !== section.spans[0]?.textSha256,
      );

      expect(changedNarratives.map(({ sourceKey }) => sourceKey)).toEqual([VERSIONED_SOURCE_KEY]);
      expect(changedSpans.map(({ sourceKey }) => sourceKey)).toEqual([VERSIONED_SOURCE_KEY]);
      expect(second.provenance.sections).toHaveLength(first.provenance.sections.length);
    }
  });

  // Scene 2 again, from the store's point of view: the same document, versioned, not a second
  // document. transform.ts derives the EMA Bundle id from Bundle.identifier.value, which is
  // version-independent, so the id is stable across versions and distinct across products.
  it("keeps one EMA document Bundle id per product across both versions", () => {
    const perProduct = SYNTHETIC_PRODUCT_IDS.map((product) => {
      const ids = SYNTHETIC_VERSIONS.map(
        (version) =>
          transformType2ToEma(createSyntheticType2Bundle(mapping, { product, version }), mapping)
            .documentBundle.id,
      );
      expect([product, new Set(ids).size]).toEqual([product, 1]);
      return ids[0];
    });

    expect(new Set(perProduct).size).toBe(SYNTHETIC_PRODUCT_IDS.length);
  });

  // Scene 3: "across products, which list this phrase in section 4.3" is only a demonstration
  // if the answer is one product rather than all of them or none.
  it("puts the section 4.3 phrase in exactly one product", () => {
    const naming = SYNTHETIC_PRODUCT_IDS.filter((product) =>
      SYNTHETIC_VERSIONS.some((version) =>
        (narrativeDivs(product, version).get(CONTRAINDICATIONS_KEY) ?? "").includes(
          SCENE_THREE_PHRASE,
        ),
      ),
    );

    expect(naming).toEqual(["synthetic-demoxetine"]);
  });

  it("marks every section of every product as synthetic demonstration text", () => {
    for (const product of SYNTHETIC_PRODUCT_IDS) {
      for (const version of SYNTHETIC_VERSIONS) {
        for (const [sourceKey, div] of narrativeDivs(product, version)) {
          expect([product, sourceKey, /synthetic demonstration/i.test(div)]).toEqual([
            product,
            sourceKey,
            true,
          ]);
          expect([product, sourceKey, div.includes("not for clinical use")]).toEqual([
            product,
            sourceKey,
            true,
          ]);
        }
      }
    }
  });
});
