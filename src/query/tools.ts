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
  type QuoteVerification,
  type SectionContent,
  type VerifyQuoteInput,
} from "../contracts/query-tools.js";
import {
  NORMALIZATION_VERSION,
  isWordCharacter,
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
import { isComposition, type CompositionSection, type FhirComposition } from "../fhir/types.js";
import { sha256Utf8, stableUuid } from "../lib/hash.js";
import type { Entitlements } from "./entitlements.js";
import type { FhirReader } from "./fhir-reader.js";

// The four tools, as pure functions over (entitlements, reader, mapping, input). Nothing here
// logs, reads the environment, or speaks HTTP: src/query/app.ts does that. Every answer is
// built from what the store holds and is validated against the published contract before it is
// returned, so a shape the contract does not allow cannot leave the service.

// --- outcomes ------------------------------------------------------------------------------

type QueryErrorCodeValue = QueryError["error"];
type QueryToolNameValue = QueryError["tool"];

// What a call resolved, for the audit record: the document it named and, once a stored version
// was read, which version. Neither is ever narrative.
type Resolved = { bundleId?: string | undefined; versionId?: string | undefined };

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

export type ReadBudget = {
  // Reserves one store read, or reports false when the request has none left.
  take(): boolean;
  remaining(): number;
};

export function createReadBudget(limit: number = REQUEST_READ_BUDGET): ReadBudget {
  let left = limit;
  return {
    take(): boolean {
      if (left <= 0) return false;
      left -= 1;
      return true;
    },
    remaining(): number {
      return left;
    },
  };
}

export type ToolContext = {
  entitlements: Entitlements | undefined;
  reader: FhirReader;
  mapping: EmaMapping;
  // Shared by every tool call of one HTTP request; every read below takes from it.
  readBudget: ReadBudget;
};

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

function codePointLength(text: string): number {
  return Array.from(text).length;
}

// The code point that ends just before UTF-16 index `index`, or undefined at the start of the
// text. Normalised text never holds a lone surrogate (spec section 2), so a low surrogate here
// is always the second half of a pair.
function codePointBefore(text: string, index: number): string | undefined {
  if (index <= 0) return undefined;
  const unit = text.charCodeAt(index - 1);
  const start = unit >= 0xdc00 && unit <= 0xdfff && index >= 2 ? index - 2 : index - 1;
  return text.slice(start, index);
}

// The code point that starts at UTF-16 index `index`, or undefined at the end of the text.
function codePointAtIndex(text: string, index: number): string | undefined {
  const codePoint = text.codePointAt(index);
  return codePoint === undefined ? undefined : String.fromCodePoint(codePoint);
}

// --- the quote-edge rule ---------------------------------------------------------------------

// A quote is checked against the stored text and nothing checks it afterwards, so where it may
// begin and end is stricter here than the publishing gate's span-edge rule
// (docs/fidelity-normalization.md section 6). The gate only has to stop a span cutting a word,
// because it then requires the whole section to equal the approved narrative; a quote has no
// such second check, and a quote that stops at punctuation can still change what the label
// says — "Take 2" out of "Take 2.5 mg", "see section 4" out of "(see section 4.4)", "20 °C"
// out of "-20 °C", "diabetic patients" out of "non-diabetic patients". The rule, for quotes
// only (the gate is unchanged):
//
// - Left edge. The code point before the quote is absent or a space, or it is a run of
//   opening punctuation (QUOTE_OPENERS) that is itself preceded by a space or the start of the
//   text. Anything else is a cut: a letter or digit, a sign or comparator ("-20", "<10",
//   "≥10"), an apostrophe joined to a word ("Don't"), a slash, a decimal point.
// - Right edge. The code point after the quote is absent or a space, or it is a run of closing
//   punctuation (QUOTE_CLOSERS) that is itself followed by a space or the end of the text. So a
//   quote may end before a sentence's full stop, a comma, a colon or a closing parenthesis
//   followed by a space, and not before "." or "," or "/" followed by a digit or a letter.
// - Across a space. A quote that begins with a digit after a space preceded by a digit, or ends
//   with a digit before a space followed by a digit, has cut a space-grouped number ("1 000"
//   out of "1 000 000 IU"); a quote preceded by a comparator or sign and a space
//   (SPACED_SIGNS: "≥ 30 ml/min") has lost it. Both are cuts.
//
// It is never looser than the gate: a word character on either side is a cut before any of the
// above is consulted, under the fidelity library's own isWordCharacter. It is not a grammar,
// and it does not make a quote complete: a quote may still stop before any following word, so
// "Take 5" matches "Take 5 mg daily" — a match proves the words a quote contains, not that
// nothing follows them. Normalised text holds no whitespace but U+0020 (spec section 3) and no
// U+00AD (step 1), so neither needs a case here.

const QUOTE_OPENERS = new Set(["(", "[", "{", '"', "'", "‘", "“", "„", "«", "‹", "¿", "¡"]);

const QUOTE_CLOSERS = new Set([
  ".",
  ",",
  ";",
  ":",
  "!",
  "?",
  ")",
  "]",
  "}",
  '"',
  "'",
  "’",
  "”",
  "»",
  "›",
  "…",
]);

// Signs and comparators that still bind a number across a space. Hyphens and dashes are not
// here: set off by spaces they are far more often separators than signs.
const SPACED_SIGNS = new Set([
  "<",
  ">",
  "~",
  "±",
  "−",
  "∓",
  "∼",
  "≈",
  "≤",
  "≥",
  "≦",
  "≧",
  "⩽",
  "⩾",
]);

const DECIMAL_DIGIT = /^\p{Nd}$/u;

function isDigit(character: string | undefined): boolean {
  return character !== undefined && DECIMAL_DIGIT.test(character);
}

function edgeBefore(text: string, start: number, quote: string): boolean {
  let before = codePointBefore(text, start);
  if (before === undefined) return true;
  if (isWordCharacter(before)) return false;
  if (before === " ") {
    const beyond = codePointBefore(text, start - 1);
    if (beyond !== undefined && SPACED_SIGNS.has(beyond)) return false;
    return !(isDigit(beyond) && isDigit(codePointAtIndex(quote, 0)));
  }
  let index = start;
  while (before !== undefined && QUOTE_OPENERS.has(before)) {
    index -= before.length;
    before = codePointBefore(text, index);
  }
  return index < start && (before === undefined || before === " ");
}

function edgeAfter(text: string, end: number, quote: string): boolean {
  let after = codePointAtIndex(text, end);
  if (after === undefined) return true;
  if (isWordCharacter(after)) return false;
  if (after === " ") {
    const beyond = codePointAtIndex(text, end + 1);
    return !(isDigit(beyond) && isDigit(codePointBefore(quote, quote.length)));
  }
  let index = end;
  while (after !== undefined && QUOTE_CLOSERS.has(after)) {
    index += after.length;
    after = codePointAtIndex(text, index);
  }
  return index > end && (after === undefined || after === " ");
}

// Where `quote` occurs in `text` with both edges on a boundary under the quote-edge rule, as a
// UTF-16 index, or -1. An occurrence that is cut does not end the search: a later occurrence
// whose edges hold still matches.
function findQuoteOccurrence(text: string, quote: string): number {
  for (let found = text.indexOf(quote); found >= 0; found = text.indexOf(quote, found + 1)) {
    if (edgeBefore(text, found, quote) && edgeAfter(text, found + quote.length, quote)) {
      return found;
    }
  }
  return -1;
}

// The decision `verify_quote` makes for one section: where the normalised `quote` first occurs
// in the section's normalised `text` under the quote-edge rule, in code points (ADR 0002), or
// undefined. Exported so that scripts/contracts/export-quote-edge-cases.ts can publish this
// rule's answers for the agent's test double to be held to (test/fixtures/contracts/
// quote-edge-cases.json); `verifyQuote` below calls nothing else to decide.
export function locateQuote(
  text: string,
  quote: string,
): { startOffset: number; endOffset: number } | undefined {
  const found = findQuoteOccurrence(text, quote);
  if (found < 0) return undefined;
  const startOffset = codePointLength(text.slice(0, found));
  return { startOffset, endOffset: startOffset + codePointLength(quote) };
}

// --- document loading --------------------------------------------------------------------------

type LoadedDocument = {
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

  const bundle =
    selector.versionId === undefined
      ? await context.reader.readBundle(selector.bundleId)
      : await context.reader.readBundleVersion(selector.bundleId, selector.versionId);

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
  const current = await context.reader.readBundle(selector.bundleId);
  return current?.meta?.versionId === loaded.document.versionId ? "current" : "superseded";
}

// --- get_section --------------------------------------------------------------------------------

export async function getSection(
  context: ToolContext,
  input: GetSectionInput,
): Promise<ToolOutcome<SectionContent>> {
  const loaded = await loadDocument<SectionContent>(context, "get_section", input);
  if (isOutcome(loaded)) return loaded;
  const at = resolved(loaded);

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
  const standing = await versionStanding(context, input, loaded);
  if (standing === "out-of-budget") return fail("get_section", "unavailable", at);
  let provenanceResourceId: string | undefined;
  if (standing === "current") {
    if (!context.readBudget.take()) return fail("get_section", "unavailable", at);
    const provenance = await context.reader.findProvenanceForBundle(input.bundleId);
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
  const at = resolved(loaded);

  // A version that is not the current one cannot be tied to its own approval yet, and the
  // newest approval is not it: the answer is `unavailable` — the closed code this tool already
  // gives for an approval it cannot state in full — and the Provenance search is not made.
  const standing = await versionStanding(context, input, loaded);
  if (standing !== "current") return fail("get_provenance", "unavailable", at);

  // The provenance lookup is one more store read and takes from the request's budget.
  if (!context.readBudget.take()) return fail("get_provenance", "unavailable", at);
  const resource = await context.reader.findProvenanceForBundle(input.bundleId);
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
  const approverId =
    attester === undefined ? undefined : agentIdentifier(attester, APPROVER_IDENTIFIER_SYSTEM);
  const role = attester === undefined ? undefined : approverRole(attester);
  if (extractor === undefined || approverId === undefined || role === undefined) {
    return fail("get_provenance", "unavailable", at);
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
    approvedContentSha256: persisted.extension.find(
      ({ url }) => url === APPROVAL_CONTENT_EXTENSION_URL,
    )?.valueString,
    extractor,
    ...(modelId === undefined ? {} : { model: { id: modelId } }),
    approver: { id: approverId, role },
    ...(section === undefined ? {} : { section }),
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
  if (normalizedQuote.length === 0) {
    return fail("verify_quote", "invalid-request", { bundleId: input.bundleId });
  }

  const loaded = await loadDocument<QuoteVerification>(context, "verify_quote", input);
  if (isOutcome(loaded)) return loaded;
  const at = resolved(loaded);

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
  let match: QuoteVerification["match"];
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
// At most this many store reads are in flight at once.
export const FIND_PRODUCT_CONCURRENCY = 8;

function productSummary(loaded: LoadedDocument, index: MappingIndex): ProductSummary | undefined {
  const product = loaded.entries.find(
    ({ resource }) => resource.resourceType === "MedicinalProductDefinition",
  )?.resource;
  if (product === undefined) return undefined;

  const names = z.array(z.object({ productName: z.string() })).safeParse(product.name);
  const productName = names.success ? names.data[0]?.productName : undefined;
  if (productName === undefined) return undefined;

  const identifiers = z
    .array(z.object({ system: z.string().optional(), value: z.string().optional() }))
    .safeParse(product.identifier);

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
    identifiers: (identifiers.success ? identifiers.data : [])
      .filter(
        (identifier): identifier is { system: string; value: string } =>
          identifier.system !== undefined && identifier.value !== undefined,
      )
      .slice(0, 20),
    ...(typeof holderName === "string" ? { marketingAuthorisationHolder: holderName } : {}),
    language: loaded.composition.language,
    sections: index.sourceKeys.filter((sourceKey) => present.has(sourceKey)),
  });
  return summary.success ? summary.data : undefined;
}

function matches(needle: string, summary: ProductSummary): boolean {
  if (normalizeText(summary.productName).toLowerCase().includes(needle)) return true;
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
  // request's read budget is spent.
  const entitled = context.entitlements?.bundles ?? [];
  const scanned = entitled.slice(0, FIND_PRODUCT_SCAN_HORIZON);

  const found: (ProductSummary | undefined)[] = new Array<ProductSummary | undefined>(
    scanned.length,
  ).fill(undefined);
  let next = 0;
  let matched = 0;
  // Entitled documents this call attempted to read. Everything the call did not attempt was not
  // searched, whatever stopped it.
  let attempted = 0;
  const worker = async (): Promise<void> => {
    while (next < scanned.length && matched < limit) {
      // Checked before the position is claimed, so a document the budget will not pay for is
      // left unattempted and counts as unsearched rather than as a read that failed.
      if (context.readBudget.remaining() === 0) return;
      const position = next;
      next += 1;
      const bundleId = scanned[position];
      if (bundleId === undefined) return;
      attempted += 1;
      const loaded = await loadDocument<FindProductOutput>(context, "find_product", { bundleId });
      if (isOutcome(loaded)) continue;
      const summary = productSummary(loaded, index);
      if (summary !== undefined && matches(needle, summary)) {
        found[position] = summary;
        matched += 1;
      }
    }
  };
  await Promise.all(
    Array.from({ length: Math.min(FIND_PRODUCT_CONCURRENCY, scanned.length) }, () => worker()),
  );

  // Matches are reported in entitlement order regardless of the order the reads completed in.
  // The pool can finish more matches than `limit` — up to FIND_PRODUCT_CONCURRENCY - 1 reads
  // are already in flight when the limit is reached — so the slice can drop some.
  const matchedSummaries = found.filter(
    (summary): summary is ProductSummary => summary !== undefined,
  );
  const products = matchedSummaries.slice(0, limit);

  // `truncated` covers both ways this answer can be shorter than what the entitlement holds:
  // entitled documents the call never attempted — because the scan horizon cut the list,
  // because `limit` stopped the scan, or because the request's read budget ran out — and
  // matches the call found and the slice did not return.
  const truncated = entitled.length > attempted || matchedSummaries.length > products.length;

  const output = FindProductOutputSchema.safeParse({ products, truncated });
  if (!output.success) return fail("find_product", "unavailable");

  return { status: "ok", value: output.data, resultCount: products.length, truncated };
}
