import { z } from "zod";

import { CanonicalUri, IsoDateTime, NonEmptyString } from "./common.js";

// Deliberately loose: FHIR resources are open by nature and `src/fhir/preflight.ts` remains the
// authority on the graph's structure. This schema only pins the handful of fields the contract
// invariants and hashes rely on, so a submission cannot be a non-document or an empty Bundle.

// Strict and grammar-limited: the identifier and the first entry's fullUrl are copied into the
// Provenance resource, so they must not be able to carry free text.
// The value is required: every id the run persists derives from it
// (docs/design/authority-import-contract.md, D7).
const BundleIdentifierSchema = z.strictObject({
  system: CanonicalUri.optional(),
  value: z.string().regex(/^[A-Za-z0-9._:-]{1,128}$/),
});

const BundleEntrySchema = z.looseObject({
  fullUrl: CanonicalUri,
  resource: z.looseObject({
    resourceType: NonEmptyString,
    id: NonEmptyString.optional(),
  }),
});

const NarrativeSchema = z.looseObject({
  status: z.enum(["generated", "extensions", "additional", "empty"]),
  div: z.string(),
});

const CodingSchema = z.looseObject({
  system: z.string().optional(),
  code: z.string().optional(),
});

export type LooseSection = {
  code?: { coding?: z.infer<typeof CodingSchema>[] | undefined } | undefined;
  text?: z.infer<typeof NarrativeSchema> | undefined;
  section?: LooseSection[] | undefined;
};

const SectionSchema: z.ZodType<LooseSection> = z.lazy(() =>
  z.looseObject({
    code: z.looseObject({ coding: z.array(CodingSchema).optional() }).optional(),
    text: NarrativeSchema.optional(),
    section: z.array(SectionSchema).optional(),
  }),
);

// Validates the section tree of the first entry before any code walks it, so a malformed
// Composition is a contract rejection rather than a runtime TypeError.
export const LooseCompositionSchema = z.looseObject({
  resourceType: z.literal("Composition"),
  section: z.array(SectionSchema),
});

export const CanonicalBundleSchema = z
  .looseObject({
    resourceType: z.literal("Bundle"),
    id: NonEmptyString.optional(),
    type: z.literal("document"),
    identifier: BundleIdentifierSchema,
    timestamp: IsoDateTime,
    entry: z.array(BundleEntrySchema).min(1).max(500),
  })
  .meta({
    id: "CanonicalBundle",
    description:
      "HL7 Global ePI document Bundle (FHIR R5), Type 2 or, for an authority import, Type 1. Structural validation happens in Zone B preflight and the HL7 validator.",
  });

export type CanonicalBundle = z.infer<typeof CanonicalBundleSchema>;
