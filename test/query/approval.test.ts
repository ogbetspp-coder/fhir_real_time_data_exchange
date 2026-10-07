import { afterEach, beforeAll, describe, expect, it } from "vitest";

import { approvalLinkId, approvalLinkProvenance } from "../../src/approval/link.js";
import { statementSha256 } from "../../src/approval/statement.js";
import type { SignedApprovalStatement } from "../../src/contracts/index.js";
import { QueryAuditRecordSchema } from "../../src/contracts/query-tools.js";
import { loadEmaMapping, type EmaMapping } from "../../src/fhir/mapping.js";
import { toProvenanceResource } from "../../src/fhir/provenance.js";
import { isComposition, type FhirBundle, type FhirResource } from "../../src/fhir/types.js";
import { loadQueryConfig } from "../../src/query/config.js";
import { approvalSources } from "../../src/query/app.js";
import {
  APPROVAL_READS,
  FIND_PRODUCT_VERIFIED_SCAN_HORIZON,
  type ApprovalSources,
} from "../../src/query/tools.js";
import { sha256Utf8, stableUuid } from "../../src/lib/hash.js";
import {
  EMAIL,
  MemoryHeads,
  SUBJECT,
  approvableRecord,
  otherKeys,
  signStatement,
  statementFor,
  trustedKeys,
  type ApprovableRecord,
} from "../support/approval.js";
import {
  PRINCIPAL_A,
  buildQueryStore,
  callTool,
  connectHarness,
  type Harness,
  type QueryStore,
  type SeededDocument,
} from "./fixtures.js";

// Build step 5 of docs/design/approval.md: the query service verifies, on every answer, the signed
// statement linked to the version it serves, and re-hashes every section against it (D9).
// Evidence asked for: `not-approved` for an unapproved product; an approved version answering with
// the approver's name in its evidence; a superseded version marked; a tampered section refused.

const SECTION = "smpc.4.2.posology";

let mapping: EmaMapping;
let base: QueryStore;
let one: ApprovableRecord;
let two: ApprovableRecord;
let bundleId: string;

beforeAll(async () => {
  mapping = await loadEmaMapping();
  base = buildQueryStore(mapping);
  one = approvableRecord(mapping, { product: "synthetic-paracetamol", version: 1 });
  two = approvableRecord(mapping, { product: "synthetic-paracetamol", version: 2 });
  bundleId = one.facts.document.emaBundleId;
});

function stored(record: ApprovableRecord, versionId: string): FhirBundle {
  return {
    ...structuredClone(record.transformed.documentBundle),
    meta: { versionId, lastUpdated: `2026-10-0${versionId}T09:00:00.000Z` },
  };
}

function ingestion(record: ApprovableRecord): FhirResource {
  const composition = record.transformed.documentBundle.entry[0]?.resource;
  return toProvenanceResource(record.gate.submission, record.gate.report, {
    bundleId,
    compositionId: composition?.id ?? "",
  });
}

type World = {
  documents: Map<string, SeededDocument>;
  provenances: Map<string, FhirResource>;
  heads: MemoryHeads;
  signed: { one: SignedApprovalStatement; two: SignedApprovalStatement };
  approvals: ApprovalSources;
};

// Version 1 and version 2 of one document, each published under its own approval and linked to
// it; version 2's approval is the head.
function world(): World {
  const signedOne = signStatement(statementFor(one.facts));
  const signedTwo = signStatement(
    statementFor(two.facts, {
      sequence: 2,
      previousStatementSha256: statementSha256(signedOne.statement),
    }),
  );
  const heads = new MemoryHeads();
  heads.append(signedOne);
  heads.append(signedTwo);
  const provenances = new Map<string, FhirResource>();
  for (const resource of [
    approvalLinkProvenance(bundleId, "1", signedOne),
    approvalLinkProvenance(bundleId, "2", signedTwo),
    ingestion(one),
    ingestion(two),
  ]) {
    provenances.set(resource.id ?? "", resource);
  }
  const documents = new Map<string, SeededDocument>([
    [bundleId, { bundle: stored(two, "2"), history: [stored(one, "1")] }],
  ]);
  return {
    documents,
    provenances,
    heads,
    signed: { one: signedOne, two: signedTwo },
    approvals: {
      environment: "dev",
      readProvenance: (id) => Promise.resolve(structuredClone(provenances.get(id))),
      heads: () => heads,
      keys: trustedKeys,
    },
  };
}

let harness: Harness | undefined;

afterEach(async () => {
  await harness?.close();
  harness = undefined;
});

async function connect(w: World, bundles: string[] = [bundleId], readBudget?: number) {
  harness = await connectHarness({
    store: base,
    principal: PRINCIPAL_A,
    entitlements: { bundles },
    documents: w.documents,
    approvals: w.approvals,
    ...(readBudget === undefined ? {} : { readBudget }),
  });
  return harness;
}

describe("an approved version", () => {
  it("answers with the approver's name, role, time and meaning, and the statement's hash", async () => {
    const w = world();
    const h = await connect(w);
    const answer = await callTool(h, "get_section", { bundleId, sourceKey: SECTION });
    expect(answer.isError).toBe(false);
    expect(answer.structured.approval).toEqual({
      statementSha256: statementSha256(w.signed.two.statement),
      kind: "approve",
      meaning: "record-represents-approved-label",
      sequence: 2,
      approver: {
        sub: SUBJECT,
        role: "content-reviewer",
        name: "Synthetic Approver",
        email: EMAIL,
      },
      signedAt: "2026-10-06T12:00:00.000Z",
      superseded: false,
    });
    expect(answer.structured.provenanceResourceId).toBe(approvalLinkId(bundleId, "2"));
    expect(h.audits.at(-1)).toMatchObject({
      outcome: "ok",
      versionId: "2",
      approverSub: SUBJECT,
      statementSha256: statementSha256(w.signed.two.statement),
    });
    expect(QueryAuditRecordSchema.parse(h.audits.at(-1))).toBeDefined();
  });

  it("names the statement's approver in get_provenance, with the ingestion record's facts", async () => {
    const w = world();
    const h = await connect(w);
    const answer = await callTool(h, "get_provenance", { bundleId });
    expect(answer.isError).toBe(false);
    expect(answer.structured).toMatchObject({
      approver: { id: SUBJECT, role: "content-reviewer" },
      approvedContentSha256: two.facts.approvedContentSha256,
      provenanceResourceId: ingestion(two).id,
      approval: { statementSha256: statementSha256(w.signed.two.statement), superseded: false },
    });
  });

  it("answers verify_quote and find_product only after verifying", async () => {
    const w = world();
    const h = await connect(w);
    const product = await callTool(h, "find_product", { query: "paracetamol" });
    expect(product.structured).toMatchObject({ truncated: false });
    expect((product.structured.products as unknown[]).length).toBe(1);
    const quote = await callTool(h, "verify_quote", { bundleId, quote: "not for clinical use" });
    expect(quote.isError).toBe(false);
    expect(h.audits.at(-1)?.statementSha256).toBe(statementSha256(w.signed.two.statement));
  });
});

describe("a superseded version", () => {
  it("is answered when named, and marked superseded by the head's statement", async () => {
    const w = world();
    const h = await connect(w);
    const answer = await callTool(h, "get_section", {
      bundleId,
      versionId: "1",
      sourceKey: SECTION,
    });
    expect(answer.isError).toBe(false);
    expect(answer.structured.approval).toMatchObject({
      statementSha256: statementSha256(w.signed.one.statement),
      superseded: true,
      supersededBy: statementSha256(w.signed.two.statement),
    });
    expect(answer.structured.provenanceResourceId).toBe(approvalLinkId(bundleId, "1"));
  });

  it("is never the current text: a newest version whose approval is not the head is not-approved", async () => {
    const w = world();
    // A third approval is the head, but its version is not published yet.
    w.heads.append(
      signStatement(
        statementFor(two.facts, {
          sequence: 3,
          previousStatementSha256: statementSha256(w.signed.two.statement),
          reviewSha256: "c".repeat(64),
        }),
      ),
    );
    const h = await connect(w);
    expect((await callTool(h, "get_section", { bundleId, sourceKey: SECTION })).text).toBe(
      "not-approved",
    );
    const named = await callTool(h, "get_section", {
      bundleId,
      versionId: "2",
      sourceKey: SECTION,
    });
    expect(named.structured.approval).toMatchObject({ superseded: true });
  });
});

describe("a version without a valid approval", () => {
  async function expectNotApproved(w: World, args: Record<string, unknown> = {}) {
    const h = await connect(w);
    for (const [tool, extra] of [
      ["get_section", { sourceKey: SECTION }],
      ["get_provenance", {}],
      ["verify_quote", { quote: "not for clinical use" }],
    ] as const) {
      const answer = await callTool(h, tool, { bundleId, ...extra, ...args });
      expect([tool, answer.isError, answer.text]).toEqual([tool, true, "not-approved"]);
      expect(h.audits.at(-1)?.outcome).toBe("not-approved");
    }
    const found = await callTool(h, "find_product", { query: "paracetamol" });
    expect(found.structured).toEqual({ products: [], truncated: false });
  }

  it("is not-approved when nothing links it, as the unsigned smoke product is", async () => {
    const w = world();
    w.provenances.clear();
    await expectNotApproved(w);
  });

  it("is not-approved when a stored section no longer hashes to the statement's", async () => {
    const w = world();
    const document = w.documents.get(bundleId);
    const composition = document?.bundle.entry[0]?.resource;
    if (composition === undefined || !isComposition(composition)) throw new Error("no Composition");
    const target = composition.section
      .flatMap(function all(section): typeof composition.section {
        return [section, ...(section.section ?? []).flatMap(all)];
      })
      .find(({ id }) => id === stableUuid("ema-qrd-section", SECTION));
    if (target?.text === undefined) throw new Error("no section");
    target.text.div = target.text.div.replace("</div>", "<p>Tampered.</p></div>");
    await expectNotApproved(w);
  });

  it("is not-approved when its link was signed by another key, or names another environment", async () => {
    for (const forged of [
      signStatement(statementFor(two.facts, { sequence: 2 }), otherKeys().privateKey),
      signStatement(statementFor(two.facts, { sequence: 2, environment: "validation" })),
    ]) {
      const w = world();
      const link = approvalLinkProvenance(bundleId, "2", forged);
      w.provenances.set(link.id ?? "", link);
      await expectNotApproved(w);
      await harness?.close();
      harness = undefined;
    }
  });

  it("is not-approved when the link names another version, or the document has no head", async () => {
    const w = world();
    // Version 1's link stored under version 2's id.
    const moved = {
      ...approvalLinkProvenance(bundleId, "1", w.signed.one),
      id: approvalLinkId(bundleId, "2"),
    };
    w.provenances.set(moved.id, moved);
    await expectNotApproved(w);
    await harness?.close();
    harness = undefined;

    const headless = world();
    headless.heads.objects.clear();
    await expectNotApproved(headless);
  });
});

describe("the reads verification costs", () => {
  it("are the Bundle and three more, and an exhausted budget is unavailable, never an answer", async () => {
    const w = world();
    const h = await connect(w, [bundleId], 1 + APPROVAL_READS - 1);
    const answer = await callTool(h, "get_section", { bundleId, sourceKey: SECTION });
    expect(answer.text).toBe("unavailable");
    await h.close();
    harness = undefined;
    const enough = await connect(w, [bundleId], 1 + APPROVAL_READS);
    expect((await callTool(enough, "get_section", { bundleId, sourceKey: SECTION })).isError).toBe(
      false,
    );
  });

  it("recalculate find_product's horizon", async () => {
    const w = world();
    const many = Array.from({ length: FIND_PRODUCT_VERIFIED_SCAN_HORIZON + 5 }, (_, index) =>
      stableUuid("unheld", String(index)),
    );
    const h = await connect(w, [bundleId, ...many]);
    const found = await callTool(h, "find_product", { query: "paracetamol" });
    expect(found.structured).toMatchObject({ truncated: true });
    expect(h.log.bundles.length).toBe(FIND_PRODUCT_VERIFIED_SCAN_HORIZON);
  });
});

describe("the setting", () => {
  const CONFIG = {
    GOOGLE_CLOUD_PROJECT: "p",
    HEALTHCARE_DATASET_ID: "d",
    TARGET_FHIR_STORE_ID: "s",
    QUERY_AUDIENCE: "https://q.example",
  };

  it("is off by default, and off means no verification at all", () => {
    const config = loadQueryConfig(CONFIG);
    expect(config.APPROVAL_VERIFICATION).toBe(false);
    expect(
      approvalSources(config, { readProvenance: () => Promise.resolve(undefined) }),
    ).toBeUndefined();
  });

  it("refuses to start on without its environment, heads bucket and key", async () => {
    expect(() => loadQueryConfig({ ...CONFIG, APPROVAL_VERIFICATION: "on" })).toThrow(
      /APPROVAL_ENVIRONMENT is required when APPROVAL_VERIFICATION=on/,
    );
    const on = loadQueryConfig({
      ...CONFIG,
      APPROVAL_VERIFICATION: "on",
      APPROVAL_ENVIRONMENT: "dev",
      APPROVAL_HEADS_BUCKET: "heads",
      APPROVAL_SIGNING_KEY: "projects/p/locations/l/keyRings/r/cryptoKeys/approval-signing-hsm",
    });
    const read: string[] = [];
    const sources = approvalSources(on, {
      readProvenance: (id) => {
        read.push(id);
        return Promise.resolve(undefined);
      },
    });
    expect(sources?.environment).toBe("dev");
    // Each source is the deployment's: the store reader's by-id read, and the heads bucket bound
    // to the request's signal.
    await Promise.all([
      sources?.readProvenance("p-1").then(() => expect(read).toEqual(["p-1"])),
      Promise.resolve(expect(sources?.heads(undefined)).toBeDefined()),
    ]);
  });
});

// The stored narrative answered is the one the statement hashed: a client can check it.
describe("what a client can recompute", () => {
  it("the section's hash equals the statement's section hash", async () => {
    const w = world();
    const h = await connect(w);
    const answer = await callTool(h, "get_section", { bundleId, sourceKey: SECTION });
    const expected = w.signed.two.statement.sections.find(({ sourceKey }) => sourceKey === SECTION);
    expect(sha256Utf8(answer.structured.div as string)).toBe(expected?.narrativeDivSha256);
  });
});
