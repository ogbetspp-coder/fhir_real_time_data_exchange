import { describe, expect, it } from "vitest";

import { FontError, readFont } from "../../src/render/font.js";

// The pinned fonts' reader, on fonts built here table by table: a PostScript name, a character map
// of format 4 or 12, and the vertical metrics.

function table(fields: [number, number][]): Buffer {
  const buffer = Buffer.alloc(fields.reduce((sum, [, size]) => sum + size, 0));
  let at = 0;
  for (const [value, size] of fields) {
    if (size === 2) buffer.writeInt16BE(value, at);
    else buffer.writeUInt32BE(value >>> 0, at);
    at += size;
  }
  return buffer;
}

function head(unitsPerEm: number): Buffer {
  const buffer = Buffer.alloc(54);
  buffer.writeUInt16BE(unitsPerEm, 18);
  return buffer;
}
function hhea(ascender: number, descender: number, lineGap: number): Buffer {
  const buffer = Buffer.alloc(36);
  buffer.writeInt16BE(ascender, 4);
  buffer.writeInt16BE(descender, 6);
  buffer.writeInt16BE(lineGap, 8);
  return buffer;
}
function os2(typo: [number, number, number], win: [number, number], useTypo: boolean): Buffer {
  const buffer = Buffer.alloc(78);
  buffer.writeUInt16BE(useTypo ? 0x80 : 0, 62);
  buffer.writeInt16BE(typo[0], 68);
  buffer.writeInt16BE(typo[1], 70);
  buffer.writeInt16BE(typo[2], 72);
  buffer.writeUInt16BE(win[0], 74);
  buffer.writeUInt16BE(win[1], 76);
  return buffer;
}
function name(postScript: string, platform: "windows" | "mac"): Buffer {
  const text =
    platform === "windows"
      ? Buffer.from(postScript, "utf16le").swap16()
      : Buffer.from(postScript, "latin1");
  const header = Buffer.alloc(6 + 12);
  header.writeUInt16BE(0, 0);
  header.writeUInt16BE(1, 2);
  header.writeUInt16BE(18, 4);
  header.writeUInt16BE(platform === "windows" ? 3 : 1, 6);
  header.writeUInt16BE(platform === "windows" ? 1 : 0, 8);
  header.writeUInt16BE(6, 12);
  header.writeUInt16BE(text.length, 14);
  header.writeUInt16BE(0, 16);
  return Buffer.concat([header, text]);
}
// A format 4 subtable mapping each [start, end] segment by delta to glyphs from 1.
function cmap4(segments: [number, number][]): Buffer {
  const all = [...segments, [0xffff, 0xffff] as [number, number]];
  const count = all.length;
  const body = Buffer.alloc(14 + count * 8 + 2);
  body.writeUInt16BE(4, 0);
  body.writeUInt16BE(body.length, 2);
  body.writeUInt16BE(count * 2, 6);
  let glyph = 1;
  all.forEach(([start, end], index) => {
    body.writeUInt16BE(end, 14 + index * 2);
    body.writeUInt16BE(start, 14 + count * 2 + 2 + index * 2);
    const delta = start === 0xffff ? 1 : (glyph - start) & 0xffff;
    body.writeUInt16BE(delta, 14 + count * 4 + 2 + index * 2);
    body.writeUInt16BE(0, 14 + count * 6 + 2 + index * 2);
    glyph += end - start + 1;
  });
  return cmapWith(3, 1, body);
}
function cmap12(groups: [number, number, number][]): Buffer {
  const body = Buffer.alloc(16 + groups.length * 12);
  body.writeUInt16BE(12, 0);
  body.writeUInt32BE(body.length, 4);
  body.writeUInt32BE(groups.length, 12);
  groups.forEach(([start, end, glyph], index) => {
    body.writeUInt32BE(start, 16 + index * 12);
    body.writeUInt32BE(end, 20 + index * 12);
    body.writeUInt32BE(glyph, 24 + index * 12);
  });
  return cmapWith(3, 10, body);
}
function cmapWith(platform: number, encoding: number, subtable: Buffer): Buffer {
  const header = Buffer.alloc(12);
  header.writeUInt16BE(1, 2);
  header.writeUInt16BE(platform, 4);
  header.writeUInt16BE(encoding, 6);
  header.writeUInt32BE(12, 8);
  return Buffer.concat([header, subtable]);
}
function sfnt(tables: Record<string, Buffer>, version = 0x00010000): Buffer {
  const tags = Object.keys(tables).sort();
  const directory = Buffer.alloc(12 + tags.length * 16);
  directory.writeUInt32BE(version, 0);
  directory.writeUInt16BE(tags.length, 4);
  let offset = directory.length;
  const bodies: Buffer[] = [];
  tags.forEach((tag, index) => {
    const body = tables[tag] ?? Buffer.alloc(0);
    directory.write(tag.padEnd(4, " "), 12 + index * 16, "latin1");
    directory.writeUInt32BE(offset, 12 + index * 16 + 8);
    directory.writeUInt32BE(body.length, 12 + index * 16 + 12);
    bodies.push(body);
    offset += body.length;
  });
  return Buffer.concat([directory, ...bodies]);
}
const base = (extra: Record<string, Buffer>): Record<string, Buffer> => ({
  head: head(2048),
  hhea: hhea(1825, -443, 87),
  "OS/2": os2([1420, -442, 307], [1825, 443], false),
  ...extra,
});

describe("the pinned fonts' reader", () => {
  it("reads a Windows PostScript name, a format 4 map and the metrics", () => {
    const font = readFont(
      sfnt(
        base({
          name: name("LiberationSerif", "windows"),
          cmap: cmap4([
            [0x20, 0x7e],
            [0xa0, 0xa0],
          ]),
        }),
      ),
    );
    expect(font.postScriptName).toBe("LiberationSerif");
    expect(font.codePoints.has(0x41)).toBe(true);
    expect(font.codePoints.has(0xa0)).toBe(true);
    expect(font.codePoints.has(0x2070)).toBe(false);
    expect(font.metrics).toEqual({
      unitsPerEm: 2048,
      hheaAscender: 1825,
      hheaDescender: -443,
      hheaLineGap: 87,
      typoAscender: 1420,
      typoDescender: -442,
      typoLineGap: 307,
      winAscent: 1825,
      winDescent: 443,
      useTypoMetrics: false,
    });
  });

  it("prefers a format 12 map, reads a Macintosh name, and leaves out .notdef", () => {
    const font = readFont(
      sfnt(
        base({
          name: name("Carlito-Regular", "mac"),
          cmap: cmap12([
            [0x41, 0x43, 1],
            [0x2070, 0x2070, 0],
            [0x1d400, 0x1d401, 9],
          ]),
        }),
        0x74727565,
      ),
    );
    expect(font.postScriptName).toBe("Carlito-Regular");
    expect([...font.codePoints].sort((a, b) => a - b)).toEqual([
      0x41, 0x42, 0x43, 0x1d400, 0x1d401,
    ]);
  });

  it("refuses bytes that are not a TrueType font, or lack a table it reads", () => {
    expect(() => readFont(Buffer.alloc(4))).toThrow(FontError);
    expect(() => readFont(sfnt({}, 0x4f54544f))).toThrow(/not a TrueType font/);
    expect(() => readFont(sfnt(base({ cmap: cmap4([[0x41, 0x41]]) })))).toThrow(/no name table/);
    expect(() => readFont(sfnt(base({ name: name("X", "windows") })))).toThrow(/no cmap table/);
    const truncated = sfnt(base({ name: name("X", "windows"), cmap: cmap4([[0x41, 0x41]]) }));
    expect(() => readFont(truncated.subarray(0, 40))).toThrow(/past the end/);
    expect(() =>
      readFont(
        sfnt(
          base({
            name: Buffer.from(
              table([
                [0, 2],
                [0, 2],
                [6, 2],
              ]),
            ),
            cmap: cmap4([[0x41, 0x41]]),
          }),
        ),
      ),
    ).toThrow(/no PostScript name/);
  });
});
