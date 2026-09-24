// The quote-edge rule's worked examples (docs/design/epi-mcp-query-service.md, "The quote-edge
// rule"), as data. test/query/acceptance.test.ts drives each one through the real MCP tool, and
// scripts/contracts/export-quote-edge-cases.ts publishes the rule's answer to each as
// test/fixtures/contracts/quote-edge-cases.json, which the agent's test double is held to
// (agent/tests/test_quote_edge.py). One list, so the service's proof and the double's cannot
// drift apart.

// A case's section is `text`, a single paragraph, or `markup`, the narrative's inner XHTML (a
// table, whose grid only markup can carry).
export type QuoteEdgeCase = ({ text: string } | { markup: string }) & {
  cut: string[];
  whole: string[];
};

// Each case becomes the whole narrative of one section.
export function quoteEdgeDiv(section: QuoteEdgeCase): string {
  const inner =
    "markup" in section
      ? section.markup
      : `<p>${section.text
          .replaceAll("&", "&amp;")
          .replaceAll("<", "&lt;")
          .replaceAll(">", "&gt;")}</p>`;
  return `<div xmlns="http://www.w3.org/1999/xhtml">${inner}</div>`;
}

export const quoteEdgeCases: QuoteEdgeCase[] = [
  {
    // The typography fixture's own sentence.
    text: "The sponsor’s first dose is 5 mg/m² daily.",
    // A unit cut before its superscript, a lone letter, a fragment straddling two words, a
    // unit cut at the slash, a word with its possessive cut off.
    cut: ["first dose is 5 mg/m", "e", "s first d", "5 mg/", "The sponsor"],
    whole: [
      "sponsor’s first dose is 5 mg/m²",
      "The sponsor’s",
      "5 mg/m² daily",
      "daily.",
      "The sponsor’s first dose is 5 mg/m² daily.",
    ],
  },
  {
    text: "The dose is 5 mg/m². The dose is 5 mg/kg body weight.",
    cut: ["The dose is 5 mg", "The dose is 5 mg/m", "The dose is 5 mg/"],
    whole: ["The dose is 5 mg/m²", "The dose is 5 mg/m².", "5 mg/kg body weight"],
  },
  {
    text: "Take 2.5 mg daily, with water. Take 10,5 mg at night.",
    cut: ["Take 2", "Take 10", "Take 2.", "5 mg daily"],
    whole: ["Take 2.5 mg daily", "with water", "Take 10,5 mg at night"],
  },
  {
    text: "Adjust the dose (see section 4.4) when needed; stop (see section 4.8).",
    cut: ["see section 4", "(see section 4", "4) when needed"],
    whole: ["see section 4.4", "(see section 4.4)", "see section 4.8", "Adjust the dose"],
  },
  {
    // A hyphen-minus and a minus sign (U+2212) before the number.
    text: "Store at -20 °C. Ship at −20 °C.",
    cut: ["20 °C", "20 °C. Ship"],
    whole: ["-20 °C", "−20 °C", "Store at -20 °C."],
  },
  {
    // An unsigned temperature in its own document is quoted as it stands.
    text: "Keep at 20 °C.",
    cut: [],
    whole: ["Keep at 20 °C", "20 °C", "20 °C."],
  },
  {
    text: "Give <10 mg per day or ≥10 mg per day. Creatinine clearance ≥ 30 ml/min.",
    cut: ["10 mg per day", "30 ml/min"],
    whole: ["<10 mg per day", "≥10 mg per day", "≥ 30 ml/min"],
  },
  {
    text: "Treat non-diabetic patients first. Don't take with food. Don’t crush.",
    cut: ["diabetic patients", "t take with food", "t crush", "Don"],
    whole: ["non-diabetic patients", "Don't take with food", "Don’t crush"],
  },
  {
    text: 'Warning: take "one tablet" daily. Up to 1 000 000 IU daily.',
    cut: ["Up to 1 000", "000 IU daily", "Up to 1"],
    whole: ["Warning", "one tablet", '"one tablet"', "Up to 1 000 000 IU daily"],
  },
  {
    // fidelity-norm/3.0.0: a thin space and an invisible separator are content, yet drawn as a
    // gap, so a number grouped with one of them and a space is still one number.
    text: "The maximum dose is 10\u2009 000 IU daily. Up to 5\u2063 000 IU weekly.",
    cut: [
      "000 IU daily.",
      "The maximum dose is 10",
      "The maximum dose is 10\u2009",
      "000 IU weekly.",
      "Up to 5\u2063",
    ],
    whole: ["The maximum dose is 10\u2009 000 IU daily.", "Up to 5\u2063 000 IU weekly."],
  },
  {
    // fidelity-norm/3.0.0 review round 15: U+2800 BRAILLE PATTERN BLANK and a supplementary-plane
    // tag character are gaps too.
    text: "Take 10\u2800 000 IU daily. Up to 5\u{E0020} 000 IU weekly.",
    cut: ["Take 10", "Take 10\u2800", "000 IU daily.", "Up to 5\u{E0020}", "000 IU weekly."],
    whole: ["Take 10\u2800 000 IU daily.", "Up to 5\u{E0020} 000 IU weekly."],
  },
  {
    // Review round 17: table cells are drawn side by side, so a number or a sign split across
    // cells reads as one, with an empty cell between too; a quote of a whole cell still matches.
    markup:
      "<table><tr><td>Adults</td><td>10</td><td>000 IU</td><td>daily</td></tr><tr><td>Children</td><td>&lt;</td><td>5 mg</td><td>daily</td></tr><tr><td>Elderly</td><td>20</td><td></td><td>000 IU</td></tr></table>",
    cut: ["10", "000 IU", "5 mg", "20"],
    whole: ["Adults", "Children", "Elderly", "IU"],
  },
  {
    // A cell spanning rows is drawn level with any of them, and one spanning columns reaches
    // the cell after its last column.
    markup:
      '<table><tr><td rowspan="3">10</td><td>Adults</td><td>x</td></tr><tr><td>000 IU</td><td>y</td></tr><tr><td>daily</td><td>z</td></tr><tr><td colspan="2">5</td><td>000 mg</td></tr><tr><td>Dose</td><td>20 mg</td><td>w</td></tr></table>',
    cut: ["10", "000 IU", "5", "000 mg"],
    whole: ["Adults", "daily", "Dose", "20 mg"],
  },
  {
    // Review round 18: a cell's lines are centred, so any of them can sit level with a line of
    // the next cell ("Up to 10 000 IU", "CrCl < 30 ml/min"); every word of every cell on that
    // side counts.
    markup:
      "<table><tr><td>Up to 10</td><td>once<br/>000 IU<br/>weekly</td></tr><tr><td>if<br/>CrCl &lt;<br/>then</td><td>30 ml/min</td></tr></table>",
    cut: ["Up to 10", "000 IU", "000 IU weekly", "30 ml/min"],
    whole: ["once", "weekly", "if", "Up to"],
  },
  {
    // A sign binds across a gap drawn as nothing or as a thin space, and across an opening
    // bracket.
    text: "CrCl <\u2063 30 ml/min. Age >\u2009 65 years. CrCl < (15 ml/min). See (section 4.4).",
    cut: ["30 ml/min.", "65 years.", "(15 ml/min).", "15 ml/min"],
    whole: ["(section 4.4).", "section 4.4", "CrCl"],
  },
  {
    // Review round 16: a Mongolian or Yi letter the default serif face draws as a blank.
    text: "Take 10\u1878 000 IU daily. Up to 5\ua4c5 000 IU weekly.",
    cut: ["Take 10", "000 IU daily.", "Up to 5\ua4c5", "000 IU weekly."],
    whole: ["Take 10\u1878 000 IU daily.", "Up to 5\ua4c5 000 IU weekly."],
  },
  {
    // Letters outside the Basic Multilingual Plane, before and after a quote, and before a
    // match, so that offsets are counted in code points, not in UTF-16 units.
    text: "Code \u{1D400}5 mg. Take 5 mg\u{1D400} now. Code \u{1D400} then \u{1D401} dose.",
    cut: ["5 mg"],
    whole: ["\u{1D401} dose", "then \u{1D401} dose."],
  },
  {
    // The two cuts the agent's verification splitter made at a space before 2026-09-22, when a
    // block over the 2,000-unit quote bound was split at the last space in the window: inside a
    // space-grouped number, and between a comparator and its number. Each piece answered
    // no-match, and a verbatim block was flagged as not what the label says.
    text: "Give up to 1 000 000 IU daily. Reduce the dose when CrCl ≥ 30 ml/min.",
    cut: ["Give up to 1 000", "000 IU daily.", "30 ml/min."],
    whole: [
      "Give up to 1 000 000 IU daily.",
      "≥ 30 ml/min.",
      "Reduce the dose when CrCl ≥ 30 ml/min.",
    ],
  },
];
