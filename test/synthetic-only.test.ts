import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

import { beforeAll, describe, expect, it } from "vitest";

import type { EmaMapping } from "../src/fhir/mapping.js";
import { loadEmaMapping } from "../src/fhir/mapping.js";
import {
  LEAFLET_PRODUCT_ID,
  SYNTHETIC_PRODUCTS,
  SYNTHETIC_PRODUCT_IDS,
  SYNTHETIC_VERSIONS,
  syntheticProduct,
} from "../src/fixtures/synthetic-products.js";
import { createSyntheticSubmission } from "../src/fixtures/synthetic-submission.js";
import { createSyntheticType2Bundle } from "../src/fixtures/synthetic.js";

// AGENTS.md: synthetic product information only. This is the guard for the fixtures the
// repository builds its runs, contracts and evidence from — `src/fixtures/` and `test/fixtures/`
// — and it fails if a product id loses its `synthetic-` prefix or a narrative loses the marker
// every synthetic sentence carries ("not for clinical use").
//
// Scope: AGENTS.md now permits one exception, an authority's published ePI (public, approved
// text) for roadmap item 3a. That text is real by design and will live in its own import
// directory; this guard does not apply to it, and must not be widened to cover it. Everything
// under the two fixture directories above stays synthetic.
//
// Out of scope here: test/fixtures/fidelity/vectors.json, whose XHTML inputs are character-level
// normalisation vectors generated from test/fixtures/fidelity/cases.ts (single letters and short
// invented strings), not product narrative.

const MARKER = "not for clinical use";
const PREFIX = "synthetic-";

let mapping: EmaMapping;

beforeAll(async () => {
  mapping = await loadEmaMapping();
});

// Every string reached from `value`, with the key it was found under.
function strings(value: unknown, key = "", into: { key: string; text: string }[] = []) {
  if (typeof value === "string") into.push({ key, text: value });
  else if (Array.isArray(value)) for (const item of value) strings(item, key, into);
  else if (typeof value === "object" && value !== null) {
    for (const [child, item] of Object.entries(value)) strings(item, child, into);
  }
  return into;
}

function xhtmlNarratives(value: unknown): string[] {
  return strings(value)
    .map(({ text }) => text)
    .filter((text) => text.startsWith("<div"));
}

function jsonFiles(directory: string): string[] {
  return readdirSync(directory).flatMap((name) => {
    const full = path.join(directory, name);
    if (statSync(full).isDirectory()) return jsonFiles(full);
    return name.endsWith(".json") ? [full] : [];
  });
}

const cases = SYNTHETIC_PRODUCT_IDS.flatMap((product) =>
  SYNTHETIC_VERSIONS.map((version) => ({ product, version })),
);

describe("synthetic product identity", () => {
  it("prefixes every fixture product id", () => {
    expect(SYNTHETIC_PRODUCT_IDS.length).toBeGreaterThan(0);
    for (const id of SYNTHETIC_PRODUCT_IDS) expect([id, id.startsWith(PREFIX)]).toEqual([id, true]);
  });

  it("names every product as synthetic", () => {
    for (const product of SYNTHETIC_PRODUCTS) {
      expect([product.id, product.productName.startsWith("Synthetic ")]).toEqual([
        product.id,
        true,
      ]);
    }
  });

  it("prefixes every resource id the Type 2 fixture builds", () => {
    for (const { product, version } of cases) {
      const bundle = createSyntheticType2Bundle(mapping, { product, version });
      const ids = [bundle.id, ...bundle.entry.map(({ resource }) => resource.id)];
      for (const id of ids) {
        expect([product, version, id, String(id).startsWith(PREFIX)]).toEqual([
          product,
          version,
          id,
          true,
        ]);
      }
    }
  });
});

// The package leaflet's product is built with the leaflet's mapping and is not among the products
// above (src/fixtures/synthetic-products.ts), so it is held to the same rules here.
describe("the synthetic package leaflet", () => {
  it("names its product and every resource as synthetic, and marks every narrative", async () => {
    const leaflet = await loadEmaMapping("fhir/mappings/cap-pl-en.json");
    const product = syntheticProduct(LEAFLET_PRODUCT_ID);
    expect([product.id.startsWith(PREFIX), product.productName.startsWith("Synthetic ")]).toEqual([
      true,
      true,
    ]);
    for (const version of SYNTHETIC_VERSIONS) {
      const options = { product: LEAFLET_PRODUCT_ID, version, optional: true };
      const bundle = createSyntheticType2Bundle(leaflet, options);
      const ids = [bundle.id, ...bundle.entry.map(({ resource }) => resource.id)];
      expect(ids.filter((id) => !String(id).startsWith(PREFIX))).toEqual([]);
      const { submission, sourceText } = createSyntheticSubmission(leaflet, options);
      const divs = [...xhtmlNarratives(bundle), ...xhtmlNarratives(submission)];
      expect(divs.length).toBeGreaterThan(0);
      expect(divs.filter((div) => !div.includes(MARKER))).toEqual([]);
      expect(sourceText.pages.filter(({ text }) => !text.includes(MARKER))).toEqual([]);
    }
  });
});

describe("synthetic narrative", () => {
  it("marks every section narrative the fixtures build", () => {
    for (const { product, version } of cases) {
      const divs = xhtmlNarratives(createSyntheticType2Bundle(mapping, { product, version }));
      expect(divs.length).toBeGreaterThan(0);
      const unmarked = divs.filter((div) => !div.includes(MARKER)).length;
      expect([product, version, unmarked]).toEqual([product, version, 0]);
    }
  });

  it("marks every source page and submission narrative", () => {
    for (const { product, version } of cases) {
      const { submission, sourceText } = createSyntheticSubmission(mapping, { product, version });
      const unmarkedDivs = xhtmlNarratives(submission).filter((div) => !div.includes(MARKER));
      const unmarkedPages = sourceText.pages.filter(({ text }) => !text.includes(MARKER));
      expect([product, version, unmarkedDivs.length, unmarkedPages.length]).toEqual([
        product,
        version,
        0,
        0,
      ]);
    }
  });

  it("marks every XHTML narrative committed under test/fixtures", () => {
    const files = jsonFiles("test/fixtures").filter(
      (file) => file !== path.join("test/fixtures/fidelity/vectors.json"),
    );
    let checked = 0;
    for (const file of files) {
      const divs = xhtmlNarratives(JSON.parse(readFileSync(file, "utf8")));
      checked += divs.length;
      const unmarked = divs.filter((div) => !div.includes(MARKER)).length;
      expect([file, unmarked]).toEqual([file, 0]);
    }
    expect(checked).toBeGreaterThan(0);
  });
});
