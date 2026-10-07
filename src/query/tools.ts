import { z } from "zod";

import { IsoDateTime, Uuid } from "../contracts/common.js";
import { ApproverRole } from "../contracts/ingestion-provenance.js";
import {
  DocumentRefSchema,
  FindProductOutputSchema,
  ProductSummarySchema,
  ProvenanceDetailSchema,
  QuoteVerificationSchema,
  SectionContentSchema,
  type DocumentRef,
  type FindProductInput,
  type FindProductOutput,
  type GetProvenanceInput,
  type GetSectionInput,
  type ProductSummary,
  type ProvenanceDetail,
  type QueryAuditOutcome,
  type QueryError,
  type QuoteMatch,
  type QuoteVerification,
  type SectionContent,
  type VerifyQuoteInput,
} from "../contracts/query-tools.js";
import {
  NORMALIZATION_VERSION,
  isGap,
  isReservedCodePoint,
  normalizeText,
  xhtmlToText,
} from "../fidelity/index.js";
import type { EmaMapping, SectionRule } from "../fhir/mapping.js";
import {
  APPROVAL_CONTENT_EXTENSION_URL,
  APPROVER_IDENTIFIER_SYSTEM,
  APPROVER_ROLE_SYSTEM,
  FIDELITY_REPORT_IDENTIFIER_SYSTEM,
  MODEL_IDENTIFIER_SYSTEM,
  PARTICIPANT_TYPE_ASSEMBLER,
  PARTICIPANT_TYPE_ATTESTER,
  PARTICIPANT_TYPE_SYSTEM,
  SOURCE_DOCUMENT_IDENTIFIER_SYSTEM,
} from "../fhir/provenance.js";
import {
  isComposition,
  type CompositionSection,
  type FhirBundle,
  type FhirComposition,
  type FhirResource,
} from "../fhir/types.js";
import { approvalLinkBytes, approvalLinkId } from "../approval/link.js";
import {
  checkStatement,
  headObjectName,
  publishedDocumentSha256,
  publishedSections,
  readHead,
  verifyWithKeys,
  type HeadSource,
  type KeySource,
  type VerifiedStatement,
} from "../approval/statement.js";
import type { ApprovalEnvironment } from "../contracts/approval.js";
import type { ApprovalCitation } from "../contracts/query-tools.js";
import { sha256Utf8, stableUuid } from "../lib/hash.js";
import type { Entitlements } from "./entitlements.js";
import type { FhirReader } from "./fhir-reader.js";
import { locateQuote } from "./quote-edge.js";

// The four tools, as pure functions over (entitlements, reader, mapping, input). Nothing here
// logs, reads the environment, or speaks HTTP: src/query/app.ts does that. Every answer is
// built from what the store holds and is validated against the published contract before it is
// returned, so a shape the contract does not allow cannot leave the service.

// --- outcomes ------------------------------------------------------------------------------

type QueryErrorCodeValue = QueryError["error"];
type QueryToolNameValue = QueryError["tool"];

// What a call resolved, for the audit record: the document it named and, once a stored version
// was read, which version. Neither is ever narrative.
type Resolved = {
  bundleId?: string | undefined;
  versionId?: string | undefined;
  // The approval an answer was verified against, for the audit record (query-tools 5.0.0).
  approval?: { approverSub: string; statementSha256: string } | undefined;
};

export type ToolOutcome<T> =
  | ({ status: "ok"; value: T; resultCount: number; truncated?: boolean | undefined } & Resolved)
  | ({
      status: "error";
      error: QueryError;
      // What the audit record records, which is not always what the caller is told: outside a
      // caller's entitlement the answer is `document-not-found` and the record is
      // `not-entitled`, so existence is not disclosed but the attempt is still visible.
      auditOutcome: Exclude<QueryAuditOutcome, "ok">;
    } & Resolved);

// --- read budget ------------------------------------------------------------------------------

// Every store read one HTTP request may make, shared by every tool call in its JSON-RPC batch.
// Without it the batch cap (8 messages) times the find_product horizon (200 documents) allows
// 1,600 Bundle reads per request. 400 is twice the horizon: it lets one request run a whole
// find_product scan and then read the documents that scan named, and it bounds the request at
// 400 reads instead of 1,600. It is not a per-principal limit — a caller may send many requests.
export const REQUEST_READ_BUDGET = 400;

// At most this many store reads of one HTTP request are in flight at once, across every tool
// call of its batch. The SDK dispatches a batch's entries concurrently, so a bound per call
// would allow the batch cap (8) times the per-call pool (8) — 64 whole-Bundle reads at once for
// one request.
export const REQUEST_READ_CONCURRENCY = 8;

export type ReadBudget = {
  // Reserves one store read, or reports false when the request has none left.
  take(): boolean;
  remaining(): number;
  // Runs one store read once fewer than REQUEST_READ_CONCURRENCY of the request's reads are in
  // flight, and frees its place when the read settles, whichever way.
  inFlight<T>(read: () => Promise<T>): Promise<T>;
};

export function createReadBudget(
  limit: number = REQUEST_READ_BUDGET,
  concurrency: number = REQUEST_READ_CONCURRENCY,
): ReadBudget {
  let left = limit;
  let running = 0;
  const waiting: (() => void)[] = [];
  const release = (): void => {
    const next = waiting.shift();
    // The freed place passes straight to the next waiter, so `running` is unchanged.
    if (next === undefined) running -= 1;
    else next();
  };
  return {
    take(): boolean {
      if (left <= 0) return false;
      left -= 1;
      return true;
    },
    remaining(): number {
      return left;
    },
    async inFlight<T>(read: () => Promise<T>): Promise<T> {
      if (running < concurrency) running += 1;
      else
        await new Promise<void>((resolve) => {
          waiting.push(resolve);
        });
      try {
        return await read();
      } finally {
        release();
      }
    },
  };
}

export type ToolContext = {
  entitlements: Entitlements | undefined;
  reader: FhirReader;
  mapping: EmaMapping;
  // Shared by every tool call of one HTTP request; every read below takes from it.
  readBudget: ReadBudget;
  // Aborted when the HTTP request is over — answered, abandoned at the deadline, or left by
  // its client — so a tool still running stops reading the store for an answer nobody gets.
  signal?: AbortSignal | undefined;
  // Present when the deployment verifies approvals (APPROVAL_VERIFICATION, query-tools 5.0.0):
  // then every answer is verified against the signed statement linked to its version, and a
  // version without a valid one is `not-approved` (docs/design/approval.md, D9).
  approvals?: ApprovalSources | undefined;
};

export type ApprovalSources = {
  environment: ApprovalEnvironment;
  // A Provenance by id, from the validated store.
  readProvenance(id: string, signal?: AbortSignal): Promise<FhirResource | undefined>;
  // The heads bucket, bound to the request's signal.
  heads(signal: AbortSignal | undefined): HeadSource;
  keys: KeySource;
};

// Every store read goes through here: it waits for one of the request's in-flight places, and
// it is not made at all once the request is over.
function storeRead<T>(
  context: ToolContext,
  read: (signal: AbortSignal | undefined) => Promise<T>,
): Promise<T> {
  return context.readBudget.inFlight(() => {
    context.signal?.throwIfAborted();
    return read(context.signal);
  });
}

function fail<T>(
  tool: QueryToolNameValue,
  code: QueryErrorCodeValue,
  resolved: Resolved = {},
): ToolOutcome<T> {
  return { status: "error", error: { tool, error: code }, auditOutcome: code, ...resolved };
}

function notEntitled<T>(tool: QueryToolNameValue, bundleId: string): ToolOutcome<T> {
  return {
    status: "error",
    error: { tool, error: "document-not-found" },
    auditOutcome: "not-entitled",
    bundleId,
  };
}

function isEntitled(entitlements: Entitlements | undefined, bundleId: string): boolean {
  return entitlements?.bundles.includes(bundleId) === true;
}

// --- mapping and section helpers -------------------------------------------------------------

type MappingIndex = {
  titleOf: Map<string, string>;
  sourceKeyOfSectionId: Map<string, string>;
  sourceKeys: string[];
};

function walkRules(rule: SectionRule, index: MappingIndex): void {
  index.titleOf.set(rule.sourceKey, rule.title);
  // A section is located by id alone: the transform stamps every EMA section with
  // stableUuid("ema-qrd-section", sourceKey) (src/fhir/transform.ts), so the canonical key
  // resolves without translating the EMA coding back to the source coding.
  index.sourceKeyOfSectionId.set(stableUuid("ema-qrd-section", rule.sourceKey), rule.sourceKey);
  index.sourceKeys.push(rule.sourceKey);
  for (const child of rule.children ?? []) walkRules(child, index);
}

function indexMapping(mapping: EmaMapping): MappingIndex {
  const index: MappingIndex = {
    titleOf: new Map(),
    sourceKeyOfSectionId: new Map(),
    sourceKeys: [],
  };
  walkRules(mapping.root, index);
  return index;
}

type LocatedSection = { section: CompositionSection; path: string };

function locateSections(
  sections: CompositionSection[],
  basePath = "Composition.section",
  found: LocatedSection[] = [],
): LocatedSection[] {
  sections.forEach((section, position) => {
    const path = `${basePath}[${position}]`;
    found.push({ section, path });
    if (section.section !== undefined) locateSections(section.section, `${path}.section`, found);
  });
  return found;
}

type SectionNarrative = {
  div: string;
  text: string;
  narrativeDivSha256: string;
  normalizedTextSha256: string;
};

// `div` verbatim, `text` its normalised plain text, both hashes recomputable by the caller from
// `div` alone. A stored narrative that fails this passed the publishing gate and no longer
// parses: that is a store problem, not a caller problem, and it surfaces as `unavailable`.
function narrativeOf(section: CompositionSection): SectionNarrative | undefined {
  const div = section.text?.div;
  if (div === undefined) return undefined;
  const text = normalizeText(xhtmlToText(div));
  return {
    div,
    text,
    narrativeDivSha256: sha256Utf8(div),
    normalizedTextSha256: sha256Utf8(text),
  };
}

// --- document loading --------------------------------------------------------------------------

type LoadedDocument = {
  // The stored Bundle as read, which a verified approval's documentBundleSha256 must hash.
  bundle: FhirBundle;
  composition: FhirComposition;
  document: DocumentRef;
  entries: { fullUrl: string; resource: Record<string, unknown> }[];
};

type DocumentSelector = { bundleId: string; versionId?: string | undefined };

async function loadDocument<T>(
  context: ToolContext,
  tool: QueryToolNameValue,
  selector: DocumentSelector,
): Promise<LoadedDocument | ToolOutcome<T>> {
  // Entitlement is applied before the read, never after it: a bundle outside the caller's list
  // is not fetched at all (design note, constraint 4). It is also applied before the budget, so
  // an exhausted budget cannot turn a `not-entitled` record into an `unavailable` one.
  if (!isEntitled(context.entitlements, selector.bundleId)) {
    return notEntitled<T>(tool, selector.bundleId);
  }

  // The request's read budget is spent before the read is made, so a request that has none left
  // answers `unavailable` rather than reading.
  if (!context.readBudget.take()) {
    return fail<T>(tool, "unavailable", { bundleId: selector.bundleId });
  }

  const { bundleId, versionId } = selector;
  const bundle = await storeRead(context, (signal) =>
    versionId === undefined
      ? context.reader.readBundle(bundleId, signal)
      : context.reader.readBundleVersion(bundleId, versionId, signal),
  );

  if (bundle === undefined) {
    return fail<T>(
      tool,
      selector.versionId === undefined ? "document-not-found" : "version-not-found",
      {
        bundleId: selector.bundleId,
      },
    );
  }

  const first = bundle.entry[0]?.resource;
  if (first === undefined || !isComposition(first)) {
    return fail<T>(tool, "unavailable", { bundleId: selector.bundleId });
  }

  // Every returned fact names the exact version it came from; a stored document that cannot say
  // which version it is cannot be cited, so it is not answered from.
  const document = DocumentRefSchema.safeParse({
    bundleId: selector.bundleId,
    versionId: bundle.meta?.versionId,
    lastUpdated: bundle.meta?.lastUpdated,
  });
  if (!document.success) return fail<T>(tool, "unavailable", { bundleId: selector.bundleId });

  return {
    bundle,
    composition: first,
    document: document.data,
    entries: bundle.entry.map(({ fullUrl, resource }) => ({
      fullUrl,
      resource: resource,
    })),
  };
}

function isOutcome<T>(value: LoadedDocument | ToolOutcome<T>): value is ToolOutcome<T> {
  return "status" in value;
}

// Once a document was read, every outcome — success or failure — names the version it resolved.
function resolved(loaded: LoadedDocument): Resolved {
  return { bundleId: loaded.document.bundleId, versionId: loaded.document.versionId };
}

// --- which version an approval belongs to -------------------------------------------------------

// Nothing in the store yet binds a stored version to its own approval: the Provenance target
// is `Bundle/<id>`, unversioned, and the stored Bundle does not point at its Provenance. The
// reader answers with the most recently *written* Provenance (latestProvenance, by the store's
// `meta.lastUpdated`), and the worker writes an approved version and its Provenance in one
// transaction, so that approval is the current version's whenever the current version came
// through the gate — an inference from write order, not a recorded link. For any earlier
// version it is another version's approval: a request for version 1 of a document that has a
// version 2 would be answered with version 2's approver and approved-content hash. Until the
// approval design binds a version to its approval (docs/vision.md, "The order", item 2), an
// approval is attached only to the version the store currently serves as the document; for
// any other the service says nothing about approval rather than the wrong thing. Not closed by
// this, and stated in the design note: a current version written without an approval, by a run
// source that bypasses the gate, is answered with the last approval written; and a version
// written between this read and the Provenance search can be answered with its approval.
//
// `current`: no version was named (loadDocument read the current one), or the named version is
// the current one. `superseded`: the store's current version is a different one, or the plain
// read answers nothing or a Bundle that does not say its version. `out-of-budget`: finding out
// would have taken a read the request no longer has.
type VersionStanding = "current" | "superseded" | "out-of-budget";

async function versionStanding(
  context: ToolContext,
  selector: DocumentSelector,
  loaded: LoadedDocument,
): Promise<VersionStanding> {
  if (selector.versionId === undefined) return "current";
  // The current version is one more store read, so it takes from the budget like any other.
  if (!context.readBudget.take()) return "out-of-budget";
  const current = await storeRead(context, (signal) =>
    context.reader.readBundle(selector.bundleId, signal),
  );
  return current?.meta?.versionId === loaded.document.versionId ? "current" : "superseded";
}

// --- the approval a version carries (query-tools 5.0.0) ---------------------------------------------

// The store reads one verification makes: the linked Provenance, the head's listing and the head's
// entry (docs/design/approval.md, D9 as amended: "the linked Provenance, one listing and the head");
// a version a later head supersedes takes one more, its own sequence's entry.
export const APPROVAL_READS = 3;

export type VerifiedVersion = { citation: ApprovalCitation; statement: VerifiedStatement };

function citationOf(
  verified: VerifiedStatement,
  supersededBy: string | undefined,
): ApprovalCitation {
  const { statement } = verified;
  return {
    statementSha256: verified.statementSha256,
    kind: statement.kind,
    meaning: statement.meaning,
    sequence: statement.sequence,
    approver: {
      sub: statement.approver.sub,
      role: statement.approver.role,
      name: statement.manifestation.name,
      email: statement.manifestation.email,
    },
    signedAt: statement.signedAt,
    superseded: supersededBy !== undefined,
    ...(supersededBy === undefined ? {} : { supersededBy }),
  };
}

// The signed statement linked to the version read, verified on this answer (D9): read by the link's
// deterministic id (D5), its signature checked against the environment's key, its document and
// environment this deployment's, every published section re-hashed from the stored Composition and
// equal to the statement's, and its standing read from the document's head. A plain request answers
// only a version whose statement is the head; a named version whose statement a later approval
// superseded is answered and marked so. Anything else is `not-approved`, never a guess.
async function verifyVersion<T>(
  context: ToolContext,
  tool: QueryToolNameValue,
  loaded: LoadedDocument,
  named: boolean,
): Promise<VerifiedVersion | ToolOutcome<T>> {
  const at = resolved(loaded);
  const approvals = context.approvals;
  if (approvals === undefined) throw new Error("approval verification is not configured");
  const { bundleId, versionId } = loaded.document;
  const notApproved = (): ToolOutcome<T> => fail<T>(tool, "not-approved", at);

  if (!context.readBudget.take()) return fail<T>(tool, "unavailable", at);
  const link = await storeRead(context, (signal) =>
    approvals.readProvenance(approvalLinkId(bundleId, versionId), signal),
  );
  const bytes = approvalLinkBytes(link, bundleId, versionId);
  if (bytes === undefined) return notApproved();
  const verified = await verifyWithKeys(bytes, approvals.keys);
  if (typeof verified === "string") return notApproved();
  const { statement } = verified;
  if (
    checkStatement(statement, { environment: approvals.environment }) !== undefined ||
    statement.document.emaBundleId !== bundleId
  ) {
    return notApproved();
  }
  // The whole stored Bundle, narrative and structure, must be the record the person approved
  // (the statement's documentBundleSha256), and every section with narrative must re-hash to the
  // statement's (D9).
  const sections = publishedSections(loaded.composition.section, context.mapping);
  if (
    sections === undefined ||
    checkStatement(statement, {
      environment: approvals.environment,
      sections,
      documentBundleSha256: publishedDocumentSha256(loaded.bundle),
    }) !== undefined
  ) {
    return notApproved();
  }

  // The head: one listing and one entry, each a read from the request's budget.
  if (context.readBudget.remaining() < APPROVAL_READS - 1) return fail<T>(tool, "unavailable", at);
  const source = approvals.heads(context.signal);
  const budgeted: HeadSource = {
    list: (prefix) => {
      context.readBudget.take();
      return storeRead(context, () => source.list(prefix));
    },
    read: (name) => {
      context.readBudget.take();
      return storeRead(context, () => source.read(name));
    },
  };
  const head = await readHead(budgeted, approvals.keys, statement.document);
  if (typeof head === "string") return notApproved();
  if (head.bytes === verified.bytes)
    return { citation: citationOf(verified, undefined), statement: verified };
  // A later approval of the same document is the head: this version is not its current text.
  if (!named || head.statement.sequence <= statement.sequence) return notApproved();
  // And this statement was that document's head at its own sequence: a valid signature on a
  // statement that never became a head (one that lost a race) is not an approval.
  if (!context.readBudget.take()) return fail<T>(tool, "unavailable", at);
  const own = await storeRead(context, () =>
    source.read(headObjectName(statement.document, statement.sequence)),
  );
  if (own !== verified.bytes) return notApproved();
  return { citation: citationOf(verified, head.statementSha256), statement: verified };
}

function isVerified<T>(value: VerifiedVersion | ToolOutcome<T>): value is VerifiedVersion {
  return "citation" in value;
}

function approvalResolved(verified: VerifiedVersion): Resolved["approval"] {
  return {
    approverSub: verified.statement.statement.approver.sub,
    statementSha256: verified.statement.statementSha256,
  };
}

// --- get_section --------------------------------------------------------------------------------

export async function getSection(
  context: ToolContext,
  input: GetSectionInput,
): Promise<ToolOutcome<SectionContent>> {
  const loaded = await loadDocument<SectionContent>(context, "get_section", input);
  if (isOutcome(loaded)) return loaded;
  // With approvals verified, nothing of a version is answered before its approval is: not even
  // which sections it has.
  const verified =
    context.approvals === undefined
      ? undefined
      : await verifyVersion<SectionContent>(
          context,
          "get_section",
          loaded,
          input.versionId !== undefined,
        );
  if (verified !== undefined && !isVerified(verified)) return verified;
  const at = {
    ...resolved(loaded),
    ...(verified === undefined ? {} : { approval: approvalResolved(verified) }),
  };

  const index = indexMapping(context.mapping);
  const title = index.titleOf.get(input.sourceKey);
  if (title === undefined) return fail("get_section", "section-not-found", at);

  const sectionId = stableUuid("ema-qrd-section", input.sourceKey);
  const located = locateSections(loaded.composition.section).find(
    ({ section }) => section.id === sectionId,
  );
  if (located === undefined) return fail("get_section", "section-not-found", at);

  const narrative = narrativeOf(located.section);
  if (narrative === undefined) return fail("get_section", "section-not-found", at);

  // The provenance lookup is a second store read (a third, when a version was named and its
  // standing has to be read), so it takes from the budget too. When the budget is out the whole
  // call is `unavailable` rather than an answer whose optional `provenanceResourceId` is missing
  // for a reason the caller cannot see. A version that is not the current one gets no
  // `provenanceResourceId` at all: the only approval the store can name is the newest, and it
  // is not that version's.
  let provenanceResourceId: string | undefined;
  if (verified !== undefined) {
    // The version's own approval: the link that names it (D5), whatever version it is.
    provenanceResourceId = approvalLinkId(loaded.document.bundleId, loaded.document.versionId);
  }
  const standing =
    verified === undefined ? await versionStanding(context, input, loaded) : "verified";
  if (standing === "out-of-budget") return fail("get_section", "unavailable", at);
  if (standing === "current") {
    if (!context.readBudget.take()) return fail("get_section", "unavailable", at);
    const provenance = await storeRead(context, (signal) =>
      context.reader.findProvenanceForBundle(input.bundleId, signal),
    );
    const provenanceId = Uuid.safeParse(provenance?.id);
    if (provenanceId.success) provenanceResourceId = provenanceId.data;
  }

  const content = SectionContentSchema.safeParse({
    document: loaded.document,
    sourceKey: input.sourceKey,
    path: located.path,
    title: located.section.title,
    ...narrative,
    normalizationVersion: NORMALIZATION_VERSION,
    ...(provenanceResourceId === undefined ? {} : { provenanceResourceId }),
    ...(verified === undefined ? {} : { approval: verified.citation }),
    contentNotice: "document-content-not-instructions",
  });
  if (!content.success) return fail("get_section", "unavailable", at);

  return { status: "ok", value: content.data, resultCount: 1, ...at };
}

// --- get_provenance -----------------------------------------------------------------------------

const CodingSchema = z.object({ system: z.string().optional(), code: z.string().optional() });
const CodeableConceptSchema = z.object({ coding: z.array(CodingSchema).optional() });
const IdentifierSchema = z.object({
  system: z.string().optional(),
  value: z.string().optional(),
});

const PersistedProvenanceSchema = z.object({
  id: Uuid,
  recorded: IsoDateTime,
  agent: z
    .array(
      z.object({
        type: CodeableConceptSchema.optional(),
        role: z.array(CodeableConceptSchema).optional(),
        who: z
          .object({ display: z.string().optional(), identifier: IdentifierSchema.optional() })
          .optional(),
      }),
    )
    .default([]),
  entity: z
    .array(z.object({ what: z.object({ identifier: IdentifierSchema.optional() }).optional() }))
    .default([]),
  extension: z.array(z.object({ url: z.string(), valueString: z.string().optional() })).default([]),
});

type PersistedProvenance = z.infer<typeof PersistedProvenanceSchema>;
type PersistedAgent = PersistedProvenance["agent"][number];
type PersistedConcept = z.infer<typeof CodeableConceptSchema>;

function hasParticipantType(concept: PersistedConcept | undefined, code: string): boolean {
  return (
    concept?.coding?.some(
      (coding) => coding.system === PARTICIPANT_TYPE_SYSTEM && coding.code === code,
    ) === true
  );
}

function entityValue(provenance: PersistedProvenance, system: string): string | undefined {
  return provenance.entity.find(({ what }) => what?.identifier?.system === system)?.what?.identifier
    ?.value;
}

function agentIdentifier(agent: PersistedAgent, system: string): string | undefined {
  return agent.who?.identifier?.system === system ? agent.who.identifier.value : undefined;
}

// `display` is written as `${parser.name}@${parser.version}`; both halves are contract Tokens,
// and a Token may itself contain "@", so the split is at the last one.
function splitToolVersion(display: string): { name: string; version: string } | undefined {
  const at = display.lastIndexOf("@");
  if (at <= 0 || at === display.length - 1) return undefined;
  return { name: display.slice(0, at), version: display.slice(at + 1) };
}

// The approver's role is read from the attester agent's `role` coding under
// APPROVER_ROLE_SYSTEM, never inferred. The FHIR participant type says "attester"; it does not
// say whether that attester was a content reviewer or a QA reviewer, and the difference is a
// regulatory fact this service must not guess at. A persisted Provenance that does not carry
// the role is answered `unavailable`.
function approverRole(agent: PersistedAgent): string | undefined {
  const codings = (agent.role ?? [])
    .flatMap((concept) => concept.coding ?? [])
    .filter((coding) => coding.system === APPROVER_ROLE_SYSTEM);
  return codings.map((coding) => coding.code).find((code) => ApproverRole.safeParse(code).success);
}

export async function getProvenance(
  context: ToolContext,
  input: GetProvenanceInput,
): Promise<ToolOutcome<ProvenanceDetail>> {
  const loaded = await loadDocument<ProvenanceDetail>(context, "get_provenance", input);
  if (isOutcome(loaded)) return loaded;
  const verified =
    context.approvals === undefined
      ? undefined
      : await verifyVersion<ProvenanceDetail>(
          context,
          "get_provenance",
          loaded,
          input.versionId !== undefined,
        );
  if (verified !== undefined && !isVerified(verified)) return verified;
  const at = {
    ...resolved(loaded),
    ...(verified === undefined ? {} : { approval: approvalResolved(verified) }),
  };

  let resource: FhirResource | undefined;
  if (verified === undefined) {
    // A version that is not the current one cannot be tied to its own approval yet, and the
    // newest approval is not it: the answer is `unavailable` — the closed code this tool already
    // gives for an approval it cannot state in full — and the Provenance search is not made.
    const standing = await versionStanding(context, input, loaded);
    if (standing !== "current") return fail("get_provenance", "unavailable", at);

    // The provenance lookup is one more store read and takes from the request's budget.
    if (!context.readBudget.take()) return fail("get_provenance", "unavailable", at);
    resource = await storeRead(context, (signal) =>
      context.reader.findProvenanceForBundle(input.bundleId, signal),
    );
  } else {
    // The ingestion Provenance the verified statement's submission wrote, by its deterministic id
    // (src/fhir/provenance.ts), read directly: no search, no newest-first.
    const { statement } = verified.statement;
    const id = stableUuid(
      "ingestion-provenance",
      `${statement.document.identifier.value}:${statement.submissionId}`,
    );
    const approvals = context.approvals;
    if (approvals === undefined || !context.readBudget.take()) {
      return fail("get_provenance", "unavailable", at);
    }
    resource = await storeRead(context, (signal) => approvals.readProvenance(id, signal));
  }
  if (resource === undefined) return fail("get_provenance", "unavailable", at);
  const parsed = PersistedProvenanceSchema.safeParse(resource);
  if (!parsed.success) return fail("get_provenance", "unavailable", at);
  const persisted = parsed.data;

  const assemblers = persisted.agent.filter((agent) =>
    hasParticipantType(agent.type, PARTICIPANT_TYPE_ASSEMBLER),
  );
  const extractor = assemblers
    .map((agent) => agent.who?.display)
    .filter((display): display is string => display !== undefined)
    .map(splitToolVersion)
    .find((tool) => tool !== undefined);
  const modelId = assemblers
    .map((agent) => agentIdentifier(agent, MODEL_IDENTIFIER_SYSTEM))
    .find((id) => id !== undefined);

  const attester = persisted.agent.find((agent) =>
    hasParticipantType(agent.type, PARTICIPANT_TYPE_ATTESTER),
  );
  // With approvals verified, the approver is the statement's, as Google asserted them; the
  // ingestion Provenance's attester is the submission's own unverified claim, and is not named.
  const approverId =
    verified !== undefined
      ? verified.statement.statement.approver.sub
      : attester === undefined
        ? undefined
        : agentIdentifier(attester, APPROVER_IDENTIFIER_SYSTEM);
  const role =
    verified !== undefined
      ? verified.statement.statement.approver.role
      : attester === undefined
        ? undefined
        : approverRole(attester);
  if (extractor === undefined || approverId === undefined || role === undefined) {
    return fail("get_provenance", "unavailable", at);
  }
  const approvedContentSha256 = persisted.extension.find(
    ({ url }) => url === APPROVAL_CONTENT_EXTENSION_URL,
  )?.valueString;
  // The ingestion record and the statement must name the same approved content.
  if (
    verified !== undefined &&
    approvedContentSha256 !== verified.statement.statement.approvedContentSha256
  ) {
    return fail("get_provenance", "not-approved", at);
  }

  let section: ProvenanceDetail["section"];
  if (input.sourceKey !== undefined) {
    const index = indexMapping(context.mapping);
    if (!index.titleOf.has(input.sourceKey)) {
      return fail("get_provenance", "section-not-found", at);
    }
    const sectionId = stableUuid("ema-qrd-section", input.sourceKey);
    const located = locateSections(loaded.composition.section).find(
      (candidate) => candidate.section.id === sectionId,
    );
    const narrative = located === undefined ? undefined : narrativeOf(located.section);
    if (narrative === undefined) return fail("get_provenance", "section-not-found", at);
    // Recomputed live from the stored narrative, so a caller can compare them with any record
    // it holds independently of this service.
    section = {
      sourceKey: input.sourceKey,
      narrativeDivSha256: narrative.narrativeDivSha256,
      normalizedTextSha256: narrative.normalizedTextSha256,
    };
  }

  const detail = ProvenanceDetailSchema.safeParse({
    document: loaded.document,
    provenanceResourceId: persisted.id,
    recorded: persisted.recorded,
    sourceDocumentSha256: entityValue(persisted, SOURCE_DOCUMENT_IDENTIFIER_SYSTEM),
    fidelityReportSha256: entityValue(persisted, FIDELITY_REPORT_IDENTIFIER_SYSTEM),
    approvedContentSha256,
    extractor,
    ...(modelId === undefined ? {} : { model: { id: modelId } }),
    approver: { id: approverId, role },
    ...(section === undefined ? {} : { section }),
    ...(verified === undefined ? {} : { approval: verified.citation }),
  });
  if (!detail.success) return fail("get_provenance", "unavailable", at);

  return { status: "ok", value: detail.data, resultCount: 1, ...at };
}

// --- verify_quote --------------------------------------------------------------------------------

export async function verifyQuote(
  context: ToolContext,
  input: VerifyQuoteInput,
): Promise<ToolOutcome<QuoteVerification>> {
  // The entitlement decision comes before the quote is looked at, so every argument shape that
  // names a document outside the caller's entitlement is audited `not-entitled` — including one
  // whose quote the normalisation would reject. No store read is made here: `loadDocument`
  // below checks the same list again before it reads.
  if (!isEntitled(context.entitlements, input.bundleId)) {
    return notEntitled("verify_quote", input.bundleId);
  }

  // A quote carrying a character the normalisation forbids cannot be compared at all; that is a
  // property of the request, so it is `invalid-request` and never a `no-match`.
  let normalizedQuote: string;
  try {
    normalizedQuote = normalizeText(input.quote);
  } catch {
    return fail("verify_quote", "invalid-request", { bundleId: input.bundleId });
  }
  // A quote of gaps alone (whitespace, thin spaces, blank glyphs, code points Unicode says to
  // ignore) quotes nothing a reader sees, and could match between the groups of a number.
  if (!Array.from(normalizedQuote).some((point) => !isGap(point.codePointAt(0) ?? 0))) {
    return fail("verify_quote", "invalid-request", { bundleId: input.bundleId });
  }
  // A table's grid markers and a picture's U+FFFC are the scanner's, never a reader's: a quote
  // carrying one could join two rows or quote nothing a reader sees (fidelity-norm/3.0.0 section
  // 2, the rule narratives follow).
  if (Array.from(normalizedQuote).some((point) => isReservedCodePoint(point.codePointAt(0) ?? 0))) {
    return fail("verify_quote", "invalid-request", { bundleId: input.bundleId });
  }

  const loaded = await loadDocument<QuoteVerification>(context, "verify_quote", input);
  if (isOutcome(loaded)) return loaded;
  const verified =
    context.approvals === undefined
      ? undefined
      : await verifyVersion<QuoteVerification>(
          context,
          "verify_quote",
          loaded,
          input.versionId !== undefined,
        );
  if (verified !== undefined && !isVerified(verified)) return verified;
  const at = {
    ...resolved(loaded),
    ...(verified === undefined ? {} : { approval: approvalResolved(verified) }),
  };

  const index = indexMapping(context.mapping);
  let candidates = locateSections(loaded.composition.section).flatMap((located) => {
    const sourceKey = index.sourceKeyOfSectionId.get(located.section.id ?? "");
    const div = located.section.text?.div;
    return sourceKey === undefined || div === undefined ? [] : [{ sourceKey, div }];
  });

  if (input.sourceKey !== undefined) {
    const sourceKey = input.sourceKey;
    candidates = candidates.filter((candidate) => candidate.sourceKey === sourceKey);
    if (candidates.length === 0) return fail("verify_quote", "section-not-found", at);
  }

  // `sectionsSearched` is the number of candidate sections that carry a narrative, whether or
  // not the search stopped early: it describes the scope of the answer, not the work done.
  const sectionsSearched = candidates.length;

  // The search normalises each section's text and stops at the first occurrence the
  // quote-edge rule accepts; only the matched section's text is hashed.
  let match: QuoteMatch | undefined;
  for (const candidate of candidates) {
    const text = normalizeText(xhtmlToText(candidate.div));
    // Offsets are code points in the section's normalised text, as every offset in this
    // repository is (ADR 0002), not UTF-16 indices.
    const located = locateQuote(text, normalizedQuote);
    if (located === undefined) continue;
    match = {
      sourceKey: candidate.sourceKey,
      ...located,
      normalizedTextSha256: sha256Utf8(text),
    };
    break;
  }

  const verification = QuoteVerificationSchema.safeParse({
    document: loaded.document,
    result: match === undefined ? "no-match" : "match",
    normalizationVersion: NORMALIZATION_VERSION,
    // The hash is of the normalised quote, so a caller comparing it with a section's
    // `normalizedTextSha256` is comparing two values produced the same way.
    quoteSha256: sha256Utf8(normalizedQuote),
    sectionsSearched,
    ...(match === undefined ? {} : { match }),
  });
  if (!verification.success) return fail("verify_quote", "unavailable", at);

  return {
    status: "ok",
    value: verification.data,
    resultCount: match === undefined ? 0 : 1,
    ...at,
  };
}

// --- find_product ---------------------------------------------------------------------------------

// One find_product call reads at most this many of the caller's entitled documents, in
// entitlement order; an entitlement longer than this is reported as `truncated`.
export const FIND_PRODUCT_SCAN_HORIZON = 200;
// With approvals verified each document costs four reads, not one (the Bundle and APPROVAL_READS),
// so the horizon is recalculated rather than the budget silently exhausted (D9 as amended): fifty
// documents are 200 reads, half the request's budget, as two hundred unverified ones were.
export const FIND_PRODUCT_VERIFIED_SCAN_HORIZON = 50;
// At most this many of one call's documents are being read at once. The request's own bound
// (REQUEST_READ_CONCURRENCY, in the read budget) holds across every call of its batch, so a
// batch of find_product calls still has at most that many reads in flight between them.
export const FIND_PRODUCT_CONCURRENCY = 8;

const ProductIdentifierSchema = ProductSummarySchema.shape.identifiers.element;

function productSummary(loaded: LoadedDocument, index: MappingIndex): ProductSummary | undefined {
  const product = loaded.entries.find(
    ({ resource }) => resource.resourceType === "MedicinalProductDefinition",
  )?.resource;
  if (product === undefined) return undefined;

  const names = z.array(z.object({ productName: z.string() })).safeParse(product.name);
  const productName = names.success ? names.data[0]?.productName : undefined;
  if (productName === undefined) return undefined;

  // Each identifier is held to the contract on its own: one the contract cannot carry is left
  // out of the summary, and the product — still found by its name and its other identifiers —
  // is not.
  const identifiers = z
    .array(z.object({ system: z.string().optional(), value: z.string().optional() }))
    .safeParse(product.identifier);
  const carried = (identifiers.success ? identifiers.data : []).flatMap(({ system, value }) => {
    const identifier = ProductIdentifierSchema.safeParse({ system, value });
    return identifier.success ? [identifier.data] : [];
  });

  const holderReference = z
    .array(z.object({ holder: z.object({ reference: z.string() }).optional() }))
    .safeParse(
      loaded.entries
        .filter(({ resource }) => resource.resourceType === "RegulatedAuthorization")
        .map(({ resource }) => resource),
    );
  const reference = holderReference.success
    ? holderReference.data
        .map(({ holder }) => holder?.reference)
        .find((value) => value !== undefined)
    : undefined;
  const holderName = loaded.entries.find(
    ({ fullUrl, resource }) => fullUrl === reference && resource.resourceType === "Organization",
  )?.resource.name;

  const present = new Set(
    locateSections(loaded.composition.section)
      .map(({ section }) => index.sourceKeyOfSectionId.get(section.id ?? ""))
      .filter((sourceKey): sourceKey is string => sourceKey !== undefined),
  );

  const summary = ProductSummarySchema.safeParse({
    document: loaded.document,
    productName,
    identifiers: carried.slice(0, 20),
    ...(typeof holderName === "string" ? { marketingAuthorisationHolder: holderName } : {}),
    language: loaded.composition.language,
    sections: index.sourceKeys.filter((sourceKey) => present.has(sourceKey)),
  });
  return summary.success ? summary.data : undefined;
}

// A stored product name the normalisation refuses (a section 2 character) cannot match by name;
// it is not an error for the whole search, which would hide every other product from the
// caller. Its identifiers still match.
function nameMatches(needle: string, productName: string): boolean {
  try {
    return normalizeText(productName).toLowerCase().includes(needle);
  } catch {
    return false;
  }
}

function matches(needle: string, summary: ProductSummary): boolean {
  if (nameMatches(needle, summary.productName)) return true;
  return summary.identifiers.some(({ value }) => value.toLowerCase().includes(needle));
}

export async function findProduct(
  context: ToolContext,
  input: FindProductInput,
): Promise<ToolOutcome<FindProductOutput>> {
  let needle: string;
  try {
    needle = normalizeText(input.query).toLowerCase();
  } catch {
    return fail("find_product", "invalid-request");
  }
  if (needle.length === 0) return fail("find_product", "invalid-request");

  const index = indexMapping(context.mapping);
  const limit = input.limit ?? 50;

  // Only the caller's own entitled documents are ever read: there is no store-wide search here,
  // so an unentitled product cannot appear even for an exact query (design note, constraint 4).
  // Phase 1 has no search against the store, so each entitled document is read and inspected;
  // the reads run through a bounded pool, cover at most the first FIND_PRODUCT_SCAN_HORIZON
  // entitled ids, stop being launched once `limit` matches are in hand, and stop when the
  // request's read budget is spent. Each read is a whole document Bundle, read for a few
  // fields, and the budget bounds reads, not bytes; phase 2 replaces the scan with a product
  // index the worker writes (design note, "`find_product`").
  const entitled = context.entitlements?.bundles ?? [];
  const scanned = entitled.slice(
    0,
    context.approvals === undefined
      ? FIND_PRODUCT_SCAN_HORIZON
      : FIND_PRODUCT_VERIFIED_SCAN_HORIZON,
  );

  const found: (ProductSummary | undefined)[] = new Array<ProductSummary | undefined>(
    scanned.length,
  ).fill(undefined);
  let next = 0;
  let matched = 0;
  // Entitled documents this call attempted to read. Everything the call did not attempt was not
  // searched, whatever stopped it.
  let attempted = 0;
  // Attempted documents the call still could not search: a stored document that could not be
  // cited (no version, no Composition first) or whose product could not be summarised under
  // the contract. A document the store does not hold is not one of them: there is nothing in
  // it to find.
  let unsearched = 0;
  // Aborted when any worker's read fails: the others launch no further read, a read still
  // waiting for a place in the request's pool is never made, and one in flight is cancelled —
  // for a call whose answer is already `unavailable`. Its signal joins the request's own.
  const stop = new AbortController();
  const scan: ToolContext = {
    ...context,
    signal:
      context.signal === undefined ? stop.signal : AbortSignal.any([context.signal, stop.signal]),
  };
  // The first read failure, which is what the call reports: the cancellations it causes in the
  // other workers are not.
  let failure: unknown;
  const worker = async (): Promise<void> => {
    try {
      while (!stop.signal.aborted && next < scanned.length && matched < limit) {
        // Checked before the position is claimed, so a document the budget will not pay for is
        // left unattempted and counts as unsearched rather than as a read that failed.
        if (context.readBudget.remaining() === 0) return;
        const position = next;
        next += 1;
        const bundleId = scanned[position];
        if (bundleId === undefined) return;
        attempted += 1;
        const loaded = await loadDocument<FindProductOutput>(scan, "find_product", {
          bundleId,
        });
        if (isOutcome(loaded)) {
          if (loaded.status !== "error" || loaded.auditOutcome !== "document-not-found") {
            unsearched += 1;
          }
          continue;
        }
        // With approvals verified, only a document whose current version carries a valid head
        // approval is listed (D9). One without is not a product this service answers about: it is
        // not counted as unsearched, as a document the store does not hold is not.
        if (scan.approvals !== undefined) {
          const verified = await verifyVersion<FindProductOutput>(
            scan,
            "find_product",
            loaded,
            false,
          );
          if (!isVerified(verified)) {
            if (verified.status !== "error" || verified.auditOutcome !== "not-approved") {
              unsearched += 1;
            }
            continue;
          }
        }
        const summary = productSummary(loaded, index);
        if (summary === undefined) {
          unsearched += 1;
          continue;
        }
        if (matches(needle, summary)) {
          found[position] = summary;
          matched += 1;
        }
      }
    } catch (error) {
      if (!stop.signal.aborted) {
        failure = error;
        stop.abort();
      }
    }
  };
  // Every worker is waited for, not only the first to fail: the call answers once no read of
  // its own is still in flight, so none outlives the answer and drains the request's budget.
  await Promise.all(
    Array.from({ length: Math.min(FIND_PRODUCT_CONCURRENCY, scanned.length) }, () => worker()),
  );
  if (failure !== undefined) {
    throw failure instanceof Error ? failure : new Error("find_product read failed");
  }

  // Matches are reported in entitlement order regardless of the order the reads completed in.
  // The pool can finish more matches than `limit` — up to FIND_PRODUCT_CONCURRENCY - 1 reads
  // are already in flight when the limit is reached — so the slice can drop some.
  const matchedSummaries = found.filter(
    (summary): summary is ProductSummary => summary !== undefined,
  );
  const products = matchedSummaries.slice(0, limit);

  // `truncated` covers both ways this answer can be shorter than what the entitlement holds:
  // entitled documents the call never attempted — because the scan horizon cut the list,
  // because `limit` stopped the scan, or because the request's read budget ran out — or
  // attempted and could not search, and matches the call found and the slice did not return.
  const truncated =
    entitled.length > attempted || unsearched > 0 || matchedSummaries.length > products.length;

  const output = FindProductOutputSchema.safeParse({ products, truncated });
  if (!output.success) return fail("find_product", "unavailable");

  return { status: "ok", value: output.data, resultCount: products.length, truncated };
}
