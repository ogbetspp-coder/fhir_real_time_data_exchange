import type { EmaMapping, SectionRule } from "./mapping.js";
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
  if (section.title !== rule.title) {
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
