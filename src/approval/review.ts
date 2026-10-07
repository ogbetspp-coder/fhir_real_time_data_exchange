import {
  APPROVAL_MEANING_TEXT,
  REVIEW_VERSION,
  ReviewRecordSchema,
  type ApprovalDocument,
  type ApprovalEnvironment,
  type ApprovedSection,
  type ReviewRecord,
} from "../contracts/approval.js";
import { approvedContent, type DocumentGateResult } from "../contracts/canonical-submission.js";
import { mappingReference, type EmaMapping } from "../fhir/mapping.js";
import type { EmaPackage } from "../fhir/transform.js";
import { isComposition, type CompositionSection } from "../fhir/types.js";
import { sha256, sha256Utf8, stableUuid } from "../lib/hash.js";
import {
  publishedDocumentSha256,
  publishedSections,
  type StatementExpectation,
  type VerifiedStatement,
} from "./statement.js";

// What the approver is shown, built before they see it and hashed (docs/design/approval.md, D6):
// a pure function of the gate's verified submission, the record the crosswalk will publish, and
// the document's current head. The diff is regulated logic, so it is mechanical: a section whose
// published narrative differs by one byte from the head's is `changed`, and its full text is shown
// whatever it says. The review never shows a diff alone.

// What a record will be approved and published as: its identity, content hash, mapping and
// sections. The signer reads these to build the review and the statement; the pipeline reads the
// same, from its own gate and crosswalk, and requires the statement to name them.
export type RecordFacts = Required<Omit<StatementExpectation, "environment">> & {
  sectionsById: ReadonlyMap<string, CompositionSection>;
  product: ReviewRecord["product"];
};

type Identifier = { system?: string; value: string };

function identifiersOf(resource: Record<string, unknown> | undefined): Identifier[] {
  const identifiers = resource?.identifier;
  return (Array.isArray(identifiers) ? identifiers : []).flatMap((identifier: unknown) => {
    const { system, value } = (identifier ?? {}) as { system?: unknown; value?: unknown };
    if (typeof value !== "string") return [];
    return [typeof system === "string" ? { system, value } : { value }];
  });
}

// What the published record states about the product, read from the crosswalk's output: the
// product's name and identifiers, its authorisations' identifiers (the EU authorisation numbers
// among them), and the holders they name. The approval covers these through the statement's
// documentBundleSha256; the review shows them so the approver sees what they attest.
function productFacts(bundle: EmaPackage["documentBundle"]): ReviewRecord["product"] {
  const entries = bundle.entry.map(({ fullUrl, resource }) => ({
    fullUrl,
    resource,
  }));
  const of = (type: string) => entries.filter(({ resource }) => resource.resourceType === type);
  const product = of("MedicinalProductDefinition")[0]?.resource;
  const names = product?.name;
  const name = Array.isArray(names)
    ? (names[0] as { productName?: unknown } | undefined)?.productName
    : undefined;
  if (typeof name !== "string") throw new Error("The published record names no product");
  const authorisations = of("RegulatedAuthorization");
  const holders = [
    ...new Set(
      authorisations.flatMap(({ resource }) => {
        const reference = (resource.holder as { reference?: unknown } | undefined)?.reference;
        const holder = entries.find(
          (entry) => entry.fullUrl === reference && entry.resource.resourceType === "Organization",
        )?.resource.name;
        return typeof holder === "string" ? [holder] : [];
      }),
    ),
  ];
  return {
    name,
    identifiers: identifiersOf(product),
    holders,
    authorisations: authorisations.flatMap(({ resource }) => identifiersOf(resource)),
  };
}

export function recordFacts(
  gate: DocumentGateResult,
  transformed: EmaPackage,
  mapping: EmaMapping,
): RecordFacts {
  const composition = transformed.documentBundle.entry[0]?.resource;
  const emaBundleId = transformed.documentBundle.id;
  if (composition === undefined || !isComposition(composition) || emaBundleId === undefined) {
    throw new Error("The published record has no Composition to approve");
  }
  const sections = publishedSections(composition.section, mapping);
  if (sections === undefined || sections.length === 0) {
    throw new Error("The published record has a section a statement cannot name");
  }
  const sectionsById = new Map<string, CompositionSection>();
  const walk = (children: readonly CompositionSection[] | undefined): void => {
    for (const section of children ?? []) {
      if (section.id !== undefined) sectionsById.set(section.id, section);
      walk(section.section);
    }
  };
  walk(composition.section);
  const { system, value } = gate.submission.bundle.identifier;
  const document: ApprovalDocument = {
    identifier: { ...(system === undefined ? {} : { system }), value },
    emaBundleId,
    language: typeof composition.language === "string" ? composition.language : "",
  };
  return {
    document,
    submissionId: gate.submission.submissionId,
    approvedContentSha256: sha256(approvedContent(gate.submission)),
    mappingVersion: mappingReference(mapping),
    documentBundleSha256: publishedDocumentSha256(transformed.documentBundle),
    sections,
    sectionsById,
    product: productFacts(transformed.documentBundle),
  };
}

function changeOf(
  section: ApprovedSection,
  baseline: ReadonlyMap<string, string> | undefined,
): "added" | "changed" | "unchanged" {
  const before = baseline?.get(section.sourceKey);
  if (before === undefined) return "added";
  return before === section.narrativeDivSha256 ? "unchanged" : "changed";
}

export function buildReview(
  environment: ApprovalEnvironment,
  gate: DocumentGateResult,
  facts: RecordFacts,
  head: VerifiedStatement | undefined,
): ReviewRecord {
  const source = gate.submission.provenance.sourceDocument;
  // Phase 1 approves drawn records; an authority import's `request` has a review of its own
  // (the design's amendment of 2026-09-25), not built yet.
  if (source.kind !== "drawn") throw new Error("Only a drawn record's review is built");
  const report = gate.report;
  if (report.status !== "passed") throw new Error("Only a passed fidelity report is reviewed");

  const baseline =
    head === undefined
      ? undefined
      : new Map(head.statement.sections.map((s) => [s.sourceKey, s.narrativeDivSha256]));
  const current = new Set(facts.sections.map(({ sourceKey }) => sourceKey));

  return ReviewRecordSchema.parse({
    reviewVersion: REVIEW_VERSION,
    environment,
    submissionId: facts.submissionId,
    approvedContentSha256: facts.approvedContentSha256,
    document: facts.document,
    mappingVersion: facts.mappingVersion,
    documentBundleSha256: facts.documentBundleSha256,
    product: facts.product,
    source: { sha256: source.sha256, mediaType: source.mediaType },
    fidelity: {
      status: report.status,
      reportSha256: report.reportHash,
      normalizationVersion: report.normalizationVersion,
      sectionsChecked: report.summary.total,
      sectionsMatched: report.summary.verified,
    },
    meaning: "record-represents-approved-label",
    baseline:
      head === undefined
        ? null
        : {
            sequence: head.statement.sequence,
            statementSha256: head.statementSha256,
            submissionId: head.statement.submissionId,
          },
    sections: facts.sections.map((section) => {
      const published = facts.sectionsById.get(stableUuid("ema-qrd-section", section.sourceKey));
      return {
        sourceKey: section.sourceKey,
        title: published?.title,
        div: published?.text?.div,
        narrativeDivSha256: section.narrativeDivSha256,
        change: changeOf(section, baseline),
      };
    }),
    removed: (head?.statement.sections ?? []).filter(({ sourceKey }) => !current.has(sourceKey)),
  });
}

// --- the file the approver opens -----------------------------------------------------------------

function escape(text: string): string {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

// Inline style only, and a policy that lets nothing load or run: the page is read, never executed.
// Each section's narrative is its published XHTML, verbatim; the gate's scanner admits only its
// closed lists of elements and attributes (src/fidelity/xhtml.ts), so it carries no script, link
// or style of its own.
const STYLE = [
  "body{font-family:system-ui,sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem;line-height:1.5}",
  "dl{display:grid;grid-template-columns:max-content 1fr;gap:.25rem 1rem}dt{font-weight:600}",
  "dd{margin:0;font-family:ui-monospace,monospace;overflow-wrap:anywhere}",
  "section{border-top:1px solid #999;margin-top:1.5rem;padding-top:.5rem}",
  ".change{font-size:.8rem;padding:.1rem .4rem;border:1px solid #333;margin-left:.5rem}",
  ".hash{font-family:ui-monospace,monospace;font-size:.8rem;color:#444;overflow-wrap:anywhere}",
  "table{border-collapse:collapse}td,th{border:1px solid #999;padding:.2rem .4rem}",
].join("");

function row(term: string, value: string): string {
  return `<dt>${escape(term)}</dt><dd>${escape(value)}</dd>`;
}

// The review file: deterministic bytes from the record alone. Its SHA-256 is what the approver's
// click carries and the statement signs (reviewSha256).
export function renderReview(record: ReviewRecord): string {
  const { document } = record;
  const identifier = `${document.identifier.system ?? "(no system)"}|${document.identifier.value}`;
  const facts = [
    row("Environment", record.environment),
    row("Document", identifier),
    row("EMA document Bundle", document.emaBundleId),
    row("Language", document.language),
    row("Submission", record.submissionId),
    row("Approved content SHA-256", record.approvedContentSha256),
    row("Mapping", record.mappingVersion),
    row("Product", record.product.name),
    ...record.product.identifiers.map(({ system, value }) =>
      row("Product identifier", `${system ?? "(no system)"}|${value}`),
    ),
    ...record.product.holders.map((holder) => row("Marketing authorisation holder", holder)),
    ...record.product.authorisations.map(({ system, value }) =>
      row("Authorisation", `${system ?? "(no system)"}|${value}`),
    ),
    row("Published record SHA-256", record.documentBundleSha256),
    row("Source document SHA-256", record.source.sha256),
    row("Source document type", record.source.mediaType),
    row(
      "Fidelity",
      `${record.fidelity.status}: ${String(record.fidelity.sectionsMatched)} of ${String(record.fidelity.sectionsChecked)} sections, ${record.fidelity.normalizationVersion}`,
    ),
    row("Fidelity report SHA-256", record.fidelity.reportSha256),
    row(
      "Compared with",
      record.baseline === null
        ? "nothing: this document has no current approval"
        : `approval ${String(record.baseline.sequence)} (statement ${record.baseline.statementSha256}, submission ${record.baseline.submissionId})`,
    ),
  ].join("");
  const sections = record.sections
    .map(
      (section) =>
        `<section><h2>${escape(section.title)}<span class="change">${section.change}</span></h2>` +
        `<p class="hash">${escape(section.sourceKey)} · narrative SHA-256 ${section.narrativeDivSha256}</p>` +
        `${section.div}</section>`,
    )
    .join("\n");
  const removed =
    record.removed.length === 0
      ? ""
      : `<section><h2>Removed since the current approval</h2><ul>${record.removed
          .map(
            ({ sourceKey, narrativeDivSha256 }) =>
              `<li class="hash">${escape(sourceKey)} · ${narrativeDivSha256}</li>`,
          )
          .join("")}</ul></section>`;
  return [
    "<!doctype html>",
    '<html lang="en"><head><meta charset="utf-8">',
    `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'">`,
    '<meta name="viewport" content="width=device-width, initial-scale=1">',
    `<title>Approval review ${escape(record.submissionId)}</title><style>${STYLE}</style></head><body>`,
    "<h1>Approval review</h1>",
    `<p>Approving signs this statement: <strong>${escape(APPROVAL_MEANING_TEXT)}</strong> It is not the regulatory approval of the label. It covers the whole published record: the facts below, and every section's text, as they hash to the published record's SHA-256.</p>`,
    `<dl>${facts}</dl>`,
    sections,
    removed,
    `<p class="hash">${escape(REVIEW_VERSION)}</p>`,
    "</body></html>",
    "",
  ].join("\n");
}

export function reviewSha256(html: string): string {
  return sha256Utf8(html);
}
