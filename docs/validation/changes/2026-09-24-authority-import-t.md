# Recorded change: the authority importer's T, importer 2.0.0, 2026-09-24

_Follows the procedure (steps 0–8) in `docs/validation/README.md`, "Change control for shared,
evidenced libraries". The UR- rows it cites are in that file._

**What changed.** The authority importer (`src/authority/`) moves from 1.1.0 to 2.0.0: T, ADR
0005's lexical transform, fills its closed lists as `docs/design/authority-import-t.md` decides
(T1–T7). T reads a section's div into a tree over the fidelity scanner's own tokens
(`src/authority/t/tree.ts`), holds the authority's tags to rules under which the HTML parser's tree
and the markup's nesting agree, computes the style a browser draws each element with from its
inline styles (`src/authority/t/style.ts`, `css.ts`), judges it by closed lists (attributes,
elements, CSS properties and values, contrast, offsets, line height, font sizes, lists, tables,
raised and lowered runs, underlines; `src/authority/t/transform.ts`), and writes T(div) by editing
the div string: attributes deleted, `span`, `u` and `a` unwrapped, a raised or lowered `span`
renamed `sup` or `sub`. The underline allowlist is `zone_a.underline`, ported
(`src/authority/underline.ts`, held to the Python by 123 shared cases). A document is transformed
in two passes for T5's one stated exception (an underlined `+` in a wholly underlined subheading,
on evidence from the same document; `src/authority/t/document.ts`). The importer gains a last
stage, `rendering`, which refuses every publication but a synthetic one until PR 3c's renderer
gate exists. The fidelity scanner exports its token grammar and entity table (no change of
behaviour). `labels/ema-epi/` pins the Imatinib Teva film-coated tablets SmPC (the owner's
end-to-end label) with its QRD check result.

**Why.** Roadmap 3a, PR 3: PR 2's T removed nothing, so every real section refused. The design
was reviewed fourteen times and its code four times (so far); the reviews are recorded in the design
note.

**Impact assessment (step 0).** `src/authority/` is imported by the worker's gate (Zone B
recomputes every import) and by the producer script. The contract does not change
(`CanonicalSubmission` stays 2.0.0). Imports stay dry-run (PR 5), and the `rendering` stage
refuses every real one, so no record changes. The synthetic publication imports as before; its
submission hash moves with the importer's version.

**Steps 1–6.** 1: `IMPORTER_VERSION` 2.0.0, locked (`npm run authority:lock`). 2: the importer's
vectors (`test/fixtures/authority/vectors.json`) now hold the imports, T's outcome for every
section of every pinned label (its T(div) hash or its refusal), and T's 222 cases
(`test/fixtures/authority/t-cases.ts`, each rule's both sides and every refusal reason, the
design reviews' repros among them). 3: the imports' outcomes are unchanged but the synthetic
hash; the tablets SmPC is added and stops at `pictures`, as the capsules' does. 4: the T cases
and the tests in `test/authority/`. 5: ADR 0003 (the stated exception), ADR 0005 (T's lists and
the renderer gate), the contract design's check order. 6: no UR row's evidence changes.

**What the pinned labels give.** Both Imatinib Teva SmPCs pass T in 31 of 32 sections; 5.1
refuses (`offset`: its tables need the renderer), with 4.2's two subheadings passing on the
plus-sign exception, read from 4.1's plain "Ph+". Brukinsa's sections refuse mostly on `font`
(Verdana, which the gate cannot yet draw); Jentadueto's on `markup` and `list`; Nuvaxovid's pass
but four. The imports themselves stop earlier, as before.

**Step 7.** No approved submission is affected: no real import has been approved.

**Approval (step 8).** Not obtained: author and releaser are the same identity. The independent
reviews are recorded in the design note's "Reviews".
