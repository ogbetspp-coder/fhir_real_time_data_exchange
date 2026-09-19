import { readFile } from "node:fs/promises";
import path from "node:path";

import { z } from "zod";

const SectionRuleSchema: z.ZodType<SectionRule> = z.lazy(() =>
  z.object({
    sourceKey: z.string().min(1),
    targetCode: z.string().min(1),
    title: z.string().min(1),
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
  title: string;
  required: boolean;
  children?: SectionRule[] | undefined;
};

export type EmaMapping = z.infer<typeof MappingSchema>;

export async function loadEmaMapping(
  mappingPath = path.resolve("fhir/mappings/cap-smpc-en.json"),
): Promise<EmaMapping> {
  const content = await readFile(mappingPath, "utf8");
  return MappingSchema.parse(JSON.parse(content));
}
