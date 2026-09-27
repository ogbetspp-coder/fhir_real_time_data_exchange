// A small reader of the pinned fonts' own tables (docs/design/authority-import-renderer.md, R3 and
// R6): the PostScript name, the character map and the vertical metrics, read from the sfnt
// (TrueType) bytes with no dependency. The judge reads no font but the pinned files.

export class FontError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "FontError";
  }
}

export type Metrics = {
  unitsPerEm: number;
  // hhea's ascender, descender (negative below the baseline) and line gap.
  hheaAscender: number;
  hheaDescender: number;
  hheaLineGap: number;
  // OS/2's typographic and Windows metrics, and whether USE_TYPO_METRICS is set.
  typoAscender: number;
  typoDescender: number;
  typoLineGap: number;
  winAscent: number;
  winDescent: number;
  useTypoMetrics: boolean;
};

export type Font = {
  postScriptName: string;
  // Every code point the character map gives a glyph other than .notdef.
  codePoints: ReadonlySet<number>;
  metrics: Metrics;
};

type Tables = Map<string, { offset: number; length: number }>;

function tables(bytes: Buffer): Tables {
  if (bytes.length < 12) throw new FontError("not an sfnt font: too short");
  const version = bytes.readUInt32BE(0);
  if (version !== 0x00010000 && version !== 0x74727565) {
    throw new FontError(`not a TrueType font (version 0x${version.toString(16)})`);
  }
  const count = bytes.readUInt16BE(4);
  const found: Tables = new Map();
  for (let index = 0; index < count; index += 1) {
    const record = 12 + index * 16;
    if (record + 16 > bytes.length) throw new FontError("table directory past the end");
    const tag = bytes.toString("latin1", record, record + 4);
    const offset = bytes.readUInt32BE(record + 8);
    const length = bytes.readUInt32BE(record + 12);
    if (offset + length > bytes.length) throw new FontError(`table ${tag} past the end`);
    found.set(tag, { offset, length });
  }
  return found;
}

function table(found: Tables, tag: string): { offset: number; length: number } {
  const entry = found.get(tag);
  if (entry === undefined) throw new FontError(`no ${tag} table`);
  return entry;
}

// The PostScript name (name ID 6), from a Windows Unicode or a Macintosh Roman record.
function postScriptName(bytes: Buffer, found: Tables): string {
  const { offset } = table(found, "name");
  const count = bytes.readUInt16BE(offset + 2);
  const strings = offset + bytes.readUInt16BE(offset + 4);
  let fallback: string | undefined;
  for (let index = 0; index < count; index += 1) {
    const record = offset + 6 + index * 12;
    const platform = bytes.readUInt16BE(record);
    const encoding = bytes.readUInt16BE(record + 2);
    const nameId = bytes.readUInt16BE(record + 6);
    if (nameId !== 6) continue;
    const length = bytes.readUInt16BE(record + 8);
    const start = strings + bytes.readUInt16BE(record + 10);
    const raw = bytes.subarray(start, start + length);
    if (platform === 3 && (encoding === 1 || encoding === 10))
      return raw.swap16().toString("utf16le");
    if (platform === 1 && encoding === 0) fallback = raw.toString("latin1");
  }
  if (fallback !== undefined) return fallback;
  throw new FontError("no PostScript name");
}

// The code points of a format 4 (BMP) or format 12 (full range) subtable.
function codePoints(bytes: Buffer, found: Tables): Set<number> {
  const { offset } = table(found, "cmap");
  const count = bytes.readUInt16BE(offset + 2);
  let format4: number | undefined;
  let format12: number | undefined;
  for (let index = 0; index < count; index += 1) {
    const record = offset + 4 + index * 8;
    const platform = bytes.readUInt16BE(record);
    const encoding = bytes.readUInt16BE(record + 2);
    const subtable = offset + bytes.readUInt32BE(record + 4);
    const format = bytes.readUInt16BE(subtable);
    if (format === 12 && ((platform === 3 && encoding === 10) || platform === 0))
      format12 = subtable;
    if (format === 4 && ((platform === 3 && encoding === 1) || platform === 0)) format4 = subtable;
  }
  const points = new Set<number>();
  if (format12 !== undefined) {
    const groups = bytes.readUInt32BE(format12 + 12);
    for (let group = 0; group < groups; group += 1) {
      const at = format12 + 16 + group * 12;
      const start = bytes.readUInt32BE(at);
      const end = bytes.readUInt32BE(at + 4);
      const glyph = bytes.readUInt32BE(at + 8);
      for (let point = start; point <= end; point += 1) {
        if (glyph + (point - start) !== 0) points.add(point);
      }
    }
    return points;
  }
  if (format4 === undefined) throw new FontError("no Unicode cmap subtable of format 4 or 12");
  const segments = bytes.readUInt16BE(format4 + 6) / 2;
  const ends = format4 + 14;
  const starts = ends + segments * 2 + 2;
  const deltas = starts + segments * 2;
  const rangeOffsets = deltas + segments * 2;
  for (let segment = 0; segment < segments; segment += 1) {
    const end = bytes.readUInt16BE(ends + segment * 2);
    const start = bytes.readUInt16BE(starts + segment * 2);
    const delta = bytes.readInt16BE(deltas + segment * 2);
    const rangeOffsetAt = rangeOffsets + segment * 2;
    const rangeOffset = bytes.readUInt16BE(rangeOffsetAt);
    for (let point = start; point <= end && point !== 0xffff; point += 1) {
      let glyph: number;
      if (rangeOffset === 0) glyph = (point + delta) & 0xffff;
      else {
        const glyphAt = rangeOffsetAt + rangeOffset + (point - start) * 2;
        const raw = bytes.readUInt16BE(glyphAt);
        glyph = raw === 0 ? 0 : (raw + delta) & 0xffff;
      }
      if (glyph !== 0) points.add(point);
    }
  }
  return points;
}

function metrics(bytes: Buffer, found: Tables): Metrics {
  const head = table(found, "head").offset;
  const hhea = table(found, "hhea").offset;
  const os2 = table(found, "OS/2").offset;
  return {
    unitsPerEm: bytes.readUInt16BE(head + 18),
    hheaAscender: bytes.readInt16BE(hhea + 4),
    hheaDescender: bytes.readInt16BE(hhea + 6),
    hheaLineGap: bytes.readInt16BE(hhea + 8),
    typoAscender: bytes.readInt16BE(os2 + 68),
    typoDescender: bytes.readInt16BE(os2 + 70),
    typoLineGap: bytes.readInt16BE(os2 + 72),
    winAscent: bytes.readUInt16BE(os2 + 74),
    winDescent: bytes.readUInt16BE(os2 + 76),
    useTypoMetrics: (bytes.readUInt16BE(os2 + 62) & 0x80) !== 0,
  };
}

export function readFont(bytes: Buffer): Font {
  const found = tables(bytes);
  return {
    postScriptName: postScriptName(Buffer.from(bytes), found),
    codePoints: codePoints(bytes, found),
    metrics: metrics(bytes, found),
  };
}
