# Recorded change: `fhir/mappings/cap-smpc-en.json` narrative flag on 4.8, 2026-09-22

_Moved verbatim from `docs/validation/README.md`, whose "Change control for shared, evidenced
libraries" section holds the procedure (steps 0–8) this record follows. The UR- rows and
section names it cites ("above", "Release criteria") are in that file.
"The previous record" and "the record above" mean [`fhir/mappings/cap-smpc-en.json` QRD coding displays and EMA List code, 2026-09-20](2026-09-20-mapping-qrd-displays-and-ema-list-code.md)._

**What changed.** The manifest's `mappingVersion` moved from `1.1.0` to `1.2.0`, and the rule
`smpc.4.8` (`200000029816`, "4.8 Undesirable effects") gained `"narrative": "required"`. The
loader `src/fhir/mapping.ts` accepts that one value and nothing else, and now also rejects a
manifest in which two rules share a `sourceKey` or a `targetCode`. The same branch makes the
crosswalk in `src/fhir/transform.ts` fail closed on what it used to drop or rearrange
(`docs/architecture.md`, "Deterministic data flow", lists every refusal).

**Why.** A mandatory leaf section must carry narrative; a section with child rules may be a
bare heading. 4.8 is the one section of the QRD CAP SmPC template with child rules whose own
text — the safety profile, the tabulated adverse reactions, the description of selected
reactions — sits above its subsection, "Reporting of suspected adverse reactions". Without the
flag an empty 4.8 above a filled reporting subsection published. The other rules with children
are headings over their subsections in the template and are not flagged: `smpc` (the document),
`smpc.4`, `smpc.5` and `smpc.6` (numbered part headings), and `smpc.4.2`, whose text sits under
its "Posology" and "Method of administration" subheadings. Every synthetic fixture carries
narrative on every section, flagged or not.

**Impact assessment (step 0).** Importers are unchanged from the previous record. What moved:

- `fhir/generated/ConceptMap-canonical-to-ema-cap-smpc-en.json` and
  `fhir/generated/StructureMap-type2-to-ema-cap-smpc-en.json`: `version` `1.1.0` → `1.2.0`,
  nothing else. The generator does not read the new flag.
- Future run manifests record `standards.mappingVersion` `1.2.0`, and lineage names the mapping
  `cap-smpc-en#1.2.0`.
- Unchanged, byte for byte: `contracts/generated/**`, `test/fixtures/contracts/*.json`,
  `test/fixtures/fidelity/vectors.json`, `zone-a/tests/fixtures/differential-smoke.jsonl`. The
  transform's `outputHash` and mapping decisions for every synthetic product and version (four
  products × two versions) are identical to those at `a281d9f`, and the validation set exported
  from the published HL7 DrugX example is byte-identical: no document the pipeline has
  published or would publish changes.
- Step 7 (re-approval): not needed. `mappingVersion` is not part of a submission's approved
  content, and no approved hash moved.

**Blast radius.** A source that the previous crosswalk accepted may now be refused: an empty
4.8; narrative in a mandatory leaf that the fidelity XHTML scanner cannot read (a comment, an
image, a named entity such as `&nbsp;`); a section carrying `entry`, `extension` or another
element the crosswalk does not carry; subsections out of manifest order; a missing or
non-English `language`. The synthetic fixtures and the published example meet all of these.

**Approval (step 8).** Not obtained, for the same reason as the record above.
