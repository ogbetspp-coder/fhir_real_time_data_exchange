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
   elements, attributes, and entities (no `style`, `hidden`, `title`, `class`, `id`, scripts,
   comments, or processing instructions), and that folds into the text, or rejects, any markup
   a renderer would draw differently from the text it emits (since `fidelity-norm/2.0.0`);
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
ligatures, a closed list of bullet glyphs where they start a list item, whitespace
classes). It does not fold case, quotes,
dashes, super- or subscripts, list markers, or line-break hyphenation. A joined or split word is
therefore a reported failure that the extractor must avoid, not a difference the checker
forgives. False failures are acceptable; false passes are not.

Golden vectors in `test/fixtures/fidelity/` are the executable specification. Any
re-implementation must reproduce every vector's output and `reportHash` byte-for-byte. Any
change within the scope that `docs/fidelity-normalization.md` section 8 defines is a new
`NORMALIZATION_VERSION`, regenerates the vectors, and follows the change-control checklist in
`docs/validation/README.md`. A runtime whose Unicode or ICU version differs from the pinned one
is a controlled change even with no code change: it is re-measured against every vector, a
seeded differential run and NFC/NFD and the word-character class over every code point, and it
is a new `NORMALIZATION_VERSION` if any output within that scope changes. Section 8 is the
single normative statement of the trigger; this ADR and the validation README defer to it.
`test/runtime.test.ts` fails the build if the runtime's Unicode or ICU version drifts from the
pin, so the re-measurement cannot be skipped unnoticed. (Amended 2026-09-23: the earlier wording
made any ICU change a new version, which section 8 does not; the Node 22.22.0 change record
shows the measurement.)

Zone B runs the verifier as a gate before `transformType2ToEma` for every `document` source:
the report must parse against its contract schema, be `passed`, on the current normalisation
version, its `reportHash` must recompute, its binding hash must equal the Bundle's, and the
verification is always re-executed against the referenced extracted text and must reproduce
the declared `reportHash`. Hashes of JSON values use canonical JSON with keys in UTF-16 code
unit order, never locale-aware ordering, so a re-implementation can reproduce them.

## Consequences

_Amended 2026-09-23 for `fidelity-norm/2.0.0`
(`docs/validation/changes/2026-09-23-fidelity-norm-2-0-0.md`): markup may not change what a
reader sees without the check seeing it._

- Structure is free (paragraph boundaries, headings, line breaks, list nesting and bullets
  flatten to spaces; from `fidelity-norm/3.0.0` a table's grid, an `ol`'s numbers and a
  picture's bytes are folded into the text and checked); words are checked. A
  bullet glyph is structure only where it starts a list item (after a line feed, before
  whitespace, on a line without a tab); mid-line or in a table cell it is content, and U+2219
  and U+2043 are never bullets. A line feed in the narrative's text is a space, as a renderer
  draws it; only a block boundary or `br` is a line break, and a table cell is a tab-separated
  line, as the extractor writes a row.
  Raised and lowered digits and signs are words: the digits and signs inside `sup` and `sub`
  fold to their script code points, so `10<sup>6</sup>` is `10⁶` and never equals a source's
  `106`; a number there with no script form, and U+00B1 or U+2213, rejects.
- The extractor contract is explicit: emit discretionary hyphens as U+00AD, hard hyphens
  verbatim, each table with its grid (from `fidelity-norm/3.0.0`: table, row, cell and
  covered-slot markers, the slots a merged cell covers marked as covered, never as empty cells),
  numbered-list markers as drawn with a space, pictures as the hash of their bytes, raised and
  lowered digits and signs as script code points, no U+000B or U+000C (a
  page break is the page record), and declare per-page `bodyStart`/`bodyEnd` so repeated
  headers and footers are excluded from spans, with every non-empty body ending in its own line
  feed. An extractor that cannot tell a glyph's baseline shift must refuse the document rather
  than emit plain digits. From `fidelity-norm/3.0.0` the contract also writes a table across pages
  as its logical text and a continuation line that begins with a bullet glyph with a leading
  tab, and it qualifies structured sources only: for drawn documents (a PDF, or a Word document however it is read) its reviews recorded open cases (line wraps, undrawn soft hyphens, page footnotes), and no
  drawn-document extractor may support an approval until a later version closes them.
- Tail-of-page omissions are visible only through the report's coverage figures, which are
  recorded as evidence for reviewers but do not fail the check. Because the body range is
  declared by the extractor, it is bounded rather than trusted: bodies must sit on line
  boundaries, end with their own line terminator even at the end of a page, and may exclude at
  most 240 code points per page (`docs/fidelity-normalization.md` section 1); pages are
  numbered 1..N, so none can be left out; and the report records the full page length
  alongside the body length.
- Across a page break the verifier concatenates the page bodies verbatim, blank gaps included,
  so the source's own characters (a final line terminator or a soft hyphen) decide whether a
  word continues; the verifier never inserts a character of its own.
- Attribute values in narrative markup are token-limited (`docs/fidelity-normalization.md`
  section 5) because they are never compared against the source. The bounds limit the
  capacity of that channel; they do not eliminate it. Nothing a viewer's stylesheet or script
  can key on to hide text is allowed: no `class`, no `id`, a language tag on the root only, no
  in-page link. `q` is not allowed because renderers generate visible characters for it; `ol`
  is allowed from fidelity-norm/3.0.0 because the numbers a renderer draws are folded into the
  text; `u` and `a` are not allowed from fidelity-norm/3.0.0 because a renderer underlines them
  and an underline turns a sign into another ("<" into "≤"); an `hr` in a table cell or caption
  is refused from fidelity-norm/3.0.0 because it is drawn as a fraction bar; and table sections
  must appear in rendering order.
- Only `br` and `hr` (and, from `fidelity-norm/3.0.0`, `img`) may be self-closing, and must
  be, because an HTML parser ignores the `/` of any other element. Inside a tag only TAB, LF, CR and SPACE are whitespace, because an HTML
  parser reads any other code point there as part of the tag name. Tables contain only table
  parts and whitespace, and `pre` is not allowed, because a renderer moves other content out of
  a table and draws preformatted columns the check cannot see. From fidelity-norm/3.0.0 the
  text carries each table's grid (rows, cells, and the slots a `colspan` or `rowspan` covers),
  so which cell a value is in is checked, and a picture is carried as the hash of its source
  (`docs/design/fidelity-norm-3-0-0.md`).
- A section may omit whole whitespace-delimited tokens but never begin or end inside one: the
  outer span edges must touch whitespace (or a body edge), read back through whitespace and
  across pages (`docs/fidelity-normalization.md` section 6, reason `word-cut`). Punctuation is
  not a boundary, because inside a number it is part of the number (`1` of `1.5`, `20` of
  `−20`), and neither is a space between the groups of a number (`10` of `10 000`) or a
  no-break space. A soft hyphen or a zero-width space in narrative rejects (from
  `fidelity-norm/3.0.0`; before it, a soft hyphen before a block boundary or `br`), and a line
  feed in text is a space, so a token can be neither truncated at a section edge nor joined
  across markup. The rule proves that edges touch whitespace, not that
  they end a sentence or a clause: "Take 5" can still be taken from "Take 5 mg twice".
- Section 2 rejects on both sides the characters a renderer draws differently from the check:
  C1 controls (remapped through windows-1252), U+000B and U+000C, and the bidirectional
  controls. It applies to the whole narrative `div` as decoded from JSON, markup included,
  before the scan, and to each decoded character reference on its own, so an unpaired
  surrogate cannot be completed across markup or by a second reference. A text layer decoded
  as Latin-1 therefore fails the page; that is intended.
- Not closed by `fidelity-norm/2.0.0`, stated: cell association. Cell boundaries flatten to
  whitespace, so a narrative table with the same text and the same row width can put a value
  in a different cell from the source — a dose can move from the Adults column to the Children
  column and verify. The empty slots the extractor contract requires for a spanned source cell
  make that easier, because an empty narrative cell costs nothing. Closing it needs a table
  extractor contract and is the next major version. (Closed by `fidelity-norm/3.0.0`, whose
  text carries each table's grid and whose extractor contract emits it, apart from the line a
  value sits on inside a multi-line cell and how high or wide a row or column is drawn, which it
  states.) Also not closed: a text layer that
  flattens a superscript is outside the check, because the narrative is derived from it (only
  the extractor can close that); a letter exponent (`2<sup>n</sup>` against `2n`) still
  verifies, and from `fidelity-norm/3.1.0` so do a lowered ∞, and a lowered ½ that is a `sub`'s
  whole content in the half-life form `t<sub>½</sub>` (section 5's lowered-half rule), against the
  same code point on the line
  (`t<sub>½</sub>` against `t½`; `docs/design/fidelity-norm-3-1-0.md`); strong right-to-left letters can reorder adjacent numbers, and no EU
  product-information language uses them; a viewer's own stylesheet or script can still act on
  the element names that remain.
- A stated exception to "false passes are not", in the authority importer's T
  (`docs/design/authority-import-t.md`, T5; amended 2026-09-24): an underlined `+` is drawn as
  `±`, and T records `+` where the underline covers a whole subheading paragraph outside every
  table and list, the `+` ends a token of at least two letters (`Ph+`), and the same document
  writes that token with a plain `+` in a section T accepts without the rule. The document itself
  settles the meaning (the Imatinib Teva SmPCs define "Ph+ ALL" in 4.1 and underline their
  subheadings as the QRD template styles them); the residual is an author who meant `±` at that
  one place. Nothing else a drawing changes is read as intended: every other sign stays refused.
- Golden vectors are the fixed, reviewed floor of a re-implementation, not its proof. The first
  second-language port (Python, 2026-09-20) passed all 130 vectors and then diverged from the
  reference on inputs nobody had written a vector for — regex dialect (`\d`, `$`), unpaired
  surrogates, JSON number integrality — and exposed that the reference's own canonical JSON
  was not RFC 8785 for integer-like keys. Vectors authored from the reference establish
  agreement only where its author already looked. A port is therefore proven by a seeded
  differential run over generated inputs (spec section 8), and the specification was amended
  (`fidelity-norm/1.1.1`) to state every rule the port had to read from the code.
- NFC (step 3) depends on the Unicode Character Database of the runtime. Zone B pins its
  runtime image (`node:22.22.0`, ICU 77.1, Unicode 16.0; `node:22.14.0`, ICU 76.1, until
  2026-09-22 — the recorded change in `docs/validation/README.md`) and any re-implementation
  must pin an equivalent; a runtime with a different Unicode version is a change to the
  normalisation version even though no code changes.
- The verifier is a second, independent narrative gate alongside the existing
  byte-preservation assertion in `src/fhir/transform.ts` (UR-01); the new control is UR-09.

## Amendment (2026-09-25, a withheld section)

The fidelity check covers "every section that carries the code and a `text.div`" except a
**withheld** section of an authority import (`docs/design/authority-import-withheld.md`), whose
only text is a fixed notice that is not narrative: `fidelity-norm/3.2.0` gives the report a
`withheld` status, binds the section in `narrativeBindingSha256` and refuses the notice anywhere
else. A section is withheld only on the renderer gate's evidence, from the browser's exact advances, its
painted pixels and the pinned fonts' ink bounds, that the authority's own drawing cannot be read as
written, confirmed by the
person who requests the import, never on a refusal of ours, so no false pass
of the authority's text enters the record by it: none of the section's text enters at all.

## Amendment (2026-09-25, contacts acknowledged)

A second stated exception to "false passes are not" (owner decisions of 2026-09-25;
`docs/design/authority-import-renderer.md`, R4): the renderer gate sorts what it draws into clear,
failures (clear misreadings, which refuse) and **contacts**, glyphs or lines that touch or stand
closer than the gate can prove harmless, which pass only once the person who requests the import,
an attested identity before anything is persisted, has seen each one in the gate's captures of the
record they name and acknowledged it legible. No automatic limit passes a contact. Two drawings that
cannot change a letter are clear by rule, not contacts, exactly as the renderer note's P8 states
them (a descender of a closed list of letters on its own cell's bottom border, keeping a row of its
own, with no shared pixel; a glyph at a background's edge, not covered, keeping 4.5:1 against both
fills). The EMA's Imatinib Teva film-coated tablets SmPC has about 70 contacts at sampled widths, 4.2's raised 9 of "10⁹/l" against the line above among
them; the findings are reported to the authority.
