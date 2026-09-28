import { createHash } from "node:crypto";
import { writeFileSync } from "node:fs";

import { NormalizationError, normalizeText } from "../../src/fidelity/normalize.js";
import { XhtmlError, xhtmlToText } from "../../src/fidelity/xhtml.js";

// The exhaustive parity sweep (audit 2026-09-27, F-4), run nightly by
// .github/workflows/parity-sweep.yml: every code point, U+0000 to U+10FFFF but the surrogates, as
// a character reference inside `sup`, inside `sub` and after an inline tag, and raw in three
// normalisation contexts (a line start before whitespace, a word, a table row; after a letter
// and before a combining mark, then a bullet line). For each, the digest of what the TypeScript
// gives. zone-a/tests/test_parity_sweep.py computes the same with the Python port and compares.
// The `codePoints` vectors hold the closed lists to the table on every run; this holds the whole
// scanner and normalisation to each other at every code point, which is too slow for every run.
//
// usage: tsx scripts/fidelity/parity-sweep.ts --out FILE
//
// Each line is the code point in hex, a tab, and 16 hex digits of the digest: no text.

const ROOT_OPEN = '<div xmlns="http://www.w3.org/1999/xhtml">';
const COMBINING_ACUTE = String.fromCodePoint(0x0301);
const BULLET = String.fromCodePoint(0x2022);

function outcome(run: () => string): string {
  try {
    return run();
  } catch (error) {
    if (error instanceof XhtmlError || error instanceof NormalizationError) {
      return `E:${error.code}`;
    }
    throw error;
  }
}

export function sweepDigest(codePoint: number): string {
  const hex = codePoint.toString(16);
  const character = String.fromCodePoint(codePoint);
  const root = (inner: string): string => `${ROOT_OPEN}${inner}</div>`;
  const parts = [
    outcome(() => xhtmlToText(root(`<p>x<sup>&#x${hex};</sup></p>`))),
    outcome(() => xhtmlToText(root(`<p>x<sub>&#x${hex};</sub></p>`))),
    outcome(() => xhtmlToText(root(`<p>a<b>&#x${hex};</b></p>`))),
    outcome(() => normalizeText(`\n${character} a${character}\t${character}`)),
    outcome(() => normalizeText(`e${character}${COMBINING_ACUTE}\n${BULLET} ${character}`)),
  ];
  return createHash("sha256").update(JSON.stringify(parts)).digest("hex").slice(0, 16);
}

const outPosition = process.argv.indexOf("--out");
const out = outPosition === -1 ? undefined : process.argv[outPosition + 1];
if (out === undefined) throw new Error("--out FILE is required");

const lines: string[] = [];
for (let codePoint = 0; codePoint <= 0x10ffff; codePoint += 1) {
  if (codePoint >= 0xd800 && codePoint <= 0xdfff) continue;
  lines.push(`${codePoint.toString(16)}\t${sweepDigest(codePoint)}`);
}
writeFileSync(out, `${lines.join("\n")}\n`, "utf8");
process.stderr.write(`parity sweep: ${lines.length} code points\n`);
