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
  NormalizationError,
  collectNarrativeSections,
  hasDrawnText,
  hasInvisibleFormatting,
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
import { transformDocument } from "./t/document.js";
import { descendants, MarkupRefusal, readTree, type ElementNode } from "./t/tree.js";
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
// function of the authority's document and List bytes and a person's request (pictures are no
// input yet: D6 refuses every one). The producer runs it to write a submission; Zone B's gate
// runs it again on bytes it fetched itself and requires the same submission (D1). It reads no
// clock, no locale and no network.

// The importer's version: part of the extractor's name in every submission it writes, and locked
// to the hash of this directory's code and data and of its golden vectors (D10).
export const IMPORTER_VERSION = "2.3.0";
export const IMPORTER_EXTRACTOR = `${AUTHORITY_IMPORTER_NAME}/${IMPORTER_VERSION}`;

const AUTHORITY_IMPORT_IDENTIFIER_SYSTEM = "https://khs.dev/fhir/identifier/authority-import";
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
  | "bytes"
  | "shape"
  | "binding"
  | "tree"
  | "titles"
  | "pictures"
  | "narrative"
  | "record"
  | "rendering";

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
  const ids = [document.id, document.identifier.value, list.id];
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

// The language the EMA's displays name, by their closed suffix (D9): the document type's display
// and the List entry's must both name the requested language.
const LANGUAGE_BY_DISPLAY_SUFFIX: Record<string, ImportRequest["language"]> = {
  " (English)": "en",
};

function displayLanguage(display: string | undefined): string | undefined {
  const suffix = Object.keys(LANGUAGE_BY_DISPLAY_SUFFIX).find((candidate) =>
    display?.endsWith(candidate),
  );
  return suffix === undefined ? undefined : LANGUAGE_BY_DISPLAY_SUFFIX[suffix];
}

// D5: the document is imported only with the List that lists it.
function checkBinding(document: EmaDocument, list: EmaList, request: ImportRequest): void {
  const composition = document.entry[0].resource;
  const reference = `Bundle/${document.id}`;
  const entries = list.entry.filter(({ item }) => item.reference === reference);
  if (entries.length !== 1) refuse("binding", "document-not-listed-once");
  if (entries[0]?.item.display !== composition.type.coding[0].display) {
    refuse("binding", "listed-as-another-document-type");
  }
  if (composition.title !== list.title) refuse("binding", "title-differs-from-the-list");
  if (displayLanguage(composition.type.coding[0].display) !== request.language) {
    refuse("binding", "language-differs-from-the-request");
  }
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

// An `img` element's start tag begins `<img` in the div (T's tree reads lower-case names only).
const IMG_START = /<img\b/iu;

// D6: every picture the document names, by form. T accepts `img`, but this stage refuses every
// picture until D6's templates and evidence exist; a reference the importer has neither a template
// nor evidence for refuses here, before T, so the reason says what is missing. The source is read
// from T's own tree of the div, its references decoded, so no other attribute (`data-src`) can pose
// as one; a div T cannot read into a tree holds no picture this stage can name, and T refuses it
// at `narrative`.
function checkPictures(placed: Placed[]): void {
  for (const { section } of placed) {
    const div = section.text?.div;
    if (div === undefined || !IMG_START.test(div)) continue;
    let root: ElementNode;
    try {
      root = readTree(div);
    } catch (error) {
      if (error instanceof MarkupRefusal) continue;
      throw error;
    }
    for (const node of descendants(root)) {
      if (node.kind !== "element" || node.name !== "img") continue;
      const src = node.attributes.find(({ name }) => name === "src")?.value;
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

// What the scanner makes of T(div) (docs/design/authority-import-t.md, T6): its text, the section's
// page; and that text normalised (fidelity section 3), undefined where normalisation refuses it,
// which decides whether the section draws anything and is its provenance's normalizedTextSha256.
// A scanner refusal, then a section 3 step 1 invisible character in the text (fidelity section 7),
// refuses the section.
type Scanned = { text: string; normalized: string | undefined };

function scan(div: string): Scanned {
  let text: string;
  try {
    text = xhtmlToText(div);
  } catch (error) {
    const code = (error as { code?: unknown }).code;
    refuse("narrative", typeof code === "string" ? `scanner-${code}` : "scanner-refused");
  }
  if (hasInvisibleFormatting(text)) refuse("narrative", "invisible-character");
  let normalized: string | undefined;
  try {
    normalized = normalizeText(text);
  } catch (error) {
    if (!(error instanceof NormalizationError)) throw error;
  }
  return { text, normalized };
}

type Page = {
  page: number;
  text: string;
  placed: Placed;
  // The section's T(div) and its normalised text, where it draws something.
  div: string | undefined;
  normalized: string | undefined;
};

// T over every section (T5's two passes), then each section in pre-order: its T refusal, the
// scanner's, and D4's record check.
function buildPages(placed: Placed[]): Page[] {
  const outcomes = transformDocument(placed.map((entry) => entry.section.text?.div));
  return placed.map((entry, index) => {
    const outcome = outcomes[index];
    if (outcome !== undefined && "refused" in outcome) refuse("narrative", outcome.refused);
    const div = outcome?.div;
    const scanned = div === undefined ? undefined : scan(div);
    const normalized = scanned?.normalized;
    const drawn = normalized !== undefined && hasDrawnText(normalized);
    const leaf = (entry.rule.children ?? []).length === 0;
    // A section that draws nothing refuses where the mapping needs its narrative, and a leaf
    // anywhere (FHIR cmp-1), with or without a div (D4).
    if (!drawn && (leaf || entry.rule.narrative === "required")) {
      refuse("record", "section-draws-nothing");
    }
    return {
      page: index + 1,
      text: scanned?.text ?? "",
      placed: entry,
      div: drawn ? div : undefined,
      normalized: drawn ? normalized : undefined,
    };
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
  checkBinding(document, list, request);
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
    decision("Bundle.type", "defaulted-by-rule", rule("document-bundle")),
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
      decisions.push(
        decision(`${path}.id`, "defaulted-by-rule", {
          sourceKey: sectionRule.sourceKey,
          ...rule("section-id-from-identifier"),
        }),
      );
      if (page.div !== undefined) {
        decisions.push(
          decision(`${path}.text`, "extracted-verbatim", {
            sourceKey: sectionRule.sourceKey,
            sourceField: `${path}.text`,
          }),
          decision(`${path}.text.status`, "defaulted-by-rule", {
            sourceKey: sectionRule.sourceKey,
            ...rule("narrative-status-additional"),
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
  // Each entry's fullUrl, id and profile are the importer's, by rule (D9).
  bundle.entry.forEach(({ resource }, position) => {
    decisions.push(
      decision(
        `Bundle.entry[${position}].fullUrl`,
        "defaulted-by-rule",
        rule("id-from-identifier"),
      ),
      decision(`${resource.resourceType}.id`, "defaulted-by-rule", rule("id-from-identifier")),
      decision(`${resource.resourceType}.meta.profile`, "defaulted-by-rule", rule("epi-profile")),
    );
  });
  decisions.push(
    decision("Bundle.id", "defaulted-by-rule", rule("id-from-identifier")),
    decision("Bundle.meta.profile", "defaulted-by-rule", rule("epi-profile")),
  );
  const canonicalBundle = bundle as unknown as CanonicalBundle;

  const sourceText: SourceDocumentText = {
    extractorVersion: IMPORTER_EXTRACTOR,
    pages: pages.map(({ page, text }) => ({ page, text, bodyStart: 0, bodyEnd: codePoints(text) })),
  };
  const narratives = collectNarrativeSections(compositionResource, mapping.sourceCodeSystem);
  const pageOf = new Map(pages.map((page) => [page.placed.path, page]));
  const sectionProvenance: SectionProvenance[] = narratives.map((narrative) => {
    const page = pageOf.get(narrative.path);
    if (page?.normalized === undefined) refuse("record", "narrative-without-a-page");
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
      normalizedTextSha256: sha256Utf8(page.normalized),
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
  // The renderer gate (docs/design/authority-import-renderer.md; PR 3c) is not wired into the
  // import yet: until it is, no authority's publication is accepted on T's static rules alone. A
  // synthetic publication is never approved content (D7), so it passes.
  if (request.authority !== "synthetic") refuse("rendering", "renderer-evidence-missing");
  return { submission, fidelityReport, sourceText };
}

// The SHA-256 of bytes as served, never of a re-serialised value (D1).
export function sha256Bytes(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
}
