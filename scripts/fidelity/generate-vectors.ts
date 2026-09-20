import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

import { format, resolveConfig } from "prettier";

import {
  NORMALIZATION_VERSION,
  NormalizationError,
  XhtmlError,
  normalizeText,
  verifyNarrativeFidelity,
  xhtmlToText,
} from "../../src/fidelity/index.js";
import { sha256Utf8 } from "../../src/lib/hash.js";
import { normalizationCases, verifyCases, xhtmlCases } from "../../test/fixtures/fidelity/cases.js";

// Golden vectors: the language-neutral, byte-for-byte specification of the fidelity check
// (ADR 0003). A re-implementation must reproduce every `expected` value and `reportHash`.

const output = path.resolve("test/fixtures/fidelity");
await mkdir(output, { recursive: true });

const normalization = normalizationCases.map(({ name, input }) => {
  try {
    return { name, input, expected: normalizeText(input) };
  } catch (error) {
    if (error instanceof NormalizationError)
      return { name, input, expected: { error: error.code } };
    throw error;
  }
});

const xhtml = xhtmlCases.map(({ name, input }) => {
  try {
    return { name, input, expected: xhtmlToText(input) };
  } catch (error) {
    if (error instanceof XhtmlError) return { name, input, expected: { error: error.code } };
    throw error;
  }
});

const verify = verifyCases.map(({ name, input }) => {
  // Fill the provenance hashes the way a conforming extractor would.
  const provenance = input.provenance.map((entry) => {
    const section = input.sections.find(({ sourceKey }) => sourceKey === entry.sourceKey);
    if (section === undefined) return entry;
    try {
      return {
        ...entry,
        normalizedTextSha256: sha256Utf8(normalizeText(xhtmlToText(section.div))),
      };
    } catch {
      return entry;
    }
  });
  const filled = { ...input, provenance };
  return { name, input: filled, expected: verifyNarrativeFidelity(filled) };
});

const vectors = { normalizationVersion: NORMALIZATION_VERSION, normalization, xhtml, verify };
const prettierOptions = { ...(await resolveConfig(output)), parser: "json" as const };
await writeFile(
  path.join(output, "vectors.json"),
  await format(JSON.stringify(vectors, null, 2), prettierOptions),
);

console.log(
  `Generated ${normalization.length} normalization, ${xhtml.length} xhtml, and ${verify.length} verify vectors`,
);
