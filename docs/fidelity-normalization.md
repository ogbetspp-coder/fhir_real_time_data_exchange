# Narrative fidelity normalisation specification

Version: `fidelity-norm/2.0.0` (`NORMALIZATION_VERSION` in `src/fidelity/normalize.ts`; history
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
- The `page` values of `pages`, in array order, are 1, 2, …, N; anything else is a structural
  error. A document therefore cannot leave out the page a section's first word is read against
  (section 6).
- A page's body is the half-open range `[bodyStart, bodyEnd)` in code points. Spans must lie
  inside the body. Text outside the body (running headers, footers, page numbers) is ignored
  by contiguity checks, but the report records the whole page length (`pageCodePoints`) next
  to the body length so excluded text is visible.
- The body range is declared by the extractor and is bounded rather than trusted: `bodyStart`
  must be 0 or immediately follow U+000A, and that U+000A must not itself follow U+00AD,
  directly or through U+000D (step 1 would delete it, so it lies inside a word); `bodyEnd` must
  equal `bodyStart`, or the code point before it must be U+000A: a non-empty body always ends
  with its own line terminator. This holds at the end of the page too: a page ending "… is 1"
  with no line feed, followed by a page beginning "0 mg", would otherwise let a section end
  inside a number. U+00AD followed by U+000A is allowed at the end of a body because a word may
  continue on the next page; section 6 decides whether a section may end there. A page may
  exclude at most 240 code points in total. A page that violates any of these makes every span
  on it `span-not-found` (reason `body-boundary` or `excluded-text`) and the report `failed`.
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

The input is malformed, and the section fails with `malformed-narrative` (reason
`forbidden-character`), if it contains:

- U+FFFD REPLACEMENT CHARACTER;
- any C0 control other than U+0009 TAB, U+000A LF and U+000D CR;
- U+007F DELETE, and the C1 controls U+0080–U+009F;
- U+FFFE or U+FFFF;
- the bidirectional controls U+061C, U+200E, U+200F, U+202A–U+202E and U+2066–U+2069;
- an unpaired UTF-16 surrogate (a code point in U+D800–U+DFFF).

U+000B and U+000C reject on both sides (`forbidden-character`): they are not XML characters,
and a renderer draws them as nothing or as a box. The C1 controls reject because a renderer
remaps them through windows-1252 (U+0085 is drawn as "…"), so a narrative carrying one shows
a character the check does not see. The bidirectional controls reject because their reach
differs between a narrative block and a line of page text, and no EU product-information
language needs them.

Rejection applies to page text as well; a page containing these characters makes every span on
it `span-not-found`.

For narrative, section 2 applies to the div string as decoded from JSON (RFC 8259, where an
escaped surrogate pair is one code point). It covers every code point, including markup,
attribute values and text outside the root. A surrogate code point that remains rejects. The
code is `forbidden-character`, and this check precedes the scan. Each character reference is
also checked on its own as the scan decodes it (section 5): a reference to half a surrogate
pair rejects even if the next reference completes it.

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
   U+000D, U+0020, U+00A0, U+1680, U+2000–U+200A, U+2028, U+2029, U+202F, U+205F, U+3000.
   Then collapse runs of U+0020 to a single U+0020 and remove leading and trailing U+0020.
   U+000B, U+000C and U+0085 are not in the list: section 2 rejects them.

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
- superscript and subscript code points (`m²`, `H₂O`) versus plain digits. Markup cannot turn
  one into the other: the digits and signs inside `sup` and `sub` are folded to these code
  points (section 5), so `m<sup>2</sup>` is `m²` and never equals a source's `m2`;
- footnote and reference markers, numbered-list markers (`1.`, `a)`), and other punctuation;
- U+200C ZERO WIDTH NON-JOINER and U+200D ZERO WIDTH JOINER;
- any character not named in section 3.

## 5. XHTML to text

The narrative `text.div` is scanned without a DOM. Any violation makes the section
`malformed-narrative`. The rule behind every bullet is the same: markup may not change what a
reader sees without the check seeing it. Where a renderer — usually an HTML parser given the
narrative as `innerHTML` — would draw markup differently from the text the scanner emits, the
markup is folded into the text or rejected.

- Exactly one root element `div` carrying `xmlns="http://www.w3.org/1999/xhtml"`; only
  whitespace may appear outside it.
- Element names are lower-case. Block elements emit U+000A before their start tag and after
  their end tag: `div p h1 h2 h3 h4 h5 h6 ul li table thead tbody tfoot tr td th caption
blockquote dl dt dd hr`. `br` emits U+000A. Inline elements contribute only their text:
  `span b i u em strong sup sub small a abbr cite code` (`sup` and `sub` fold theirs, below).
- Only `br` and `hr` may be self-closing, and they must be: `<x/>` for any other element, and a
  `br` or `hr` written as a start tag without `/` (`<br>`, `<hr></hr>`), reject
  (`void-element`). An HTML parser ignores the `/` on every other element, so `<sup/>6`,
  `<a href="…"/>text` and `<li/>` open an element around the text that follows; and an XML
  renderer draws no children of a `br` written `<br>…</br>`.
- Any other element (including `script`, `style`, `img`, `svg`, `object`, `iframe`, `del`,
  `s`, `strike`, `math`, form controls, `ol` and `q`, whose renderers generate list numbers and
  quotation marks the source may not contain, and `pre`, which keeps whitespace a renderer
  draws as columns the check cannot see) rejects (`unknown-element`).
- The content of `sup` and `sub` is text and character references only; a child element
  rejects (`script-content`). Folding happens after references are decoded. Inside `sup`:
  `0`–`9` → U+2070, U+00B9, U+00B2, U+00B3, U+2074–U+2079; `+` → U+207A; `-` and U+2212 →
  U+207B; `=` → U+207C; `(` → U+207D; `)` → U+207E. Inside `sub`: `0`–`9` → U+2080–U+2089;
  `+` → U+208A; `-` and U+2212 → U+208B; `=` → U+208C; `(` → U+208D; `)` → U+208E. Folding
  applies to U+0030–U+0039. Inside sup, U+2010–U+2014, U+FE63 and U+FF0D fold to U+207B, and
  U+FE62 and U+FF0B fold to U+207A (as well as '-', U+2212 → U+207B and '+' → U+207A). Inside
  sub, the same characters fold to U+208B and U+208A. Inside sup or sub, any other code point
  of general category N that is not a target of these tables, and U+00B1 and U+2213, reject
  (`unmappable-script`). The targets of general category N are the script digits U+2070,
  U+00B9, U+00B2, U+00B3, U+2074–U+2079 and U+2080–U+2089, whichever of `sup` or `sub` they
  appear in; they are kept. Other code points (letters, footnote marks, ®) are kept unchanged:
  raising a letter or a mark does not change what it says, so `C<sub>max</sub>`,
  `<sup>a</sup>` and `<sup>®</sup>` are accepted, and `t<sub>1/2</sub>` is `t₁/₂`.
- Tables contain only table parts. The only children of `table` are `caption`, `thead`,
  `tbody`, `tfoot` and `tr`; `thead`, `tbody` and `tfoot` contain only `tr`; `tr` contains only
  `td` and `th`. Any other element directly inside `table`, `thead`, `tbody`, `tfoot` or `tr`
  rejects (`table-content`), and so does any character reference, and any raw code point other
  than U+0009, U+000A, U+000D and U+0020, directly inside them: a renderer moves such content
  out of the table. Every `tr` of a table, in whichever section, has the same number of `td`
  and `th` children; cells of a nested table do not count. Otherwise the table rejects
  (`table-shape`), decided at its end tag. A table with no rows is accepted. `colspan` and
  `rowspan` are not allowed (below), so no cell is drawn across a column or row the check
  cannot see.
- Table parts must appear in the one document order that renders as written, because
  renderers place them by role: `caption` (at most one) first; then either rows (`tr`)
  directly under `table`, or sections in the order `thead` (at most one), `tbody` (any
  number), `tfoot` (at most one), never both forms in one table. `caption`, `thead`, `tbody`,
  `tfoot` must be direct children of `table`; `tr` of `table` or a section; `td` and `th` of
  `tr`. Violations reject (`table-structure`, `table-section-order`, or `misnested-tag`).
- In the text the scanner emits, U+00AD followed by U+000A, or by U+000D and then U+000A,
  rejects (`soft-hyphen-at-boundary`), whatever produced the break: raw text, a reference, a
  block boundary or `br`. In page text U+00AD before a line break is the extractor's mark for a
  word broken across lines (section 7) and step 1 joins it; in XHTML a line break inside text is
  a space and a block boundary or `br` is a visible new line, and neither is a hyphenated word.
- Allowed attributes: `xmlns` (root), `xml:lang` and `lang` (root `div` only), `href` (`a`
  only, `https://` form only), `scope` (`th` only). Values must be double- or single-quoted.
  Any other attribute — in particular `style`, `hidden`, `title`, `class`, `id`, `colspan` and
  `rowspan` — rejects, and so does an allowed attribute on any other element and a repeated
  attribute name on one element. A viewer's stylesheet or script can key on a class, an id, a
  language tag or an in-page link to hide content, and a narrative needs none of them below the
  root.
- Attribute values are never compared against the source, so they must not be able to carry
  text. Each value must match its token form or the element rejects: `xml:lang`, `lang`,
  `scope` — `[A-Za-z0-9_.:-]{1,32}`; `href` — `https://` host and up to eight path segments of
  at most 32 unreserved characters (no query or fragment). Anything else (`javascript:` links,
  `#` fragments, spaces, `<`, `&`, query strings) rejects. These bounds limit, but do not
  eliminate, what attribute values can carry; narrative markup should not need links.
- Comments, processing instructions, CDATA sections, DOCTYPE declarations, a stray `<`, a
  stray `&`, unbalanced or misnested tags reject.
- Entities: `&amp; &lt; &gt; &quot; &apos;`, decimal `&#N;`, and hexadecimal `&#xH;` only.
  Each decoded code point is subject to section 2 on its own. Named HTML entities such as
  `&nbsp;` reject.
- The extracted text is then normalised (section 3) by the verifier, which is where an empty
  result is decided: a narrative whose normalised text is empty is `malformed-narrative` with
  reason `empty-narrative`. The scanner itself never produces that reason.

Because block boundaries become U+000A and the whitespace step collapses them, paragraph and
table-cell boundaries are structure, not content. Cell text is content.

### Tokeniser

The scanner is a single left-to-right pass over the `div` string, matching at the current
offset only (sticky matching), after the section 2 check of the whole string. The grammar is
given as regular expressions in a dialect where every class is spelled out: `[0-9]` never
means a non-ASCII digit, `$` never matches before a trailing line break, and `WS` stands for
the JavaScript `\s` class exactly — U+0009, U+000A, U+000B, U+000C, U+000D, U+0020, U+00A0,
U+1680, U+2000–U+200A, U+2028, U+2029, U+202F, U+205F, U+3000, U+FEFF. (U+000B and U+000C can
no longer reach the grammar, because section 2 has rejected them; the class is kept as it was
so that the grammar does not change.) An implementation whose regex dialect differs in any of
these must spell the class out; a port that copies the reference's regex text verbatim is
wrong. General category N (section 5, `sup` and `sub`) is the Unicode general category of the
pinned Unicode version.

- Start tag: `<(NAME)(ATTRS)WS*(/?)>` where `NAME` is `[A-Za-z][A-Za-z0-9]*` and `ATTRS` is
  zero or more of `WS+ ANAME WS* = WS* ( "[^"<]*" | '[^'<]*' )`, with `ANAME` =
  `[A-Za-z_:][-A-Za-z0-9_:.]*`. Attribute values may not contain `<` or their own quote
  character; the value grammars of section 5 are then applied to the unquoted value as
  whole-string matches.
- End tag: `</(NAME)WS*>`.
- Entity: `&( [A-Za-z]+ | #[0-9]{1,7} | #x[0-9A-Fa-f]{1,6} );`. Named entities are only the
  five listed; a decoded code point above U+10FFFF is `unknown-entity`.
- At `<`: a comment (`<!--`), CDATA section (`<![CDATA[`), any other `<!`, and `<?` reject with
  `comment`, `cdata`, `doctype`, `processing-instruction` respectively. A `</` that does not
  match the end-tag grammar is `malformed-tag`. A `<` that matches neither grammar is
  `malformed-tag` when the next character is an ASCII letter and `stray-lt` otherwise.
- Root: the first element must be `div`, else `root-not-div`; a second element at depth zero,
  or any element after the root has closed, is `multiple-roots`; non-whitespace text (or an
  entity) outside the root is `text-outside-root`, where whitespace means U+0009, U+000A,
  U+000D, U+0020 only. A root `div` without `xmlns="http://www.w3.org/1999/xhtml"` is
  `root-not-div`, checked after its attributes; `xmlns` anywhere else, or with any other
  value, is `forbidden-attribute`.
- A block element's start tag emits U+000A; its end tag emits U+000A; a self-closing `hr`
  therefore emits two. `br` emits one.
- Input ending with no root is `root-not-div`; input ending with elements still open is
  `unbalanced-tag`.

The first violation encountered in this pass decides the code; scanning does not continue. The
reason code is inside `reportHash`, so the order in which violations are decided is normative:

- At a start tag: `malformed-tag`/`stray-lt`, then `uppercase-element`, then
  `unknown-element`, then `multiple-roots`/`root-not-div`, then attributes in document order
  (`forbidden-attribute`; root without xmlns = `root-not-div`), then `void-element`, then the
  parent check, then `table-structure`, then `table-section-order`. The parent check:
  `script-content` if the parent is `sup` or `sub`; else `misnested-tag` for a table part
  (`caption`, `thead`, `tbody`, `tfoot`, `tr`, `td`, `th`) in the wrong parent; else
  `table-content` for any other element whose parent is `table`, `thead`, `tbody`, `tfoot` or
  `tr`.
- At an end tag: `malformed-tag`, then `uppercase-element`, then `unbalanced-tag`, then
  `misnested-tag`, then `table-shape` (for `</table>`).
- At `&`: `stray-amp`, then `text-outside-root`, then `unknown-entity`, then
  `forbidden-character`, then `table-content`, then `unmappable-script`.
- At a raw code point: `text-outside-root` or `table-content`, then `unmappable-script`.
- After a clean scan: `soft-hyphen-at-boundary`, then `empty-narrative`.

(So `<table><td>a</td></table>` is `misnested-tag` and `<img/>` is `unknown-element`.) The
section 2 check of the whole `div`, which precedes the scan, decides `forbidden-character`
before any of these.

The scanner's reason codes are, in the order of this section: `forbidden-character`,
`root-not-div`, `multiple-roots`, `text-outside-root`, `uppercase-element`,
`unknown-element`, `void-element`, `script-content`, `unmappable-script`, `table-content`,
`table-shape`, `table-structure`, `table-section-order`, `misnested-tag`,
`soft-hyphen-at-boundary`, `forbidden-attribute`, `comment`, `processing-instruction`,
`cdata`, `doctype`, `malformed-tag`, `stray-lt`, `stray-amp`, `unknown-entity`,
`unbalanced-tag`.

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
- A section may omit words but never begin or end inside one. After all spans resolve, the
  first span's start and the last span's end are checked; a cut is `invalid-provenance` with
  reason `word-cut`.
  - Start. Read backwards from the code point before the first span's start offset through
    page n's body. On passing its bodyStart, continue from the last code point of page n−1's
    body, and so on through earlier pages. Only body text is read, and earlier pages are read
    as declared even if they fail §1 or §2. Skip code points in the §3 step 5 whitespace list
    and stop at the first other code point c. The result is word-cut if c is U+00AD, or if c
    is a word character and no code point was skipped. If reading passes the start of page 1's
    body, there is no cut.
  - End. The result is word-cut if the last span, with trailing §3 step 5 whitespace removed,
    ends in U+00AD, or if the code point at its end offset is a word character and lies inside
    the body.
  - Word characters are Unicode letters, digits, and combining marks (`\p{L}`, `\p{N}`,
    `\p{M}`), the step 1 invisible characters, U+200C, and U+200D; whitespace and punctuation
    are boundaries.

  So a section cannot begin at "safe for pregnant women" when the page before ends "un" and
  U+00AD, whether a line break, a blank line, or a page break with a blank head lies between;
  and it cannot end after "intra", U+00AD and a space.

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
  code (section 5, including `forbidden-character` for the `div` or a decoded reference),
  `forbidden-character` (section 2 on the extracted text), or `empty-narrative`; span
  resolution fails → the status and reason below; normalised source and narrative differ →
  `mismatch`; else `verified`.
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
  implementation's own, page numbers that are not 1, 2, …, N in array order (section 1, which
  includes a duplicate page number and a missing page), a page whose body range is not a valid
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
- decode the text layer as Unicode, never through Latin-1 or windows-1252: page text contains
  no section 2 character. Page text contains no U+000B or U+000C; a page break is the page
  record, not a character;
- emit table cells row-major separated by U+0009 and rows by U+000A, with the same number of
  cells in every row: a spanned cell's text is emitted once, in its first slot, and each other
  slot it covers as an empty cell; a line break inside a cell is emitted as U+0020, except a
  discretionary hyphen, which is U+00AD U+000A;
- in a raised or lowered glyph run, emit every digit and sign of section 5's folding tables as
  its script code point — raised: U+0030–U+0039 as U+2070, U+00B9, U+00B2, U+00B3,
  U+2074–U+2079, `+`, U+FE62 and U+FF0B as U+207A, `-`, U+2212, U+2010–U+2014, U+FE63 and
  U+FF0D as U+207B, `=` as U+207C, `(` and `)` as U+207D and U+207E; lowered: the same
  characters as U+2080–U+2089 and U+208A–U+208E — and every other character as it is. An
  extractor that cannot tell a glyph's baseline shift is unverifiable and must refuse the
  document (emit no SourceDocumentText) rather than emit plain digits: the narrative is
  derived from the page text, so if the text layer flattens `10⁹` to `109`, both sides say
  `109` and the check passes against a document that shows `10⁹`;
- emit discretionary (line-break) hyphens as U+00AD and hard hyphens verbatim;
- declare `bodyStart`/`bodyEnd` per page so repeated headers and footers are excluded, with
  every non-empty body ending in its final line terminator, including at the end of the page
  (section 1), and never exclude more than a running header and footer;
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

- `fidelity-norm/2.0.0` (major) — markup may not change what a reader sees without the check
  seeing it (`docs/design/fidelity-norm-2-0-0.md`, as amended by its independent re-review).
  Only `br` and `hr` may be self-closing, and must be (`void-element`); digits and signs inside
  `sup` and `sub` fold to script code points, other numbers and U+00B1, U+2213 there reject
  (`unmappable-script`), and `sup`/`sub` hold no element (`script-content`) (section 5); U+00AD
  before a line break anywhere in the emitted narrative text rejects, decided after the scan
  (section 5); the section-edge rules read back through whitespace and across pages, pages are
  numbered 1..N, and a non-empty body ends with its own line terminator even at the end of a
  page (sections 1 and 6); section 2 adds U+000B, U+000C, the C1 controls and the bidirectional
  controls on both sides, applies to the whole `div` as decoded from JSON before the scan and to
  each decoded reference on its own, and U+000B, U+000C and U+0085 leave the section 3
  whitespace list; `class` and `id` are removed, `lang` and `xml:lang` are allowed on the root
  only, `href` has only its `https://` form and `scope` is allowed on `th` only (section 5);
  tables contain only table parts and rows of one width (`table-content`, `table-shape`), and
  `colspan`, `rowspan` and `pre` are removed (section 5); the order in which violations are
  decided is stated in full (section 5); and the extractor contract gains the script,
  unverifiable-document, table-slot and page-break rules (section 7). Major under section 8:
  extractor output that conformed to 1.1.1 (plain digits in a raised run, uneven table rows, a
  final line without its line feed) no longer does, and spans that were valid (a section
  beginning after a blank line that follows a hyphenated word) become `word-cut`. The changed
  vectors are listed with their reasons in
  `docs/validation/changes/2026-09-23-fidelity-norm-2-0-0.md`.
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
