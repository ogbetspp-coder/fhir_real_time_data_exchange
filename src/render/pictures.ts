import { createHash } from "node:crypto";

import { XHTML_TOKENS } from "../fidelity/xhtml.js";
import { ORIGIN, type Resource } from "./page.js";

// R2's pictures (docs/design/authority-import-renderer.md): every `img` is drawn as the import
// carries it, its reference replaced for the drawing only. The forms 3c-B2b draws:
// - `data`: a `data:` URI the scanner accepts (PNG or JPEG, base64), drawn from its own bytes,
//   unchanged;
// - `contained`: a `#id` reference to a Binary the document contains, drawn from those bytes,
//   served at a fixed URL of their hash;
// - `unpinned`: any other reference, with no pinned bytes and no evidence (only a withheld
//   section can hold one), rewritten to one fixed URL that fails, so Chrome draws its
//   broken-image box.
// 3c-E adds the `fetched`, `export` and `not-drawn` forms.

export type PictureForm = "data" | "contained" | "unpinned";

export type Picture = {
  reference: string;
  form: PictureForm;
  // The SHA-256 of the bytes drawn, or "none".
  sha256: string;
};

// A picture the document contains: its Binary's bytes and content type, by the id `#id` names.
export type Contained = ReadonlyMap<string, Resource>;

// Where an unpinned picture is sent: a URL the page's interception always fails.
export const UNPINNED_URL = `${ORIGIN}unpinned-picture`;

// A div whose pictures cannot be read by the scanner's grammar is not drawn: an `img` the
// tokeniser does not read as one start tag, an `img` named in another case, or one with more
// than one `src`.
export class PictureError extends Error {}

// The scanner's tokens (src/fidelity/xhtml.ts), so an `img` and its `src` are read exactly as the
// scanner and T read them: a value may hold `>`, the other quote, or ` src=`.
const START_TAG = new RegExp(XHTML_TOKENS.startTag, "y");
const ATTRIBUTE = new RegExp(XHTML_TOKENS.attribute, "g");

// The scanner's `isPictureData` (src/fidelity/xhtml.ts, under the importer's lock, so repeated
// here and tested to agree): a PNG or JPEG `data:` URI whose base64 body is non-empty, at most
// 1 MiB decoded, a multiple of 4 long, and padded only at its end.
const PICTURE_DATA_PREFIXES = ["data:image/png;base64,", "data:image/jpeg;base64,"];
const PICTURE_DATA_LIMIT = 1_398_104;
const BASE64_ALPHABET = /^[A-Za-z0-9+/]*$/;

function isPictureData(value: string): boolean {
  const prefix = PICTURE_DATA_PREFIXES.find((candidate) => value.startsWith(candidate));
  if (prefix === undefined) return false;
  const body = value.slice(prefix.length);
  if (body.length === 0 || body.length > PICTURE_DATA_LIMIT || body.length % 4 !== 0) return false;
  const padding = body.endsWith("==") ? 2 : body.endsWith("=") ? 1 : 0;
  return BASE64_ALPHABET.test(body.slice(0, body.length - padding));
}

function sha256(bytes: Buffer): string {
  return createHash("sha256").update(bytes).digest("hex");
}

const REFERENCES: Readonly<Record<string, string>> = {
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
  amp: "&",
};

// The attribute value as the DOM holds it: XML's five named references and numeric references
// decoded, in one pass (so `&amp;lt;` is `&lt;`).
function decode(value: string): string {
  return value.replace(
    /&(?:(lt|gt|quot|apos|amp)|#([0-9]{1,7})|#x([0-9A-Fa-f]{1,6}));/gu,
    (whole, name?: string, decimal?: string, hex?: string) => {
      if (name !== undefined) return REFERENCES[name] ?? whole;
      const codePoint = Number.parseInt(decimal ?? hex ?? "", decimal === undefined ? 16 : 10);
      return codePoint <= 0x10ffff ? String.fromCodePoint(codePoint) : whole;
    },
  );
}

// Where a comment or a CDATA section that starts at `at` ends, or -1 if none starts there.
function skipped(div: string, at: number): number {
  for (const [open, close] of [
    ["<!--", "-->"],
    ["<![CDATA[", "]]>"],
  ] as const) {
    if (div.startsWith(open, at)) {
      const end = div.indexOf(close, at + open.length);
      return end < 0 ? div.length : end + close.length;
    }
  }
  return -1;
}

// The div to draw, the resources the page serves, each picture's form and hash, and the URL each
// is drawn from, in document order.
export function preparePictures(
  div: string,
  contained: Contained = new Map(),
): { div: string; resources: Map<string, Resource>; pictures: Picture[]; urls: string[] } {
  const resources = new Map<string, Resource>();
  const pictures: Picture[] = [];
  const urls: string[] = [];
  let drawn = "";
  let copied = 0;
  let cursor: number;
  for (let at = div.indexOf("<"); at >= 0; at = div.indexOf("<", cursor)) {
    const end = skipped(div, at);
    if (end >= 0) {
      cursor = end;
      continue;
    }
    START_TAG.lastIndex = at;
    const tag = START_TAG.exec(div);
    const named = /^<img(?![A-Za-z0-9])/iu.exec(div.slice(at, at + 5));
    if (tag?.[1] !== "img") {
      if (named !== null)
        throw new PictureError(`an img the scanner's grammar does not read at ${at}`);
      cursor = at + 1;
      continue;
    }
    cursor = START_TAG.lastIndex;
    const attributes = tag[2] ?? "";
    const base = at + 1 + tag[1].length;
    const sources = [...attributes.matchAll(ATTRIBUTE)].filter(([, name]) => name === "src");
    if (sources.length > 1) throw new PictureError(`an img with ${sources.length} src at ${at}`);
    const source = sources[0];
    if (source === undefined) {
      // An `img` with no `src` draws nothing from any bytes: drawn broken, as unpinned.
      pictures.push({ reference: "", form: "unpinned", sha256: "none" });
      urls.push("");
      continue;
    }
    const raw = source[2] ?? source[3] ?? "";
    // The value's span: the match's end, less the closing quote.
    const valueEnd = base + source.index + source[0].length - 1;
    const valueStart = valueEnd - raw.length;
    const reference = decode(raw);
    let url: string;
    // Classified on the value as written, as the scanner does: a reference spelled with a
    // character reference is not a picture the scanner takes.
    if (isPictureData(raw)) {
      const body = reference.slice(reference.indexOf(",") + 1);
      pictures.push({ reference, form: "data", sha256: sha256(Buffer.from(body, "base64")) });
      url = reference;
    } else {
      const inside = reference.startsWith("#") ? contained.get(reference.slice(1)) : undefined;
      if (inside !== undefined) {
        const hash = sha256(inside.body);
        url = `${ORIGIN}pictures/${hash}`;
        resources.set(url, inside);
        pictures.push({ reference, form: "contained", sha256: hash });
      } else {
        url = UNPINNED_URL;
        pictures.push({ reference, form: "unpinned", sha256: "none" });
      }
    }
    urls.push(url);
    if (url !== reference) {
      drawn += `${div.slice(copied, valueStart)}${url}`;
      copied = valueEnd;
    }
  }
  return { div: drawn + div.slice(copied), resources, pictures, urls };
}
