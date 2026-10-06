import { permittedTitles, type EmaMapping, type SectionRule } from "./mapping.js";
import {
  EU_AUTHORISATION_NUMBER_PATTERN,
  EU_AUTHORISATION_NUMBER_SYSTEM,
  EU_PRODUCT_NUMBER_PATTERN,
  EU_PRODUCT_NUMBER_SYSTEM,
} from "./standards.js";
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
// Composition, the product scope, its holder and its authorisations, and nothing else: exactly one
// of each but the RegulatedAuthorization, of which there is one per authorisation
// (docs/design/version-identity.md: one per EU authorisation number). Packs, items, ingredients
// and substances are declared not supplied by their absence; none may be inferred.
const TYPE1_RESOURCES = [
  "Composition",
  "MedicinalProductDefinition",
  "Organization",
  "RegulatedAuthorization",
] as const;
const TYPE1_MANY: readonly string[] = ["RegulatedAuthorization"];

type GraphType = "type1" | "type2";

function referenceOf(value: unknown): string | undefined {
  const reference = (value as { reference?: unknown } | undefined)?.reference;
  return typeof reference === "string" ? reference : undefined;
}

// The one reference a Type 1 link has: a list of exactly one, or a single Reference.
function onlyReference(value: unknown): string | undefined {
  if (!Array.isArray(value)) return referenceOf(value);
  return value.length === 1 ? referenceOf(value[0]) : undefined;
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
    } else if (entries.length !== 1 && !TYPE1_MANY.includes(resourceType)) {
      issues.push(
        issue("error", "structure", `A Type 1 record has one ${resourceType}`, "Bundle.entry"),
      );
    }
  }
  const all = (type: (typeof TYPE1_RESOURCES)[number]) => {
    const entries = byType.get(type);
    if (entries === undefined) {
      issues.push(issue("error", "required", `Type 1 record is missing ${type}`, "Bundle.entry"));
    }
    return entries ?? [];
  };
  const one = (type: (typeof TYPE1_RESOURCES)[number]) => {
    const entries = all(type);
    return entries.length === 1 ? entries[0] : undefined;
  };
  const composition = one("Composition");
  const product = one("MedicinalProductDefinition");
  const holder = one("Organization");
  const authorisations = all("RegulatedAuthorization");
  if (composition === undefined || product === undefined || holder === undefined) return issues;

  const expectLink = (actual: string | undefined, expected: string, where: string): void => {
    if (actual !== expected) {
      issues.push(issue("error", "value", `${where} must reference its Type 1 target`, where));
    }
  };
  expectLink(onlyReference(composition.resource.subject), product.fullUrl, "Composition.subject");
  expectLink(onlyReference(composition.resource.author), holder.fullUrl, "Composition.author");
  for (const authorisation of authorisations) {
    expectLink(
      onlyReference(authorisation.resource.subject),
      product.fullUrl,
      "RegulatedAuthorization.subject",
    );
    expectLink(
      referenceOf(authorisation.resource.holder),
      holder.fullUrl,
      "RegulatedAuthorization.holder",
    );
  }
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
    ...authorisations.map(
      ({ resource }) => [resource, "RegulatedAuthorization.identifier"] as const,
    ),
  ] as const) {
    if (!hasIdentifier(resource)) {
      issues.push(issue("error", "required", `${where} is required`, where));
    }
  }
  return issues;
}

// EU authorisation numbers in either graph (docs/design/version-identity.md): every value in the
// two systems has its strict form; only a RegulatedAuthorization carries an authorisation number,
// one at most, and no two the same one; only a MedicinalProductDefinition carries a product number;
// and the product numbers are exactly the authorisation numbers' products. The package's
// invariants khs-eu-1 to khs-eu-4 (scripts/fhir/generate-artifacts.ts) state the same rules for
// the official validator.
function euNumberIssues(bundle: FhirBundle): OperationOutcomeIssue[] {
  const issues: OperationOutcomeIssue[] = [];
  const numbers = (resource: FhirResource, system: string): unknown[] =>
    (Array.isArray(resource.identifier) ? (resource.identifier as unknown[]) : [])
      .filter((entry) => (entry as { system?: unknown } | null)?.system === system)
      .map((entry) => (entry as { value?: unknown }).value);
  const authorisationNumbers: string[] = [];
  const productNumbers = new Set<string>();
  const rules = [
    [EU_AUTHORISATION_NUMBER_SYSTEM, EU_AUTHORISATION_NUMBER_PATTERN, "RegulatedAuthorization"],
    [EU_PRODUCT_NUMBER_SYSTEM, EU_PRODUCT_NUMBER_PATTERN, "MedicinalProductDefinition"],
  ] as const;
  for (const { resource } of bundle.entry) {
    for (const [system, pattern, carrier] of rules) {
      const values = numbers(resource, system);
      if (values.length === 0) continue;
      const where = `${resource.resourceType}.identifier`;
      if (resource.resourceType !== carrier) {
        issues.push(issue("error", "structure", `Only a ${carrier} carries ${system}`, where));
      }
      for (const value of values) {
        if (typeof value !== "string" || !new RegExp(pattern).test(value)) {
          issues.push(
            issue("error", "value", `An identifier in ${system} is not ${pattern}`, where),
          );
        } else if (system === EU_PRODUCT_NUMBER_SYSTEM) {
          productNumbers.add(value);
        } else {
          authorisationNumbers.push(value);
        }
      }
      if (system === EU_AUTHORISATION_NUMBER_SYSTEM && values.length > 1) {
        issues.push(
          issue(
            "error",
            "structure",
            "A RegulatedAuthorization has one EU authorisation number",
            where,
          ),
        );
      }
    }
  }
  if (new Set(authorisationNumbers).size !== authorisationNumbers.length) {
    issues.push(
      issue(
        "error",
        "duplicate",
        "Two RegulatedAuthorizations carry one EU authorisation number",
        "RegulatedAuthorization.identifier",
      ),
    );
  }
  const products = new Set(authorisationNumbers.map((value) => value.replace(/\/[0-9]{3}$/, "")));
  if (
    products.size !== productNumbers.size ||
    [...products].some((product) => !productNumbers.has(product))
  ) {
    issues.push(
      issue(
        "error",
        "value",
        "The EU product numbers are not exactly those of the EU authorisation numbers",
        "MedicinalProductDefinition.identifier",
      ),
    );
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
  issues.push(...type1GraphIssues(bundle), ...euNumberIssues(bundle));
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
  issues.push(...euNumberIssues(bundle));

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

  // Each child rule finds its section by EMA code, not by position: the transform leaves out an
  // absent optional child, so a positional match would compare every later child with its rule's
  // neighbour. The children must still be in the manifest's order.
  const children = section.section ?? [];
  let previous = -1;
  for (const childRule of rule.children ?? []) {
    const positions = children.flatMap((child, position) =>
      codingCode(child, mapping.targetCodeSystem) === childRule.targetCode ? [position] : [],
    );
    const [position, ...others] = positions;
    if (position === undefined) {
      if (childRule.required) {
        validateTargetSection(undefined, childRule, mapping, `${path}.section`, issues);
      }
      continue;
    }
    if (others.length > 0) {
      issues.push(
        issue(
          "error",
          "duplicate",
          `Duplicate EMA section ${childRule.targetCode}`,
          `${path}.section`,
        ),
      );
    }
    if (position < previous) {
      issues.push(
        issue(
          "error",
          "structure",
          `EMA section ${childRule.targetCode} is out of the manifest's order`,
          `${path}.section[${position}]`,
        ),
      );
    }
    previous = Math.max(previous, position);
    validateTargetSection(
      children[position],
      childRule,
      mapping,
      `${path}.section[${position}]`,
      issues,
    );
  }
  // A child no rule names is refused rather than passed over unchecked.
  const ruled = new Set((rule.children ?? []).map(({ targetCode }) => targetCode));
  children.forEach((child, position) => {
    const code = codingCode(child, mapping.targetCodeSystem);
    if (code === undefined || !ruled.has(code)) {
      issues.push(
        issue(
          "error",
          "structure",
          `Unexpected EMA section ${code ?? "without an EMA code"}`,
          `${path}.section[${position}]`,
        ),
      );
    }
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
