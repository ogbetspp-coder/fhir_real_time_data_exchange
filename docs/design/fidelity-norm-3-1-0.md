# `fidelity-norm/3.1.0`: ½ and ∞ inside `sub`

- Status: proposed, 2026-09-24 (roadmap 3a, PR 3's first step; ADR 0005 decision 1)
- Changes: `docs/fidelity-normalization.md` section 5 (one rule) and section 9; nothing else

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

Inside `sub` only, U+00BD VULGAR FRACTION ONE HALF and U+221E INFINITY are kept unchanged, as
letters and marks already are. `t<sub>½</sub>` reads `t½`; `AUC<sub>(0-∞)</sub>` reads
`AUC₍₀₋∞₎` (the brackets, digit and hyphen fold as before). Inside `sup` both still refuse.
Every other code point's treatment is unchanged, so every other fraction (¼, ¾, U+2189) and
every other symbol (U+29DC INCOMPLETE INFINITY) inside `sub` still refuses.

In code: the sub rule's kept set (`own`) gains the two code points (`KEPT_IN_SUBSCRIPT` in
`src/fidelity/xhtml.ts` and `zone-a/src/zone_a/fidelity/xhtml.py`). `NORMALIZATION_VERSION`
becomes `fidelity-norm/3.1.0`.

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
   have not seen; a lowered ½ or ∞ joins its neighbours exactly as the same code point on the
   line does.
2. **What is lost is only the lowering, and lowered they say the same.** The check now equates
   `t<sub>½</sub>` with `t½` on the line, as it already equates `C<sub>max</sub>` with `Cmax`
   (section 5's stated residual for letters and marks, ADR 0003). Neither ½ nor ∞ has a
   subscript meaning of its own: no notation reads a lowered ½ as other than one half, or a
   lowered ∞ as other than infinity. `1<sub>½</sub>` is drawn as "1" and a small low "½", which
   reads as "1½" as the same text on the line does. Raised is different: `2<sup>½</sup>` is the
   square root of 2, not "2½", and `10<sup>∞</sup>` is not "10∞"; both still refuse.
3. **Every text 3.0.0 accepts reads the same.** The change only turns a refusal into text; no
   accepted input's text, status or reason moves. The existing 600 vectors show it: none
   changes except in the version string and the hashes that embed it.
4. **The extractor contract already agrees.** Section 7 says an extractor emits, in a lowered
   run, "every other character as it is"; so a drawn source that lowers ½ gives `t½`, the
   scanner's reading of the narrative.

## Alternatives rejected

- **Every vulgar fraction, and every symbol, inside `sub`.** Not needed by any label, and each
  would need its own argument (a lowered `⅟` or a lowered `′` may read differently). The list
  stays closed and is widened only on evidence, as 3.0.0's lists were.
- **Only after a letter** (`t<sub>½</sub>` but not `1<sub>½</sub>`). Point 2 finds no reading
  that the adjacency would protect, and adjacency across markup is where fidelity-norm/3.0.0's
  reviews found most of their defects (the mark rules, rounds 14 and 15). No real label has the
  refused form, so the rule would guard nothing and cost a second rule in two languages.
- **T rewrites the source instead** (`t<sub>½</sub>` to `t½` in the import). T may drop only
  what cannot change the drawn page (ADR 0005), and the lowering is drawn; the contract is the
  place to say that lowering these two code points changes nothing a reader reads.
- **± inside `sup`** (Brukinsa's footnote mark). A raised ± after a number is an exponent sign,
  and Brukinsa has other refusals; not taken.

## Consequences

- Minor version (section 8: at least one vector's outcome changes). Nine vectors are added: ½
  and ∞ kept inside `sub` (literal and as references, after a letter and after a digit, among
  folded signs), and still refused inside `sup` (literal and reference), a neighbouring
  fraction (¼, U+2189) and a neighbouring symbol (U+29DC) inside `sub`. The differential
  generator gains a class drawing ½, ∞, ¼ and U+29DC (literal and as references) inside both
  scripts.
- The authority importer's golden vectors embed the version, so its lock requires a new version:
  `IMPORTER_VERSION` 1.1.0 (it now also accepts the two forms).
- Previously approved submissions need re-approval (section 8). The dev store's demonstration
  records were approved under 3.0.0; their narratives hold neither form, so their text is the
  same under 3.1.0 and re-seeding reproduces them.
