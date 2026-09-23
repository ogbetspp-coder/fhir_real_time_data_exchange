// Text normalisation for the narrative fidelity check. This file is the executable form of
// docs/fidelity-normalization.md sections 2-4; the golden vectors in test/fixtures/fidelity are
// the language-neutral proof. Any change here is a new NORMALIZATION_VERSION.

export const NORMALIZATION_VERSION = "fidelity-norm/2.0.0";

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

// U+2219 BULLET OPERATOR and U+2043 HYPHEN BULLET are not here: one is a multiplication sign
// and the other a dash, so they are always content.
const BULLET_GLYPHS = new Set([
  0x2022, 0x2023, 0x25a0, 0x25a1, 0x25aa, 0x25ab, 0x25cb, 0x25cf, 0x25e6,
]);

// Section 3 step 5. U+000B, U+000C and U+0085 are not here: section 2 rejects them.
const WHITESPACE = new Set([
  0x0009, 0x000a, 0x000d, 0x0020, 0x00a0, 0x1680, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000,
]);

export function isWhitespace(codePoint: number): boolean {
  return WHITESPACE.has(codePoint) || (codePoint >= 0x2000 && codePoint <= 0x200a);
}

const WORD_CHARACTER = /^[\p{L}\p{N}\p{M}]$/u;

// A character that belongs to a word: letters, digits, combining marks, the invisible formatting
// characters of step 1, and the zero-width (non-)joiners. The query service's quote-edge rule
// uses it. The verifier's span-edge rule (spec section 6) no longer does: since
// fidelity-norm/2.0.0 a span edge must touch whitespace, because punctuation inside a number
// (`1.5`, `−20`, `1,000`) is not a boundary either.
export function isWordCharacter(character: string): boolean {
  const codePoint = character.codePointAt(0) ?? 0;
  return (
    WORD_CHARACTER.test(character) ||
    INVISIBLE_FORMATTING.has(codePoint) ||
    codePoint === 0x200c ||
    codePoint === 0x200d
  );
}

// Section 2's closed rejection list. Bidirectional controls are here because their reach differs
// between a narrative block and page text; C1 controls because a renderer remaps them through
// windows-1252; U+000B and U+000C because they are not XML characters.
export function isForbiddenCodePoint(codePoint: number): boolean {
  if (codePoint === 0xfffd || codePoint === 0xfffe || codePoint === 0xffff) return true;
  if (codePoint >= 0x007f && codePoint <= 0x009f) return true;
  if (codePoint >= 0xd800 && codePoint <= 0xdfff) return true;
  if (codePoint === 0x061c || codePoint === 0x200e || codePoint === 0x200f) return true;
  if (codePoint >= 0x202a && codePoint <= 0x202e) return true;
  if (codePoint >= 0x2066 && codePoint <= 0x2069) return true;
  if (codePoint < 0x0020) {
    return !(codePoint === 0x0009 || codePoint === 0x000a || codePoint === 0x000d);
  }
  return false;
}

// Code-point offset of the first character rejected by spec section 2, if any.
export function findForbiddenCharacter(text: string): number | undefined {
  let offset = 0;
  for (const character of text) {
    if (isForbiddenCodePoint(character.codePointAt(0) ?? 0)) return offset;
    offset += 1;
  }
  return undefined;
}

// For each code point, whether the line it is on (delimited by U+000A) contains U+0009.
function linesWithTab(points: readonly string[]): boolean[] {
  const result: boolean[] = [];
  let lineStart = 0;
  for (let position = 0; position <= points.length; position += 1) {
    if (position < points.length && points[position] !== "\n") continue;
    const hasTab = points.slice(lineStart, position).includes("\t");
    for (let inLine = lineStart; inLine < position; inLine += 1) result[inLine] = hasTab;
    lineStart = position + 1;
  }
  return result;
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

  // Step 4: a bullet glyph is list structure only where a list item starts — at the start of a
  // line (after U+000A, then optional whitespace), followed by whitespace, on a line that
  // contains no U+0009. Anywhere else it is content (`2 • 10` in a line is not `2 10`). The start
  // of the text is not a line start: normalised text has no U+000A, so normalising it again
  // replaces nothing, which keeps the procedure idempotent; the scanner's text begins with
  // U+000A, and the verifier reads a page slice from its line terminator (section 6). A line
  // with U+0009 is a table row (section 7; the scanner emits cells with U+0009), so a bullet in a
  // table cell is always content. A bullet this step replaced counts as whitespace for the
  // bullet after it.
  const output: string[] = [];
  const composed = Array.from(expanded.join("").normalize("NFC"));
  const onTabLine = linesWithTab(composed);
  let atLineStart = false;
  for (let position = 0; position < composed.length; position += 1) {
    const character = composed[position] ?? "";
    const codePoint = character.codePointAt(0) ?? 0;
    if (isWhitespace(codePoint)) {
      if (codePoint === 0x000a) atLineStart = true;
      output.push(" ");
      continue;
    }
    const next = composed[position + 1]?.codePointAt(0);
    if (
      BULLET_GLYPHS.has(codePoint) &&
      atLineStart &&
      onTabLine[position] !== true &&
      next !== undefined &&
      isWhitespace(next)
    ) {
      output.push(" ");
      continue;
    }
    atLineStart = false;
    output.push(character);
  }

  return output.join("").replace(/ {2,}/g, " ").replace(/^ | $/g, "");
}

export function countWords(normalized: string): number {
  return normalized.length === 0 ? 0 : normalized.split(" ").length;
}
