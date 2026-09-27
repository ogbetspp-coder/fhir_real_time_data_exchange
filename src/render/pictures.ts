import { createHash } from "node:crypto";

import { ORIGIN, type Resource } from "./page.js";

// R2's pictures (docs/design/authority-import-renderer.md): every `img` is drawn as the import
// carries it, its reference replaced for the drawing only. The forms 3c-B2b draws:
// - `data`: a `data:` URI, drawn from its own bytes, unchanged;
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

// An `img` start tag's `src`, quoted either way (T's and the scanner's grammar: one `src`).
const IMG_SRC = /(<img\b[^>]*?\ssrc\s*=\s*)(["'])(.*?)\2/gsu;

const DATA_URI = /^data:(image\/(?:png|jpeg|gif));base64,([A-Za-z0-9+/]+={0,2})$/u;

function sha256(bytes: Buffer): string {
  return createHash("sha256").update(bytes).digest("hex");
}

// The attribute value as the DOM holds it: XML's five references decoded (a `data:` URI or an id
// holds none of them in practice; decoding keeps the reference exact where one does).
function decode(value: string): string {
  return value
    .replace(/&lt;/gu, "<")
    .replace(/&gt;/gu, ">")
    .replace(/&quot;/gu, '"')
    .replace(/&apos;/gu, "'")
    .replace(/&amp;/gu, "&");
}

// The div to draw, the resources the page serves, and each picture's form and hash, in document
// order.
export function preparePictures(
  div: string,
  contained: Contained = new Map(),
): { div: string; resources: Map<string, Resource>; pictures: Picture[] } {
  const resources = new Map<string, Resource>();
  const pictures: Picture[] = [];
  const drawn = div.replace(IMG_SRC, (_whole, start: string, quote: string, raw: string) => {
    const reference = decode(raw);
    const data = DATA_URI.exec(reference);
    if (data !== null) {
      pictures.push({
        reference,
        form: "data",
        sha256: sha256(Buffer.from(data[2] ?? "", "base64")),
      });
      return `${start}${quote}${raw}${quote}`;
    }
    const inside = reference.startsWith("#") ? contained.get(reference.slice(1)) : undefined;
    if (inside !== undefined) {
      const hash = sha256(inside.body);
      const url = `${ORIGIN}pictures/${hash}`;
      resources.set(url, inside);
      pictures.push({ reference, form: "contained", sha256: hash });
      return `${start}${quote}${url}${quote}`;
    }
    pictures.push({ reference, form: "unpinned", sha256: "none" });
    return `${start}${quote}${UNPINNED_URL}${quote}`;
  });
  return { div: drawn, resources, pictures };
}
