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
   unwraps a `span` left with no attributes, and a link or a `u` whose text an underline
   cannot change (below), keeping their text; it rewrites a
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
     left with no attributes, and a link or a `u`, whose text is kept and whose target is not
     drawn (and
     beyond unwrapping, T renames a raised or lowered run as `sup` or `sub` and deletes a picture
     only as decision 3 allows); any
     other element the scanner refuses (`font`, `center`, `bdo`, `ruby`, `q`, `ins`, `del`, `s`)
     refuses the section.
   - **Underlines are not unwrapped blindly.** The authority's viewer underlines a link with
     a target, a `u`, and any text under `text-decoration: underline` or
     `text-decoration-line: underline` on its element or an ancestor; and an underline turns what
     it underlines into another sign or word: `<u>&lt;</u>` is drawn "≤", `+` "±", `=` "≡", `-`
     nearly "=", U+02C2 exactly "≤", and `1<u>a</u>` "1ª" (the reviews of `fidelity-norm/3.0.0`
     that found it, rounds 16 and 17, are why §5 refuses `u` and `a` in a narrative).
     Unwrapping, or deleting the declaration, would keep the "<" and lose the "≤" a reader sees.
     So wherever the authority's div draws an underline, from any of these sources, T removes it
     only when every code point it covers is on a closed allowlist PR 3 sets against the
     renderer, and anything else refuses the section. The allowlist starts from
     `zone_a.underline`, the rule the QRD check reports by: letters and decimal digits of the
     Latin, Greek and Cyrillic scripts; spaces; the punctuation `. , ; : ( ) [ ] / ' " % @ _ &
     # ! ? *` and the curly quotation marks; a hyphen only between two letters; never another
     code point of category Sm (mathematical symbols) or Pd (dashes), a modifier letter, or a
     symbol. Adjacency is judged on the drawn text, the underlined run's neighbours outside the
     element included: an underlined lower-case letter directly after a number of any script
     (`1<u>a</u>`, `20<u>o</u>C`, read past code points drawn as nothing) refuses, since it is
     drawn as an ordinal indicator ("1ª", "20ºC"), and so does an underlined "o" after an "N"
     ("Nº"), look-alikes of each included. The QRD check already
     finds the case in a pinned label: Brukinsa's 5.1 writes ">1" with the ">" underlined,
     which the EMA's viewer draws as "≥1".
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
   under the attribute rule above); the QRD Appendix V link, whose path was longer than §5's href
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

## Amendment (2026-09-24, the canonical contract for an import)

`docs/design/authority-import-contract.md` (roadmap 3a, PR 2) makes the contract hold an import
(`CanonicalSubmission` 2.0.0, ADR 0002's amendment) and changes this ADR as follows.

- **Where the importer runs (D1).** The importer is shared TypeScript (`src/authority/`, under
  ADR 0004's amendment), not a Zone A component: a producer script (`scripts/authority/import.ts`)
  runs it to write a submission, and Zone B fetches the authority's bytes itself and runs it again,
  accepting only the submission it recomputes. The ePI reader stays in Zone A for the QRD
  conformance check.
- **Decision 1's T ships with empty closed lists.** In PR 2, T removes nothing: a section is
  carried only if the scanner accepts its div as it stands, and every other refuses. PR 3 fills
  the lists against the real label with the renderer cross-check; that changes the importer's
  version and vectors, not the contract.
- **The document must be the mapping's tree (D4).** Its section tree must equal the mapping's
  rule tree: the same sections, each with exactly one code (the EMA's SPOR system URI is a stated
  alias of the mapping's), under the same parent, in the same order, and nothing else. Each
  heading must be one the QRD template permits for its rule (the mapping lists them from 1.3.0:
  6.5 and 6.6 with and without their optional wording), and the record carries the document's
  heading. There is one page per section at every depth, in pre-order; a section without `text`
  has the empty page.
- **Decision 3's qualifier.** An unmapped, uncoded or doubly coded section refuses, empty or not,
  where decision 3 named only an unmapped code carrying narrative.
- **The List binds the document (D5).** A document is imported only with the EMA List that lists
  it: exactly one entry references `Bundle/<document id>`, its display is the document type's,
  `Composition.title` equals `List.title`, and the List is `current`. Both files are read by a
  closed shape enumerated from the four SmPCs and four Lists pinned in `labels/ema-epi/` (the
  design's Appendix A); anything else refuses. The EMA serialises FHIR codes as integers of its
  own (`language` `0` for English, `resourceType` and `status` `0`); the record's
  `Composition.status` is `final` by rule from the publication, and what the EMA's `0` status
  means is a stated residual.
- **Pictures (D6).** A document names a picture in one of four forms: a `data:` URI in the div;
  `#id` naming a `Binary` contained in the Composition (both in the document's bytes); a reference
  the authority's viewer resolves, fetched by the importer's template for its grammar and pinned;
  or a reference the viewer draws as nothing. A deletion is proven only by an evidence record of
  the authority's own viewer, made in a pinned browser and reviewed into the importer's data,
  never by a failed fetch: the EMA's service answers "Resource not found" for every unknown path,
  its own root included. So decision 3's statement that Imatinib Teva's `~/_entity/annotation/…`
  references are deleted on a not-found and a zero-size draw is withdrawn: whether its two
  pictures are drawn is unsettled until PR 3 measures them in the EMA's viewer and reconciles this
  ADR and `docs/design/fidelity-norm-3-0-0.md`. In PR 2 the importer refuses every picture, and
  Imatinib Teva stops there.
- **Decision 2's record (D9).** A Type 1 record is exactly one Composition,
  MedicinalProductDefinition, Organization and RegulatedAuthorization. Every value comes from the
  document or the List, or is a stated rule; none is derived from another value's content (the
  EMA product number is not read out of the procedure number, and EU numbers, strengths and forms
  stay in the narrative). The authority states no authorisation number in structured form, so the
  procedure number stands in for `RegulatedAuthorization.identifier`, stated; the
  MedicinalProductDefinition's identifier is the ePI's id, the product scope, not a product id.
- **Decision 4's approval and re-import (D2, D8).** The approval's method is
  `authority-publication`: it names the publication (ePI id, document, List, version number,
  procedure number, the Bundle's timestamp) and who requested the import. A re-import after a
  normalisation change is possible only while the authority still serves the same bytes for the
  document and its List (the List changes whenever any document of the product changes);
  otherwise it is a new import of the current publication, and the earlier record is superseded
  or withdrawn under roadmap item 2.

## Amendment (2026-09-24, `fidelity-norm/3.1.0`)

Decision 1 left `t` with a lowered `½` and `AUC` with a lowered `(0-∞)` to "a proposed minor
version of the fidelity contract". `fidelity-norm/3.1.0` (`docs/design/fidelity-norm-3-1-0.md`)
is that version: inside `sub`, U+221E is kept unchanged, as letters and marks are, and U+00BD
only as the half-life, `t<sub>½</sub>` with the `t` starting a word (section 5's lowered-half
rule; the form of every lowered ½ in the EMA's English labels);
inside `sup` both still refuse. Of the refusals decision 1 lists, those two are settled; the
rest stay PR 3's work. The importer's version moves to 1.1.0 with it (D10's lock).

One finding of the same survey is recorded here for PR 3: both Imatinib Teva SmPCs (capsules
and film-coated tablets) underline "Posology for Ph+ ALL in adult patients" and "Posology for
Ph+ ALL in children" (4.2) and "Clinical studies in Ph+ ALL" (5.1). An underlined "+" is drawn "±"
(decision 1's underline requirement), so under that requirement 4.2 and 5.1 refuse in both;
PR 3's design of T settles it.

## Amendment (2026-09-24, T's closed lists)

`docs/design/authority-import-t.md` (roadmap 3a, PR 3b) fills decision 1's lists, after fourteen
independent reviews, and changes this ADR as follows.

- **What T decides, and what the renderer measures.** T's rules are static and decide what the
  markup alone decides: which elements and characters, raised or lowered, underlined or not,
  colours against every background behind them, fonts, and offsets bounded so text stays in its
  box. Whether glyphs drawn at a given width and font touch, whether cells' text meets, whether a
  line runs under a sign: those the renderer cross-check measures, made a **gate** on every import
  (PR 3c) with the requirements the design lists, including a comparison of T's style model with
  the browser's for every text node. Until it exists, the importer's `rendering` stage refuses
  every publication but a synthetic one.
- **Decision 1's bounds change:** font sizes may be given in `em` and `%`, and the effective size
  is at least 5 pt (it said absolute units and 2 pt); line height at least the font size at every
  text node, not "at least normal", which would refuse the label's body text; borders at most
  3 pt at 3:1 contrast (it said thin and dark); offsets as the design's T3b states; and T also
  holds the authority's own tags to rules under which the HTML parser's tree and the markup's
  nesting agree.
- **One stated exception** to the underline requirement and to "T drops only what cannot change
  the drawn page": T5's plus sign in a wholly underlined subheading, on evidence from the same
  document (ADR 0003, amended).
- **What the pinned labels give:** both Imatinib Teva SmPCs pass T in 31 of their 32 sections;
  5.1 refuses (its tables need the renderer), and the import stops first at the pictures stage.

## Amendment (2026-09-25, the renderer gate and a withheld section)

`docs/design/authority-import-renderer.md` and `docs/design/authority-import-withheld.md`
(roadmap 3a, PR 3c; owner decisions of 2026-09-25) change this ADR as follows.

- **The renderer cross-check is a store of attested records.** A dedicated Cloud Build
  configuration, under its own identity, draws every publication in a pinned image (Chrome for
  Testing's headless shell, pinned metric-compatible fonts) from the importer's own pinned bytes,
  regenerates each committed record twice and signs an attestation only if both reproduce it. The
  image build that every deploy runs verifies each record's attestation offline against pinned
  public keys and drops a record without one. The importer's `rendering` stage looks the record up
  by the document's hash and each carried section's three output hashes (T(div), T's model output,
  the scanner's text) and pictures; the run manifest pins the record's hash. The consequence "the
  importer's CI job needs a headless browser" is that build, with a pre-check in CI.
- **Decision 3 is narrowed: a withheld section.** A leaf section whose drawing, as the authority's
  div draws with its inline styles, the renderer gate records as misleading a reader (two cells'
  text running together, a line through the body of a letter, a character wholly off the page),
  with no refusal of the gate's own, may be **withheld** by the person who requests the import;
  never a safety section (4.2 to 4.9), and never on a refusal or a conservative failure of ours.
  It keeps its heading and code, carries none of the authority's content, and carries our own
  `emptyReason` code and a fixed notice; the Composition is `partial` and the record is marked
  incomplete wherever it is read. The import refuses if a listed section is not shown so or a
  failing one is not listed, and a document-level refusal still refuses it. A withheld section is
  never repaired, answered from or quoted. Imatinib Teva's 5.1, whose at-risk table runs its
  values together, is the first.
- **Decision 3's pictures.** The EMA's viewer draws Imatinib Teva's two `~/_entity/annotation/…`
  pictures as broken images, and the EMA's FHIR export of the same List carries them as contained
  Binaries under the same ids. A reference is carried from the authority's own export of the same
  publication, bound by hash and by the export's text equalling the document's (PR 3c-E, its own
  note), which D6's "reference the authority's viewer resolves" did not foresee.
