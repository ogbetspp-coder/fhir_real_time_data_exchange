import { z } from "zod";

// The one way a contract's JSON Schema is generated (scripts/contracts/generate-schemas.ts writes
// it; test/contracts/schema-generation.test.ts reproduces it).
//
// Published patterns use no shorthand character class (audit B07 follow-up, Low-2). JSON Schema
// reads a pattern as an ECMA-262 regular expression, where `\d` is exactly [0-9], and Zod (Zone B)
// agrees; but Pydantic, which Zone A's generated models validate with, compiles patterns with
// Rust's regex crate, where `\d` is any Unicode decimal digit: Zone A accepted a package version
// or a normalisation version written in Arabic-Indic or fullwidth digits that Zone B refuses. The
// contracts' own patterns are written with [0-9]; the one Zod writes itself (the ISO date-time
// pattern of `z.iso.datetime`) is rewritten here to the same ASCII class, which is the same
// language under the schema's own dialect. Any other shorthand (\w, \s, \b and their negations,
// \D) would diverge the same way, so generation refuses it rather than publishing it.

// `\d` as `[0-9]` (or `0-9` inside a class), leaving every other escape as it is.
export function asciiDigits(pattern: string): string {
  let out = "";
  let inClass = false;
  for (let index = 0; index < pattern.length; index += 1) {
    const char = pattern[index] ?? "";
    if (char === "\\") {
      const next = pattern[index + 1] ?? "";
      index += 1;
      if (next === "d") out += inClass ? "0-9" : "[0-9]";
      else if (/[DwWsSbB]/.test(next)) {
        throw new Error(`pattern ${pattern} uses \\${next}, which Zone A's regex reads as Unicode`);
      } else out += `\\${next}`;
      continue;
    }
    if (char === "[" && !inClass) inClass = true;
    else if (char === "]" && inClass) inClass = false;
    out += char;
  }
  return out;
}

// Every `pattern` of the document, at any depth: a string with two `.regex()` calls is emitted as
// `allOf: [{ pattern }, { pattern }]`, which a per-node hook that saw only the string's own node
// missed (audit B07 follow-up, review L2-b).
function asciiPatterns(node: unknown): void {
  if (Array.isArray(node)) {
    for (const item of node) asciiPatterns(item);
    return;
  }
  if (node === null || typeof node !== "object") return;
  const record = node as Record<string, unknown>;
  for (const [key, value] of Object.entries(record)) {
    if (key === "pattern" && typeof value === "string") record[key] = asciiDigits(value);
    else asciiPatterns(value);
  }
}

export function contractJsonSchema(
  schema: z.ZodType,
  io: "input" | "output",
): Record<string, unknown> {
  const document = z.toJSONSchema(schema, {
    target: "draft-2020-12",
    io,
    unrepresentable: "throw",
    cycles: "ref",
    reused: "inline",
  });
  asciiPatterns(document);
  return document;
}
