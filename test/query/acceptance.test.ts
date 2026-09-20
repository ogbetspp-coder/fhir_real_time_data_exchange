import { readFileSync, existsSync } from "node:fs";
import path from "node:path";

import { beforeAll, describe, expect, it, vi } from "vitest";

import {
  ProvenanceDetailSchema,
  QuoteVerificationSchema,
  SectionContentSchema,
  type QueryAuditRecord,
} from "../../src/contracts/query-tools.js";
import { NORMALIZATION_VERSION, normalizeText, xhtmlToText } from "../../src/fidelity/index.js";
import { loadEmaMapping } from "../../src/fhir/mapping.js";
import { sha256, sha256Utf8 } from "../../src/lib/hash.js";
import type { EntitlementDirectory } from "../../src/query/entitlements.js";
import {
  INJECTED_SECTION_KEY,
  INJECTED_SENTENCE,
  LAST_UPDATED,
  PRINCIPAL_A,
  PRINCIPAL_B,
  SECTION_KEY,
  SERVICE_VERSION,
  TYPOGRAPHY_SECTION_KEY,
  VERSION_ID,
  allNarratives,
  buildQueryStore,
  callTool,
  connectHarness,
  entitlementDirectory,
  narrativeDivOf,
  withApproverRole,
  type Harness,
  type QueryStore,
  type SeededDocument,
} from "./fixtures.js";

// The seven acceptance tests of docs/design/epi-mcp-query-service.md, in its order and under its
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
        tool: "get_section",
        outcome: "ok",
        resultCount: 1,
        bundleId: store.bundleIdA,
        argumentsSha256: sha256(args),
      });
    });
  });

  it("is this quote accurate?", async () => {
    await withHarness(PRINCIPAL_A, async (harness) => {
      const verify = async (quote: string, sourceKey: string, bundleId: string) => {
        const answer = await callTool(harness, "verify_quote", { bundleId, sourceKey, quote });
        expect(answer.isError).toBe(false);
        return QuoteVerificationSchema.parse(answer.structured);
      };

      // A fragment of a section of the document the pipeline published.
      const text = normalizeText(xhtmlToText(narrativeDivOf(store, store.bundleIdA, SECTION_KEY)));
      const fragment = text.slice(10, 40).trim();
      const offset = text.indexOf(fragment);
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

  // The role is the one ProvenanceDetail field the persisted resource does not carry today:
  // src/fhir/provenance.ts records the approver's identity but not the approver's role, and this
  // service will not guess it. Adding the coding to that projection is what closes this.
  it("answers unavailable when the persisted Provenance omits the approver role", async () => {
    const seeded = store.documents.get(store.bundleIdA);
    if (seeded === undefined) throw new Error("expected a seeded document");
    const documents = new Map([
      [store.bundleIdA, { bundle: seeded.bundle, provenance: store.provenanceWithoutRole }],
    ]);
    const harness = await harnessFor(PRINCIPAL_A, documents);
    try {
      const answer = await callTool(harness, "get_provenance", { bundleId: store.bundleIdA });
      expect(answer.isError).toBe(true);
      expect(answer.structured).toEqual({ tool: "get_provenance", error: "unavailable" });
      expect(onlyAudit(harness.audits).outcome).toBe("unavailable");

      // The role the projection would have to carry is the one the approval record holds.
      const withRole = withApproverRole(store.provenanceWithoutRole, store.approverRole);
      expect(JSON.stringify(withRole).includes(store.approverRole)).toBe(true);
    } finally {
      await harness.close();
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

      // ... while the audit trail records what was really attempted.
      expect(harness.audits.map((record) => record.outcome)).toEqual(
        shapes.map(() => "not-entitled"),
      );
      expect(harness.audits.every((record) => record.bundleId === foreign)).toBe(true);

      // The other tenant's document was never fetched: the entitlement is applied before the
      // read, not after it.
      expect(harness.log.bundles.some((read) => read.includes(foreign))).toBe(false);
      expect(harness.log.provenance).toEqual([]);

      // And an exact query does not surface the other tenant's product either.
      const found = await callTool(harness, "find_product", { query: store.productNameB });
      expect(found.isError).toBe(false);
      expect(found.structured).toEqual({ products: [] });
      expect(harness.log.bundles.some((read) => read.includes(foreign))).toBe(false);

      // The caller's own products are found, so the empty answer above is a wall, not a bug.
      const own = await callTool(harness, "find_product", { query: store.productNameA });
      const products = (own.structured as { products: { productName: string }[] }).products;
      expect(products.map(({ productName }) => productName)).toEqual([store.productNameA]);
    });
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

  it("least privilege, proven", (context) => {
    const tfPath = path.resolve("infra/query.tf");
    if (!existsSync(tfPath)) {
      // This test depends on infra/query.tf, which the service's Terraform builder owns.
      context.skip();
      return;
    }

    const roles = queryServiceAccountRoles(readFileSync(tfPath, "utf8"));
    expect(new Set(roles.map(({ role }) => role))).toEqual(
      new Set(["roles/healthcare.fhirResourceReader", "roles/logging.logWriter"]),
    );
    expect(roles).toHaveLength(2);

    const reader = roles.find(({ role }) => role === "roles/healthcare.fhirResourceReader");
    const writer = roles.find(({ role }) => role === "roles/logging.logWriter");
    // The FHIR reader role is bound on the dataset, never on the project.
    expect(reader?.type).toBe("google_healthcare_dataset_iam_member");
    expect(writer?.type).toBe("google_project_iam_member");
  });
});

// --- helpers ---------------------------------------------------------------------------------

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

type TerraformBinding = { type: string; role: string };

// Reads the IAM bindings Terraform declares for the query service account. It is deliberately
// tolerant about naming — the Terraform is another builder's file — and strict about what it
// asserts: the set of roles, and where each one is bound.
export function queryServiceAccountRoles(terraform: string): TerraformBinding[] {
  const blocks = terraformBlocks(terraform);
  const accountNames = blocks
    .filter(({ type, body }) => type === "google_service_account" && /query/i.test(body))
    .map(({ name }) => name);

  return blocks.flatMap(({ type, body }) => {
    if (!type.endsWith("_iam_member")) return [];
    const mentionsAccount =
      accountNames.some((name) => new RegExp(`google_service_account\\.${name}\\b`).test(body)) ||
      body.includes("ema-flow-query");
    if (!mentionsAccount) return [];
    const role = /\brole\s*=\s*"([^"]+)"/.exec(body)?.[1];
    return role === undefined ? [] : [{ type, role }];
  });
}

function terraformBlocks(terraform: string): { type: string; name: string; body: string }[] {
  const blocks: { type: string; name: string; body: string }[] = [];
  const header = /resource\s+"([^"]+)"\s+"([^"]+)"\s*\{/g;
  for (const match of terraform.matchAll(header)) {
    const start = match.index + match[0].length;
    let depth = 1;
    let index = start;
    while (index < terraform.length && depth > 0) {
      const character = terraform[index];
      if (character === "{") depth += 1;
      if (character === "}") depth -= 1;
      index += 1;
    }
    blocks.push({
      type: match[1] ?? "",
      name: match[2] ?? "",
      body: terraform.slice(start, index - 1),
    });
  }
  return blocks;
}
