import { z } from "zod";

import {
  ApproverRole,
  AttestationMethod,
  Count,
  IsoDateTime,
  PositiveInt,
  PrincipalId,
  RecomputeRequestSchema,
  RecomputeVersionsSchema,
  RecordRef,
  Sha256Hex,
  StorageUri,
  Token,
} from "../contracts/index.js";
import { EU_AUTHORISATION_NUMBER_PATTERN } from "../fhir/standards.js";

// What the certified Word importer reads (docs/design/certified-word-import.md, D1): what a person
// confirmed, and what `python -m zone_a.recompute` wrote. Both closed: any other key or value type
// refuses.

// A label's own text as a person chose it, never retyped (ADR 0006 decision 5): one line of it,
// with no control, format or lone surrogate code point, as short as the gate keeps strings outside
// a narrative.
const LabelText = z
  .string()
  .min(1)
  .max(300)
  .refine((value) => !/[\p{Cc}\p{Cf}\p{Cs}]/u.test(value), "a control or format character");

// Our own ids, as the record's identifier value and the gate write them: a lower-case UUID.
const OurId = z.string().regex(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);

// What a person confirmed for this label (ADR 0006 decisions 4, 5 and 7), an input to the import
// and, through the record, part of the approved content.
export const CertifiedWordRequestSchema = z.strictObject({
  // Where the uploaded .docx is (D4), and its name as uploaded.
  upload: z.strictObject({
    filename: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._ -]{0,254}$/),
    storageUri: StorageUri,
  }),
  // What the recompute was asked: the document, the view, the part, the assignments, the versions.
  recompute: RecomputeRequestSchema,
  // The ePI this label's part is a version of: our id, confirmed once, never derived from the
  // label's position in a file.
  documentId: OurId,
  // The canonical product: our ids, the name and holder exactly as chosen from sections 1 and 7,
  // and every EU authorisation number section 8 states, in `zone_a.product`'s strict form.
  product: z.strictObject({
    id: OurId,
    name: LabelText,
    holder: z.strictObject({ id: OurId, name: LabelText }),
    euAuthorisationNumbers: z
      .array(z.string().regex(new RegExp(EU_AUTHORISATION_NUMBER_PATTERN)))
      .min(1)
      .max(200),
  }),
  // The approval placeholder, as a drawn source's: an attestation by an opaque principal.
  approval: z.strictObject({
    approverId: PrincipalId,
    approverRole: ApproverRole,
    approvedAt: IsoDateTime,
    method: AttestationMethod,
    recordRef: RecordRef.optional(),
  }),
});

export type CertifiedWordRequest = z.infer<typeof CertifiedWordRequestSchema>;

// One section as `zone_a.word_epi` writes it: its title is its heading line; its narrative is
// null and its page empty where it draws nothing. A result holds no refused section: the recompute
// refuses the whole instead. Its key is the registry's (a leaflet's may hold a hyphen); the
// mapping's tree decides which the importer carries.
const SectionKey = z.string().regex(/^[a-z0-9]+(?:[.-][a-z0-9]+)*$/);
const SectionSchema = z.strictObject({
  key: SectionKey,
  parent: SectionKey.nullable(),
  code: Token,
  title: z.string(),
  heading: Count,
  paragraphs: z.tuple([Count, Count]),
  narrative: z.string().nullable(),
  page: z.string(),
  refusal: z.null(),
});

// `zone_a.recompute`'s result. The structure is the structurer's own record: only what binds it
// to the versions and that it is ready is read.
export const RecomputeResultSchema = z.strictObject({
  versions: RecomputeVersionsSchema,
  source: z.strictObject({ sha256: Sha256Hex, bytes: PositiveInt }),
  view: z.enum(["accepted", "original"]).nullable(),
  changes: Count,
  document: z.enum(["smpc", "pl"]),
  part: Count,
  span: z.tuple([Count, Count]),
  structure: z.looseObject({
    structurer: Token,
    registryVersion: Token,
    mappingVersion: Token,
    ready: z.literal(true),
  }),
  sections: z.array(SectionSchema).min(1).max(2_000),
});

export type RecomputeResult = z.infer<typeof RecomputeResultSchema>;
export type RecomputedSection = RecomputeResult["sections"][number];

// The recompute's refusal (`{"refusal": {code, detail}}`, exit status 1).
export const RecomputeRefusalSchema = z.strictObject({
  refusal: z.strictObject({ code: z.string(), detail: z.string() }),
});
