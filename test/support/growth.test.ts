import { describe, expect, it } from "vitest";

import { growth } from "./growth.js";

// The cost tests' bound (under 10 for a quarter of the input against the whole) tells the two
// apart: seeded here on work whose growth is known.
describe("growth", () => {
  const work = (steps: number) => (): number => {
    let total = 0;
    for (let step = 0; step < steps; step += 1) total = (total + step) | 0;
    return total;
  };

  it("reads linear work as under 10", () => {
    expect(growth((size) => work(size * 2_000), 2_000)).toBeLessThan(10);
  }, 60_000);

  // 2 000 against 8 000 squared: about 16, and under coverage about half a second a large run
  // (review of B15: 5 000 took 28 s of a 30 s limit there).
  it("reads quadratic work as over 10", () => {
    expect(growth((size) => work(size * size), 2_000)).toBeGreaterThan(10);
  }, 120_000);

  it("returns the lowest round, and stops at a linear one", () => {
    // The large input runs 6 times a round (once untimed, 5 timed) and is five times slower in the
    // first round alone, as if a busy spell had landed on it: about 20, then about 4, which is
    // linear, so no third round is timed.
    let calls = 0;
    const slowFirst = (size: number) => {
      let runs = 0;
      return (): number => {
        calls += 1;
        runs += 1;
        return work(size * (size > 2_000 && runs <= 6 ? 5 * 2_000 : 2_000))();
      };
    };
    const ratio = growth(slowFirst, 2_000);
    expect(ratio).toBeLessThan(8);
    expect(calls).toBe(24);
  }, 60_000);

  it("refuses an input too small to time", () => {
    expect(() => growth(() => () => 0, 1)).toThrow("choose a larger size");
  });
});
