import { transformSection, TRefusal } from "./transform.js";

// T over a whole document (docs/design/authority-import-t.md, T5 and T6): every section once
// without the plus-sign waiver, not stopping at a refusal; then, again with it, the sections the
// first pass refused for `underline`, the evidence read from the sections the first pass accepted,
// whatever their order.

export type SectionOutcome = { div: string } | { refused: string };

function attempt(
  div: string,
  evidence?: ReadonlySet<string>,
): SectionOutcome & { tokens?: ReadonlySet<string> } {
  try {
    const result = transformSection(div, evidence);
    return { div: result.div, tokens: result.plainTokens };
  } catch (error) {
    if (error instanceof TRefusal) return { refused: error.reason };
    throw error;
  }
}

// The outcome of T for each section's div, in the order given (pre-order); undefined where the
// section has no div.
export function transformDocument(
  divs: readonly (string | undefined)[],
): (SectionOutcome | undefined)[] {
  const first = divs.map((div) => (div === undefined ? undefined : attempt(div)));
  const evidence = new Set<string>();
  for (const outcome of first) {
    if (outcome !== undefined && "div" in outcome)
      for (const token of outcome.tokens ?? []) evidence.add(token);
  }
  return first.map((outcome, index) => {
    if (outcome === undefined) return undefined;
    if ("refused" in outcome && outcome.refused === "underline") {
      const second = attempt(divs[index] ?? "", evidence);
      if ("div" in second) return { div: second.div };
      return { refused: outcome.refused };
    }
    return "div" in outcome ? { div: outcome.div } : { refused: outcome.refused };
  });
}
