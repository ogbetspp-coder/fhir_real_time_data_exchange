import { describe, expect, it } from "vitest";

import { normalizeText, xhtmlToText } from "../../src/fidelity/index.js";
import { locateQuote } from "../../src/query/tools.js";

// The quote-edge rule reads a section's table grids once per search, not once per occurrence: a
// quote that occurs at the edge of every one of section 5's 50 000 slots is decided in linear
// time. Rebuilding the grid per occurrence took minutes here, inside a synchronous call the
// request deadline cannot interrupt (fidelity-norm/3.0.0 review round 18).
describe("the quote-edge rule's cost", () => {
  it("decides a quote at every cell edge of a 50 000-slot table quickly", () => {
    const row = `<tr>${"<td>1</td>".repeat(200)}</tr>`;
    const div = `<div xmlns="http://www.w3.org/1999/xhtml"><table>${row.repeat(250)}</table></div>`;
    const text = normalizeText(xhtmlToText(div));
    const started = performance.now();
    expect(locateQuote(text, "1")).toBeUndefined();
    expect(performance.now() - started).toBeLessThan(2000);
  });
});

// Branches of the rule the worked examples do not reach, on normalised text written directly.
describe("the quote-edge rule's corners", () => {
  const T = "﷐";
  const END = "﷑";
  const ROW = "﷒";
  const CELL = "﷓";
  const LEFT = "﷔";
  const ABOVE = "﷕";

  it("reads opening punctuation at the start of the text and refuses it after a letter", () => {
    expect(locateQuote("(see below)", "see below")).toBeDefined();
    expect(locateQuote("a(see below)", "see below")).toBeUndefined();
    expect(locateQuote("CrCl < (30 ml/min)", "(30 ml/min)")).toBeUndefined();
  });

  it("holds a quote to the cells on both sides, through spans and an unclosed table", () => {
    // A number ending the quote with a word, not a number, after it in the next cell.
    const words = `${T} ${ROW} ${CELL} 10 ${CELL} mg ${END}`;
    expect(locateQuote(words, "10")).toBeDefined();
    // A quote ending in a word next to a cell starting with a digit.
    const unit = `${T} ${ROW} ${CELL} dose ${CELL} 10 ${END}`;
    expect(locateQuote(unit, "dose")).toBeDefined();
    // A slot covered from above in the first row has no owner; it reads as nothing.
    const orphan = `${T} ${ROW} ${ABOVE} ${CELL} 10 ${END}`;
    expect(locateQuote(orphan, "10")).toBeDefined();
    // A cell spanning two columns reaches the cell after its last column.
    const spanned = `${T} ${ROW} ${CELL} 5 ${LEFT} ${CELL} 000 ${END}`;
    expect(locateQuote(spanned, "5")).toBeUndefined();
    // A table the text never closes is read to the end of the text.
    const open = `${T} ${ROW} ${CELL} 10 ${CELL} 000`;
    expect(locateQuote(open, "000")).toBeUndefined();
    // A sign ending the last word of a cell on the left binds the quote.
    const sign = `${T} ${ROW} ${CELL} CrCl ≥ ${CELL} 30 ml/min ${END}`;
    expect(locateQuote(sign, "30 ml/min")).toBeUndefined();
  });
});
