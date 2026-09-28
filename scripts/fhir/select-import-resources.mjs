#!/usr/bin/env node
// The conformance resources of one FHIR package that the deploy imports into the target store.
//
//   node select-import-resources.mjs SOURCE_DIR DESTINATION_DIR
//   node select-import-resources.mjs --types
//
// --types prints the resource types imported, space-separated: the types scripts/gcp/bootstrap.sh
// reconciles the store against, and the only ones it may delete from it. Node built-ins only.

import { cp, mkdir, readdir, readFile } from "node:fs/promises";
import path from "node:path";

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

if (process.argv[2] === "--types") {
  console.log([...allowedTypes].join(" "));
  process.exit(0);
}

const [sourceArg, destinationArg] = process.argv.slice(2);
if (!sourceArg || !destinationArg) {
  throw new Error("Usage: node select-import-resources.mjs SOURCE_DIR DESTINATION_DIR | --types");
}

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

// Files of a pinned package that are known not to parse as JSON and are skipped by name, each
// with the reason. Empty: every JSON file of the four pinned packages parsed on 2026-09-28. Any
// other file that does not parse fails the import rather than being skipped with a warning
// (audit B08, D-7): a package that no longer parses is a changed package, and dropping part of it
// quietly would import an incomplete profile set.
const unparsableFiles = new Set([]);

const source = path.resolve(sourceArg);
const destination = path.resolve(destinationArg);
await mkdir(destination, { recursive: true });

let copied = 0;
const unreadable = [];
for (const name of await readdir(source)) {
  if (!name.endsWith(".json") || name === "package.json" || name.startsWith(".")) continue;
  const input = path.join(source, name);
  let resource;
  try {
    resource = JSON.parse(await readFile(input, "utf8"));
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    if (unparsableFiles.has(name)) {
      console.warn(`Skipping ${name}, listed as unparsable: ${reason}`);
    } else {
      unreadable.push(`${name}: ${reason}`);
    }
    continue;
  }
  if (!allowedTypes.has(resource?.resourceType)) continue;
  if (excludedResourceIds.has(resource.id)) continue;
  await cp(input, path.join(destination, name));
  copied += 1;
}

if (unreadable.length > 0) {
  throw new Error(
    `${source}: ${unreadable.length} file(s) do not parse:\n  ${unreadable.join("\n  ")}`,
  );
}
if (copied === 0) throw new Error(`No conformance resources found in ${source}`);
console.log(`Prepared ${copied} conformance resources in ${destination}`);
