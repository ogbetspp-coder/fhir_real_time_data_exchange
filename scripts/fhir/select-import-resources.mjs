#!/usr/bin/env node

import { cp, mkdir, readdir, readFile } from "node:fs/promises";
import path from "node:path";

const [sourceArg, destinationArg] = process.argv.slice(2);
if (!sourceArg || !destinationArg) {
  throw new Error("Usage: node select-import-resources.mjs SOURCE_DIR DESTINATION_DIR");
}

const allowedTypes = new Set([
  "CodeSystem",
  "ConceptMap",
  "ImplementationGuide",
  "NamingSystem",
  "OperationDefinition",
  "SearchParameter",
  "StructureDefinition",
  "ValueSet",
]);

// Resources that are structurally invalid per base FHIR R5 rules and that Google
// Cloud Healthcare API's FHIR store import rejects outright, unrelated to this
// project's EU ePI/Type 2 interoperability content. Each entry is the upstream
// HL7 package's own resource id, not something authored here.
const excludedResourceIds = new Set([
  // hl7.fhir.uv.extensions.r5: its root element sets label/code/requirements,
  // violating the base StructureDefinition FHIRPath constraint that those may
  // only be set on non-root elements. Healthcare API import error:
  // "failed FHIRPath constraint: fhirpath-constraint-violation-StructureDefinition".
  "operationoutcome-instance-id",
]);

const source = path.resolve(sourceArg);
const destination = path.resolve(destinationArg);
await mkdir(destination, { recursive: true });

let copied = 0;
for (const name of await readdir(source)) {
  if (!name.endsWith(".json") || name === "package.json" || name.startsWith(".")) continue;
  const input = path.join(source, name);
  try {
    const resource = JSON.parse(await readFile(input, "utf8"));
    if (!allowedTypes.has(resource.resourceType)) continue;
    if (excludedResourceIds.has(resource.id)) continue;
    await cp(input, path.join(destination, name));
    copied += 1;
  } catch (error) {
    console.warn(`Skipping ${name}: ${error instanceof Error ? error.message : String(error)}`);
  }
}

if (copied === 0) throw new Error(`No conformance resources found in ${source}`);
console.log(`Prepared ${copied} conformance resources in ${destination}`);
