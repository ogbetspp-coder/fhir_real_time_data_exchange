import {
  composeText,
  isDefaultIgnorable,
  isForbiddenCodePoint,
  isGap,
  isWhitespace,
  isWordCharacter,
  normalizeText,
} from "../../../src/fidelity/normalize.js";
import {
  isGridMarker,
  isInvisibleBreak,
  isReservedCodePoint,
  scriptCodePoint,
} from "../../../src/fidelity/xhtml.js";

// The `codePoints` vector family: what every closed code-point list of normalize.ts and xhtml.ts
// says about every code point, U+0000 to U+10FFFF. The worked vectors reach a list only where their
// author wrote an input for it, so an entry dropped from one moved none of them (an ignorable, a
// thin space); this table moves whenever any list does, and test/fidelity-code-points.test.ts ties
// it to the normalisation version (docs/fidelity-normalization.md section 8). Zone A and the agent
// hold their own copies of the lists to it.
//
// Each class is a bit, read through the function the check itself calls. The table is run-length
// encoded: `[start, bits]`, each run lasting until the next one starts.

export const CODE_POINT_CLASSES = [
  // Section 2: refused.
  "forbidden",
  // Section 3 step 5: whitespace.
  "whitespace",
  // Section 6: a gap (whitespace, a thin space, a blank glyph, Default_Ignorable_Code_Point).
  "gap",
  "defaultIgnorable",
  // The query service's word characters (letters, numbers, marks and the invisible joiners).
  "wordCharacter",
  // Step 1: removed.
  "removedByStep1",
  // Step 2: expanded (a ligature).
  "expanded",
  // Step 4: a bullet at a line start, before whitespace, is list structure.
  "bullet",
  // Section 5: the scanner's reserved code points, grid markers, and the invisible breaks a
  // narrative may not hold.
  "reserved",
  "gridMarker",
  "invisibleBreak",
  // Section 5: a combining mark (general category M), refused right after an inline tag.
  "mark",
  // Section 5: folded to a script form, or refused, inside `sup` and inside `sub`.
  "supFolded",
  "supRefused",
  "subFolded",
  "subRefused",
] as const;

export const LAST_CODE_POINT = 0x10ffff;

function classesOf(codePoint: number): number {
  const character = String.fromCodePoint(codePoint);
  const composed = composeText(character);
  const removed = composed === "";
  let bullet = false;
  if (!removed && !isWhitespace(codePoint) && !isForbiddenCodePoint(codePoint)) {
    bullet = normalizeText(`\n${character} x`) === "x";
  }
  const sup = scriptCodePoint("sup", codePoint);
  const sub = scriptCodePoint("sub", codePoint);
  const flags = [
    isForbiddenCodePoint(codePoint),
    isWhitespace(codePoint),
    isGap(codePoint),
    isDefaultIgnorable(codePoint),
    isWordCharacter(character),
    removed,
    !removed && composed !== character.normalize("NFC"),
    bullet,
    isReservedCodePoint(codePoint),
    isGridMarker(codePoint),
    isInvisibleBreak(codePoint),
    /^\p{M}$/u.test(character),
    sup !== undefined && sup !== codePoint,
    sup === undefined,
    sub !== undefined && sub !== codePoint,
    sub === undefined,
  ];
  return flags.reduce((bits, flag, bit) => (flag ? bits | (1 << bit) : bits), 0);
}

export type CodePointVectors = {
  classes: readonly string[];
  // `[start, bits]`: every code point from `start` to the next run's start has these bits.
  runs: [number, number][];
  // Where `sup` or `sub` folds: `[codePoint, sup, sub]`, each the folded code point or null.
  scriptFolds: [number, number | null, number | null][];
};

export function codePointVectors(): CodePointVectors {
  const runs: [number, number][] = [];
  const scriptFolds: [number, number | null, number | null][] = [];
  let previous = -1;
  for (let codePoint = 0; codePoint <= LAST_CODE_POINT; codePoint += 1) {
    const bits = classesOf(codePoint);
    if (bits !== previous) runs.push([codePoint, bits]);
    previous = bits;
    const sup = scriptCodePoint("sup", codePoint);
    const sub = scriptCodePoint("sub", codePoint);
    const supFolded = sup !== undefined && sup !== codePoint ? sup : null;
    const subFolded = sub !== undefined && sub !== codePoint ? sub : null;
    if (supFolded !== null || subFolded !== null)
      scriptFolds.push([codePoint, supFolded, subFolded]);
  }
  return { classes: CODE_POINT_CLASSES, runs, scriptFolds };
}
