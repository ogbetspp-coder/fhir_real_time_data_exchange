# Recorded change: the worker's recompute sized, 2026-10-07

_Follows the procedure in `docs/validation/README.md`, "Change control for shared, evidenced
libraries", for the certified Word importer (`src/certified-word/`, its own version lock)._

**What changed.** `src/certified-word/recompute.ts`: the recompute runs one at a time in the
worker process (`exclusive`, in the order asked; a failure does not stop the next), and its limit is
300 s (was 60 s). `infra/run.tf`: the worker container has 2 GiB (was 1 GiB). The certified Word
importer moves to 1.2.2 (`IMPORTER_VERSION`, `src/certified-word/importer.lock.json`) because its
locked directory holds the gate; what it makes is unchanged but for the version in the extractor
token, so its vectors move in their hashes only.

**Why.** Measured on an M2 laptop: the slowest EMA Word SmPC that builds (2,280 paragraphs, 90,422
runs, two tracked changes, accepted view) took 31.6 s and peaked at 796 MB resident. With 1 GiB and
four requests at once, one such read could get the instance killed, failing every request in flight
on it; with a 60 s limit on a slower vCPU, it could time out. Both fail closed; neither may decide
what a label can be imported.

**Impact.** The worker's cost per instance-second rises with its memory (Cloud Run bills memory by
the GiB-second); certified Word runs wait their turn on one instance. No contract, no output, no
other component changes. Tested: three concurrent recomputes run start, end, start, end, start, end
(`test/certified-word/recompute.test.ts`, which fails without `exclusive`).

**Approval (step 8).** Not obtained: author and releaser are the same identity.
