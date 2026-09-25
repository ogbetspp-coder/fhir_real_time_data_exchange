import { writeFileSync } from "node:fs";

import { transformSection, TRefusal } from "../../src/authority/t/transform.js";
import { importerVectors, labelSectionVectors } from "../../src/authority/vectors.js";
import { loadEmaMapping } from "../../src/fhir/mapping.js";
import { T_CASES } from "../../test/fixtures/authority/t-cases.js";

// Writes the importer's golden vectors (docs/design/authority-import-contract.md, D10;
// docs/design/authority-import-t.md, T7): the imports, T's outcome for every section of every
// pinned label, and T's cases.
const ROOT = '<div xmlns="http://www.w3.org/1999/xhtml">';
// Every committed narrative is synthetic and says so (test/synthetic-only.test.ts).
const MARKED = "<p>not for clinical use</p></div>";
const transforms = T_CASES.map(({ name, inner, evidence, root }) => {
  const div = `${root ?? ROOT}${inner}${MARKED}`;
  try {
    const result = transformSection(div, evidence === undefined ? undefined : new Set(evidence));
    return { name, div, evidence: evidence ?? null, output: result.div };
  } catch (error) {
    if (error instanceof TRefusal)
      return { name, div, evidence: evidence ?? null, refused: error.reason };
    throw error;
  }
});
const mapping = await loadEmaMapping();
writeFileSync(
  "test/fixtures/authority/vectors.json",
  `${JSON.stringify(
    { imports: importerVectors(mapping), sections: labelSectionVectors(), transforms },
    null,
    2,
  )}\n`,
);
