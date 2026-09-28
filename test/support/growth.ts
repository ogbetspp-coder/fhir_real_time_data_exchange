// How a cost grows with its input, not how long it takes: a wall-clock bound fails on a loaded
// machine or under coverage instrumentation whatever the code does, and passes a quadratic cost
// on a fast one. `prepare(size)` builds an input of that size outside the clock and returns the
// work to time; the work is timed at `size` and at `factor` times it, and the second time over
// the first is returned. A linear cost grows by about `factor`, a quadratic one by about its
// square, so a caller asserts a bound between the two.
//
// Each is run once untimed first (so neither is timed before the JIT has compiled it), then the
// two are timed in turn, `tries` times, and the fastest of each is kept: the fastest is the run
// least disturbed by anything else on the machine, and timing them in turn spreads a busy spell
// over both. Load still pulls the ratio towards 1 (it lengthens the short run more, in proportion),
// which can hide a quadratic cost but never fails a linear one.
export function growth(
  prepare: (size: number) => () => unknown,
  size: number,
  factor = 4,
  tries = 5,
): number {
  const small = prepare(size);
  const large = prepare(size * factor);
  small();
  large();
  let fastestSmall = Number.POSITIVE_INFINITY;
  let fastestLarge = Number.POSITIVE_INFINITY;
  for (let attempt = 0; attempt < tries; attempt += 1) {
    let started = performance.now();
    small();
    fastestSmall = Math.min(fastestSmall, performance.now() - started);
    started = performance.now();
    large();
    fastestLarge = Math.min(fastestLarge, performance.now() - started);
  }
  // A clock too coarse to time the small input says nothing about growth.
  if (fastestSmall < 1) {
    throw new Error(`The small input took ${fastestSmall.toFixed(3)} ms: choose a larger size`);
  }
  return fastestLarge / fastestSmall;
}
