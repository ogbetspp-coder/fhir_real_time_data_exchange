// Text normalisation for the narrative fidelity check. This file is the executable form of
// docs/fidelity-normalization.md sections 2-4; the golden vectors in test/fixtures/fidelity are
// the language-neutral proof. Any change here is a new NORMALIZATION_VERSION.

export const NORMALIZATION_VERSION = "fidelity-norm/1.1.0";

export class NormalizationError extends Error {
  public constructor(
    public readonly code: "forbidden-character",
    public readonly offset: number,
  ) {
    super(`Forbidden character at code point offset ${offset}`);
    this.name = "NormalizationError";
  }
}

const INVISIBLE_FORMATTING = new Set([0x00ad, 0x200b, 0xfeff, 0x2060]);

const LIGATURES = new Map<number, string>([
  [0xfb00, "ff"],
  [0xfb01, "fi"],
  [0xfb02, "fl"],
  [0xfb03, "ffi"],
  [0xfb04, "ffl"],
  [0xfb06, "st"],
]);

const BULLET_GLYPHS = new Set([
  0x2022, 0x2023, 0x2043, 0x2219, 0x25a0, 0x25a1, 0x25aa, 0x25ab, 0x25cb, 0x25cf, 0x25e6,
]);

const WHITESPACE = new Set([
  0x0009, 0x000a, 0x000b, 0x000c, 0x000d, 0x0020, 0x0085, 0x00a0, 0x1680, 0x2028, 0x2029, 0x202f,
  0x205f, 0x3000,
]);

function isWhitespace(codePoint: number): boolean {
  return WHITESPACE.has(codePoint) || (codePoint >= 0x2000 && codePoint <= 0x200a);
}

const WORD_CHARACTER = /^[\p{L}\p{N}\p{M}]$/u;

// A character that belongs to a word (spec section 6): letters, digits, combining marks, the
// invisible formatting characters of step 1, and the zero-width (non-)joiners. A span edge that
// touches one of these cuts a word.
export function isWordCharacter(character: string): boolean {
  const codePoint = character.codePointAt(0) ?? 0;
  return (
    WORD_CHARACTER.test(character) ||
    INVISIBLE_FORMATTING.has(codePoint) ||
    codePoint === 0x200c ||
    codePoint === 0x200d
  );
}

function isForbidden(codePoint: number): boolean {
  if (codePoint === 0xfffd || codePoint === 0xfffe || codePoint === 0xffff) return true;
  if (codePoint === 0x007f) return true;
  if (codePoint >= 0xd800 && codePoint <= 0xdfff) return true;
  if (codePoint < 0x0020) {
    return !(
      codePoint === 0x0009 ||
      codePoint === 0x000a ||
      codePoint === 0x000b ||
      codePoint === 0x000c ||
      codePoint === 0x000d
    );
  }
  return false;
}

// Code-point offset of the first character rejected by spec section 2, if any.
export function findForbiddenCharacter(text: string): number | undefined {
  let offset = 0;
  for (const character of text) {
    if (isForbidden(character.codePointAt(0) ?? 0)) return offset;
    offset += 1;
  }
  return undefined;
}

export function normalizeText(text: string): string {
  const forbidden = findForbiddenCharacter(text);
  if (forbidden !== undefined) throw new NormalizationError("forbidden-character", forbidden);

  // Invisible characters are removed and ligatures expanded BEFORE NFC so that a composition
  // NFC would otherwise be blocked from (e.g. "e" + ZWSP + combining acute) is applied in the
  // first pass; this is what makes the procedure idempotent.
  const expanded: string[] = [];
  const points = Array.from(text);
  for (let position = 0; position < points.length; position += 1) {
    const character = points[position] ?? "";
    const codePoint = character.codePointAt(0) ?? 0;
    if (INVISIBLE_FORMATTING.has(codePoint)) {
      // A soft hyphen at a line end marks a word broken across lines: the break goes with it.
      if (codePoint === 0x00ad) {
        if (points[position + 1] === "\r" && points[position + 2] === "\n") position += 2;
        else if (points[position + 1] === "\n") position += 1;
      }
      continue;
    }
    expanded.push(LIGATURES.get(codePoint) ?? character);
  }

  const output: string[] = [];
  for (const character of expanded.join("").normalize("NFC")) {
    const codePoint = character.codePointAt(0) ?? 0;
    if (BULLET_GLYPHS.has(codePoint) || isWhitespace(codePoint)) {
      output.push(" ");
      continue;
    }
    output.push(character);
  }

  return output.join("").replace(/ {2,}/g, " ").replace(/^ | $/g, "");
}

export function countWords(normalized: string): number {
  return normalized.length === 0 ? 0 : normalized.split(" ").length;
}
