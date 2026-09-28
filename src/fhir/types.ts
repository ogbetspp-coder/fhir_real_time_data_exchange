export type FhirMeta = {
  profile?: string[];
  tag?: Coding[];
  versionId?: string;
  lastUpdated?: string;
};

export type Coding = {
  system?: string;
  code?: string;
  display?: string;
};

export type CodeableConcept = {
  coding?: Coding[];
  text?: string;
};

export type Identifier = {
  system?: string;
  value?: string;
};

export type Reference = {
  reference?: string;
  type?: string;
  display?: string;
};

export type Narrative = {
  status: "generated" | "extensions" | "additional" | "empty";
  div: string;
};

export type FhirResource = {
  resourceType: string;
  id?: string;
  meta?: FhirMeta;
  language?: string;
  [key: string]: unknown;
};

export type BundleEntry = {
  fullUrl: string;
  resource: FhirResource;
};

export type FhirBundle = FhirResource & {
  resourceType: "Bundle";
  identifier: Identifier;
  type: "document" | "collection" | "transaction";
  timestamp: string;
  entry: BundleEntry[];
};

export type CompositionSection = {
  id?: string;
  title: string;
  code: CodeableConcept;
  text?: Narrative;
  section?: CompositionSection[];
};

export type FhirComposition = FhirResource & {
  resourceType: "Composition";
  identifier?: Identifier[];
  status: string;
  type: CodeableConcept;
  subject: Reference[];
  date: string;
  author: Reference[];
  title: string;
  section: CompositionSection[];
};

export type OperationOutcomeIssue = {
  severity: "fatal" | "error" | "warning" | "information" | "success";
  code: string;
  diagnostics?: string;
  expression?: string[];
};

export type OperationOutcome = FhirResource & {
  resourceType: "OperationOutcome";
  issue: OperationOutcomeIssue[];
};

export function isComposition(resource: FhirResource): resource is FhirComposition {
  return resource.resourceType === "Composition" && Array.isArray(resource.section);
}
