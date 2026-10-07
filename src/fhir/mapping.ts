import { readFile } from "node:fs/promises";
import path from "node:path";

import { z } from "zod";

// The SmPC manifest's name: the basename of the default manifest file and, with the manifest's
// version, the mapping lineage records. The manifest itself names only its version. The
// generated ConceptMap and StructureMap spell the same name in their own literals
// (scripts/fhir/generate-artifacts.ts).
export const EMA_MAPPING_ID = "cap-smpc-en";

// The canonical record's document type (Composition.type), a code system of our own package.
export const DOCUMENT_TYPE_SYSTEM = "https://khs.dev/fhir/CodeSystem/document-type";
// The EMA's document type code system, as the pinned EUePI package defines it, and the SPOR form
// of its URL an authority's published ePI writes.
export const EMA_DOCUMENT_TYPE_SYSTEM = "http://ema.europa.eu/fhir/CodeSystem/100000155531";
const EMA_DOCUMENT_TYPE_ALIAS = "https://spor.ema.europa.eu/v1/lists/100000155531/terms/";

// Each document a manifest maps, by the manifest's root key: the manifest's name, and the code and
// display the EMA's document type code system gives the document (CodeSystem 100000155531 of the
// pinned EUePI package, which EUEpiCompositionSmPC and EUEpiCompositionPackageLeaflet fix;
// test/official/profile-slots.test.ts reads all three there). Our own document type code system
// takes the root key as its code and the same display.
export const DOCUMENTS = {
  smpc: {
    mappingId: EMA_MAPPING_ID,
    emaCode: "100000155532",
    display: "Summary of Product Characteristics",
  },
  pl: { mappingId: "cap-pl-en", emaCode: "100000155538", display: "Package Leaflet" },
} as const;
export type DocumentKey = keyof typeof DOCUMENTS;

function isDocumentKey(key: unknown): key is DocumentKey {
  return typeof key === "string" && Object.hasOwn(DOCUMENTS, key);
}

// The document a manifest maps, by its root key; undefined for a manifest of another.
export function documentOf(mapping: EmaMapping): (typeof DOCUMENTS)[DocumentKey] | undefined {
  const key = mapping.root.sourceKey;
  return isDocumentKey(key) ? DOCUMENTS[key] : undefined;
}

const SectionRuleSchema: z.ZodType<SectionRule> = z.lazy(() =>
  z.object({
    sourceKey: z.string().min(1),
    targetCode: z.string().min(1),
    title: z.string().min(1),
    alternativeTitles: z.array(z.string().min(1)).min(1).optional(),
    display: z.string().min(1).optional(),
    required: z.boolean(),
    narrative: z.literal("required").optional(),
    children: z.array(SectionRuleSchema).optional(),
  }),
);

// A slot of the EMA profile that no rule maps: its canonical key, the EMA code, a heading for the
// key, and why the crosswalk does not carry it. The ConceptMap publishes it as noMap, and the
// crosswalk refuses a source section with its key (no rule has it).
const UnmappedSchema = z.object({
  sourceKey: z.string().min(1),
  targetCode: z.string().min(1),
  title: z.string().min(1),
  reason: z.string().min(1),
});

const MappingSchema = z.object({
  mappingVersion: z.string().min(1),
  sourceCodeSystem: z.url(),
  targetCodeSystem: z.url(),
  // Other URIs by which authorities write the same code system (the EMA's live ePIs use the SPOR
  // list's URI): an authority import reads them as targetCodeSystem, and only there.
  targetCodeSystemAliases: z.array(z.url()).optional(),
  profiles: z.object({
    list: z.url(),
    bundle: z.url(),
    composition: z.array(z.url()).min(1),
  }),
  root: SectionRuleSchema,
  unmapped: z.array(UnmappedSchema).optional(),
});

export type SectionRule = {
  sourceKey: string;
  targetCode: string;
  // The section heading the label carries (Composition.section.title).
  title: string;
  // Headings the QRD template also permits: the heading without its optional wording ("6.5
  // Nature and contents of container"). An authority import carries the heading its label has,
  // one of these or `title`; the crosswalk and the EMA preflight accept any of them.
  alternativeTitles?: string[] | undefined;
  // The target code system's own display string for `targetCode`, when it differs from the
  // heading. The validator rejects any other display on the coding; the heading is not bound.
  display?: string | undefined;
  required: boolean;
  // A leaf rule's section must always carry narrative. A rule with children must too when this
  // is "required": its QRD section has text of its own above its subsections (4.8 Undesirable
  // effects above "Reporting of suspected adverse reactions"). Absent, such a section may be a
  // bare heading.
  narrative?: "required" | undefined;
  children?: SectionRule[] | undefined;
};

export type EmaMapping = z.infer<typeof MappingSchema>;

// Which title a target section takes: the source's where the QRD template permits it, else the
// template's; or, for a certified Word source, the source's as written (ADR 0006 decision 4,
// docs/design/certified-word-import.md, D6): its label's heading line, a heading the label team
// accepted included, never replaced by the template's. The crosswalk and the EMA preflight read it.
export type TitleRule = "template" | "as-written";

// Every heading a rule permits: its title first, then the QRD template's shorter forms.
export function permittedTitles(rule: SectionRule): string[] {
  return [rule.title, ...(rule.alternativeTitles ?? [])];
}

// Every sourceKey and every targetCode may appear once in the whole rule tree and its unmapped
// slots. A repeated sourceKey would publish one source section under two headings; a repeated
// targetCode would publish two source sections under one; a key both mapped and unmapped would
// say two things. Either is a manifest error, not a run-time choice.
export function duplicateRuleIssues(
  root: SectionRule,
  unmapped: readonly { sourceKey: string; targetCode: string }[] = [],
): string[] {
  const sourceKeys = new Set<string>();
  const targetCodes = new Set<string>();
  const issues: string[] = [];
  const visit = (rule: {
    sourceKey: string;
    targetCode: string;
    children?: SectionRule[] | undefined;
  }): void => {
    if (sourceKeys.has(rule.sourceKey)) {
      issues.push(`Duplicate sourceKey ${rule.sourceKey} in mapping manifest`);
    }
    if (targetCodes.has(rule.targetCode)) {
      issues.push(`Duplicate targetCode ${rule.targetCode} in mapping manifest`);
    }
    sourceKeys.add(rule.sourceKey);
    targetCodes.add(rule.targetCode);
    (rule.children ?? []).forEach(visit);
  };
  visit(root);
  unmapped.forEach(visit);
  return issues;
}

// The mapping as lineage names it: the manifest's id and the version the loaded manifest
// declares, which is the same `mappingVersion` the run manifest records.
export function mappingReference(mapping: EmaMapping): string {
  return `${mappingId(mapping)}#${mapping.mappingVersion}`;
}

// The manifest's name, by the document it maps (the SmPC's for a manifest of no known document,
// as before there was a second).
export function mappingId(mapping: EmaMapping): string {
  return documentOf(mapping)?.mappingId ?? EMA_MAPPING_ID;
}

export async function loadEmaMapping(
  mappingPath = path.resolve(`fhir/mappings/${EMA_MAPPING_ID}.json`),
): Promise<EmaMapping> {
  const content = await readFile(mappingPath, "utf8");
  const mapping = MappingSchema.parse(JSON.parse(content));
  const duplicates = duplicateRuleIssues(mapping.root, mapping.unmapped);
  if (duplicates.length > 0) {
    throw new Error(`Mapping manifest ${mappingPath} is invalid: ${duplicates.join("; ")}`);
  }
  return mapping;
}

// Every manifest Zone B carries, one per document: the SmPC's and the package leaflet's.
export async function loadEmaMappings(): Promise<EmaMapping[]> {
  return Promise.all(
    Object.values(DOCUMENTS).map(({ mappingId: id }) =>
      loadEmaMapping(path.resolve(`fhir/mappings/${id}.json`)),
    ),
  );
}

// The documents a source's Composition.type names: our own code (smpc, pl), or the EMA's, in
// either form of its URL (an authority import keeps the EMA's). A source read from a store or a
// submission is not checked against the type, so nothing here is assumed of its shape.
export function sourceDocumentTypes(bundle: unknown): Set<string> {
  const entry = (bundle as { entry?: unknown } | null | undefined)?.entry;
  const first = Array.isArray(entry) ? (entry[0] as { resource?: unknown } | undefined) : undefined;
  const type = (first?.resource as { type?: { coding?: unknown } } | undefined)?.type;
  const codings = Array.isArray(type?.coding) ? (type.coding as unknown[]) : [];
  const named = new Set<string>();
  for (const coding of codings) {
    const { system, code } = (coding ?? {}) as { system?: unknown; code?: unknown };
    if (system === DOCUMENT_TYPE_SYSTEM) {
      named.add(typeof code === "string" ? code : "");
    } else if (system === EMA_DOCUMENT_TYPE_SYSTEM || system === EMA_DOCUMENT_TYPE_ALIAS) {
      const found = Object.entries(DOCUMENTS).find(([, { emaCode }]) => emaCode === code);
      named.add(found?.[0] ?? "");
    }
  }
  return named;
}

// The manifest for a source: the one of `mappings` whose document the source's Composition.type
// names, and no other. A source that names none, or two, or one no manifest maps, has none: it is
// refused, never mapped by a guess.
export function mappingFor(
  bundle: unknown,
  mappings: readonly EmaMapping[],
): EmaMapping | undefined {
  const [only, ...others] = sourceDocumentTypes(bundle);
  if (only === undefined || others.length > 0) return undefined;
  const found = mappings.filter((mapping) => mapping.root.sourceKey === only);
  return found.length === 1 ? found[0] : undefined;
}
