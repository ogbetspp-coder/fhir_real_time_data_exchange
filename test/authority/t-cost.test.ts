import { describe, expect, it } from "vitest";

import { transformDocument } from "../../src/authority/t/document.js";
import { transformSection, TRefusal } from "../../src/authority/t/transform.js";

// T is linear enough for a section of several megabytes, and refuses deep nesting rather than
// overflowing the stack (the second code review: each of these took seconds to minutes, or threw).

const div = (inner: string): string =>
  `<div xmlns="http://www.w3.org/1999/xhtml">${inner}<p>not for clinical use</p></div>`;

function outcome(action: () => unknown): string {
  try {
    action();
    return "ok";
  } catch (error) {
    if (error instanceof TRefusal) return error.reason;
    throw error;
  }
}

describe("T's cost", () => {
  it("handles long lists, tall cells, many runs and large sections quickly", () => {
    const started = performance.now();
    expect(outcome(() => transformSection(div(`<ol>${"<li>x</li>".repeat(20000)}</ol>`)))).toBe(
      "list",
    );
    expect(
      outcome(() =>
        transformSection(div(`<ol style="margin-left:100pt">${"<li>x</li>".repeat(2000)}</ol>`)),
      ),
    ).toBe("ok");
    expect(
      outcome(() =>
        transformSection(
          div(
            `<table><tr><td rowspan="1000">${"<p>x</p>".repeat(100000)}</td><td>y</td></tr>${"<tr><td>z</td></tr>".repeat(999)}</table>`,
          ),
        ),
      ),
    ).toBe("ok");
    expect(
      outcome(() =>
        transformDocument([div("<p>(Ph+ ALL)</p>"), div("<p><u>Ph+ ALL</u></p>".repeat(40000))]),
      ),
    ).toBe("ok");
    expect(
      outcome(() =>
        transformSection(div(`<p style="font-family:'${"&#10;".repeat(50000)}">x</p>`)),
      ),
    ).toBe("css-grammar");
    expect(
      outcome(() =>
        transformSection(div(`<p>${"<span>".repeat(10000)}x${"</span>".repeat(10000)}</p>`)),
      ),
    ).toBe("markup");
    expect(
      outcome(() =>
        transformSection(
          div('<p style="font-size:11pt;margin:0cm">Dose 10 mg daily.</p>'.repeat(70000)),
        ),
      ),
    ).toBe("ok");
    // About 2 s here; minutes when any of these was quadratic. Room for coverage instrumentation.
    expect(performance.now() - started).toBeLessThan(30_000);
  }, 60_000);
});
