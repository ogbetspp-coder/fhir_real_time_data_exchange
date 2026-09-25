# The renderer gate: a pinned browser's evidence for every authority import (roadmap 3a, PR 3c)

- Status: proposed, 2026-09-25 (architecture approved by the owner the same day); seventh draft,
  after six design reviews; R4's thresholds provisional, settled by 3c-C's measured design
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

### R1. Attested records

- **What is written.** A small evidence record per publication (R8) in its own store,
  `src/render/records/`, outside `src/authority/`, so a new record does not change the importer's
  version (D10's lock hashes `src/authority/`). A person commits it; nothing trusts it for having
  been committed. A record is read by the hash of its raw bytes, which must be canonical JSON (RFC
  8785), parsed with the parser that refuses duplicate keys.
- **One pin set.** The render build draws only from the bytes the importer itself pins: the
  publication in `labels/` (D14), and each picture from the importer's own pinned data (D6's
  fetched pictures and deletion evidence and, from 3c-E, the authority's export), never from a
  second copy. `pinsSha256` is the hash of the sorted list of every pinned file the record's draw
  read, by path and SHA-256.
- **The render build draws and attests.** A Cloud Build trigger bound to the repository runs
  `cloudbuild.render.yaml`, from the commit it builds, only on first-parent commits of `main`, under
  its own service account (the **render identity**), which no person and no other build may act
  as. For each record without an attestation:
  1. with network: `npm ci --ignore-scripts` from the lockfile, and the renderer image pulled by the
     digest the lock names;
  2. with `--network none`, the workspace read-only and no environment passed in, in a container of
     its own: T and the scanner compute their outputs for every section (R3) from the pinned bytes,
     written as files;
  3. with `--network none` likewise, in another container: the judge, which reads T's and the
     scanner's outputs only as those files and never loads their code, checks its own bundle's hash
     against the lock's entry for the record's gate version (R9) and each pinned file's hash, and
     **regenerates the whole record** twice, in separate processes, requiring each to equal the
     committed record canonically, the pixel fields within ε (R7);
  4. only then, with network: signs an **attestation** `{ environment, keyVersion, commitSha,
 gateSha256, recordSha256, documentSha256, pinsSha256, rendererImageDigest }` with a KMS key
     version of that environment's key, which only the render identity may use, after checking that
     `commitSha` is a first-parent commit of `main`, and writes it, create-if-absent
     (`ifGenerationMatch=0`; "exists" is done), to `attestations/<gateSha256>/<recordSha256>.json` in
     a bucket only the render identity may write. A record whose attestation already exists at that
     path is not drawn again.

  The render identity's grants are exactly: reader on the renderer image repository, the build's
  staging bucket reader, `objectViewer` and `objectCreator` on the attestation and captures
  buckets, signer on its environment's key, and log writer. The renderer image is built and pushed, when its pins change,
  by a separate image build with writer on its own repository (`renderer-images`), not the worker's.
  A record that does not reproduce gets no attestation.

- **Where a record comes from.** The same configuration has a **propose** mode, which a deployer runs
  on any commit under a separate proposal identity that cannot sign: it draws a publication and
  writes the candidate record, with its captures, to a proposals bucket. A person commits that
  record in a pull request; after the merge, the attest mode regenerates it on `main` and signs only
  if it reproduces. The first gate version needs two changes: one adding `Dockerfile.renderer` and
  its pins, after which the image build pushes the renderer image; then one locking its digest and
  the gate, with the first records proposed under that digest.

- **The image build verifies, offline.** Every deploy route (`deploy.yml` and `scripts/gcp/deploy.sh`)
  first downloads, for each record in the store, the attestation at its predictable path, by the
  record's own hash (the deployer may read the bucket), checking each object's name against
  `^[0-9a-f]{64}$`, into
  `render-attestations/`, which is uploaded with the tree (neither `.gitignore` nor
  `.gcloudignore` excludes it). `cloudbuild.images.yaml` gains a step, `--network none`, before the
  worker image is built: for each record, the attestation's signature verifies against a public key
  pinned for this environment (`src/render/attestation-keys/<environment>/`, one PEM per key
  version, less those in its `revoked` list), and `gateSha256` equals the record's and the lock's
  entry for this build's `GATE_VERSION`, `recordSha256` the record's bytes', `documentSha256` the
  record's and its file name's, and `rendererImageDigest` the lock's. The image build does not
  recompute the judge's hash (CI and the render build do). A record without a valid attestation is
  removed from the image's store, and the lookup then refuses its publication
  (`renderer-evidence-missing`): the render build is off the deploy's critical path, and one bad
  record never blocks a deploy. So the deploy that follows a record's merge usually runs before its
  attestation exists and drops it; a later deploy (or a `workflow_dispatch` of `deploy.yml`)
  activates it. The worker's digest is taken from the build's own results.
- **The trust root, stated.** A record is trusted because the render identity, run only from
  `main`, drew it twice with the reviewed judge from pinned bytes and signed that it reproduced.
  Whoever can change `main`, `cloudbuild.render.yaml`, the trigger or the pinned keys, or act as the
  render identity (the project's deployers, who hold `serviceAccountUser`, and can run the trigger
  on another commit, which the `commitSha` check then refuses; without branch protection, whoever can
  push to `main`) can change that; they can also
  ship an importer without the stage, so the gate adds no trust beyond them and takes none away.
  The producer, whom D1 does not trust, can neither draw nor sign, and a pull request's own build
  configuration never runs as the render identity.
- **Pull requests.** CI's `renderer` job is a pre-check: it builds its own renderer image from
  `Dockerfile.renderer` (it cannot pull the private one; its digest differs), runs the judge's unit
  tests, R3 on every accepted T case at the named widths, recomputes every record's output hashes
  against the current T and scanner so a change that makes a record stale fails the pull request
  that makes it, and draws the records whose bytes, gate hash or pinned files differ from `main`'s
  (by git content, never by an Actions cache). Captures of the failures and defects are the run's
  artefacts, so the reviewer of a record sees the drawing.
- **What Zone B does.** The `rendering` stage is a pure, synchronous lookup inside
  `importPublication`, with the store passed in as data (as the mapping is). D10's check order is
  unchanged; the gate's recomputation (D1) repeats it; the record's hash goes into the signed run
  manifest (R10), and, when a section is withheld, into the request and the submission (the
  withheld note).
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
- **Pictures.** Every `img` is drawn as the import carries it, its reference replaced for the
  drawing only:
  - a picture whose bytes the import carries (a `data:` URI, a contained Binary, a fetched or
    exported picture; D6) is drawn from them;
  - a picture D6's evidence shows the authority's viewer draws as nothing is drawn as an empty box
    of the evidence's `drawnBox` (0 × 0 if nothing), never a broken-image box;
  - a reference with no pinned bytes and no evidence, which only a withheld section can hold, is
    rewritten to one fixed absolute URL that fails, so Chrome draws its broken-image box (16 × 16,
    its `alt` box, or its declared size); a test asserts each.

  The record lists, per section, each picture as `{ reference, form, sha256 | "none", box }`: its
  `src` as served, its form (`data`, `contained`, `fetched`, `export`, `not-drawn`, `unpinned`), the
  SHA-256 of the bytes drawn, and the box drawn for a not-drawn one. The import computes the same
  list per section from its own recomputation (D6's `pictures`, with the section of each and the
  forms D6 leaves out of `pictures`), and the lookup compares the two for every carried section
  (R5). No defect (R8) may involve a picture's box.

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
  the named widths, Chrome's computed style against T's model output for every property the model
  holds: `font-size` (under `smaller` against the model's range, × 0.75 to × 0.9), weight, style,
  `color`, `line-height`, `-webkit-text-decorations-in-effect`, the chain of backgrounds,
  `text-indent`, margins, paddings, borders (width as snapped, style, colour), `border-collapse`,
  cell padding and spacing, `display`, `position`, `top`, `bottom` and `vertical-align`. Any
  difference is a refusal of ours. List numbers come from the accessibility tree and are compared
  with the scanner's; each table's grid from cell rectangles, compared with T(div)'s. Each character
  box's height must equal the pinned face's ascent plus descent at its computed size, rounded at the
  device size (R4).
- **The output format.** T's model output is specified in 3c-B's addendum to this note before R3
  lands: its entries keyed by the pre-order index of each text node and marker in the authority's
  div (the same in both modes by T1's one-tree rule); each property as T models it (a size's range
  under `smaller`, a colour's set under a link, a line height's kind, each element's fold); the
  code-point offsets of T5's waivers and of the scanner's text for each drawn code point, which the
  allowlist's neighbours need; how points compare with Chrome's serialised pixels; and the
  per-section picture list (R2). Its serialiser is T's code, under D10's lock, and step 2 of the
  render build (R1) emits it with the scanner's text and the picture list.
- **What the record binds.** Not T's or the scanner's code but their outputs, per section: T(div)'s
  hash; the hash of T's model output (every text node's and list marker's modelled style, fold
  decision and waiver, serialised canonically); and the scanner's text hash (which holds the list
  numbers, T3d). The judge reads T only through those serialised outputs (R1 step 2). A change to T
  or the scanner that leaves a label's outputs alone leaves its record valid; one that alters them
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
  positioning those lookups can apply (`kern` is advance-only in the pinned faces, value format 4,
  which the rectangles already hold; Carlito's `cpsp` is not a default feature, and its 40-unit
  placement is included only to be safe), and its canonical compositions; a ligature's characters
  are one unit, bounded by the ligature's glyph over their joint advances. A mark's ink is the union
  of every placement it can take: every anchor vector of the face's `mark` and `mkmk` lookups for its
  base, the unattached position at the end of the base's advance, and the sum for stacked marks; a
  mark in a face with no mark lookup for its base (HarfBuzz's own fallback, and so every mark in
  Carlito) is a refusal of ours.
- **Rounding out.** Each ink bound is widened by the baseline's snapping (one device pixel
  vertically, and horizontally too since positions are whole pixels without sub-pixel
  positioning), by anti-aliasing (one device pixel), and by the largest per-glyph difference
  between the pinned and original outlines (measured once from the original fonts' files and
  stated), then rounded outwards to whole device pixels at the drawn ratio.
- **Calibration, per record.** In every draw, for every (face, computed size, ratio) the record
  uses, at the fractional origins the page uses, the pixels a glyph paints on a page of its own
  must lie inside its rounded-out ink bound. A glyph that fails is a refusal of ours, not a
  widened bound.
- **Glyph pixels** (P1 below): a glyph's pixels are those inside its advance-and-ink region, in
  the drawing with only the glyphs, that differ from the background at a coverage of 40 % or more,
  a coverage within ε of 40 % counting against the section (R7); they are attributed to a glyph by
  its advance, never by colour.
- **Markers.** A list marker's box from `DOM.getBoxModel` on its `::marker` pseudo-node, bounded as
  text.
- **Lines.** Borders, rules, decoration lines and the edges of backgrounds, at their positions
  snapped to device pixels. A glyph's **own frame** is the borders of the cell or caption it stands
  in, the edges of its ancestors' backgrounds, and its own run's decorations; every other line is a
  **foreign** line.
- **Side by side on one line.** Two runs of text stand side by side on one line when their
  baselines are within 0.5 em of the larger computed size of the two.

**What is binding now, and what 3c-C settles.** Six reviews of this section found that geometric
thresholds argued on paper either refuse the label's sound safety sections or miss a real
misreading. So the principles below are binding, with the seeded cases that test them; the exact
thresholds, candidate rules and costs are **provisional**, settled by 3c-C's own design note from the
judge's measurements of the pinned labels, and reviewed there before any record is attested.

**Principles (binding).**

- **P1. Bounds nominate, pixels and exact geometry decide.** A rounded-out ink bound only finds
  candidates. Every verdict is decided on glyph pixels (attributed to a glyph by its advance) or on
  exact horizontal advances, never on a widened bound. Glyph pixels come from drawings made once per
  width and ratio where candidates exist (the page with only the glyphs; with only the lines; with
  the candidate's box alone made transparent), layout asserted unchanged, cropped locally.
- **P2. Thresholds in device pixels, measured.** Every clearance is stated in device pixels (or in
  em of the glyph's own size where the reading depends on the size), set from the pinned labels'
  accepted drawings at every ratio with a stated margin, and never loosened by hand: a label that
  does not clear one refuses until this note is amended by review.
- **P3. A line through a glyph fails, whatever the line.** A line (a border of any cell, a rule, a
  decoration, a background's edge) with one glyph's pixels on both of its sides within the glyph's
  advance fails, own frame or foreign, shifted glyph or not; so does a line on a glyph's pixels. A
  glyph shifted by `position` is judged on its moved pixels.
- **P4. What a font draws is not a collision.** Two characters adjacent in logical order on one line
  fragment, in whatever text nodes, are not checked against each other; a run's own decoration is
  the one drawn by its decorating box, and the collinear decorations of adjacent runs are one line.
  T5's waived `+` is exempt, under its own run's underline, from every check that judges a line
  under text.
- **P5. A line under text is judged as an underline** by the allowlist over the stretch of text it
  spans, a code point counting as underlined where its pixels come within a stated distance, in its
  own em, above the line (`<` over its cell's bottom border reads "≤").
- **P6. Nothing covers a glyph.** A glyph's pixels are the same in the full drawing and in one with a
  candidate box (a background, a border, a picture) alone made transparent; and its colour keeps
  4.5:1 against what is drawn behind it.
- **P7. Text stays readable as laid out.** Two cells' text standing side by side on one line keep a
  stated gap; every glyph stays inside its frame and on the page (no advance left of the page, no
  pixel above the section's div or the page), shifted or not; a folded glyph stands off its
  neighbours' baseline by T4's bound, an unfolded one on it.

**Seeded cases (binding).** Each is a case of the judge's tests in both modes and at every ratio.
Must fail: a colon shifted across its cell's bottom border ("10:1" drawn "10.1"); a border through a
letter; a line under a `<`; overlapping cells; text off the page; raised text above the section; a
marker off the page; a picture over a glyph; a later box in the text's own colour over a glyph; a
missing glyph (U+2070); an unpinned family (Verdana). Must pass: a descender a pixel above its own
cell's border; a `>` 0.36 em above its cell's bottom border (4.2); a descender over an ascender at
`line-height: normal` (4.4's "g" over "b"); `<span>T</span><span>he`; two adjacent `u` elements
(4.2's "Posology for Ph+ ALL in children"); the waived `+` of 4.2's headings; the reporting box's
grey background a device pixel below the line above (4.8); text inside its own inline background;
the last line's descenders at the bottom of the section and of a cell.

**Provisional checks** (the second draft's list, kept as 3c-C's starting point, each to be restated
under P1–P7 with measured thresholds): overlap; every drawn line as ink (0.5 CSS px); a line under
text as an underline (0.3 em); the gap between cells (0.25 em); the frame (1/32 CSS px
horizontally); reach; visibility and contrast (4.5:1); folds (0.1 em and 0.2 em).

### R5. The `rendering` stage: what the lookup requires

The stage finds the record for the document's SHA-256 (among those the image build verified, R1)
and requires:

- the record's `authority` and document `id` equal the request's; its `gateVersion` and
  `gateSha256` (R9) are this build's; and its `widths`, `ratios`, `modes` and `tolerances` equal the
  gate's constants;
- the record's sections are exactly the document's, in pre-order, matched by `path` and `code`;
  for a carried section also by the three output hashes this import computes (T(div), T's model
  output, the scanner's text; R3), and by its pictures, which must equal the list the import
  computes for the section (R2);
- every carried section has no refusal and no failure (and so no defect: every defect is also a
  failure, R8);
- the withheld sections: the withheld note's W1, read from the record named in the request.

Reasons: `renderer-evidence-missing`, `renderer-evidence-stale` (another gate, constants, output or
picture this import does not compute), `renderer-evidence-failed`, and the withheld note's
(`withheld-section-not-shown`, `withheld-evidence-changed`). A
synthetic publication with nothing withheld passes, as today; one with a withheld section needs an
attested record like any other. The importer's golden vectors use a synthetic store, so adding a
record does not change them.

### R6. The image: pinned browser, pinned fonts

`Dockerfile.renderer`, pinned as `Dockerfile.validator` is, its pins read by one reader
(`scripts/ci/renderer-pins.mjs`) used by the Dockerfile check and the lock. The image build of R1
builds it when its pins change and pushes it to `renderer-images`; it is used by digest
(`rendererImageDigest`, in the lock and in every attestation), so a draw does not download Chrome or
depend on `snapshot.debian.org`:

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
- **pixels** (the candidates' resolutions, check 7 and `line-through-letter`), the contrast and
  visibility margins within ε = 0.02, and a coverage or margin within ε of its threshold counts
  against the section (a failure, never a defect), so no verdict flips between CPUs.

The render build regenerates twice (R1). Once, 3c-C draws on two CPU families (Cloud Build's E2
machines vary) and records that the layout hashes agree; a later disagreement fails the render
build for that record, a false failure that is investigated, never waived.

### R8. The record

`src/render/records/<documentSha256>.json`, canonical JSON:

```
{ recordVersion, gateVersion, gateSha256,
  renderer: { chrome, imageDigest, fonts: [{ file, sha256 }] },
  document: { authority, id, sha256 }, pinsSha256, widths, ratios, modes, tolerances,
  sections: [{ path, code, outputs?: { tDivSha256, modelSha256, textSha256 },
               pictures: [{ reference, form, sha256 | "none", box? }],
               layoutSha256: { "<mode>@<ratio>": sha256 },
               pixel: { minContrast, visibilityMargin },
               refusals: [{ reason, at }],
               failures: [{ check, worst, at: { width, ratio, mode } }],
               defects: [{ kind, measure, at: [{ width, ratio }] }] }] }
```

- `refusals` are ours (R3 differences, `font-unpinned`, `font-coverage`, a run in a script the gate
  does not bound, a mark without a lookup, a calibration failure, an XML `parsererror`, a width
  assertion, a layout that changed between a drawing and its transparent or clipped counterpart,
  and a defect that is not also a failure): the gate cannot judge the section.
- `failures` are R4's conservative checks.
- `defects` are geometric findings that a reader cannot read the drawing as written, which the
  person who withholds a section confirms against the record (the withheld note's W1); they are
  never taken from a content area or a threshold of ours:
  - `cells-run-together`, at every width and ratio (it needs no drawing): two cells whose text stands
    side by side on one line (R4), whose facing characters (not whitespace, a no-break space or a
    zero-width code point) have advances that abut or overlap horizontally (a gap of zero or less in
    Chrome's layout units, 1/64 px), so no more space
    separates the cells than separates two letters of a word ("182:8" beside "177:12" read as one
    run); a split number such as "12" beside ".5" is the same geometry, which is why a person
    confirms each;
  - `off-page`, at every width and ratio: a character whose rounded-out ink bound, which contains
    all its ink, lies wholly beyond the page's left or top edge, so none of it is drawn;
    - `line-through-letter`, from glyph pixels (R4) at every width where P3's candidates arise: a line
      (not a run's own decoration) with a glyph's pixels on both of its sides, inside the
      band from one device pixel above the glyph's baseline to one below its x-height (or cap height
      for a capital), the baseline's snapping allowed for: a line through the body of a letter, not a
      touch at a tail or serif; seeded controls that must not be one: "jelly" and an italic "jf" in an
      unpadded bordered cell, a ")" on a bottom border.

    Every defect is also a failure (a `cells-run-together` pair fails P7's gap, an `off-page` character
    P7's page, a line through a letter P3); a defect that is not is a refusal of ours. No defect
    involves a picture's box. A test requires that no carried section of a pinned label has one.

No overall pass and no withheld field: what is withheld is the request's. Captures (clipped
screenshots of the failing and defect regions, and the full measurements) are not part of the
record and not compared: the render build's last step stores them by hash in its captures bucket.

### R9. Versions and the lock

- `GATE_VERSION` in `src/render/version.ts`, and `src/render/renderer.lock.json` mapping each
  released version to `gateSha256`: the hash of the judge as it runs, a single bundle built
  deterministically by a pinned bundler (its version in the lock) from `src/render/` with every
  module it imports (the underline allowlist and the font parser from `node_modules` included), the
  page-side script, `Dockerfile.renderer`, its pins and image digest, the fontconfig, the tolerances
  and the gate's constants (widths, ratios, modes). T and the scanner are not in the judge: they run
  in the render build's own step (R1) and hand the judge their outputs, which the record binds and
  the lookup recomputes. The judge reads no file but the pinned bytes, fonts and those outputs, and
  imports nothing dynamically; the lock file is not in its own hash. The importer lock's
  first-parent rule applies.
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
(6 406 layouts); a full-page capture of 5.1 at ratio 3 takes 2 to 6.5 s. So pixels are drawn only
drawn only at widths and ratios where candidates exist, once per drawing (glyphs only, lines only,
a box made transparent), and cropped locally; the advance-only defects are computed from the
rectangles already read. The sixth review measured 709 to 741 own-frame candidates per layout in
4.8 and a full-page capture of 4.8 at 0.13 s (ratio 1) to 0.73 s (ratio 3): about two CPU-hours per
draw for 4.8 alone, which 3c-C measures again before setting the build's timeout. The render build runs on a
high-CPU Cloud Build machine (a private pool in the project's region if the region's default pool
has none), sharded by ratio and width range within one build, which alone signs, with a timeout set
from 3c-C's measurement of the pinned labels (4.8's candidates the largest share); it runs only when
a record or the gate changes, off the deploy's critical path (R1). If a record's draw exceeds the
build's limit, the widths change by an amendment of this note, reviewed, never by sampling silently.

## Delivery

1. **3c-B**: the image, the CI pre-check, R3.
2. **3c-C**: R4, R7, R8, R9, R11; the render build, its trigger and attestations; the records of the
   pinned labels.
3. **3c-W**: the withheld section (its note), which reads the records.
4. **3c-D**: R5 and R10: the lookup, the image build's verification, the manifest, re-verification.
5. **3c-E**: pictures from the authority's export (the owner's decision of 2026-09-25; its own
   note), needed by every pinned label but Imatinib Teva once 5.1 is withheld.

## Stated residuals

- The gate proves what its pinned fonts, widths and ratios draw; a reader's own fonts, a
  fractional width or ratio, or a width above 1 920 px can wrap differently.
- The authority's viewer applies a stylesheet the gate does not, and its page margin may differ
  from the gate's 16 px, which `off-page` and check 6 depend on.
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
- Seeded cases, each caught: a wrong model entry, overlapping cells, text off the page, raised text
  above the section, a marker off the page, a line under a `<`, a border through a letter, a missing
  glyph (U+2070), an unpinned family (Verdana), a Greek run bounded, a script in the div, an XML
  `parsererror`, a border snapped differently at ratio 1.5, two glyphs 0.2 px apart across lines, a
  broken picture's box, a not-drawn picture drawn at its evidence's box, a first block's negative
  margin; and seeded sound cases that must pass: a descender a pixel above its cell's border, text
  inside its own inline background.
- Defects: the seeded controls are not defects; no carried section of a pinned label has one; the
  tablets' 5.1 shows `cells-run-together` in table 8 (from 320 to 419 px and at 671 px at ratio 1;
  320 to 418 px at the other ratios).
- The render build refuses a record it cannot regenerate, one whose pinned bytes are missing, and a
  judge whose hash is not the lock's; it never runs outside `main`'s trigger; the image build drops a
  record without a valid attestation, and one attested in another environment or with a revoked key.
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
5. **Fifth independent reviews** (2026-09-25). Medium: the defect drawings' coverage assertion could
   never pass where a glyph's edge meets a border or where the text is not pure black (5.1's is
   `#231f20`), so 5.1 could not have been withheld; T and the scanner, loaded into the judge's own
   process, were in no hash, and the note did not say which commits the render build runs; a picture
   the viewer draws as nothing was drawn as a broken-image box; the offline steps could not compute
   the judge's hash; check 2, with rounded-out bounds, failed descenders a pixel above their own
   cells' borders in 4.2 and 4.8, safety sections that can never be withheld, so the label could not
   have been imported. Low: the renderer image's builder, `pinsSha256`, the picture comparison,
   raised text above the section, the defects' wording and definitions, keys per environment and
   revocation, record hashing, `cpsp`. Fixed in this draft: `cells-run-together` and `off-page`
   computed from advances and ink bounds at every width, `line-through-letter` from glyph-only and
   line-only drawings attributed by advance, with no recolouring; the render build runs only from
   `main`'s trigger, T and the scanner in their own step handing the judge their outputs; not-drawn
   pictures at their evidence's box; the image build checks attestations against the lock without
   recomputing; checks resolve their bounds' candidates by glyph pixels, and a glyph's own frame is
   judged on pixels (a descender beside its border passes, a border on a letter fails); the rest as
   found.
6. **Sixth independent reviews** (2026-09-25). High: a colon shifted across its own cell's bottom
   border, a dot on each side and no pixel shared, passed every check: "10:1" drawn "10.1". Medium:
   check 3 had lost T5's waived `+` (4.2's headings); checks 1, 3, 5 and 2's foreign lines still
   decided on rounded-out bounds, refusing sound text in 4.2, 4.4 and 4.8, safety sections that can
   never be withheld (neighbours in different text nodes, a descender over an ascender, a `>` 0.36 em
   above its border, the reporting box's background a device pixel below a descender); one
   attestation per (gate, document) could not hold a changed record; the model output's format,
   which the lookup rests on, was unspecified; the pixel work's cost; nothing named who produces a
   record. Fixed in this draft: R4 now binds principles (bounds nominate, pixels and exact advances
   decide; a line through a glyph fails whatever its frame; neighbours in logical order and a run's
   decorating box exempt; the waived `+` exempt everywhere) and seeded cases, sound and unsound, from
   these findings, and leaves the thresholds to 3c-C's measured design; attestations keyed by the
   record's hash, created if absent, bound to a first-parent commit of `main`; the output format's
   contents; a propose mode; captures once per drawing per width.
