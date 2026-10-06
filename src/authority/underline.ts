import { isDefaultIgnorable } from "../fidelity/normalize.js";
import letters from "./data/underline-letters.json" with { type: "json" };

// What an underline can change: ADR 0005, "Underlines are not unwrapped blindly"; the port of
// zone-a/src/zone_a/underline.py, which the QRD check uses, and which the shared cases in
// test/fixtures/authority/underline-cases.json hold both to.
//
// A renderer's underline turns a sign into another sign ("<" underlined is drawn "≤", "+" "±",
// "=" "≡", "-" nearly "="; U+02C2 exactly "≤") and a letter after a number into an ordinal
// indicator ("1" and an underlined "a" read "1ª"). An underline changes nothing only over the
// closed allowlist below, judged on the drawn text around it: letters and decimal digits of the
// Latin, Greek and Cyrillic scripts, spaces, and plain punctuation, with no underlined lower-case
// letter directly after a number (read past code points drawn as nothing, not past a space), and
// no underlined "o" after an "N" ("Nº"), look-alikes included. A hyphen (U+002D, U+2010, U+2011)
// inside an underlined word, with the line under a letter on each side ("Breast-feeding"
// underlined whole), cannot read as "=" and is allowed where a caller says so; a hyphen at the
// line's edge ("CL-CR" with only the hyphen underlined) can, and so can any other dash.

// Space, no-break space, plain punctuation and the curly quotation marks: none of them changes
// under a line (an e-mail address and a link's text are often underlined).
const PUNCTUATION = new Set([
  ...Array.from(" .,;:()[]/'\"%@_&#!?*"),
  "\u00a0",
  "\u2018",
  "\u2019",
  "\u201c",
  "\u201d",
]);
// An underlined lower-case letter after a number is drawn as an ordinal indicator; an underlined
// "o" (or a look-alike) after "N" (or a look-alike) is drawn as the numero sign "Nº".
const NUMERO = new Set(["N", "n", "\u039d", "\uff2e"]);
const O_LETTERS = new Set(["o", "\u043e", "\u03bf", "\u1d0f"]);

// The letters of the Latin, Greek and Cyrillic scripts, as Python's Unicode database names them
// (zone-a/scripts/generate_underline_letters.py): JavaScript has no character names, and its
// Script property counts other code points (ª, modifier letters) as Latin.
const LETTER_RANGES: readonly (readonly [number, number])[] = letters.ranges.map((range) => {
  const [first, last] = range;
  if (first === undefined || last === undefined || range.length !== 2 || first > last) {
    throw new Error("src/authority/data/underline-letters.json holds a malformed range");
  }
  return [first, last] as const;
});

export function isUnderlineLetter(character: string): boolean {
  const codePoint = character.codePointAt(0);
  if (codePoint === undefined) return false;
  let low = 0;
  let high = LETTER_RANGES.length - 1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    const [first, last] = LETTER_RANGES[middle] ?? [0, -1];
    if (codePoint < first) high = middle - 1;
    else if (codePoint > last) low = middle + 1;
    else return true;
  }
  return false;
}

const isDigit = (character: string): boolean => character >= "0" && character <= "9";
const LOWER = /^\p{Ll}$/u;
const NUMBER = /^\p{N}$/u;
// The hyphens an underline may run through inside a word: a hyphen-minus, a hyphen, a
// non-breaking hyphen.
const HYPHENS: ReadonlySet<string> = new Set(["-", "\u2010", "\u2011"]);

// The first code point before `index` that a renderer draws: a Default_Ignorable code point such
// as U+2063 is drawn as nothing; a space is drawn, and stops the reading.
function drawnBefore(points: readonly string[], index: number): string {
  let position = index - 1;
  while (position >= 0 && isDefaultIgnorable((points[position] ?? "").codePointAt(0) ?? 0)) {
    position -= 1;
  }
  return points[position] ?? "";
}

export type UnderlineOptions = {
  // Code points a caller reads as markup rather than text (the QRD template's own brackets).
  also?: ReadonlySet<string>;
  // A hyphen inside an underlined word (a letter underlined on each side of it) is allowed.
  hyphensInWords?: boolean;
};

// Whether an underline over the code points `points[start:end]` can change what the drawn text
// says. Offsets are in code points, as in the Python.
export function underlineChanges(
  points: readonly string[],
  start: number,
  end: number,
  options: UnderlineOptions = {},
): boolean {
  for (let index = start; index < end; index += 1) {
    const character = points[index] ?? "";
    if (isUnderlineLetter(character) || isDigit(character) || PUNCTUATION.has(character)) continue;
    if (options.also?.has(character) === true) continue;
    if (
      options.hyphensInWords === true &&
      HYPHENS.has(character) &&
      index > start &&
      index < end - 1 &&
      isUnderlineLetter(points[index - 1] ?? "") &&
      isUnderlineLetter(points[index + 1] ?? "")
    ) {
      continue;
    }
    return true;
  }
  for (let index = start; index < end; index += 1) {
    const character = points[index] ?? "";
    const before = drawnBefore(points, index);
    if (LOWER.test(character) && NUMBER.test(before === "" ? " " : before)) return true;
    // "N" not underlined and an underlined "o" first after it.
    if (index === start && NUMERO.has(before) && O_LETTERS.has(character)) return true;
  }
  return false;
}
