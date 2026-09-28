import { analyseSection, plainTokens, TRefusal, type Analysis } from "./transform.js";

// T over a whole document (docs/design/authority-import-t.md, T5 and T6): every section once
// without the plus-sign waiver, not stopping at a refusal; then, again with it, the sections the
// first pass refused for `underline`, the evidence read from the sections the first pass accepted,
// whatever their order.

export type SectionOutcome = { div: string } | { refused: string };

// T's two passes, the one routine for T(div) and for the model output (src/authority/t/model.ts):
// `accept` is given each accepted section's analysis once, as T accepts it, and what it returns is
// the section's outcome. With `model`, T records what the model output needs. Undefined where the
// section has no div.
export function analyseDocument<T>(
  divs: readonly (string | undefined)[],
  accept: (analysis: Analysis) => T,
  options: { model?: boolean } = {},
): ({ accepted: T } | { refused: string } | undefined)[] {
  const attempt = (
    div: string,
    evidence?: ReadonlySet<string>,
  ): { accepted: T; tokens: ReadonlySet<string> } | { refused: string } => {
    try {
      const analysis = analyseSection(div, evidence, options);
      return {
        accepted: accept(analysis),
        tokens: plainTokens(analysis.walk.codes, analysis.walk.points),
      };
    } catch (error) {
      if (error instanceof TRefusal) return { refused: error.reason };
      throw error;
    }
  };
  const first = divs.map((div) => (div === undefined ? undefined : attempt(div)));
  const evidence = new Set<string>();
  for (const outcome of first) {
    if (outcome !== undefined && "accepted" in outcome) {
      for (const token of outcome.tokens) evidence.add(token);
    }
  }
  return first.map((outcome, index) => {
    if (outcome === undefined) return undefined;
    if ("refused" in outcome && outcome.refused === "underline") {
      const second = attempt(divs[index] ?? "", evidence);
      if ("accepted" in second) return { accepted: second.accepted };
      return { refused: outcome.refused };
    }
    return "accepted" in outcome ? { accepted: outcome.accepted } : { refused: outcome.refused };
  });
}

// The outcome of T for each section's div, in the order given (pre-order); undefined where the
// section has no div.
export function transformDocument(
  divs: readonly (string | undefined)[],
): (SectionOutcome | undefined)[] {
  return analyseDocument(divs, (analysis) => analysis.output).map((outcome) =>
    outcome === undefined || "refused" in outcome ? outcome : { div: outcome.accepted },
  );
}
