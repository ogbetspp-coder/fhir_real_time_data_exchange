// Text normalisation for the narrative fidelity check. This file is the executable form of
// docs/fidelity-normalization.md sections 2-4; the golden vectors in test/fixtures/fidelity are
// the language-neutral proof. Any change here is a new NORMALIZATION_VERSION.

export const NORMALIZATION_VERSION = "fidelity-norm/3.6.0";

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

// Whether text holds a character step 1 removes (U+00AD, U+200B, U+FEFF, U+2060). An authority
// import refuses such a character in a structured page (section 7), from this one list.
export function hasInvisibleFormatting(text: string): boolean {
  for (const character of text) {
    if (INVISIBLE_FORMATTING.has(character.codePointAt(0) ?? 0)) return true;
  }
  return false;
}

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

// Section 3 step 5. U+000B, U+000C and U+0085 are not here: section 2 rejects them. Nor, from
// fidelity-norm/3.0.0, are the spaces a renderer does not draw as a full gap: U+1680 OGHAM SPACE
// MARK is drawn as a stroke ("Take 2" U+1680 "10 mg" reads as a range), and the spaces narrower
// than a quarter of an em (THIN_SPACES) can look like no space at all ("2" U+200A "10" as "210").
// They are content.
const WHITESPACE = new Set([
  0x0009, 0x000a, 0x000d, 0x0020, 0x00a0, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2007,
  0x2008, 0x2028, 0x2029, 0x3000,
]);

export function isWhitespace(codePoint: number): boolean {
  return WHITESPACE.has(codePoint);
}

// The spaces narrower than a quarter of an em: SIX-PER-EM, THIN, HAIR, NARROW NO-BREAK and MEDIUM
// MATHEMATICAL SPACE. Content (section 3 step 5), yet drawn as a gap.
const THIN_SPACES = new Set([0x2006, 0x2009, 0x200a, 0x202f, 0x205f]);

// Unicode's Default_Ignorable_Code_Point, spelled out (Unicode 16.0, DerivedCoreProperties.txt):
// code points a renderer draws as nothing.
const DEFAULT_IGNORABLE: readonly (readonly [number, number])[] = [
  [0x00ad, 0x00ad],
  [0x034f, 0x034f],
  [0x061c, 0x061c],
  [0x115f, 0x1160],
  [0x17b4, 0x17b5],
  [0x180b, 0x180f],
  [0x200b, 0x200f],
  [0x202a, 0x202e],
  [0x2060, 0x206f],
  [0x3164, 0x3164],
  [0xfe00, 0xfe0f],
  [0xfeff, 0xfeff],
  [0xffa0, 0xffa0],
  [0xfff0, 0xfff8],
  [0x1bca0, 0x1bca3],
  [0x1d173, 0x1d17a],
  [0xe0000, 0xe0fff],
];

export function isDefaultIgnorable(codePoint: number): boolean {
  return DEFAULT_IGNORABLE.some(([low, high]) => codePoint >= low && codePoint <= high);
}

// Code points drawn as an empty glyph that are neither whitespace nor Default_Ignorable, found by
// rendering every assigned code point in Chrome's default fonts on macOS and measuring the ink:
// U+2800 BRAILLE PATTERN BLANK, and Mongolian and Yi letters the default serif face lacks and
// draws as an em-wide blank.
const BLANK_GLYPHS = new Set([0x1878, 0x18aa, 0x2800, 0xa4a2, 0xa4a3, 0xa4b4, 0xa4c1, 0xa4c5]);

// A gap for the digit-group rules (section 6) and the quote-edge rule: section 3 whitespace, a
// thin space, a blank glyph, or a code point Unicode says to ignore. Reading past these, "10"
// U+2009 " 000" is one number, however the gap between its groups is written.
export function isGap(codePoint: number): boolean {
  return (
    isWhitespace(codePoint) ||
    THIN_SPACES.has(codePoint) ||
    BLANK_GLYPHS.has(codePoint) ||
    isDefaultIgnorable(codePoint)
  );
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

// From fidelity-norm/3.0.0: the interlinear annotation controls, which Unicode reserves for
// internal use and a renderer draws as a blank, and the prepended concatenation marks, which a
// renderer draws across the digits after them (U+070F puts a bar over "000").
const FORBIDDEN_3_0_0 = new Set([
  0x0600, 0x0601, 0x0602, 0x0603, 0x0604, 0x0605, 0x06dd, 0x070f, 0x0890, 0x0891, 0x08e2, 0xfff9,
  0xfffa, 0xfffb, 0x110bd, 0x110cd,
]);

// Section 2's closed rejection list. Bidirectional controls are here because their reach differs
// between a narrative block and page text; C1 controls because a renderer remaps them through
// windows-1252; U+000B and U+000C because they are not XML characters.
export function isForbiddenCodePoint(codePoint: number): boolean {
  if (FORBIDDEN_3_0_0.has(codePoint)) return true;
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

// `lastLineHasTab`: the text is a slice of page text whose last line continues past the slice
// in the page, and the whole page line contains U+0009 (section 6). Step 4 then treats the last
// line as a line with U+0009, so cutting a table row before its U+0009 cannot turn a bullet
// in its first cell into a list item.
export type NormalizeOptions = { lastLineHasTab?: boolean };

// Steps 1 to 3. Invisible characters are removed and ligatures expanded BEFORE NFC so that a
// composition NFC would otherwise be blocked from (e.g. "e" + ZWSP + combining acute) is applied
// in the first pass; this is what makes the procedure idempotent. The scanner uses it too, to
// refuse a composition across inline markup (section 5).
export function composeText(text: string): string {
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
  return expanded.join("").normalize("NFC");
}

export function normalizeText(text: string, options: NormalizeOptions = {}): string {
  const forbidden = findForbiddenCharacter(text);
  if (forbidden !== undefined) throw new NormalizationError("forbidden-character", forbidden);

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
  const composed = Array.from(composeText(text));
  const onTabLine = linesWithTab(composed);
  if (options.lastLineHasTab === true) {
    for (let position = composed.length - 1; position >= 0; position -= 1) {
      if (composed[position] === "\n") break;
      onTabLine[position] = true;
    }
  }
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
