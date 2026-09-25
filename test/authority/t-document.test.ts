import { describe, expect, it } from "vitest";

import { transformDocument } from "../../src/authority/t/document.js";

// T over a whole document (docs/design/authority-import-t.md, T5): the plus-sign exception's two
// passes, and where its evidence may come from.

const div = (inner: string): string =>
  `<div xmlns="http://www.w3.org/1999/xhtml">${inner}<p>not for clinical use</p></div>`;
const DEFINITION = div(
  "<p>Philadelphia chromosome positive acute lymphoblastic leukaemia (Ph+ ALL).</p>",
);
const SUBHEADING = div("<p><u>Posology for Ph+ ALL in children</u></p>");

describe("T over a document", () => {
  it("waives an underlined plus on the document's own plain use of the token", () => {
    const [definition, subheading, none] = transformDocument([DEFINITION, SUBHEADING, undefined]);
    expect(definition).toEqual({ div: DEFINITION });
    expect(subheading).toEqual({
      div: div("<p>Posology for Ph+ ALL in children</p>"),
    });
    expect(none).toBeUndefined();
  });

  it("reads the evidence from a later section as well", () => {
    const [subheading] = transformDocument([SUBHEADING, DEFINITION]);
    expect(subheading).toEqual({ div: div("<p>Posology for Ph+ ALL in children</p>") });
  });

  it("refuses the subheading where no accepted section writes the token plainly", () => {
    expect(transformDocument([SUBHEADING])).toEqual([{ refused: "underline" }]);
    // A refused section's plain use is no evidence.
    const refusedDefinition = div(
      '<p style="font-family: Verdana">Philadelphia chromosome positive (Ph+ ALL).</p>',
    );
    expect(transformDocument([refusedDefinition, SUBHEADING])).toEqual([
      { refused: "font" },
      { refused: "underline" },
    ]);
  });

  it("takes as evidence only a token written plainly, whole and followed by a space", () => {
    for (const plain of [
      "<p>xPh+ ALL</p>", // a longer token
      "<p>Ph+2 ALL</p>", // not followed by a space
      "<p><u>Ph+</u> ALL</p>", // underlined
      "<p>Ph<sup>+</sup> ALL</p>", // raised
      "<p>Ph+́ ALL</p>", // a mark on the sign
    ]) {
      const [, subheading] = transformDocument([div(plain), SUBHEADING]);
      expect([plain, subheading]).toEqual([plain, { refused: "underline" }]);
    }
  });

  it("keeps a first-pass refusal other than an underline", () => {
    expect(transformDocument([div('<p style="display: none">x</p>')])).toEqual([
      { refused: "css-property" },
    ]);
  });
});
