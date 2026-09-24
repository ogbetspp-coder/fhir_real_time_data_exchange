import { permittedTitles, type EmaMapping, type SectionRule } from "./mapping.js";
import {
  isComposition,
  type CompositionSection,
  type FhirBundle,
  type FhirResource,
  type OperationOutcome,
  type OperationOutcomeIssue,
} from "./types.js";

const REQUIRED_TYPE2_RESOURCES = [
  "Composition",
  "Organization",
  "MedicinalProductDefinition",
  "RegulatedAuthorization",
  "PackagedProductDefinition",
  "ManufacturedItemDefinition",
  "AdministrableProductDefinition",
  "Ingredient",
  "SubstanceDefinition",
] as const;

function issue(
  severity: OperationOutcomeIssue["severity"],
  code: string,
  diagnostics: string,
  expression?: string,
): OperationOutcomeIssue {
  return {
    severity,
    code,
    diagnostics,
    ...(expression === undefined ? {} : { expression: [expression] }),
  };
}

// A Type 1 record, an authority import's (docs/design/authority-import-contract.md, D9): the
// Composition, the product scope, its holder and its authorisation, exactly one of each and
// nothing else. Packs, items, ingredients and substances are declared not supplied by their
// absence; none may be inferred.
const TYPE1_RESOURCES = [
  "Composition",
  "MedicinalProductDefinition",
  "Organization",
  "RegulatedAuthorization",
] as const;

type GraphType = "type1" | "type2";

function referenceOf(value: unknown): string | undefined {
  const reference = (value as { reference?: unknown } | undefined)?.reference;
  return typeof reference === "string" ? reference : undefined;
}

function firstReference(value: unknown): string | undefined {
  return Array.isArray(value) ? referenceOf(value[0]) : referenceOf(value);
}

function hasIdentifier(resource: FhirResource): boolean {
  const identifiers: unknown = resource.identifier;
  return (
    Array.isArray(identifiers) &&
    identifiers.some((entry: unknown) => {
      const { system, value } = (entry ?? {}) as { system?: unknown; value?: unknown };
      return typeof system === "string" && typeof value === "string" && value.length > 0;
    })
  );
}

function type1GraphIssues(bundle: FhirBundle): OperationOutcomeIssue[] {
  const issues: OperationOutcomeIssue[] = [];
  const byType = new Map<string, { fullUrl: string; resource: FhirResource }[]>();
  for (const entry of bundle.entry) {
    const list = byType.get(entry.resource.resourceType) ?? [];
    list.push(entry);
    byType.set(entry.resource.resourceType, list);
  }
  for (const [resourceType, entries] of byType) {
    if (!(TYPE1_RESOURCES as readonly string[]).includes(resourceType)) {
      issues.push(
        issue("error", "structure", `A Type 1 record carries no ${resourceType}`, "Bundle.entry"),
      );
    } else if (entries.length !== 1) {
      issues.push(
        issue("error", "structure", `A Type 1 record has one ${resourceType}`, "Bundle.entry"),
      );
    }
  }
  const one = (type: (typeof TYPE1_RESOURCES)[number]) => {
    const entries = byType.get(type);
    if (entries === undefined) {
      issues.push(issue("error", "required", `Type 1 record is missing ${type}`, "Bundle.entry"));
    }
    return entries?.length === 1 ? entries[0] : undefined;
  };
  const composition = one("Composition");
  const product = one("MedicinalProductDefinition");
  const holder = one("Organization");
  const authorisation = one("RegulatedAuthorization");
  if (composition === undefined || product === undefined || holder === undefined) return issues;
  if (authorisation === undefined) return issues;

  const expectLink = (actual: string | undefined, expected: string, where: string): void => {
    if (actual !== expected) {
      issues.push(issue("error", "value", `${where} must reference its Type 1 target`, where));
    }
  };
  expectLink(firstReference(composition.resource.subject), product.fullUrl, "Composition.subject");
  expectLink(firstReference(composition.resource.author), holder.fullUrl, "Composition.author");
  expectLink(
    firstReference(authorisation.resource.subject),
    product.fullUrl,
    "RegulatedAuthorization.subject",
  );
  expectLink(
    referenceOf(authorisation.resource.holder),
    holder.fullUrl,
    "RegulatedAuthorization.holder",
  );
  const names: unknown = product.resource.name;
  if (
    !Array.isArray(names) ||
    names.length !== 1 ||
    typeof (names[0] as { productName?: unknown }).productName !== "string"
  ) {
    issues.push(
      issue("error", "required", "The product has one name", "MedicinalProductDefinition.name"),
    );
  }
  for (const [resource, where] of [
    [product.resource, "MedicinalProductDefinition.identifier"],
    [holder.resource, "Organization.identifier"],
    [authorisation.resource, "RegulatedAuthorization.identifier"],
  ] as const) {
    if (!hasIdentifier(resource)) {
      issues.push(issue("error", "required", `${where} is required`, where));
    }
  }
  return issues;
}

// The canonical record's preflight: Type 2 exactly as strict as ever, Type 1 for an authority
// import only (the gate ties the graph type to the source).
export function validateCanonicalPreflight(
  bundle: FhirBundle,
  graphType: GraphType,
): OperationOutcome {
  if (graphType === "type2") return validateType2Preflight(bundle);
  const issues: OperationOutcomeIssue[] = [];
  if (bundle.type !== "document") {
    issues.push(issue("error", "value", "An ePI must use Bundle.type=document", "Bundle.type"));
  }
  const first = bundle.entry[0]?.resource;
  if (first === undefined || !isComposition(first)) {
    issues.push(
      issue(
        "error",
        "structure",
        "A document Bundle must have Composition as its first entry",
        "Bundle.entry[0]",
      ),
    );
  }
  issues.push(...type1GraphIssues(bundle));
  if (issues.length === 0) {
    issues.push(issue("success", "informational", "Canonical Type 1 preflight passed"));
  }
  return { resourceType: "OperationOutcome", issue: issues };
}

export function validateType2Preflight(bundle: FhirBundle): OperationOutcome {
  const issues: OperationOutcomeIssue[] = [];
  if (bundle.type !== "document") {
    issues.push(issue("error", "value", "Type 2 ePI must use Bundle.type=document", "Bundle.type"));
  }

  const first = bundle.entry[0]?.resource;
  if (first === undefined || !isComposition(first)) {
    issues.push(
      issue(
        "error",
        "structure",
        "A document Bundle must have Composition as its first entry",
        "Bundle.entry[0]",
      ),
    );
  }

  const resourceTypes = new Set(bundle.entry.map(({ resource }) => resource.resourceType));
  for (const requiredType of REQUIRED_TYPE2_RESOURCES) {
    if (!resourceTypes.has(requiredType)) {
      issues.push(
        issue("error", "required", `Type 2 graph is missing ${requiredType}`, "Bundle.entry"),
      );
    }
  }

  const duplicateFullUrls = bundle.entry
    .map(({ fullUrl }) => fullUrl)
    .filter((fullUrl, index, all) => all.indexOf(fullUrl) !== index);
  for (const fullUrl of new Set(duplicateFullUrls)) {
    issues.push(
      issue("error", "duplicate", `Duplicate Bundle.entry.fullUrl ${fullUrl}`, "Bundle.entry"),
    );
  }

  if (issues.length === 0) {
    issues.push(issue("success", "informational", "Canonical Type 2 preflight passed"));
  }
  return { resourceType: "OperationOutcome", issue: issues };
}

function codingCode(section: CompositionSection, system: string): string | undefined {
  return section.code.coding?.find((coding) => coding.system === system)?.code;
}

function validateTargetSection(
  section: CompositionSection | undefined,
  rule: SectionRule,
  mapping: EmaMapping,
  path: string,
  issues: OperationOutcomeIssue[],
): void {
  if (section === undefined) {
    issues.push(issue("error", "required", `Missing EMA section ${rule.targetCode}`, path));
    return;
  }
  const actualCode = codingCode(section, mapping.targetCodeSystem);
  if (actualCode !== rule.targetCode) {
    issues.push(
      issue(
        "error",
        "value",
        `Expected EMA code ${rule.targetCode}, received ${actualCode ?? "none"}`,
        `${path}.code`,
      ),
    );
  }
  if (!permittedTitles(rule).includes(section.title)) {
    issues.push(issue("error", "value", `Expected title "${rule.title}"`, `${path}.title`));
  }

  (rule.children ?? []).forEach((childRule, position) => {
    validateTargetSection(
      section.section?.[position],
      childRule,
      mapping,
      `${path}.section[${position}]`,
      issues,
    );
  });
}

export function validateEmaPreflight(
  list: FhirResource,
  bundle: FhirBundle,
  mapping: EmaMapping,
): OperationOutcome {
  const issues: OperationOutcomeIssue[] = [];
  if (!list.meta?.profile?.includes(mapping.profiles.list)) {
    issues.push(issue("error", "value", "EMA List profile is missing", "List.meta.profile"));
  }
  if (!bundle.meta?.profile?.includes(mapping.profiles.bundle)) {
    issues.push(issue("error", "value", "EMA Bundle profile is missing", "Bundle.meta.profile"));
  }

  const composition = bundle.entry[0]?.resource;
  if (composition === undefined || !isComposition(composition)) {
    issues.push(
      issue("error", "structure", "EMA Bundle first entry is not Composition", "Bundle.entry[0]"),
    );
  } else {
    for (const profile of mapping.profiles.composition) {
      if (!composition.meta?.profile?.includes(profile)) {
        issues.push(
          issue(
            "error",
            "value",
            `EMA Composition profile ${profile} is missing`,
            "Composition.meta.profile",
          ),
        );
      }
    }
    validateTargetSection(
      composition.section[0],
      mapping.root,
      mapping,
      "Composition.section[0]",
      issues,
    );
  }

  if (issues.length === 0) {
    issues.push(issue("success", "informational", "EMA structural preflight passed"));
  }
  return { resourceType: "OperationOutcome", issue: issues };
}

export function hasValidationErrors(outcome: OperationOutcome): boolean {
  return outcome.issue.some(({ severity }) => severity === "fatal" || severity === "error");
}
