import {
  CANONICAL_SUBMISSION_VERSION,
  CERTIFIED_WORD_IMPORTER_NAME,
  CERTIFIED_WORD_PREFIX,
  CERTIFIED_WORD_SYNTHETIC_BLOCK,
  approvedContent,
  certifiedWordExtractorRecord,
  type CanonicalBundle,
  type CanonicalSubmission,
  type CertifiedWordSourceDocument,
  type SectionProvenance,
  type StructuringDecision,
} from "../contracts/index.js";
import {
  NORMALIZATION_VERSION,
  NormalizationError,
  XhtmlError,
  collectNarrativeSections,
  hasDrawnText,
  hasInvisibleFormatting,
  normalizeText,
  verifyNarrativeFidelity,
  xhtmlToText,
  type FidelityReport,
  type SourceDocumentText,
} from "../fidelity/index.js";
import { EMA_MAPPING_ID, type EmaMapping, type SectionRule } from "../fhir/mapping.js";
import { EU_AUTHORISATION_NUMBER_SYSTEM, EU_PRODUCT_NUMBER_SYSTEM } from "../fhir/standards.js";
import { sha256, sha256Utf8, stableUuid } from "../lib/hash.js";
import { AuthorityBytesError, readAuthorityJson } from "../authority/json.js";
import {
  CertifiedWordRequestSchema,
  hasMarker,
  RecomputeRefusalSchema,
  RecomputeResultSchema,
  type CertifiedWordRequest,
  type RecomputeResult,
  type RecomputedSection,
} from "./shape.js";

// The certified Word importer (docs/design/certified-word-import.md, D1): a pure, deterministic
// function of what `python -m zone_a.recompute` wrote for a Word label and what a person confirmed
// for it, to the canonical submission. Narratives, pages and titles come only from the recompute;
// nothing is added, reordered or reworded. The producer runs it on the recompute it ran; Zone B's
// gate runs the recompute again on the uploaded bytes and this again on its result, and
// requires the same submission (D2). It reads no clock, no locale and no network.

// The importer's version: with the recompute's versions it names the extractor (ADR 0006 decision
// 6), and it is locked to the hash of this directory's code and of its golden vectors
// (importer.lock.json, `npm run certified-word:lock`), so a change of what it makes changes it.
export const IMPORTER_VERSION = "1.1.0";
export const CERTIFIED_WORD_IMPORTER = `certified-word-import/${IMPORTER_VERSION}`;

export const CERTIFIED_WORD_IDENTIFIER_SYSTEM = "https://khs.dev/fhir/identifier/certified-word";
// Our own ids for the product and its holder, a person's confirmation (ADR 0006 decision 5).
export const CANONICAL_PRODUCT_SYSTEM = "https://khs.dev/fhir/identifier/canonical-product";
export const CANONICAL_ORGANIZATION_SYSTEM =
  "https://khs.dev/fhir/identifier/canonical-organization";
const GLOBAL_EPI_PROFILE_BASE =
  "http://hl7.org/fhir/uv/emedicinal-product-info/StructureDefinition/";
const DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

// The canonical document type of each document the importer carries. A package leaflet is not
// yet: Zone B's crosswalk and preflight are the SmPC's (cap-smpc-en).
const DOCUMENT_TYPES: Partial<Record<RecomputeResult["document"], object>> = {
  smpc: {
    system: "https://khs.dev/fhir/CodeSystem/document-type",
    code: "smpc",
    display: "Summary of Product Characteristics",
  },
};

// The stage that refused, and a closed reason; never narrative.
export class CertifiedWordRefusedError extends Error {
  public constructor(
    public readonly stage: CertifiedWordStage,
    public readonly reason: string,
  ) {
    super(`Certified Word import refused at ${stage}: ${reason}`);
    this.name = "CertifiedWordRefusedError";
  }
}

// The order the importer checks in; a refusal names the first stage that fails.
export type CertifiedWordStage =
  | "request"
  | "bytes"
  | "recompute"
  | "shape"
  | "binding"
  | "tree"
  | "titles"
  | "narrative"
  | "product"
  | "record";

// The fields of a submission a run chooses: identifiers, times and storage locations only. The
// gate copies them from the submission it recomputes.
export type CertifiedWordRun = {
  submissionId: string;
  createdAt: string;
  extractionRunId: string;
  serviceVersion: string;
  sourceTextUri: string;
  fidelityReportUri: string;
};

export type CertifiedWordResult = {
  submission: CanonicalSubmission;
  fidelityReport: FidelityReport;
  sourceText: SourceDocumentText;
};

function refuse(stage: CertifiedWordStage, reason: string): never {
  throw new CertifiedWordRefusedError(stage, reason);
}

function codePoints(text: string): number {
  return Array.from(text).length;
}

// ADR 0002 invariant 7's extractor token for this kind: `certified-word/` and the SHA-256 of the
// canonical JSON of this importer and every version the recompute names (ADR 0006 decision 6).
export function certifiedWordExtractor(versions: RecomputeResult["versions"]): string {
  const record = certifiedWordExtractorRecord({
    importer: CERTIFIED_WORD_IMPORTER,
    recompute: { versions },
  });
  return `${CERTIFIED_WORD_IMPORTER_NAME}/${sha256(record)}`;
}

function readRequest(request: unknown): CertifiedWordRequest {
  const parsed = CertifiedWordRequestSchema.safeParse(request);
  if (!parsed.success) refuse("request", "request-shape");
  const { documentId, product } = parsed.data;
  // A synthetic label's ids are all in the reserved block, and a real one's none (ADR 0002
  // invariant 10).
  const ids = [documentId, product.id, product.holder.id];
  const synthetic = ids.filter((id) => id.startsWith(CERTIFIED_WORD_SYNTHETIC_BLOCK)).length;
  if (synthetic !== 0 && synthetic !== ids.length) refuse("request", "synthetic-and-real-ids");
  const numbers = product.euAuthorisationNumbers;
  if (new Set(numbers).size !== numbers.length) refuse("request", "eu-number-repeated");
  return parsed.data;
}

function readResult(bytes: Uint8Array): RecomputeResult {
  let json: unknown;
  try {
    json = readAuthorityJson(bytes);
  } catch (error) {
    if (error instanceof AuthorityBytesError) refuse("bytes", error.reason);
    throw error;
  }
  if (RecomputeRefusalSchema.safeParse(json).success) refuse("recompute", "recompute-refused");
  const parsed = RecomputeResultSchema.safeParse(json);
  if (!parsed.success) refuse("shape", "result-shape");
  return parsed.data;
}

// The result is the one the request names: the same document, view, part, headings and versions,
// made with the mapping this build carries (D1).
function checkBinding(
  result: RecomputeResult,
  request: CertifiedWordRequest,
  mapping: EmaMapping,
): void {
  const asked = request.recompute;
  if (sha256(result.versions) !== sha256(asked.versions)) refuse("binding", "other-versions");
  const { structure, versions } = result;
  if (
    structure.structurer !== versions.structurer ||
    structure.registryVersion !== versions.registryVersion ||
    structure.mappingVersion !== versions.mappingVersion
  ) {
    refuse("binding", "structure-of-other-versions");
  }
  if (result.document !== asked.document) refuse("binding", "other-document");
  if (result.view !== asked.view) refuse("binding", "other-view");
  if (result.part !== asked.part) refuse("binding", "other-part");
  // The headings the structure says a person assigned are exactly the request's, both ways: an
  // assignment the request names that the result did not apply, and one the result applied that
  // the request does not name, each refuse.
  const assigned = structure.sections.filter(({ status }) => status === "assigned");
  const asks = Object.entries(asked.assignments);
  if (
    assigned.length !== asks.length ||
    asks.some(
      ([key, paragraph]) =>
        !assigned.some((section) => section.key === key && section.heading === paragraph) ||
        result.sections.find((section) => section.key === key)?.heading !== paragraph,
    )
  ) {
    refuse("binding", "assignments-differ");
  }
  if (DOCUMENT_TYPES[result.document] === undefined) refuse("binding", "document-not-carried");
  if (mapping.root.sourceKey !== result.document) refuse("binding", "mapping-of-another-document");
  if (versions.mappingVersion !== mapping.mappingVersion)
    refuse("binding", "other-mapping-version");
}

type Placed = { section: RecomputedSection; rule: SectionRule };

// The sections are the mapping's tree in its order, a parent before its children: each the rule's
// key, under its rule's parent, with the rule's EMA code; a rule that is not required may have no
// section, and every section has a rule.
function placeSections(sections: RecomputedSection[], mapping: EmaMapping): Placed[] {
  const placed: Placed[] = [];
  const walk = (rules: SectionRule[], parent: string | null): void => {
    for (const rule of rules) {
      const section = sections[placed.length];
      if (section?.key !== rule.sourceKey) {
        if (rule.required) refuse("tree", "section-tree-differs");
        continue;
      }
      if (section.parent !== parent) refuse("tree", "section-tree-differs");
      if (section.code !== rule.targetCode) refuse("tree", "section-code-differs");
      placed.push({ section, rule });
      walk(rule.children ?? [], rule.sourceKey);
    }
  };
  walk([mapping.root], null);
  if (placed.length !== sections.length) refuse("tree", "section-tree-differs");
  return placed;
}

// A title is the label's heading line as the recompute gives it (D6): one line of plain text that
// draws something, carried as written, an assigned heading included.
function checkTitles(placed: Placed[]): void {
  for (const { section } of placed) {
    if (
      section.title.trim().length === 0 ||
      /[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]/u.test(section.title)
    ) {
      refuse("titles", "title-not-one-line");
    }
  }
}

type Page = Placed & { page: number; normalized: string | undefined };

// Each section's narrative reads as its page (the builder checked it with Zone A's scanner; this is
// Zone B's), and a section draws nothing exactly where its page is empty. A section that draws
// nothing refuses where the mapping needs its narrative, and a leaf anywhere (FHIR cmp-1).
function checkNarratives(placed: Placed[]): Page[] {
  return placed.map((entry, index) => {
    const { narrative, page } = entry.section;
    const leaf = (entry.rule.children ?? []).length === 0;
    if (narrative === null) {
      if (page !== "") refuse("narrative", "page-without-narrative");
      if (leaf || entry.rule.narrative === "required") refuse("narrative", "section-draws-nothing");
      return { ...entry, page: index + 1, normalized: undefined };
    }
    let text: string;
    try {
      text = xhtmlToText(narrative);
    } catch (error) {
      if (error instanceof XhtmlError) refuse("narrative", `scanner-${error.code}`);
      throw error;
    }
    // Normalisation would remove such a character from either, so the two could match while one
    // holds what the other does not (fidelity section 7).
    if (hasInvisibleFormatting(text) || hasInvisibleFormatting(page)) {
      refuse("narrative", "invisible-character");
    }
    let normalized: string;
    try {
      normalized = normalizeText(text);
      if (normalizeText(page) !== normalized) refuse("narrative", "narrative-differs-from-page");
    } catch (error) {
      if (error instanceof NormalizationError) refuse("narrative", "not-normalisable");
      throw error;
    }
    if (!hasDrawnText(normalized)) refuse("narrative", "narrative-draws-nothing");
    return { ...entry, page: index + 1, normalized };
  });
}

// An EU authorisation number standing alone on section 8's page: after a line's start, a space, a
// tab or one of ",;:(", and before its end, a space, a tab, one of ",;:()", or a full stop that ends
// the line or comes before whitespace (`zone_a.product`'s strict form, with the page's own
// separators; a run of presentations is not one, nor "001.3").
const EU_NUMBER =
  /(?<=^|[ \t,;:(])EU\/1\/[0-9]{2}\/[0-9]{3,4}\/[0-9]{3}(?=$|[ \t,;:()]|\.(?:$|\s))/gmu;
// Anything that could be the start of one, in any case and with any space inside "EU/": each must
// be the start of a number read, or the section states a number the strict form does not read.
const EU_START = /E\s*U\s*\//giu;

// The first line of a section's page (a page begins with a line feed), or "" for an empty page.
function firstLine(page: string): string {
  return page.split("\n").find((line) => line.length > 0) ?? "";
}

// The names section 1's first line allows: the whole line, or the line up to its strength, the
// first whitespace-separated token that begins with a digit ("BRUKINSA" of "BRUKINSA 80 mg hard
// capsules"), without the whitespace before it.
function namesOf(title: string): string[] {
  const strength = [...title.matchAll(/\S+/gu)].find(([token]) => /^\p{Nd}/u.test(token));
  const before = strength === undefined ? "" : title.slice(0, strength.index).trimEnd();
  return before.length === 0 ? [title] : [title, before];
}

// A slash, or a character drawn as one (division slash, fraction slash, fullwidth solidus).
const SLASH = /[/\u2215\u2044\uff0f]/u;

// The product a person confirmed is this label's (ADR 0006 decision 5):
// - its name is section 1's whole first line, or that line up to its strength (namesOf), and does
//   not end in punctuation;
// - its holder is section 7's first line, exactly;
// - neither first line is a line only the page writes (a table's or a picture's);
// - its EU authorisation numbers are exactly those section 8 states, each standing alone, with no
//   other "EU/" there in any case or spacing, and no line of section 8 holds a slash, or a
//   character drawn as one, unless it begins with a number read.
function checkProduct(placed: Placed[], product: CertifiedWordRequest["product"]): void {
  const page = (key: string): string =>
    placed.find(({ section }) => section.key === key)?.section.page ?? "";
  const { name } = product;
  const title = firstLine(page("smpc.1"));
  if (hasMarker(title)) refuse("product", "section-1-begins-with-no-text");
  if (!namesOf(title).includes(name) || /\p{P}$/u.test(name)) {
    refuse("product", "name-not-in-section-1");
  }
  const holder = firstLine(page("smpc.7"));
  if (hasMarker(holder)) refuse("product", "section-7-begins-with-no-text");
  if (holder !== product.holder.name) refuse("product", "holder-not-in-section-7");
  const numbers = page("smpc.8");
  const found = [...numbers.matchAll(EU_NUMBER)];
  if (found.length !== [...numbers.matchAll(EU_START)].length) {
    refuse("product", "eu-number-unread");
  }
  // A number written with a look-alike letter or slash is in no strict form: every line that holds
  // a slash must begin with a number read.
  const starts = new Set(found.map(({ index }) => index));
  let offset = 0;
  for (const line of numbers.split("\n")) {
    if (SLASH.test(line) && !starts.has(offset)) refuse("product", "eu-number-unread");
    offset += line.length + 1;
  }
  const stated = new Set(found.map(([number]) => number));
  const confirmed = new Set(product.euAuthorisationNumbers);
  if (stated.size !== confirmed.size || [...stated].some((number) => !confirmed.has(number))) {
    refuse("product", "eu-numbers-differ");
  }
}

function profile(resourceType: string): { profile: string[] } {
  return { profile: [`${GLOBAL_EPI_PROFILE_BASE}${resourceType}-uv-epi`] };
}

type Decision = StructuringDecision;

function decision(
  target: string,
  action: Decision["action"],
  extra: Omit<Decision, "target" | "action"> = {},
): Decision {
  return { target, action, ...extra };
}

const rule = (id: string): { ruleId: string } => ({ ruleId: `certified-word:${id}` });

export function importCertifiedWord(
  recomputed: Uint8Array,
  requested: unknown,
  mapping: EmaMapping,
  run: CertifiedWordRun,
): CertifiedWordResult {
  const request = readRequest(requested);
  const result = readResult(recomputed);
  checkBinding(result, request, mapping);
  const placed = placeSections(result.sections, mapping);
  checkTitles(placed);
  const pages = checkNarratives(placed);
  const { product } = request;
  checkProduct(placed, product);

  const identifierValue = `${CERTIFIED_WORD_PREFIX}${request.documentId}`;
  const id = (resourceType: string, at = ""): string =>
    stableUuid(`certified-word:${resourceType}`, `${identifierValue}${at}`);
  const url = (resourceType: string, at = ""): string => `urn:uuid:${id(resourceType, at)}`;
  const authorisations = [...product.euAuthorisationNumbers].sort();
  const productNumbers = [...new Set(authorisations.map((number) => number.slice(0, -4)))];

  const decisions: Decision[] = [
    decision("Bundle.identifier", "defaulted-by-rule", rule("identifier-from-document-id")),
    decision("Bundle.language", "defaulted-by-rule", rule("language-en")),
    decision("Bundle.timestamp", "defaulted-by-rule", rule("time-of-assembly")),
    decision("Bundle.type", "defaulted-by-rule", rule("document-bundle")),
    decision("Composition.identifier", "defaulted-by-rule", rule("identifier-from-document-id")),
    decision("Composition.language", "defaulted-by-rule", rule("language-en")),
    decision("Composition.status", "defaulted-by-rule", rule("status-from-attestation")),
    decision("Composition.type", "defaulted-by-rule", rule("type-from-document")),
    decision("Composition.title", "extracted-verbatim", { sourceKey: "smpc.1" }),
    decision("Composition.date", "defaulted-by-rule", rule("time-of-assembly")),
    decision("Composition.subject", "defaulted-by-rule", rule("subject-is-the-product")),
    decision("Composition.author", "defaulted-by-rule", rule("author-is-the-holder")),
    decision("MedicinalProductDefinition.identifier[0]", "defaulted-by-rule", {
      ...rule("canonical-product-confirmed"),
    }),
    ...productNumbers.map((_, position) =>
      decision(`MedicinalProductDefinition.identifier[${position + 1}]`, "extracted-verbatim", {
        sourceKey: "smpc.8",
      }),
    ),
    decision("MedicinalProductDefinition.name", "extracted-verbatim", { sourceKey: "smpc.1" }),
    decision("Organization.identifier", "defaulted-by-rule", rule("canonical-holder-confirmed")),
    decision("Organization.name", "extracted-verbatim", { sourceKey: "smpc.7" }),
  ];

  type Section = {
    id: string;
    title: string;
    code: { coding: { system: string; code: string }[] };
    text?: { status: "additional"; div: string };
    section?: Section[];
  };
  // The tree from the parent keys, in the recompute's order (checked to be the mapping's above).
  const byKey = new Map<string, Section>();
  const roots: Section[] = [];
  const paths = new Map<string, string>();
  for (const { section, rule: sectionRule } of pages) {
    const built: Section = {
      id: stableUuid("certified-word:section", `${identifierValue}:${section.key}`),
      title: section.title,
      code: { coding: [{ system: mapping.sourceCodeSystem, code: section.key }] },
      ...(section.narrative === null
        ? {}
        : { text: { status: "additional" as const, div: section.narrative } }),
    };
    byKey.set(section.key, built);
    const parent = section.parent === null ? undefined : byKey.get(section.parent);
    const siblings = parent === undefined ? roots : (parent.section ??= []);
    const base = section.parent === null ? "Composition" : (paths.get(section.parent) ?? "");
    const path = `${base}.section[${siblings.length}]`;
    paths.set(section.key, path);
    siblings.push(built);
    decisions.push(
      decision(`${path}.code`, "code-mapped", {
        sourceKey: section.key,
        terminologyRef: {
          system: mapping.sourceCodeSystem,
          code: section.key,
          version: mapping.mappingVersion,
          lookupId: `mapping:${sectionRule.targetCode}`,
        },
      }),
      decision(`${path}.title`, "extracted-verbatim", { sourceKey: section.key }),
      decision(`${path}.id`, "defaulted-by-rule", {
        sourceKey: section.key,
        ...rule("section-id-from-identifier"),
      }),
    );
    if (section.narrative !== null) {
      decisions.push(
        decision(`${path}.text`, "extracted-verbatim", { sourceKey: section.key }),
        decision(`${path}.text.status`, "defaulted-by-rule", {
          sourceKey: section.key,
          ...rule("narrative-status-additional"),
        }),
      );
    }
  }

  const compositionResource = {
    resourceType: "Composition",
    id: id("Composition"),
    meta: profile("Composition"),
    language: "en",
    identifier: [
      { system: CERTIFIED_WORD_IDENTIFIER_SYSTEM, value: `${identifierValue}:composition` },
    ],
    status: "final",
    type: { coding: [DOCUMENT_TYPES[result.document]] },
    subject: [{ reference: url("MedicinalProductDefinition") }],
    date: run.createdAt,
    author: [{ reference: url("Organization") }],
    title: product.name,
    section: roots,
  };
  const bundle = {
    resourceType: "Bundle" as const,
    id: id("Bundle"),
    meta: profile("Bundle"),
    language: "en",
    identifier: { system: CERTIFIED_WORD_IDENTIFIER_SYSTEM, value: identifierValue },
    type: "document" as const,
    timestamp: run.createdAt,
    entry: [
      { fullUrl: url("Composition"), resource: compositionResource },
      {
        fullUrl: url("MedicinalProductDefinition"),
        resource: {
          resourceType: "MedicinalProductDefinition",
          id: id("MedicinalProductDefinition"),
          meta: profile("MedicinalProductDefinition"),
          identifier: [
            { system: CANONICAL_PRODUCT_SYSTEM, value: product.id },
            ...productNumbers.map((value) => ({ system: EU_PRODUCT_NUMBER_SYSTEM, value })),
          ],
          name: [{ productName: product.name }],
        },
      },
      {
        fullUrl: url("Organization"),
        resource: {
          resourceType: "Organization",
          id: id("Organization"),
          meta: profile("Organization"),
          identifier: [{ system: CANONICAL_ORGANIZATION_SYSTEM, value: product.holder.id }],
          name: product.holder.name,
        },
      },
      // One per EU authorisation number (docs/design/version-identity.md).
      ...authorisations.map((number) => ({
        fullUrl: url("RegulatedAuthorization", `:${number}`),
        resource: {
          resourceType: "RegulatedAuthorization",
          id: id("RegulatedAuthorization", `:${number}`),
          meta: profile("RegulatedAuthorization"),
          identifier: [{ system: EU_AUTHORISATION_NUMBER_SYSTEM, value: number }],
          subject: [{ reference: url("MedicinalProductDefinition") }],
          holder: { reference: url("Organization") },
        },
      })),
    ],
  };
  // Each entry's fullUrl, id and profile are the importer's, by rule; an authorisation's number is
  // section 8's, and its links are to the product and the holder.
  bundle.entry.forEach(({ resource }, position) => {
    const at = `Bundle.entry[${position}]`;
    decisions.push(
      decision(`${at}.fullUrl`, "defaulted-by-rule", rule("id-from-identifier")),
      decision(`${at}.resource.id`, "defaulted-by-rule", rule("id-from-identifier")),
      decision(`${at}.resource.meta.profile`, "defaulted-by-rule", rule("epi-profile")),
    );
    if (resource.resourceType === "RegulatedAuthorization") {
      decisions.push(
        decision(`${at}.resource.identifier`, "extracted-verbatim", { sourceKey: "smpc.8" }),
        decision(`${at}.resource.subject`, "defaulted-by-rule", rule("subject-is-the-product")),
        decision(`${at}.resource.holder`, "defaulted-by-rule", rule("holder-is-the-holder")),
      );
    }
  });
  decisions.push(
    decision("Bundle.id", "defaulted-by-rule", rule("id-from-identifier")),
    decision("Bundle.meta.profile", "defaulted-by-rule", rule("epi-profile")),
  );
  const canonicalBundle = bundle as unknown as CanonicalBundle;

  const extractor = certifiedWordExtractor(result.versions);
  const sourceText: SourceDocumentText = {
    extractorVersion: extractor,
    pages: pages.map(({ page, section }) => ({
      page,
      text: section.page,
      bodyStart: 0,
      bodyEnd: codePoints(section.page),
    })),
  };
  const pageOf = new Map(pages.map((page) => [page.section.key, page]));
  const narratives = collectNarrativeSections(compositionResource, mapping.sourceCodeSystem);
  const sectionProvenance: SectionProvenance[] = narratives.map((narrative) => {
    const page = pageOf.get(narrative.sourceKey);
    if (page?.normalized === undefined) refuse("record", "narrative-without-a-page");
    return {
      sourceKey: narrative.sourceKey,
      spans: [
        {
          page: page.page,
          startOffset: 0,
          endOffset: codePoints(page.section.page),
          textSha256: sha256Utf8(page.section.page),
        },
      ],
      narrativeDivSha256: sha256Utf8(narrative.div),
      normalizedTextSha256: sha256Utf8(page.normalized),
    };
  });
  const fidelityReport = verifyNarrativeFidelity({
    normalizationVersion: NORMALIZATION_VERSION,
    source: sourceText,
    sections: narratives,
    provenance: sectionProvenance,
  });
  // Both hold by construction (each page is its section's, and a section without a narrative has
  // the empty page); they stay as a defence, and the gate checks them again.
  if (fidelityReport.status !== "passed") refuse("record", "fidelity-not-passed");
  if (fidelityReport.coverage.uncoveredGaps !== 0) refuse("record", "uncovered-page-not-blank");

  const source: CertifiedWordSourceDocument = {
    kind: "certified-word",
    mediaType: DOCX,
    document: {
      sha256: result.source.sha256,
      byteLength: result.source.bytes,
      filename: request.upload.filename,
      storageUri: request.upload.storageUri,
    },
    recompute: request.recompute,
    importer: CERTIFIED_WORD_IMPORTER,
    changes: result.changes,
    sectionPages: pages.map(({ page, section }) => ({
      page,
      key: section.key,
      code: section.code,
    })),
    extractedText: {
      uri: run.sourceTextUri,
      sha256: sha256(sourceText),
      extractorVersion: extractor,
    },
  };
  const provenance: CanonicalSubmission["provenance"] = {
    sourceDocument: source,
    extraction: {
      extractionRunId: run.extractionRunId,
      serviceVersion: run.serviceVersion,
      parser: {
        name: CERTIFIED_WORD_IMPORTER_NAME,
        version: extractor.slice(`${CERTIFIED_WORD_IMPORTER_NAME}/`.length),
      },
      terminologyService: {
        name: EMA_MAPPING_ID,
        version: mapping.mappingVersion,
        snapshotSha256: sha256(mapping),
      },
    },
    sections: sectionProvenance,
    decisions,
    fidelity: {
      normalizationVersion: NORMALIZATION_VERSION,
      status: "passed",
      sectionsChecked: fidelityReport.summary.total,
      sectionsMatched: fidelityReport.summary.verified,
      narrativeBindingSha256: fidelityReport.narrativeBindingSha256,
      reportSha256: fidelityReport.reportHash,
      reportUri: run.fidelityReportUri,
    },
  };
  const content: Parameters<typeof approvedContent>[0] = {
    schemaVersion: CANONICAL_SUBMISSION_VERSION,
    graphType: "type1" as const,
    bundle: canonicalBundle,
    provenance,
  };
  const submission: CanonicalSubmission = {
    schemaVersion: CANONICAL_SUBMISSION_VERSION,
    submissionId: run.submissionId,
    createdAt: run.createdAt,
    graphType: "type1",
    bundle: canonicalBundle,
    bundleSha256: sha256(canonicalBundle),
    provenance,
    approval: {
      ...request.approval,
      meaning: "reviewed-fidelity-and-structure",
      approvedContentSha256: sha256(approvedContent(content)),
    },
  };
  return { submission, fidelityReport, sourceText };
}
