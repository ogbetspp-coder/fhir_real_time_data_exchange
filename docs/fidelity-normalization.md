# Narrative fidelity normalisation specification

Version: `fidelity-norm/1.1.1` (`NORMALIZATION_VERSION` in `src/fidelity/normalize.ts`; history
in section 9)

This document is the language-neutral specification of the text normalisation and XHTML
extraction used by the narrative fidelity check (ADR 0003). The TypeScript implementation in
`src/fidelity/` and any re-implementation must produce identical results for the golden vectors
in `test/fixtures/fidelity/`. Every rule below is applied identically to the extractor's page
text and to the narrative text; nothing is applied to one side only.

## 1. Text units

- Offsets, lengths, and slices are counted in Unicode code points (not UTF-16 code units, not
  bytes). In TypeScript iterate with `Array.from(text)`; in Python index `str` directly.
- A span's `textSha256` is the SHA-256 hex digest of the UTF-8 encoding of the raw
  (un-normalised) slice `text[startOffset:endOffset]`.
- A page's body is the half-open range `[bodyStart, bodyEnd)` in code points. Spans must lie
  inside the body. Text outside the body (running headers, footers, page numbers) is ignored
  by contiguity checks, but the report records the whole page length (`pageCodePoints`) next
  to the body length so excluded text is visible.
- The body range is declared by the extractor and is bounded rather than trusted: `bodyStart`
  must be 0 or immediately follow U+000A, and that U+000A must not itself follow U+00AD (step 1
  would delete it, so it lies inside a word); `bodyEnd` must be the page length or the code
  point before it must be U+000A (a body ends with its own line terminator; U+00AD U+000A is
  allowed there because a word may continue on the next page — section 6 decides whether the
  section may end there); and a page may exclude at most 240 code points in total. A page that
  violates any of these makes every span on it `span-not-found` (reason `body-boundary` or
  `excluded-text`) and the report `failed`.
- Every hash of a JSON value (`reportHash`, `extractedTextSha256`, `narrativeBindingSha256`,
  the contract hashes) is the SHA-256 of canonical JSON: object keys sorted by UTF-16 code
  unit order (RFC 8785), no insignificant whitespace, `JSON.stringify` number and string
  formatting. Locale-aware sorting is never used. Two consequences an implementation must
  honour explicitly: the sorted order must be emitted directly, never obtained by inserting
  keys into a language-native object or map that reorders integer-like keys (`"2"`, `"10"`);
  and strings are written as `JSON.stringify` writes them, so an unpaired surrogate is emitted
  as the escape `\udXXX`, never as a raw code unit. Every number in a hashed structure is an
  integer; a value such as `1.0` is the integer 1 wherever it is read.
- `extractedTextSha256` is the hash of the whole `SourceDocumentText` value the check ran
  against. `reportHash` is the hash of the report with its own `reportHash` member removed;
  every other member of the report, including `issues` and each section result, is inside it.
- `narrativeBindingSha256` is the hash of an array, in Composition section order, of objects
  `{ "sourceKey", "normalizedTextSha256" }`, where `normalizedTextSha256` is the SHA-256 of the
  UTF-8 bytes of the section's normalised narrative (section 5 then section 3), or JSON `null`
  when the narrative cannot be normalised. It is computable from the Bundle alone.

## 2. Rejection (before any normalisation)

The input is malformed, and the section fails with `malformed-narrative`, if it contains:

- U+FFFD REPLACEMENT CHARACTER;
- any C0 control other than U+0009 TAB, U+000A LF, U+000B VT, U+000C FF, U+000D CR;
- U+007F DELETE;
- U+FFFE or U+FFFF;
- an unpaired UTF-16 surrogate (a code point in U+D800–U+DFFF).

Rejection applies to page text as well; a page containing these characters makes every span on
it `span-not-found`.

## 3. Normalisation steps (ordered)

1. Delete invisible formatting characters — closed list: U+00AD SOFT HYPHEN, U+200B ZERO WIDTH
   SPACE, U+FEFF ZERO WIDTH NO-BREAK SPACE, U+2060 WORD JOINER. When U+00AD is immediately
   followed by U+000A (or U+000D U+000A), that line break is deleted with it: a soft hyphen at a
   line end marks a word broken across lines.
2. Expand ligatures — closed list: U+FB00 → `ff`, U+FB01 → `fi`, U+FB02 → `fl`, U+FB03 → `ffi`,
   U+FB04 → `ffl`, U+FB06 → `st`. (NFKC is not used: it would also flatten superscripts and
   subscripts, which are content.)
3. Unicode Normalization Form C (NFC). It runs after steps 1 and 2 so that a composition an
   invisible character or a ligature would otherwise block (`e` + ZWSP + combining acute) is
   applied in the first pass.
4. Replace bullet glyphs with U+0020 — closed list: U+2022, U+2023, U+2043, U+2219, U+25A0,
   U+25A1, U+25AA, U+25AB, U+25CB, U+25CF, U+25E6.
5. Replace every whitespace-class code point with U+0020 — closed list: U+0009, U+000A,
   U+000B, U+000C, U+000D, U+0020, U+0085, U+00A0, U+1680, U+2000–U+200A, U+2028, U+2029,
   U+202F, U+205F, U+3000. Then collapse runs of U+0020 to a single U+0020 and remove leading
   and trailing U+0020.

The procedure is idempotent: applying it twice yields the first result (the golden vectors
include the blocking cases that make step order matter).

## 4. Deliberately not normalised

These are treated as content; a difference is a mismatch:

- letter case;
- straight versus typographic quotation marks and apostrophes (U+0027, U+0022, U+2018, U+2019,
  U+201C, U+201D and others);
- hyphen and dash variants (U+002D, U+2010–U+2015, U+2212) and line-break de-hyphenation
  (`intra-` at a line end followed by `venous` stays `intra- venous`; the extractor must emit
  discretionary hyphens as U+00AD so that step 1 removes them together with the line break);
- superscript and subscript code points (`m²`, `H₂O`) versus plain digits;
- footnote and reference markers, numbered-list markers (`1.`, `a)`), and other punctuation;
- U+200C ZERO WIDTH NON-JOINER and U+200D ZERO WIDTH JOINER;
- any character not named in section 3.

## 5. XHTML to text

The narrative `text.div` is scanned without a DOM. Any violation makes the section
`malformed-narrative`.

- Exactly one root element `div` carrying `xmlns="http://www.w3.org/1999/xhtml"`; only
  whitespace may appear outside it.
- Element names are lower-case. Block elements emit U+000A before their start tag and after
  their end tag: `div p h1 h2 h3 h4 h5 h6 ul li table thead tbody tfoot tr td th caption pre
blockquote dl dt dd hr`. `br` emits U+000A. Inline elements contribute only their text:
  `span b i u em strong sup sub small a abbr cite code`. Self-closing syntax (`<br/>`,
  `<td/>`) is accepted for any allowed element.
- Any other element (including `script`, `style`, `img`, `svg`, `object`, `iframe`, `del`,
  `s`, `strike`, `math`, form controls, and `ol` and `q`, whose renderers generate list
  numbers and quotation marks the source may not contain) rejects (`unknown-element`).
- Table parts must appear in the one document order that renders as written, because
  renderers place them by role: `caption` (at most one) first; then either rows (`tr`)
  directly under `table`, or sections in the order `thead` (at most one), `tbody` (any
  number), `tfoot` (at most one), never both forms in one table. `caption`, `thead`, `tbody`,
  `tfoot` must be direct children of `table`; `tr` of `table` or a section; `td` and `th` of
  `tr`. Violations reject (`table-structure`, `table-section-order`, or `misnested-tag`).
- U+00AD directly before a structural U+000A (a block boundary or `br`) rejects
  (`soft-hyphen-at-boundary`): step 1 would join the word across markup that renders as a
  hyphenated line break.
- Allowed attributes: `xmlns` (root only), `xml:lang`, `lang`, `id`, `class`, `href` (on `a`
  only), `colspan`, `rowspan`, `scope`. Values must be double- or single-quoted. Any other
  attribute — in particular `style`, `hidden`, and `title` — rejects, and so does a repeated
  attribute name on one element.
- Attribute values are never compared against the source, so they must not be able to carry
  text. Each value must match its token form or the element rejects: `xml:lang`, `lang`,
  `id`, `scope` — `[A-Za-z0-9_.:-]{1,32}`; `class` — up to three such tokens separated by
  single spaces; `colspan`, `rowspan` — an integer 1–999; `href` — `https://` host and up to
  eight path segments of at most 32 unreserved characters (no query or fragment), or `#`
  followed by a token. Anything else (`javascript:` links, spaces, `<`, `&`, query strings)
  rejects. These bounds limit, but do not eliminate, what attribute values can carry; narrative
  markup should not need links.
- Comments, processing instructions, CDATA sections, DOCTYPE declarations, a stray `<`, a
  stray `&`, unbalanced or misnested tags reject.
- Entities: `&amp; &lt; &gt; &quot; &apos;`, decimal `&#N;`, and hexadecimal `&#xH;` only.
  Decoded code points are subject to section 2. Named HTML entities such as `&nbsp;` reject.
- The extracted text is then normalised (section 3) by the verifier, which is where an empty
  result is decided: a narrative whose normalised text is empty is `malformed-narrative` with
  reason `empty-narrative`. The scanner itself never produces that reason.

Because block boundaries become U+000A and the whitespace step collapses them, paragraph and
table-cell boundaries are structure, not content. Cell text is content.

### Tokeniser

The scanner is a single left-to-right pass over the `div` string, matching at the current
offset only (sticky matching). The grammar is given as regular expressions in a dialect where
every class is spelled out: `[0-9]` never means a non-ASCII digit, `$` never matches before a
trailing line break, and `WS` stands for the JavaScript `\s` class exactly — U+0009, U+000A,
U+000B, U+000C, U+000D, U+0020, U+00A0, U+1680, U+2000–U+200A, U+2028, U+2029, U+202F, U+205F,
U+3000, U+FEFF. An implementation whose regex dialect differs in any of these must spell the
class out; a port that copies the reference's regex text verbatim is wrong.

- Start tag: `<(NAME)(ATTRS)WS*(/?)>` where `NAME` is `[A-Za-z][A-Za-z0-9]*` and `ATTRS` is
  zero or more of `WS+ ANAME WS* = WS* ( "[^"<]*" | '[^'<]*' )`, with `ANAME` =
  `[A-Za-z_:][-A-Za-z0-9_:.]*`. Attribute values may not contain `<` or their own quote
  character; the value grammars of the previous bullet are then applied to the unquoted value
  as whole-string matches.
- End tag: `</(NAME)WS*>`.
- Entity: `&( [A-Za-z]+ | #[0-9]{1,7} | #x[0-9A-Fa-f]{1,6} );`. Named entities are only the
  five listed; a decoded code point above U+10FFFF rejects.
- At `<`: a comment (`<!--`), CDATA section (`<![CDATA[`), any other `<!`, and `<?` reject with
  `comment`, `cdata`, `doctype`, `processing-instruction` respectively. A `</` that does not
  match the end-tag grammar is `malformed-tag`. A `<` that matches neither grammar is
  `malformed-tag` when the next character is an ASCII letter and `stray-lt` otherwise. An
  element name containing an upper-case letter is `uppercase-element`, checked before the name
  is looked up; a name outside the allowed lists is `unknown-element`.
- Root: the first element must be `div`, else `root-not-div`; a second element at depth zero,
  or any element after the root has closed, is `multiple-roots`; non-whitespace text (or an
  entity) outside the root is `text-outside-root`, where whitespace means U+0009, U+000A,
  U+000D, U+0020 only. A root `div` without `xmlns="http://www.w3.org/1999/xhtml"` is
  `root-not-div`, checked after its attributes; `xmlns` anywhere else, or with any other
  value, is `forbidden-attribute`.
- Attributes are checked in document order: a repeated name, an unlisted name, `href` on an
  element other than `a`, or a value failing its grammar is `forbidden-attribute`.
- A block element's start tag emits U+000A; its end tag emits U+000A; a self-closing block
  (`<hr/>`, `<td/>`) therefore emits two. `br` emits one. Before any of these emissions, an
  immediately preceding U+00AD in the output is `soft-hyphen-at-boundary`.
- An end tag with nothing open is `unbalanced-tag`; one not matching the innermost open
  element is `misnested-tag`; input ending with elements still open is `unbalanced-tag`.
- Table structure is checked at each start tag as in the earlier bullet, in this order:
  parent checks (`misnested-tag`), then `table-structure`, then `table-section-order`.
- At `&`: a run not matching the entity grammar is `stray-amp`.

The first violation encountered in this pass decides the code; scanning does not continue.

## 6. Verification and report rules

- A section's expected text is assembled from its spans as one contiguous raw slice per page —
  from the first span's start to the last span's end on that page, including whatever lies
  between consecutive spans (which must normalise to nothing). The source's own characters
  therefore decide where words begin and end; the verifier never inserts whitespace between
  spans on the same page, so adjacent spans cannot split a word.
- When a section continues onto the next page, the page-1 slice is extended to `bodyEnd` and
  the page-2 slice starts at `bodyStart` (the blank tail and head must normalise to nothing),
  and the slices are concatenated verbatim with no separator: the body's own final line
  terminator, or a soft hyphen when a word continues, decides how the pages join. The verifier
  never inserts a character of its own.
- A section may omit words but never begin or end inside one. Its first span must start at a
  word boundary and its last span must end at one: unless the first span starts at `bodyStart`,
  the code point before it must not be a word character and must not be a U+000A that follows
  U+00AD; the last span must not end in U+00AD; unless it ends at `bodyEnd`, the code point at
  its end must not be a word character; and if it ends at `bodyEnd`, that end must not be a
  U+00AD U+000A pair (the word continues on the next page, so the section must continue too).
  Word characters are Unicode letters, digits, and combining marks (`\p{L}`, `\p{N}`, `\p{M}`),
  the step 1 invisible characters, U+200C, and U+200D; whitespace and punctuation are
  boundaries. Violations are `invalid-provenance` with reason `word-cut`.
- The joined text is normalised (section 3) and must equal the normalised narrative exactly.
- A report with zero narrative sections is `failed` (issue `No narrative sections to verify`);
  "nothing to check" is never a pass.
- `coverage` records `pageCodePoints` (all page text), `bodyCodePoints` (declared bodies),
  `coveredCodePoints` (verified spans), and `uncoveredGaps` (non-blank body text between
  verified spans). Coverage never fails the check; it is evidence for reviewers.

### Section results

Each section of the Composition that carries the canonical source code system produces one
result. Sections and provenance entries are matched by `sourceKey`; a duplicate key on either
side is a structural error (below), not a result.

- Members present in every result: `sourceKey`, `path` (the section's Composition path),
  `status`, `spanCount` (number of provenance spans; 0 without provenance).
- `normalizedTextSha256` is present whenever the narrative normalises (every status except
  `malformed-narrative`), and equals the value that enters the binding hash.
- `reason` is present for `malformed-narrative`, `span-not-found`, and `invalid-provenance`,
  and absent otherwise. `details` is present for `mismatch` only. An absent member is absent,
  never `null`; this decides `reportHash`.
- The status is decided in this order, stopping at the first that applies: no provenance entry →
  `missing-provenance`; narrative does not normalise → `malformed-narrative` with the scanner's
  code, `forbidden-character` (section 2), or `empty-narrative`; span resolution fails → the
  status and reason below; normalised source and narrative differ → `mismatch`; else
  `verified`.
- Span resolution examines the spans in order and stops at the first failure. Per span:
  `span-not-found` with reason `page-not-found`, `page-malformed` (the page contains a section
  2 character), `body-boundary` or `excluded-text` (section 1), `outside-body` (the span lies
  outside the body or is empty), `hash-mismatch` (`textSha256`); then, against the previous
  span, `invalid-provenance` with reason `span-order` (same page, starts before the previous
  ends), `non-contiguous` (the gap does not normalise to nothing, or the pages are not
  consecutive). After all spans, `invalid-provenance` with reason `word-cut` (the edge rules
  above).
- Overlap is decided after every section is resolved: the spans of all `verified` sections are
  ordered by page, then start offset; whenever two consecutive spans on one page belong to
  different sections and the later starts before the earlier ends, both sections are marked;
  every marked section that was `verified` becomes `invalid-provenance` with reason `overlap`,
  and its spans are left out of the coverage figures.
- `details` (the diff hint) is computed on the two normalised texts as code-point sequences:
  `expectedLength` and `actualLength`; `firstDifferingOffset`, the length of the common prefix;
  `commonSuffixLength`, the length of the common suffix of what follows that prefix (so prefix
  plus suffix never exceeds the shorter text); `expectedWordCount` and `actualWordCount`, the
  number of U+0020-separated tokens of each normalised text, 0 for an empty text; and
  `expectedSha256`, `actualSha256`, the SHA-256 of each text's UTF-8 bytes.

### Report

- `status` is `passed` if and only if every section is `verified` and `issues` is empty.
- `issues` holds exactly these strings, each once per occurrence and in this order: for each
  page that fails a section 1 rule, `Page <n>: body-boundary` or `Page <n>: excluded-text`
  (in page order); `No narrative sections to verify` when there are no sections; `Orphan
provenance <sourceKey>` for each provenance entry with no section, in provenance order.
  The wording is normative because `issues` is inside `reportHash`.
- `summary` is `{ "total": <sections>, "verified": <verified sections> }`.
- Structural errors do not produce a report at all: a normalisation version other than the
  implementation's own, a duplicate page number, a page whose body range is not a valid
  range, a duplicate `sourceKey` among sections or among provenance entries. Their wording is
  not normative.

## 7. Extractor contract

The page text an extractor produces is the reference the narrative is checked against, so the
extractor is a controlled component: its name and version are recorded in
`IngestionProvenance.extraction.parser`, pinned, and checksummed like the FHIR packages. It must:

- take every character from the document's embedded text layer through a pinned,
  deterministic library, so that for a born-digital document the extracted text is
  character-exact by construction. A component that classifies structure (headers, footers,
  headings, tables) may be used to decide body ranges and serialise tables, but its own text
  is never emitted: the extractor spike (`docs/design/extractor-spike.md`) showed that a layout
  parser straightens quotes, substitutes dashes and spaces, and flattens superscripts, and
  because the narrative is derived from the extracted text, the fidelity check cannot see
  such a change — both sides carry it;
- record in `extractorVersion` the pinned versions of every component whose output reaches the
  text or the body ranges, read from each component's own version endpoint rather than from
  its response when the response does not carry one;
- emit table cells row-major separated by U+0009 and rows by U+000A;
- emit discretionary (line-break) hyphens as U+00AD and hard hyphens verbatim;
- declare `bodyStart`/`bodyEnd` per page so repeated headers and footers are excluded, with the
  body ending in its final line terminator (section 1), and never exclude more than a running
  header and footer;
- not apply any normalisation of its own beyond faithful text extraction.

## 8. Change control

Any change to sections 2–6 is a new `NORMALIZATION_VERSION`. The golden vectors are
regenerated, every changed vector is reviewed by hand with a recorded reason, the ADR is
amended, and previously approved submissions require re-approval because the version is part
of the approved content hash.

The increment states what changed. A **patch** increment documents behaviour the vectors
already pin: no normalised text, extracted text, status, reason, coverage figure, or diff hint
in any vector changes, and only the hashes that embed the version string move. A **minor**
increment changes at least one vector's outcome. Anything that invalidates a span, a hash
rule, or the extractor contract is **major**. Every increment, patch included, is part of the
approved content and so still requires re-approval of anything approved under the previous
version.

A second-language implementation is proven by agreement with the reference on inputs neither
author chose — a seeded differential run over generated inputs — and not by the golden vectors
alone: vectors authored from the reference only establish agreement where its author already
looked. The vectors remain the fixed, reviewed floor; the differential run is the proof.

## 9. Version history

- `fidelity-norm/1.1.1` (patch) — documents behaviour the vectors already pinned but the text
  left to the reference implementation, found when the check was re-implemented in a second
  language: the tokeniser grammar and error-code precedence, the two line breaks of a
  self-closing block, where `empty-narrative` is decided, what `reportHash` and
  `narrativeBindingSha256` cover, the exact `issues` strings, the reason-code catalogue and
  its order, overlap handling and its effect on coverage, the presence rules of section-result
  members, the diff-hint definitions, and the `passed` rule (section 1, 5, 6). Also states the
  canonical-JSON consequences an implementation must honour explicitly — no native-object
  reordering of integer-like keys, `\udXXX` for unpaired surrogates, integers only — after the
  reference itself was found to violate the first. No vector outcome changed.
- `fidelity-norm/1.1.0` — U+00AD followed by a line break deletes the break (step 1);
  cross-page slices are concatenated verbatim including blank gaps (section 6); body ranges
  must sit on line boundaries and exclude at most 240 code points (section 1); span edges must
  fall on word boundaries (section 6); `ol` and `q` reject, table parts must be in rendering
  order, and U+00AD before a structural line break rejects (section 5); attribute grammars
  tightened (section 5). Closes review rounds 2 and 3. No submission was ever approved under
  1.0.0, so no re-approval was due.
- `fidelity-norm/1.0.0` — initial specification.
