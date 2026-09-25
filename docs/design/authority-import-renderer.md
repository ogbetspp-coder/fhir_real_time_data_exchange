# The renderer gate: a pinned browser's evidence for every authority import (roadmap 3a, PR 3c)

- Status: proposed, 2026-09-25 (architecture approved by the owner the same day)
- Decides: what `docs/design/authority-import-t.md`'s "What waits for PR 3c" left open: who draws,
  what is drawn and measured, the evidence record and its store, and the `rendering` stage
- Amends: ADR 0005 (decision 1's renderer cross-check and its consequence "the importer's CI job
  needs a headless browser"); `docs/design/authority-import-t.md` (the store and who writes it,
  settled; the frame tolerance); `docs/design/authority-import-contract.md` (D1's trust
  statement, D10's check order, D12's evidence, D13's versions)
- Related: ADR 0003, `docs/design/authority-import-withheld.md`, ADR 0004 (the importer lock)

## What this is for

T decides from the markup alone what the text is, and refuses presentation that can hide or alter
it in any layout. It cannot decide whether glyphs drawn at a given width and font touch, whether
two cells' text reads as one, or whether a line runs under a sign. So an import is accepted only
for a publication a pinned browser has drawn and found free of hidden, overlapping or displaced
text (T's opening; ADR 0005, amended 2026-09-24). This note designs that gate. Until it exists the
importer's `rendering` stage refuses every publication but a synthetic one.

## Decisions

### R1. Reproduced records: CI draws, a record is merged only if it reproduces, Zone B looks it up

- **Who draws.** One pinned renderer image (R6) and one TypeScript judge (`src/render/`) run in
  CI, never on the producer's machine and never from the producer's files: the job reads the
  publication's bytes from the pinned copies in `labels/` (checked against their pins) or from
  the authority by D1's fetch rules.
- **What it writes.** A small evidence record per publication (R8) in its own store,
  `src/render/records/`, outside `src/authority/`, so a new publication's record does not change
  the importer's version (D10's lock hashes `src/authority/`).
- **Who may write the store.** Records enter only by a reviewed merge to `main`, and the merge
  gate's `renderer` job draws every committed record again from reviewed code: a record whose
  layout does not reproduce exactly (R7) fails the build. So a record in the store is one the
  current code, in the pinned image, produces; the producer, whom D1 does not trust, never writes
  one, and a hand-edited record fails.
- **What Zone B does.** The `rendering` stage is a pure, synchronous lookup inside
  `importPublication`, with the store passed in as data (as the mapping is): D10's check order is
  unchanged, and the gate's recomputation (D1) repeats the lookup. The record's hash goes into the
  signed run manifest (R10), so D1's re-verification re-checks it.
- **Why not draw in the worker.** A browser in Zone B at gate time would recompute the drawing
  too, but it does not fit the worker's 1 GiB, needs its own network blocking (a Cloud Run sidecar
  shares the worker's network and identity), adds an image digest the manifest does not record,
  is invisible to the vulnerability scan (a pinned browser zip is not a lockfile), and would do
  nothing until PR 5 lifts the dry run. The judge is written so it can later run in the worker
  image unchanged, should the number of publications make a merge per publication impractical;
  that is a later decision, recorded here.

The cost is stated: every new publication needs a record merged before it can be imported. For
the EMA's current public corpus (five English SmPCs) that is no burden.

### R2. What is drawn

- **Two drawings of every section.** The authority's div as served, and T(div). Geometry is
  measured on the authority's drawing (T's output has no styles, so its geometry proves nothing);
  the text, list numbers, table grids and pictures are compared between the two.
- **The page.** A standards-mode HTML document holding nothing but the div: `<!DOCTYPE html>`, a
  `meta charset`, no stylesheet, and `<body style="margin:0">` whose only child is the div. The
  authority's class stylesheet is not applied (ADR 0005; T's model assumes the same). The EMA's
  viewer draws the portal's own HTML of the section inside a Verdana stylesheet, with the
  section's inline styles overriding it; that stylesheet is not the authority's publication and
  is not modelled (a stated residual, as today).
- **Two modes.** HTML (`text/html`, as a browser draws the EMA's div) and XML
  (`application/xhtml+xml`), as ADR 0005 requires.
- **Pictures.** A picture is drawn from the bytes the import carries (D6): the div's reference
  is replaced, for the drawing only, by the same `data:` URI T writes, so both drawings show the
  picture a reader of the record sees. A withheld section is drawn too, and its failure is its
  evidence (the withheld note's W1), but it is never required to pass.
- **Widths.** Every integer CSS width from 320 px to 1 280 px at device pixel ratio 1 (the EMA
  viewer's narrative column measured 813 px in a 1 280 px window and 1 240 px in a 1 920 px one;
  a phone's is 320 to 430 px), and, at 360 px and 813 px, device pixel ratios 2 and 3 (borders
  snap to device pixels, T3e's third code review). The 5.1 measurements found defects at single
  widths (541, 549, 561 px), so a sampled set of widths is not enough. Fractional widths and zoom
  are a stated residual; the tolerances below absorb sub-pixel layout. A width is a relayout of
  the loaded page (`Emulation.setDeviceMetricsOverride`), not a new load; 3c-C measures the cost
  on the pinned labels, and if the job is too slow the widths are changed by an amendment of this
  note, reviewed, never by sampling silently.

### R3. T's model against the drawing (the first check; PR 3c-B)

For every text node and list marker, in both modes, the judge reads Chrome's computed
`font-size`, `color`, `line-height`, `-webkit-text-decorations-in-effect` and the chain of
backgrounds behind it, and compares them with T's model (`computeStyle`; a size under `smaller`
against the model's range, × 0.75 to × 0.9). Any difference fails, whatever its cause. List
numbers are read from the accessibility tree and compared with the scanner's; each table's grid
from cell rectangles and compared with T(div)'s. This check needs no fonts and no geometry, so it
ships first: it makes the third code review's one-off comparison (1 894 random sections, no
mismatch) a merge gate on T, run on every accepted golden case of T and every accepted section of
every pinned label.

### R4. Geometry (PR 3c-C)

Ink is measured from the pinned fonts' outlines (the `glyf` table), placed at the glyph boxes and
baselines Chrome reports, never from pixels (R7). Each check, on the authority's drawing, at every
width and ratio of R2:

1. **Overlap.** No glyph's ink meets another's, except within a grapheme cluster.
2. **Every drawn line as ink.** Decoration lines, borders, rules and the edges of inline
   backgrounds: a line that crosses, or comes within 0.5 CSS px of, any glyph's ink above or
   below it fails, except a run's own underline under unshifted glyphs the allowlist accepted and
   a `+` T5 waived under its own run's underline. The 0.5 px clearance is measured again in the
   pinned fonts before it is set.
3. **A line under text is an underline.** Any horizontal line under text (a border, a rule, an
   underline, an inline background's edge) is judged as an underline by the allowlist
   (`src/authority/underline.ts`) over the stretch of the line of text above it that it spans,
   counting a code point as underlined only where its own ink lies within 0.3 em, of its own size,
   above the line.
4. **The gap between cells.** Across every shared vertical edge, bordered or not, the gap between
   the two cells' nearest ink is at least 0.25 em of the larger font size (T3e's bound, measured).
   Cells above and below each other are left to checks 1 and 2.
5. **The frame.** Every glyph's advance box inside its frame (the section `div`'s content box
   outside tables, widened on the right by 1.2 pt; in a table its cell's or caption's content box,
   a collapsed border counting half inside the cell), horizontally, and its line box vertically,
   with a tolerance of 1/32 CSS px (a line-end hyphen in the tablets' 5.1 table 4 overhangs its
   cell by 0.028 px at 280 px; T's note said at least 1/64, now settled).
6. **Reach.** No text left of or above the initial containing block, or clipped; overflow to the
   right, which the page scrolls, is allowed.
7. **Contrast against pixels.** Each glyph's colour against the pixels drawn behind its box,
   at least T3a's 4.5:1 with a stated margin (R7's pixel tier).
8. **Folds.** Each raised or lowered glyph's drawn shift against T4's fold decision: a glyph T
   left on the baseline is drawn within 0.1 em of it, a folded one at least 0.2 em off it.

A section fails if any check fails at any width or ratio. The record keeps, per check, the worst
case and the width it occurs at.

### R5. What the record binds, and the `rendering` stage

The stage finds the publication's record by the document's SHA-256 and requires:

- the record's gate version is this build's (`GATE_VERSION`, R9);
- for every section the import carries, the record holds T(div)'s SHA-256 as this import computes
  it, and a pass;
- for every withheld section, the record holds its drawing, failed or not (the withheld note).

Reasons: `renderer-evidence-missing` (no record for the document), `renderer-evidence-stale` (a
record of another gate version, or a T(div) hash this import does not compute: T changed), and
`renderer-evidence-failed` (a carried section failed). A synthetic publication passes, as today
(D7's reserved ids, keyed on the request's checked `authority`).

Binding T(div)'s hash, not the importer's version, means an importer change that leaves T's output
alone does not invalidate every record.

### R6. The image: pinned browser, pinned fonts

`Dockerfile.renderer`, pinned as `Dockerfile.validator` is and read by CI through one pins reader
(`scripts/ci/renderer-pins.mjs`, modelled on `validator-pins.mjs`):

- base: the worker's own `node:22.22.0-bookworm-slim@sha256:…` (`check-dockerfiles.mjs`
  requires one node digest; ICU is then the worker's), its shared libraries from a dated
  `snapshot.debian.org` mirror;
- Chrome for Testing's `chrome-headless-shell` for linux64 at a pinned version, checked by
  SHA-256 (the current stable, 154.0.8037.57, when 3c-B lands);
- fonts: Liberation Serif and Sans (2.1.5), Carlito and Caladea (from a commit-pinned
  `google/fonts` path), each file by SHA-256; a `fontconfig` that lists only that directory, with
  explicit aliases (Times New Roman and Times → Liberation Serif; Arial and Helvetica →
  Liberation Sans; Calibri → Carlito; Cambria → Caladea; `serif`, `sans-serif` likewise), no
  hinting, no sub-pixel order, no embedded bitmaps;
- flags: `--headless`, `--font-render-hinting=none`, `--disable-gpu`, `--disable-lcd-text`,
  `--force-color-profile=srgb`, `--disable-skia-runtime-opts`, a fixed `--lang=en-US`;
- no network while drawing: the job runs the container with `--network none`, and pages come from
  the judge by the DevTools protocol over a pipe (`--remote-debugging-pipe`), no port.

Each text node's fonts are read with `CSS.getPlatformFontsForNode`: a glyph drawn in any font but
the pinned one for its family (a fallback) fails the section (`font-coverage`), so a code point a
pinned font lacks refuses rather than being measured in an unknown font. T's font list (T3) is
the list of families this image can draw.

### R7. Determinism: three tiers

A record is reproduced when a second draw, in a second process, gives:

- **layout**, identical: every measured box and baseline, in Chrome's 1/64 px units, serialised
  canonically (RFC 8785) and hashed; ink derived from outlines is part of this tier, since it is
  a function of layout and the pinned font files;
- **pixels**, within a stated tolerance: only check 7 reads pixels, and the record stores its
  minimum contrast rounded down to 0.01 with a margin, since Skia's raster paths can differ by CPU;
- **verdict**, identical.

Screenshots are CI artefacts, never hashed into the record. CI draws each record twice on every
run; once, 3c-C runs the same draw in Cloud Build to show a different CPU gives the same layout
hash, and records the result.

### R8. The record

`src/render/records/<documentSha256>.json`, canonical JSON:

```
{ recordVersion, gateVersion, renderer: { chrome, imageDigest, fonts: [{ file, sha256 }] },
  document: { authority, id, sha256 }, widths, ratios, modes,
  sections: [{ path, code, tDivSha256, withheld, layoutSha256, pixelMinContrast,
               checks: [{ name, pass, worst, atWidth }], pass }],
  pass }
```

Kilobytes per publication. The full measurements and screenshots are CI artefacts of the run
that made the record. `recordSha256` is the SHA-256 of the file's bytes.

### R9. Versions and the lock

- `GATE_VERSION` in `src/render/version.ts`, and `src/render/renderer.lock.json` mapping each
  released gate version to the hash of `src/render/` (the records excluded), `Dockerfile.renderer`
  and its pins, as the importer lock does, with the same first-parent history rule (released
  entries never change; merges are merge commits).
- A change to the judge, the image, a font, a width or a tolerance is a new gate version, and
  every record is drawn again in the same change.
- The importer's version changes when the `rendering` stage changes (3c-D), not when a record is
  added.

### R10. The run manifest

`AuthorityFetchSchema` gains `rendering: { gateVersion, recordSha256 }`, required for an
authority import and forbidden for a synthetic one; `RUN_MANIFEST_VERSION` 2.1.0, with 2.0.0 kept
in `AnyRunManifestSchema`. The record is not part of the submission: it proves nothing about the
approved content's bytes, only about how they draw. The worker image carries the store
(`Dockerfile` copies `src/render/records`), whose digest the manifest already records.

## Delivery

1. **3c-B**: the image, the CI job, R3. T's model is exported from `src/authority/t/` (an importer
   version bump).
2. **3c-W**: the withheld section (`docs/design/authority-import-withheld.md`).
3. **3c-C**: R4, R7, R8, R9 and the records of the pinned labels.
4. **3c-D**: R5 and R10: the lookup, the manifest, re-verification.
5. **3c-E**: pictures from the authority's export (the owner's decision of 2026-09-25; its own
   note), needed by every pinned label but Imatinib Teva once 5.1 is withheld.

## Stated residuals

- The gate proves what its pinned fonts, widths and ratios draw. A reader's own fonts, a
  fractional width or a zoom level can wrap differently.
- The authority's viewer applies a stylesheet the gate does not; its drawing can differ.
- The visited link colour is judged statically (T3a): a drawing cannot see it.
- A record reproduces on the CI runner's CPU; its pixel tier is compared within a tolerance, not
  exactly.
- Re-verifying an old import needs its worker image, which carries the store, to persist in the
  registry.

## Verification

- Unit tests of the judge on recorded Chrome outputs (`test/render/`), so `npm run check` needs
  no browser; a coverage floor.
- The `renderer` job: every accepted T case and every section of every pinned label, both modes;
  a seeded wrong model entry, a seeded overlap, clipping and a line under a `<` each fail.
- Two draws per record agree; the Cloud Build draw agrees once.
- The lookup's refusals, each tested; the gate's recomputation repeats the lookup.
