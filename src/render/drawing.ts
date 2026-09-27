import { readMarkers } from "./measure.js";
import type { Page } from "./page.js";

// R2's second drawing (docs/design/authority-import-renderer.md): the text, list numbers, table
// grids and pictures of the authority's drawing, which do not depend on the width, compared with
// T(div)'s at the named widths. T drops presentation and never text, so any difference is a
// refusal of ours (`second-drawing`).

export type Rect = { left: number; top: number; right: number; bottom: number };

export type Drawing = {
  // The div's rendered text (`innerText`): the words, in order, with the line and cell breaks
  // the drawing makes.
  text: string;
  // Each list item's marker text, in document order.
  markers: string[];
  // Each table's cells (not its caption), in document order, by their rectangles.
  tables: Rect[][];
  // Each picture's drawn box, in document order.
  pictures: { width: number; height: number }[];
};

const READ_DRAWING = `(() => {
  const root = document.body.firstElementChild;
  const box = (element) => {
    const r = element.getBoundingClientRect();
    return { left: r.left, top: r.top, right: r.right, bottom: r.bottom };
  };
  const tables = [];
  for (const table of root.querySelectorAll("table")) {
    const cells = [];
    for (const cell of table.querySelectorAll("td, th")) {
      if (cell.closest("table") === table) cells.push(box(cell));
    }
    tables.push(cells);
  }
  const pictures = [];
  for (const picture of root.querySelectorAll("img")) {
    const r = picture.getBoundingClientRect();
    pictures.push({ width: r.width, height: r.height });
  }
  return { text: root.innerText, tables, pictures };
})()`;

export async function readDrawing(page: Page): Promise<Drawing> {
  const read = await page.evaluate<Omit<Drawing, "markers">>(READ_DRAWING);
  const markers = [...(await readMarkers(page))]
    .sort(([first], [second]) => first - second)
    .map(([, text]) => text);
  return { ...read, markers };
}

// A table's grid from its cells' rectangles: the grid lines are the distinct edges where cells
// start (edges within half a CSS pixel are one line), a cell's row and column the lines it starts
// on, and its span the number of those lines it covers. Starts, not ends, because the spacing
// between cells (2 px by default, 0 in a collapsed or `cellspacing="0"` table) puts a cell's end
// and its neighbour's start apart. The grid, not the geometry, is compared: T drops widths,
// borders, padding and spacing, which move the edges.
export type GridCell = { row: number; rows: number; column: number; columns: number };

function lines(values: readonly number[]): number[] {
  const sorted = [...values].sort((first, second) => first - second);
  const found: number[] = [];
  for (const value of sorted) {
    const last = found.at(-1);
    if (last === undefined || value - last > 0.5) found.push(value);
  }
  return found;
}

// The index of the line a start stands on, and how many lines lie from it up to an end.
function place(found: readonly number[], start: number, end: number): [number, number] {
  const first = found.findIndex((line) => Math.abs(line - start) <= 0.5);
  const covered = found.filter((line) => line >= start - 0.5 && line < end - 0.5).length;
  return [first, covered];
}

export function grid(cells: readonly Rect[]): GridCell[] {
  const columns = lines(cells.map(({ left }) => left));
  const rows = lines(cells.map(({ top }) => top));
  return cells.map(({ left, right, top, bottom }) => {
    const [column, columnSpan] = place(columns, left, right);
    const [row, rowSpan] = place(rows, top, bottom);
    return { row, rows: rowSpan, column, columns: columnSpan };
  });
}

export type DrawingMismatch = { property: string; authority: string; t: string };

// The authority's drawing against T(div)'s.
export function compareDrawings(authority: Drawing, t: Drawing): DrawingMismatch[] {
  const found: DrawingMismatch[] = [];
  const differ = (property: string, first: unknown, second: unknown): void => {
    const a = JSON.stringify(first);
    const b = JSON.stringify(second);
    if (a !== b) found.push({ property, authority: a, t: b });
  };
  differ("text", authority.text, t.text);
  differ("markers", authority.markers, t.markers);
  differ("tables", authority.tables.length, t.tables.length);
  authority.tables.forEach((cells, index) => {
    differ(`table ${index}`, grid(cells), grid(t.tables[index] ?? []));
  });
  differ("pictures", authority.pictures, t.pictures);
  return found;
}
