// The quote-edge rule's worked examples (docs/design/epi-mcp-query-service.md, "The quote-edge
// rule"), as data. test/query/acceptance.test.ts drives each one through the real MCP tool, and
// scripts/contracts/export-quote-edge-cases.ts publishes the rule's answer to each as
// test/fixtures/contracts/quote-edge-cases.json, which the agent's test double is held to
// (agent/tests/test_quote_edge.py). One list, so the service's proof and the double's cannot
// drift apart.

export type QuoteEdgeCase = { text: string; cut: string[]; whole: string[] };

// Each text becomes the whole narrative of one section, as a single paragraph.
export function quoteEdgeDiv(text: string): string {
  return `<div xmlns="http://www.w3.org/1999/xhtml"><p>${text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")}</p></div>`;
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
