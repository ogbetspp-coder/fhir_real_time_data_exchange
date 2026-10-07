# Recorded change: the Word drawing's entry point and image, 2026-10-07

_Follows the procedure in `docs/validation/README.md`, "Change control for shared, evidenced
libraries", for the certified Word importer (`src/certified-word/`, its own version lock)._

`recompute/1.2.0`, `word-drawing/1.2.0`, the certified Word importer 1.2.3
(`certified-word-import/1.2.3`), and the `word-drawing` target of `Dockerfile.renderer`. No contract
version changes. ADR 0006 P4, D3: `docs/design/certified-word-drawing.md`, build order steps 1 and 2
(PR 1).

**What changed.**

1. **`zone_a.recompute`** (`recompute/1.1.0` → `1.2.0`): `recompute_with_read` gives the result
   together with the body the label reader certified that it was made from, so the drawing holds
   each narrative to that very read and nothing reads the .docx twice; `written` is what the
   command writes for a result. The command's output for any input is unchanged but for the
   version string: each committed label's output, from `python -I -X utf8 -m zone_a.recompute` as
   the gate runs it, is the function's `written` bytes (`zone-a/tests/test_recompute.py`).
2. **`zone_a.drawing`** (`word-drawing/1.1.2` → `1.2.0`): `check`'s rules unchanged. New: `record`
   and `python -m zone_a.drawing LABEL.docx < REQUEST.json`. The request is the drawing request's
   canonical JSON exactly (`{docxSha256, recompute}`); the command hashes the .docx it opens and
   requires that hash, runs the recompute with its read, checks every narrative against the read,
   and only if every section agrees writes the record's fields as canonical JSON: its version
   (`word-drawing-record/1.0.0`), the request, the .docx's SHA-256 and length, the SHA-256 of the
   recompute's written bytes, this check's version and Chrome's, and each drawn section's key and
   `narrativeDivSha256` (as the importer hashes it). Otherwise status 1 and nothing on standard
   output; standard error gives a closed code (`refused: <code>`, `browser-failed`,
   `error: <type>`) or each section that differs and where, never the text.
3. **The image** (`Dockerfile.renderer`): the renderer's stage is named `renderer`, its instructions
   unchanged, and `npm run renderer:image` builds that target. A `word-drawing` target follows it:
   Python 3.14.7 installed by uv 0.12.17 (pinned by digest) in a stage built on the renderer, as the
   worker installs it; the launcher `src/render/image/word-drawing-chrome.sh`, through which
   zone_a.drawing runs the pinned chrome-headless-shell (`LABEL_CHROME`) with `--no-sandbox`, since
   Chrome's own sandbox could not start in the hardened container on CI's ubuntu-24.04 host, whose
   AppArmor restricts unprivileged user namespaces (run 37651573909: "No usable sandbox!"); and the
   Python environment (`PYTHONPATH` to the mounted checkout, `ZONE_A_ROOT`, safe path, no user site,
   no bytecode, UTF-8). Its build asserts Python 3.14.7, Unicode 16.0.0 and the pinned Chrome's
   version. zone_a and label_docx are not installed: they and the registry and mapping files are
   mounted from the checkout. `scripts/ci/renderer-pins.mjs` reads the renderer's pins from its
   stage and refuses any later stage that is neither built on it nor pinned by digest.
4. **The hardened run** (`scripts/render/word-drawing.mjs`): `docker run` with the renderer's
   hardening (`HARDENING`, now exported by `scripts/render/run.mjs`, whose `ISOLATION` is it plus
   the renderer's no-sandbox flag) and, read-only, only what the drawing and its checks read.
5. **The lock** (`src/render/word-drawing/lock.json`): `zone_a.drawing`'s version and each
   environment's image digest, null until an image is pushed and pinned (PR 3). A test holds the
   version to `DRAWING_VERSION`.
6. **CI**: a `Word drawing` job builds the target and runs `zone-a/scripts/word_drawing_check.py`
   in it, hardened: the isolation from inside; each committed certified Word label drawn twice in
   two processes, the same bytes and the fields its recompute gives, the refused one refused; every
   carried section of the five Word-made SmPCs and the QRD template agreeing; and Chrome's HTML and
   XML parsers making the same tree of every narrative drawn. The deploy does not wait for it (it
   ships nothing that reads a drawing), as for Renderer.
7. **The certified Word importer** (1.2.2 → 1.2.3): what it makes is unchanged; its golden vectors
   read the committed recompute results, which name `recompute/1.2.0`, so their hashes move, and its
   lock covers them.

**Why.** ADR 0006 decision 1's third leg: Zone B may trust a Word narrative only where a drawing it
did not make says Chrome draws it as the .docx was read. The owner decided D3 (a) on 2026-10-06 and
the drawing record's design on 2026-10-07. This is its first build step: the code and the image,
with no cloud resource.

**Impact assessment (step 0).**

- Zone B (`src/`): the certified Word importer's version, so its extractor token; nothing else.
- The worker image: rebuilt on deploy (`zone-a/src` changed); its recompute's output is unchanged
  but for the version string.
- Zone A: `zone_a.recompute`'s and `zone_a.drawing`'s versions; the drawing's verdicts are
  unchanged (`check` is). Every other component's version is unchanged.
- The renderer gate: its image is built by target; its stage's instructions, pins and checks are
  unchanged.
- Contracts: no version moves.
- Evidence: a certified Word submission made at importer 1.2.2 or `recompute/1.1.0` is refused by
  this build (another importer, a request naming versions it does not have). None is persisted:
  every certified Word run is still dry.
- Infrastructure: none. Nothing is pushed or deployed for the drawing.

**Steps 1–7.** 1: the versions above. 2: in `zone-a/`, `scripts/certified_word_fixtures.py` and
`scripts/lock_versions.py`; `npm run certified-word:vectors`, `npm run certified-word:lock`,
`npm run contracts:check` (no other drift). 3: every changed vector reviewed: the committed results
differ only in `recompute/1.2.0`; 13 result hashes and 4 submissions' submission, page text and
report hashes move; no outcome changes. 4: `zone-a/tests/test_drawing_record.py` (a request not
the bytes', malformed or not canonical; a refused label; a section drawn otherwise; a failing
Chrome; any other failure naming its type only; with Chrome, two identical runs; the parse check's
seeded differences), `zone-a/tests/test_recompute.py` (the command's bytes),
`test/render/word-drawing.test.ts`, `test/ci/renderer-pins.test.ts` (two more refusals). 5: no ADR
amended; ADR 0002's invariant 11 is restated with the gate (PR 4). 6: none. 7: no approval exists
of a submission made at importer 1.2.2.

**Blast radius.** A certified Word submission made at importer 1.2.2, or with a request naming
`recompute/1.1.0`, which the previous build accepted in a dry run and this one refuses. None it
accepts that the previous refused.

**Tests.** `zone-a/tests/test_drawing_record.py`, `zone-a/tests/test_recompute.py`,
`test/render/word-drawing.test.ts`, `test/render/run.test.ts`, `test/ci/renderer-pins.test.ts`,
`test/ci/check-all.test.ts`, `test/ci/workflow-runs.test.ts`, `test/certified-word/`; CI's Word
drawing job in the image.

**The independent review of #201**, each fixed with a test that fails without it where code is
involved:

1. Step 1 was overclaimed as done: the design note now says it is partly done and lists what
   remains for PR 2 (the corpus in the Linux image, with its time and memory; Cloud Build's
   machines; Chrome's sandbox on Cloud Build's hosts). The corpus harness is committed
   (`zone-a/scripts/word_drawing_corpus.py`, counts only), including the launcher it draws at 375 by
   812 and a ratio of 2 through, without changing the reader, and the counts were made again with
   it. The 130 against 131 SmPCs is stated as unexplained.
2. The drawing's version did not cover everything that decides the record's bytes: its lock now
   also covers `zone_a/canonical_json.py`, `zone_a/recompute.py`, `zone_a/qrd/check.py` (which
   the structurer reads under no version of its own) and `label_docx/epi.py` (the thresholds the
   browser check marks by), `word-drawing/1.2.0` re-locked, unreleased.
   `zone-a/tests/test_versions_lock.py` holds every module `python -m zone_a.drawing` imports to
   the drawing's own lock or to a version its request names.
3. The image's check could pass on less: it now requires at least the committed fixtures, a
   refused label's exact closed code, Chrome's raw answers equal to Google Chrome's recording, and
   reads the hardening back from inside (the checkout's mounts read-only, `/tmp` a writable tmpfs,
   memory and processes bounded) (`zone-a/tests/test_word_drawing_check.py`).
4. The uv image is held to the worker's by digest, not tag (`test/render/word-drawing.test.ts`).
5. A request nested too deep for the JSON parser or for canonical JSON is refused
   (`refused: request`), not a traceback (`zone-a/tests/test_drawing_record.py`).
6. `Dockerfile.renderer` no longer says both packages need the standard library only: what the
   drawing imports does, which `zone-a/tests/test_word_drawing_check.py` holds.
7. The sandbox's failure is the host's (its AppArmor restriction of user namespaces), as Chrome's
   message says, not the container's flags alone; a seccomp profile would not lift it there.

**Not verified here.** The Mac this was written on has no container runtime. The image was built
and run in CI only, on the committed fixtures, and the EMA corpus was drawn with the pinned shell's
macOS build instead (`docs/design/certified-word-drawing.md`, "Step 1"). Left for PR 2: the corpus
in the Linux image; Cloud Build's Intel and AMD machines; and e2-standard-2 timings.

**Approval (step 8).** Not obtained: the author and the releaser are the same identity.
