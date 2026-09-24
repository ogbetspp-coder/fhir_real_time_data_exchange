# `fidelity-norm/3.1.0`: ½ and ∞ inside `sub`

- Status: proposed, 2026-09-24 (roadmap 3a, PR 3's first step; ADR 0005 decision 1)
- Changes: `docs/fidelity-normalization.md` section 5 (two rules and the error order), section 7
  (one sentence) and section 9

## Why

ADR 0005 decision 1 records that the pinned Imatinib Teva SmPC refuses two sections under
`fidelity-norm/3.0.0` for one reason: 5.2 writes the half-life as `t<sub>½</sub>`, and 4.5 writes
`AUC<sub>(0-∞)</sub>`. Section 5 refuses, inside `sup` or `sub`, every number that is not a
target of the element's own fold table and every mathematical symbol outside the fold tables
(`unmappable-script`), so ½ (general category No) and ∞ (Sm) refuse. The ADR leaves the choice
to "a proposed minor version of the fidelity contract, reviewed like this one, if the lowered
forms are to be accepted". This is that version.

What the EMA's published labels hold. Every English SmPC the EMA's ePI service lists today (21
documents in 23 product Lists, fetched 2026-09-24 for the authority-import design) was scanned
for code points inside `sub` or `sup` that section 5 refuses:

| Code point | Inside | Count | Labels                                          | Written as                 |
| ---------- | ------ | ----- | ----------------------------------------------- | -------------------------- |
| U+00BD ½   | `sub`  | 6     | Imatinib Teva (2 SmPCs), Brukinsa, Vitrakvi (3) | `t<sub>½</sub>`, half-life |
| U+221E ∞   | `sub`  | 2     | Imatinib Teva (2 SmPCs)                         | `AUC<sub>(0-∞)</sub>`      |
| U+00B1 ±   | `sup`  | 9     | Brukinsa                                        | a footnote mark after "†"  |

(U+2011 NON-BREAKING HYPHEN also appears inside `sub`; it already folds to U+208B.) The
Imatinib Teva film-coated tablets SmPC, which the owner-chosen product List also covers, has no
other script refusal: with this change its narrative stops refusing in 4.5 and 5.2.

## The change

Inside `sub` only, U+221E INFINITY is kept unchanged, as letters and marks already are:
`AUC<sub>(0-∞)</sub>` reads `AUC₍₀₋∞₎` (the brackets, digit and hyphen fold as before). U+00BD
VULGAR FRACTION ONE HALF is kept too, but only under the **lowered-half rule**: the `sub`'s whole
emitted content is the one code point ½, the first drawn code point before it is a letter, and the
first drawn code point after it is neither a number nor a script sign (U+207A–U+207E,
U+208A–U+208E). "Drawn" reads past Default_Ignorable code points that are not marks, as the mark
rule of 3.0.0 does. `t<sub>½</sub>` reads `t½`; `1<sub>½</sub>`, `log<sub>2½</sub>`,
`x<sub>-½</sub>`, `t<sub>½</sub>2` and `<sub>½</sub>` at a line start refuse
(`unmappable-script`). The rule is checked after the scan, `sub` by `sub` in document order, and
before `combining-across-markup`, so every error the scan finds wins over it, wherever it is.
Inside `sup` both code points still refuse. Every other code point's treatment is unchanged: every
other fraction (¼, ¾, U+2189) and every other symbol (U+29DC INCOMPLETE INFINITY) inside `sub`
still refuses.

In code: the sub rule's kept set (`own`) gains the two code points (`KEPT_IN_SUBSCRIPT` in
`src/fidelity/xhtml.ts` and `zone-a/src/zone_a/fidelity/xhtml.py`); the scanner records each
`sub` holding ½ and `checkLoweredHalves` / `_check_lowered_halves` applies the rule after the
scan. `NORMALIZATION_VERSION` becomes `fidelity-norm/3.1.0`.

Section 7 gains one sentence: an extractor refuses a document whose raised or lowered run holds
what section 5 refuses inside the corresponding element (a raised ½, a lowered ½ outside the
rule), since the page text cannot carry its position. Drawn documents are not qualified, so this
states the rule for the version that qualifies them.

## Why this is safe

ADR 0003 accepts false failures and refuses false passes. The rule that refuses unmappable
script content exists because position can be meaning: `10<sup>6</sup>` is not `106`, so the text
carries the position of every digit and sign that has a script form, and refuses one that has
none, rather than lose the position. The question is whether losing the position of a lowered ½
or ∞ can make the check pass text a reader reads differently.

1. **No new text.** Section 5 emits ½ and ∞ unchanged, so the normalised text `t½` or
   `AUC₍₀₋∞₎` is text a narrative could already carry under 3.0.0 by writing the code points on
   the line. 3.1.0 adds a markup form for existing text, never a text. Sections 3 and 6 (the
   normalisation and every quote-edge, digit-group and sign rule) therefore see nothing they
   have not seen.
2. **∞ has one reading wherever it is.** It is never part of a number, so which run it belongs to
   does not change what is read: `AUC<sub>(0-</sub>∞<sub>)</sub>` and `AUC<sub>(0-∞)</sub>` say
   the same.
3. **½ is a number, so the danger is joining, and the rule removes it.** The first review found
   that ½ kept anywhere in `sub` loses which run it belongs to: `log<sub>2½</sub>` (logarithm to
   the base 2½) and `log<sub>2</sub>½` (log₂ of ½, that is −1) both read `log₂½`;
   `CaSO<sub>4·½</sub>H` and the hemihydrate `CaSO<sub>4</sub>·½H` both read `CaSO₄·½H`. A ½
   that is a `sub`'s whole content, after a letter and before no number or script sign, has no
   number to join on either side, so the text `t½` has one reading: a lowered half after a
   letter, which in a label is the half-life. Against a plain `t½` on the line it verifies, as
   `C<sub>max</sub>` verifies against `Cmax` (section 5's stated residual for letters, ADR 0003).
4. **Every narrative 3.0.0 accepts reads the same.** The change only turns refusals into text; no
   accepted input's text, status or reason moves. The 591 vectors of 3.0.0 show it: none changes
   except in the version string and the hashes that embed it.

## Alternatives rejected

- **½ anywhere in `sub`** (this design's first draft). Refused by the first review: the joining
  above.
- **Every vulgar fraction, and every symbol, inside `sub`.** Not needed by any label, and each
  would need its own argument. The list stays closed and is widened only on evidence, as 3.0.0's
  lists were.
- **T rewrites the source instead** (`t<sub>½</sub>` to `t½` in the import). T may drop only
  what cannot change the drawn page (ADR 0005), and the lowering is drawn; the contract is the
  place to say when lowering changes nothing a reader reads.
- **± inside `sup`** (Brukinsa's footnote mark). A raised ± after a number is an exponent sign,
  and Brukinsa has other refusals; not taken.

## Consequences

- Minor version (section 8): inputs refused under 3.0.0 now give text, pinned by new vectors.
  Twenty XHTML vectors are added (591 → 611): ½ kept (literal, decimal and hex reference, after
  markup and a word joiner, before a space) and refused (after a digit or a sign, at a line start, joined to an index, in a formula, with a space in the element, before a digit past
  a word joiner, before a subscript digit and a superscript sign), ∞ kept among folded signs,
  both refused inside `sup`, the neighbours ¼, U+2189 and U+29DC refused inside `sub`, and the
  rule's place in the error order (a later scan error wins; the rule precedes
  `combining-across-markup`). The differential generator gains two classes (the two code points
  and their neighbours in both scripts; lowered halves between letters, digits, signs, spaces and
  joiners), both required in the full corpus.
- The authority importer's golden vectors embed the version, so its lock requires a new version:
  `IMPORTER_VERSION` 1.1.0 (it now also accepts the two forms).
- Previously approved submissions need re-approval (section 8). The dev store's demonstration
  records were approved under 3.0.0; their narratives hold neither form, so their text is the
  same under 3.1.0 and re-seeding reproduces them.

## Reviews

1. **First independent review** (2026-09-24). High: ½ kept anywhere in `sub` joins a number
   (`log<sub>2½</sub>` against `log<sub>2</sub>½`), and the query service's `verify_quote` would
   match "log₂½" on the line against a label drawing log to the base 2½. Fixed: the lowered-half
   rule. Medium: the spec's "every other fraction, and every other symbol" contradicted the kept
   set (® and `/` are kept). Fixed: "every other code point of general category N, Sm, Ps, Pe or
   Pd". Low: this note's claim that section 7 already agreed proved too much (it keeps a raised ½
   too; section 7 now refuses it); the vector count; unreproducible figures in the change record;
   the differential's new class not required and ½ still drawn in its "unmappable" pool; the
   spec still saying "3.0.0 qualifies". Each fixed.
