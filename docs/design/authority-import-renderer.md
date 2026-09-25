# The renderer gate: a pinned browser's evidence for every authority import (roadmap 3a, PR 3c)

- Status: proposed, 2026-09-25 (architecture approved by the owner the same day); fifth draft,
  after four design reviews
- Decides: what `docs/design/authority-import-t.md`'s "What waits for PR 3c" left open: who draws,
  what is drawn and measured, the evidence record and its store, and the `rendering` stage
- Amends: ADR 0005 (decision 1's renderer cross-check); `docs/design/authority-import-t.md` (the
  store and who writes it, settled; the frame; the gap between cells; the tolerances);
  `docs/design/authority-import-contract.md` (D1's trust statement, D6, D10, D12, D13, D14)
- Related: ADR 0003, ADR 0004 (the importer lock), `docs/design/authority-import-withheld.md`

## What this is for

T decides from the markup alone what the text is, and refuses presentation that can hide or alter
it in any layout. It cannot decide whether glyphs drawn at a given width and font touch, whether
two cells' text reads as one, or whether a line runs under a sign. So an import is accepted only
for a publication a pinned browser has drawn and found free of hidden, overlapping or displaced
text (T's opening; ADR 0005, amended 2026-09-24). Until the gate exists the importer's
`rendering` stage refuses every publication but a synthetic one.

## Decisions

### R1. Reproduced, attested records

- **What is written.** A small evidence record per publication (R8) in its own store,
  `src/render/records/`, outside `src/authority/`, so a new record does not change the importer's
  version (D10's lock hashes `src/authority/`). A person commits it; nothing trusts it for having
  been committed.
- **One pin set.** The render build draws only from the bytes the importer itself pins: the
  publication in `labels/` (D14), and each picture from the importer's own pinned data (D6's
  fetched pictures and, from 3c-E, the authority's export), never from a second copy.
- **The render build draws and attests.** A dedicated Cloud Build configuration,
  `cloudbuild.render.yaml`, run under its own service account (the **render identity**), for each
  record without an attestation:
  1. in a step with `--network none`, the workspace mounted read-only, no environment passed in
     and dependencies installed with `--ignore-scripts`: computes the judge bundle's hash and
     checks it against the lock's entry for the record's gate version (R9); checks each pinned
     file's hash; **regenerates the whole record** from the pinned bytes, twice, in separate
     processes, and requires each regeneration to equal the committed record canonically, the
     pixel fields within ε (R7);
  2. only then, in a later step with network: signs an **attestation** `{ keyVersion, gateSha256,
recordSha256, documentSha256, pinsSha256, rendererImageDigest }` with a KMS key version only
     the render identity may use, and writes it (`objectCreator`, no overwrite) to an attestation
     bucket only the render identity may write, named by the hash of its content.

  Its grants are exactly: Artifact Registry reader (the renderer image), the staging bucket's
  reader, `objectCreator` on the attestation and captures buckets, signer on the attestation key,
  and log writer. A record that does not reproduce gets no attestation.

- **The image build verifies, offline.** Every deploy route (`deploy.yml` and `scripts/gcp/deploy.sh`)
  first downloads the attestations for the store's records (the deployer may read the bucket) into
  the uploaded tree. `cloudbuild.images.yaml` gains a step, `--network none`, before the worker
  image is built: for each record, it computes the record's and the judge bundle's hashes and
  verifies an attestation over them against the public keys pinned in the repository
  (`src/render/attestation-keys/`, one PEM per key version; a rotation adds a key and keeps the
  old). A record without a valid attestation is removed from the image's store, and the lookup
  then refuses its publication (`renderer-evidence-missing`): the render build is off the deploy's
  critical path, and one bad record never blocks a deploy. The worker's digest is taken from the
  build's own results, not from a tag.
- **The trust root, stated.** A record is trusted because the render identity drew it twice from
  the reviewed judge and pinned bytes and signed that it reproduced. Whoever can change
  `cloudbuild.render.yaml`, the pinned keys or act as the render identity (the project's deployers;
  without branch protection, whoever can push to `main`) can change that; they can also ship an
  importer without the stage, so the gate adds no trust beyond them and takes none away. The
  producer, whom D1 does not trust, can neither draw nor sign.
- **Pull requests.** CI's `renderer` job is a pre-check: it builds its own renderer image from
  `Dockerfile.renderer` (it cannot pull the private one; its digest differs), runs the judge's unit
  tests, R3 on every accepted T case at the named widths, recomputes every record's output hashes
  against the current T and scanner (R3) so a change to T that makes a record stale fails the pull
  request that makes it, and draws the records whose bytes, gate hash or pinned files differ from
  `main`'s (by git content, never by an Actions cache). Captures of the failures and defects are the
  run's artefacts, so the reviewer of a record sees the drawing.
- **What Zone B does.** The `rendering` stage is a pure, synchronous lookup inside
  `importPublication`, with the store passed in as data (as the mapping is). D10's check order is
  unchanged; the gate's recomputation (D1) repeats it; the record's hash goes into the signed run
  manifest (R10), and, when a section is withheld, into the submission (the withheld note).
- **Why not draw in the worker.** A browser in Zone B would recompute the drawing too, but it
  does not fit the worker's 1 GiB, needs its own network blocking, adds an image digest the
  manifest does not record, is invisible to the vulnerability scan, and would do nothing until
  PR 5 lifts the dry run. The judge can later run in the worker image unchanged.

### R2. What is drawn

- **The page.** For each section on its own, loaded at a fixed origin (`https://renderer.invalid/`,
  served by request interception, so relative references resolve and fail the same way every
  time): `<!DOCTYPE html>`, a `meta charset`, no stylesheet, and
  `<body style="margin:0;padding:16px">` whose only child is the div. The padding is a viewer's page
  margin (padding, so a first block's negative margin cannot collapse through it), so an italic
  overhang or a slightly negative indent is drawn, not clipped. Scripts are disabled
  (`Emulation.setScriptExecutionDisabled`); the judge measures in an isolated world. The authority's
  class stylesheet is not applied (ADR 0005). So the gate draws **the FHIR div as published, with its
  inline styles only, in fonts metric-compatible with those it names**; the EMA viewer's own
  stylesheet is not modelled, a stated residual.
- **Two drawings of every section T transforms.** The authority's div as served, and T(div).
  Geometry is measured on the authority's drawing; the text, list numbers, table grids and
  pictures, which do not depend on the width, are compared between the two at the named widths. A
  section T refuses is drawn once, as served.
- **Two modes.** HTML (`text/html`) and XML (`application/xhtml+xml`). Geometry is measured in HTML
  mode; in XML mode R3 runs in full and the geometry of the text, markers and cells (not of
  elements the HTML parser inserts, such as `tbody`) must equal HTML mode's at the named widths
  (813, 360 and 1 240 px) at every ratio; a `parsererror` is a refusal of ours.
- **Pictures.** A picture is drawn from the bytes the import carries for it, its reference replaced
  for the drawing only by the `data:` URI; the record lists each section's pictures as
  `{ reference, sha256 | "none" }`, which the lookup requires to equal the import's recomputed
  `pictures` (R5). A picture the import does not carry (a withheld section's, or one the import
  proves draws as nothing, D6) is `"none"` and drawn as the authority's viewer draws a reference it
  cannot resolve: rewritten to one fixed absolute URL that fails, so Chrome draws its broken-image
  box (16 × 16 px, its `alt` text box where the div gives one, or its declared size); a test asserts
  each. No defect (R8) may rest on a box of a picture drawn so.
- **Widths.** The width is the div's content-box width, asserted by the judge; the viewport is
  32 px wider. A div with its own padding or border, or whose content box differs from the width,
  is a refusal of ours. Every integer width from 320 to 1 920 CSS px at ratio 1 (the EMA viewer's
  narrative column is 813 px in a 1 280 px window and 1 240 px in a 1 920 px one; a phone's is 320
  to 430 px), each a relayout of the loaded page (`Emulation.setDeviceMetricsOverride`, width only;
  measured equal to a fresh load).
- **Device pixel ratios.** An emulated ratio does not change layout, so each ratio is its own
  browser process launched with `--force-device-scale-factor`: 1, 1.25, 1.5, 1.75, 2 and 3 (zoom
  times ratio is the effective ratio, so these cover zoom); at ratios other than 1, every integer
  width from 320 to 1 280 px. Layout does change with the ratio (the tablets' 5.1 alone is
  10 776 px tall at 813 px and ratio 1, 10 730 px at ratio 3). A test asserts snapped border widths
  per ratio.
- Fractional widths and ratios between those drawn can break lines in combinations no drawn
  layout has: a stated residual.

### R3. T's model against the drawing, and the fonts of every section (PR 3c-B)

- **Fonts and scripts, every section.** For every text node of every section drawn (T's or not),
  the platform font is exactly one pinned face, the one R6 binds to the family and face the node
  names; a family R6 does not bind (Verdana, Segoe UI, a symbol font) is a refusal of ours
  (`font-unpinned`), so no measurement is ever made in a substitute font. The scripts the gate
  bounds are Latin, Greek, Common and Inherited (the labels' α, β and μ are Greek; the pinned faces
  cover it); a run in another script is a refusal of ours.
- **T's model.** For every text node and list marker of a section T transforms, in both modes at
  the named widths, Chrome's computed style against T's model (`computeStyle`) for every property
  the model holds: `font-size` (under `smaller` against the model's range, × 0.75 to × 0.9),
  weight, style, `color`, `line-height`, `-webkit-text-decorations-in-effect`, the chain of
  backgrounds, `text-indent`, margins, paddings, borders (width as snapped, style, colour),
  `border-collapse`, cell padding and spacing, `display`, `position`, `top`, `bottom` and
  `vertical-align`. Any difference is a refusal of ours. List numbers come from the accessibility
  tree and are compared with the scanner's; each table's grid from cell rectangles, compared with
  T(div)'s. Each character box's height must equal the pinned face's ascent plus descent at its
  computed size, rounded at the device size (R4).
- **What the record binds.** Not T's or the scanner's code but their outputs, per section: T(div)'s
  hash; the hash of T's model output (every text node's and list marker's modelled style, fold
  decision and waiver); and the scanner's text hash (which holds the list numbers, T3d). A change to
  T or the scanner that leaves a label's outputs alone leaves its record valid; one that alters them
  makes the record stale (R5), which the pull request's pre-check reports (R1).

R3 ships first: it makes the third code review's one-off comparison a merge gate on T.

### R4. Geometry (PR 3c-C)

**Where boxes come from.** DevTools reports no glyph ids, positions or baselines. The sources:
per-character rectangles (`Range.getClientRects`), whose horizontal extent is each character's
exact advance and whose vertical extent is the face's content area; Chrome splits a ligature's
advance evenly across its characters, and a mark takes its cluster's rectangle. Everything else is
computed from the pinned fonts and bounded:

- **Baseline.** The rectangle's top plus the face's ascent at the computed size, rounded at the
  device size; with `--disable-font-subpixel-positioning` (R6) Blink's Linux adjustment that
  moves a pixel from ascent to descent under sub-pixel positioning does not apply. Glyphs are
  drawn with the baseline snapped to whole device pixels, so the baseline is known to within one
  device pixel, and every bound below allows for it.
- **Ink bound.** For each character, the union of the ink boxes (the `glyf` bounding boxes,
  overhangs included) of every glyph reachable for it through the lookups of HarfBuzz's default
  features for the run's script and language, as the pinned face has them, displaced by every
  positioning those features can apply (`kern` is advance-only in the pinned faces, value format
  4, which the rectangles already hold; Carlito's `cpsp` places capitals by up to 40 units), and
  its canonical compositions; a ligature's characters are one unit, bounded by the ligature's
  glyph over their joint advances. A mark's ink is the union of every placement it can take: every
  anchor vector of the face's `mark` and `mkmk` lookups for its base, the unattached position at
  the end of the base's advance, and the sum for stacked marks; a mark in a face with no mark
  lookup for its base (HarfBuzz's own fallback, and so every mark in Carlito) is a refusal of ours.
- **Rounding out.** Each ink bound is widened by the baseline's snapping (one device pixel
  vertically, and horizontally too since positions are whole pixels without sub-pixel
  positioning), by anti-aliasing (one device pixel), and by the largest per-glyph difference
  between the pinned and original outlines (measured once from the original fonts' files and
  stated), then rounded outwards to whole device pixels at the drawn ratio.
- **Calibration, per record.** In every draw, for every (face, computed size, ratio) the record
  uses, at the fractional origins the page uses, the pixels a glyph paints on a page of its own
  must lie inside its rounded-out ink bound. A glyph that fails is a refusal of ours, not a
  widened bound.
- **Markers.** A list marker's box from `DOM.getBoxModel` on its `::marker` pseudo-node, bounded as
  text.
- **Lines.** Borders, rules, decoration lines and the edges of backgrounds, at their positions
  snapped to device pixels.

Each check, on the authority's drawing, at every width and ratio of R2 ("ink" is the rounded-out
ink bound). Each is conservative: it passes only what the bounds prove, and a failure says the gate
cannot prove the drawing sound, not that it is unsound (R8 separates the two).

1. **Overlap.** No two inks meet, except two consecutive characters (or ligature units) of the
   same line fragment of the same text node, which a font's own spacing draws; every other pair,
   across lines, elements, cells, shifts or pictures, is checked. No painted box that is not the
   glyph's own or an ancestor's (a background, a border, a picture) meets an ink, whatever the
   painting order.
2. **Every drawn line as ink.** A line that crosses, or comes within 0.5 CSS px of, any ink, above,
   below or beside it, fails, except a run's own underline under unshifted glyphs the allowlist
   accepted, and a `+` T5 waived under its own run's underline. In a section T refuses, T's
   allowlist and waiver do not apply, so its underlines fail this check (a failure, not a defect).
3. **A line under text is an underline.** Any horizontal line under text is judged by the
   allowlist over the stretch of the line of text above it that it spans, a code point counting
   as underlined where its ink lies within 0.3 em (its computed size) above the line.
4. **The gap between cells.** Between any two cells whose text stands side by side on one line
   (whether they share a vertical edge or not: a `rowspan` or an empty cell between them), the gap
   between their nearest text, measured on the union of advance boxes and inks, is at least
   0.25 em of the larger computed size. (T's note named advance boxes and shared edges; this is
   stricter.)
5. **The frame.** Horizontally, every advance box inside its frame (the section `div`'s content
   box outside tables, widened on the right by 1.2 pt; in a table its cell's or caption's content
   box, a collapsed border counting half inside it), with a tolerance of 1/32 CSS px. Vertically,
   every ink inside its frame's border box: Chrome exposes no line box (the rectangles are content
   areas, which a `line-height` or a `position` shift moves), so the vertical frame is judged on ink,
   where T's note named the line box; text shifted by `position` is left to checks 1 and 2, as T's
   note requires.
6. **Reach.** No advance box beyond the page's left or top edge (the viewport's, outside the 16 px
   padding), and none clipped; overflow to the right, which the page scrolls, is allowed.
7. **Visibility and contrast.** At the named widths and every ratio, a second drawing with each
   glyph's fill and its decorations transparent (`-webkit-text-fill-color` and
   `text-decoration-color`; borders keep their colour; layout asserted unchanged): the pixels inside
   each ink must differ between the two drawings (nothing covers the glyph), and the glyph's colour
   must have at least 4.5:1 against the second drawing's pixels behind it, excluding its own
   accepted underline.
8. **Folds.** A glyph T left on the baseline has its baseline within 0.1 em (its computed size)
   plus one device pixel of its line's; a folded glyph's baseline stands at least 0.2 em minus one
   device pixel from the baseline of the unshifted text beside it on the same line.

**Tolerances re-measured.** Before a gate version is set, 0.5 px, 0.3 em, 0.25 em, 1/32 px and the
fold bounds are measured again on the pinned labels in the pinned fonts, and each is kept only if
the labels' accepted drawings clear it with a stated margin; T's note records the values.

### R5. The `rendering` stage: what the lookup requires

The stage finds the record for the document's SHA-256 (among those the image build verified, R1)
and requires:

- the record's `authority` and document `id` equal the request's; its `gateVersion` and
  `gateSha256` (R9) are this build's; and its `widths`, `ratios`, `modes` and `tolerances` equal the
  gate's constants;
- the record's sections are exactly the document's, in pre-order, matched by `path` and `code`;
  for a carried section also by the three output hashes this import computes (T(div), T's model
  output, the scanner's text; R3), and by its pictures, which must equal the import's recomputed
  `pictures` for the section (R2);
- every carried section has no refusal and no failure (and so no defect: every defect is also a
  failure, R8);
- the withheld sections: the withheld note's W1, read from the record's `refusals` and `defects`.

Reasons: `renderer-evidence-missing`, `renderer-evidence-stale` (another gate, constants, output or
picture this import does not compute), `renderer-evidence-failed`, and the withheld note's. A
synthetic publication with nothing withheld passes, as today; one with a withheld section needs a
record (CI draws the synthetic vectors), or it refuses. The importer's golden vectors use a synthetic
store, so adding a record does not change them.

### R6. The image: pinned browser, pinned fonts

`Dockerfile.renderer`, pinned as `Dockerfile.validator` is, its pins read by one reader
(`scripts/ci/renderer-pins.mjs`) used by the Dockerfile check and the lock. The render identity
builds it in Cloud Build when its pins change and pushes it to Artifact Registry; it is used by
digest (`rendererImageDigest`, in the lock and in every attestation), so a draw does not download
Chrome or depend on `snapshot.debian.org`:

- base: the worker's own `node:22.22.0-bookworm-slim@sha256:…`, its shared libraries from a dated
  `snapshot.debian.org` mirror;
- Chrome for Testing's `chrome-headless-shell`, linux64, at a pinned version by SHA-256 (the
  current stable, 154.0.8037.57, when 3c-B lands), run with `--no-sandbox` inside a container that
  has no network, a read-only workspace and no credentials; it carries its own ICU, which the
  judge uses by measuring grapheme clusters in the page;
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
the face. Every code point is checked against the cmap of the pinned face that draws it; a code
point the face lacks is a refusal of ours (`font-coverage`), except a closed list of substitutions
Blink and HarfBuzz make, each asserted by a pixel test: U+00A0 drawn as the space's glyph, U+2011
as the hyphen's, U+00AD drawn as a hyphen only at a line break. A test seeds U+2070, which
Liberation Serif lacks.

### R7. Determinism

A regeneration reproduces a record when it gives:

- **the record itself**, canonically equal, but for the pixel fields: the layout (every measured
  rectangle and snapped line, as the raw doubles Chrome reports, serialised by RFC 8785 and hashed
  per section, mode and ratio), the outputs, the pictures, the constants and every verdict;
- **pixels** (check 7 and the defects' pixel tests, R8), the contrast and visibility margins within
  ε = 0.02, and a margin within ε of its threshold counts against the section (a failure, never a
  defect), so no verdict flips between CPUs.

The render build regenerates twice (R1). Once, 3c-C draws on two CPU families (Cloud Build's E2
machines vary) and records that the layout hashes agree; a later disagreement fails the render
build for that record, a false failure that is investigated, never waived.

### R8. The record

`src/render/records/<documentSha256>.json`, canonical JSON:

```
{ recordVersion, gateVersion, gateSha256,
  renderer: { chrome, imageDigest, fonts: [{ file, sha256 }] },
  document: { authority, id, sha256 }, widths, ratios, modes, tolerances,
  sections: [{ path, code, outputs?: { tDivSha256, modelSha256, textSha256 },
               pictures: [{ reference, sha256 | "none" }],
               layoutSha256: { "<mode>@<ratio>": sha256 },
               pixel: { minContrast, visibilityMargin },
               refusals: [{ reason, at }],
               failures: [{ check, worst, at: { width, ratio, mode } }],
               defects: [{ kind, measure, at }] }] }
```

- `refusals` are ours (R3 differences, `font-unpinned`, `font-coverage`, a run in a script the gate
  does not bound, a mark without a lookup, a calibration failure, an XML `parsererror`, a width
  assertion, and a failed assertion of the defect drawings below): the gate cannot judge the
  section.
- `failures` are R4's conservative checks.
- `defects` are evidence that the drawing misleads a reader, from what Chrome lays out exactly (a
  character's horizontal advance) and paints, never from a content area, a widened bound or a
  threshold of ours. At the first width of each ratio where a check fails, and at the named
  widths, the judge draws twice more, layout asserted unchanged each time: once with only the
  glyphs, each cell's text in its original colour's place drawn red or blue by column parity (the
  two colours whose coverage Skia draws identically to black, measured; the coverage of every pixel
  asserted equal to the original drawing's), and once with only the lines. A glyph pixel is one at
  60 % coverage or more; one between 40 % and 60 % counts for neither side. Three kinds:
  - `cells-run-together`: two cells whose text stands on one line (their baselines within 0.5 em)
    and whose facing characters' advance boxes abut or overlap horizontally, so nothing, not even
    one pixel of space or a line, separates them ("182:8" and "177:12" read as one run);
  - `line-through-letter`: a line (not a run's own decoration) whose pixels lie on a glyph's pixels
    with the glyph's pixels on both sides of the line, between the glyph's baseline and its
    x-height or cap height: a line through the body of a letter, not a touch at a tail or serif
    (seeded controls that must not be defects: "jelly" and an italic "jf" in an unpadded bordered
    cell, a ")" on a bottom border);
  - `off-page`: a character whose advance box lies wholly beyond the page's left or top edge by
    more than the face's largest overhang, so none of its ink can be drawn (horizontal advances are
    exact; the one defect read from a box).

  Every defect is also a failure (a `cells-run-together` pair fails check 4, a line through a letter
  fails check 2, an `off-page` character fails check 6); the judge asserts it, and a failed
  assertion is a refusal of ours. A test requires that no carried section of a pinned label has a
  defect. They are what the withheld note's W1 reads.

No overall pass and no withheld field: what is withheld is the request's. Captures (screenshots of
the failing and defect regions, clipped, and the full measurements) are not part of the record and
not compared: the render build's later step stores them by hash in a captures bucket of its own.

### R9. Versions and the lock

- `GATE_VERSION` in `src/render/version.ts`, and `src/render/renderer.lock.json` mapping each
  released version to `gateSha256`: the hash of the judge as it runs, a single bundle built
  deterministically from `src/render/` with every module it imports except T and the scanner (the
  underline allowlist and the font parser from `node_modules` included), the page-side script,
  `Dockerfile.renderer`, its pins and image digest, the fontconfig, the tolerances and the gate's
  constants (widths, ratios, modes). T and the scanner are the bundle's two declared externals,
  loaded from the build they run in: the judge uses them only to compute the outputs the record
  binds (R3), which the lookup recomputes, so their code is not in the hash. The judge reads no file
  but the pinned bytes and fonts, and imports nothing else dynamically; the lock file is not in its
  own hash. The importer lock's first-parent rule applies.
- Any change to the bundle, the image, the tolerances or the constants is a new gate version, and
  every record is drawn and attested again. A change to T or the scanner that alters a label's
  outputs makes that label's record stale, and only it is redrawn.
- The importer's version changes when the `rendering` stage changes (3c-D), not when a record is
  added. An import's identity is (importer version, gate version, record hash); the gate compares
  all three, and the manifest records all three.

### R10. The run manifest, and re-verification

`AuthorityFetchSchema` gains `authority` and `rendering: { gateVersion, gateSha256, recordSha256 }`,
the latter required when `authority` is not `synthetic` or something is withheld, and absent
otherwise. One minor version of `RUN_MANIFEST_VERSION` carries this and the withheld note's fields,
the previous kept in `AnyRunManifestSchema`. The run's evidence keeps the record's bytes by hash. The
worker image carries the store (`Dockerfile` copies `src/render/records`); re-verifying an import
(`scripts/authority/verify-import.ts`) runs in that import's own worker image, whose store and gate
it used, not in a later one.

### R11. Cost

The reviews measured 211 ms to read the whole tablets label's 105 k rectangles per width on an
M-series core, about 0.5 s per layout with the checks, so about 50 CPU-minutes per label per draw
(6 406 layouts); and 2.0 to 2.6 s per full-page capture of 5.1 alone at ratio 3. So the defect
drawings (R8) are made only at the first failing width of each ratio and at the named widths,
captures are clipped to the failing regions, and screenshots are never taken at every width. The
render build runs on a high-CPU Cloud Build machine (a private pool in the project's region if the
region's default pool has none), sharded by ratio and width range within one build, which alone
signs, with a timeout set from 3c-C's measurement; it runs only when a record or the gate changes,
off the deploy's critical path (R1). If a record's draw exceeds the build's limit, the widths change
by an amendment of this note, reviewed, never by sampling silently.

## Delivery

1. **3c-B**: the image, the CI pre-check, R3.
2. **3c-C**: R4, R7, R8, R9, R11; the render build and attestations; the records of the pinned labels.
3. **3c-W**: the withheld section (its note), which reads the records.
4. **3c-D**: R5 and R10: the lookup, the image build's verification, the manifest, re-verification.
5. **3c-E**: pictures from the authority's export (the owner's decision of 2026-09-25; its own
   note), needed by every pinned label but Imatinib Teva once 5.1 is withheld.

## Stated residuals

- The gate proves what its pinned fonts, widths and ratios draw; a reader's own fonts, a
  fractional width or ratio, or a width above 1 920 px can wrap differently.
- The authority's viewer applies a stylesheet the gate does not.
- The visited link colour is judged statically (T3a).
- Chrome runs without its sandbox inside the render container; an exploit in authority content
  could forge both draws of one build (the container has no network and no credentials).
- Re-verifying an old import needs its worker image, which carries the store, to persist.
- Nothing binds a deployed image to a checked build unless Binary Authorization is enforced (it is
  off by default); branch protection (a required `renderer` check bound to the GitHub Actions app,
  up-to-date branches, enforcement for administrators) and `CODEOWNERS` for `src/render/**`,
  `.github/workflows/**`, `cloudbuild*.yaml` and `Dockerfile.renderer` are recommended to the owner.
  The trust rule (R1) names its root and does not rest on them.

## Verification

- Unit tests of the judge on recorded Chrome outputs (`test/render/`), so `npm run check` needs
  no browser; a coverage floor.
- Seeded cases, each caught: a wrong model entry, overlapping cells, text off the page, a marker
  off the page, a line under a `<`, a missing glyph (U+2070), an unpinned family (Verdana), a Greek
  run bounded, a script in the div, an XML `parsererror`, a border snapped differently at ratio 1.5,
  two glyphs 0.2 px apart across lines, a broken picture's box, a first block's negative margin.
- Defects: the seeded controls are not defects; no carried section of a pinned label has one; the
  tablets' 5.1 shows `cells-run-together` in table 8 (from 320 to 419 px at ratio 1).
- The render build refuses a record it cannot regenerate, one whose pinned bytes are missing, and a
  judge whose hash is not the lock's; the image build drops a record without a valid attestation.
- The layout hashes agree across two CPU families once.
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
3. **Third independent review** (2026-09-25). High: R8's defects were defined on character
   rectangles (content areas), so every underline, a collapsed border in a tight table and a first
   line at the page's top were "defects": a sound section could have been withheld, and every label
   with an underline refused. Medium: a section T refuses got no font check, so a defect could be
   measured in a substitute font; the trust rule's reach (a mutable tag, the bundle's hash not
   checked against the lock, the trust root unnamed); a reference on a page without a base URL
   draws at 0 × 0, not the 16 × 16 broken box; redrawing every record on every deploy costs hours;
   calibration was per gate version, not per record. Fixed in this draft: defects measured on the
   pixels Chrome paints, in a colour-coded drawing, and asserted to be failures too; the font check
   on every section (`font-unpinned`); a render build that draws and signs attestations, which the
   image build verifies; digests from build results; the page at a fixed origin with a 16 px margin;
   calibration in every draw; the record binds T's outputs, not T's code.
4. **Fourth independent reviews** (2026-09-25; one of this note, one of the withheld note reading
   R8). High: picture bytes were not bound between the record and the import (T keeps `img@src`,
   and the render build pinned pictures apart from the importer); the pixel defects still told a
   sound drawing from an unsound one badly (a "j", an italic "f" or a ")" brushing an unpadded
   border was a "line through text"), and missed 5.1's real defect: table 8's cells run together
   with abutting advances while their nearest pixels stand an ordinary letter-space apart. Medium:
   recolouring text changes Skia's coverage (yellow and green by up to 0.18; red and blue not at
   all); the body's margin collapses with a first block's negative margin, and `clientWidth`
   includes padding; the attestation's keys, grants and offline verification unspecified; the
   record's constants not regenerated or checked; the pixel work's cost; Greek runs would refuse the
   label; ADR 0005's amendment stale. Fixed in this draft: one pin set and pictures bound in the
   lookup; `cells-run-together` on exact horizontal advances and `line-through-letter` through a
   glyph's body, with seeded controls; red and blue with coverage asserted equal, a 40–60 %
   exclusion band, two defect drawings; body padding and a content-box assertion; pinned public
   keys, stated grants, offline verification, unattested records dropped, not blocking a deploy;
   the whole record regenerated and its constants checked; defect drawings at the first failing
   width only, clipped captures; Latin, Greek, Common and Inherited bounded; T and the scanner
   declared externals whose outputs are bound; the pre-check reports stale records.
