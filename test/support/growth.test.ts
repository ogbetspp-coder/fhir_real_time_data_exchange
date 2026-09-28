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
  });

  it("reads quadratic work as over 10", () => {
    expect(growth((size) => work(size * size), 5_000)).toBeGreaterThan(10);
  }, 30_000);

  it("refuses an input too small to time", () => {
    expect(() => growth(() => () => 0, 1)).toThrow("choose a larger size");
  });
});
