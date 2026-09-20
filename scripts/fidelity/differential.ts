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
const BULLET = CHARS(
  0x2022,
  0x2023,
  0x2043,
  0x2219,
  0x25a0,
  0x25a1,
  0x25aa,
  0x25ab,
  0x25cb,
  0x25cf,
  0x25e6,
);
const NEAR_BULLET = CHARS(0x2024, 0x25a2, 0x25cc, 0x00b7, 0x2027);
const WHITESPACE = CHARS(
  0x0009,
  0x000a,
  0x000b,
  0x000c,
  0x000d,
  0x0020,
  0x0085,
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
    input = [...points.slice(0, at), pick(random, FORBIDDEN), ...points.slice(at)].join("");
    classes.add("forbidden-character");
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
// Family (b): the XHTML scanner. Documents are assembled from the allowed grammar; one case in
// three then carries a single deliberate violation, so the error codes are reached as densely
// as the accepting paths.

const XMLNS = `xmlns="http://www.w3.org/1999/xhtml"`;
const BLOCK_WRAPPERS = ["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote"];
const INLINE_WRAPPERS = ["span", "b", "i", "u", "em", "strong", "sup", "sub", "small", "abbr"];

// Text that is safe inside markup: its own `<`, `&`, `>` and quotes are stripped, and entities
// are added back explicitly so every accepted entity form appears.
const MARKUP_UNSAFE = new RegExp(`[${CP(0x003c)}${CP(0x0026)}${CP(0x003e)}"']`, "gu");

function markupText(random: Random): Passage {
  const { text, classes } = passage(random, between(random, 1, 5));
  let safe = text.replace(MARKUP_UNSAFE, "");
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
    ]);
    classes.add("entity");
  }
  return { text: safe, classes };
}

type Markup = { markup: string; classes: Set<string> };

function attributes(random: Random, element: string): Markup {
  const classes = new Set<string>();
  let markup = "";
  if (chance(random, 0.25)) {
    markup += ` id="${word(random).slice(0, 8)}"`;
    classes.add("attribute-id");
  }
  if (chance(random, 0.2)) {
    const names: string[] = [];
    for (let position = 0; position < between(random, 1, 3); position += 1) {
      names.push(word(random).slice(0, 6));
    }
    markup += ` class="${names.join(SPACE)}"`;
    classes.add("attribute-class");
  }
  if (chance(random, 0.12)) {
    markup += ` ${pick(random, ["lang", "xml:lang"])}="${pick(random, ["en", "en-GB", "de"])}"`;
    classes.add("attribute-lang");
  }
  if (element === "a" && chance(random, 0.6)) {
    markup += ` href="${pick(random, ["#x", "https://example.org/a/b", "https://example.org/"])}"`;
    classes.add("attribute-href");
  }
  if ((element === "td" || element === "th") && chance(random, 0.3)) {
    markup += ` ${pick(random, ["colspan", "rowspan"])}="${between(random, 1, 999)}"`;
    classes.add("attribute-span");
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

function table(random: Random, depth: number): Markup {
  const classes = new Set<string>(["table"]);
  const row = (cell: "td" | "th"): string => {
    let cells = "";
    for (let position = 0; position < between(random, 1, 3); position += 1) {
      const attribute = attributes(random, cell);
      for (const name of attribute.classes) classes.add(name);
      if (chance(random, 0.08)) {
        cells += `<${cell}${attribute.markup}/>`;
        classes.add("self-closing-cell");
        continue;
      }
      const inner = node(random, depth + 1);
      for (const name of inner.classes) classes.add(name);
      cells += `<${cell}${attribute.markup}>${inner.markup}</${cell}>`;
    }
    return `<tr>${cells}</tr>`;
  };
  let markup = "";
  if (chance(random, 0.25)) {
    const caption = markupText(random);
    for (const name of caption.classes) classes.add(name);
    markup += `<caption>${caption.text}</caption>`;
    classes.add("table-caption");
  }
  if (chance(random, 0.5)) {
    // Sections, in the one document order that renders as written.
    if (chance(random, 0.7)) {
      markup += `<thead>${row("th")}</thead>`;
      classes.add("table-thead");
    }
    for (let position = 0; position < between(random, 1, 2); position += 1) {
      markup += `<tbody>${row("td")}</tbody>`;
      classes.add("table-tbody");
    }
    if (chance(random, 0.4)) {
      markup += `<tfoot>${row("td")}</tfoot>`;
      classes.add("table-tfoot");
    }
  } else {
    for (let position = 0; position < between(random, 1, 3); position += 1) markup += row("td");
    classes.add("table-bare-rows");
  }
  return { markup: `<table>${markup}</table>`, classes };
}

function node(random: Random, depth: number): Markup {
  const classes = new Set<string>();
  if (depth > 3) return markupTextNode(random);
  const kind = random();
  if (kind < 0.34) return markupTextNode(random);
  if (kind < 0.48) {
    const element = pick(random, INLINE_WRAPPERS);
    const attribute = attributes(random, element);
    const inner = node(random, depth + 1);
    for (const name of [...attribute.classes, ...inner.classes]) classes.add(name);
    classes.add("inline-element");
    return { markup: `<${element}${attribute.markup}>${inner.markup}</${element}>`, classes };
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
    const attribute = attributes(random, element);
    const inner = node(random, depth + 1);
    for (const name of [...attribute.classes, ...inner.classes]) classes.add(name);
    classes.add("block-element");
    return { markup: `<${element}${attribute.markup}>${inner.markup}</${element}>`, classes };
  }
  if (kind < 0.8) {
    let items = "";
    for (let position = 0; position < between(random, 1, 3); position += 1) {
      const inner = node(random, depth + 1);
      for (const name of inner.classes) classes.add(name);
      items += `<li>${inner.markup}</li>`;
    }
    classes.add("list");
    return { markup: `<ul>${items}</ul>`, classes };
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
  if (kind < 0.94) return table(random, depth);
  if (kind < 0.97) {
    classes.add("self-closing-block");
    return { markup: pick(random, ["<hr/>", `<hr${SPACE}/>`]), classes };
  }
  const inner = markupTextNode(random);
  for (const name of inner.classes) classes.add(name);
  classes.add("line-break");
  return { markup: `${inner.markup}<br/>${inner.markup}`, classes };
}

// A single deliberate violation, applied to an otherwise well-formed document. Each names the
// class it belongs to; between them they reach every error code the scanner can raise.
type Violation = {
  className: string;
  apply: (body: string, rootAttributes: string, random: Random) => string;
};

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
      const element = pick(random, ["ol", "q", "img", "style", "script", "del", "s", "math"]);
      return root(`${body}<${element}>x</${element}>`, attrs);
    },
  },
  {
    className: "forbidden-attribute-name",
    apply: (body, attrs, random) => {
      const attribute = pick(random, [`style="x"`, `hidden="hidden"`, `title="t"`, `href="#x"`]);
      return root(`${body}<p ${attribute}>x</p>`, attrs);
    },
  },
  {
    className: "forbidden-attribute-value",
    apply: (body, attrs, random) => {
      const attribute = pick(random, [
        `id="a${LF}"`,
        `id="a${CR}${LF}"`,
        `id="a b"`,
        `class="a${LF}hidden"`,
        `class="a b c d"`,
        `lang="${"x".repeat(33)}"`,
        `href="https://example.org/a?q=1"`,
        `href="javascript:x"`,
        `id=""`,
        `scope="row col"`,
      ]);
      return root(`${body}<p ${attribute}>x</p>`, attrs);
    },
  },
  {
    className: "duplicate-attribute",
    apply: (body, attrs) => root(`${body}<p id="a" id="b">x</p>`, attrs),
  },
  {
    className: "nested-xmlns",
    apply: (body, attrs) => root(`${body}<p ${XMLNS}>x</p>`, attrs),
  },
  {
    className: "span-value-out-of-range",
    apply: (body, attrs, random) => {
      const value = pick(random, ["0", "1000", "01", "-1", "1.0"]);
      return root(`${body}<table><tr><td colspan="${value}">x</td></tr></table>`, attrs);
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
      ]);
      return root(`${body}${inner}`, attrs);
    },
  },
  {
    className: "soft-hyphen-at-boundary",
    apply: (body, attrs, random) => {
      const inner = pick(random, [
        `<p>a${SOFT_HYPHEN}</p>`,
        `<p>a${SOFT_HYPHEN}<br/>b</p>`,
        `<p>a${SOFT_HYPHEN}<hr/></p>`,
      ]);
      return root(`${body}${inner}`, attrs);
    },
  },
];

function xhtmlCase(random: Random, seed: number, index: number): CorpusCase {
  const classes = new Set<string>();
  const attribute = attributes(random, "div");
  for (const name of attribute.classes) classes.add(name);
  let body = "";
  for (let position = 0; position < between(random, 1, 4); position += 1) {
    const child = node(random, 1);
    for (const name of child.classes) classes.add(name);
    body += child.markup;
  }
  let input: string;
  if (chance(random, 0.34)) {
    const violation = pick(random, VIOLATIONS);
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
const LINE_TERMINATORS = new RegExp(
  `[${CP(0x000a)}${CP(0x000d)}${CP(0x000b)}${CP(0x000c)}${CP(0x0085)}${CP(0x2028)}${CP(0x2029)}]`,
  "gu",
);

function buildPage(random: Random, number: number): BuiltPage {
  const header = `HEADER ${word(random)}${LF}`;
  const sentences: { start: number; end: number }[] = [];
  let body = "";
  for (let position = 0; position < between(random, 2, 4); position += 1) {
    const { text } = passage(random, between(random, 3, 8));
    const sentence = text.replace(LINE_TERMINATORS, SPACE).trim();
    if (sentence.length === 0) continue;
    const start = Array.from(header).length + Array.from(body).length;
    body += `${sentence}${LF}`;
    sentences.push({ start, end: start + Array.from(sentence).length });
  }
  if (body.length === 0) {
    const filler = word(random);
    sentences.push({
      start: Array.from(header).length,
      end: Array.from(header).length + filler.length,
    });
    body = `${filler}${LF}`;
  }
  const text = `${header}${body}Page ${number}`;
  const bodyStart = Array.from(header).length;
  return {
    page: { page: number, text, bodyStart, bodyEnd: bodyStart + Array.from(body).length },
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
  for (let number = 1; number <= pageCount; number += 1) pages.push(buildPage(random, number));
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

  const floatOffsets = chance(random, 0.1);
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
