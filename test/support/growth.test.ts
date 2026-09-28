import { describe, expect, it } from "vitest";

import { growth } from "./growth.js";

// The cost tests' bound (under 10 for a quarter of the input against the whole) tells the two
// apart. The round logic is checked on a scripted clock, with exact durations, so nothing here
// but the one real-timing test depends on the machine's load: a real linear round read 9.26 under
// load once, which made an extra round and failed a count (review round 2 of audit B15).
describe("growth", () => {
  // A clock that moves only when a scripted input runs: `durations(size, run)` is how long the
  // run-th call (from 1) of the input of `size` takes. Calls are counted per input.
  const scripted = (durations: (size: number, run: number) => number) => {
    let time = 0;
    const calls: number[] = [];
    const prepare = (size: number) => {
      let run = 0;
      return () => {
        run += 1;
        calls.push(size);
        time += durations(size, run);
      };
    };
    return { prepare, now: () => time, calls };
  };

  it("reads linear work as its factor, in one round", () => {
    const clock = scripted((size) => size * 10);
    expect(growth(clock.prepare, 100, { now: clock.now })).toBe(4);
    // One round: each input once untimed, then five times timed.
    expect(clock.calls).toHaveLength(12);
  });

  it("keeps the fastest try of each input in a round", () => {
    // The small input's third run (its second timed one) is slowed tenfold: the fastest ignores it.
    const clock = scripted((size, run) => size * 10 * (size === 100 && run === 3 ? 10 : 1));
    expect(growth(clock.prepare, 100, { now: clock.now })).toBe(4);
  });

  it("returns the lowest round, and stops at the first under twice the factor", () => {
    // The large input's first round is five times slower (a busy spell on it): 20, then 4, so no
    // third round is timed.
    const clock = scripted((size, run) => size * 10 * (size === 400 && run <= 6 ? 5 : 1));
    expect(growth(clock.prepare, 100, { now: clock.now })).toBe(4);
    expect(clock.calls).toHaveLength(24);
  });

  it("measures every round while each reads high, and returns the lowest", () => {
    // 20, then 16, then 12: all at or over 8, so all three rounds, and 12 is returned.
    const slowdown = [5, 4, 3];
    const clock = scripted(
      (size, run) => size * 10 * (size === 400 ? (slowdown[Math.floor((run - 1) / 6)] ?? 1) : 1),
    );
    expect(growth(clock.prepare, 100, { now: clock.now })).toBe(12);
    expect(clock.calls).toHaveLength(36);
    // A round reading exactly twice the factor is not yet linear enough to stop at.
    const edge = scripted((size, run) => size * 10 * (size === 400 && run <= 6 ? 2 : 1));
    expect(growth(edge.prepare, 100, { now: edge.now })).toBe(4);
    expect(edge.calls).toHaveLength(24);
  });

  it("refuses an input too small to time", () => {
    const clock = scripted(() => 0.5);
    expect(() => growth(clock.prepare, 1, { now: clock.now })).toThrow("choose a larger size");
  });

  // The one test on the real clock: its detection power. 2 000 against 8 000 squared reads about
  // 16, and 12 or more under load; under coverage a large run takes about half a second.
  it("reads real quadratic work as over 10", () => {
    const work = (steps: number) => (): number => {
      let total = 0;
      for (let step = 0; step < steps; step += 1) total = (total + step) | 0;
      return total;
    };
    expect(growth((size) => work(size * size), 2_000)).toBeGreaterThan(10);
  }, 120_000);
});
