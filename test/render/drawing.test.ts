import { createHash } from "node:crypto";

import { describe, expect, it } from "vitest";

import { xhtmlToText, XhtmlError } from "../../src/fidelity/xhtml.js";
import {
  comparePictureLists,
  compareDrawings,
  grid,
  listPictures,
  type Drawing,
  type DrawnPicture,
  type Rect,
} from "../../src/render/drawing.js";
import { ORIGIN } from "../../src/render/page.js";
import { PictureError, preparePictures, UNPINNED_URL } from "../../src/render/pictures.js";
import { BLUE_PNG, GREEN_PNG, PICTURE_FORMS, RED_PNG } from "../fixtures/render/drawing-cases.js";

// R2's pictures and second drawing, as rules on what the page is given and what Chrome reports
// (the renderer image's run of scripts/render/check-drawings.ts draws them).

const hash = (base64: string): string =>
  createHash("sha256").update(Buffer.from(base64, "base64")).digest("hex");

describe("R2's pictures", () => {
  it("draws a data URI from itself, a contained Binary from its bytes, and anything else broken", () => {
    const { div, resources, pictures, urls } = preparePictures(
      PICTURE_FORMS.div,
      PICTURE_FORMS.contained,
    );
    expect(pictures.slice(0, 3)).toEqual([
      { reference: `data:image/png;base64,${RED_PNG}`, form: "data", sha256: hash(RED_PNG) },
      { reference: "#blue", form: "contained", sha256: hash(BLUE_PNG) },
      {
        reference: "~/_entity/annotation/00000000-0000-0000-0000-000000000000",
        form: "unpinned",
        sha256: "none",
      },
    ]);
    const served = `${ORIGIN}pictures/${hash(BLUE_PNG)}`;
    expect(urls).toEqual([
      `data:image/png;base64,${RED_PNG}`,
      served,
      UNPINNED_URL,
      UNPINNED_URL,
      UNPINNED_URL,
    ]);
    expect(div).toContain(`src="data:image/png;base64,${RED_PNG}"`);
    expect(div).toContain(`src="${served}"`);
    expect(div).toContain(`src="${UNPINNED_URL}"`);
    expect(div).not.toContain("#blue");
    expect([...resources.keys()]).toEqual([served]);
    expect(resources.get(served)?.body.equals(Buffer.from(BLUE_PNG, "base64"))).toBe(true);
  });

  it("reads single quotes and XML's references, and leaves a div without pictures alone", () => {
    const { pictures, div } = preparePictures("<p><img alt='x' src='#a&amp;lt;b&#35;'/></p>");
    expect(pictures).toEqual([{ reference: "#a&lt;b#", form: "unpinned", sha256: "none" }]);
    expect(div).toBe(`<p><img alt='x' src='${UNPINNED_URL}'/></p>`);
    expect(preparePictures("<p>none</p>")).toEqual({
      div: "<p>none</p>",
      resources: new Map(),
      pictures: [],
      urls: [],
    });
  });

  // The first review's repros: another attribute holding `>` or ` src=` misread the `src`.
  it("reads the src as the scanner's grammar does, whatever other attributes hold", () => {
    const red = `data:image/png;base64,${RED_PNG}`;
    const blue = `data:image/png;base64,${BLUE_PNG}`;
    const read = (div: string): string[] =>
      preparePictures(div).pictures.map(({ form, sha256 }) => `${form} ${sha256}`);
    expect(read(`<p><img class="a>b" src="${red}"/></p>`)).toEqual([`data ${hash(RED_PNG)}`]);
    expect(read(`<p><img title=" src='${blue}'" src="${red}"/></p>`)).toEqual([
      `data ${hash(RED_PNG)}`,
    ]);
    const titled = preparePictures(`<p><img title="a src='#zzz'" src="#b"/></p>`);
    expect(titled.pictures).toEqual([{ reference: "#b", form: "unpinned", sha256: "none" }]);
    expect(titled.div).toBe(`<p><img title="a src='#zzz'" src="${UNPINNED_URL}"/></p>`);
    // Not a picture: a comment's, a CDATA section's, or another element's `src`.
    expect(read(`<!-- <img src="#a"/> --><![CDATA[<img src="#b"/>]]><imgx src="#c"/>`)).toEqual([]);
    // Refused: an img the grammar does not read, in another case, or with two sources.
    expect(() => preparePictures('<p><img src="a<b"/></p>')).toThrow(PictureError);
    expect(() => preparePictures('<p><IMG src="#a"/></p>')).toThrow(PictureError);
    expect(() => preparePictures('<p><img src="#a" src="#b"/></p>')).toThrow(PictureError);
  });

  it("takes a data URI as a picture exactly where the scanner does", () => {
    const scanned = (value: string): boolean => {
      try {
        xhtmlToText(
          `<div xmlns="http://www.w3.org/1999/xhtml"><p>x <img src="${value}"/></p></div>`,
        );
        return true;
      } catch (error) {
        if (error instanceof XhtmlError) return false;
        throw error;
      }
    };
    for (const value of [
      `data:image/png;base64,${RED_PNG}`,
      `data:image/jpeg;base64,${RED_PNG}`,
      `data:image/gif;base64,${RED_PNG}`,
      "data:image/png;base64,iVBORw0KGgo",
      "data:image/png;base64,",
      "data:image/png;base64,ab=c",
      "data:image/png;base64,a===",
      "data:image/png,abcd",
      `data:image/png;base64,${RED_PNG.slice(0, -2)}&#61;&#x3D;`,
      `data:image/png;base64,${RED_PNG.replace("A", "&#65;")}`,
      "#a",
    ]) {
      const div = `<p><img src="${value}"/></p>`;
      expect(preparePictures(div).pictures[0]?.form === "data", value).toBe(scanned(value));
    }
  });
});

describe("R2's second drawing", () => {
  const cell = (left: number, top: number, right: number, bottom: number): Rect => ({
    left,
    top,
    right,
    bottom,
  });

  it("reads a table's grid from its cells, not its geometry", () => {
    // Two by two, then the same grid wider and with edges a quarter pixel apart.
    const a = [cell(0, 0, 10, 5), cell(10, 0, 30, 5), cell(0, 5, 10, 9), cell(10, 5, 30, 9)];
    const b = [
      cell(0, 0, 40, 7),
      cell(40.25, 0, 90, 7),
      cell(0, 7.25, 40, 12),
      cell(40, 7, 90, 12),
    ];
    expect(grid(a)).toEqual(grid(b));
    expect(grid(a)).toEqual([
      { row: 0, rows: 1, column: 0, columns: 1 },
      { row: 0, rows: 1, column: 1, columns: 1 },
      { row: 1, rows: 1, column: 0, columns: 1 },
      { row: 1, rows: 1, column: 1, columns: 1 },
    ]);
    // The same grid with 2 px between cells, as T(div)'s default spacing draws it.
    const spaced = [cell(2, 2, 12, 7), cell(14, 2, 34, 7), cell(2, 9, 12, 13), cell(14, 9, 34, 13)];
    expect(grid(spaced)).toEqual(grid(a));
    // A row-spanning cell, spaced.
    expect(grid([cell(2, 2, 12, 20), cell(14, 2, 34, 9), cell(14, 11, 34, 20)])).toEqual([
      { row: 0, rows: 2, column: 0, columns: 1 },
      { row: 0, rows: 1, column: 1, columns: 1 },
      { row: 1, rows: 1, column: 1, columns: 1 },
    ]);
    // A cell spanning two columns.
    expect(grid([cell(0, 0, 30, 5), cell(0, 5, 10, 9), cell(10, 5, 30, 9)])[0]).toEqual({
      row: 0,
      rows: 1,
      column: 0,
      columns: 2,
    });
  });

  it("refuses any difference in the text, list numbers, grids and pictures", () => {
    const base: Drawing = {
      text: "Take 2 tablets",
      markers: ["1. ", "2. "],
      tables: [[cell(0, 0, 10, 5), cell(10, 0, 20, 5)]],
      pictures: [{ width: 30, height: 20, decoded: true, source: "data:a" }],
    };
    expect(compareDrawings(base, structuredClone(base))).toEqual([]);
    const properties = (other: Partial<Drawing>): string[] =>
      compareDrawings(base, { ...base, ...other }).map(({ property }) => property);
    expect(properties({ text: "Take 20 tablets" })).toEqual(["text"]);
    expect(properties({ markers: ["2. ", "3. "] })).toEqual(["markers"]);
    expect(properties({ tables: [[cell(0, 0, 20, 5)]] })).toEqual(["table 0"]);
    expect(properties({ tables: [] })).toEqual(["tables", "table 0"]);
    const picture = (width: number, height: number, decoded: boolean): DrawnPicture => ({
      width,
      height,
      decoded,
      source: "data:a",
    });
    expect(properties({ pictures: [picture(12, 8, true)] })).toEqual(["pictures"]);
    expect(properties({ pictures: [picture(30, 20, false)] })).toEqual(["pictures"]);
    expect(properties({ pictures: [] })).toEqual(["pictures"]);
  });

  it("lists each picture with its box, checked against the page", () => {
    const { pictures, urls } = preparePictures(PICTURE_FORMS.div, PICTURE_FORMS.contained);
    const drawn = (decoded: boolean[]): Drawing => ({
      text: "",
      markers: [],
      tables: [],
      pictures: urls.map((source, index) => ({
        width: 16,
        height: 16,
        decoded: decoded[index] ?? false,
        source,
      })),
    });
    const good = listPictures(pictures, urls, drawn([true, true, false, false, false]));
    expect(good.problems).toEqual([]);
    expect(good.list[1]).toEqual({ ...pictures[1], box: { width: 16, height: 16 } });
    // A data picture Chrome could not decode (the review's truncated PNG), an unpinned one it did.
    expect(listPictures(pictures, urls, drawn([false, true, true, false, false])).problems).toEqual(
      [
        "picture-decoded: picture 0 (data) not decoded",
        "picture-decoded: picture 2 (unpinned) decoded",
      ],
    );
    // One drawn picture fewer, and one drawn from another URL.
    const fewer = drawn([true, true, false, false, false]);
    fewer.pictures.pop();
    const moved = fewer.pictures[1];
    if (moved !== undefined) moved.source = UNPINNED_URL;
    expect(listPictures(pictures, urls, fewer).problems).toEqual([
      "picture-count: 4 drawn, 5 prepared",
      "picture-source: picture 1 drawn from another URL",
    ]);
    // The same box from other bytes differs in the list's hashes alone.
    const listOf = (png: string) => {
      const prepared = preparePictures(`<p><img src="data:image/png;base64,${png}"/></p>`);
      return listPictures(prepared.pictures, prepared.urls, {
        text: "",
        markers: [],
        tables: [],
        pictures: [{ width: 30, height: 20, decoded: true, source: prepared.urls[0] ?? "" }],
      }).list;
    };
    expect(comparePictureLists(listOf(RED_PNG), listOf(RED_PNG))).toEqual([]);
    expect(
      comparePictureLists(listOf(RED_PNG), listOf(GREEN_PNG)).map(({ property }) => property),
    ).toEqual(["picture list"]);
  });
});
