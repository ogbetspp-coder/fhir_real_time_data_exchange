import { readFile } from "node:fs/promises";
import path from "node:path";

import { z } from "zod";

// The manifest's name. It is the basename of the default manifest file and the id lineage and
// the generated ConceptMap and StructureMap carry; the manifest itself names only its version.
export const EMA_MAPPING_ID = "cap-smpc-en";

const SectionRuleSchema: z.ZodType<SectionRule> = z.lazy(() =>
  z.object({
    sourceKey: z.string().min(1),
    targetCode: z.string().min(1),
    title: z.string().min(1),
    display: z.string().min(1).optional(),
    required: z.boolean(),
    children: z.array(SectionRuleSchema).optional(),
  }),
);

const MappingSchema = z.object({
  mappingVersion: z.string().min(1),
  sourceCodeSystem: z.url(),
  targetCodeSystem: z.url(),
  profiles: z.object({
    list: z.url(),
    bundle: z.url(),
    composition: z.array(z.url()).min(1),
  }),
  root: SectionRuleSchema,
});

export type SectionRule = {
  sourceKey: string;
  targetCode: string;
  // The section heading the label carries (Composition.section.title).
  title: string;
  // The target code system's own display string for `targetCode`, when it differs from the
  // heading. The validator rejects any other display on the coding; the heading is not bound.
  display?: string | undefined;
  required: boolean;
  children?: SectionRule[] | undefined;
};

export type EmaMapping = z.infer<typeof MappingSchema>;

// Every sourceKey and every targetCode may appear once in the whole rule tree. A repeated
// sourceKey would publish one source section under two headings; a repeated targetCode would
// publish two source sections under one. Either is a manifest error, not a run-time choice.
export function duplicateRuleIssues(root: SectionRule): string[] {
  const sourceKeys = new Set<string>();
  const targetCodes = new Set<string>();
  const issues: string[] = [];
  const visit = (rule: SectionRule): void => {
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
  return issues;
}

// The mapping as lineage names it: the manifest's id and the version the loaded manifest
// declares, which is the same `mappingVersion` the run manifest records.
export function mappingReference(mapping: EmaMapping): string {
  return `${EMA_MAPPING_ID}#${mapping.mappingVersion}`;
}

export async function loadEmaMapping(
  mappingPath = path.resolve(`fhir/mappings/${EMA_MAPPING_ID}.json`),
): Promise<EmaMapping> {
  const content = await readFile(mappingPath, "utf8");
  const mapping = MappingSchema.parse(JSON.parse(content));
  const duplicates = duplicateRuleIssues(mapping.root);
  if (duplicates.length > 0) {
    throw new Error(`Mapping manifest ${mappingPath} is invalid: ${duplicates.join("; ")}`);
  }
  return mapping;
}
