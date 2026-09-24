# Zone A skeleton

Delivered (was roadmap item 2). A Python project that consumes the published Zone B contracts,
proves that the canonical hashes reproduce in a second language, and re-implements the narrative
fidelity check with the golden vectors as its only oracle.

This is the first real test of the claim in ADR 0002 that the contract and the fidelity
specification are language-neutral: "A Zone A implementation in another language must generate
its models from `contracts/generated/` and prove fidelity-check equivalence against the golden
vectors." Nothing here structures a document yet. It establishes that the boundary can be
implemented from the published artefacts, and it records — in the last section of this file —
every place where it could not be.

Nothing under `src/`, `docs/fidelity-normalization.md`, or the ADRs was changed to make any of
this pass. Where the Python and the TypeScript disagreed, the TypeScript decided and the Python
moved. Zone B did gain test material: seven golden vectors in `test/fixtures/fidelity/cases.ts`,
the differential generator in `scripts/fidelity/differential.ts`, and the npm scripts and CI steps
that run them. None of that changes a byte of what the pipeline computes.

## What is proven

| Claim                                                        | Where                                         |
| ------------------------------------------------------------ | --------------------------------------------- |
| Canonical JSON hashes reproduce across languages             | `tests/test_contracts_parity.py`              |
| Canonical JSON is byte-identical where the defaults disagree | `tests/test_canonical_json_parity.py`         |
| The generated models neither drop nor invent a field         | `tests/test_contracts_parity.py` (round trip) |
| Strict objects reject, open objects accept and preserve      | `tests/test_contracts_strictness.py`          |
| The fidelity check reproduces every golden vector            | `tests/test_golden_vectors.py`                |
| The two implementations agree off the vectors as well        | `tests/test_differential.py`                  |
| The runtime's Unicode Character Database is the pinned one   | `tests/test_environment.py`                   |
| No test prints narrative and no file here quotes a vector    | `tests/test_no_narrative_leak.py`             |
| A Word body is read exactly, or refused with a reason        | `tests/test_docx_reader.py`                   |
| The QRD registry is what the pinned EMA files build          | `tests/test_qrd_registry.py`                  |
| An EMA ePI section is read exactly, or refused with a reason | `tests/test_epi_reader.py`                    |
| The conformance results are what the pinned ePIs give        | `tests/test_qrd_check.py`                     |

The second and sixth rows are new, and they exist because the first version of this port was
wrong in five places that 130 passing vectors could not see. See "What the vectors could not
see" below.

## Set-up

Python 3.14 is required and the range is closed (`>=3.14,<3.15`): the Unicode Character
Database ships with the interpreter, NFC output depends on it, and ADR 0003 pins Unicode 16.0
because the Zone B runtime image (`node:22.22.0`, ICU 77.1) is on that version. A different
minor Python is a different UCD and therefore a change to `NORMALIZATION_VERSION` even though no
line of code changed. `tests/test_environment.py` asserts it rather than trusting it.

**`uv` lives outside the environment it manages, in its own virtualenv.** An earlier version of
this file said to `pip install uv` into `.venv` and then run `.venv/bin/uv sync --frozen`, and
that sequence destroys itself: `--frozen` makes `.venv` match `uv.lock` exactly, `uv` is not in
`uv.lock` (it is a tool, not a dependency), so the first `sync` prunes the binary that is running
it. The second command in the old sequence worked and every command after it was a
`no such file or directory`. A tool that can prune the environment it is installed into belongs
in a second one:

```bash
cd zone-a
python3.14 -m venv .uv-bootstrap
.uv-bootstrap/bin/pip install "uv==0.12.17"
.uv-bootstrap/bin/uv sync --frozen
```

`.uv-bootstrap` is in `.gitignore`, in `.prettierignore` and in the ESLint ignore list, and
`tests/test_no_narrative_leak.py` skips every dot-directory so it does not walk into either
virtualenv. `pipx install uv==0.12.17` works just as well; the point is only that `uv` is not
installed into `.venv`.

`--frozen` installs exactly what `uv.lock` records and fails rather than re-resolving. Every
dependency, direct and transitive, is pinned; there is no floating version anywhere in the
project.

Then run the same five commands CI runs:

```bash
cd zone-a
.uv-bootstrap/bin/uv run --frozen ruff check .
.uv-bootstrap/bin/uv run --frozen ruff format --check .
.uv-bootstrap/bin/uv run --frozen mypy --strict
.uv-bootstrap/bin/uv run --frozen python scripts/generate_models.py --check
.uv-bootstrap/bin/uv run --frozen pytest
```

Every line above was run as written. One local caveat that is a toolchain defect rather than a
project one, recorded because it cost an hour: on macOS 26 with Homebrew's `python@3.14`,
`python3 -m venv` fails inside `ensurepip`, because the interpreter's `pyexpat` is linked against
the system `libexpat` and cannot load, `plistlib` therefore cannot import, `platform.mac_ver()`
returns an empty string, and the `truststore` module vendored into pip parses that empty string
as an integer. Prefixing the two `venv`/`pip` lines with
`DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib` (after `brew install expat`) is what made them
run here. Nothing after the bootstrap needs it: `uv` is a static binary and the project's own
code does not import `plistlib`. CI is unaffected — it uses `actions/setup-python` and
`astral-sh/setup-uv`, and neither goes through `ensurepip`.

The test suite reads three things from the repository root: the golden vectors
(`test/fixtures/fidelity/vectors.json`), the exported contract fixtures
(`test/fixtures/contracts/`), and the differential smoke corpus
(`tests/fixtures/differential-smoke.jsonl`). All three are generated and gate-checked on the Node
side, so run `npm run check` from the repository root first if any is missing.

## Regenerating the models

`src/zone_a/contracts/` is generated from `contracts/generated/*.schema.json`, one module per
contract, and is committed:

```bash
cd zone-a
.uv-bootstrap/bin/uv run --frozen python scripts/generate_models.py         # regenerate
.uv-bootstrap/bin/uv run --frozen python scripts/generate_models.py --check # fail if it changes
```

`--check` mirrors `scripts/ci/check-generated.mjs`: it regenerates into a temporary directory
and compares the bytes, so a contract change that was not regenerated and committed cannot pass
the gate. It compares content rather than modification times because this generator rewrites
every module unconditionally.

Two things were needed to make that reproducible, and both are worth knowing:

- **`--formatters builtin` plus an explicit `ruff format --config`.** With
  `--formatters ruff-format`, datamodel-code-generator lets ruff discover its configuration from
  the output directory, so generating into the project produced 100-column output and generating
  into a temporary directory produced 88-column output — the `--check` run disagreed with the
  committed files for no reason but where it ran. The generator now formats with an explicit
  config path.
- **`skip-magic-trailing-comma = true`** in `[tool.ruff.format]`. datamodel-code-generator emits
  a trailing comma only when its own width estimate wraps a call, and ruff's formatter preserves
  one where it finds it, so the same input could produce two stable-but-different files.

**Strictness is not configured, and it is not assumed.** datamodel-code-generator maps
`additionalProperties: false` to `ConfigDict(extra="forbid")` and an open object to
`ConfigDict(extra="allow")` by itself, so no generator option or shared base class was needed.
`tests/test_contracts_strictness.py` proves it per model instead of trusting it: it counts the
closed and open object schemas in each JSON Schema, counts the models that actually reject and
actually accept an unknown field, and requires the counts to match. It also names the three
models that are allowed to be open — `CanonicalBundle`, `EntryItem`, `Resource`, the FHIR objects
that `src/contracts/canonical-bundle.ts` models with `z.looseObject` — and proves that an open model
_preserves_ the unknown field rather than merely tolerating it, which is what the Bundle hash
depends on.

One generator option was needed, for a different reason. See "Specification ambiguities found",
item 1.

## What the parity test proves

`tests/test_contracts_parity.py` recomputes, in Python, four digests that TypeScript wrote into
the fixtures. The Python canonical JSON encoder is an independent port of `src/lib/hash.ts`; if
the two implementations disagree about key order, string escaping, or number formatting by one
byte, every digest differs and these tests fail.

1. `sha256(canonical_json(submission.bundle))` equals `submission.bundleSha256`.
2. `sha256(canonical_json({schemaVersion, bundle, provenance}))` equals
   `submission.approval.approvedContentSha256` — the hash a human approval is bound to
   (`approvedContent()` in `src/contracts/canonical-submission.ts`).
3. `sha256(canonical_json(source-document-text.json))` equals
   `submission.provenance.sourceDocument.extractedText.sha256`.
4. The fidelity report hashed without its own `reportHash` field reproduces `reportHash`, the
   way `verifyReportHash()` in `src/fidelity/verify.ts` does it.

Then every fixture is parsed by its pydantic model and dumped again
(`by_alias`, `exclude_none`, `mode="json"`), and the canonical JSON of the result must equal the
canonical JSON of the file. That is the part that catches a model which quietly drops an
unmodelled FHIR field: such a model would still parse, still look right, and produce a different
Bundle hash. The submission's two hashes are then recomputed from the _dumped_ model, so the
claim is not merely "the bytes survived" but "the approval still verifies".

One limit of the round trip is stated rather than hidden: `exclude_none=True` drops an _extra_
field whose value is JSON `null`, so a Bundle carrying an explicit null at an unmodelled position
would dump to different canonical JSON and a different Bundle hash. No fixture carries one, and
`test_no_fixture_carries_a_json_null` fails the moment that stops being true, so the claim above
is bounded by a test rather than by a hope.

Two choices inside the encoder are worth stating, because both are places where Python's
defaults are wrong:

- **Key order is UTF-16, not code point.** `sorted()` orders strings by code point, which puts
  every astral character _after_ U+E000–U+FFFF; RFC 8785 and `src/lib/hash.ts` put it before,
  because the key is compared as UTF-16 code units. Keys are therefore sorted on
  `key.encode("utf-16-be")`. There is a test for exactly this case.
- **Floats are refused, not formatted.** The specification says "`JSON.stringify` number and
  string formatting", which is a normative reference to a JavaScript function rather than a
  language-neutral rule. Rather than guess where the two languages' shortest-round-trip
  formatting diverges, `canonical_json` raises on a float, and a test asserts that no number
  anywhere in the four fixtures is a non-integer. Integer formatting is identical in both
  languages, so the hashes that do match, match for a stated reason.

## Vector results

All 600 golden vectors of `fidelity-norm/3.1.0` pass, byte for byte, including every error case:

| Module                         | Result          |
| ------------------------------ | --------------- |
| `zone_a/fidelity/normalize.py` | normalize 71/71 |
| `zone_a/fidelity/xhtml.py`     | xhtml 349/349   |
| `zone_a/fidelity/verify.py`    | verify 180/180  |

Under `fidelity-norm/1.1.1` there were 137 (25, 60 and 52). Seven of those were added by the
first round of this port: six XHTML cases and one verify case, each of them pinning a divergence
the existing 130 could not see. The 274 added by `fidelity-norm/2.0.0` pin its rules and both
sides of every boundary (`docs/validation/changes/2026-09-23-fidelity-norm-2-0-0.md`). The 190 added by
`fidelity-norm/3.0.0` (10 of 2.0.0 replaced or renamed, so 180 more) pin numbered lists, table
grids, pictures, the reserved and invisible code points, the nesting bounds, combining marks
across markup, the refusal of underlines and links, and the thin spaces and other gaps (`docs/validation/changes/2026-09-23-fidelity-norm-3-0-0.md`). The 9 added by
`fidelity-norm/3.1.0` pin ½ and ∞ kept inside `sub` and still refused inside `sup`
(`docs/validation/changes/2026-09-24-fidelity-norm-3-1-0.md`). They are defined in
`test/fixtures/fidelity/cases.ts` on the Zone B side, where the TypeScript defines the expected
behaviour, and regenerated with `npm run vectors:generate`.

`xhtml.py` and `verify.py` were the stretch goal; both are complete, so `KNOWN_FAILURES` in
`tests/test_golden_vectors.py` is empty for all three families. Any vector that were to be
listed there is marked `xfail(strict=True)` by name, so it fails loudly the moment it starts
passing and the counts above cannot quietly drift.

Six Python-specific hazards, and how each is handled. The first three were known when the port
was written; the last three were found afterwards, by a review that stopped asking whether the
vectors passed and started asking what the vectors did not cover.

- **Offsets are code points.** Python's `str` indexes code points natively, so no conversion is
  needed — but where the TypeScript reads past the end of its code point array it gets
  `undefined`, and Python would wrap around to the end of the string for a negative index. Every
  such read goes through `_at()` in `verify.py`.
- **Python's whitespace is not the specification's.** `str.split()`, `str.strip()` and
  `str.isspace()` all act on a wider set than the closed list in section 3 — it includes
  U+001C–U+001F, which section 2 forbids outright — so none of them is used for the whitespace
  step. Membership of the explicit list is tested instead.
- **`\s` differs between the two regex dialects.** JavaScript's `\s` includes U+FEFF and excludes
  U+001C–U+001F and U+0085; Python's is the reverse. Every `\s` in the ported scanner regexes
  was spelled out as an explicit class built from code points, so `xhtml.py` accepted and
  rejected exactly what `xhtml.ts` did. Since `fidelity-norm/2.0.0` neither side uses `\s` in a
  tag at all: tag whitespace is `[\t\n\r ]`, because an HTML parser reads any other code point
  there as part of the tag name (the second review's C1).
- **`\d` differs too, and in the direction that opens a channel.** JavaScript's `\d` is ASCII;
  Python's matches every Unicode decimal digit, so a numeric character reference written with
  U+FF10–U+FF19 FULLWIDTH DIGIT (or Arabic-Indic digits, or the mathematical digits) decoded here
  and was a stray `&` there. The classes are now written out as `[0-9]` and `[0-9A-Fa-f]`.
  `re.ASCII` was deliberately **not** used as a blanket flag: it would also narrow a class
  someone adds later, silently, in the other direction.
- **`$` is not the end of the string and `re.match` is not anchored.** Python's `$` also matches
  immediately before a trailing newline, and `re.match` only anchors the start, so an attribute
  grammar applied with `pattern.match(value)` accepted a value with a trailing U+000A — and, for
  the multi-token grammars, arbitrary text after that newline. Attribute values are never
  compared against the source, so that is precisely the channel section 5 exists to close. Every
  whole-value grammar now uses `fullmatch` and carries no anchors at all.
- **`isinstance(x, int)` is not `Number.isInteger(x)`.** JSON and JavaScript each have one number
  type, so `1` and `1.0` are the same value on the Zone B side; `json.loads` gives Python an
  `int` and a `float`. JSON Schema defines `{"type": "integer"}` as a number with a zero
  fractional part, so a source page written `"page": 1.0` satisfies the contract, verified in
  Zone B, and was refused here. Offsets now go through `_as_integer()` in `verify.py`, and
  `canonical_json` writes an integral float as the integer `JSON.stringify` writes.

`fidelity-norm/2.0.0` added three more, all about strings rather than regexes:

- **A lone surrogate is an ordinary code point to Python.** `json.loads` turns an escaped
  unpaired surrogate into a one-code-point `str`, and `chr(0xD835)` does the same for a
  character reference. Section 2 now applies to the whole `div` before the scan and to each
  decoded reference on its own, so both are checked explicitly (`find_forbidden_character`,
  `is_forbidden`); an escaped valid pair is one code point on both sides, which
  `tests/test_golden_vectors.py` pins because `JSON.stringify` never writes one.
- **`\p{N}` has no `re` equivalent.** The `unmappable-script` rule rejects any general category N
  code point without a script form inside `sup` or `sub`; it is read from
  `unicodedata.category`, whose Unicode version is pinned with the interpreter.
- **The TypeScript scanner walks UTF-16 units; this one walks code points.** A supplementary
  digit inside `sup` must be one code point on both sides, so `xhtml.ts` steps over a pair with
  `codePointAt`; here it is native.

## What the vectors could not see

An adversarial review made the point that 130 out of 130 passing proves agreement only where the
TypeScript author already looked. Five divergences were found off the vectors. All five are
fixed on the Python side, because the TypeScript is the specification here; each is now pinned by
a test that fails if it comes back.

| #   | Divergence                                                                                           | Pinned by                                                  |
| --- | ---------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| 1   | Unicode-aware `\d` decoded numeric character references written with non-ASCII digits                | 2 XHTML vectors + the `entity-non-ascii-digits` class      |
| 2   | `re.match` against a `$`-anchored attribute grammar accepted a trailing newline and text after it    | 3 XHTML vectors + the `forbidden-attribute-value` class    |
| 3   | An unpaired surrogate raised `UnicodeEncodeError` instead of hashing as `JSON.stringify` writes it   | 1 verify vector + `tests/test_canonical_json_parity.py`    |
| 4   | `isinstance(x, int)` refused the integral float that the contract and `Number.isInteger` both allow  | the `integral-float-offsets` class in the corpus           |
| 5   | `uv sync --frozen` pruned `uv` from the environment the set-up instructions had just installed it in | the set-up section above, every line of which has been run |

Divergence 4 could not be pinned by a golden vector: the vectors are written by `JSON.stringify`,
which writes `1` for `1.0`, so a JSON file cannot carry an integral float at all. It is carried in
the differential corpus instead, as a per-case `floatOffsets` flag that the Python reader acts on.

Two further differences were found while fixing those, both in the same family as #3 and #4 and
both fixed the same way:

- `sha256_utf8` used `errors="surrogatepass"`, which is WTF-8 (`ed a0 80`). Node's
  `Buffer.from(text, "utf8")` substitutes U+FFFD (`ef bf bd`) per unpaired **code unit**. The two
  digests were different. Unreachable from the verifier today — a page or narrative carrying a
  surrogate is rejected by section 2 before anything hashes it — but it is a ported primitive and
  a primitive that is right for a different language is not right.
- `canonical_json` refused every float, including an integral one. That is reachable: the report's
  `extractedTextSha256` is the canonical JSON hash of the whole source object, so a source whose
  page numbers arrived as `1.0` could not be hashed here at all.

## The differential harness

`scripts/fidelity/differential.ts` generates a seeded corpus of synthetic inputs together with
what the TypeScript produced for each one, and `tests/test_differential.py` runs the Python
implementation over the same corpus and compares. That turns "language-neutral" from a claim into
a property with a test attached.

```bash
# from the repository root
npx tsx scripts/fidelity/differential.ts --seed 20260920 --count 2000 --out /tmp/differential.jsonl
cd zone-a && DIFFERENTIAL_CORPUS=/tmp/differential.jsonl .uv-bootstrap/bin/uv run --frozen pytest -q tests/test_differential.py
```

Three families, a third of the corpus each:

- **normalize** — strings assembled from a weighted alphabet that covers every code point in the
  specification's closed lists (the four invisibles, the six ligatures, the eleven bullet glyphs,
  the whitespace class U+0009, U+000A, U+000D, U+0020, U+00A0, U+1680, U+2000–U+200A, U+2028,
  U+2029, U+202F, U+205F and U+3000), plus the near misses that a wrong implementation would
  treat as members (U+FB05, U+180E, U+2024, and the accepted neighbours of every range
  `fidelity-norm/2.0.0` rejects), combining marks after invisibles, NFC singletons, Hangul jamo,
  U+00AD before LF, before CRLF and before a space, the section 2 forbidden characters including
  unpaired surrogates and, since 2.0.0, U+000B, U+000C, the C1 controls and the bidirectional
  controls, and ordinary ASCII words.
- **xhtml** — documents assembled from the allowed grammar (every block and inline element,
  nested tables and lists, self-closing `br` and `hr`, table sections in rendering order with
  whitespace between parts, tables of one row width including empty ones, `sup` and `sub` holding
  ASCII digits and signs, dashes, letters, script digits, references and the numbers that have
  no script form, entities in named, decimal and hexadecimal form, supplementary characters, every
  allowed attribute), with a single deliberate violation injected into two cases in five. The
  violation classes reach every error code the scanner can raise, including self-closing tags
  and `<br>` start tags, a start tag that is both a void and a parent violation, content and
  references directly inside table parts, uneven rows, surrogates split by markup and by
  reference, section 2 characters raw, referenced, in a tag, in an attribute and outside the
  root, and U+00AD before a line break in each form (raw, reference, CR LF, `br`, block).
- **verify** — documents of one to three pages with running headers and footers and one to four
  sections, with span layouts drawn from the arrangements the specification distinguishes: exact,
  shifted by one in each direction, cut mid-word, two spans on one page, crossing a page
  boundary, starting at the first sentence of a later page, ending at the end of a page, ending
  just after U+00AD and whitespace, overlapping another section, hash-mismatched, outside the
  declared body, out of order, and pointing at a page that does not exist. Pages may begin with a
  blank line, end with a word continued on the next page (U+00AD before the final LF), or lack
  their final LF before the footer or at the very end of the page; a document may leave a page
  out, number its pages out of order, or start at 2. The comparison key is the full `reportHash`,
  plus each section's status and reason and the report's issue list.

Every case records the **classes** it was assembled from rather than its text, which is what makes
it possible to report a divergence — here, or in this file — without ever quoting an input. A
mismatch prints the family, the index, the seed, the class list and two digests, and nothing else.
`tests/test_differential.py` also asserts that the corpus reaches the classes the review named, so
a future edit to the generator cannot quietly shrink the alphabet while the run goes on passing.

**Results.** 2000 cases at each of three seeds, 6000 in total, **zero divergences**:

| Seed      | Cases | Divergences found | Fixed | Recorded as ambiguities |
| --------- | ----- | ----------------- | ----- | ----------------------- |
| 20260920  | 2000  | 0                 | 0     | 0                       |
| 1         | 2000  | 0                 | 0     | 0                       |
| 987654321 | 2000  | 0                 | 0     | 0                       |

A harness that has never failed proves nothing, so each of the five fixes was reverted in turn and
the seed-20260920 corpus re-run: the ASCII digit class → 6 failures, `fullmatch` → 1, the
surrogate escape → 9, integral floats in `verify.py` → 48, and the JavaScript rendering of a
number inside an issue string → 5. Each revert was undone and the run went back to 2002 passed.

**`fidelity-norm/2.0.0`.** Rerun on the extended generator, 2000 cases at each of seeds
20260920, 1 and 2: **zero divergences**, and the class-coverage test passes on each. The
generator was extended twice: for the rules of the design note and its amendments, and again
for the second review's findings folded into 2.0.0 (C1 tag whitespace, C2 punctuation at span
edges, C3 bullets, L1 script digits of the other script and the added fold forms, L2
non-integer span fields). The reviewer's own probes (121 cases) and XHTML fuzz (60,000
narratives) agree too, 0 divergent.

Twenty-five rules were then broken in the Python port one at a time and the three corpora
re-run (divergences at seeds 20260920 / 1 / 2). The second review's: tag whitespace back to
`\s` → 34 / 30 / 35; end edge back to word characters → 25 / 39 / 29; start edge back to word
characters → 15 / 17 / 22; bullets replaced away from a line start → 215 / 191 / 213; bullets
replaced without following whitespace → 54 / 60 / 56; U+2219 back in the bullet list →
13 / 12 / 10; the other script's digits kept → 7 / 11 / 10; U+2796 not folded → 3 / 4 / 4;
non-integer span fields not refused → 11 / 12 / 28; a boolean written as Python's `True` in an
issue → 19 / 14 / 12. The first round's: a body at the end of a page without its line feed
accepted → 44 / 45 / 52; U+000B and U+000C allowed → 10 / 6 / 5; U+2066–U+2069 allowed →
10 / 12 / 11; the start rule stopping at the page start → 22 / 10 / 14; the 1..N page check
dropped → 76 / 59 / 60; U+2013 not folded inside `sup` → 0 / 3 / 2; numbers without a script
form kept → 12 / 8 / 9; the section 2 check of the whole `div` skipped → 14 / 13 / 15; any
element allowed to self-close → 10 / 12 / 10; `void-element` decided after the parent check →
2 / 4 / 7; U+00AD before CR LF accepted → 15 / 16 / 11; `table-shape` skipped → 24 / 12 / 12; a
whitespace reference allowed in a table part → 5 / 0 / 5; `lang` allowed below the root →
0 / 1 / 1; the end rule not reading through trailing whitespace → 17 / 14 / 14. Every review
break diverged on all three seeds and every break on at least two; each was restored and the
runs went back to 2002 passed. Two breaks (the order of `void-element` and the end rule's
trailing whitespace) were at first invisible to the generator; the `void-element-and-parent`
violation class and the `span-ends-after-soft-hyphen-space` and `span-page-end` layouts were
added so that they are not.

Round 2 of the second review (text line breaks as spaces, grouped numbers at section edges,
bullets in table cells and the line-start rule that keeps normalisation idempotent, the other
kind's script letters and symbols in `sup`/`sub`) extended the generator again; zero
divergences at the same three seeds. Breaking each of its rules in this port diverged on all
three seeds (20260920 / 1 / 2): text line breaks kept → 183 / 192 / 187; only raw ones turned
into spaces → 48 / 43 / 40; end digit-group rule dropped → 1 / 1 / 1; start digit-group rule
dropped → 3 / 2 / 4; joiners counted as edge whitespace → 5 / 4 / 8; the U+0009-line bullet
rule dropped → 111 / 129 / 122; cells emitted with U+000A → 25 / 29 / 30; the start of a text
counted as a line start → 11 / 2 / 6; page slices read from the span start → 9 / 13 / 12;
symbols kept in `sup`/`sub` → 11 / 7 / 12; the other kind's letters kept → 1 / 3 / 2. The
reviewer's probes (44 and 121 cases) and fuzz (two sets of 60,000 narratives) agree, 0
divergent.

Round 3 (the digit-group rule reading past whitespace at a span edge, and a page line's tab
status decided on the whole line) extended the generator with numbers grouped by two
whitespace code points, edges inside them, and rows cut before their tab. Zero divergences at
the same seeds. Breaks (20260920 / 1 / 2): end inner code point read raw → 3 / 5 / 4; start
inner code point read raw → 1 / 6 / 4; whole-line tab status dropped → 17 / 11 / 9; the round 2
end and start digit-group rules dropped → 6 / 6 / 5 and 1 / 7 / 4 (they were 1 / 1 / 1 and
3 / 2 / 4 before these shapes were generated). The round's probes (37 cases) agree, 0
divergent.

The corpus is generated in CI rather than committed, because a committed corpus proves agreement
with a past revision of the TypeScript rather than with the current one. A tiny 18-case smoke
corpus is committed at `tests/fixtures/differential-smoke.jsonl` so `pytest` runs offline and
without Node; it is produced by `npm run differential:smoke` and is inside the `contracts:check`
guard, so it cannot drift away from the generator.

## Continuous integration

`.github/workflows/ci.yml` has a `zone-a` job alongside the Node `check` job: pinned Python 3.14,
pinned `uv`, `uv sync --frozen`, then lint, format check, `mypy --strict`, the model-regeneration
check, and the tests. It now also sets up Node at the same pinned version as the `check` job and
runs `npm ci`, because the differential corpus is produced by the TypeScript implementation: the
job generates 2000 cases at seed 20260920 and points `DIFFERENTIAL_CORPUS` at them before
`pytest`. The seed and count are pinned so a CI failure is reproducible locally from the two
numbers in the log.

Each step was verified to fail closed by breaking it locally and confirming a non-zero exit: one
ligature mapping changed (`pytest` → 6 failures, exit 1), one line appended to a generated model
(`generate_models.py --check` → exit 1), one wrong return type (`mypy --strict` → exit 1), one
unused import (`ruff check` → exit 1, `ruff format --check` → exit 1). Each break was then
reverted and the suite re-run green. The differential step was verified the same way, by the five
reverts recorded above.

## Narrative safety

The vectors and fixtures are synthetic by construction, which is exactly why the habit is worth
enforcing while it costs nothing. `tests/test_no_narrative_leak.py` parses every test module and
fails if it calls `print`, writes to `sys.stdout`/`sys.stderr`, or imports `logging`; and it
fails if any Python or Markdown file in this directory contains a run of 24 or more characters
taken from a vector's normalisation input, XHTML input, page text, or section markup. Vector
comparisons are made on the values themselves, as the TypeScript tests do, but a failing
comparison reports only the vector name, the canonical lengths, and two digests.

## Specification ambiguities found

The port did find gaps. Every item below is a place where `docs/fidelity-normalization.md`, the
ADRs, or the generated schemas did not determine the answer and `src/fidelity/*.ts` or
`src/contracts/*.ts` had to be read, or where the TypeScript does something the specification
does not say. None of them was resolved by changing Zone B.

**1. The contracts pin `uuid` and `date-time` with both a `format` and a `pattern`, which no
pydantic model can express.** `contracts/generated/` emits
`{"type":"string","format":"uuid","pattern":"..."}`. datamodel-code-generator turns that into
`Annotated[UUID, Field(pattern=...)]`, and pydantic cannot apply a string pattern to a `uuid` (or
`datetime`) schema: every such field raises `TypeError` on the first validation, so a generated
model of `CanonicalSubmission` is unusable out of the box. Resolved inside Zone A with
`--type-mappings string+uuid=string string+date-time=string`, which keeps the regex — the
stricter of the two constraints, and the one the contract actually enforces — and keeps the value
byte-identical through a round trip. Worth deciding on the Zone B side whether `format` should be
emitted at all when a pattern is present.

**2. The XHTML scanner's grammar is not in the specification.** Section 5 lists the allowed
elements, attributes, and entities, but not the tokeniser: whether whitespace may precede `>`,
whether `</div >` is legal, what an attribute value may contain, or which error code each
violation produces. The error codes themselves — `stray-lt`, `stray-amp`, `malformed-tag`,
`misnested-tag`, `unbalanced-tag`, `multiple-roots`, `text-outside-root`, `forbidden-attribute`,
`unknown-entity`, `unknown-element`, `uppercase-element`, `root-not-div`, `comment`, `cdata`,
`doctype`, `processing-instruction`, `table-structure`, `table-section-order`,
`soft-hyphen-at-boundary` — appear only in `src/fidelity/xhtml.ts` and in the vectors.
`xhtml.py` could not have been written from the specification alone; the regexes were ported
from `xhtml.ts`.

**3. Two specific classifications inside that scanner are arbitrary from the specification's
point of view.** A `<` that does not begin a well-formed tag is `malformed-tag` when the next
character is an ASCII letter and `stray-lt` otherwise. A root `div` that is missing its `xmlns`
is `root-not-div`, not `forbidden-attribute`. Both were read from `xhtml.ts`.

**4. A self-closing block element emits two line breaks, not one.** Section 5 says block
elements emit U+000A before their start tag and after their end tag, and that self-closing
syntax is accepted; it does not say that `<hr/>` therefore emits both. It does in `xhtml.ts`,
and the `self-closing-block` vector depends on it.

**5. `empty-narrative` is produced by the verifier, not by the scanner.** Section 5 ends "The
extracted text is then normalised (section 3). An empty result rejects (`empty-narrative`)",
which reads as part of the scanner's contract. In the implementation `xhtmlToText` returns the
text and `normalizeNarrative` in `verify.ts` decides emptiness after normalisation — which is
why no XHTML vector ever expects `empty-narrative`. Read from `verify.ts`.

**6. What `reportHash` covers is not specified.** Section 1 says every hash of a JSON value is
the SHA-256 of its canonical JSON, but not that the report's own hash is taken over the report
with the `reportHash` field removed. Read from `verifyReportHash` in `verify.ts`.

**7. The report's `issues` strings are free text and are inside the hash.** `Page 2:
body-boundary`, `Orphan provenance <key>`, `No narrative sections to verify`, `Duplicate page
number <n>`, `Invalid body range on page <n>`, `Ambiguous source section <key>` — their exact
wording is load-bearing for `reportHash` and appears nowhere in the specification. A
re-implementation must copy them character for character; these were taken from `verify.ts`.

**8. Most span-resolution reason codes are not in the specification.** Section 6 names
`word-cut`, and section 1 names `body-boundary` and `excluded-text`. `page-not-found`,
`page-malformed`, `outside-body`, `hash-mismatch`, `span-order`, `non-contiguous` and `overlap`
are only in `verify.ts`, as is `forbidden-character`, the code the normalisation vectors record
for a section 2 rejection.

**9. Cross-section overlap is resolved after verification, and silently changes coverage.** ADR
0003 says spans "must not overlap another section's spans". It does not say that the affected
sections are first verified and then rewritten to `invalid-provenance` with reason `overlap`,
nor that their spans are then excluded from the coverage figures. Both are in `verify.ts` and
both change `reportHash`.

**10. A non-empty `issues` list fails an otherwise fully verified report.** Section 6 says only
that a report with zero narrative sections is `failed`. The rule in `verify.ts` is
`verified === sections.length && issues.length === 0`, so a single orphan provenance entry fails
a report in which every section verified. That is the right behaviour — it is fail-closed — but
it is not written down.

**11. The shape hashed into `narrativeBindingSha256` is not specified.** ADR 0003 says the
binding is "computed from the Bundle's normalised narratives alone". The actual value is the
canonical JSON hash of an array of `{sourceKey, normalizedTextSha256}` objects in section order,
with `null` for a section whose narrative cannot be normalised. Read from
`computeNarrativeBinding` in `verify.ts`.

**12. `DiffHint`'s word counts have no definition in the specification.** ADR 0003 lists "word
counts" among the fields a mismatch may report. The definition — split the already-normalised
text on single U+0020 — is only in `countWords` in `normalize.ts`, and it is inside
`reportHash` whenever a section mismatches.

**13. `exclude_none` and open objects interact badly.** Not a specification gap but a
consequence of one: because the contract's open objects are the place where unmodelled content
lives, and pydantic's `exclude_none` removes extras whose value is `null`, the specification's
silence about whether `null` may appear inside a Bundle becomes a hash question. Bounded by a
test here; worth a sentence in the contract.

**14. The `fixtures:export` script name was already taken.** The task asked for an npm script
called `fixtures:export`; `package.json` already binds that name to
`scripts/fhir/export-validation-set.ts`, a hand-run exporter that takes two command-line
arguments. Rather than silently repoint an existing entry point, the new exporter is
`npm run contracts:fixtures`, and it is wired into the guarded paths of `contracts:check` so
drift in `test/fixtures/contracts/` fails the gate. (Since removed: `scripts/ci/emit-validation-set.ts`
superseded that hand-run exporter, and `fixtures:export` no longer exists.)

The three below were found by this round. Each was resolved by making the Python match the
TypeScript, which is the rule here; each is recorded because the resolution came from reading an
implementation rather than from reading the specification, and a third implementation would have
to read the same file.

While they were being written, `docs/fidelity-normalization.md` was amended to
`fidelity-norm/1.1.1`, and that amendment already closes the canonical-JSON half of item 15 and
all of item 16 — it now states the `\udXXX` escape and that a hashed number such as `1.0` is the
integer 1. The entries are kept as the record of what a port had to discover for itself, and
because one part of item 15 is still open: `sha256Utf8` is not `JSON.stringify`, and the
replacement Node's UTF-8 encoder performs on an unpaired surrogate is not written down anywhere.
This port is on `fidelity-norm/3.1.0`, in step with `NORMALIZATION_VERSION` in
`src/fidelity/normalize.ts`; whenever that constant moves, the Python constant, the vectors, and
every recorded hash move with it. (It moved from `fidelity-norm/1.1.1` to 2.0.0, and then to 3.0.0,
on 2026-09-23, and to 3.1.0 on 2026-09-24, each time in the same change as the TypeScript and by the same author; that is why the seeded differential run,
not the vectors, is the evidence that the two agree.)

**15. "`JSON.stringify` string formatting" is a normative reference to a JavaScript function,
and it decides two things the specification does not mention.** Section 1 fixes canonical JSON as
"object keys sorted by UTF-16 code unit order (RFC 8785), no insignificant whitespace,
`JSON.stringify` number and string formatting". That last clause silently carries the
well-formed-stringify behaviour of ES2019: an unpaired surrogate is written as a `\udXXX` escape
rather than being an error or being replaced. It also does not cover `sha256Utf8`, which is not
`JSON.stringify` at all: there, Node's UTF-8 encoder substitutes U+FFFD for each unpaired
surrogate code unit, so a re-implementation that raises, or that writes WTF-8, produces a digest
Zone B never produces. Both behaviours were read from Node, and both are now pinned by
`tests/test_canonical_json_parity.py`. Worth stating in section 1 in language-neutral terms —
the escape and the replacement, not the function name.

**16. Whether a JSON `1.0` is an acceptable offset is decided by three different documents that
do not reference each other.** `contracts/generated/` says `{"type": "integer"}`; JSON Schema
defines that as a number with a zero fractional part, so `1.0` validates. `verify.ts` uses
`Number.isInteger`, which accepts it. `docs/fidelity-normalization.md` says offsets are "counted
in Unicode code points" and says nothing about their JSON form. In a language with one number
type the question never arises; in any language without one it is the first thing a port gets
wrong. The specification could close it in one clause — either "offsets are integers and an
integral non-integer JSON number is not one", or the converse.

**17. `Number.MAX_SAFE_INTEGER` is a bound on the contract, not on the hash.** Every numeric
field in `contracts/generated/` is capped at 9007199254740991, but canonical JSON is specified
over arbitrary JSON values, and above that bound JavaScript and Python disagree about the digits
a number is written with (JavaScript switches to exponential notation at 1e21 and loses precision
above 2^53−1). `canonical_json` refuses a number outside the safe range rather than guessing,
which is the same posture it already took towards non-integral floats. No fixture or vector comes
near it; the refusal is there so that a future one fails loudly instead of hashing differently.
