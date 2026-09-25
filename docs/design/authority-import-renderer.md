# The renderer gate: a pinned browser's evidence for every authority import (roadmap 3a, PR 3c)

- Status: proposed, 2026-09-25 (architecture approved by the owner the same day); second draft
  after the first design review
- Decides: what `docs/design/authority-import-t.md`'s "What waits for PR 3c" left open: who draws,
  what is drawn and measured, the evidence record and its store, and the `rendering` stage
- Amends: ADR 0005 (decision 1's renderer cross-check); `docs/design/authority-import-t.md` (the
  store and who writes it, settled; the frame tolerance); `docs/design/authority-import-contract.md`
  (D1's trust statement, D10's check order, D12's evidence, D13's versions)
- Related: ADR 0003, ADR 0004 (the importer lock), `docs/design/authority-import-withheld.md`

## What this is for

T decides from the markup alone what the text is, and refuses presentation that can hide or alter
it in any layout. It cannot decide whether glyphs drawn at a given width and font touch, whether
two cells' text reads as one, or whether a line runs under a sign. So an import is accepted only
for a publication a pinned browser has drawn and found free of hidden, overlapping or displaced
text (T's opening; ADR 0005, amended 2026-09-24). Until the gate exists the importer's
`rendering` stage refuses every publication but a synthetic one.

## Decisions

### R1. Reproduced records: CI draws, main's own workflow redraws before any deploy, Zone B looks up

- **Who draws.** One pinned renderer image (R6) and one TypeScript judge (`src/render/`), in CI,
  from pinned bytes only: every publication with a record, and every picture it carries, is pinned
  in `labels/` by content hash (D14), so CI never depends on the authority being up.
- **What is written.** A small evidence record per publication (R8) in its own store,
  `src/render/records/`, outside `src/authority/`, so a new record does not change the importer's
  version (D10's lock hashes `src/authority/`). A person commits the file; what makes it trusted
  is that reviewed code in the pinned image regenerates it exactly (R7).
- **What makes a record trusted.** The trust root is main, as for the importer lock:
  - the pull request's `renderer` job redraws every record (a required check);
  - **main's own deploy workflow** (`deploy.yml`, which a pull request cannot alter before it is
    merged) redraws every record in the store before it builds the worker image, and refuses to
    deploy if any record differs; so a record pushed to main by any route, reviewed or not, never
    reaches Zone B unless the code on main reproduces it;
  - the branch protection of `main` requires the `renderer` check, up-to-date branches and
    enforcement for administrators, and `CODEOWNERS` covers `src/render/**`, `.github/workflows/**`
    and `Dockerfile.renderer` (settings the owner applies; the deploy redraw holds without them).
- **What Zone B does.** The `rendering` stage is a pure, synchronous lookup inside
  `importPublication`, with the store passed in as data (as the mapping is). D10's check order is
  unchanged; the gate's recomputation (D1) repeats the lookup; the record's hash goes into the
  signed run manifest (R10).
- **Why not draw in the worker.** A browser in Zone B would recompute the drawing too, but it
  does not fit the worker's 1 GiB, needs its own network blocking (a Cloud Run sidecar shares the
  worker's network and identity), adds an image digest the manifest does not record, is invisible
  to the vulnerability scan, and would do nothing until PR 5 lifts the dry run. The judge can later
  run in the worker image unchanged, if a merge per publication becomes impractical.

The cost: every new publication needs a record merged before it can be imported, and every change
of the gate redraws every record.

### R2. What is drawn

- **The page.** A standards-mode HTML document holding nothing but the div: `<!DOCTYPE html>`, a
  `meta charset`, no stylesheet, `<body style="margin:0">` whose only child is the div. Scripts are
  disabled (`Emulation.setScriptExecutionDisabled`) and the judge measures in an isolated world.
  The authority's class stylesheet is not applied (ADR 0005; T's model assumes the same). The
  EMA's viewer draws the portal's own HTML of the section inside a Verdana stylesheet with the
  inline styles overriding it; that stylesheet is not modelled (a stated residual, as today).
- **Two drawings of every section.** The authority's div as served, and T(div). Geometry is
  measured on the authority's drawing (T's output has no styles, so its geometry proves nothing);
  the text, list numbers, table grids and pictures are compared between the two.
- **Two modes.** HTML (`text/html`) and XML (`application/xhtml+xml`). Geometry is measured in
  HTML mode at every width and ratio; in XML mode R3 is run in full and the layout hash must equal
  HTML mode's at the named widths (813, 360 and 1 240 px, every ratio), since T's one-tree rules
  make the two trees the same; a `parsererror` in XML mode fails the section.
- **Pictures.** A carried picture is drawn from the bytes the import carries (D6): the reference
  is replaced, for the drawing only, by the same `data:` URI T writes. A withheld section's
  pictures are drawn from their pinned bytes when the authority publishes them (PR 3c-E), and
  otherwise as the authority's viewer draws them (a broken image, 16 × 16 px), in which case no
  failing check may rest on a picture's box.
- **Widths.** The viewport width, with scrollbars hidden (`--hide-scrollbars`), asserting the div's
  `clientWidth` equals it. Every integer width from 320 to 1 920 CSS px at ratio 1 (the EMA
  viewer's narrative column is 813 px in a 1 280 px window and 1 240 px in a 1 920 px one; a
  phone's is 320 to 430 px). A width is a relayout of the loaded page
  (`Emulation.setDeviceMetricsOverride`, width only), which the first review measured equal to a
  fresh load.
- **Device pixel ratios.** An emulated ratio does not change layout (measured), so each ratio is
  its own browser process launched with `--force-device-scale-factor`: 1, 1.25, 1.5, 1.75, 2 and
  3 (Windows' 125 % and 150 % scaling; zoom times ratio is the effective ratio, so these cover
  zoom). At ratios other than 1, every integer width from 320 to 1 280 px. A test asserts the
  snapped border widths (a 1.4 pt border is 1 px at ratio 1, 1.33 px at 1.5, 1.67 px at 3).
- Fractional widths and ratios between those drawn can break lines in combinations no drawn
  layout has: a stated residual.

### R3. T's model against the drawing (PR 3c-B)

For every text node and every list marker, in both modes and at the named widths, the judge reads
Chrome's computed style and compares it with T's model (`computeStyle`) for **every property the
model holds**: the used font file (R6), `font-size` (a size under `smaller` against the model's
range, × 0.75 to × 0.9), weight, style, `color`, `line-height`, `-webkit-text-decorations-in-effect`,
the chain of backgrounds, `text-indent`, margins, paddings, borders (width as snapped, style,
colour), `border-collapse`, cell padding and spacing, `display`, `position`, `top`, `bottom` and
`vertical-align`. Any difference fails, whatever its cause. List numbers come from the
accessibility tree and are compared with the scanner's; each table's grid from cell rectangles,
compared with T(div)'s. Glyph size is measured too: each character's box height equals the pinned
font's content area at its computed size, rounded as Chrome rounds it (R4). This check ships first:
it makes the third code review's one-off comparison a merge gate on T.

### R4. Geometry (PR 3c-C)

**Where boxes come from.** DevTools reports no glyph ids, positions or baselines; per-character
rectangles (`Range.getClientRects`) are the source, and a ligature or a mark shares its cluster's
rectangle. So ink is bounded, never assumed:

- **Baseline.** Each character rectangle's top plus the pinned font's ascent at the computed size,
  rounded as Chrome rounds it on Linux; the rule is calibrated (below).
- **Ink bound.** For each character, the union of the ink boxes (the `glyf` bounding boxes, with
  their left and right overhangs past the advance) of every glyph the pinned font can draw for its
  cluster: the cmap's glyph, every ligature containing it (`liga`, `calt`, `ccmp`), and, for a
  mark, its glyph displaced by the font's largest mark anchor offset (GPOS `mark`, `mkmk`),
  placed on the cluster's rectangle and baseline. A run whose shaping the bound cannot cover
  (another script's shaping, a feature outside that list) refuses.
- **The originals' outlines.** The pinned fonts share the originals' advances, not their outlines;
  each ink bound is widened by the largest per-glyph difference between the pinned and original
  outlines, measured once from the original fonts' files and stated.
- **Calibration.** Once per gate version, for every glyph of every pinned font at 5 to 24 pt and at
  every ratio, a text-only pixel mask of the glyph drawn in Chrome must lie inside its computed
  ink bound; the baseline rule must place every glyph within 1/64 px of the mask's measured
  baseline. A glyph that fails is a gate refusal, not a widened tolerance.
- **Line box.** Each line fragment's rectangle (`getClientRects` of the line's text range).
- **Markers.** A list marker's box from `DOM.getBoxModel` on its `::marker` pseudo-node, its ink
  bounded as text.

Each check, on the authority's drawing, at every width and ratio of R2; "ink" is the ink bound:

1. **Overlap.** No ink bound meets another's (boxes, no clearance), except within a grapheme
   cluster (read in the page, Chrome's own ICU); no painted box (a background, a border, a
   picture) of an element painted after a glyph meets its ink bound.
2. **Every drawn line as ink.** Decoration lines, borders, rules and the edges of inline
   backgrounds: a line that crosses, or comes within 0.5 CSS px of, any ink bound, above, below or
   beside it, fails, except a run's own underline under unshifted glyphs the allowlist accepted
   and a `+` T5 waived under its own run's underline.
3. **A line under text is an underline.** Any horizontal line under text is judged by the
   allowlist (`src/authority/underline.ts`) over the stretch of the line of text above it that it
   spans, counting a code point as underlined where its ink bound lies within 0.3 em (of its own
   computed size) above the line.
4. **The gap between cells.** Across every shared vertical edge, bordered or not, the gap between
   the two cells' nearest ink bounds is at least 0.25 em of the larger computed size.
5. **The frame.** Every glyph's advance box inside its frame horizontally, and its line box
   vertically (the section `div`'s content box outside tables, widened on the right by 1.2 pt; in
   a table its cell's or caption's content box, a collapsed border counting half inside the cell),
   with a tolerance of 1/32 CSS px, set from a measurement in the pinned fonts within the drawn
   widths.
6. **Reach.** No advance box left of or above the initial containing block, and none clipped;
   overflow to the right, which the page scrolls, is allowed.
7. **Visibility and contrast.** At the named widths and every ratio: the pixels inside each ink
   bound differ between the text drawn in its colour and the same page with the text transparent
   (nothing covers it), and its colour has at least 4.5:1 against the pixels behind it with the
   text transparent, with a margin of 0.05.
8. **Folds.** A glyph T left on the baseline has its baseline within 0.1 em (its computed size) of
   its line's; a folded glyph's baseline stands at least 0.2 em (its computed size) from the
   baseline of the unshifted text beside it on the same line.

**Tolerances re-measured.** Before a gate version is set, 0.5 px (check 2), 0.3 em (check 3),
0.25 em (check 4), 1/32 px (check 5) and the fold bounds (check 8) are measured again on the
pinned labels in the pinned fonts, and each is kept only if the label's accepted drawing clears it
with the stated margin; T's note records the values.

A section fails if any check fails at any width or ratio. The record keeps, per check, the worst
case and where (width, ratio, mode).

### R5. The `rendering` stage: what the lookup requires

The stage finds the record for the document's SHA-256 and requires:

- the record's `authority` and document `id` equal the request's, and its `gateVersion` and
  `gateSha256` (the lock entry's hash, R9) are this build's;
- the record's sections are exactly the document's, in pre-order, each matched by `path`, `code`
  and `tDivSha256` (the hash this import computes for T(div); a section T does not transform has
  none);
- every carried section passes;
- the withheld sections' drawn failures, per `docs/design/authority-import-withheld.md` W1: a
  listed section that passes refuses (`withheld-section-accepted`), and a failing one that is not
  listed refuses;
- every section that gave T5 evidence is carried and passes.

Reasons: `renderer-evidence-missing`, `renderer-evidence-stale` (another gate, or a section or
T(div) hash this import does not compute), `renderer-evidence-failed`, and the withheld note's.
A synthetic publication passes, as today (D7's reserved ids, on the request's checked
`authority`).

### R6. The image: pinned browser, pinned fonts

`Dockerfile.renderer`, pinned as `Dockerfile.validator` is; CI builds it and runs the judge in it
(`--network none`), its pins read by one reader (`scripts/ci/renderer-pins.mjs`) used by the
Dockerfile check and the lock:

- base: the worker's own `node:22.22.0-bookworm-slim@sha256:…` (`check-dockerfiles.mjs` requires
  one node digest), its shared libraries from a dated `snapshot.debian.org` mirror;
- Chrome for Testing's `chrome-headless-shell`, linux64, at a pinned version by SHA-256 (the
  current stable, 154.0.8037.57, when 3c-B lands); it ships its own ICU, which the judge uses by
  measuring grapheme clusters in the page;
- fonts: Liberation Serif and Sans 2.1.5, Carlito and Caladea (from a commit-pinned
  `google/fonts` path), each file by SHA-256; a `fontconfig` listing only that directory, with
  strong bindings (not weak aliases: Skia accepts a substitution only for its own list, which lacks
  Times and Helvetica) from Times New Roman and Times to Liberation Serif, Arial and Helvetica to
  Liberation Sans, Calibri to Carlito, Cambria to Caladea, and `serif` and `sans-serif`; no
  hinting, no sub-pixel order, no embedded bitmaps; sub-pixel glyph positioning pinned (on or off,
  the same at every ratio) and asserted by a test that reads a fractional advance;
- flags: `--headless`, `--font-render-hinting=none`, `--disable-gpu`, `--disable-lcd-text`,
  `--force-color-profile=srgb`, `--disable-skia-runtime-opts`, `--hide-scrollbars`, `--lang=en-US`,
  `--force-device-scale-factor` per process (R2); the judge drives it over
  `--remote-debugging-pipe`, with no port.

**Coverage.** `CSS.getPlatformFontsForNode` counts a glyph the font lacks under the font, so it
proves only the family. The judge checks every code point against the cmap of the pinned file
for its family; a code point the file lacks refuses the section (`font-coverage`), except a
closed list of substitutions HarfBuzz makes (U+2011 drawn as the hyphen's glyph), each asserted by
a pixel test. A test seeds U+2070, which Liberation Serif lacks. Every alias is read back from the
drawn page in a test.

### R7. Determinism: three tiers

A record is reproduced when a second draw, in a second process, gives:

- **layout**, identical: every measured rectangle and baseline, as the raw doubles Chrome reports
  (a forced ratio gives values that are not multiples of 1/64 px), serialised by RFC 8785 and
  hashed per section, mode and ratio; ink bounds are a function of these and the pinned files;
- **pixels** (check 7 only), within a tolerance: the minimum contrast and the visibility margin,
  rounded down to 0.01, since Skia's raster can differ by CPU;
- **verdict**, identical.

CI draws each record twice. Once, 3c-C runs the draw in Cloud Build to show another CPU gives
the same layout hashes, and records the result.

### R8. The record

`src/render/records/<documentSha256>.json`, canonical JSON:

```
{ recordVersion, gateVersion, gateSha256,
  renderer: { chrome, pinsSha256, fonts: [{ file, sha256 }] },
  document: { authority, id, sha256 }, widths, ratios, modes, tolerances,
  sections: [{ path, code, tDivSha256?, layoutSha256: { <mode>@<ratio>: sha256 },
               pixel: { minContrast, visibilityMargin },
               checks: [{ name, pass, worst, at: { width, ratio, mode } }], pass }],
  capturesSha256 }
```

No `withheld` field and no overall pass: what is withheld is the request's (the withheld note),
and the lookup reads each section's `pass`. The full measurements and the screenshots of main's
deploy redraw go to the evidence bucket (CMEK, immutable) by hash, `capturesSha256` naming the
set; they are evidence, not part of reproduction. `pinsSha256` identifies the image by its pinned
inputs, since a rebuilt image's digest differs.

### R9. Versions and the lock

- `GATE_VERSION` in `src/render/version.ts`, and `src/render/renderer.lock.json` mapping each
  released gate version to `gateSha256`: the hash of **the judge's whole module import closure**
  (computed from the TypeScript module graph, so T's model, the underline allowlist, the scanner
  and the hashing code are in it), `Dockerfile.renderer`, its pins, the fontconfig and the
  tolerances, with the importer lock's first-parent rule (a released entry never changes; merges
  are merge commits). So any change to anything the judge runs is a new gate version, and every
  record is redrawn in the same change.
- The importer's version changes when the `rendering` stage changes (3c-D), not when a record is
  added. The importer's identity is then (importer version, gate version, record hash); the gate
  compares all three, and the manifest records all three.

### R10. The run manifest

`AuthorityFetchSchema` gains `rendering: { gateVersion, gateSha256, recordSha256 }`, required when
the submission's source authority is not `synthetic` and absent when it is; `RUN_MANIFEST_VERSION`
moves a minor version, with the previous kept in `AnyRunManifestSchema`. The record is not part of
the submission: it proves how the approved content draws, not what it is. The worker image
carries the store (`Dockerfile` copies `src/render/records`).

### R11. Cost

The first review measured 5.1 of the capsules at 43.5 k character rectangles, about 110 ms to read
per width and 1.4 MB of rectangles per width; the label holds about 107 k characters. The job is
sharded by ratio (one process each, run in parallel), streams rectangles into per-section hashes
rather than keeping them, takes screenshots only at the named widths, and caches a redraw's
verdict by (`gateSha256`, document hash, record hash) in caches written only by main's runs, which
pull requests can read but not write. 3c-C measures the real cost; if a shard exceeds its time
limit, the widths change by an amendment of this note, reviewed, never by sampling silently.

## Delivery

1. **3c-B**: the image, the CI job, R3. T's model is exported from `src/authority/t/` (an importer
   version bump).
2. **3c-C**: R4, R7, R8, R9, R11; the records of the pinned labels; the deploy redraw.
3. **3c-W**: the withheld section (its note), which reads the records.
4. **3c-D**: R5 and R10: the lookup, the manifest, re-verification.
5. **3c-E**: pictures from the authority's export (the owner's decision of 2026-09-25; its own
   note), needed by every pinned label but Imatinib Teva once 5.1 is withheld.

## Stated residuals

- The gate proves what its pinned fonts, widths and ratios draw; a reader's own fonts, a
  fractional width or ratio, or a width above 1 920 px can wrap differently.
- The authority's viewer applies a stylesheet the gate does not.
- The visited link colour is judged statically (T3a).
- The pixel tier is compared within a tolerance, not exactly.
- Re-verifying an old import needs its worker image, which carries the store, to persist.

## Verification

- Unit tests of the judge on recorded Chrome outputs (`test/render/`), so `npm run check` needs
  no browser; a coverage floor.
- The `renderer` job on every accepted T case and every section of every pinned label; seeded
  failures each caught: a wrong model entry, an overlap, text off the page, a marker off the page,
  a line under a `<`, a missing glyph (U+2070), a script in the div, an XML `parsererror`, a
  border snapped differently at ratio 1.5.
- Two draws per record agree; the Cloud Build draw agrees once; the calibration passes.
- The deploy workflow refuses a record it cannot reproduce (a test of the workflow's script).
- The lookup's refusals, each tested; the gate's recomputation repeats the lookup.

## Reviews

1. **First independent review** (2026-09-25), with Chrome for Testing 154 and the pinned fonts.
   High: nothing enforced "a record merges only if it reproduces" (main is pushable by its
   administrators, checks are not strict, the deploy did not redraw); the gate version did not
   cover the code the judge imports (T's model, the allowlist), so a stale record could pass; an
   emulated device pixel ratio does not change layout, so the ratio draws tested nothing;
   `getPlatformFontsForNode` counts a missing glyph under the font, so the coverage guard was
   blind; DevTools reports no glyph positions, so "ink placed at Chrome's glyph boxes" was not
   Chrome's ink. Medium: markers left out of the geometry; the withheld list stored in the record;
   an ambiguous lookup key; the cost; the width undefined; tolerances not re-measured; undefined
   baselines and line boxes; R3 compared five properties of the model's many; the record lacked
   fields, and CI fetched from the authority. Fixed in this draft: main's deploy redraws every
   record; the lock hashes the judge's whole import closure; one process per forced ratio, with
   fractional ratios; a cmap coverage check; ink bounded from the pinned outlines and calibrated
   against pixels; markers, the lookup key, sharding and caching, widths to 1 920 px, every model
   property, the record's fields and pinned bytes.
