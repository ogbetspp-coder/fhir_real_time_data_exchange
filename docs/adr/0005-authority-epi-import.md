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
   built mechanically from the authority's div. It keeps the same words, paragraphs, lists (with
   their `type` and `start`), tables (with their spans), pictures (with their `src` verbatim),
   raised and lowered text (as `sup` and `sub`), and bold, italic and underline. Every style,
   class and other presentational attribute is dropped. Nothing is added, reordered or reworded.

   A mark that changes what the words say, or whether they are seen, is never dropped:
   - raised and lowered runs are kept as `sup` and `sub`, and folded on both sides;
   - a section with struck-through text, faint text (white, nearly white, or under two points),
     a text colour or a shading refuses the import. Colour against shading can hide text as
     surely as white on white, and a colour rule that proved contrast would be a new contract.

   The authority's div is read by the ePI reader (`zone_a.epi.reader`), acting as an extractor
   under `docs/fidelity-normalization.md` §7 (fidelity-norm/3.0.0):
   - it draws list numbers with §5's counter algorithm, and refuses an `li` that is not a
     direct child of `ol` or `ul`;
   - it lays tables out by the HTML table model and emits the grid markers;
   - it emits a picture as U+FFFC and the hash of its `src`;
   - it folds raised and lowered digits and signs.

   Its text is one page per section, in the authority's order (§7, structured sources), and
   each narrative section's provenance span covers its page. The unchanged verifier (§1, §6)
   then proves, section by section, that the clean div reads exactly as the authority's div
   draws. Where it does not, the import fails before anything is written. The authority's file
   stays pinned by hash, and the provenance records the hash of every source div.
   `AGENTS.md`'s "Preserve supplied XHTML" is reworded to say this: the content and structure a
   reader sees are preserved and proved, and presentation is not.

2. **A text-only (Type 1) record is allowed for an authority import.** The graph holds the
   Composition, a MedicinalProductDefinition with the product name, the marketing authorisation
   holder as an Organization, and a RegulatedAuthorization carrying the authority's procedure
   number, all taken from the authority's structured index (the EMA List), never from narrative.
   Packs, ingredients and substances are declared not supplied. This is the HL7 Global ePI
   Type 1 case; Type 2 submissions stay exactly as strict.

3. **An authority's defects are refused and recorded, never repaired.** A section whose code and
   title disagree with the mapping, a section the reader refuses, or an unmapped code carrying
   narrative fails the import; it is recorded as a finding of the conformance check. A picture's
   source reference is kept verbatim, even when it cannot be resolved.

4. **The approval is the authority's publication.** The submission's approval names the
   authority's publication (ePI identifier and procedure number), not an approval of ours.
   Re-approval under `docs/fidelity-normalization.md` §8 is therefore re-import. When the
   normalisation version changes, the import is run again from the same pinned publication and
   must verify again. If the authority publishes a new version, that is a new import.

## Consequences

- The fidelity contract gains numbered lists, table grids with spans, and pictures bound to
  their source (`fidelity-norm/3.0.0`), because real labels have them. Each is folded into the
  text the check compares, or refused, as ADR 0003 requires.
- The canonical submission contract gains a FHIR source (`application/fhir+json`) with one
  page per source section, the Type 1 graph, and the authority-publication approval. ADR 0001
  is amended to allow the Type 1 graph for an authority import only.
- The round trip compares our EMA ePI with the authority's: the same sections, codes, titles and
  order, and the same text in every section as the ePI reader reads it. Presentation is not
  compared.
- A label with any refused or mis-coded section cannot be imported whole until the authority
  corrects it; Imatinib Teva was chosen because its sections are all coded correctly.
