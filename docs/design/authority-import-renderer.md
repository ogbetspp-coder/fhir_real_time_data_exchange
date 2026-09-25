# The renderer gate: a pinned browser's evidence for every authority import (roadmap 3a, PR 3c)

- Status: proposed, 2026-09-25 (architecture approved by the owner the same day); third draft,
  after two design reviews
- Decides: what `docs/design/authority-import-t.md`'s "What waits for PR 3c" left open: who draws,
  what is drawn and measured, the evidence record and its store, and the `rendering` stage
- Amends: ADR 0005 (decision 1's renderer cross-check); `docs/design/authority-import-t.md` (the
  store and who writes it, settled; the frame; the gap between cells; the tolerances);
  `docs/design/authority-import-contract.md` (D1's trust statement, D10, D12, D13, D14)
- Related: ADR 0003, ADR 0004 (the importer lock), `docs/design/authority-import-withheld.md`

## What this is for

T decides from the markup alone what the text is, and refuses presentation that can hide or alter
it in any layout. It cannot decide whether glyphs drawn at a given width and font touch, whether
two cells' text reads as one, or whether a line runs under a sign. So an import is accepted only
for a publication a pinned browser has drawn and found free of hidden, overlapping or displaced
text (T's opening; ADR 0005, amended 2026-09-24). Until the gate exists the importer's
`rendering` stage refuses every publication but a synthetic one.

## Decisions

### R1. Reproduced records: the worker image carries only records its own code reproduced

- **Who draws.** One pinned renderer image (R6) and one judge (`src/render/`), from pinned bytes
  only: every publication with a record, and every picture it draws, is pinned in `labels/` by
  content hash (D14). A redraw checks each pinned file's hash against the record's
  `document.sha256` and the file's name, and a record with no pinned bytes fails the redraw.
- **What is written.** A small evidence record per publication (R8) in its own store,
  `src/render/records/`, outside `src/authority/`, so a new record does not change the importer's
  version (D10's lock hashes `src/authority/`). A person commits it (made by running the judge in
  the renderer image, locally or in Cloud Build); nothing trusts it for having been committed.
- **The trust rule.** A worker image carries only records that the image's own code reproduced
  while the image was built. `cloudbuild.images.yaml`, which every deploy route runs (the
  `deploy.yml` workflow and `scripts/gcp/deploy.sh` alike), gains a step before the worker image
  is built, in the manner of `validator-starts-offline`: it builds the renderer image from the same
  uploaded tree, runs the judge with `--network none` and no environment passed in, redraws every
  record in the store twice, and fails the build if any record differs (R7). So whatever tree is
  uploaded, the image's records and the image's code agree; the draw holds no credentials.
- **Pull requests.** CI's `renderer` job is a faster pre-check, not the trust root: it runs the
  judge's unit tests, R3 on every accepted T case, and redraws the records whose bytes, gate hash
  or pinned files differ from `main`'s (decided by git content, never by an Actions cache, which
  a pull request can write in its own scope). The failing checks' captures are published as the
  run's artefacts, so the reviewer of a record that fails a section sees the drawing.
- **What Zone B does.** The `rendering` stage is a pure, synchronous lookup inside
  `importPublication`, with the store passed in as data (as the mapping is). D10's check order is
  unchanged; the gate's recomputation (D1) repeats it; the record's hash goes into the signed run
  manifest (R10), and, when a section is withheld, into the submission (the withheld note).
- **Why not draw in the worker.** A browser in Zone B would recompute the drawing too, but it
  does not fit the worker's 1 GiB, needs its own network blocking, adds an image digest the
  manifest does not record, is invisible to the vulnerability scan, and would do nothing until
  PR 5 lifts the dry run. The judge can later run in the worker image unchanged.

The cost: every new publication needs a record merged before it can be imported, and every change
of the gate redraws every record at the next image build.

### R2. What is drawn

- **The page.** For each section on its own, a standards-mode HTML document holding nothing but its
  div: `<!DOCTYPE html>`, a `meta charset`, no stylesheet, `<body style="margin:0">` whose only
  child is the div. Scripts are disabled (`Emulation.setScriptExecutionDisabled`); the judge
  measures in an isolated world. The authority's class stylesheet is not applied (ADR 0005). So
  the gate draws **the FHIR div as published, with its inline styles only, in fonts metric-
  compatible with those it names**, which is what any reader of the div without the authority's
  portal draws; the EMA viewer's own stylesheet (Verdana, with the inline styles overriding it)
  is not modelled, a stated residual.
- **Two drawings of every section T transforms.** The authority's div as served, and T(div).
  Geometry is measured on the authority's drawing; the text, list numbers, table grids and
  pictures are compared between the two. A section T refuses is drawn once, as served.
- **Two modes.** HTML (`text/html`) and XML (`application/xhtml+xml`). Geometry is measured in HTML
  mode; in XML mode R3 runs in full and the geometry of the text, markers and cells (not of
  elements the HTML parser inserts, such as `tbody`) must equal HTML mode's at the named widths
  (813, 360 and 1 240 px) at every ratio; a `parsererror` fails the section.
- **Pictures.** Keyed on the picture, not on withholding: a picture whose bytes the import carries
  or the authority publishes (pinned, R1) is drawn from them, its reference replaced for the
  drawing only by the `data:` URI; any other is drawn as the authority's viewer draws it, a broken
  image of 16 × 16 px, and no failure used as a withheld section's evidence may rest on its box.
  The record lists each picture drawn, with its bytes' hash or `broken`.
- **Widths.** The viewport width, scrollbars hidden (`--hide-scrollbars`), asserting the div's
  `clientWidth` equals it: every integer width from 320 to 1 920 CSS px at ratio 1 (the EMA viewer's
  narrative column is 813 px in a 1 280 px window and 1 240 px in a 1 920 px one; a phone's is 320
  to 430 px), each a relayout of the loaded page (`Emulation.setDeviceMetricsOverride`, width
  only; measured equal to a fresh load).
- **Device pixel ratios.** An emulated ratio does not change layout, so each ratio is its own
  browser process launched with `--force-device-scale-factor`: 1, 1.25, 1.5, 1.75, 2 and 3 (zoom
  times ratio is the effective ratio, so these cover zoom); at ratios other than 1, every integer
  width from 320 to 1 280 px. Layout does change with the ratio (the tablets' 5.1 is 26 683 px tall
  at 813 px and ratio 1, 26 654 px at ratio 3). A test asserts snapped border widths per ratio.
- Fractional widths and ratios between those drawn can break lines in combinations no drawn
  layout has: a stated residual.

### R3. T's model against the drawing (PR 3c-B)

For every text node and every list marker of a section T transforms, in both modes and at the named
widths, the judge reads Chrome's computed style and compares it with T's model (`computeStyle`) for
every property the model holds: the platform font (exactly one per text node, and the pinned file
for its family and face, R6), `font-size` (under `smaller` against the model's range, × 0.75 to
× 0.9), weight, style, `color`, `line-height`, `-webkit-text-decorations-in-effect`, the chain of
backgrounds, `text-indent`, margins, paddings, borders (width as snapped, style, colour),
`border-collapse`, cell padding and spacing, `display`, `position`, `top`, `bottom` and
`vertical-align`. Any difference is a refusal of ours (R8). List numbers come from the
accessibility tree and are compared with the scanner's; each table's grid from cell rectangles,
compared with T(div)'s. Each character box's height must equal the pinned face's ascent plus
descent at its computed size, rounded at the device size as Chrome rounds them (R4). This check
ships first: it makes the third code review's one-off comparison a merge gate on T.

### R4. Geometry (PR 3c-C)

**Where boxes come from.** DevTools reports no glyph ids, positions or baselines. The sources:
per-character rectangles (`Range.getClientRects`), which hold each character's advance and the
face's content area; Chrome splits a ligature's advance evenly across its characters, and a mark
takes its cluster's rectangle. Everything else is computed from the pinned fonts and bounded:

- **Baseline.** The rectangle's top plus the face's ascent at the computed size, rounded at the
  device size; with `--disable-font-subpixel-positioning` (R6) Blink's Linux adjustment that
  moves a pixel from ascent to descent under sub-pixel positioning does not apply. Glyphs are
  drawn with the baseline snapped to whole device pixels, so the baseline is known to within one
  device pixel, and every bound below allows for it.
- **Ink bound.** For each character, the union of the ink boxes (the `glyf` bounding boxes,
  overhangs included) of every glyph reachable for it through the lookups of HarfBuzz's default
  features for the run's script and language (`ccmp`, `locl`, `rlig`, `liga`, `clig`, `calt` and
  their kin, as the pinned face has them), and of its canonical compositions; a ligature's
  characters are one unit, bounded by the ligature's glyph over their joint advances. A mark's
  ink is the union of every placement it can take: every anchor vector of the face's `mark` and
  `mkmk` lookups for its base, the unattached position at the end of the base's advance, and the
  sum for stacked marks; a mark in a face with no mark lookup for its base (HarfBuzz's own
  fallback) refuses. It is asserted once per gate version, from the font files, that no Latin
  positioning lookup but `kern` (advance-only in the pinned faces: value format 4, which the
  rectangles already hold), `mark` and `mkmk` moves a glyph. A run in another script refuses.
- **Rounding out.** Each ink bound is widened by the baseline's snapping (one device pixel
  vertically, and horizontally too since positions are whole pixels without sub-pixel
  positioning), by anti-aliasing (one device pixel), and by the largest per-glyph difference
  between the pinned and original outlines (measured once from the original fonts' files and
  stated), then rounded outwards to whole device pixels at the drawn ratio.
- **Calibration.** Once per gate version, for every (face, computed size, ratio) the records use,
  at the fractional origins the pages use, the pixels a glyph paints on a page of its own must lie
  inside its rounded-out ink bound. A glyph that fails is a refusal of ours, not a widened bound.
- **Markers.** A list marker's box from `DOM.getBoxModel` on its `::marker` pseudo-node, bounded
  as text.
- **Lines.** Borders, rules, decoration lines and the edges of backgrounds, at their positions
  snapped to device pixels.

Each check, on the authority's drawing, at every width and ratio of R2 ("ink" is the rounded-out
ink bound):

1. **Overlap.** No two inks meet, except two consecutive characters (or ligature units) of the
   same line fragment of the same text node, which a font's own spacing draws; every other pair,
   across lines, elements, cells, shifts or pictures, is checked. No painted box that is not the
   glyph's own or an ancestor's (a background, a border, a picture) meets an ink, whatever the
   painting order.
2. **Every drawn line as ink.** A line that crosses, or comes within 0.5 CSS px of, any ink, above,
   below or beside it, fails, except a run's own underline under unshifted glyphs the allowlist
   accepted, and a `+` T5 waived under its own run's underline. In a section T refuses, T's
   allowlist and waiver do not apply, so a line under text there is a failure of this check but
   never a withheld section's evidence (the withheld note).
3. **A line under text is an underline.** Any horizontal line under text is judged by the
   allowlist over the stretch of the line of text above it that it spans, a code point counting
   as underlined where its ink lies within 0.3 em (its computed size) above the line.
4. **The gap between cells.** Across every shared vertical edge, bordered or not, the gap between
   the two cells' nearest text, measured on the union of advance boxes and inks (ink can overhang
   an advance), is at least 0.25 em of the larger computed size. (T's note named advance boxes;
   the union is stricter.)
5. **The frame.** Horizontally, every advance box inside its frame (the section `div`'s content
   box outside tables, widened on the right by 1.2 pt; in a table its cell's or caption's content
   box, a collapsed border counting half inside it), with a tolerance of 1/32 CSS px. Vertically,
   every ink inside its frame's border box. Chrome exposes no line box (the text rectangles are
   content areas, and a `line-height` or a `position` shift moves them), so the vertical frame is
   judged on ink, where T's note named the line box; text shifted by `position` is left to checks 1
   and 2, as T's note requires.
6. **Reach.** No advance box left of or above the initial containing block, and none clipped;
   overflow to the right, which the page scrolls, is allowed.
7. **Visibility and contrast.** At the named widths and every ratio, a second drawing with each
   glyph's fill and its decorations transparent (`-webkit-text-fill-color` and
   `text-decoration-color`; borders keep their colour): the pixels inside each ink must differ
   between the two drawings (nothing covers the glyph), and the glyph's colour must have at least
   4.5:1 against the second drawing's pixels behind it, excluding its own accepted underline.
8. **Folds.** A glyph T left on the baseline has its baseline within 0.1 em (its computed size)
   plus one device pixel of its line's; a folded glyph's baseline stands at least 0.2 em minus one
   device pixel from the baseline of the unshifted text beside it on the same line.

**Tolerances re-measured.** Before a gate version is set, 0.5 px, 0.3 em, 0.25 em, 1/32 px and the
fold bounds are measured again on the pinned labels in the pinned fonts, and each is kept only if
the labels' accepted drawings clear it with a stated margin; T's note records the values.

### R5. The `rendering` stage: what the lookup requires

The stage finds the record for the document's SHA-256 and requires:

- the record's `authority` and document `id` equal the request's, and its `gateVersion` and
  `gateSha256` (R9) are this build's;
- the record's sections are exactly the document's, in pre-order, matched by `path` and `code`;
  for a carried section also by `tDivSha256`, the hash this import computes for T(div);
- every carried section has no refusal of ours and no failure;
- the withheld sections: the withheld note's W1, read from the record's `refusals`, `failures` and
  `defects` (R8).

Reasons: `renderer-evidence-missing`, `renderer-evidence-stale` (another gate, or a section or
T(div) hash this import does not compute), `renderer-evidence-failed`, and the withheld note's. A
synthetic publication with nothing withheld passes, as today; a synthetic publication with a
withheld section needs a record (CI draws the synthetic vectors), or it refuses. The importer's
golden vectors use a synthetic store, so adding a record does not change them.

### R6. The image: pinned browser, pinned fonts

`Dockerfile.renderer`, pinned as `Dockerfile.validator` is and built by `cloudbuild.images.yaml`
(and by CI for the pre-check), its pins read by one reader (`scripts/ci/renderer-pins.mjs`) used by
the Dockerfile check and the lock:

- base: the worker's own `node:22.22.0-bookworm-slim@sha256:…`, its shared libraries from a dated
  `snapshot.debian.org` mirror;
- Chrome for Testing's `chrome-headless-shell`, linux64, at a pinned version by SHA-256 (the
  current stable, 154.0.8037.57, when 3c-B lands); it carries its own ICU, which the judge uses by
  measuring grapheme clusters in the page;
- fonts: the four faces (regular, bold, italic, bold italic) of Liberation Serif and Sans 2.1.5,
  Carlito and Caladea (from a commit-pinned `google/fonts` path), each by SHA-256; a `fontconfig`
  listing only that directory, with strong bindings from Times New Roman and Times to Liberation
  Serif, Arial and Helvetica to Liberation Sans, Calibri to Carlito, Cambria to Caladea, and
  `serif` and `sans-serif`, each read back from a drawn page in a test; no hinting, no sub-pixel
  order, no embedded bitmaps;
- flags: `--headless`, `--font-render-hinting=none`, `--disable-font-subpixel-positioning`,
  `--disable-gpu`, `--disable-lcd-text`, `--force-color-profile=srgb`,
  `--disable-skia-runtime-opts`, `--hide-scrollbars`, `--lang=en-US`, and
  `--force-device-scale-factor` per process; the judge drives it over `--remote-debugging-pipe`.

**Coverage.** `CSS.getPlatformFontsForNode` counts a missing glyph under the font, so it proves only
the family. Every code point is checked against the cmap of the pinned face that draws it; a code
point the face lacks is a refusal of ours (`font-coverage`), except a closed list of substitutions
Blink and HarfBuzz make, each asserted by a pixel test: U+00A0 drawn as the space's glyph, U+2011
as the hyphen's, U+00AD drawn as a hyphen only at a line break. A test seeds U+2070, which
Liberation Serif lacks.

### R7. Determinism

A record is reproduced when a redraw, in a separate process, gives:

- **layout**, identical: every measured rectangle and snapped line, as the raw doubles Chrome
  reports, serialised by RFC 8785 and hashed per section, mode and ratio;
- **pixels** (check 7 only), within ε = 0.02 of the recorded contrast and visibility margins, and a
  value within ε of its threshold fails the check, so the verdict cannot flip between CPUs;
- **verdict**, identical.

The image build redraws each record twice (R1). Once, 3c-C draws in Cloud Build and on a second
CPU type and records that the layout hashes agree.

### R8. The record

`src/render/records/<documentSha256>.json`, canonical JSON:

```
{ recordVersion, gateVersion, gateSha256,
  renderer: { chrome, pinsSha256, fonts: [{ file, sha256 }] },
  document: { authority, id, sha256 }, widths, ratios, modes, tolerances,
  sections: [{ path, code, tDivSha256?, pictures: [{ src, sha256 | "broken" }],
               layoutSha256: { "<mode>@<ratio>": sha256 },
               pixel: { minContrast, visibilityMargin },
               refusals: [{ reason, at }],
               failures: [{ check, worst, at: { width, ratio, mode } }],
               defects: [{ kind, worst, at }] }] }
```

- `refusals` are ours (R3 differences, `font-coverage`, a run in another script, a mark without
  a lookup, a calibration failure, an XML `parsererror`): the gate cannot judge the section.
- `failures` are R4's checks, whose bounds are conservative: a failure proves the gate cannot
  show the drawing is sound, not that it is unsound.
- `defects` are lower-bound evidence that the drawing is unsound, from Chrome's own rectangles
  with no widening and no threshold of ours: two cells' advance boxes that touch or overlap
  (`cells-touch`), an advance box left of or above the page (`off-page`), and a drawn line
  crossing an advance box's interior (`line-through-text`), none resting on a broken picture's
  box. They are what the withheld note's W1 reads.

No overall pass and no withheld field: what is withheld is the request's. Captures (screenshots,
full measurements) are not part of the record and not compared: the image build writes them to the
evidence bucket, by hash, as evidence of that build.

### R9. Versions and the lock

- `GATE_VERSION` in `src/render/version.ts`, and `src/render/renderer.lock.json` mapping each
  released version to `gateSha256`: the hash of the judge as it runs, a single bundle built
  deterministically from `src/render/` with every module it imports (T's model, the underline
  allowlist, the scanner, the font parser from `node_modules`) and the page-side script, plus
  `Dockerfile.renderer`, its pins, the fontconfig and the tolerances. The judge reads no file but
  the pinned bytes and fonts, and imports nothing dynamically; the lock file is not in its own
  hash. The importer lock's first-parent rule applies (released entries never change; merges are
  merge commits).
- Any change to the bundle, the image or the tolerances is a new gate version, and every record is
  redrawn in the same change.
- The importer's version changes when the `rendering` stage changes (3c-D), not when a record is
  added. An import's identity is (importer version, gate version, record hash); the gate compares
  all three, and the manifest records all three.

### R10. The run manifest

`AuthorityFetchSchema` gains `authority` and `rendering: { gateVersion, gateSha256, recordSha256 }`,
the latter required when `authority` is not `synthetic` or something is withheld, and absent
otherwise. One minor version of `RUN_MANIFEST_VERSION` carries this and the withheld note's fields,
the previous kept in `AnyRunManifestSchema`. The worker image carries the store (`Dockerfile`
copies `src/render/records`).

### R11. Cost

The first two reviews measured 187 ms to relayout and read the whole label's 105 k characters per
width on an M-series Mac, and 43.5 k rectangles for 5.1 alone. A full draw of one label is 1 601
widths at ratio 1 and 961 at each of five other ratios (6 406 layouts). The image build runs on a
large Cloud Build machine, sharded by ratio and width range across its cores, streaming
rectangles into per-section hashes and taking screenshots only at the named widths. GitHub's
two-core runners run only the pre-check (R1). 3c-C measures the real cost on the pinned labels; if
the build exceeds its time limit, the widths change by an amendment of this note, reviewed, never
by sampling silently.

## Delivery

1. **3c-B**: the image, the CI pre-check, R3. T's model is exported from `src/authority/t/` (an
   importer version bump).
2. **3c-C**: R4, R7, R8, R9, R11; the image-build redraw; the records of the pinned labels.
3. **3c-W**: the withheld section (its note), which reads the records.
4. **3c-D**: R5 and R10: the lookup, the manifest, re-verification.
5. **3c-E**: pictures from the authority's export (the owner's decision of 2026-09-25; its own
   note), needed by every pinned label but Imatinib Teva once 5.1 is withheld.

## Stated residuals

- The gate proves what its pinned fonts, widths and ratios draw; a reader's own fonts, a
  fractional width or ratio, or a width above 1 920 px can wrap differently.
- The authority's viewer applies a stylesheet the gate does not.
- The visited link colour is judged statically (T3a).
- Re-verifying an old import needs its worker image, which carries the store, to persist.
- Branch protection (a required `renderer` check bound to the GitHub Actions app, up-to-date
  branches, enforcement for administrators) and `CODEOWNERS` for `src/render/**`,
  `.github/workflows/**` and `Dockerfile.renderer` are recommended to the owner; the trust rule
  (R1) does not rest on them.

## Verification

- Unit tests of the judge on recorded Chrome outputs (`test/render/`), so `npm run check` needs
  no browser; a coverage floor.
- Seeded cases, each caught: a wrong model entry, overlapping cells, text off the page, a marker
  off the page, a line under a `<`, a missing glyph (U+2070), a script in the div, an XML
  `parsererror`, a border snapped differently at ratio 1.5, two glyphs 0.2 px apart across lines.
- The image build refuses a record it cannot reproduce, and one whose pinned bytes are missing.
- Two draws per record agree; the draw on a second CPU type agrees once; the calibration passes.
- The lookup's refusals, each tested; the gate's recomputation repeats the lookup.

## Reviews

1. **First independent review** (2026-09-25), with Chrome for Testing 154 and the pinned fonts.
   High: nothing enforced that a record reproduces; the gate version did not cover the code the
   judge imports; an emulated device pixel ratio does not change layout; `getPlatformFontsForNode`
   counts a missing glyph under the font; DevTools reports no glyph positions, so ink "at Chrome's
   glyph boxes" was not Chrome's. Medium: markers, the withheld list in the record, the lookup key,
   cost, the width, tolerances, baselines and line boxes, R3's scope, the record's fields, fetching
   from the authority. Fixed in the second draft.
2. **Second independent review** (2026-09-25). High: the verdict cache was writable by a pull
   request in its own scope and by any job on main, so a redraw could be skipped; painted pixels
   fall outside exact glyph boxes (baselines snap to whole device pixels; anti-aliasing), so check
   1 could pass touching glyphs. Medium: check 1 as written refused every label (a font's own
   neighbouring glyphs overlap) and ligature rectangles are split, not shared; the shaping list
   named the wrong features and left out `kern`; mark placement in fonts without mark lookups;
   the baseline rule wrong at ratios above 1 without `--disable-font-subpixel-positioning`;
   "line box" is not what the rectangles give; check 7's transparent drawing undefined; captures'
   hash contradicted reproduction; the record could not tell a defect from a refusal of ours; the
   cost exceeds GitHub's runners; the local `deploy.sh` route skipped the redraw. Fixed in this
   draft: the redraw moves into `cloudbuild.images.yaml`, which every deploy route runs, with no
   cache; ink bounds rounded out to device pixels with snapping and anti-aliasing, calibrated at the
   pages' origins; check 1 exempts only consecutive characters of one text node; shaping bounded by
   every glyph HarfBuzz's default features reach, marks by every placement; the flag added; the
   vertical frame judged on ink; check 7 defined; captures kept out of the record; the record
   separates refusals, failures and lower-bound defects; the judge's hash is its deterministic
   bundle.
