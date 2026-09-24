# ADR 0005: Importing an authority's published ePI into the canonical record

- Status: Accepted (owner decisions of 2026-09-23)
- Date: 2026-09-23
- Related: ADR 0001 (Global ePI Type 2 canonical), ADR 0002 (two trust zones), ADR 0003
  (mechanical narrative fidelity), `docs/roadmap.md` item 3a,
  `docs/design/fidelity-norm-3-0-0.md`, `docs/design/qrd-conformance-check.md`

## Context

Roadmap item 3a takes one real, authority-published label through the whole system: into the
canonical record, back out as our EMA ePI, compared with the authority's own, and answered by
the agent with proof. The source is the EMA's ePI API (`labels/ema-epi/`), and the first label is
the English summary of product characteristics of Imatinib Teva.

Three facts about that source meet three rules of the record:

1. The EMA's section narratives carry presentation markup on nearly every element — inline CSS,
   classes, table borders — which the fidelity contract refuses, while `AGENTS.md` says
   "Preserve supplied XHTML".
2. The EMA's summary Bundle holds only a Composition. The canonical record (ADR 0001) is a Global
   ePI Type 2 graph of nine resource types; product name, marketing authorisation holder and
   procedure number are in the EMA's List, and packs, ingredients and substances are only in the
   narrative.
3. The EMA's own files contain defects (`docs/design/qrd-conformance-check.md`): sections coded
   with another section's code, template brackets left in headings, broken image references.

## Decision

1. **Presentation is not narrative.** An authority import enters the record as clean XHTML
   built mechanically from the authority's div. It keeps the same words, paragraphs, line breaks,
   lists (with their `type` and `start`), tables (with their spans), pictures (decision 3), and
   raised and lowered text (as `sup` and `sub`). Presentation that cannot change what a reader
   sees is dropped. Nothing is added, reordered or reworded.

   **The page is the scanner's text of a transformed div.** For each section, a stated lexical
   transform T edits the authority's div string itself (no parse and re-serialise, which would
   lose the refusals below), and the page is exactly what §5's scanner code emits for T(div): one
   page per section, as `docs/fidelity-normalization.md` §7 defines for a structured source. T
   does only four things: it deletes attributes and CSS declarations on a closed list; it
   unwraps a `span` left with no attributes, and a link, keeping their text; it rewrites a
   raised or lowered run as `sup` or `sub`; and it replaces a referenced picture with its pinned
   `data:` URI, or deletes it and records it (decision 3). Where the scanner refuses
   T(div), where T meets anything not on its lists, or where the div's text (character
   references decoded) holds U+00AD or another §3 step 1 invisible character, the section
   refuses. The clean div stored in the record is T(div), so the fidelity check compares the
   record with the page by construction; what makes the import trustworthy is that T drops only
   what cannot change the drawn page, which the requirements below and the renderer cross-check
   secure.

   **Requirements on T**, designed and reviewed in PR 3 against the pinned label:
   - **Closed attribute list, by element and value.** What may be deleted is named (for example
     `valign`, `align`, `border`, `cellspacing`, `cellpadding`, `width` on table parts, `nowrap`,
     `lang`, `title`, `id`, `a@name`, `hr@size`), and every other attribute refuses: in
     particular `dir` other than `ltr` (`<p dir="rtl">10 mg or 20 mg</p>` draws "mg or 20 mg
     10"), `bgcolor`, `background`, `hidden`, `li@value`, `ol@reversed`, `type` outside a list,
     and `font@color`, `face` and `size`. No element is unwrapped or removed except a `span`
     left with no attributes, and a link, whose text is kept and whose target is not drawn (and
     beyond unwrapping, T renames a raised or lowered run as `sup` or `sub` and deletes a picture
     only as decision 3 allows); any
     other element the scanner refuses (`font`, `center`, `bdo`, `ruby`, `q`, `ins`, `del`, `s`)
     refuses the section.
   - **Closed CSS list, by property and value, that can neither hide nor overprint text.** Font
     families only from a closed list of Unicode text fonts, every family in the list on it
     (symbol-encoded fonts such as Symbol and Wingdings refuse); font sizes in absolute units
     only, with the effective size at least two points; opaque text colours; margins, padding
     and indents never negative (except a hanging indent that keeps the line start on the page),
     vertical margins never negative; borders only on table parts, thin and dark; an inline
     background only with no padding; `line-height` at least normal; `width` and `height` only
     on table parts and pictures; `vertical-align: baseline` only outside `sup` and `sub`, and
     `top`, `middle`, `bottom` only on table cells; a picture's `width` and `height` no smaller
     than a stated bound, so a picture the authority draws at a pixel is not carried at full
     size. Struck, hidden, faint (white, nearly white, under two points or well under the
     surrounding text) or transparent text refuses, and so do `opacity`, `clip`, `overflow`,
     `display`, `position` other than a folded vertical offset, `content`, `text-transform`,
     `list-style-type` and every unlisted property.
   - **Colour.** A text colour and a shading are dropped only where the text keeps a stated
     contrast with its effective background (for example WCAG's 4.5:1), since colour hides text
     only through contrast; otherwise the section refuses.
   - **Raised and lowered runs.** Each glyph's total baseline shift is summed over every
     ancestor's `vertical-align`, relative `top` or `bottom`, `sup` and `sub`, and measured
     against its effective font size: at least a stated fraction, the glyph is folded as `sup`
     or `sub`; below a stated bound, the offsets are deleted (the label's 18 runs at
     `top: .5pt` in 5.1 must not turn "(" into "₍"); between the two, the section refuses.
     Offsets are never judged run by run, since nested small offsets add up to a superscript.
   - **Empty sections.** A section draws nothing when §5's `empty-narrative` test holds for the
     scanner's text of T(div) (the label's `<div>&#160;</div>` heading sections): it gets its
     page but no narrative and no span. Every page without a span must normalise to nothing (the
     report's uncovered gaps are zero), enforced by the importer and by PR 2's gate, because the
     verifier alone reads coverage as evidence, not as a failure.

   Two checks close what the equality cannot see, because T could itself drop something drawn:
   - **A renderer cross-check.** In CI, every pinned publication is drawn by a headless browser
     with its inline styles and presentational attributes applied and without the authority's
     class stylesheet, in HTML mode (as a browser draws the EMA's div) and in XML mode. The list
     numbers (from the accessibility tree), each table's grid (from cell rectangles), the text,
     each text box's visibility (on the page and not overlapped), each glyph's drawn baseline
     shift against whether the page folds it, and each picture's drawn box are compared with the
     page. A difference fails the build.
   - **A line check.** Inside every table cell, T(div) must hold the same sequence of line breaks
     and paragraphs as the authority's div, compared structurally. The fidelity contract does
     not compare the line a value sits on inside a cell (§5, a stated residual), and an import
     is where both sides' markup is at hand.

   Not proved, and stated: the authority's own stylesheet (`class` values are dropped and the
   EMA's stylesheet is not applied); paragraph breaks, headings, bullets, list nesting and
   emphasis, which are kept but compared only by the line check and the round trip (PR 4).

   **What the first label already shows.** The eleventh review of `fidelity-norm/3.0.0` ran the
   pinned Imatinib Teva SmPC through the scanner with its presentation loosely stripped: 6 of its
   32 sections refuse, and these requirements would add the QRD grey shading of the reporting
   section and two coloured passages. The refusals are a `span` left inside a `sup` (unwrapped
   under the attribute rule above); the QRD Appendix V link, whose path is longer than §5's href
   grammar allows and which is in every EU SmPC, and an `http:` link in section 10 (links are
   unwrapped, so neither reaches the scanner); `t` with a lowered `½` and `AUC` with a lowered
   `(0-∞)`, which §5 refuses inside `sub` and which nearly every label has in 5.2; one
   `table-shape` in 5.1, not yet explained; and section 10's `margin: 0cm -0.1pt …`, which the
   rule on negative margins refuses. PR 3 settles each: the unwrapping above, the contrast
   rule, the table investigated (an EMA defect is refused and recorded, decision 3), and, for `½`
   and `∞` in `sub`, a proposed minor version of the fidelity contract, reviewed like this one,
   if the lowered forms are to be accepted. Until then the label cannot be imported whole.

2. **A text-only (Type 1) record is allowed for an authority import.** The graph holds the
   Composition, a MedicinalProductDefinition with the product name, the marketing authorisation
   holder as an Organization, and a RegulatedAuthorization carrying the authority's procedure
   number, all taken from the authority's structured index (the EMA List), never from narrative.
   Packs, ingredients and substances are declared not supplied. This is the HL7 Global ePI
   Type 1 case; Type 2 submissions stay exactly as strict.

3. **An authority's defects are refused and recorded, never repaired.** A section whose code and
   title disagree with the mapping, a section the reader refuses, or an unmapped code carrying
   narrative fails the import; it is recorded as a finding of the conformance check.

   Pictures are carried as their bytes, never as a reference: a reference draws whatever the
   viewer's origin serves at that path, or nothing, and the fidelity contract accepts only a
   `data:` URI (fidelity-norm/3.0.0 §5). A picture the authority embeds is carried as it is. A
   picture the authority references is fetched from the authority, pinned by hash beside the
   publication, and carried as a `data:` URI. The reference is resolved against the base URL the
   authority publishes the document under. A picture is deleted only on pinned evidence that the
   authority's own viewer draws nothing for it (the EMA's `~/_entity/annotation/…` references
   return "not found" from its ePI service and draw at zero size in a browser): it is not
   carried, the reader emits nothing for it, and the import records it, with its reference,
   section and the evidence, as a finding. That is not a repair: the record shows what every
   reader of the authority's publication sees. Any other failure to fetch a picture fails the
   import, since it says nothing about what readers see. A fetched picture is carried only if its
   bytes are a PNG or JPEG file by signature, under the media type they are (anything else, a
   GIF, SVG or WebP labelled as PNG included, refuses the section), and only if the authority
   does not draw it much smaller than its own size (a large image drawn small would be carried
   at full size once T drops `width` and `height`).

4. **The approval is the authority's publication.** The submission's approval names the
   authority's publication (ePI identifier and procedure number), not an approval of ours.
   Re-approval under `docs/fidelity-normalization.md` §8 is therefore re-import. When the
   normalisation version changes, the import is run again from the same pinned publication and
   must verify again. If the authority publishes a new version, that is a new import.

## Consequences

- The fidelity contract gains numbered lists, table grids with spans, and embedded pictures
  bound to their bytes (`fidelity-norm/3.0.0`), because real labels have them. Each is folded
  into the text the check compares, or refused, as ADR 0003 requires.
- The importer's CI job needs a headless browser for the renderer cross-check, pinned like
  every other tool.
- The canonical submission contract gains a FHIR source (`application/fhir+json`) with one
  page per source section, the Type 1 graph, and the authority-publication approval. ADR 0001
  is amended to allow the Type 1 graph for an authority import only.
- The round trip compares our EMA ePI with the authority's: the same sections, codes, titles and
  order, and the same text in every section as the ePI reader reads it. Presentation is not
  compared.
- A label with any refused or mis-coded section cannot be imported whole until the authority
  corrects it, or until PR 3's requirements above admit what it draws; Imatinib Teva was chosen
  because its sections are all coded correctly, and the refusals decision 1 records are PR 3's
  first work.
