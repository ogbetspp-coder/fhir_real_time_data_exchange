# ADR 0003: Mechanical narrative fidelity

- Status: Accepted for prototype
- Date: 2026-09-19

## Context

ADR 0002 allows an AI-assisted service to structure label documents but forbids it from
changing narrative words. A prompt cannot enforce that rule; a proof can. The proof must be
pure, deterministic, dependency-free, re-implementable in another language, and must never
expose clinical text in its outputs, because logs, manifests, and ledgers must not carry
narrative.

## Decision

`src/fidelity/` implements a pure, synchronous verifier. For every Composition section that
carries the canonical source code system and a narrative `text.div`:

1. the XHTML is converted to text by a fail-closed scanner that accepts only a closed list of
   elements, attributes, and entities (no `style`, `hidden`, `title`, scripts, comments, or
   processing instructions);
2. the provenance spans are located in the extractor's page text, their raw-slice hashes are
   recomputed, and consecutive spans must be contiguous (only whitespace or page
   header/footer between them) and must not overlap another section's spans; the expected text
   is one contiguous slice per page, so the source's own characters — never a separator of the
   verifier's — decide where words begin and end;
3. both texts are normalised with the versioned procedure in `docs/fidelity-normalization.md`
   (`NORMALIZATION_VERSION`), and must be exactly equal; an input with no narrative sections
   fails rather than vacuously passing.

The verifier emits a `FidelityReport` containing statuses, counts, offsets, lengths, and hashes
only, plus a `narrativeBindingSha256` computed from the Bundle's normalised narratives alone
(so Zone B can bind a report to a Bundle without the source text) and a `reportHash`.
Mismatch details are a `DiffHint` of lengths, the first differing offset, common-suffix length,
word counts, and hashes; never characters.

The normalisation is deliberately conservative. It removes only artefacts of extraction and
markup (Unicode NFC, a closed list of invisible formatting characters, a closed list of
ligatures, a closed list of bullet glyphs, whitespace classes). It does not fold case, quotes,
dashes, super- or subscripts, list markers, or line-break hyphenation. A joined or split word is
therefore a reported failure that the extractor must avoid, not a difference the checker
forgives. False failures are acceptable; false passes are not.

Golden vectors in `test/fixtures/fidelity/` are the executable specification. Any
re-implementation must reproduce every vector's output and `reportHash` byte-for-byte. Any
change within the scope that `docs/fidelity-normalization.md` section 8 defines — including a
runtime whose Unicode or ICU version differs from the pinned one, with no code change — is a
new `NORMALIZATION_VERSION`, regenerates the vectors, and follows the change-control checklist
in `docs/validation/README.md`. Section 8 is the single normative statement of the trigger;
this ADR and the validation README defer to it. `test/runtime.test.ts` fails the build if the
runtime's Unicode version drifts from the pin, so the trigger cannot fire unnoticed.

Zone B runs the verifier as a gate before `transformType2ToEma` for every `document` source:
the report must parse against its contract schema, be `passed`, on the current normalisation
version, its `reportHash` must recompute, its binding hash must equal the Bundle's, and the
verification is always re-executed against the referenced extracted text and must reproduce
the declared `reportHash`. Hashes of JSON values use canonical JSON with keys in UTF-16 code
unit order, never locale-aware ordering, so a re-implementation can reproduce them.

## Consequences

- Structure is free (paragraph and cell boundaries flatten to spaces); words are checked.
- The extractor contract is explicit: emit discretionary hyphens as U+00AD, hard hyphens
  verbatim, table cells row-major separated by TAB and rows by LF, and declare per-page
  `bodyStart`/`bodyEnd` so repeated headers and footers are excluded from spans.
- Tail-of-page omissions are visible only through the report's coverage figures, which are
  recorded as evidence for reviewers but do not fail the check. Because the body range is
  declared by the extractor, it is bounded rather than trusted: bodies must sit on line
  boundaries and may exclude at most 240 code points per page (`docs/fidelity-normalization.md`
  section 1), and the report records the full page length alongside the body length.
- Across a page break the verifier concatenates the page bodies verbatim, blank gaps included,
  so the source's own characters (a final line terminator or a soft hyphen) decide whether a
  word continues; the verifier never inserts a character of its own.
- Attribute values in narrative markup are token-limited (`docs/fidelity-normalization.md`
  section 5) because they are never compared against the source. The bounds limit the
  capacity of that channel; they do not eliminate it. `ol` and `q` are not allowed because
  renderers generate visible characters for them, and table sections must appear in rendering
  order.
- A section may omit words but never begin or end inside one: the outer span edges must fall
  on word boundaries (`docs/fidelity-normalization.md` section 6, reason `word-cut`), and a
  soft hyphen directly before a structural line break in the narrative rejects, so a word can
  be neither truncated at a section edge nor joined across markup.
- Golden vectors are the fixed, reviewed floor of a re-implementation, not its proof. The first
  second-language port (Python, 2026-09-20) passed all 130 vectors and then diverged from the
  reference on inputs nobody had written a vector for — regex dialect (`\d`, `$`), unpaired
  surrogates, JSON number integrality — and exposed that the reference's own canonical JSON
  was not RFC 8785 for integer-like keys. Vectors authored from the reference establish
  agreement only where its author already looked. A port is therefore proven by a seeded
  differential run over generated inputs (spec section 8), and the specification was amended
  (`fidelity-norm/1.1.1`) to state every rule the port had to read from the code.
- NFC (step 3) depends on the Unicode Character Database of the runtime. Zone B pins its
  runtime image (`node:22.14.0`, ICU 76.1, Unicode 16.0) and any re-implementation must pin an
  equivalent; a runtime with a different Unicode version is a change to the normalisation
  version even though no code changes.
- The verifier is a second, independent narrative gate alongside the existing
  byte-preservation assertion in `src/fhir/transform.ts` (UR-01); the new control is UR-09.
