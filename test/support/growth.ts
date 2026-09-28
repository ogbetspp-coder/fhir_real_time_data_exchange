// How a cost grows with its input, not how long it takes: a wall-clock bound fails on a loaded
// machine or under coverage instrumentation whatever the code does, and passes a quadratic cost
// on a fast one. `prepare(size)` builds an input of that size outside the clock and returns the
// work to time; the work is timed at `size` and at `factor` times it, and the second time over
// the first is returned. A linear cost grows by about `factor`, a quadratic one by about its
// square, so a caller asserts a bound between the two (the cost tests: under 10, for 4 and 16).
//
// A round runs each input once untimed (so neither is timed before the JIT has compiled it), then
// times the two in turn, `tries` times, keeping the fastest of each: the fastest is the run least
// disturbed by anything else on the machine, and timing them in turn spreads a busy spell over
// both. Load can still skew a round either way: a busy spell over the large input's runs reads
// high, one over the small input's reads low. So up to `rounds` rounds are measured and the lowest
// ratio returned, stopping early at one under twice `factor` (already a linear reading): linear
// work has to read high in every round to fail, where one round did under load (10.02, B15's
// review). The lowest leans the other way: load on the small runs in every round could hide a
// quadratic cost. That is the chosen trade, a missed regression on a loaded machine rather than a
// failed gate on correct code; test/support/growth.test.ts shows quadratic work still reads ~16.
// `now` is the clock, performance.now unless a test scripts one to check the rounds exactly.
export function growth(
  prepare: (size: number) => () => unknown,
  size: number,
  {
    factor = 4,
    tries = 5,
    rounds = 3,
    now = () => performance.now(),
  }: { factor?: number; tries?: number; rounds?: number; now?: () => number } = {},
): number {
  const small = prepare(size);
  const large = prepare(size * factor);
  let lowest = Number.POSITIVE_INFINITY;
  for (let round = 0; round < rounds && lowest >= 2 * factor; round += 1) {
    small();
    large();
    let fastestSmall = Number.POSITIVE_INFINITY;
    let fastestLarge = Number.POSITIVE_INFINITY;
    for (let attempt = 0; attempt < tries; attempt += 1) {
      let started = now();
      small();
      fastestSmall = Math.min(fastestSmall, now() - started);
      started = now();
      large();
      fastestLarge = Math.min(fastestLarge, now() - started);
    }
    // A clock too coarse to time the small input says nothing about growth.
    if (fastestSmall < 1) {
      throw new Error(`The small input took ${fastestSmall.toFixed(3)} ms: choose a larger size`);
    }
    lowest = Math.min(lowest, fastestLarge / fastestSmall);
  }
  return lowest;
}
