import { writeFileSync } from "node:fs";

import { NORMALIZATION_VERSION, NormalizationError } from "../../src/fidelity/normalize.js";
import type { FidelityInput, SourcePage } from "../../src/fidelity/verify.js";
import { FidelityError, verifyNarrativeFidelity } from "../../src/fidelity/verify.js";
import { XhtmlError, xhtmlToText } from "../../src/fidelity/xhtml.js";
import { normalizeText } from "../../src/fidelity/normalize.js";
import { sha256Utf8 } from "../../src/lib/hash.js";

// Differential corpus generator for the Zone A port.
//
// The golden vectors prove agreement on the cases the author of `src/fidelity/` thought to
// write. They cannot prove agreement anywhere else, and "the specification is language-neutral"
// is a claim about everywhere else. This script produces a seeded, reproducible corpus of
// synthetic inputs together with the TypeScript's output for each one;
// `zone-a/tests/test_differential.py` runs the Python implementation over the same corpus and
// compares, so a divergence is a failing test rather than an unexamined difference.
//
// usage: tsx scripts/fidelity/differential.ts [--seed N] [--count N] [--out FILE]
//
// Every character class below is assembled from code points rather than typed as a literal, for
// the same reason the Zone A modules do it: half of these are invisible, several are forbidden
// by section 2, and a source file that contains them cannot be reviewed by reading it.
//
// The inputs are generated gibberish and carry no product information, but they are still
// narrative-shaped: nothing here prints one. The corpus is data written to a file or to stdout,
// and the only thing on stderr is a count.

const CP = (...points: readonly number[]): string => String.fromCodePoint(...points);
const CHARS = (...points: readonly number[]): readonly string[] =>
  points.map((point) => String.fromCodePoint(point));

const TAB = CP(0x0009);
const LF = CP(0x000a);
const CR = CP(0x000d);
const SPACE = CP(0x0020);
const SOFT_HYPHEN = CP(0x00ad);

type Family = "normalize" | "xhtml" | "verify";

type NormalizeExpectation = { text: string } | { error: string };
type XhtmlExpectation = { text: string } | { error: string };
type VerifySectionResult = { sourceKey: string; status: string; reason: string | null };
type VerifyExpectation =
  | { reportHash: string; status: string; sections: VerifySectionResult[]; issues: string[] }
  | { error: "FidelityError"; issues: string[] };

type CorpusCase = {
  family: Family;
  index: number;
  seed: number;
  tag: string;
  classes: string[];
  input: string | FidelityInput;
  // Verify cases only: the Python reader rewrites every page and span offset as a float before
  // running. `JSON.stringify` writes `1` for `1.0`, so an integral float cannot be expressed in
  // the corpus itself; the flag is how the case travels. In JavaScript the two are one value,
  // so `expected` is exactly what a float-offset input would have produced there.
  floatOffsets?: true;
  expected: NormalizeExpectation | XhtmlExpectation | VerifyExpectation;
};

// ----------------------------------------------------------------------------------------------
// Seeded randomness. Same generator as test/fidelity.test.ts, so both are reproducible the same
// way: a corpus is fully determined by (seed, count) and by this file.

function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

type Random = () => number;

function pick<T>(random: Random, pool: readonly T[]): T {
  const item = pool[Math.floor(random() * pool.length)];
  if (item === undefined) throw new Error("cannot pick from an empty pool");
  return item;
}

function between(random: Random, low: number, high: number): number {
  if (high < low) return low;
  return low + Math.floor(random() * (high - low + 1));
}

function chance(random: Random, probability: number): boolean {
  return random() < probability;
}

// ----------------------------------------------------------------------------------------------
// The alphabet. Every closed list the specification names is here, together with the near misses
// that a wrong implementation would treat as members. Fragments are grouped into classes because
// a divergence is reported by class and never by text: a README entry says "a combining mark
// after an invisible", not the string that failed.

const INVISIBLE = CHARS(0x00ad, 0x200b, 0xfeff, 0x2060);
const LIGATURE = CHARS(0xfb00, 0xfb01, 0xfb02, 0xfb03, 0xfb04, 0xfb06);
// U+FB05 is the other long-s ligature and is NOT in the closed list; the rest decompose under
// NFKC, which section 3 deliberately does not use.
const NEAR_LIGATURE = CHARS(0xfb05, 0xfb13, 0x0132, 0x01c4);
const BULLET = CHARS(0x2022, 0x2023, 0x25a0, 0x25a1, 0x25aa, 0x25ab, 0x25cb, 0x25cf, 0x25e6);
// Separators between the groups of a number: a space and a thin space are whitespace, the
// joiners are whitespace but not a boundary for the edge rules.
const GROUP_SEPARATORS = CHARS(0x0020, 0x2009);
const GROUP_JOINERS = CHARS(0x00a0, 0x2007, 0x202f);
// What stands between two groups: one code point, or two (a span can then end or start inside
// the run and still cut the number, review round 3).
const GROUP_RUNS: readonly string[] = [
  ...GROUP_SEPARATORS,
  ...GROUP_JOINERS,
  "  ",
  `${CP(0x2009)} `,
  ` ${CP(0x00a0)}`,
  `${CP(0x202f)} `,
];
// U+2043 and U+2219 were bullets until the review of fidelity-norm/2.0.0 made them content.
const NEAR_BULLET = CHARS(0x2024, 0x25a2, 0x25cc, 0x00b7, 0x2027, 0x2043, 0x2219);
// Section 3 step 5. U+000B, U+000C and U+0085 left the list in fidelity-norm/2.0.0: section 2
// rejects them, and they are in FORBIDDEN_2_0_0 below.
const WHITESPACE = CHARS(
  0x0009,
  0x000a,
  0x000d,
  0x0020,
  0x00a0,
  0x1680,
  0x2000,
  0x2001,
  0x2002,
  0x2003,
  0x2004,
  0x2005,
  0x2006,
  0x2007,
  0x2008,
  0x2009,
  0x200a,
  0x2028,
  0x2029,
  0x202f,
  0x205f,
  0x3000,
);
// Not in the section 3 list, and each is "whitespace" to something: U+180E was a space
// separator before Unicode 6.3, U+3164 and U+2800 render blank, U+00B7 is a visible dot.
const NEAR_WHITESPACE = CHARS(0x180e, 0x3164, 0x2800, 0x00b7);
const COMBINING = CHARS(0x0301, 0x0308, 0x0327, 0x0323, 0x20d7, 0x0334);
// NFC singletons and the exclusions around them: the first three map to another code point and
// the rest stay put. A runtime on a different Unicode version gets these wrong and little else.
const NFC_SINGLETON = CHARS(0x212b, 0x2126, 0x212a, 0x0374, 0x037e, 0x0387);
const HANGUL_JAMO = CHARS(0x1100, 0x1161, 0x11a8, 0x1102, 0x1165, 0x11ab);
const PRECOMPOSED = CHARS(0x00e9, 0x00c5, 0xac00, 0x1e69, 0x0f43);
// Section 2's rejection list, plus the C0 controls it allows nowhere. The surrogates are built
// with `fromCharCode`, which is the only way to make an unpaired one.
const FORBIDDEN: readonly string[] = [
  ...CHARS(0x0000, 0x0001, 0x0008, 0x000e, 0x001b, 0x001c, 0x001f, 0x007f, 0xfffd, 0xfffe, 0xffff),
  String.fromCharCode(0xd800),
  String.fromCharCode(0xdbff),
  String.fromCharCode(0xdc00),
  String.fromCharCode(0xdfff),
];
// What fidelity-norm/2.0.0 added to section 2: U+000B, U+000C, the C1 controls (U+0092 and
// U+0096 are what a text layer decoded as Latin-1 carries), and the bidirectional controls.
const FORBIDDEN_2_0_0: readonly string[] = CHARS(
  0x000b,
  0x000c,
  0x0080,
  0x0085,
  0x0092,
  0x0096,
  0x009f,
  0x061c,
  0x200e,
  0x200f,
  0x202a,
  0x202c,
  0x202e,
  0x2066,
  0x2068,
  0x2069,
);
// The accepted neighbours of each range fidelity-norm/2.0.0 rejects.
const NEAR_FORBIDDEN = CHARS(
  0x007e,
  0x00a0,
  0x00a1,
  0x061b,
  0x061d,
  0x200d,
  0x2029,
  0x202f,
  0x206a,
);
const PUNCTUATION: readonly string[] = [
  ...CHARS(0x002d, 0x2010, 0x2011, 0x2013, 0x2014, 0x2212),
  ...CHARS(0x0027, 0x0022, 0x2018, 0x2019, 0x201c, 0x201d),
  ...CHARS(0x003b, 0x003a, 0x002e, 0x002c, 0x0028, 0x0029, 0x002f),
];
const SUPERSCRIPT = CHARS(0x00b2, 0x00b3, 0x2082, 0x2070, 0x2081);
const JOINER = CHARS(0x200c, 0x200d);
const WORD_BODY = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";

function word(random: Random): string {
  let built = "";
  for (let position = 0; position < between(random, 1, 9); position += 1) {
    built += pick(random, Array.from(WORD_BODY));
  }
  return built;
}

// A fragment generator and the class name a divergence in it would be reported under.
type Fragment = { weight: number; className: string; make: (random: Random) => string };

const FRAGMENTS: readonly Fragment[] = [
  { weight: 30, className: "ascii-word", make: word },
  { weight: 12, className: "space", make: () => SPACE },
  { weight: 8, className: "whitespace-class", make: (random) => pick(random, WHITESPACE) },
  { weight: 3, className: "whitespace-run", make: (random) => pick(random, WHITESPACE).repeat(3) },
  { weight: 2, className: "near-whitespace", make: (random) => pick(random, NEAR_WHITESPACE) },
  { weight: 6, className: "invisible", make: (random) => pick(random, INVISIBLE) },
  {
    weight: 4,
    className: "combining-after-invisible",
    make: (random) => `${word(random)}${pick(random, INVISIBLE)}${pick(random, COMBINING)}`,
  },
  {
    weight: 3,
    className: "soft-hyphen-lf",
    make: (random) => `${word(random)}${SOFT_HYPHEN}${LF}${word(random)}`,
  },
  {
    weight: 3,
    className: "soft-hyphen-crlf",
    make: (random) => `${word(random)}${SOFT_HYPHEN}${CR}${LF}${word(random)}`,
  },
  {
    weight: 2,
    className: "soft-hyphen-cr-only",
    make: (random) => `${word(random)}${SOFT_HYPHEN}${CR}${word(random)}`,
  },
  { weight: 5, className: "ligature", make: (random) => pick(random, LIGATURE) },
  {
    weight: 3,
    className: "ligature-then-combining",
    make: (random) => `${pick(random, LIGATURE)}${pick(random, COMBINING)}`,
  },
  { weight: 2, className: "near-ligature", make: (random) => pick(random, NEAR_LIGATURE) },
  { weight: 5, className: "bullet", make: (random) => pick(random, BULLET) },
  { weight: 2, className: "near-bullet", make: (random) => pick(random, NEAR_BULLET) },
  // Step 4 replaces a bullet only at the start of a line and before whitespace.
  {
    weight: 3,
    className: "bullet-line-start",
    make: (random) =>
      `${LF}${pick(random, ["", SPACE, TAB])}${pick(random, [...BULLET, ...CHARS(0x2219, 0x2043)])}${pick(random, WHITESPACE)}${word(random)}`,
  },
  {
    weight: 3,
    className: "bullet-mid-line",
    make: (random) =>
      `${word(random)}${pick(random, ["", SPACE])}${pick(random, [...BULLET, ...NEAR_BULLET])}${pick(random, ["", SPACE])}${word(random)}`,
  },
  // A number with punctuation inside it: the span-edge rule must not treat `.`, `,` or `−` as a
  // word boundary.
  {
    weight: 3,
    className: "number-with-punctuation",
    make: (random) =>
      `${pick(random, ["", CP(0x2212), "-"])}${between(random, 0, 99)}${pick(random, [".", ",", "/"])}${between(random, 0, 999)}`,
  },
  // A number grouped with a space, a thin space or a joiner (`10 000`): an edge inside it cuts.
  {
    weight: 3,
    className: "number-grouped",
    make: (random) =>
      chance(random, 0.7)
        ? `${between(random, 1, 99)}${pick(random, GROUP_RUNS)}${String(
            between(random, 0, 999),
          ).padStart(3, "0")}`
        : `${between(random, 1, 99)}${pick(random, GROUP_JOINERS)}${pick(random, ["mg", "IU", "%"])}`,
  },
  // A bullet on a line with U+0009 (a table row) is content, wherever it stands.
  {
    weight: 2,
    className: "bullet-on-tab-line",
    make: (random) =>
      `${LF}${pick(random, ["", TAB])}${pick(random, BULLET)}${SPACE}${word(random)}${TAB}${word(random)}`,
  },
  { weight: 4, className: "nfc-singleton", make: (random) => pick(random, NFC_SINGLETON) },
  {
    weight: 4,
    className: "hangul-jamo",
    make: (random) => `${pick(random, HANGUL_JAMO)}${pick(random, HANGUL_JAMO)}`,
  },
  { weight: 3, className: "precomposed", make: (random) => pick(random, PRECOMPOSED) },
  {
    weight: 4,
    className: "decomposed",
    make: (random) => `${pick(random, Array.from("aeouAEOU"))}${pick(random, COMBINING)}`,
  },
  { weight: 5, className: "punctuation", make: (random) => pick(random, PUNCTUATION) },
  { weight: 2, className: "superscript", make: (random) => pick(random, SUPERSCRIPT) },
  { weight: 2, className: "joiner", make: (random) => pick(random, JOINER) },
  { weight: 2, className: "near-forbidden", make: (random) => pick(random, NEAR_FORBIDDEN) },
  {
    weight: 2,
    className: "soft-hyphen-space",
    make: (random) => `${word(random)}${SOFT_HYPHEN}${SPACE}${word(random)}`,
  },
];

const TOTAL_WEIGHT = FRAGMENTS.reduce((sum, item) => sum + item.weight, 0);

function fragment(random: Random): Fragment {
  let target = random() * TOTAL_WEIGHT;
  for (const candidate of FRAGMENTS) {
    target -= candidate.weight;
    if (target <= 0) return candidate;
  }
  return pick(random, FRAGMENTS);
}

type Passage = { text: string; classes: Set<string> };

function passage(random: Random, pieces: number): Passage {
  const classes = new Set<string>();
  let text = "";
  for (let position = 0; position < pieces; position += 1) {
    const chosen = fragment(random);
    classes.add(chosen.className);
    text += chosen.make(random);
  }
  return { text, classes };
}

// ----------------------------------------------------------------------------------------------
// Family (a): normalisation.

function normalizeCase(random: Random, seed: number, index: number): CorpusCase {
  const { text, classes } = passage(random, between(random, 1, 14));
  let input = text;
  // One case in eight carries a section 2 character, so the rejection path is walked about as
  // often as a single accepting path is.
  if (chance(random, 0.125)) {
    const at = between(random, 0, Array.from(input).length);
    const points = Array.from(input);
    const added = chance(random, 0.5);
    const forbidden = pick(random, added ? FORBIDDEN_2_0_0 : FORBIDDEN);
    input = [...points.slice(0, at), forbidden, ...points.slice(at)].join("");
    classes.add(added ? "forbidden-character-2-0-0" : "forbidden-character");
  }
  if (chance(random, 0.1)) {
    input = `${pick(random, WHITESPACE)}${input}${pick(random, WHITESPACE)}`;
    classes.add("whitespace-edges");
  }
  let expected: NormalizeExpectation;
  try {
    expected = { text: normalizeText(input) };
  } catch (error) {
    if (!(error instanceof NormalizationError)) throw error;
    expected = { error: error.code };
  }
  return {
    family: "normalize",
    index,
    seed,
    tag: "error" in expected ? "normalize/rejected" : "normalize/accepted",
    classes: [...classes].sort(),
    input,
    expected,
  };
}

// ----------------------------------------------------------------------------------------------
// Family (b): the XHTML scanner. Documents are assembled from the allowed grammar; two cases in
// five then carry a single deliberate violation, so the error codes are reached as densely as
// the accepting paths.

const XMLNS = `xmlns="http://www.w3.org/1999/xhtml"`;
// `pre` left the allowed elements in fidelity-norm/2.0.0 (it is a violation below); `sup` and
// `sub` hold text only and have their own generator.
const BLOCK_WRAPPERS = ["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"];
const INLINE_WRAPPERS = ["span", "b", "i", "u", "em", "strong", "small", "abbr"];

// Text that is safe inside markup: its own `<`, `&`, `>` and quotes are stripped, and entities
// are added back explicitly so every accepted entity form appears.
const MARKUP_UNSAFE = new RegExp(`[${CP(0x003c)}${CP(0x0026)}${CP(0x003e)}"']`, "gu");
// U+00AD before a line break rejects in narrative text (fidelity-norm/2.0.0). The passage
// alphabet makes one often; most are turned into U+00AD U+0020, which is accepted, so the
// rejection is reached without drowning the accepting paths.
const SOFT_HYPHEN_BREAK = new RegExp(`${SOFT_HYPHEN}${CR}?${LF}`, "u");
const SOFT_HYPHEN_BREAKS = new RegExp(`${SOFT_HYPHEN}${CR}?${LF}`, "gu");

function markupText(random: Random): Passage {
  const { text, classes } = passage(random, between(random, 1, 5));
  let safe = text.replace(MARKUP_UNSAFE, "");
  if (SOFT_HYPHEN_BREAK.test(safe)) {
    if (chance(random, 0.8)) safe = safe.replace(SOFT_HYPHEN_BREAKS, `${SOFT_HYPHEN}${SPACE}`);
    else classes.add("soft-hyphen-before-break");
  }
  if (chance(random, 0.25)) {
    safe += pick(random, [
      "&amp;",
      "&lt;",
      "&gt;",
      "&quot;",
      "&apos;",
      "&#65;",
      "&#x42;",
      "&#8212;",
      "&#x2014;",
      "&#x2019;",
      "&#1114111;",
      "&#x7E;",
      "&#xA0;",
      "&#x1D6FC;",
    ]);
    classes.add("entity");
  }
  // A line break in text is a space to a renderer, so a bullet after it is mid-line.
  if (chance(random, 0.08)) {
    const lineBreak = pick(random, [LF, `${CR}${LF}`, CR, "&#10;", "&#13;", "&#13;&#10;"]);
    safe += `${lineBreak}${pick(random, BULLET)}${SPACE}${word(random)}`;
    classes.add("text-line-break-then-bullet");
  }
  if (chance(random, 0.04)) {
    safe += CP(0x1d6fc);
    classes.add("supplementary-character");
  }
  return { text: safe, classes };
}

type Markup = { markup: string; classes: Set<string> };

// Only what fidelity-norm/2.0.0 allows: a language tag on the root, an `https://` link on `a`,
// `scope` on `th`. Everything else is a violation below.
function attributes(random: Random, element: string, isRoot = false): Markup {
  const classes = new Set<string>();
  let markup = "";
  if (isRoot && chance(random, 0.2)) {
    markup += ` ${pick(random, ["lang", "xml:lang"])}="${pick(random, ["en", "en-GB", "de"])}"`;
    classes.add("attribute-lang");
  }
  if (element === "a" && chance(random, 0.6)) {
    markup += ` href="${pick(random, ["https://example.org/a/b", "https://example.org/"])}"`;
    classes.add("attribute-href");
  }
  if (element === "th" && chance(random, 0.3)) {
    markup += ` scope="${pick(random, ["row", "col"])}"`;
    classes.add("attribute-scope");
  }
  return { markup, classes };
}

function markupTextNode(random: Random): Markup {
  const { text, classes } = markupText(random);
  classes.add("text");
  return { markup: text, classes };
}

// Text inside `sup` or `sub`: the digits and signs that fold, the letters and marks that are
// kept, the script digits that are kept, and the numbers and signs that have no script form.
const SCRIPT_PIECES: readonly { className: string; pool: readonly string[] }[] = [
  { className: "script-ascii-digit", pool: Array.from("0123456789") },
  { className: "script-ascii-sign", pool: Array.from("+-=()") },
  {
    className: "script-dash",
    pool: CHARS(
      0x2010,
      0x2011,
      0x2012,
      0x2013,
      0x2014,
      0x2015,
      0x2212,
      0xfe62,
      0xfe63,
      0xff0b,
      0xff0d,
    ),
  },
  // The fold forms the review added (U+02D7, U+FE58, U+2795, U+2796), drawn on their own so
  // each is reached.
  { className: "script-dash-review", pool: CHARS(0x02d7, 0xfe58, 0x2795, 0x2796, 0x2015) },
  {
    className: "script-letter",
    pool: [...Array.from("anmaxif"), ...CHARS(0x00ae, 0x002a, 0x002f)],
  },
  // Script digits and signs of both scripts: an element's own are kept, the other's reject.
  {
    className: "script-code-point",
    pool: CHARS(0x00b2, 0x00b3, 0x2070, 0x2082, 0x2089, 0x207b, 0x208a, 0x207e),
  },
  { className: "script-reference", pool: ["&#x2212;", "&#54;", "&#x2B;", "&#8315;", "&#x2082;"] },
  { className: "script-space", pool: [SPACE, TAB] },
];
// Drawn rarely, so the folding paths are not drowned by `unmappable-script`.
const SCRIPT_UNMAPPABLE = {
  className: "script-unmappable",
  pool: [
    ...CHARS(0x00b1, 0x2213, 0x0663, 0xff12, 0x00bd, 0x2163, 0x1d7ce, 0x0966),
    "&#xB1;",
    "&#x1D7CE;",
  ],
};
// Script letters of both kinds (an element's own are kept, the other's reject) and symbols,
// brackets and dashes outside the fold tables (reject).
const SCRIPT_ROUND_TWO = {
  className: "script-letter-or-symbol",
  pool: [
    ...CHARS(0x2071, 0x207f, 0x2090, 0x2093, 0x209c),
    ...CHARS(0xff1d, 0xfe59, 0x2e3a, 0xfe31, 0x007e, 0x005b, 0x005d, 0x2e17),
    "&lt;",
  ],
};

function scriptText(random: Random, classes: Set<string>): string {
  let text = "";
  for (let position = 0; position < between(random, 1, 4); position += 1) {
    const roll = random();
    const piece =
      roll < 0.05 ? SCRIPT_UNMAPPABLE : roll < 0.1 ? SCRIPT_ROUND_TWO : pick(random, SCRIPT_PIECES);
    classes.add(piece.className);
    text += pick(random, piece.pool);
  }
  return text;
}

function scriptElement(random: Random): Markup {
  const classes = new Set<string>(["script-element"]);
  const element = pick(random, ["sup", "sub"]);
  let inner = scriptText(random, classes);
  // An element inside `sup` or `sub` is `script-content`.
  if (chance(random, 0.08)) {
    inner += pick(random, ["<b>2</b>", "<sup>2</sup>", "<br/>", "<sub>n</sub>"]);
    classes.add("script-child-element");
  }
  return { markup: `<${element}>${inner}</${element}>`, classes };
}

// Whitespace a table container may hold between its parts.
function tableWhitespace(random: Random, classes: Set<string>): string {
  if (!chance(random, 0.15)) return "";
  classes.add("table-whitespace");
  return pick(random, [SPACE, LF, TAB, `${CR}${LF}`, `${LF}${SPACE}${SPACE}`]);
}

function table(random: Random, depth: number): Markup {
  const classes = new Set<string>(["table"]);
  // One width per table: every row covers the same number of slots (fidelity-norm/2.0.0). A row
  // of another width is the `table-shape` violation.
  const width = between(random, 0, 3);
  if (width === 0) classes.add("table-empty-rows");
  // One row group of `count` rows. Cells are laid out by the HTML table model and span columns
  // and rows now and then (fidelity-norm/3.0.0); a span made one too wide overlaps a cell or
  // leaves the row ragged, and one made one too tall runs past the group, which a renderer clips
  // (`table-shape`).
  const rows = (cell: "td" | "th", count: number): string => {
    const covered: boolean[][] = Array.from({ length: count }, () => []);
    let markup = "";
    for (let rowIndex = 0; rowIndex < count; rowIndex += 1) {
      const coveredHere = covered[rowIndex] ?? [];
      let slots = width;
      if (chance(random, 0.06)) {
        slots = width === 0 ? 1 : width - 1 + 2 * between(random, 0, 1);
        classes.add("table-uneven-row");
      }
      let cells = "";
      for (let column = 0; column < slots; column += 1) {
        if (coveredHere[column] === true) continue;
        let free = 0;
        while (column + free < slots && coveredHere[column + free] !== true) free += 1;
        let colspan = free >= 2 && chance(random, 0.3) ? between(random, 2, free) : 1;
        let rowspan =
          count - rowIndex >= 2 && chance(random, 0.25) ? between(random, 2, count - rowIndex) : 1;
        if (colspan > 1) classes.add("table-colspan");
        if (rowspan > 1) classes.add("table-rowspan");
        if (chance(random, 0.02)) {
          colspan += 1;
          classes.add("table-span-perturbed");
        }
        if (chance(random, 0.02)) {
          rowspan += 1;
          classes.add("table-span-clipped");
        }
        for (let down = rowIndex; down < Math.min(count, rowIndex + rowspan); down += 1) {
          const target = covered[down] ?? [];
          for (let across = column; across < column + colspan; across += 1) target[across] = true;
        }
        let spans = "";
        if (colspan > 1) spans += ` colspan="${String(colspan)}"`;
        if (rowspan > 1) spans += ` rowspan="${String(rowspan)}"`;
        const attribute = attributes(random, cell);
        for (const name of attribute.classes) classes.add(name);
        const inner = node(random, depth + 1, true);
        for (const name of inner.classes) classes.add(name);
        // A bullet inside a cell is content, never a list item (the cell is on a U+0009 line).
        let lead = "";
        if (chance(random, 0.08)) {
          lead = `${pick(random, BULLET)}${pick(random, [SPACE, TAB, "&#10;"])}`;
          classes.add("table-cell-bullet");
        }
        cells += `${tableWhitespace(random, classes)}<${cell}${attribute.markup}${spans}>${lead}${inner.markup}</${cell}>`;
      }
      // Content a renderer would move out of the table (`table-content`).
      if (chance(random, 0.05)) {
        cells += pick(random, ["x", "&#32;", "&#10;", "<span>x</span>", CP(0x00a0), "<br/>"]);
        classes.add("table-stray-content");
      }
      markup += `<tr>${cells}${tableWhitespace(random, classes)}</tr>`;
    }
    return markup;
  };
  let markup = "";
  if (chance(random, 0.25)) {
    const caption = markupText(random);
    for (const name of caption.classes) classes.add(name);
    markup += `<caption>${caption.text}</caption>`;
    classes.add("table-caption");
  }
  if (chance(random, 0.05)) {
    classes.add("table-no-rows");
  } else if (chance(random, 0.5)) {
    // Sections, in the one document order that renders as written.
    if (chance(random, 0.7)) {
      markup += `${tableWhitespace(random, classes)}<thead>${rows("th", between(random, 1, 2))}</thead>`;
      classes.add("table-thead");
    }
    for (let position = 0; position < between(random, 1, 2); position += 1) {
      markup += `${tableWhitespace(random, classes)}<tbody>${rows("td", between(random, 1, 3))}</tbody>`;
      classes.add("table-tbody");
    }
    if (chance(random, 0.4)) {
      markup += `<tfoot>${rows("td", between(random, 1, 2))}${tableWhitespace(random, classes)}</tfoot>`;
      classes.add("table-tfoot");
    }
  } else {
    markup += rows("td", between(random, 1, 3));
    classes.add("table-bare-rows");
  }
  if (chance(random, 0.03)) {
    markup = `${pick(random, ["Dose", "&#65;", "<p>x</p>"])}${markup}`;
    classes.add("table-stray-content");
  }
  return { markup: `<table>${markup}${tableWhitespace(random, classes)}</table>`, classes };
}

// Picture sources fidelity-norm/3.0.0 accepts: relative references and PNG or JPEG `data:` URIs.
const PICTURE_SOURCES: readonly string[] = [
  "~/_entity/annotation/0c1d2e3f-aaaa-bbbb-cccc-0123456789ab",
  "images/logo.png",
  "a_b/c-d~e.f",
  "x",
];
const PICTURE_DATA: readonly string[] = [
  "data:image/png;base64,iVBORw0KGgo=",
  "data:image/jpeg;base64,/9j/4AAQ",
  "data:image/png;base64,AA==",
];

// An `ol`'s attributes: every counter style, and starts on each side of the style's range.
function orderedListAttributes(random: Random, classes: Set<string>): string {
  // Now and then a style with a start at the edge of its range.
  if (chance(random, 0.3)) {
    classes.add("ordered-list-edge");
    return pick(random, [
      ' type="i" start="3998"',
      ' type="I" start="3999"',
      ' type="a" start="26"',
      ' type="A" start="702"',
      ' type="i" start="-1"',
      ' type="a" start="0"',
      ' type="i" start="9999"',
    ]);
  }
  let markup = "";
  if (chance(random, 0.6)) {
    markup += ` type="${pick(random, ["1", "a", "A", "i", "I"])}"`;
    classes.add("ordered-list-type");
  }
  if (chance(random, 0.4)) {
    markup += ` start="${pick(random, ["0", "1", "3", "-2", "26", "27", "3998", "9999", "-9999"])}"`;
    classes.add("ordered-list-start");
  }
  return markup;
}

// `inCell`: inside a table cell, where a nested table is refused (fidelity-norm/3.0.0), so none
// is generated there; the violation list has them.
function node(random: Random, depth: number, inCell = false): Markup {
  const classes = new Set<string>();
  if (depth > 3) return markupTextNode(random);
  const kind = random();
  if (kind < 0.3) return markupTextNode(random);
  if (kind < 0.4) {
    const element = pick(random, INLINE_WRAPPERS);
    const inner = node(random, depth + 1, inCell);
    for (const name of inner.classes) classes.add(name);
    classes.add("inline-element");
    return { markup: `<${element}>${inner.markup}</${element}>`, classes };
  }
  if (kind < 0.48) {
    const before = markupTextNode(random);
    const script = scriptElement(random);
    for (const name of [...before.classes, ...script.classes]) classes.add(name);
    return { markup: `${before.markup}${script.markup}`, classes };
  }
  if (kind < 0.54) {
    const element = pick(random, ["cite", "code"]);
    const inner = markupTextNode(random);
    for (const name of inner.classes) classes.add(name);
    classes.add("inline-element");
    return { markup: `<${element}>${inner.markup}</${element}>`, classes };
  }
  if (kind < 0.6) {
    const attribute = attributes(random, "a");
    const inner = markupTextNode(random);
    for (const name of [...attribute.classes, ...inner.classes]) classes.add(name);
    classes.add("anchor");
    return { markup: `<a${attribute.markup}>${inner.markup}</a>`, classes };
  }
  if (kind < 0.74) {
    const element = pick(random, BLOCK_WRAPPERS);
    const inner = node(random, depth + 1, inCell);
    for (const name of inner.classes) classes.add(name);
    classes.add("block-element");
    // Whitespace inside a tag is TAB, LF, CR or SPACE, and those are accepted; one tag in
    // twenty carries whitespace that is only `\s` (U+00A0, U+3000, ...), which is malformed.
    const tagSpace = (): string => {
      if (chance(random, 0.05)) {
        classes.add("tag-non-ascii-whitespace");
        return pick(random, NON_TAG_WHITESPACE);
      }
      if (chance(random, 0.15)) {
        classes.add("tag-ascii-whitespace");
        return pick(random, TAG_WHITESPACE);
      }
      return "";
    };
    return {
      markup: `<${element}${tagSpace()}>${inner.markup}</${element}${tagSpace()}>`,
      classes,
    };
  }
  if (kind < 0.8) {
    const ordered = chance(random, 0.5);
    const listAttributes = ordered ? orderedListAttributes(random, classes) : "";
    // Now and then enough items to pass `z` and to cross a roman boundary.
    const count = chance(random, 0.05) ? 28 : between(random, 1, 3);
    if (count === 28) classes.add("ordered-list-long");
    let items = "";
    for (let position = 0; position < count; position += 1) {
      const inner = count === 28 ? markupTextNode(random) : node(random, depth + 1, inCell);
      for (const name of inner.classes) classes.add(name);
      items += `<li>${inner.markup}</li>${chance(random, 0.1) ? pick(random, [SPACE, LF, TAB]) : ""}`;
    }
    classes.add(ordered ? "ordered-list" : "list");
    const element = ordered ? "ol" : "ul";
    return { markup: `<${element}${listAttributes}>${items}</${element}>`, classes };
  }
  if (kind < 0.86) {
    let items = "";
    for (let position = 0; position < between(random, 1, 2); position += 1) {
      const term = markupTextNode(random);
      const definition = markupTextNode(random);
      for (const name of [...term.classes, ...definition.classes]) classes.add(name);
      items += `<dt>${term.markup}</dt><dd>${definition.markup}</dd>`;
    }
    classes.add("definition-list");
    return { markup: `<dl>${items}</dl>`, classes };
  }
  if (kind < 0.94) return inCell ? markupTextNode(random) : table(random, depth);
  if (kind < 0.955) {
    const before = markupTextNode(random);
    const data = chance(random, 0.3);
    const source = pick(random, data ? PICTURE_DATA : PICTURE_SOURCES);
    for (const name of before.classes) classes.add(name);
    classes.add(data ? "picture-data" : "picture");
    return {
      markup: `${before.markup}<img${pick(random, ["", SPACE, LF])} src="${source}"${pick(random, ["", SPACE])}/>${chance(random, 0.5) ? word(random) : ""}`,
      classes,
    };
  }
  if (kind < 0.97) {
    classes.add("self-closing-block");
    return { markup: pick(random, ["<hr/>", `<hr${SPACE}/>`]), classes };
  }
  const inner = markupTextNode(random);
  for (const name of inner.classes) classes.add(name);
  classes.add("line-break");
  return {
    markup: `${inner.markup}${pick(random, ["<br/>", `<br${SPACE}/>`])}${inner.markup}`,
    classes,
  };
}

// A single deliberate violation, applied to an otherwise well-formed document. Each names the
// class it belongs to; between them they reach every error code the scanner can raise.
type Violation = {
  className: string;
  apply: (body: string, rootAttributes: string, random: Random) => string;
};

const TAG_WHITESPACE: readonly string[] = [SPACE, TAB, LF, CR, `${CR}${LF}`, `${SPACE}${TAB}`];
// Whitespace to `\s` but not inside a tag: an HTML parser reads it as part of the tag name.
const NON_TAG_WHITESPACE = CHARS(
  0x00a0,
  0x1680,
  0x2000,
  0x2003,
  0x200a,
  0x2028,
  0x2029,
  0x202f,
  0x205f,
  0x3000,
  0xfeff,
);

const root = (body: string, rootAttributes: string): string =>
  `<div ${XMLNS}${rootAttributes}>${body}</div>`;

const VIOLATIONS: readonly Violation[] = [
  {
    className: "uppercase-element",
    apply: (body, attrs) => root(`${body}<P>x</P>`, attrs),
  },
  {
    className: "unknown-element",
    apply: (body, attrs, random) => {
      const element = pick(random, ["q", "iframe", "style", "script", "del", "s", "math", "pre"]);
      return root(`${body}<${element}>x</${element}>`, attrs);
    },
  },
  {
    className: "forbidden-attribute-name",
    apply: (body, attrs, random) => {
      const attribute = pick(random, [
        `style="x"`,
        `hidden="hidden"`,
        `title="t"`,
        `href="https://example.org/"`,
        `class="c"`,
        `id="s1"`,
        `lang="en"`,
        `xml:lang="en"`,
        `scope="row"`,
      ]);
      return root(`${body}<p ${attribute}>x</p>`, attrs);
    },
  },
  {
    className: "forbidden-attribute-value",
    apply: (body, attrs, random) => {
      const attribute = pick(random, [
        `href="https://example.org/a?q=1"`,
        `href="javascript:x"`,
        `href="#x"`,
        `href="https://example.org/a${LF}"`,
        `href=""`,
      ]);
      return root(`${body}<p><a ${attribute}>x</a></p>`, attrs);
    },
  },
  {
    className: "forbidden-root-attribute",
    apply: (body, attrs, random) => {
      const attribute = pick(random, [
        ` id="s1"`,
        ` class="c"`,
        ` lang="${"x".repeat(33)}"`,
        ` lang="en${LF}"`,
        ` lang="en" lang="de"`,
      ]);
      return root(body, `${attrs}${attribute}`);
    },
  },
  {
    className: "duplicate-attribute",
    apply: (body, attrs) =>
      root(`${body}<p><a href="https://example.org/" href="https://example.org/">a</a></p>`, attrs),
  },
  {
    className: "nested-xmlns",
    apply: (body, attrs) => root(`${body}<p ${XMLNS}>x</p>`, attrs),
  },
  {
    // Span values outside `[1-9][0-9]{0,2}|1000`, and `scope` on a `td`.
    className: "spanned-cell",
    apply: (body, attrs, random) => {
      const attribute = pick(random, ["colspan", "rowspan", "scope"]);
      const value = pick(random, ["0", "1001", "01", " 2", "2.0", "-1", "", "row", "1000"]);
      return root(`${body}<table><tr><td ${attribute}="${value}">x</td></tr></table>`, attrs);
    },
  },
  {
    className: "table-span-overlap",
    apply: (body, attrs, random) => {
      const inner = pick(random, [
        '<table><tr><td>Adults</td><td rowspan="2">10 mg</td></tr><tr><td colspan="2">Children</td></tr></table>',
        '<table><tr><td rowspan="2">a</td><td>b</td></tr><tr><td>c</td><td colspan="2">d</td></tr></table>',
        '<table><tr><td>a</td><td rowspan="2">b</td><td>c</td></tr><tr><td colspan="3">d</td></tr></table>',
      ]);
      return root(`${body}${inner}`, attrs);
    },
  },
  {
    className: "table-span-hole",
    apply: (body, attrs, random) => {
      const inner = pick(random, [
        '<table><tr><td>a</td><td>b</td><td rowspan="2">c</td></tr><tr><td>d</td></tr></table>',
        '<table><tr><td>a</td><td rowspan="2">b</td></tr><tr></tr></table>',
        '<table><tr><td>a</td><td>b</td><td rowspan="2">c</td></tr><tr><td>d</td><td>e</td></tr></table>',
      ]);
      return root(`${body}${inner}`, attrs);
    },
  },
  {
    className: "picture-source",
    apply: (body, attrs, random) => {
      const source = pick(random, [
        "javascript:x",
        "//evil/x",
        "../x",
        "a/../x",
        "./x",
        "a/./x",
        "/x",
        "https://example.org/x",
        "a b",
        "a//b",
        "~/_entity/annotation/.x",
        "a/b/",
      ]);
      return root(`${body}<p>a<img src="${source}"/>b</p>`, attrs);
    },
  },
  {
    className: "picture-data",
    apply: (body, attrs, random) => {
      const source = pick(random, [
        "data:image/svg+xml;base64,AAAA",
        "data:image/png;base64,AAAAA",
        "data:image/png;base64,AA=A",
        "data:image/png;base64,A===",
        "data:image/png;base64,=AAA",
        "data:image/png;base64,",
        "data:image/jpeg;base64,AAA=",
        "data:image/png;base64,AA==",
      ]);
      return root(`${body}<p>a<img src="${source}"/>b</p>`, attrs);
    },
  },
  {
    className: "picture-markup",
    apply: (body, attrs, random) => {
      const inner = pick(random, [
        '<p><img src="x" alt="Take 10 mg"/></p>',
        "<p><img/></p>",
        "<p>a<img />b</p>",
        '<p><img src="x"></img></p>',
        '<p><img src="x" src="y"/></p>',
      ]);
      return root(`${body}${inner}`, attrs);
    },
  },
  {
    // The counter styles at the edges of their ranges.
    className: "ordered-list-boundaries",
    apply: (body, attrs, random) => {
      const list = pick(random, [
        ' type="I" start="3998"',
        ' type="i" start="3999"',
        ' type="a" start="25"',
        ' type="A" start="701"',
        ' type="I" start="-1"',
        ' start="-9999"',
      ]);
      return root(`${body}<ol${list}><li>a</li><li>b</li><li>c</li></ol>`, attrs);
    },
  },
  {
    className: "attribute-limits",
    apply: (body, attrs, random) => {
      const inner = pick(random, [
        '<table><tr><td colspan="1000">a</td></tr></table>',
        '<table><tr><td colspan="999">a</td></tr></table>',
        '<table><tr><td colspan="1001">a</td></tr></table>',
        '<ol start="-0"><li>a</li></ol>',
        '<ol start="9999"><li>a</li></ol>',
        '<ol start="-9999"><li>a</li></ol>',
        '<ol start="10000"><li>a</li></ol>',
      ]);
      return root(`${body}${inner}`, attrs);
    },
  },
  {
    className: "reserved-reference",
    apply: (body, attrs, random) => {
      const reference = pick(random, [
        "&#xFFFC;",
        "&#65532;",
        "&#xFDD2;",
        "&#64976;",
        "&#xFDEF;",
        "&#xFDE0;",
        "&#xFDD9;",
        CP(0xfde5),
      ]);
      return root(`${body}<p>a${reference}b</p>`, attrs);
    },
  },
  {
    // Grids a renderer draws with an overlap, a clipped span or a hole, and grids it accepts.
    className: "table-span-shape",
    apply: (body, attrs, random) => {
      const inner = pick(random, [
        '<table><tr><td>Adults</td><td rowspan="2">10 mg</td></tr><tr><td colspan="2">Children</td></tr></table>',
        '<table><thead><tr><td rowspan="2">a</td><td>b</td></tr></thead><tbody><tr><td>c</td></tr></tbody></table>',
        '<table><tr><td colspan="2">a</td><td>b</td></tr><tr><td>1</td><td>2</td><td>3</td></tr></table>',
        '<table><tr><td rowspan="2">A</td><td>B</td><td>C</td></tr><tr><td>D</td><td>E</td></tr></table>',
        '<table><tr><td>a</td><td>b</td><td rowspan="2">c</td></tr><tr><td>d</td></tr></table>',
        '<table><tr><td rowspan="3">a</td><td>b</td></tr><tr><td>c</td></tr></table>',
        '<table><tbody><tr><td rowspan="2">a</td></tr></tbody><tbody><tr><td>b</td></tr></tbody></table>',
        '<table><tr><td colspan="2" rowspan="2">X</td><td>a</td></tr><tr><td>b</td></tr><tr><td>c</td><td></td><td>d</td></tr></table>',
        '<table><tr><td>a</td><td rowspan="2">b</td></tr><tr></tr></table>',
        '<table><tr><td rowspan="2">a</td></tr><tr></tr></table>',
      ]);
      return root(`${body}${inner}`, attrs);
    },
  },
  {
    className: "nested-table",
    apply: (body, attrs, random) => {
      const inner = pick(random, [
        "<table><tr><td><table><tr><td>a</td></tr></table></td></tr></table>",
        "<table><tr><td><div><table></table></div></td></tr></table>",
        "<table><tr><th><ul><li><table><tr><td>a</td></tr></table></li></ul></th></tr></table>",
      ]);
      return root(`${body}${inner}`, attrs);
    },
  },
  {
    className: "list-content",
    apply: (body, attrs, random) => {
      const inner = pick(random, [
        "<ol> text <li>x</li></ol>",
        "<ul><p>x</p></ul>",
        "<ol>&#32;<li>a</li></ol>",
        "<ul><li>a</li><br/></ul>",
        '<ol><img src="x"/><li>a</li></ol>',
        `<ul>${CP(0x00a0)}<li>a</li></ul>`,
        "<ol><ol><li>a</li></ol></ol>",
      ]);
      return root(`${body}${inner}`, attrs);
    },
  },
  {
    className: "li-outside-list",
    apply: (body, attrs, random) => {
      const inner = pick(random, [
        "<div><li>x</li></div>",
        "<ol><li>a<li>b</li></li></ol>",
        "<li>x</li>",
        "<table><tr><td><li>x</li></td></tr></table>",
        "<ol><li>a<blockquote><li>b</li></blockquote></li></ol>",
      ]);
      return root(`${body}${inner}`, attrs);
    },
  },
  {
    className: "list-attribute",
    apply: (body, attrs, random) => {
      const inner = pick(random, [
        '<ol reversed="reversed"><li>a</li></ol>',
        '<ol type="disc"><li>a</li></ol>',
        '<ol start="-0"><li>a</li></ol>',
        '<ol start="007"><li>a</li></ol>',
        '<ol start="10000"><li>a</li></ol>',
        '<ul type="a"><li>a</li></ul>',
        '<ol><li value="3">a</li></ol>',
        '<ul start="2"><li>a</li></ul>',
        '<ol type="a" type="i"><li>a</li></ol>',
      ]);
      return root(`${body}${inner}`, attrs);
    },
  },
  {
    className: "picture-violation",
    apply: (body, attrs, random) => {
      const inner = pick(random, [
        '<p><img src="x" alt="Take 10 mg"/></p>',
        "<p><img/></p>",
        '<p><img src="x"></img></p>',
        '<p><img src="x" title="t"/></p>',
        '<p><img src="javascript:x"/></p>',
        '<p><img src="//evil/x"/></p>',
        '<p><img src="../x"/></p>',
        '<p><img src="a/../x"/></p>',
        '<p><img src="a/./x"/></p>',
        '<p><img src="/x"/></p>',
        '<p><img src="https://example.org/x"/></p>',
        '<p><img src="data:image/svg+xml;base64,AAAA"/></p>',
        '<p><img src="data:image/png;base64,AAAAA"/></p>',
        '<p><img src="data:image/png;base64,AA=A"/></p>',
        '<p><img src="data:image/png;base64,"/></p>',
        '<p><img src="a b"/></p>',
        '<p><sup><img src="x"/></sup></p>',
        '<table><tr><img src="x"/></tr></table>',
        '<p><img src="x"/><img src="x" src="y"/></p>',
      ]);
      return root(`${body}${inner}`, attrs);
    },
  },
  {
    className: "reserved-character",
    apply: (body, attrs, random) => {
      const reserved = pick(random, [
        CP(0xfffc),
        CP(0xfdd0),
        CP(0xfdd3),
        CP(0xfdd5),
        CP(0xfdef),
        "&#xFFFC;",
        "&#65020;",
        "&#xFDD2;",
        "&#64976;",
      ]);
      const where = between(random, 0, 3);
      if (where === 0) return root(`${body}<p>a${reserved}b</p>`, attrs);
      if (where === 1) return root(`${body}<table><tr>${reserved}<td>a</td></tr></table>`, attrs);
      if (where === 2) return root(`${body}<p><sup>${reserved}</sup></p>`, attrs);
      return `${reserved}${root(body, attrs)}`;
    },
  },
  {
    // Code points next to the reserved ones, which are ordinary text.
    className: "near-reserved",
    apply: (body, attrs, random) => {
      const near = pick(random, [CP(0xfdcf), CP(0xfdf0), CP(0xfffb), "&#xFFFB;", "&#xFDCF;"]);
      return root(`${body}<p>a${near}b</p>`, attrs);
    },
  },
  {
    className: "entity-non-ascii-digits",
    apply: (body, attrs, random) => {
      // Fullwidth and Arabic-Indic digits: `\d` matches them in Python and not in JavaScript.
      const entity = pick(random, [
        `&#${CP(0xff16, 0xff15)};`,
        `&#x${CP(0xff14, 0xff12)};`,
        `&#${CP(0x0660, 0x0661)};`,
        `&#${CP(0x1d7ce)};`,
      ]);
      return root(`${body}<p>${entity}</p>`, attrs);
    },
  },
  {
    className: "unknown-entity",
    apply: (body, attrs, random) => {
      const entity = pick(random, ["&nbsp;", "&copy;", "&#x110000;", "&#1114112;", "&#;", "&#x;"]);
      return root(`${body}<p>${entity}</p>`, attrs);
    },
  },
  {
    className: "stray-amp",
    apply: (body, attrs) => root(`${body}<p>a & b</p>`, attrs),
  },
  {
    className: "stray-lt",
    apply: (body, attrs) => root(`${body}<p>a < b</p>`, attrs),
  },
  {
    className: "malformed-tag",
    apply: (body, attrs, random) => {
      const broken = pick(random, [`<p class=x>a</p>`, `<p id='a>a</p>`, `<p <x>a</p>`, `<p`]);
      return root(`${body}${broken}`, attrs);
    },
  },
  { className: "comment", apply: (body, attrs) => root(`${body}<!-- c -->`, attrs) },
  { className: "cdata", apply: (body, attrs) => root(`${body}<![CDATA[x]]>`, attrs) },
  { className: "doctype", apply: (body, attrs) => `<!DOCTYPE html>${root(body, attrs)}` },
  { className: "processing-instruction", apply: (body, attrs) => root(`${body}<?x?>`, attrs) },
  {
    className: "unbalanced-tag",
    apply: (body, attrs) => `<div ${XMLNS}${attrs}>${body}<p>a</div>`,
  },
  { className: "stray-end-tag", apply: (body, attrs) => `${root(body, attrs)}</p>` },
  {
    className: "misnested-tag",
    apply: (body, attrs) => root(`${body}<p><b>a</p></b>`, attrs),
  },
  {
    className: "multiple-roots",
    apply: (body, attrs) => `${root(body, attrs)}<div ${XMLNS}><p>b</p></div>`,
  },
  { className: "text-outside-root", apply: (body, attrs) => `x${root(body, attrs)}` },
  { className: "root-missing-xmlns", apply: (body, attrs) => `<div${attrs}>${body}</div>` },
  {
    className: "root-not-div",
    apply: (body, attrs) => `<p ${XMLNS}${attrs}>${body}</p>`,
  },
  {
    className: "table-section-order",
    apply: (body, attrs, random) => {
      const inner = pick(random, [
        "<tfoot><tr><td>f</td></tr></tfoot><tbody><tr><td>b</td></tr></tbody>",
        "<tbody><tr><td>b</td></tr></tbody><thead><tr><th>h</th></tr></thead>",
        "<thead><tr><th>h</th></tr></thead><thead><tr><th>i</th></tr></thead>",
        "<tfoot><tr><td>f</td></tr></tfoot><tfoot><tr><td>g</td></tr></tfoot>",
      ]);
      return root(`${body}<table>${inner}</table>`, attrs);
    },
  },
  {
    className: "table-structure",
    apply: (body, attrs, random) => {
      const inner = pick(random, [
        "<tr><td>a</td></tr><tbody><tr><td>b</td></tr></tbody>",
        "<tbody><tr><td>b</td></tr></tbody><caption>c</caption>",
        "<caption>c</caption><caption>d</caption>",
        "<thead><tr><th>h</th></tr></thead><tr><td>a</td></tr>",
      ]);
      return root(`${body}<table>${inner}</table>`, attrs);
    },
  },
  {
    className: "table-misnesting",
    apply: (body, attrs, random) => {
      const inner = pick(random, [
        "<table><td>a</td></table>",
        "<p><tr><td>a</td></tr></p>",
        "<table><tbody><td>a</td></tbody></table>",
        "<ul><caption>c</caption></ul>",
        "<table><tr><caption>c</caption></tr></table>",
      ]);
      return root(`${body}${inner}`, attrs);
    },
  },
  {
    className: "table-content",
    apply: (body, attrs, random) => {
      const inner = pick(random, [
        "<table>x<tr><td>a</td></tr></table>",
        "<table><tbody>&#65;<tr><td>a</td></tr></tbody></table>",
        "<table><tr><td>a</td>&#32;</tr></table>",
        "<table><tr><span>a</span></tr></table>",
        "<table><thead><p>a</p></thead></table>",
        "<table><tr><table></table></tr></table>",
        `<table><tr>${CP(0x00a0)}<td>a</td></tr></table>`,
        "<table>&#133;</table>",
      ]);
      return root(`${body}${inner}`, attrs);
    },
  },
  {
    className: "table-shape",
    apply: (body, attrs, random) => {
      const inner = pick(random, [
        "<table><tr><td>a</td></tr><tr><td>b</td><td>c</td></tr></table>",
        "<table><thead><tr><th>h</th><th>i</th></tr></thead><tbody><tr><td>a</td></tr></tbody></table>",
        "<table><tr></tr><tr><td>a</td></tr></table>",
        "<table><tr><td><table><tr><td>a</td></tr><tr></tr></table></td></tr></table>",
      ]);
      return root(`${body}${inner}`, attrs);
    },
  },
  {
    className: "void-element",
    apply: (body, attrs, random) => {
      const inner = pick(random, [
        "<p>10<sup/>6 mg</p>",
        '<p><a href="https://example.org/"/>text</p>',
        "<p>Do<br>not</br> take</p>",
        "<p>a</p><hr><p>b</p>",
        "<hr></hr>",
        "<table><tr><td/></tr></table>",
        "<ul><li/></ul>",
        "<p/>",
      ]);
      return root(`${body}${inner}`, attrs);
    },
  },
  {
    // Two violations at one start tag: `void-element` is decided before the parent check.
    className: "void-element-and-parent",
    apply: (body, attrs, random) => {
      const inner = pick(random, [
        "<table><br></table>",
        "<p><sup><hr></sup></p>",
        "<table><tr><span/></tr></table>",
        "<ul><li><sub><td/></sub></li></ul>",
      ]);
      return root(`${body}${inner}`, attrs);
    },
  },
  { className: "self-closing-root", apply: (_body, attrs) => `<div ${XMLNS}${attrs}/>` },
  {
    className: "soft-hyphen-at-boundary",
    apply: (body, attrs, random) => {
      const inner = pick(random, [
        `<p>a${SOFT_HYPHEN}</p>`,
        `<p>a${SOFT_HYPHEN}<br/>b</p>`,
        `<p>a${SOFT_HYPHEN}<hr/></p>`,
        `<p>a${SOFT_HYPHEN}${LF}b</p>`,
        `<p>a${SOFT_HYPHEN}${CR}${LF}b</p>`,
        "<p>a&#173;&#10;b</p>",
        "<p>a&#173;&#13;<br/>b</p>",
        "<p>a&#173;&#13;&#10;b</p>",
      ]);
      return root(`${body}${inner}`, attrs);
    },
  },
  {
    className: "soft-hyphen-accepted",
    apply: (body, attrs, random) => {
      const inner = pick(random, [
        `<p>a${SOFT_HYPHEN}${SPACE}b</p>`,
        "<p>a&#173;&#13;b</p>",
        `<p>a${SOFT_HYPHEN}${CR}b</p>`,
        "<p>a&#173;b</p>",
      ]);
      return root(`${body}${inner}`, attrs);
    },
  },
  {
    className: "surrogate-split-markup",
    apply: (body, attrs, random) => {
      const high = String.fromCharCode(0xd835);
      const low = String.fromCharCode(0xdefc);
      const inner = pick(random, [
        `<p>${high}<b></b>${low}</p>`,
        `<p>${high}</p><p>${low}</p>`,
        `<p>${high}&#65;${low}</p>`,
      ]);
      return root(`${body}${inner}`, attrs);
    },
  },
  {
    className: "surrogate-by-reference",
    apply: (body, attrs, random) => {
      const inner = pick(random, [
        "<p>&#xD835;&#xDEFC;</p>",
        "<p>&#55349;&#57084;</p>",
        `<p>&#xD835;${String.fromCharCode(0xdefc)}</p>`,
        "<p>&#xDEFC;</p>",
      ]);
      return root(`${body}${inner}`, attrs);
    },
  },
  {
    className: "forbidden-reference",
    apply: (body, attrs, random) => {
      const reference = pick(random, [
        "&#133;",
        "&#x85;",
        "&#x92;",
        "&#11;",
        "&#xC;",
        "&#x202E;",
        "&#8206;",
        "&#x61C;",
        "&#x2066;",
        "&#0;",
        "&#x7F;",
      ]);
      return root(`${body}<p>a${reference}b</p>`, attrs);
    },
  },
  {
    className: "tag-non-ascii-whitespace",
    apply: (body, attrs, random) => {
      const space = pick(random, NON_TAG_WHITESPACE);
      const inner = pick(random, [
        `<p>10<sup${space}>6</sup></p>`,
        `<p>10<sup>6</sup${space}></p>`,
        `<p>a<br${space}/>b</p>`,
        `<table${space}><tr><td>a</td></tr></table>`,
        `<table><tr><td${space}>a</td></tr></table>`,
        `<p><a${space}href="https://example.org/">a</a></p>`,
        `<p><a href${space}="https://example.org/">a</a></p>`,
      ]);
      return root(`${body}${inner}`, attrs);
    },
  },
  {
    className: "forbidden-raw-character",
    apply: (body, attrs, random) => {
      const forbidden = pick(random, FORBIDDEN_2_0_0);
      const where = between(random, 0, 3);
      if (where === 0) return root(`${body}<p>a${forbidden}b</p>`, attrs);
      if (where === 1) return `${forbidden}${root(body, attrs)}`;
      if (where === 2) return root(`${body}<p${forbidden}>a</p>`, attrs);
      return root(body, `${attrs} lang="en${forbidden}"`);
    },
  },
];

let violationTurn = 0;

function xhtmlCase(random: Random, seed: number, index: number): CorpusCase {
  const classes = new Set<string>();
  const attribute = attributes(random, "div", true);
  for (const name of attribute.classes) classes.add(name);
  let body = "";
  for (let position = 0; position < between(random, 1, 4); position += 1) {
    const child = node(random, 1);
    for (const name of child.classes) classes.add(name);
    body += child.markup;
  }
  let input: string;
  if (chance(random, 0.4)) {
    // In turn rather than at random: with this many classes a random pick leaves some class out
    // of a 2000-case corpus at some seeds, and the coverage requirement then fails.
    const violation = VIOLATIONS[violationTurn % VIOLATIONS.length];
    if (violation === undefined) throw new Error("no violations");
    violationTurn += 1;
    classes.add(violation.className);
    input = violation.apply(body, attribute.markup, random);
  } else {
    input = root(body, attribute.markup);
  }
  if (chance(random, 0.15)) {
    const lead = pick(random, [SPACE, LF, TAB, `${CR}${LF}`]);
    input = `${lead}${input}${pick(random, ["", LF, SPACE + SPACE])}`;
    classes.add("whitespace-outside-root");
  }
  let expected: XhtmlExpectation;
  try {
    expected = { text: xhtmlToText(input) };
  } catch (error) {
    if (!(error instanceof XhtmlError)) throw error;
    expected = { error: error.code };
  }
  return {
    family: "xhtml",
    index,
    seed,
    tag: "error" in expected ? `xhtml/${expected.error}` : "xhtml/accepted",
    classes: [...classes].sort(),
    input,
    expected,
  };
}

// ----------------------------------------------------------------------------------------------
// Family (c): the verifier. Small synthetic documents with running headers and footers, then a
// provenance layout drawn from the span arrangements the specification distinguishes.

type SourceSpanLike = { page: number; startOffset: number; endOffset: number; textSha256: string };
type BuiltPage = { page: SourcePage; sentences: { start: number; end: number }[] };
type VerifySection = { sourceKey: string; div: string; spans: SourceSpanLike[] };

// A sentence must carry no line terminator of its own: the line structure of a page is exactly
// what the body-boundary rules are about, so it is built here rather than drawn.
const LINE_TERMINATORS = new RegExp(`[${CP(0x000a)}${CP(0x000d)}${CP(0x2028)}${CP(0x2029)}]`, "gu");

// How a page's body is laid out around its sentences. `blank-head` starts the body with an
// empty line; `hyphen-end` ends it with a word continued on the next page (U+00AD before the
// final line feed); `no-final-lf` leaves the final line feed out before the footer, and
// `no-final-lf-at-end` leaves it out at the very end of the page, where fidelity-norm/1.1.1 still
// accepted it.
type PageShape = "plain" | "blank-head" | "hyphen-end" | "no-final-lf" | "no-final-lf-at-end";

function pageShape(random: Random): PageShape {
  const roll = random();
  if (roll < 0.1) return "blank-head";
  if (roll < 0.2) return "hyphen-end";
  if (roll < 0.24) return "no-final-lf";
  if (roll < 0.28) return "no-final-lf-at-end";
  return "plain";
}

function buildPage(random: Random, number: number, shape: PageShape): BuiltPage {
  const header = `HEADER ${word(random)}${LF}`;
  const headerLength = Array.from(header).length;
  const sentences: { start: number; end: number }[] = [];
  let body = shape === "blank-head" ? LF : "";
  for (let position = 0; position < between(random, 2, 4); position += 1) {
    const { text } = passage(random, between(random, 3, 8));
    const sentence = text.replace(LINE_TERMINATORS, SPACE).trim();
    if (sentence.length === 0) continue;
    const start = headerLength + Array.from(body).length;
    body += `${sentence}${LF}`;
    sentences.push({ start, end: start + Array.from(sentence).length });
  }
  // A line with a grouped number (`is 10  000 IU`, review round 3), so span edges inside the
  // number are common enough that the digit-group rule is exercised, not only reachable.
  if (chance(random, 0.35)) {
    const digits = String(between(random, 0, 999)).padStart(3, "0");
    const runs = chance(random, 0.7) ? GROUP_RUNS.filter((run) => run.length > 1) : GROUP_RUNS;
    const grouped = `${word(random)}${SPACE}${between(random, 1, 99)}${pick(random, runs)}${digits}${SPACE}${word(random)}`;
    const start = headerLength + Array.from(body).length;
    body += `${grouped}${LF}`;
    sentences.push({ start, end: start + Array.from(grouped).length });
  }
  // A table row whose first cell starts with a bullet (review round 3): a section cut before
  // the row's U+0009 must still read the bullet as content.
  if (chance(random, 0.2)) {
    const row = `${pick(random, BULLET)}${SPACE}${word(random)}${TAB}${word(random)}`;
    const start = headerLength + Array.from(body).length;
    body += `${row}${LF}`;
    sentences.push({ start, end: start + Array.from(row).length });
  }
  if (sentences.length === 0) {
    const filler = word(random);
    const start = headerLength + Array.from(body).length;
    sentences.push({ start, end: start + filler.length });
    body += `${filler}${LF}`;
  }
  if (shape === "hyphen-end") body = `${body.slice(0, -1)}${SOFT_HYPHEN}${LF}`;
  if (shape === "no-final-lf" || shape === "no-final-lf-at-end") body = body.slice(0, -1);
  const footer = shape === "no-final-lf-at-end" ? "" : `Page ${number}`;
  const text = `${header}${body}${footer}`;
  return {
    page: {
      page: number,
      text,
      bodyStart: headerLength,
      bodyEnd: headerLength + Array.from(body).length,
    },
    sentences,
  };
}

function sliceOf(page: SourcePage, start: number, end: number): string {
  return Array.from(page.text).slice(start, end).join("");
}

function narrativeFor(text: string): string {
  // The narrative is the same text as markup, so an exact span verifies and any shift does not.
  const escaped = text
    .split(CP(0x0026))
    .join("&amp;")
    .split(CP(0x003c))
    .join("&lt;")
    .split(CP(0x003e))
    .join("&gt;");
  return `<div ${XMLNS}><p>${escaped}</p></div>`;
}

function spanOver(page: SourcePage, start: number, end: number): SourceSpanLike {
  const text = sliceOf(page, start, end);
  return { page: page.page, startOffset: start, endOffset: end, textSha256: sha256Utf8(text) };
}

function sentenceSection(pages: BuiltPage[], random: Random, shift: number): VerifySection {
  const built = pick(random, pages);
  const sentence =
    built.sentences.length > 0
      ? pick(random, built.sentences)
      : { start: built.page.bodyStart, end: built.page.bodyEnd };
  const start = Math.max(built.page.bodyStart, sentence.start + shift);
  const end = Math.max(start + 1, Math.min(built.page.bodyEnd, sentence.end + shift));
  return {
    sourceKey: "k",
    div: narrativeFor(sliceOf(built.page, start, end)),
    spans: [spanOver(built.page, start, end)],
  };
}

type SpanLayout = {
  className: string;
  build: (pages: BuiltPage[], random: Random) => VerifySection;
};

const SPAN_LAYOUTS: readonly SpanLayout[] = [
  { className: "span-exact", build: (pages, random) => sentenceSection(pages, random, 0) },
  { className: "span-shifted-left", build: (pages, random) => sentenceSection(pages, random, -1) },
  { className: "span-shifted-right", build: (pages, random) => sentenceSection(pages, random, 1) },
  {
    className: "span-cut-mid-word",
    build: (pages, random) => {
      const built = pick(random, pages);
      const start = between(random, built.page.bodyStart, Math.max(built.page.bodyEnd - 2, 0));
      const end = between(random, start + 1, built.page.bodyEnd);
      return {
        sourceKey: "k",
        div: narrativeFor(sliceOf(built.page, start, end)),
        spans: [spanOver(built.page, start, end)],
      };
    },
  },
  {
    className: "span-crossing-pages",
    build: (pages, random) => {
      const first = pages[0];
      const second = pages[1];
      const firstSentence = first?.sentences[first.sentences.length - 1];
      const secondSentence = second?.sentences[0];
      if (
        first === undefined ||
        second === undefined ||
        firstSentence === undefined ||
        secondSentence === undefined
      ) {
        return sentenceSection(pages, random, 0);
      }
      const joined =
        sliceOf(first.page, firstSentence.start, first.page.bodyEnd) +
        sliceOf(second.page, second.page.bodyStart, secondSentence.end);
      return {
        sourceKey: "k",
        div: narrativeFor(joined),
        spans: [
          spanOver(first.page, firstSentence.start, firstSentence.end),
          spanOver(second.page, secondSentence.start, secondSentence.end),
        ],
      };
    },
  },
  {
    className: "span-two-on-one-page",
    build: (pages, random) => {
      const built = pick(random, pages);
      if (built.sentences.length < 2) return sentenceSection(pages, random, 0);
      const first = built.sentences[0];
      const second = built.sentences[1];
      if (first === undefined || second === undefined) return sentenceSection(pages, random, 0);
      return {
        sourceKey: "k",
        div: narrativeFor(sliceOf(built.page, first.start, second.end)),
        spans: [
          spanOver(built.page, first.start, first.end),
          spanOver(built.page, second.start, second.end),
        ],
      };
    },
  },
  {
    className: "span-hash-mismatch",
    build: (pages, random) => {
      const section = sentenceSection(pages, random, 0);
      const span = section.spans[0];
      if (span === undefined) return section;
      return { ...section, spans: [{ ...span, textSha256: "0".repeat(64) }] };
    },
  },
  {
    className: "span-outside-body",
    build: (pages, random) => {
      const section = sentenceSection(pages, random, 0);
      const span = section.spans[0];
      if (span === undefined) return section;
      return { ...section, spans: [{ ...span, startOffset: 0 }] };
    },
  },
  {
    className: "span-order-reversed",
    build: (pages, random) => {
      const built = pick(random, pages);
      const section = sentenceSection([built], random, 0);
      const span = section.spans[0];
      if (span === undefined) return section;
      return { ...section, spans: [span, { ...span, startOffset: built.page.bodyStart }] };
    },
  },
  {
    // A section that begins at the first sentence of a later page: the start rule reads back
    // through that page's blank head and into the previous page's body.
    className: "span-page-start",
    build: (pages, random) => {
      const later = pages.slice(1);
      if (later.length === 0) return sentenceSection(pages, random, 0);
      const built = pick(random, later);
      const sentence = built.sentences[0];
      if (sentence === undefined) return sentenceSection(pages, random, 0);
      return {
        sourceKey: "k",
        div: narrativeFor(sliceOf(built.page, sentence.start, sentence.end)),
        spans: [spanOver(built.page, sentence.start, sentence.end)],
      };
    },
  },
  {
    // A section that ends on a page's last sentence, which may be followed by U+00AD (a word
    // continued on the next page) or by nothing at all.
    className: "span-page-end",
    build: (pages, random) => {
      const built = pick(random, pages);
      const sentence = built.sentences[built.sentences.length - 1];
      if (sentence === undefined) return sentenceSection(pages, random, 0);
      const end = chance(random, 0.5) ? built.page.bodyEnd : sentence.end;
      // The narrative is the sentence alone, as an extractor would give it: a narrative that
      // carried the page's U+00AD U+000A would be refused before the span edges are looked at.
      return {
        sourceKey: "k",
        div: narrativeFor(sliceOf(built.page, sentence.start, sentence.end)),
        spans: [spanOver(built.page, sentence.start, end)],
      };
    },
  },
  {
    // A section whose last span ends just after U+00AD and whitespace inside the body: the end
    // rule reads through the whitespace to the soft hyphen.
    className: "span-ends-after-soft-hyphen-space",
    build: (pages, random) => {
      const built = pick(random, pages);
      const points = Array.from(built.page.text);
      const candidates: number[] = [];
      for (let at = built.page.bodyStart; at + 1 < built.page.bodyEnd; at += 1) {
        const next = points[at + 1] ?? "";
        if (points[at] === SOFT_HYPHEN && next !== LF && WHITESPACE.includes(next)) {
          candidates.push(at + 2);
        }
      }
      if (candidates.length === 0) return sentenceSection(pages, random, 0);
      const end = pick(random, candidates);
      const start = built.sentences.find((sentence) => sentence.end >= end)?.start;
      if (start === undefined || start >= end) return sentenceSection(pages, random, 0);
      return {
        sourceKey: "k",
        div: narrativeFor(sliceOf(built.page, start, end)),
        spans: [spanOver(built.page, start, end)],
      };
    },
  },
  {
    // A section that ends at a table row's U+0009, the row's first cell starting with a bullet:
    // the row's U+0009 lies outside the slice, and the whole page line decides (review round 3).
    className: "span-row-cut-before-tab",
    build: (pages, random) => {
      const built = pick(random, pages);
      const points = Array.from(built.page.text);
      const rows = built.sentences.filter(
        (sentence) =>
          BULLET.includes(points[sentence.start] ?? "") &&
          points.slice(sentence.start, sentence.end).includes(TAB),
      );
      if (rows.length === 0) return sentenceSection(pages, random, 0);
      const row = pick(random, rows);
      const end = points.indexOf(TAB, row.start);
      const earlier = built.sentences.filter((sentence) => sentence.end < row.start);
      const start =
        chance(random, 0.5) && earlier.length > 0 ? pick(random, earlier).start : row.start;
      return {
        sourceKey: "k",
        div: narrativeFor(sliceOf(built.page, start, end)),
        spans: [spanOver(built.page, start, end)],
      };
    },
  },
  {
    // A section whose first or last edge falls inside a number grouped with a space or a joiner.
    className: "span-number-group-edge",
    build: (pages, random) => {
      const built = pick(random, pages);
      const points = Array.from(built.page.text);
      const isDigit = (character: string | undefined): boolean =>
        character !== undefined && character >= "0" && character <= "9";
      const separators = [...GROUP_SEPARATORS, ...GROUP_JOINERS];
      const candidates: { sentence: { start: number; end: number }; at: number; run: number }[] =
        [];
      for (const sentence of built.sentences) {
        for (let at = sentence.start + 1; at + 1 < sentence.end; at += 1) {
          if (!isDigit(points[at - 1]) || !separators.includes(points[at] ?? "")) continue;
          let run = at;
          while (run < sentence.end && separators.includes(points[run] ?? "")) run += 1;
          if (isDigit(points[run]) || GROUP_JOINERS.includes(points[at] ?? "")) {
            candidates.push({ sentence, at, run });
          }
        }
      }
      if (candidates.length === 0) return sentenceSection(pages, random, 0);
      const { sentence, at, run } = pick(random, candidates);
      // The edge anywhere from the last digit of one group to the first of the next: inside the
      // run, the span begins or ends with whitespace and the digit rule reads past it.
      const edge =
        run - at >= 2 && chance(random, 0.6)
          ? between(random, at + 1, run - 1)
          : between(random, at, run);
      const [start, end] = chance(random, 0.5) ? [edge, sentence.end] : [sentence.start, edge];
      if (start >= end) return sentenceSection(pages, random, 0);
      return {
        sourceKey: "k",
        div: narrativeFor(sliceOf(built.page, start, end)),
        spans: [spanOver(built.page, start, end)],
      };
    },
  },
  {
    // A section whose first or last edge falls inside a token next to punctuation (`1|.5`,
    // `−|20`, `0.|5`): not a boundary, so a word cut.
    className: "span-punctuation-edge",
    build: (pages, random) => {
      const built = pick(random, pages);
      const points = Array.from(built.page.text);
      const isSpace = (character: string | undefined): boolean =>
        character === undefined || WHITESPACE.includes(character);
      const candidates: { sentence: { start: number; end: number }; at: number }[] = [];
      for (const sentence of built.sentences) {
        for (let at = sentence.start + 1; at < sentence.end; at += 1) {
          const before = points[at - 1];
          const after = points[at];
          if (isSpace(before) || isSpace(after)) continue;
          if (PUNCTUATION.includes(before ?? "") || PUNCTUATION.includes(after ?? "")) {
            candidates.push({ sentence, at });
          }
        }
      }
      if (candidates.length === 0) return sentenceSection(pages, random, 0);
      const { sentence, at } = pick(random, candidates);
      const [start, end] = chance(random, 0.5) ? [at, sentence.end] : [sentence.start, at];
      return {
        sourceKey: "k",
        div: narrativeFor(sliceOf(built.page, start, end)),
        spans: [spanOver(built.page, start, end)],
      };
    },
  },
  {
    className: "span-page-not-found",
    build: (pages, random) => {
      const section = sentenceSection(pages, random, 0);
      const span = section.spans[0];
      if (span === undefined) return section;
      return { ...section, spans: [{ ...span, page: 99 }] };
    },
  },
];

type ProvenanceEntry = {
  sourceKey: string;
  spans: SourceSpanLike[];
  narrativeDivSha256: string;
  normalizedTextSha256: string;
};

function verifyCase(random: Random, seed: number, index: number): CorpusCase {
  const classes = new Set<string>();
  const pages: BuiltPage[] = [];
  const pageCount = between(random, 1, 3);
  for (let number = 1; number <= pageCount; number += 1) {
    const shape = pageShape(random);
    if (shape !== "plain") classes.add(`page-${shape}`);
    pages.push(buildPage(random, number, shape));
  }
  classes.add(`pages-${pageCount}`);

  const sections: { sourceKey: string; path: string; div: string }[] = [];
  const provenance: ProvenanceEntry[] = [];

  for (let position = 0; position < between(random, 1, 4); position += 1) {
    const layout = pick(random, SPAN_LAYOUTS);
    classes.add(layout.className);
    const built = layout.build(pages, random);
    const key = `s${position}`;
    sections.push({ sourceKey: key, path: `Composition.section[${position}]`, div: built.div });
    if (chance(random, 0.08)) {
      classes.add("missing-provenance");
      continue;
    }
    provenance.push({
      sourceKey: key,
      spans: built.spans,
      narrativeDivSha256: sha256Utf8(built.div),
      normalizedTextSha256: "0".repeat(64),
    });
  }
  if (chance(random, 0.08)) {
    classes.add("orphan-provenance");
    provenance.push({
      sourceKey: "orphan",
      spans: [],
      narrativeDivSha256: "0".repeat(64),
      normalizedTextSha256: "0".repeat(64),
    });
  }
  // Two sections pointing at one passage: every per-section check passes and the cross-section
  // overlap rule is the only thing that catches it.
  const first = provenance[0];
  const shared = first?.spans[0];
  if (chance(random, 0.12) && first !== undefined && shared !== undefined) {
    classes.add("cross-section-overlap");
    const key = `overlap${sections.length}`;
    const built = pages.find((candidate) => candidate.page.page === shared.page);
    const text =
      built === undefined ? "" : sliceOf(built.page, shared.startOffset, shared.endOffset);
    sections.push({
      sourceKey: key,
      path: `Composition.section[${sections.length}]`,
      div: narrativeFor(text),
    });
    provenance.push({
      sourceKey: key,
      spans: [{ ...shared }],
      narrativeDivSha256: "0".repeat(64),
      normalizedTextSha256: "0".repeat(64),
    });
  }

  let sourcePages = pages.map((built) => built.page);
  // A body range off a line boundary, or one that excludes more than a header and a footer: the
  // extractor-declared range is bounded, not trusted.
  const [firstPage, ...restPages] = sourcePages;
  if (chance(random, 0.1) && firstPage !== undefined) {
    classes.add("body-range-violated");
    sourcePages = [
      chance(random, 0.5)
        ? { ...firstPage, bodyEnd: Math.max(firstPage.bodyStart, firstPage.bodyEnd - 1) }
        : { ...firstPage, bodyStart: Math.max(0, firstPage.bodyStart - 1) },
      ...restPages,
    ];
  } else if (chance(random, 0.06) && firstPage !== undefined) {
    classes.add("forbidden-character-in-page");
    const points = Array.from(firstPage.text);
    const at = between(random, firstPage.bodyStart, firstPage.bodyEnd);
    const text = [...points.slice(0, at), pick(random, FORBIDDEN), ...points.slice(at)].join("");
    sourcePages = [{ ...firstPage, text, bodyEnd: firstPage.bodyEnd + 1 }, ...restPages];
  }

  // Structurally unusable input: the verifier throws rather than reporting, and the issue list
  // it throws with is part of what a re-implementation has to reproduce.
  let structural = sections;
  if (chance(random, 0.04) && sourcePages.length > 0) {
    classes.add("duplicate-page-number");
    const duplicate = sourcePages[0];
    if (duplicate !== undefined) sourcePages = [...sourcePages, duplicate];
  } else if (chance(random, 0.04) && firstPage !== undefined) {
    classes.add("body-range-past-end");
    sourcePages = [{ ...firstPage, bodyEnd: Array.from(firstPage.text).length + 5 }, ...restPages];
  } else if (chance(random, 0.04) && sections.length > 0) {
    classes.add("duplicate-section-key");
    structural = [...sections, ...sections.slice(0, 1)];
  } else if (chance(random, 0.04)) {
    classes.add("no-narrative-sections");
    structural = [];
  }
  // Pages are numbered 1..N in array order (fidelity-norm/2.0.0): a missing page, pages out of
  // order and a numbering that does not start at 1 are all structural.
  if (chance(random, 0.05) && sourcePages.length > 1) {
    const variant = between(random, 0, 2);
    if (variant === 0) {
      classes.add("page-missing");
      sourcePages = sourcePages.filter((_, position) => position !== 0);
    } else if (variant === 1) {
      classes.add("page-misnumbered");
      sourcePages = [...sourcePages].reverse();
    } else {
      classes.add("page-misnumbered");
      sourcePages = sourcePages.map((page) => ({ ...page, page: page.page + 1 }));
    }
  }

  // A span field that is not an integer, or a page number that is a boolean, is structural
  // (review L2). Python reads `true` as 1, so these are exactly where a port can diverge.
  let typeViolation = false;
  const firstEntry = provenance[0];
  const firstSpan = firstEntry?.spans[0];
  if (chance(random, 0.03) && firstEntry !== undefined && firstSpan !== undefined) {
    classes.add("span-non-integer");
    typeViolation = true;
    const field = pick(random, ["page", "startOffset", "endOffset"] as const);
    const value: unknown = pick(random, [true, false, 0.5, firstSpan[field] + 0.5, null]);
    provenance[0] = { ...firstEntry, spans: [{ ...firstSpan, [field]: value }] };
  } else if (chance(random, 0.02) && sourcePages[0] !== undefined) {
    classes.add("page-number-boolean");
    typeViolation = true;
    sourcePages = [{ ...sourcePages[0], page: true as unknown as number }, ...sourcePages.slice(1)];
  }

  const input: FidelityInput = {
    normalizationVersion: NORMALIZATION_VERSION,
    source: { extractorVersion: "differential-extractor/1.0.0", pages: sourcePages },
    sections: structural,
    provenance,
  };

  let expected: VerifyExpectation;
  try {
    const report = verifyNarrativeFidelity(input);
    expected = {
      reportHash: report.reportHash,
      status: report.status,
      sections: report.sections.map((section) => ({
        sourceKey: section.sourceKey,
        status: section.status,
        reason: section.reason ?? null,
      })),
      issues: report.issues,
    };
  } catch (error) {
    if (!(error instanceof FidelityError)) throw error;
    expected = { error: "FidelityError", issues: error.issues };
  }

  // The Python reader rewrites every offset as `float(value)`, which would turn `true` into 1.0.
  const floatOffsets = chance(random, 0.1) && !typeViolation;
  if (floatOffsets) classes.add("integral-float-offsets");
  return {
    family: "verify",
    index,
    seed,
    tag: "error" in expected ? "verify/threw" : `verify/${expected.status}`,
    classes: [...classes].sort(),
    input,
    ...(floatOffsets ? { floatOffsets: true as const } : {}),
    expected,
  };
}

// ----------------------------------------------------------------------------------------------

function numericArgument(name: string, fallback: number): number {
  const position = process.argv.indexOf(`--${name}`);
  if (position === -1) return fallback;
  const raw = process.argv[position + 1];
  const parsed = raw === undefined ? Number.NaN : Number.parseInt(raw, 10);
  if (!Number.isInteger(parsed) || parsed < 0) {
    throw new Error(`--${name} needs a non-negative integer`);
  }
  return parsed;
}

const seed = numericArgument("seed", 20260920);
const count = numericArgument("count", 2000);
const outPosition = process.argv.indexOf("--out");
const out = outPosition === -1 ? undefined : process.argv[outPosition + 1];

const random = mulberry32(seed);
const lines: string[] = [];
for (let index = 0; index < count; index += 1) {
  const family = index % 3;
  const generated =
    family === 0
      ? normalizeCase(random, seed, index)
      : family === 1
        ? xhtmlCase(random, seed, index)
        : verifyCase(random, seed, index);
  lines.push(JSON.stringify(generated));
}

const corpus = lines.length === 0 ? "" : `${lines.join(LF)}${LF}`;
if (out === undefined) process.stdout.write(corpus);
else writeFileSync(out, corpus, "utf8");
process.stderr.write(`differential corpus: ${count} cases, seed ${seed}${LF}`);
