import { writeFileSync } from "node:fs";

import { importerVectors } from "../../src/certified-word/vectors.js";
import { loadEmaMappings } from "../../src/fhir/mapping.js";

// Writes the certified Word importer's golden vectors (docs/design/certified-word-import.md, D1):
// what it makes of each recompute result zone-a/scripts/certified_word_fixtures.py committed, and
// where it refuses changed copies.
const mappings = await loadEmaMappings();
writeFileSync(
  "test/fixtures/certified-word/vectors.json",
  `${JSON.stringify({ imports: importerVectors(Object.values(mappings)) }, null, 2)}\n`,
);
