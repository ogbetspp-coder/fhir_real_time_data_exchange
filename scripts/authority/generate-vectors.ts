import { writeFileSync } from "node:fs";

import { importerVectors } from "../../src/authority/vectors.js";
import { loadEmaMapping } from "../../src/fhir/mapping.js";

// Writes the importer's golden vectors (docs/design/authority-import-contract.md, D10).
const mapping = await loadEmaMapping();
writeFileSync(
  "test/fixtures/authority/vectors.json",
  `${JSON.stringify(importerVectors(mapping), null, 2)}\n`,
);
