import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { serviceAccountRoles, terraformBlocks } from "../support/terraform.js";

import { beforeAll, describe, expect, it, vi } from "vitest";

import {
  FindProductOutputSchema,
  ProvenanceDetailSchema,
  QueryAuditRecordSchema,
  QuoteVerificationSchema,
  SectionContentSchema,
  type QueryAuditRecord,
  type QuoteVerification,
} from "../../src/contracts/query-tools.js";
import { NORMALIZATION_VERSION, normalizeText, xhtmlToText } from "../../src/fidelity/index.js";
import { loadEmaMapping } from "../../src/fhir/mapping.js";
import { isComposition } from "../../src/fhir/types.js";
import { APPROVAL_CONTENT_EXTENSION_URL, APPROVER_ROLE_SYSTEM } from "../../src/fhir/provenance.js";
import { sha256, sha256Utf8, stableUuid } from "../../src/lib/hash.js";
import type { EntitlementDirectory } from "../../src/query/entitlements.js";
import { FIND_PRODUCT_CONCURRENCY, FIND_PRODUCT_SCAN_HORIZON } from "../../src/query/tools.js";
import {
  INJECTED_SECTION_KEY,
  INJECTED_SENTENCE,
  LAST_UPDATED,
  PRINCIPAL_A,
  PRINCIPAL_B,
  SECTION_KEY,
  SERVICE_VERSION,
  TYPOGRAPHY_DIV,
  TYPOGRAPHY_SECTION_KEY,
  VERSION_ID,
  allNarratives,
  buildQueryStore,
  callTool,
  connectHarness,
  entitlementDirectory,
  narrativeDivOf,
  withNarratives,
  type Harness,
  type QueryStore,
  type SeededDocument,
} from "./fixtures.js";

// The acceptance tests of docs/design/epi-mcp-query-service.md, in its order and under its
// names. Each one is also the demonstration it describes: the store is built by the real
// pipeline and the tools are driven through the MCP client, so a passing run is evidence about
// the service, not about the test's own arithmetic.

let store: QueryStore;
let directory: EntitlementDirectory;

beforeAll(async () => {
  store = buildQueryStore(await loadEmaMapping());
  directory = entitlementDirectory(store);
});

function harnessFor(principal: string, documents?: Map<string, SeededDocument>): Promise<Harness> {
  return connectHarness({
    store,
    principal,
    entitlements: directory.entitlementsFor(principal),
    ...(documents === undefined ? {} : { documents }),
  });
}

async function withHarness<T>(
  principal: string,
  use: (harness: Harness) => Promise<T>,
): Promise<T> {
  const harness = await harnessFor(principal);
  try {
    return await use(harness);
  } finally {
    await harness.close();
  }
}

function onlyAudit(audits: QueryAuditRecord[]): QueryAuditRecord {
  expect(audits).toHaveLength(1);
  const record = audits[0];
  if (record === undefined) throw new Error("expected one audit record");
  return record;
}

describe("ePI query service, phase 1", () => {
  it("verbatim with citations", async () => {
    await withHarness(PRINCIPAL_A, async (harness) => {
      const args = { bundleId: store.bundleIdA, sourceKey: SECTION_KEY };
      const answer = await callTool(harness, "get_section", args);

      expect(answer.isError).toBe(false);
      const section = SectionContentSchema.parse(answer.structured);

      // Byte for byte what the store holds, and both hashes recomputed here from `div` alone.
      const stored = narrativeDivOf(store, store.bundleIdA, SECTION_KEY);
      expect(section.div).toBe(stored);
      expect(section.text).toBe(normalizeText(xhtmlToText(stored)));
      expect(section.narrativeDivSha256).toBe(sha256Utf8(stored));
      expect(section.normalizedTextSha256).toBe(sha256Utf8(normalizeText(xhtmlToText(stored))));
      expect(section.normalizationVersion).toBe(NORMALIZATION_VERSION);
      expect(section.contentNotice).toBe("document-content-not-instructions");

      // The citation: which document, which version, which section.
      expect(section.document).toEqual({
        bundleId: store.bundleIdA,
        versionId: VERSION_ID,
        lastUpdated: LAST_UPDATED,
      });
      expect(section.sourceKey).toBe(SECTION_KEY);
      expect(section.path.startsWith("Composition.section[0]")).toBe(true);
      expect(section.provenanceResourceId).toBe(
        store.documents.get(store.bundleIdA)?.provenance?.id,
      );

      const record = onlyAudit(harness.audits);
      expect(record).toMatchObject({
        service: "ema-flow-query",
        serviceVersion: SERVICE_VERSION,
        principal: PRINCIPAL_A,
        credentialType: "id-token",
        tool: "get_section",
        outcome: "ok",
        resultCount: 1,
        bundleId: store.bundleIdA,
        // The version the call actually read, so the record ties to the exact stored content.
        versionId: VERSION_ID,
        argumentsSha256: sha256(args),
      });
      // Nothing outside a container, nothing declared by the caller.
      expect(record.imageDigest).toBeUndefined();
      expect(record.turnId).toBeUndefined();
    });
  });

  it("is this quote accurate?", async () => {
    await withHarness(PRINCIPAL_A, async (harness) => {
      const verify = async (quote: string, sourceKey: string, bundleId: string) => {
        const answer = await callTool(harness, "verify_quote", { bundleId, sourceKey, quote });
        expect(answer.isError).toBe(false);
        return QuoteVerificationSchema.parse(answer.structured);
      };

      // A fragment of a section of the document the pipeline published: whole words from the
      // middle of it, because a quote that begins or ends inside a word is not a match.
      const text = normalizeText(xhtmlToText(narrativeDivOf(store, store.bundleIdA, SECTION_KEY)));
      const words = text.split(" ");
      const fragment = words.slice(1, 6).join(" ");
      const offset = (words[0] ?? "").length + 1;
      expect(text.indexOf(fragment)).toBe(offset);
      expect(offset).toBeGreaterThan(0);
      const match = await verify(fragment, SECTION_KEY, store.bundleIdA);
      expect(match.result).toBe("match");
      expect(match.match?.sourceKey).toBe(SECTION_KEY);
      expect(match.match?.startOffset).toBe(offset);
      expect(match.match?.endOffset).toBe(offset + fragment.length);
      expect(match.match?.normalizedTextSha256).toBe(sha256Utf8(text));
      expect(match.quoteSha256).toBe(sha256Utf8(fragment));
      expect(match.sectionsSearched).toBe(1);
      expect(match.normalizationVersion).toBe(NORMALIZATION_VERSION);

      // The typographic cases, against a section that carries a right single quotation mark, a
      // superscript, and the letters "fi": exactly the characters the publishing gate treats as
      // significant (ADR 0003).
      const typography = store.bundleIdTypography;
      const quoted = "sponsor\u2019s first dose is 5 mg/m\u00b2";

      expect((await verify(quoted, TYPOGRAPHY_SECTION_KEY, typography)).result).toBe("match");

      // No-match: one changed character, a straightened quotation mark, a flattened
      // superscript, and a removed word. A near miss is what a reviewer must see for themselves.
      for (const wrong of [
        "sponsor\u2019s first dase is 5 mg/m\u00b2",
        "sponsor\u0027s first dose is 5 mg/m\u00b2",
        "sponsor\u2019s first dose is 5 mg/m2",
        "sponsor\u2019s dose is 5 mg/m\u00b2",
      ]) {
        const answer = await verify(wrong, TYPOGRAPHY_SECTION_KEY, typography);
        expect([wrong, answer.result]).toEqual([wrong, "no-match"]);
        expect(answer.match).toBeUndefined();
      }

      // Match: extra whitespace, and a ligature where the section has "fi" — because that is
      // what the publishing gate accepts.
      for (const right of [
        "sponsor\u2019s   first \n dose is 5 mg/m\u00b2",
        "sponsor\u2019s \ufb01rst dose is 5 mg/m\u00b2",
      ]) {
        const answer = await verify(right, TYPOGRAPHY_SECTION_KEY, typography);
        expect([right, answer.result]).toEqual([right, "match"]);
        expect(answer.match?.sourceKey).toBe(TYPOGRAPHY_SECTION_KEY);
      }

      // Without a section, every section of the document is searched.
      const whole = await callTool(harness, "verify_quote", {
        bundleId: typography,
        quote: quoted,
      });
      const searched = QuoteVerificationSchema.parse(whole.structured);
      expect(searched.result).toBe("match");
      expect(searched.sectionsSearched).toBeGreaterThan(1);

      // `sectionsSearched` is the number of sections that carry a narrative, whether the match
      // sits in the first section or the last: the search stops at the first match, the count
      // does not.
      const narratives = allNarratives(store)
        .filter(({ sourceKey }) => sourceKey.startsWith(`${store.bundleIdA}:`))
        .map(({ div }) => normalizeText(xhtmlToText(div)));
      const first = narratives[0];
      const last = narratives.at(-1);
      if (first === undefined || last === undefined || first === last) {
        throw new Error("expected at least two narrative sections in document A");
      }
      for (const text of [first, last]) {
        const answer = await callTool(harness, "verify_quote", {
          bundleId: store.bundleIdA,
          quote: text.split(" ").slice(0, 4).join(" "),
        });
        const parsed = QuoteVerificationSchema.parse(answer.structured);
        expect(parsed.result).toBe("match");
        expect(parsed.sectionsSearched).toBe(narratives.length);
      }

      // A quote carrying a character the normalisation forbids is a bad request, not a no-match.
      const forbidden = await callTool(harness, "verify_quote", {
        bundleId: typography,
        quote: `dose${String.fromCodePoint(0)} is`,
      });
      expect(forbidden.isError).toBe(true);
      expect(forbidden.structured).toEqual({ tool: "verify_quote", error: "invalid-request" });
      expect(forbidden.text).toBe("invalid-request");
    });
  });

  it("prove where it came from", async () => {
    await withHarness(PRINCIPAL_A, async (harness) => {
      const answer = await callTool(harness, "get_provenance", {
        bundleId: store.bundleIdA,
        sourceKey: SECTION_KEY,
      });
      expect(answer.isError).toBe(false);
      const detail = ProvenanceDetailSchema.parse(answer.structured);

      // Every field is what src/fhir/provenance.ts wrote into the persisted resource.
      expect(detail.provenanceResourceId).toBe(
        store.documents.get(store.bundleIdA)?.provenance?.id,
      );
      expect(detail.recorded).toBe(store.recorded);
      expect(detail.sourceDocumentSha256).toBe(store.sourceDocumentSha256);
      expect(detail.fidelityReportSha256).toBe(store.fidelityReportSha256);
      expect(detail.approvedContentSha256).toBe(store.approvedContentSha256);
      expect(detail.extractor).toEqual(store.extractor);
      expect(detail.approver).toEqual({ id: store.approverId, role: store.approverRole });
      expect(detail.document).toEqual({
        bundleId: store.bundleIdA,
        versionId: VERSION_ID,
        lastUpdated: LAST_UPDATED,
      });

      // The section hashes are recomputed live, and they are the ones get_section reports.
      const section = SectionContentSchema.parse(
        (
          await callTool(harness, "get_section", {
            bundleId: store.bundleIdA,
            sourceKey: SECTION_KEY,
          })
        ).structured,
      );
      expect(detail.section).toEqual({
        sourceKey: SECTION_KEY,
        narrativeDivSha256: section.narrativeDivSha256,
        normalizedTextSha256: section.normalizedTextSha256,
      });
    });
  });

  // The approver's role is read from the persisted resource and never inferred from the
  // participant type: a Provenance written without the role coding (an older store) is answered
  // `unavailable`, and the audit record says so.
  it("answers unavailable when the persisted Provenance omits the approver role", async () => {
    const seeded = store.documents.get(store.bundleIdA);
    if (seeded === undefined) throw new Error("expected a seeded document");
    // The fixture really lacks the coding, and the projection as persisted really carries it.
    expect(JSON.stringify(store.provenanceWithoutRole).includes(APPROVER_ROLE_SYSTEM)).toBe(false);
    expect(JSON.stringify(seeded.provenance).includes(APPROVER_ROLE_SYSTEM)).toBe(true);

    const documents = new Map([
      [store.bundleIdA, { bundle: seeded.bundle, provenance: store.provenanceWithoutRole }],
    ]);
    const harness = await harnessFor(PRINCIPAL_A, documents);
    try {
      const answer = await callTool(harness, "get_provenance", { bundleId: store.bundleIdA });
      expect(answer.isError).toBe(true);
      expect(answer.structured).toEqual({ tool: "get_provenance", error: "unavailable" });
      const record = onlyAudit(harness.audits);
      expect(record.outcome).toBe("unavailable");
      expect(record.versionId).toBe(VERSION_ID);
    } finally {
      await harness.close();
    }
  });

  // The publishing gate lets a span omit words but never begin or end inside one
  // (docs/fidelity-normalization.md section 6), and verify_quote claims the gate's rules. A
  // quote cut inside a word is therefore no-match, even though its characters are a slice of
  // the section: a truncated number or unit is exactly the near miss a reviewer must see.
  it("a quote matches as whole words only", async () => {
    const verifyIn = async (
      harness: Harness,
      quote: string,
      sourceKey: string | undefined,
    ): Promise<QuoteVerification> => {
      const answer = await callTool(harness, "verify_quote", {
        bundleId: store.bundleIdTypography,
        ...(sourceKey === undefined ? {} : { sourceKey }),
        quote,
      });
      expect(answer.isError).toBe(false);
      return QuoteVerificationSchema.parse(answer.structured);
    };

    // The label says "The sponsor\u2019s first dose is 5 mg/m\u00b2 daily."
    const label = await harnessFor(PRINCIPAL_A);
    try {
      const text = normalizeText(xhtmlToText(TYPOGRAPHY_DIV));

      // Each of these is a slice of it that begins or ends inside a word — a unit cut before
      // its superscript, a lone letter, a fragment straddling two words, and a unit cut at the
      // slash — and none is confirmed.
      for (const cut of ["first dose is 5 mg/m", "e", "s first d", "5 mg/"]) {
        expect(text.includes(cut)).toBe(true);
        const answer = await verifyIn(label, cut, TYPOGRAPHY_SECTION_KEY);
        expect([cut, answer.result]).toEqual([cut, "no-match"]);
        expect(answer.match).toBeUndefined();
      }

      // Whole words match: in the middle of the section, at its very start, at its very end,
      // and ending just before punctuation — whether it closes a word or a sentence.
      for (const whole of [
        "first dose is 5 mg/m\u00b2",
        "The sponsor",
        "daily.",
        "5 mg/m\u00b2 daily",
        "sponsor",
      ]) {
        const answer = await verifyIn(label, whole, TYPOGRAPHY_SECTION_KEY);
        expect([whole, answer.result]).toEqual([whole, "match"]);
        expect([whole, answer.match?.startOffset]).toEqual([whole, text.indexOf(whole)]);
      }
      const start = await verifyIn(label, "The sponsor", TYPOGRAPHY_SECTION_KEY);
      expect(start.match?.startOffset).toBe(0);
      const end = await verifyIn(label, "daily.", TYPOGRAPHY_SECTION_KEY);
      expect(end.match?.endOffset).toBe(Array.from(text).length);

      // One audit record per call, as always.
      expect(label.audits).toHaveLength(11);
    } finally {
      await label.close();
    }

    // Wording the synthetic submission does not carry: a dose limit in one section, and in a
    // later section a quote whose first occurrence is inside a longer number and whose second
    // is whole. The mathematical bold capital A is a letter outside the Basic Multilingual
    // Plane, so a word boundary has to be judged on a code point, not on half of a surrogate
    // pair.
    const seeded = store.documents.get(store.bundleIdTypography);
    if (seeded === undefined) throw new Error("expected a seeded document");
    const limits = "Adults: max 100 mg daily. Children: max 10 mg daily. Code \u{1D400}5 mg.";
    const bundle = withNarratives(seeded.bundle, {
      [TYPOGRAPHY_SECTION_KEY]:
        '<div xmlns="http://www.w3.org/1999/xhtml"><p>Do not exceed max 100 mg daily.</p></div>',
      [SECTION_KEY]: `<div xmlns="http://www.w3.org/1999/xhtml"><p>${limits}</p></div>`,
    });
    const composition = bundle.entry[0]?.resource;
    if (composition === undefined || !isComposition(composition)) {
      throw new Error("expected a Composition");
    }
    // The section holding only the cut occurrence comes first in the document, so a search
    // across sections meets it before the whole one.
    const order = JSON.stringify(composition.section);
    expect(order.indexOf(stableUuid("ema-qrd-section", TYPOGRAPHY_SECTION_KEY))).toBeLessThan(
      order.indexOf(stableUuid("ema-qrd-section", SECTION_KEY)),
    );

    const harness = await harnessFor(
      PRINCIPAL_A,
      new Map([[store.bundleIdTypography, { ...seeded, bundle }]]),
    );
    try {
      // A truncated number: "max 10" is a slice of "max 100 mg" and is not what it says.
      const truncated = await verifyIn(harness, "max 10", TYPOGRAPHY_SECTION_KEY);
      expect(truncated.result).toBe("no-match");
      expect(truncated.match).toBeUndefined();

      // Within one section: the first occurrence is inside "max 100", the later one is whole,
      // and the later one is the match, at its own code-point offset.
      const whole = limits.lastIndexOf("max 10");
      expect(limits.indexOf("max 10")).toBeLessThan(whole);
      const later = await verifyIn(harness, "max 10", SECTION_KEY);
      expect(later.result).toBe("match");
      expect(later.match?.startOffset).toBe(whole);
      expect(later.match?.endOffset).toBe(whole + "max 10".length);

      // Across sections: with no section named, the cut occurrence in the earlier section does
      // not end the search, and the whole one in the later section answers.
      const anywhere = await verifyIn(harness, "max 10", undefined);
      expect(anywhere.result).toBe("match");
      expect(anywhere.match?.sourceKey).toBe(SECTION_KEY);
      expect(anywhere.match?.startOffset).toBe(whole);

      // A letter outside the Basic Multilingual Plane is still a letter: "5 mg" after it begins
      // inside a word.
      expect((await verifyIn(harness, "5 mg", SECTION_KEY)).result).toBe("no-match");

      expect(harness.audits).toHaveLength(4);
    } finally {
      await harness.close();
    }
  });

  // Nothing yet binds a stored version to its own approval: the Provenance names the Bundle
  // without a version, so the newest approval is the only one the store can find. A caller who
  // names an earlier version must not be handed that approval as if it were the version's own.
  it("an earlier version is never given a later version's approval", async () => {
    const seeded = store.documents.get(store.bundleIdA);
    if (seeded?.provenance === undefined) throw new Error("expected a seeded, approved document");
    const version1 = seeded.bundle;
    expect(version1.meta?.versionId).toBe(VERSION_ID);
    const version2 = {
      ...structuredClone(version1),
      meta: { ...version1.meta, versionId: "2", lastUpdated: "2026-09-21T09:00:00.000Z" },
    };
    // Version 2's approval: its own id and its own approved-content hash.
    const approval2 = {
      ...structuredClone(seeded.provenance),
      id: stableUuid("ingestion-provenance", "version-2"),
      recorded: "2026-09-21T08:59:00.000Z",
      extension: [
        {
          url: APPROVAL_CONTENT_EXTENSION_URL,
          valueString: sha256Utf8("version 2 approved content"),
        },
      ],
    };
    const documents = new Map<string, SeededDocument>([
      [store.bundleIdA, { bundle: version2, provenance: approval2, history: [version1] }],
    ]);

    // Version 1, named: get_provenance cannot say which approval is version 1's, so it answers
    // `unavailable` — with or without a section — and does not search for one.
    const earlier = await harnessFor(PRINCIPAL_A, documents);
    try {
      for (const args of [
        { bundleId: store.bundleIdA, versionId: VERSION_ID },
        { bundleId: store.bundleIdA, versionId: VERSION_ID, sourceKey: SECTION_KEY },
      ]) {
        const answer = await callTool(earlier, "get_provenance", args);
        expect(answer.isError).toBe(true);
        expect(answer.structured).toEqual({ tool: "get_provenance", error: "unavailable" });
      }
      // get_section still answers version 1 verbatim, but without a Provenance reference.
      const section = await callTool(earlier, "get_section", {
        bundleId: store.bundleIdA,
        versionId: VERSION_ID,
        sourceKey: SECTION_KEY,
      });
      expect(section.isError).toBe(false);
      const content = SectionContentSchema.parse(section.structured);
      expect(content.document.versionId).toBe(VERSION_ID);
      expect("provenanceResourceId" in section.structured).toBe(false);
      expect(JSON.stringify(section.structured).includes(approval2.id)).toBe(false);

      // No approval was even looked for; the current version was read to find out.
      expect(earlier.log.provenance).toEqual([]);
      expect(earlier.log.bundles).toEqual(
        Array.from({ length: 3 }, () => [
          `${store.bundleIdA}/_history/${VERSION_ID}`,
          store.bundleIdA,
        ]).flat(),
      );
      // One record per call, each naming the version actually read.
      expect(earlier.audits.map(({ outcome, versionId }) => [outcome, versionId])).toEqual([
        ["unavailable", VERSION_ID],
        ["unavailable", VERSION_ID],
        ["ok", VERSION_ID],
      ]);
    } finally {
      await earlier.close();
    }

    // The current version — named, or not named — is answered as before, with its approval.
    const current = await harnessFor(PRINCIPAL_A, documents);
    try {
      for (const selector of [{ versionId: "2" }, {}]) {
        const provenance = await callTool(current, "get_provenance", {
          bundleId: store.bundleIdA,
          ...selector,
        });
        expect(provenance.isError).toBe(false);
        const detail = ProvenanceDetailSchema.parse(provenance.structured);
        expect(detail.document.versionId).toBe("2");
        expect(detail.provenanceResourceId).toBe(approval2.id);
        expect(detail.approvedContentSha256).toBe(sha256Utf8("version 2 approved content"));

        const section = await callTool(current, "get_section", {
          bundleId: store.bundleIdA,
          sourceKey: SECTION_KEY,
          ...selector,
        });
        const content = SectionContentSchema.parse(section.structured);
        expect(content.document.versionId).toBe("2");
        expect(content.provenanceResourceId).toBe(approval2.id);
      }
      expect(current.audits.map(({ outcome }) => outcome)).toEqual(["ok", "ok", "ok", "ok"]);
    } finally {
      await current.close();
    }

    // Naming a version costs one more read, so it is budgeted like the others: two reads are
    // not enough for a get_section of a named version, and the call is `unavailable` rather
    // than an answer missing its Provenance reference for a reason the caller cannot see.
    const budgeted = await connectHarness({
      store,
      principal: PRINCIPAL_A,
      entitlements: directory.entitlementsFor(PRINCIPAL_A),
      documents,
      readBudget: 2,
    });
    try {
      const answer = await callTool(budgeted, "get_section", {
        bundleId: store.bundleIdA,
        versionId: "2",
        sourceKey: SECTION_KEY,
      });
      expect(answer.structured).toEqual({ tool: "get_section", error: "unavailable" });
      expect(budgeted.log.provenance).toEqual([]);
      expect(onlyAudit(budgeted.audits).outcome).toBe("unavailable");
    } finally {
      await budgeted.close();
    }
  });

  it("the injection test", async () => {
    await withHarness(PRINCIPAL_B, async (harness) => {
      const answer = await callTool(harness, "get_section", {
        bundleId: store.bundleIdB,
        sourceKey: INJECTED_SECTION_KEY,
      });
      expect(answer.isError).toBe(false);
      const section = SectionContentSchema.parse(answer.structured);

      // Byte-identical, instruction-shaped sentence and all, with its hash attached.
      const stored = narrativeDivOf(store, store.bundleIdB, INJECTED_SECTION_KEY);
      expect(section.div).toBe(stored);
      expect(section.div.includes(INJECTED_SENTENCE)).toBe(true);
      expect(section.text.includes(INJECTED_SENTENCE)).toBe(true);
      expect(section.narrativeDivSha256).toBe(sha256Utf8(stored));
      expect(section.normalizedTextSha256).toBe(sha256Utf8(normalizeText(xhtmlToText(stored))));
      expect(section.contentNotice).toBe("document-content-not-instructions");

      // And nothing else in the response changed: the same call against the same section of the
      // uninjected document agrees on every field that is not the narrative or its hashes.
      const clean = await withHarness(PRINCIPAL_A, async (other) =>
        SectionContentSchema.parse(
          (
            await callTool(other, "get_section", {
              bundleId: store.bundleIdA,
              sourceKey: INJECTED_SECTION_KEY,
            })
          ).structured,
        ),
      );
      expect(Object.keys(section).sort()).toEqual(Object.keys(clean).sort());
      expect({
        sourceKey: section.sourceKey,
        path: section.path,
        title: section.title,
        normalizationVersion: section.normalizationVersion,
        contentNotice: section.contentNotice,
        versionId: section.document.versionId,
      }).toEqual({
        sourceKey: clean.sourceKey,
        path: clean.path,
        title: clean.title,
        normalizationVersion: clean.normalizationVersion,
        contentNotice: clean.contentNotice,
        versionId: clean.document.versionId,
      });

      const record = onlyAudit(harness.audits);
      expect(record.outcome).toBe("ok");
      expect(JSON.stringify(record).includes("Ignore all previous")).toBe(false);
    });
  });

  it("the tenant wall", async () => {
    await withHarness(PRINCIPAL_A, async (harness) => {
      const foreign = store.bundleIdB;
      const quote = "Synthetic demonstration content";
      const shapes: { tool: string; args: Record<string, unknown> }[] = [
        { tool: "get_section", args: { bundleId: foreign, sourceKey: SECTION_KEY } },
        {
          tool: "get_section",
          args: { bundleId: foreign, versionId: VERSION_ID, sourceKey: SECTION_KEY },
        },
        { tool: "get_provenance", args: { bundleId: foreign } },
        { tool: "get_provenance", args: { bundleId: foreign, sourceKey: SECTION_KEY } },
        { tool: "get_provenance", args: { bundleId: foreign, versionId: VERSION_ID } },
        { tool: "verify_quote", args: { bundleId: foreign, quote } },
        { tool: "verify_quote", args: { bundleId: foreign, sourceKey: SECTION_KEY, quote } },
        { tool: "verify_quote", args: { bundleId: foreign, versionId: VERSION_ID, quote } },
        // A quote the normalisation refuses, against a document outside the entitlement: the
        // entitlement decision comes first, so this is the wall and not a bad request.
        {
          tool: "verify_quote",
          args: { bundleId: foreign, quote: `dose${String.fromCodePoint(0)} is` },
        },
      ];

      for (const shape of shapes) {
        const answer = await callTool(harness, shape.tool, shape.args);
        // Outside the caller's entitlement every document is `document-not-found`: existence is
        // not disclosed, and `not-entitled` never reaches the caller.
        expect([shape.tool, answer.structured]).toEqual([
          shape.tool,
          { tool: shape.tool, error: "document-not-found" },
        ]);
        expect(answer.text).toBe("document-not-found");
      }

      // ... while the audit trail records what was really attempted. No version is recorded:
      // none was read.
      expect(harness.audits.map((record) => record.outcome)).toEqual(
        shapes.map(() => "not-entitled"),
      );
      expect(harness.audits.every((record) => record.bundleId === foreign)).toBe(true);
      expect(harness.audits.every((record) => record.versionId === undefined)).toBe(true);

      // The other tenant's document was never fetched: the entitlement is applied before the
      // read, not after it.
      expect(harness.log.bundles.some((read) => read.includes(foreign))).toBe(false);
      expect(harness.log.provenance).toEqual([]);

      // And an exact query does not surface the other tenant's product either.
      const found = await callTool(harness, "find_product", { query: store.productNameB });
      expect(found.isError).toBe(false);
      expect(found.structured).toEqual({ products: [], truncated: false });
      expect(harness.log.bundles.some((read) => read.includes(foreign))).toBe(false);

      // The caller's own products are found, so the empty answer above is a wall, not a bug.
      const own = await callTool(harness, "find_product", { query: store.productNameA });
      const products = (own.structured as { products: { productName: string }[] }).products;
      expect(products.map(({ productName }) => productName)).toEqual([store.productNameA]);
    });
  });

  it("find_product reads at most the scan horizon, and stops at the limit", async () => {
    const seeded = store.documents.get(store.bundleIdA);
    if (seeded === undefined) throw new Error("expected a seeded document");

    // An entitlement longer than one call searches: the same published document under many
    // ids. The reader is the only store, so its call log is the read count.
    const beyondHorizon = FIND_PRODUCT_SCAN_HORIZON + 50;
    const ids = Array.from({ length: beyondHorizon }, (_, position) =>
      stableUuid("ema-bundle", `horizon-${String(position)}`),
    );
    const documents = new Map<string, SeededDocument>(ids.map((id) => [id, seeded]));
    const wide = await connectHarness({
      store,
      principal: PRINCIPAL_A,
      entitlements: { bundles: ids },
      documents,
    });
    try {
      // No product matches: every scanned document is read, and only those.
      const none = await callTool(wide, "find_product", { query: "no-such-product-anywhere" });
      expect(none.isError).toBe(false);
      expect(FindProductOutputSchema.parse(none.structured)).toEqual({
        products: [],
        truncated: true,
      });
      expect(wide.log.bundles).toHaveLength(FIND_PRODUCT_SCAN_HORIZON);
      expect(wide.log.bundles).toEqual(ids.slice(0, FIND_PRODUCT_SCAN_HORIZON));
      expect(onlyAudit(wide.audits)).toMatchObject({
        tool: "find_product",
        outcome: "ok",
        resultCount: 0,
        truncated: true,
      });

      // Every document matches and one is wanted: reads stop once the limit is reached — no
      // more than one pool's worth are ever in flight — and the answer is the first entitled id.
      wide.log.bundles.length = 0;
      const one = await callTool(wide, "find_product", { query: store.productNameA, limit: 1 });
      const output = FindProductOutputSchema.parse(one.structured);
      expect(output.products.map(({ document }) => document.bundleId)).toEqual([ids[0]]);
      expect(output.truncated).toBe(true);
      expect(wide.log.bundles.length).toBeGreaterThanOrEqual(1);
      expect(wide.log.bundles.length).toBeLessThanOrEqual(FIND_PRODUCT_CONCURRENCY);
    } finally {
      await wide.close();
    }

    // Within the horizon nothing is truncated, and the record says so.
    await withHarness(PRINCIPAL_A, async (harness) => {
      const answer = await callTool(harness, "find_product", { query: "no-such-product-anywhere" });
      expect(FindProductOutputSchema.parse(answer.structured)).toEqual({
        products: [],
        truncated: false,
      });
      expect(harness.log.bundles).toHaveLength(2);
      expect(onlyAudit(harness.audits).truncated).toBe(false);
    });
  });

  it("find_product reports truncated whenever the limit stopped the scan short", async () => {
    const seeded = store.documents.get(store.bundleIdA);
    if (seeded === undefined) throw new Error("expected a seeded document");

    // Every entitled document matches the query, and there are more of them than `limit`.
    const ids = Array.from({ length: 60 }, (_, position) =>
      stableUuid("ema-bundle", `limit-${String(position)}`),
    );
    const documents = new Map<string, SeededDocument>(ids.map((id) => [id, seeded]));
    const harness = await connectHarness({
      store,
      principal: PRINCIPAL_A,
      entitlements: { bundles: ids },
      documents,
    });
    try {
      const capped = FindProductOutputSchema.parse(
        (await callTool(harness, "find_product", { query: store.productNameA, limit: 50 }))
          .structured,
      );
      // 50 of 60 entitled documents are reported, so 10 were never searched — the scan stopped
      // at the limit, well inside the horizon of 200.
      expect(capped.products).toHaveLength(50);
      expect(harness.log.bundles.length).toBeLessThan(ids.length);
      expect(capped.truncated).toBe(true);
      expect(onlyAudit(harness.audits).truncated).toBe(true);

      // The same entitlement with a limit it cannot reach: every document is searched and
      // nothing is truncated.
      harness.audits.length = 0;
      harness.log.bundles.length = 0;
      const whole = FindProductOutputSchema.parse(
        (await callTool(harness, "find_product", { query: "no-such-product-anywhere" })).structured,
      );
      expect(whole.truncated).toBe(false);
      expect(harness.log.bundles.length).toBe(ids.length);
      expect(onlyAudit(harness.audits).truncated).toBe(false);
    } finally {
      await harness.close();
    }
  });

  it("find_product reports truncated when the limit threw away matches it had already read", async () => {
    const seeded = store.documents.get(store.bundleIdA);
    if (seeded === undefined) throw new Error("expected a seeded document");

    // An entitlement small enough that one pool reaches the end of it: every document is read,
    // every one matches, and all but `limit` of those matches are dropped from the answer. The
    // scan left nothing unsearched, so only the dropped matches can make `truncated` true.
    const ids = Array.from({ length: FIND_PRODUCT_CONCURRENCY }, (_, position) =>
      stableUuid("ema-bundle", `dropped-${String(position)}`),
    );
    const documents = new Map<string, SeededDocument>(ids.map((id) => [id, seeded]));
    const harness = await connectHarness({
      store,
      principal: PRINCIPAL_A,
      entitlements: { bundles: ids },
      documents,
    });
    try {
      const one = FindProductOutputSchema.parse(
        (await callTool(harness, "find_product", { query: store.productNameA, limit: 1 }))
          .structured,
      );

      expect(harness.log.bundles).toHaveLength(ids.length);
      expect(one.products).toHaveLength(1);
      expect(one.truncated).toBe(true);
      expect(onlyAudit(harness.audits)).toMatchObject({
        tool: "find_product",
        outcome: "ok",
        resultCount: 1,
        truncated: true,
      });
    } finally {
      await harness.close();
    }
  });

  it("bounds the store reads one request may make, and says so", async () => {
    const seeded = store.documents.get(store.bundleIdA);
    if (seeded === undefined) throw new Error("expected a seeded document");
    const ids = Array.from({ length: 20 }, (_, position) =>
      stableUuid("ema-bundle", `budget-${String(position)}`),
    );
    const documents = new Map<string, SeededDocument>(ids.map((id) => [id, seeded]));

    const budgeted = (readBudget: number) =>
      connectHarness({
        store,
        principal: PRINCIPAL_A,
        entitlements: { bundles: ids },
        documents,
        readBudget,
      });

    // find_product stops scanning when the budget is spent, and reports the documents it did
    // not search as `truncated`.
    const scanning = await budgeted(5);
    try {
      const output = FindProductOutputSchema.parse(
        (await callTool(scanning, "find_product", { query: store.productNameA })).structured,
      );
      expect(scanning.log.bundles).toHaveLength(5);
      expect(output.products).toHaveLength(5);
      expect(output.truncated).toBe(true);
      expect(onlyAudit(scanning.audits)).toMatchObject({ outcome: "ok", truncated: true });
    } finally {
      await scanning.close();
    }

    // A request with no budget left answers `unavailable` rather than reading.
    const spent = await budgeted(0);
    try {
      const answer = await callTool(spent, "get_section", {
        bundleId: ids[0],
        sourceKey: SECTION_KEY,
      });
      expect(answer.structured).toEqual({ tool: "get_section", error: "unavailable" });
      expect(spent.log.bundles).toEqual([]);
      expect(spent.log.provenance).toEqual([]);
      expect(onlyAudit(spent.audits).outcome).toBe("unavailable");
    } finally {
      await spent.close();
    }

    // The provenance lookup is a read too: one read is not enough for a get_section.
    const partial = await budgeted(1);
    try {
      const answer = await callTool(partial, "get_section", {
        bundleId: ids[0],
        sourceKey: SECTION_KEY,
      });
      expect(answer.structured).toEqual({ tool: "get_section", error: "unavailable" });
      expect(partial.log.bundles).toHaveLength(1);
      expect(partial.log.provenance).toEqual([]);
    } finally {
      await partial.close();
    }
  });

  it("the written audit line is the published record", async () => {
    // What an assessor is shown is the retained Cloud Logging line, not the in-process object.
    // The line is parsed back here, minus the four fields the logger adds, and has to satisfy
    // QueryAuditRecordSchema — including `credentialType`, the field that distinguishes a
    // forwarded end-user access token from a direct ID-token caller.
    const capture = captureLines();
    const harness = await connectHarness({
      store,
      principal: PRINCIPAL_A,
      entitlements: directory.entitlementsFor(PRINCIPAL_A),
      identity: {
        principal: PRINCIPAL_A,
        credentialType: "access-token",
        imageDigest: `sha256:${"ab".repeat(32)}`,
        turnId: "0f6d1a2e-3b4c-4d5e-8f60-718293a4b5c6",
      },
      logAudit: true,
    });
    try {
      await callTool(harness, "get_section", {
        bundleId: store.bundleIdA,
        sourceKey: SECTION_KEY,
      });
      await callTool(harness, "find_product", { query: store.productNameA });
      await callTool(harness, "get_section", { bundleId: store.bundleIdB, sourceKey: SECTION_KEY });
    } finally {
      await harness.close();
      capture.restore();
    }

    const written = capture.lines
      .map((line) => JSON.parse(line) as Record<string, unknown>)
      .filter((line) => line.stage === "query-tool");
    expect(written).toHaveLength(harness.audits.length);
    expect(written.length).toBeGreaterThan(2);

    for (const line of written) {
      expect([line.severity, line.message, line.stage]).toEqual([
        "INFO",
        "Query tool call",
        "query-tool",
      ]);
      expect(typeof line.timestamp).toBe("string");
      // Not `safeParse`: the failure has to name the field that did not survive the logger.
      const parsed = QueryAuditRecordSchema.parse(withoutLoggerFields(line));
      expect(parsed.credentialType).toBe("access-token");
    }

    // And the line really is the record the service built, field for field.
    expect(written.map(withoutLoggerFields)).toEqual(
      harness.audits.map((record) => ({ ...record })),
    );
  });

  it("no narrative anywhere but the answer", async () => {
    // Every narrative of every seeded document, as 24-code-point windows, plus the injected
    // sentence: the same technique test/no-narrative-leak.test.ts holds the pipeline to.
    const windows = narrativeWindows([
      ...allNarratives(store).map(({ sourceKey, div }) => ({
        sourceKey,
        text: normalizeText(xhtmlToText(div)),
      })),
      { sourceKey: "injected-sentence", text: INJECTED_SENTENCE },
    ]);
    expect(windows.length).toBeGreaterThan(50);

    const errors: string[] = [];
    const capture = captureLines();
    try {
      for (const principal of [PRINCIPAL_A, PRINCIPAL_B]) {
        const harness = await connectHarness({
          store,
          principal,
          entitlements: directory.entitlementsFor(principal),
          // The audit records go through the service's own logger here, so the lines under scan
          // are the ones the service really writes.
          logAudit: true,
        });
        try {
          const calls: { tool: string; args: Record<string, unknown> }[] = [
            { tool: "find_product", args: { query: "synthetic" } },
            { tool: "get_section", args: { bundleId: store.bundleIdA, sourceKey: SECTION_KEY } },
            {
              tool: "get_section",
              args: { bundleId: store.bundleIdB, sourceKey: INJECTED_SECTION_KEY },
            },
            { tool: "get_section", args: { bundleId: store.bundleIdA, sourceKey: "smpc.99" } },
            { tool: "get_provenance", args: { bundleId: store.bundleIdB } },
            {
              tool: "verify_quote",
              args: { bundleId: store.bundleIdB, quote: INJECTED_SENTENCE },
            },
            {
              tool: "verify_quote",
              args: { bundleId: store.bundleIdTypography, quote: "no such wording here" },
            },
            {
              tool: "verify_quote",
              args: { bundleId: store.bundleIdA, quote: String.fromCodePoint(0) },
            },
          ];
          for (const call of calls) {
            const answer = await callTool(harness, call.tool, call.args);
            // Only failures are scanned: a successful get_section answer is the narrative, and
            // that is the one place it is allowed to be.
            if (answer.isError) errors.push(JSON.stringify(answer.structured), answer.text);
          }
        } finally {
          await harness.close();
        }
      }
    } finally {
      capture.restore();
    }

    expect(capture.lines.length).toBeGreaterThan(4);
    expect(errors.length).toBeGreaterThan(2);
    const scanned: [string, string][] = [
      ["logs", capture.lines.join("\n")],
      ["errors", errors.join("\n")],
    ];
    for (const [name, haystack] of scanned) {
      expect([name, firstLeak(haystack, windows)]).toEqual([name, undefined]);
    }

    // Self-check: the same scan finds a single injected window, so the clean results above are
    // evidence of containment and not of a scan that can never match.
    const probe = windows[0];
    if (probe === undefined) throw new Error("expected at least one window");
    expect(firstLeak(`{"message":"${probe.text}"}`, windows)).toBe(
      `${probe.sourceKey}@${String(probe.offset)}`,
    );
  });

  it("least privilege, proven", () => {
    // Every Terraform file is read, not just the one named after the service: a role bound to
    // the query service account from any file counts, and a missing infra directory fails.
    const infra = path.resolve("infra");
    const files = readdirSync(infra)
      .filter((name) => name.endsWith(".tf"))
      .sort()
      .map((name) => readFileSync(path.join(infra, name), "utf8"));
    expect(files.length).toBeGreaterThan(0);

    const terraform = files.join("\n");
    const blocks = terraformBlocks(terraform);

    // Both identities are named by their Terraform resource name, not matched by a word in the
    // block. A name-matching test can be evaded by choosing a different name, which is exactly
    // what a boundary test must not permit.
    const declared = blocks
      .filter(({ type }) => type === "google_service_account")
      .map(({ name }) => name);
    expect(declared).toContain("query");
    expect(declared).toContain("caller");

    const roles = serviceAccountRoles(terraform, "query");
    expect(new Set(roles.map(({ role }) => role))).toEqual(
      new Set(["roles/healthcare.fhirResourceReader", "roles/logging.logWriter"]),
    );
    expect(roles).toHaveLength(2);

    const reader = roles.find(({ role }) => role === "roles/healthcare.fhirResourceReader");
    const writer = roles.find(({ role }) => role === "roles/logging.logWriter");
    // The FHIR reader role is bound on the dataset, never on the project.
    expect(reader?.type).toBe("google_healthcare_dataset_iam_member");
    expect(writer?.type).toBe("google_project_iam_member");

    // The impersonation-only caller identity may invoke the query service and do nothing else.
    // It reads no store, writes no log, and holds no project or dataset role; the token-creator
    // binding is a role others hold over it, so it is not counted here.
    const callerRoles = serviceAccountRoles(terraform, "caller");
    expect(callerRoles).toEqual([
      { type: "google_cloud_run_v2_service_iam_member", role: "roles/run.invoker" },
    ]);
  });
});

// --- helpers ---------------------------------------------------------------------------------

// A written log line minus the four fields the logger itself adds, which is what has to be the
// published audit record.
const LOGGER_OWN_FIELDS = new Set(["severity", "message", "timestamp", "stage"]);

function withoutLoggerFields(line: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(line).filter(([key]) => !LOGGER_OWN_FIELDS.has(key)));
}

const WINDOW_SIZE = 24;
const WINDOW_STEP = 12;

type NarrativeWindow = { sourceKey: string; offset: number; text: string };

function narrativeWindows(sections: { sourceKey: string; text: string }[]): NarrativeWindow[] {
  return sections.flatMap(({ sourceKey, text }) => {
    if (text.length === 0) return [];
    if (text.length <= WINDOW_SIZE) return [{ sourceKey, offset: 0, text }];
    const found: NarrativeWindow[] = [];
    for (let offset = 0; offset + WINDOW_SIZE <= text.length; offset += WINDOW_STEP) {
      found.push({ sourceKey, offset, text: text.slice(offset, offset + WINDOW_SIZE) });
    }
    return found;
  });
}

// Reports where a leak was found, never the narrative that leaked.
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
