import { createHash } from "node:crypto";

import { describe, expect, it } from "vitest";

import { compareDrawings, grid, type Drawing, type Rect } from "../../src/render/drawing.js";
import { ORIGIN } from "../../src/render/page.js";
import { preparePictures, UNPINNED_URL } from "../../src/render/pictures.js";
import { BLUE_PNG, PICTURE_FORMS, RED_PNG } from "../fixtures/render/drawing-cases.js";

// R2's pictures and second drawing, as rules on what the page is given and what Chrome reports
// (the renderer image's run of scripts/render/check-drawings.ts draws them).

const hash = (base64: string): string =>
  createHash("sha256").update(Buffer.from(base64, "base64")).digest("hex");

describe("R2's pictures", () => {
  it("draws a data URI from itself, a contained Binary from its bytes, and anything else broken", () => {
    const { div, resources, pictures } = preparePictures(
      PICTURE_FORMS.div,
      PICTURE_FORMS.contained,
    );
    expect(pictures).toEqual([
      { reference: `data:image/png;base64,${RED_PNG}`, form: "data", sha256: hash(RED_PNG) },
      { reference: "#blue", form: "contained", sha256: hash(BLUE_PNG) },
      {
        reference: "~/_entity/annotation/00000000-0000-0000-0000-000000000000",
        form: "unpinned",
        sha256: "none",
      },
    ]);
    const served = `${ORIGIN}pictures/${hash(BLUE_PNG)}`;
    expect(div).toContain(`src="data:image/png;base64,${RED_PNG}"`);
    expect(div).toContain(`src="${served}"`);
    expect(div).toContain(`src="${UNPINNED_URL}"`);
    expect(div).not.toContain("#blue");
    expect([...resources.keys()]).toEqual([served]);
    expect(resources.get(served)?.body.equals(Buffer.from(BLUE_PNG, "base64"))).toBe(true);
  });

  it("reads single quotes and XML's references, and leaves a div without pictures alone", () => {
    const { pictures, div } = preparePictures("<p><img alt='x' src='#a&amp;b'/></p>");
    expect(pictures).toEqual([{ reference: "#a&b", form: "unpinned", sha256: "none" }]);
    expect(div).toBe(`<p><img alt='x' src='${UNPINNED_URL}'/></p>`);
    expect(preparePictures("<p>none</p>")).toEqual({
      div: "<p>none</p>",
      resources: new Map(),
      pictures: [],
    });
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
      pictures: [{ width: 30, height: 20 }],
    };
    expect(compareDrawings(base, structuredClone(base))).toEqual([]);
    const properties = (other: Partial<Drawing>): string[] =>
      compareDrawings(base, { ...base, ...other }).map(({ property }) => property);
    expect(properties({ text: "Take 20 tablets" })).toEqual(["text"]);
    expect(properties({ markers: ["2. ", "3. "] })).toEqual(["markers"]);
    expect(properties({ tables: [[cell(0, 0, 20, 5)]] })).toEqual(["table 0"]);
    expect(properties({ tables: [] })).toEqual(["tables", "table 0"]);
    expect(properties({ pictures: [{ width: 12, height: 8 }] })).toEqual(["pictures"]);
  });
});
