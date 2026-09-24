import { createHash } from "node:crypto";

import {
  AUTHORITY_IMPORTER_NAME,
  AUTHORITY_IMPORT_PREFIX,
  CANONICAL_SUBMISSION_VERSION,
  approvedContent,
  type AuthoritySourceDocument,
  type CanonicalBundle,
  type CanonicalSubmission,
  type ImportRequest,
  type SectionProvenance,
  type StructuringDecision,
} from "../contracts/index.js";
import {
  NORMALIZATION_VERSION,
  collectNarrativeSections,
  normalizeNarrative,
  normalizeText,
  verifyNarrativeFidelity,
  xhtmlToText,
  type FidelityReport,
  type SourceDocumentText,
} from "../fidelity/index.js";
import {
  EMA_MAPPING_ID,
  permittedTitles,
  type EmaMapping,
  type SectionRule,
} from "../fhir/mapping.js";
import { sha256, sha256Utf8, stableUuid } from "../lib/hash.js";
import { AuthorityBytesError, readAuthorityJson } from "./json.js";
import {
  EMA_DOCUMENT_IDENTIFIER_SYSTEM,
  EMA_EPI_ID_SYSTEM,
  EMA_PROCEDURE_SYSTEM,
  EMA_SECTION_SYSTEM,
  EmaDocumentSchema,
  EmaListSchema,
  SPOR_ORGANISATIONS,
  listIdentity,
  type EmaDocument,
  type EmaList,
  type EmaSection,
  type ListIdentity,
} from "./shape.js";

// The authority importer (docs/design/authority-import-contract.md): a pure, deterministic
// function of the authority's document and List bytes, the pictures it references, and a
// person's request. The producer runs it to write a submission; Zone B's gate runs it again on
// bytes it fetched itself and requires the same submission (D1). It reads no clock, no locale
// and no network.

// The importer's version: part of the extractor's name in every submission it writes, and locked
// to the hash of this directory's code and data and of its golden vectors (D10).
export const IMPORTER_VERSION = "1.0.0";
export const IMPORTER_EXTRACTOR = `${AUTHORITY_IMPORTER_NAME}/${IMPORTER_VERSION}`;

export const AUTHORITY_IMPORT_IDENTIFIER_SYSTEM =
  "https://khs.dev/fhir/identifier/authority-import";
const GLOBAL_EPI_PROFILE_BASE =
  "http://hl7.org/fhir/uv/emedicinal-product-info/StructureDefinition/";

// The stage that refused, and a closed reason; never narrative.
export class ImportRefusedError extends Error {
  public constructor(
    public readonly stage: ImportStage,
    public readonly reason: string,
  ) {
    super(`Authority import refused at ${stage}: ${reason}`);
    this.name = "ImportRefusedError";
  }
}

// The order the importer checks in; a refusal names the first stage that fails (D10).
export type ImportStage =
  "bytes" | "shape" | "binding" | "tree" | "titles" | "pictures" | "narrative" | "record";

export type ImportInputs = {
  document: Uint8Array;
  index: Uint8Array;
};

// The fields of a submission a run chooses: identifiers, times and storage locations only
// (D3's free class). The gate copies them from the submission it recomputes.
export type ImportRun = {
  submissionId: string;
  createdAt: string;
  extractionRunId: string;
  serviceVersion: string;
  requestedBy: string;
  requestedAt: string;
  sourceTextUri: string;
  fidelityReportUri: string;
};

export type ImportResult = {
  submission: CanonicalSubmission;
  fidelityReport: FidelityReport;
  sourceText: SourceDocumentText;
};

// A synthetic authority's ids sit in a reserved block (D7).
export const SYNTHETIC_ID_BLOCK = "00000000-5979-4e74-8000-";
const SYNTHETIC_VALUE_PREFIX = "SYNTHETIC-";

const SEGMENT = { EMA: "ema", synthetic: "synthetic" } as const;

function refuse(stage: ImportStage, reason: string): never {
  throw new ImportRefusedError(stage, reason);
}

function parse<T>(
  schema: { safeParse: (value: unknown) => { success: true; data: T } | { success: false } },
  bytes: Uint8Array,
  what: string,
): T {
  let json: unknown;
  try {
    json = readAuthorityJson(bytes);
  } catch (error) {
    if (error instanceof AuthorityBytesError) refuse("bytes", `${what}-${error.reason}`);
    throw error;
  }
  const parsed = schema.safeParse(json);
  if (!parsed.success) refuse("shape", `${what}-shape`);
  return parsed.data;
}

function codePoints(text: string): number {
  return Array.from(text).length;
}

// The request, the files and the authority must name the same things; a synthetic authority's
// values are all synthetic, and a real one's none (D7).
function checkIdentity(
  request: ImportRequest,
  document: EmaDocument,
  list: EmaList,
  identity: ListIdentity,
): void {
  if (document.id !== request.documentId) refuse("shape", "document-is-not-the-requested-one");
  if (list.id !== request.indexId) refuse("shape", "list-is-not-the-requested-one");
  const values = [
    identity.epiId,
    identity.procedureNumber,
    identity.holder.code,
    identity.agency.code,
  ];
  const ids = [document.id, list.id];
  if (request.authority === "synthetic") {
    if (!ids.every((id) => id.startsWith(SYNTHETIC_ID_BLOCK))) {
      refuse("shape", "synthetic-publication-with-a-real-id");
    }
    if (!values.every((value) => value.startsWith(SYNTHETIC_VALUE_PREFIX))) {
      refuse("shape", "synthetic-publication-with-a-real-value");
    }
  } else if (
    ids.some((id) => id.startsWith(SYNTHETIC_ID_BLOCK)) ||
    values.some((value) => value.startsWith(SYNTHETIC_VALUE_PREFIX))
  ) {
    refuse("shape", "real-publication-with-a-synthetic-value");
  }
}

// D5: the document is imported only with the List that lists it.
function checkBinding(document: EmaDocument, list: EmaList): void {
  const composition = document.entry[0].resource;
  const reference = `Bundle/${document.id}`;
  const entries = list.entry.filter(({ item }) => item.reference === reference);
  if (entries.length !== 1) refuse("binding", "document-not-listed-once");
  if (entries[0]?.item.display !== composition.type.coding[0].display) {
    refuse("binding", "listed-as-another-document-type");
  }
  if (composition.title !== list.title) refuse("binding", "title-differs-from-the-list");
}

type Placed = { section: EmaSection; rule: SectionRule; path: string };

// D4: the document's section tree is the mapping's, section for section, in order, each with
// the rule's code (through the stated alias) and a heading the QRD template permits.
function placeSections(document: EmaDocument, mapping: EmaMapping): Placed[] {
  if (!(mapping.targetCodeSystemAliases ?? []).includes(EMA_SECTION_SYSTEM)) {
    refuse("tree", "section-code-system-not-aliased");
  }
  const placed: Placed[] = [];
  const walk = (sections: EmaSection[], rules: SectionRule[], base: string): void => {
    if (sections.length !== rules.length) refuse("tree", "section-tree-differs");
    sections.forEach((section, position) => {
      const rule = rules[position];
      if (section.code.coding[0].code !== rule?.targetCode) {
        refuse("tree", "section-tree-differs");
      }
      const path = `${base}[${position}]`;
      placed.push({ section, rule, path });
      walk(section.section ?? [], rule.children ?? [], `${path}.section`);
    });
  };
  walk(document.entry[0].resource.section, [mapping.root], "Composition.section");
  for (const { section, rule } of placed) {
    if (!permittedTitles(rule).includes(section.title)) refuse("titles", "heading-not-permitted");
  }
  return placed;
}

const IMG = /<img\b[^>]*>/giu;
const SRC = /\bsrc\s*=\s*("([^"]*)"|'([^']*)')/iu;

// D6: every picture the document names, by form. PR 2's T carries none, so the stage refuses
// any picture; a reference the importer has neither a template nor evidence for refuses here,
// before T, so the reason says what is missing.
function checkPictures(placed: Placed[]): void {
  for (const { section } of placed) {
    const div = section.text?.div;
    if (div === undefined) continue;
    for (const tag of div.match(IMG) ?? []) {
      const match = SRC.exec(tag);
      const src = match?.[2] ?? match?.[3];
      if (src === undefined) refuse("pictures", "picture-without-a-source");
      if (src.startsWith("data:") || src.startsWith("#")) {
        refuse("pictures", "pictures-not-enabled");
      }
      if (/^~\/_entity\/annotation\/[0-9a-f-]{36}$/u.test(src)) {
        refuse("pictures", "picture-reference-without-template-or-evidence");
      }
      refuse("pictures", "picture-reference-in-no-known-grammar");
    }
  }
}

// Characters §3 step 1 removes, which a structured page may not hold (fidelity §7).
const INVISIBLE = /[\u00ad\u200b\ufeff\u2060]/u;

// T, ADR 0005's lexical transform, with empty closed lists: it removes nothing, so a div is
// carried only if the fidelity scanner reads it as it stands (D1, "What PR 2 ships").
function transform(div: string): string {
  let text: string;
  try {
    text = xhtmlToText(div);
  } catch (error) {
    const code = (error as { code?: unknown }).code;
    refuse("narrative", typeof code === "string" ? `scanner-${code}` : "scanner-refused");
  }
  if (INVISIBLE.test(text)) refuse("narrative", "invisible-character");
  return div;
}

type Page = { page: number; text: string; placed: Placed; div: string | undefined };

function buildPages(placed: Placed[]): Page[] {
  return placed.map((entry, index) => {
    const source = entry.section.text?.div;
    if (source === undefined) return { page: index + 1, text: "", placed: entry, div: undefined };
    const div = transform(source);
    const drawn = "text" in normalizeNarrative(div);
    const leaf = (entry.rule.children ?? []).length === 0;
    if (!drawn && (leaf || entry.rule.narrative === "required")) {
      refuse("record", "section-draws-nothing");
    }
    return { page: index + 1, text: xhtmlToText(div), placed: entry, div: drawn ? div : undefined };
  });
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

const rule = (id: string): { ruleId: string } => ({ ruleId: `authority-import:${id}` });

export function importPublication(
  request: ImportRequest,
  inputs: ImportInputs,
  mapping: EmaMapping,
  run: ImportRun,
): ImportResult {
  const document = parse(EmaDocumentSchema, inputs.document, "document");
  const list = parse(EmaListSchema, inputs.index, "list");
  const identity = listIdentity(list);
  if (typeof identity === "string") refuse("shape", identity);
  checkIdentity(request, document, list, identity);
  checkBinding(document, list);
  const placed = placeSections(document, mapping);
  checkPictures(placed);
  const pages = buildPages(placed);

  const composition = document.entry[0].resource;
  const segment = SEGMENT[request.authority];
  const identifierValue = `${AUTHORITY_IMPORT_PREFIX}${segment}:${document.id}`;
  const id = (resourceType: string): string =>
    stableUuid(`authority-import:${resourceType}`, identifierValue);
  const url = (resourceType: string): string => `urn:uuid:${id(resourceType)}`;

  const decisions: Decision[] = [
    decision("Bundle.identifier", "defaulted-by-rule", rule("identifier")),
    decision("Bundle.language", "defaulted-by-rule", rule("language")),
    decision("Bundle.timestamp", "extracted-verbatim", { sourceField: "Bundle.timestamp" }),
    decision("Composition.identifier", "defaulted-by-rule", rule("identifier")),
    decision("Composition.relatesTo", "extracted-verbatim", { sourceField: "Bundle.identifier" }),
    decision("Composition.language", "defaulted-by-rule", rule("language")),
    decision("Composition.status", "defaulted-by-rule", rule("status-from-publication")),
    decision("Composition.type", "extracted-verbatim", { sourceField: "Composition.type" }),
    decision("Composition.title", "extracted-verbatim", { sourceField: "Composition.title" }),
    decision("Composition.date", "extracted-verbatim", { sourceField: "Composition.date" }),
    decision("Composition.subject", "defaulted-by-rule", rule("subject-is-the-product")),
    decision("Composition.author", "defaulted-by-rule", rule("author-is-the-holder")),
    decision("MedicinalProductDefinition.identifier", "extracted-verbatim", {
      sourceField: "List.identifier",
    }),
    decision("MedicinalProductDefinition.name", "extracted-verbatim", {
      sourceField: "List.title",
    }),
    decision("Organization.identifier", "extracted-verbatim", {
      sourceField: "List.subject.extension[marketingAuthorisationHolder]",
    }),
    decision("Organization.name", "extracted-verbatim", {
      sourceField: "List.subject.extension[marketingAuthorisationHolder]",
    }),
    decision("RegulatedAuthorization.identifier", "extracted-verbatim", {
      sourceField: "List.subject.extension[procedureNumber]",
    }),
    decision("RegulatedAuthorization.case", "extracted-verbatim", {
      sourceField: "List.subject.extension[procedureNumber]",
    }),
    decision("RegulatedAuthorization.regulator", "extracted-verbatim", {
      sourceField: "List.subject.extension[regulatoryAgency]",
    }),
    decision("RegulatedAuthorization.subject", "defaulted-by-rule", rule("subject-is-the-product")),
    decision("RegulatedAuthorization.holder", "defaulted-by-rule", rule("holder-is-the-holder")),
  ];

  type Section = {
    id: string;
    title: string;
    code: { coding: { system: string; code: string }[] };
    text?: { status: "additional"; div: string };
    section?: Section[];
  };
  const byPath = new Map(pages.map((page) => [page.placed.path, page]));
  const build = (sections: EmaSection[], base: string): Section[] =>
    sections.map((section, position) => {
      const path = `${base}[${position}]`;
      const page = byPath.get(path);
      if (page === undefined) refuse("record", "section-without-a-page");
      const { rule: sectionRule } = page.placed;
      decisions.push(
        decision(`${path}.code`, "code-mapped", {
          sourceKey: sectionRule.sourceKey,
          sourceField: `${path}.code`,
          terminologyRef: {
            system: mapping.sourceCodeSystem,
            code: sectionRule.sourceKey,
            version: mapping.mappingVersion,
            lookupId: `mapping:${sectionRule.targetCode}`,
          },
        }),
        decision(`${path}.title`, "extracted-verbatim", {
          sourceKey: sectionRule.sourceKey,
          sourceField: `${path}.title`,
        }),
      );
      if (page.div !== undefined) {
        decisions.push(
          decision(`${path}.text`, "extracted-verbatim", {
            sourceKey: sectionRule.sourceKey,
            sourceField: `${path}.text`,
          }),
        );
      }
      const children = build(section.section ?? [], `${path}.section`);
      return {
        id: stableUuid("authority-import:section", `${identifierValue}:${path}`),
        title: section.title,
        code: { coding: [{ system: mapping.sourceCodeSystem, code: sectionRule.sourceKey }] },
        ...(page.div === undefined
          ? {}
          : { text: { status: "additional" as const, div: page.div } }),
        ...(children.length === 0 ? {} : { section: children }),
      };
    });
  const sections = build(composition.section, "Composition.section");

  const compositionResource = {
    resourceType: "Composition",
    id: id("Composition"),
    meta: profile("Composition"),
    language: request.language,
    identifier: [
      { system: AUTHORITY_IMPORT_IDENTIFIER_SYSTEM, value: `${identifierValue}:composition` },
    ],
    status: "final",
    type: { coding: [{ ...composition.type.coding[0] }] },
    subject: [{ reference: url("MedicinalProductDefinition") }],
    date: composition.date,
    author: [{ reference: url("Organization") }],
    title: composition.title,
    relatesTo: [
      {
        type: "derived-from",
        resourceReference: {
          identifier: {
            system: EMA_DOCUMENT_IDENTIFIER_SYSTEM,
            value: document.identifier.value,
          },
        },
      },
    ],
    section: sections,
  };
  const bundle = {
    resourceType: "Bundle" as const,
    id: id("Bundle"),
    meta: profile("Bundle"),
    language: request.language,
    identifier: { system: AUTHORITY_IMPORT_IDENTIFIER_SYSTEM, value: identifierValue },
    type: "document" as const,
    timestamp: document.timestamp,
    entry: [
      { fullUrl: url("Composition"), resource: compositionResource },
      {
        fullUrl: url("MedicinalProductDefinition"),
        resource: {
          resourceType: "MedicinalProductDefinition",
          id: id("MedicinalProductDefinition"),
          meta: profile("MedicinalProductDefinition"),
          identifier: [{ system: EMA_EPI_ID_SYSTEM, value: identity.epiId }],
          name: [{ productName: identity.title }],
        },
      },
      {
        fullUrl: url("Organization"),
        resource: {
          resourceType: "Organization",
          id: id("Organization"),
          meta: profile("Organization"),
          identifier: [{ system: SPOR_ORGANISATIONS, value: identity.holder.code }],
          name: identity.holder.display,
        },
      },
      {
        fullUrl: url("RegulatedAuthorization"),
        resource: {
          resourceType: "RegulatedAuthorization",
          id: id("RegulatedAuthorization"),
          meta: profile("RegulatedAuthorization"),
          identifier: [{ system: EMA_PROCEDURE_SYSTEM, value: identity.procedureNumber }],
          subject: [{ reference: url("MedicinalProductDefinition") }],
          holder: { reference: url("Organization") },
          regulator: {
            identifier: { system: SPOR_ORGANISATIONS, value: identity.agency.code },
            display: identity.agency.display,
          },
          case: { identifier: { system: EMA_PROCEDURE_SYSTEM, value: identity.procedureNumber } },
        },
      },
    ],
  };
  const canonicalBundle = bundle as unknown as CanonicalBundle;

  const sourceText: SourceDocumentText = {
    extractorVersion: IMPORTER_EXTRACTOR,
    pages: pages.map(({ page, text }) => ({ page, text, bodyStart: 0, bodyEnd: codePoints(text) })),
  };
  const narratives = collectNarrativeSections(compositionResource, mapping.sourceCodeSystem);
  const pageOf = new Map(pages.map((page) => [page.placed.path, page]));
  const sectionProvenance: SectionProvenance[] = narratives.map((narrative) => {
    const page = pageOf.get(narrative.path);
    if (page === undefined) refuse("record", "narrative-without-a-page");
    return {
      sourceKey: narrative.sourceKey,
      spans: [
        {
          page: page.page,
          startOffset: 0,
          endOffset: codePoints(page.text),
          textSha256: sha256Utf8(page.text),
        },
      ],
      narrativeDivSha256: sha256Utf8(narrative.div),
      normalizedTextSha256: sha256Utf8(normalizeText(xhtmlToText(narrative.div))),
    };
  });
  const fidelityReport = verifyNarrativeFidelity({
    normalizationVersion: NORMALIZATION_VERSION,
    source: sourceText,
    sections: narratives,
    provenance: sectionProvenance,
  });
  // Both hold by construction (each page is its own section's scanner text, and a section left
  // without text draws nothing); they stay as a defence, and the gate checks the second again.
  if (fidelityReport.status !== "passed") refuse("record", "fidelity-not-passed");
  if (fidelityReport.coverage.uncoveredGaps !== 0) refuse("record", "uncovered-page-not-blank");

  const source: AuthoritySourceDocument = {
    kind: "authority-publication",
    mediaType: "application/fhir+json",
    authority: request.authority,
    request,
    document: {
      id: document.id,
      sha256: sha256Bytes(inputs.document),
      byteLength: inputs.document.length,
    },
    index: {
      id: list.id,
      sha256: sha256Bytes(inputs.index),
      byteLength: inputs.index.length,
      epiId: identity.epiId,
      versionNumber: identity.versionNumber,
      metaVersionId: identity.metaVersionId,
      status: "current",
    },
    pictures: [],
    sectionPages: pages.map(({ page, placed: entry }) => ({
      page,
      path: entry.path,
      code: entry.section.code.coding[0].code,
    })),
    extractedText: {
      uri: run.sourceTextUri,
      sha256: sha256(sourceText),
      extractorVersion: IMPORTER_EXTRACTOR,
    },
  };
  const provenance: CanonicalSubmission["provenance"] = {
    sourceDocument: source,
    extraction: {
      extractionRunId: run.extractionRunId,
      serviceVersion: run.serviceVersion,
      parser: { name: AUTHORITY_IMPORTER_NAME, version: IMPORTER_VERSION },
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
      method: "authority-publication",
      meaning: "authority-publication-imported",
      authority: request.authority,
      authorityStatus: "pilot",
      publication: {
        epiId: identity.epiId,
        documentId: document.id,
        indexId: list.id,
        versionNumber: identity.versionNumber,
        procedureNumber: identity.procedureNumber,
        authorityTimestamp: document.timestamp,
      },
      requestedBy: run.requestedBy,
      requestedAt: run.requestedAt,
      approvedContentSha256: sha256(approvedContent(content)),
    },
  };
  return { submission, fidelityReport, sourceText };
}

// The SHA-256 of bytes as served, never of a re-serialised value (D1).
export function sha256Bytes(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
}
