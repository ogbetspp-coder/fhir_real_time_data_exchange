import { describe, expect, it } from "vitest";

import {
  buildModel,
  MODEL_FORMAT,
  modelDocument,
  modelSection,
  type Model,
} from "../../src/authority/t/model.js";
import { analyseSection, transformSection } from "../../src/authority/t/transform.js";
import { readTree, treeIndex } from "../../src/authority/t/tree.js";
import { xhtmlToText } from "../../src/fidelity/xhtml.js";
import { canonicalJson } from "../../src/lib/hash.js";
import { MODEL_CASES } from "../fixtures/render/model-cases.js";

// T's model output (docs/design/authority-import-renderer-model.md). Chrome's side of the
// comparison is test/render/compare-style.test.ts; here, what T emits and that emitting it
// changes nothing T decides.

const ROOT = '<div xmlns="http://www.w3.org/1999/xhtml">';
const MARKED = "<p>not for clinical use</p></div>";
const section = (inner: string): string => `${ROOT}${inner}${MARKED}`;
const model = (inner: string, evidence?: string[]): Model =>
  JSON.parse(
    modelSection(section(inner), evidence === undefined ? undefined : new Set(evidence)),
  ) as Model;

describe("T's model output", () => {
  it("is canonical JSON of the stated format, reproduced byte for byte", () => {
    const div = section('<p style="font-size:11pt">a<b>b</b></p>');
    const first = modelSection(div);
    expect(modelSection(div)).toBe(first);
    const parsed = JSON.parse(first) as Model;
    expect(parsed.format).toBe(MODEL_FORMAT);
    expect(canonicalJson(parsed)).toBe(first);
  });

  it("covers every synthetic model case, each accepted by T and the scanner", () => {
    for (const { inner } of MODEL_CASES) {
      expect(() => xhtmlToText(transformSection(section(inner)).div)).not.toThrow();
      expect(model(inner).elements.length).toBeGreaterThan(1);
    }
  });

  it("keys every element in document order, the div 0", () => {
    const { elements } = model("<p>a<b>b</b></p><ul><li>c</li></ul>");
    expect(elements.map(({ key, name }) => `${key}:${name}`)).toEqual([
      "0:div",
      "1:p",
      "2:b",
      "3:ul",
      "4:li",
      "5:p",
    ]);
  });

  it("counts the XML DOM's text: whitespace between table parts and list items, one LF for CR LF", () => {
    const inner = "<table>\r\n <tr>\n<td>a\r\nb\rc&#13;d</td></tr></table><ul> <li>e</li>\n</ul>";
    const { text } = model(inner);
    expect(text).toEqual([
      { key: 0, element: 1, start: 0, end: 2, interElement: true },
      { key: 1, element: 2, start: 2, end: 3, interElement: true },
      { key: 2, element: 3, start: 3, end: 10 },
      { key: 3, element: 4, start: 10, end: 11, interElement: true },
      { key: 4, element: 5, start: 11, end: 12 },
      { key: 5, element: 4, start: 12, end: 13, interElement: true },
      { key: 6, element: 6, start: 13, end: 33 },
    ]);
    // T's own code points are unchanged: a raw CR or LF is a space to the scanner.
    const root = readTree(section(inner));
    const cell = treeIndex(root).texts[2];
    expect(cell?.node?.points.join("")).toBe("a  b c d");
    expect(cell?.data.join("")).toBe("a\nb\nc\rd");
    expect(cell?.pointToDom).toEqual([0, 1, 1, 2, 3, 4, 5, 6]);
  });

  it("keeps a CR or LF given by reference as the DOM's own code point", () => {
    // Raw CR LF and lone CR are joined into one LF; &#13;&#10;, CR then &#10;, and &#13; then LF
    // are two code points each (the second review, measured in both modes).
    const [paragraph] = model("<p>a&#13;&#10;b\r&#10;c&#13;\nd</p>").text;
    expect(paragraph).toEqual({ key: 0, element: 1, start: 0, end: 10 });
  });

  it("maps a waived sign after a raw CR LF to the DOM's offset", () => {
    const waived = model("<p><u>Posology\r\nfor Ph+ ALL in children</u></p>", ["Ph+"]);
    const plus = "Posology\nfor Ph+".length - 1;
    expect(waived.waivers).toEqual([{ start: plus, end: plus + 1 }]);
  });

  it("models weights, styles and markers as Chrome draws them", () => {
    const { elements, markers } = model(
      '<p style="font-weight:lighter"><b>a</b></p><h2><i>b</i></h2><ol start="2"><li>c<ul><li>d<ul><li>e</li></ul></li></ul></li></ol>',
    );
    const weight = (name: string) =>
      elements.find((element) => element.name === name)?.style.fontWeight;
    expect([weight("p"), weight("b"), weight("h2")]).toEqual([100, 400, 700]);
    expect(elements.find((element) => element.name === "i")?.style.fontStyle).toBe("italic");
    expect(markers.map(({ text }) => text)).toEqual(["2. ", "◦ ", "■ "]);
    expect(markers.every(({ style }) => !style.underline)).toBe(true);
  });

  it("records T4's decisions and T5's waived signs in the DOM's offsets", () => {
    const shifted = model(
      '<p style="font-size:11pt">x 10<span style="position:relative;top:-4pt">9</span>/l</p>',
    );
    expect(shifted.folds).toEqual([{ element: 2, decision: "sup" }]);
    const waived = model("<p><u>Posology for Ph+ ALL in children</u></p>", ["Ph+"]);
    const plus = "Posology for Ph+".length - 1;
    expect(waived.waivers).toEqual([{ start: plus, end: plus + 1 }]);
  });

  it("models a document with T's two passes, the waived section with its evidence", () => {
    const divs = [
      section("<p>Ph+ ALL</p>"),
      section("<p><u>Posology for Ph+ ALL in children</u></p>"),
      undefined,
      section("<p><font>x</font></p>"),
    ];
    const models = modelDocument(divs);
    expect(models[0]).toBeDefined();
    expect((JSON.parse(models[1] ?? "{}") as Model).waivers).toHaveLength(1);
    expect(models[2]).toBeUndefined();
    expect(models[3]).toBeUndefined();
    // T accepts this, but the scanner refuses T(div) (an li inside an li): no model (M2).
    const nested = section("<ol><li>a<li>b</li></li></ol>");
    expect(() => modelSection(nested)).not.toThrow();
    expect(modelDocument([nested])).toEqual([undefined]);
  });

  it("is computed from T's own analysis, which T(div) is unchanged by", () => {
    const div = section('<p><span style="color:#000">a</span> <u>b</u></p>');
    const analysis = analyseSection(div);
    expect(analysis.output).toBe(transformSection(div).div);
    expect(canonicalJson(buildModel(analysis))).toBe(modelSection(div));
  });
});
