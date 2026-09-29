import { isGap, isWordCharacter } from "../fidelity/index.js";
import {
  CELL_START,
  COVERED_ABOVE,
  COVERED_LEFT,
  ROW_START,
  TABLE_END,
  TABLE_START,
} from "../fidelity/xhtml.js";

// The quote-edge rule `verify_quote` decides with (src/query/tools.ts): where a quote may begin
// and end in a section's normalised text. Pure: text in, offsets out. The agent's port is
// agent/src/verifiable_answer_agent/quote_edge.py, held to this module's answers through
// test/fixtures/contracts/quote-edge-cases.json.

function codePointLength(text: string): number {
  return Array.from(text).length;
}

// The code point that ends just before UTF-16 index `index`, or undefined at the start of the
// text. Normalised text never holds a lone surrogate (spec section 2), so a low surrogate here
// is always the second half of a pair.
function codePointBefore(text: string, index: number): string | undefined {
  if (index <= 0) return undefined;
  const unit = text.charCodeAt(index - 1);
  const start = unit >= 0xdc00 && unit <= 0xdfff && index >= 2 ? index - 2 : index - 1;
  return text.slice(start, index);
}

// The code point that starts at UTF-16 index `index`, or undefined at the end of the text.
function codePointAtIndex(text: string, index: number): string | undefined {
  const codePoint = text.codePointAt(index);
  return codePoint === undefined ? undefined : String.fromCodePoint(codePoint);
}

// --- the quote-edge rule ---------------------------------------------------------------------

// A quote is checked against the stored text and nothing checks it afterwards, so where it may
// begin and end is stricter here than the publishing gate's span-edge rule
// (docs/fidelity-normalization.md section 6). The gate only has to stop a span cutting a word,
// because it then requires the whole section to equal the approved narrative; a quote has no
// such second check, and a quote that stops at punctuation can still change what the label
// says — "Take 2" out of "Take 2.5 mg", "see section 4" out of "(see section 4.4)", "20 °C"
// out of "-20 °C", "diabetic patients" out of "non-diabetic patients". The rule, for quotes
// only (the gate is unchanged):
//
// - Left edge. The code point before the quote is absent or a space, or it is a run of
//   opening punctuation (QUOTE_OPENERS) that is itself preceded by a space or the start of the
//   text. Anything else is a cut: a letter or digit, a sign or comparator ("-20", "<10",
//   "≥10"), an apostrophe joined to a word ("Don't"), a slash, a decimal point.
// - Right edge. The code point after the quote is absent or a space, or it is a run of closing
//   punctuation (QUOTE_CLOSERS) that is itself followed by a space or the end of the text. So a
//   quote may end before a sentence's full stop, a comma, a colon or a closing parenthesis
//   followed by a space, and not before "." or "," or "/" followed by a digit or a letter.
// - Across a space. A quote that begins with a number after a space preceded by a number, or
//   ends with a number before a space followed by a number, has cut a space-grouped number
//   ("1 000" out of "1 000 000 IU"); a quote preceded by a sign and a space (isSpacedSign:
//   "≥ 30 ml/min"), or ending in a number before a space and a sign ("30 %", "100 × 10⁹/l"),
//   has lost it. All are cuts.
//
// It is never looser than the gate: a word character on either side is a cut before any of the
// above is consulted, under the fidelity library's own isWordCharacter. It is not a grammar,
// and it does not make a quote complete: a quote may still stop before any following word, so
// "Take 5" matches "Take 5 mg daily" — a match proves the words a quote contains, not that
// nothing follows them. Normalised text holds no whitespace but U+0020 (spec section 3) and no
// U+00AD (step 1), so neither needs a case here.

const QUOTE_OPENERS = new Set(["(", "[", "{", '"', "'", "‘", "“", "„", "«", "‹", "¿", "¡"]);

const QUOTE_CLOSERS = new Set([
  ".",
  ",",
  ";",
  ":",
  "!",
  "?",
  ")",
  "]",
  "}",
  '"',
  "'",
  "’",
  "”",
  "»",
  "›",
  "…",
]);

// What may stand between a number and a quote's edge across a space without binding them: plain
// punctuation (PLAIN_PUNCTUATION: sentence marks, closing brackets and quotation marks, and "®",
// "™", "©"), and dashes and hyphens (general category Pd: set off by spaces they are far more
// often separators than signs). Every other code point that is not a letter, a number, a gap, a
// combining mark, an opening mark reading back skips, or one of the scanner's own markers is
// read as a sign: a mathematical symbol, a comparator's look-alike from any block ("˂", "❮",
// "⧼", "⟪", "‹"), a dingbat ("➕"), a middle dot, a slash, "%", "°". A look-alike no list names
// is therefore still a cut. A letter drawn like a sign ("x" or Cyrillic "х" for "×", U+1438 for
// "<") is not read as one, a stated residual; modifier letters (Lm, "ˍ") are signs here.
const PLAIN_PUNCTUATION = new Set([
  ".",
  ",",
  ";",
  ":",
  "!",
  "?",
  ")",
  "]",
  "}",
  '"',
  "'",
  "’",
  "”",
  "»",
  "…",
  "®",
  "™",
  "©",
  // Reference marks: a footnote's mark after a word or a number binds neither ("decreased†",
  // "Grade 3*"). An asterisk written for a multiplication ("2 * 10") is not read as one, a
  // stated residual.
  "*",
  "†",
  "‡",
  "§",
  "¶",
  "#",
]);
// Signs that bind the number before them only ("30 %", "25 °C"): read after a number, never as
// a sign before a quote.
const POSTFIX_SIGNS = new Set(["%", "‰", "‱", "°", "′", "″", "℃", "℉"]);
const DASH = /^\p{Pd}$/u;
const LETTER = /^\p{L}$/u;
const MODIFIER_LETTER = /^\p{Lm}$/u;
const MARK = /^\p{M}$/u;

// A number is any code point of general category N: a decimal digit of any script, and "½",
// "¹" or "₂" as well, so "1 ½" and "10" | "₀₀₀" are each one number.
const NUMBER = /^\p{N}$/u;

function isDigit(character: string | undefined): boolean {
  return character !== undefined && NUMBER.test(character);
}

function isLetterOrNumber(character: string): boolean {
  return (LETTER.test(character) && !MODIFIER_LETTER.test(character)) || NUMBER.test(character);
}

// The scanner's grid markers and picture token delimiters: structure, never a sign.
function isScannerMarker(character: string): boolean {
  const codePoint = character.codePointAt(0) ?? 0;
  return codePoint === 0xfffc || (codePoint >= 0xfdd0 && codePoint <= 0xfdef);
}

// The opening marks reading back for a sign skips: QUOTE_OPENERS but "‹", drawn like "<".
function isSkippedOpener(character: string): boolean {
  return QUOTE_OPENERS.has(character) && character !== "‹";
}

// A sign before a quote binds the number after it: a postfix sign does not.
function isSignBefore(character: string): boolean {
  return isSpacedSign(character) && !POSTFIX_SIGNS.has(character);
}

function isSpacedSign(character: string | undefined): boolean {
  return (
    character !== undefined &&
    !isLetterOrNumber(character) &&
    !isGapPoint(character) &&
    !MARK.test(character) &&
    !isScannerMarker(character) &&
    !isSkippedOpener(character) &&
    !PLAIN_PUNCTUATION.has(character) &&
    !DASH.test(character)
  );
}

function isGapPoint(character: string | undefined): boolean {
  return character !== undefined && isGap(character.codePointAt(0) ?? 0);
}

// --- across table cells ---------------------------------------------------------------------
//
// A renderer draws a row's cells side by side with a gap about as wide as a space, and centres
// each cell's lines vertically, so any line of a cell can sit level with any line of another
// cell in the same row: "Up to 10" | "once" / "000 IU" / "weekly" is drawn with "Up to 10 000
// IU" on one line, and "<" | "5 mg" reads "< 5 mg". Where a cell's lines fall depends on the
// viewer's width, which the text cannot say. So a quote that begins at a word boundary inside a
// cell is held to the digit and sign rules against every word of every cell to its left, and one
// that ends at a word boundary inside a cell against every word of every cell to its right, in
// each row its cell covers (fidelity-norm/3.0.0 section 5 writes the grid: U+FDD0 table, U+FDD1
// end, U+FDD2 row, U+FDD3 cell, U+FDD4 and U+FDD5 slots covered from the left and from above).
// A word is a run of code points that are not gaps. The grid of every table in a section is
// read once per search.

const SLOT_MARKERS = new Set([CELL_START, COVERED_LEFT, COVERED_ABOVE]);

// What a cell's words hold, as bits: a word ending in a sign, a word ending in a number, a word
// beginning with a number, a word beginning with a sign (read past marks and opening marks).
const ENDS_SIGN = 1;
const ENDS_DIGIT = 2;
const STARTS_DIGIT = 4;
const STARTS_SIGN = 8;

type Cell = {
  // For each row the cell covers, from its first: the bits of every cell to its left in that
  // row, and of every cell to its right.
  left: number[];
  right: number[];
};

type TableIndex = {
  // For each UTF-16 index inside a cell's text, the cell's number; -1 elsewhere.
  cellAt: Int32Array;
  cells: Cell[];
};

// A word ends in a sign when reading back from its end, past opening punctuation that is not
// itself a sign and past combining marks, reaches a run of symbols and punctuation holding a
// sign ("<(" before "30 ml/min)" in the next cell, "<" with U+0332 drawn "≤", "+/-").
function wordBits(text: string, start: number, end: number): number {
  const words: string[][] = [[]];
  for (const point of text.slice(start, end)) {
    if (isGapPoint(point)) words.push([]);
    else words[words.length - 1]?.push(point);
  }
  let bits = 0;
  for (const points of words) {
    if (points.length === 0) continue;
    const word = points.join("");
    if (isDigit(numberFrom(word, 0))) bits |= STARTS_DIGIT;
    if (isSpacedSign(drawnFrom(word, 0))) bits |= STARTS_SIGN;
    if (isDigit(numberBefore(word, word.length))) bits |= ENDS_DIGIT;
    if (signsBefore(word)[word.length] === 1) bits |= ENDS_SIGN;
  }
  return bits;
}

type Slot = { marker: string; start: number; end: number };

function indexTable(text: string, open: number, index: TableIndex): number {
  const close = text.indexOf(TABLE_END, open);
  const stop = close < 0 ? text.length : close;
  const rows: Slot[][] = [];
  let slot: Slot | undefined;
  // Grid markers are single UTF-16 units, so the text is walked by unit here.
  for (let at = open + 1; at <= stop; at += 1) {
    const unit = text[at] ?? "";
    const isMarker = at === stop || unit === ROW_START || SLOT_MARKERS.has(unit);
    if (isMarker && slot !== undefined) {
      slot.end = at;
      slot = undefined;
    }
    if (at === stop) break;
    if (unit === ROW_START) rows.push([]);
    else if (SLOT_MARKERS.has(unit)) {
      slot = { marker: unit, start: at + 1, end: at + 1 };
      rows[rows.length - 1]?.push(slot);
    }
  }
  // Which cell owns each slot, and each cell's position and bits.
  const owner: number[][] = rows.map(() => []);
  const placed: { row: number; column: number; columns: number; rows: number; bits: number }[] = [];
  rows.forEach((slots, row) => {
    slots.forEach((current, column) => {
      let id: number;
      if (current.marker === CELL_START) {
        id = placed.length;
        placed.push({ row, column, columns: 1, rows: 1, bits: 0 });
        const entry = placed[id];
        if (entry !== undefined) {
          entry.bits = wordBits(text, current.start, current.end);
          while (rows[row]?.[column + entry.columns]?.marker === COVERED_LEFT) entry.columns += 1;
          while (rows[row + entry.rows]?.[column]?.marker === COVERED_ABOVE) entry.rows += 1;
        }
        index.cellAt.fill(index.cells.length + id, current.start, current.end);
      } else if (current.marker === COVERED_LEFT) {
        id = owner[row]?.[column - 1] ?? -1;
      } else {
        id = owner[row - 1]?.[column] ?? -1;
      }
      owner[row]?.push(id);
    });
  });
  // For each row, the bits of the cells before each column and from each column on.
  const before = owner.map((ids) => {
    const out = [0];
    for (const id of ids) out.push((out[out.length - 1] ?? 0) | (placed[id]?.bits ?? 0));
    return out;
  });
  const after = owner.map((ids) => {
    const out = Array<number>(ids.length + 1).fill(0);
    for (let column = ids.length - 1; column >= 0; column -= 1) {
      out[column] = (out[column + 1] ?? 0) | (placed[ids[column] ?? -1]?.bits ?? 0);
    }
    return out;
  });
  for (const entry of placed) {
    const left: number[] = [];
    const right: number[] = [];
    for (let row = entry.row; row < entry.row + entry.rows; row += 1) {
      left.push(before[row]?.[entry.column] ?? 0);
      right.push(after[row]?.[entry.column + entry.columns] ?? 0);
    }
    index.cells.push({ left, right });
  }
  return stop;
}

// Every table of a normalised section text, or undefined when it holds none.
function indexTables(text: string): TableIndex | undefined {
  let open = text.indexOf(TABLE_START);
  if (open < 0) return undefined;
  const index: TableIndex = { cellAt: new Int32Array(text.length + 1).fill(-1), cells: [] };
  while (open >= 0) {
    const stop = indexTable(text, open, index);
    open = text.indexOf(TABLE_START, stop);
  }
  return index;
}

// Whether a quote beginning at UTF-16 index `start` inside a cell, whose first code point that
// is neither a gap nor a combining mark is `first`, has lost a sign or cut a number drawn in a
// cell to its left.
function cutAcrossCellBefore(
  tables: TableIndex | undefined,
  start: number,
  first: string | undefined,
): boolean {
  const cell = tables?.cells[tables.cellAt[start] ?? -1];
  if (cell === undefined) return false;
  return cell.left.some(
    (bits) => (bits & ENDS_SIGN) !== 0 || ((bits & ENDS_DIGIT) !== 0 && isDigit(first)),
  );
}

// Whether a quote ending at UTF-16 index `end` inside a cell, whose last code point that is
// neither a gap nor a combining mark is `last`, has cut a number drawn on, or lost a sign after
// it, in a cell to its right.
function cutAcrossCellAfter(
  tables: TableIndex | undefined,
  end: number,
  last: string | undefined,
): boolean {
  const cell = tables?.cells[tables.cellAt[end] ?? -1];
  if (cell === undefined || !isDigit(last)) return false;
  return cell.right.some((bits) => (bits & (STARTS_DIGIT | STARTS_SIGN)) !== 0);
}

// What reading back for a sign skips: gaps, combining marks, and the opening marks.
function skippedBeforeSign(point: string): boolean {
  return isGapPoint(point) || MARK.test(point) || isSkippedOpener(point);
}

// For each UTF-16 index of `text`: 1 when reading back from it, past what `skippedBeforeSign`
// skips, reaches a run of code points (no letter, number, gap or scanner marker) holding a sign.
// One pass over the text, so the rule stays linear however long a run of brackets and spaces is.
function signsBefore(text: string): Uint8Array {
  const reached = new Uint8Array(text.length + 1);
  let runHasSign = false;
  let current = 0;
  let index = 0;
  for (const point of text) {
    if (isGapPoint(point)) {
      runHasSign = false;
    } else if (isLetterOrNumber(point) || isScannerMarker(point)) {
      runHasSign = false;
      current = 0;
    } else {
      runHasSign ||= isSignBefore(point);
      if (!skippedBeforeSign(point)) current = runHasSign ? 1 : 0;
    }
    index += point.length;
    reached[index] = current;
  }
  return reached;
}

// Whether a quote beginning at `start` (or its opening punctuation) set off by the space at
// UTF-16 index `space` is cut: a sign before the space, read past gaps, marks and opening
// punctuation, binds the number after it ("≥ 30", "<" U+2063 " 30", "< ( 30", "+/- 5"); a
// number before it and a number first in the quote, read past gaps and combining marks (not
// opening marks), are one number grouped
// with spaces ("10 000", "10" U+2009 " 000", "1 ½", and "10 (000", a false failure); and so
// across table cells (above).
function cutAfterSpace(
  text: string,
  space: number,
  start: number,
  first: string | undefined,
  context: SearchContext,
): boolean {
  if (context.signs[space] === 1) return true;
  if (isDigit(numberBefore(text, space)) && isDigit(first)) return true;
  return cutAcrossCellBefore(context.tables, start, first);
}

// What a search reads once from the section's text.
type SearchContext = { tables: TableIndex | undefined; signs: Uint8Array };

// The first code point before UTF-16 index `index`, and at or after it, that is neither a gap
// nor a combining mark: the number a reader sees there ("10" U+0332 " 000" is "10 000" with a
// mark set apart). Opening marks are read past for a sign only (drawnFrom): "0.52 (95%" is two
// numbers.
function numberBefore(text: string, index: number): string | undefined {
  let position = index;
  let character = codePointBefore(text, position);
  while (character !== undefined && (isGapPoint(character) || MARK.test(character))) {
    position -= character.length;
    character = codePointBefore(text, position);
  }
  return character;
}

function numberFrom(text: string, index: number): string | undefined {
  let position = index;
  let character = codePointAtIndex(text, position);
  while (character !== undefined && (isGapPoint(character) || MARK.test(character))) {
    position += character.length;
    character = codePointAtIndex(text, position);
  }
  return character;
}

// The first code point at or after UTF-16 index `index` that is not skipped when reading for a
// sign (a gap, a combining mark, an opening mark): what the reader sees first there (U+0332
// before "000" is "000" with a mark set apart, "(× 10⁹/l)" a sign in brackets).
function drawnFrom(text: string, index: number): string | undefined {
  let position = index;
  let character = codePointAtIndex(text, position);
  while (character !== undefined && skippedBeforeSign(character)) {
    position += character.length;
    character = codePointAtIndex(text, position);
  }
  return character;
}

function edgeBefore(text: string, start: number, quote: string, context: SearchContext): boolean {
  let before = codePointBefore(text, start);
  if (before === undefined) return true;
  if (isWordCharacter(before)) return false;
  const first = numberFrom(quote, 0);
  if (before === " ") return !cutAfterSpace(text, start - 1, start, first, context);
  let index = start;
  while (before !== undefined && QUOTE_OPENERS.has(before)) {
    // An opening mark drawn like a comparator ("‹30") is a sign joined to the quote.
    if (!isSkippedOpener(before)) return false;
    index -= before.length;
    before = codePointBefore(text, index);
  }
  if (index === start) return false;
  if (before === undefined) return true;
  return before === " " && !cutAfterSpace(text, index - 1, start, first, context);
}

function edgeAfter(text: string, end: number, quote: string, context: SearchContext): boolean {
  let after = codePointAtIndex(text, end);
  if (after === undefined) return true;
  if (isWordCharacter(after)) return false;
  if (after === " ") {
    // A number read past gaps and combining marks on both sides of the space; a sign past
    // opening marks too, as on the left edge ("100 (× 10⁹/l)").
    const last = numberBefore(quote, quote.length);
    if (
      isDigit(last) &&
      (isDigit(numberFrom(text, end + 1)) || isSpacedSign(drawnFrom(text, end + 1)))
    ) {
      return false;
    }
    return !cutAcrossCellAfter(context.tables, end, last);
  }
  let index = end;
  while (after !== undefined && QUOTE_CLOSERS.has(after)) {
    index += after.length;
    after = codePointAtIndex(text, index);
  }
  return index > end && (after === undefined || after === " ");
}

// Where `quote` occurs in `text` with both edges on a boundary under the quote-edge rule, as a
// UTF-16 index, or -1. An occurrence that is cut does not end the search: a later occurrence
// whose edges hold still matches.
function findQuoteOccurrence(text: string, quote: string): number {
  const first = text.indexOf(quote);
  // Most sections do not hold the quote at all; the indices below are built only for one that
  // does.
  if (first < 0) return -1;
  const context: SearchContext = { tables: indexTables(text), signs: signsBefore(text) };
  for (let found = first; found >= 0; found = text.indexOf(quote, found + 1)) {
    if (
      edgeBefore(text, found, quote, context) &&
      edgeAfter(text, found + quote.length, quote, context)
    ) {
      return found;
    }
  }
  return -1;
}

// The decision `verify_quote` makes for one section: where the normalised `quote` first occurs
// in the section's normalised `text` under the quote-edge rule, in code points (ADR 0002), or
// undefined. Exported so that scripts/contracts/export-quote-edge-cases.ts can publish this
// rule's answers for the agent's test double to be held to (test/fixtures/contracts/
// quote-edge-cases.json); `verifyQuote` in src/query/tools.ts calls nothing else to decide.
export function locateQuote(
  text: string,
  quote: string,
): { startOffset: number; endOffset: number } | undefined {
  const found = findQuoteOccurrence(text, quote);
  if (found < 0) return undefined;
  const startOffset = codePointLength(text.slice(0, found));
  return { startOffset, endOffset: startOffset + codePointLength(quote) };
}
