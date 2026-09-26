# The renderer gate: a pinned browser's evidence for every authority import (roadmap 3a, PR 3c)

- Status: proposed, 2026-09-25 (architecture approved by the owner the same day); twenty-eighth draft,
  after twenty-seven design reviews; R4's thresholds provisional, settled by 3c-C's measured design
- Decides: what `docs/design/authority-import-t.md`'s "What waits for PR 3c" left open: who draws,
  what is drawn and measured, the evidence record and its store, and the `rendering` stage
- Amends: ADR 0003 (the second stated exception, contacts acknowledged; the third, zoomed out); ADR 0005 (decision 1's
  renderer cross-check); `docs/design/authority-import-t.md` (the
  store and who writes it, settled; the frame; the gap between cells; the tolerances);
  `docs/design/authority-import-contract.md` (D1's trust statement, D2, D3 and D8 (the request's
  review fields), D6, D10, D12, D13, D14)
- Related: ADR 0003, ADR 0004 (the importer lock), `docs/design/authority-import-withheld.md`

## What this is for

T decides from the markup alone what the text is, and refuses presentation that can hide or alter
it in any layout. It cannot decide whether glyphs drawn at a given width and font touch, whether
two cells' text reads as one, or whether a line runs under a sign. So an import is accepted only
for a publication a pinned browser has drawn and found free of hidden, overlapping or displaced
text, save the contacts a person has acknowledged (R4) (T's opening; ADR 0005, amended 2026-09-24). Until the gate exists the importer's
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
  its own service account (the **render identity**; who else can act as it is stated below). For each record without an attestation:
  1. with network: `npm ci --ignore-scripts` from the lockfile, and the renderer image pulled by the
     digest the lock names;
  2. with `--network none`, the workspace read-only and no environment passed in, in a container of
     its own: T and the scanner compute their outputs for every section (R3), and the per-section
     picture list (R2), from the pinned bytes, written as files;
  3. with `--network none` likewise, in another container: the judge, which reads T's and the
     scanner's outputs only as those files and never loads their code, checks its own bundle's hash
     against the lock's entry for the record's gate version (R9) and each pinned file's hash, and
     **regenerates the whole record** twice, in separate processes, requiring each to equal the
     committed record canonically, the pixel fields within ε (R7);
  4. then, with network, writes the captures (clipped screenshots of every failure and every defect,
     each at its worst width and at the first and last width of each ratio where it occurs; of every
     contact, at every distinct mask of pixels it takes (P6); and the full measurements) to `captures/<sha256>` by each file's own hash, create-if-
     absent (so a rerun cannot collide), and an index listing each capture's path, SHA-256, identity,
     width and ratio, create-if-absent at `captures/index/<gateSha256>/<recordSha256>/<buildId>.json`;
     an index a build finds is reused only if every capture it lists verifies, and otherwise the build
     writes its own under its own build id;
  5. only then signs an **attestation** `{ environment, keyVersion, commitSha, gateSha256,
recordSha256, capturesIndex, capturesSha256, documentSha256, pinsSha256, rendererImageDigest }`
     (`capturesIndex` the index's path, `capturesSha256` its hash) with a KMS key version of that
     environment's key, after checking that `commitSha` is a first-parent commit of `main` (the build
     reads the repository's history with the repository connection's read token), and writes it,
     create-if-absent (`ifGenerationMatch=0`; "exists" is done), to
     `attestations/<gateSha256>/<recordSha256>/<keyVersion>.json` in a bucket only the render identity
     may write, so a record attested under a key later revoked can be attested again under a new one.
     A record with a verified attestation under an unrevoked key, and its index, is not drawn again.

  The render identity's grants are exactly: reader on the renderer image repository, the build's
  staging bucket reader, `objectViewer` and `objectCreator` on the attestation and captures
  buckets, signer on its environment's key, the repository connection's token accessor, and log
  writer. The captures bucket keeps objects under a retention policy, and the deployers and the
  people who request imports may read it. The renderer image is built and pushed, when its pins
  change, by a separate image build with writer on its own repository (`renderer-images`), not the
  worker's. A record that does not reproduce gets no attestation.

- **Who can act as the render identity.** Creating or running a trigger that uses a service
  account requires `iam.serviceAccounts.actAs` on it, so whoever manages the trigger, the project's
  owners and deployers, can also submit any build as the render identity and sign anything. The
  `commitSha` check guards against mistakes (a trigger run on another commit), not against them;
  they are the trust root (below).
- **Where a record comes from.** The same configuration has a **propose** mode, run under a separate
  proposal identity that cannot sign, by a person granted `actAs` on that identity alone (the owner,
  or a developer the owner names): it draws a publication at any commit and writes the candidate
  record, with its captures, to a proposals bucket. A person commits that record in a pull request;
  after the merge, the attest mode regenerates it on `main` and signs only if it reproduces. The
  first gate version needs two changes: one adding `Dockerfile.renderer`, its pins,
  `cloudbuild.renderer-image.yaml` and the Terraform for the three identities, their triggers, the
  `renderer-images` repository and the buckets (the repository connection to GitHub is a step only
  the owner can take, with exact instructions), after which the renderer image build pushes the
  image; then one locking its digest and the gate, with the first records proposed under that
  digest.

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
  Whoever can change `main`, `cloudbuild.render.yaml`, the trigger, its service agent's grant or the
  pinned keys (the project's owners and deployers; a deployer who runs the trigger on another commit
  is refused by the `commitSha` check; without branch protection, whoever can push to `main`) can
  change that; they can also
  ship an importer without the stage, so the gate adds no trust beyond them and takes none away.
  The producer, whom D1 does not trust, can neither draw nor sign, and a pull request's own build
  configuration never runs as the render identity.
- **Pull requests.** CI's `renderer` job is a pre-check: it builds its own renderer image from
  `Dockerfile.renderer` (it cannot pull the private one; its digest differs, so it compares
  verdicts and outputs, never a record's exact bytes), runs the judge's unit
  tests, R3 on every accepted T case at the named widths, recomputes every record's output hashes
  against the current T and scanner so a change that makes a record stale fails the pull request
  that makes it, and draws the records whose bytes, gate hash or pinned files differ from `main`'s
  (by git content, never by an Actions cache). Captures of the failures and defects are the run's
  artefacts, a pre-check's; the drawing a person reviews before acknowledging a contact or
  withholding a section is the render build's captures of the attested record, in the review item 2's
  signer builds (R5, `docs/design/approval.md`).
- **What Zone B does.** The `rendering` stage is a pure, synchronous lookup inside
  `importPublication`, with the store passed in as data (as the mapping is). D10's check order is
  unchanged; the gate's recomputation (D1) repeats it; the record's hash goes into the signed run
  manifest (R10) and, named by the request's `renderEvidence` (R5), into the submission of every
  authority import that R5 requires to name it (every one but a synthetic import that withholds
  nothing), whose source record carries it as `rendering` (the withheld note's W3).
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
  browser process launched with `--force-device-scale-factor`: 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2,
  2.625 and 3 (a page zoom times the display's ratio is the effective ratio: 80 %, 90 % and 110 % zoom
  on an ordinary display, and a common phone's 2.625, are drawn); at ratios other than 1, every
  integer width from 320 to 1 280 px (R11). At 0.8 and 0.9, **zoomed out**, one kind of contact
  that changes no letter is not judged (R4; ADR 0003's third stated exception, the owner's decision
  of 2026-09-26). Layout does change with the ratio (the tablets' 5.1 alone is
  10 776 px tall at 813 px and ratio 1, 10 730 px at ratio 3). A test asserts snapped border widths
  per ratio.
- Fractional widths and ratios between those drawn can break lines in combinations no drawn
  layout has, and can move a glyph against a line or another glyph (by up to two device pixels, in
  the nineteenth review's measurements): a stated residual, narrowed by the added ratios, not
  bounded by them (the nineteenth review found an 11 pt serif
  comma two blank rows from its cell's border at every drawn ratio touching it at 1.05, and a 12 pt
  semicolon at 0.95; between 1.25 and 3 it found none). Ratios below 0.8 (Chrome's 75 %, 67 % and
  50 % zoom on an ordinary display) and above 3 are not drawn, a stated residual.

### R3. T's model against the drawing, and the fonts of every section (PR 3c-B)

- **Fonts and scripts, every section.** For every text node of every section drawn (T's or not),
  the platform font is exactly one pinned face, the one R6 binds to the family and face the node
  names; a family R6 does not bind (Verdana, Segoe UI, a symbol font) is a refusal of ours
  (`font-unpinned`), so no measurement is ever made in a substitute font. The scripts the gate
  bounds are Latin, Greek, Common and Inherited (the labels' α, β and μ are Greek; the pinned faces
  cover it); a run in another script is a refusal of ours.
- **T's model.** For every element and list marker of a section T transforms (a text node takes
  its element's style), in both modes at
  the named widths, Chrome's computed style against T's model output for every property the model
  holds: `font-size` (under `smaller` against the model's range, × 0.75 to × 0.9), weight, style,
  `color`, `line-height`, `-webkit-text-decorations-in-effect`, the chain of backgrounds,
  `text-indent`, margins, paddings, borders (width as snapped, style, colour), `border-collapse`,
  cell padding and spacing, `display`, `position`, `top`, `bottom` and `vertical-align`. Any
  difference is a refusal of ours. List numbers come from the accessibility tree and are compared
  with the scanner's; each table's grid from cell rectangles, compared with T(div)'s. Each character
  box's height must equal the pinned face's ascent plus descent at its computed size, rounded at the
  device size (R4).
- **The index space.** Every section, carried or withheld, T's or not, has one index space, which
  the judge assigns itself from the XML-mode DOM of the authority's div (source order; an element the
  HTML parser inserts, such as a `tbody`, has no key; a marker placed before its `li`'s first child;
  an element with no text node, such as an empty bordered cell, keyed for its borders and padding),
  characters counted as code-point offsets in the concatenation, in that order, of the text nodes'
  data as the XML-mode DOM holds it (references decoded, raw CR LF and CR one LF; the addendum's
  M1). The render build's step 2 keys T's model output with the same
  function, tested to agree with the judge on every carried section.
- **The output format.** T's model output is specified in 3c-B's addendum to this note
  (`docs/design/authority-import-renderer-model.md`), reviewed independently before R3 lands: its
  entries in the index space above; each property as T models it (a size's range under `smaller`,
  a colour's set under a link, a line height's kind, each element's fold); the code-point offsets
  of T5's waivers; and how points compare with Chrome's serialised pixels. The scanner's offsets
  for each drawn code point, which the allowlist's neighbours need, are 3c-C's addition to the
  model (`t-model/1.1.0`, a change to T's code under D10's lock, so a new importer version); the
  per-section picture list (R2) is an output of its own, R8's, not part of the model. The
  serialiser is T's code, under D10's lock, and step 2 of the render build (R1) emits the model
  with the scanner's text and the picture list.
- **What the record binds.** Not T's or the scanner's code but their outputs, per section: T(div)'s
  hash; the hash of T's model output (every element's and list marker's modelled style, which its text
  nodes take, fold
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
  device size as Blink rounds it (3c-C states the estimator and seeds 4.8's descenders at ratio 1,
  where P8(a)'s two rows hold with no slack); with `--disable-font-subpixel-positioning` (R6) Blink's Linux adjustment that
  moves a pixel from ascent to descent under sub-pixel positioning does not apply. Glyphs are
  drawn with the baseline snapped to whole device pixels, so the baseline is known to within one
  device pixel, and every bound below allows for it, except for the **exact baseline** the zoomed-out
  kind needs at 0.8 and 0.9 (the rectangle-plus-ascent estimate was a device pixel off at 10 pt
  there, measured). Binding: at those ratios, and only for a descender of one of P8(a)'s letters
  against its own underline or its own cell's bottom border, the judge may establish the glyph's
  baseline exactly from the drawing; the method (3c-C's, provisional) changes nothing on the page
  that is judged, derives every reference baseline from drawn pixels (never from the estimate),
  accounts for whatever the raster depends on (the twenty-sixth review measured four horizontal
  sub-pixel phases and a dependence on the text colour at 0.8 and 0.9 on the Mac shell, despite the
  flag), and is checked in every record: an established baseline lies within the estimate's
  one-pixel band and tells its row apart from both neighbours, or it is not established; and each
  record compares the method against controls whose baseline is independently known, at the same
  face, size, ratio, phase and colour, and where they disagree nothing in that record is
  established. Where it is established, it is both the lowest and the
  highest baseline R4 allows for that glyph, for P3, P4, P8(a) and R8 alike; where it is not, the
  glyph keeps the one-pixel allowance and the zoomed-out kind does not apply to it, so it is judged
  by the other rules (clear, clear by P8, a contact or a failure; never a refusal of ours, never
  unjudged). Every
  other glyph keeps the allowance. Every seed at 0.8 or 0.9 involving a descender of P8(a)'s letters
  binds with the exact baseline established, the label's zoomed-out "y"s among them (below).
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
  vertically, and horizontally one device pixel too, since horizontal positions can
  fall between pixels: 3c-C measures on the pinned image whether the flag holds them to whole
  pixels), by anti-aliasing (one device pixel), and by the largest per-glyph difference
  between the pinned and original outlines (measured once from the original fonts' files and
  stated), then rounded outwards to whole device pixels at the drawn ratio.
- **Calibration, per record.** In every draw, for every (face, computed size, ratio) the record
  uses, at the fractional origins the page uses, the pixels a glyph paints on a page of its own
  must lie inside its rounded-out ink bound. A glyph that fails is a refusal of ours, not a
  widened bound.
- **Glyph pixels** (P1 below). Where two candidate glyphs' boxes overlap, the judge isolates each by
  drawing it alone (every other glyph made transparent, and every marker's `::marker` too, layout
  asserted unchanged, and each drawing's computed colours asserted to have taken effect, a drawing
  whose colours did not being a refusal of ours), by a method that does not clip the glyph's ink:
  the isolated drawings, composited source-over in paint order, must reproduce the drawing with
  only the glyphs within ε at every pixel of the isolated glyphs' boxes (every glyph that paints
  there composited), and any ink lost is a refusal of ours (`::highlight()` clips a glyph to its own advance,
  losing an italic "f"'s hook and tail and up to 5 px of a sans "y", measured, so 3c-C uses another
  method, such as colour-only wrappers with every character rectangle asserted unchanged); elsewhere each pixel of the
  drawing with only the glyphs belongs to the glyph whose exact ink box, snapped outwards to device
  pixels, contains it, and an inked pixel of that drawing that belongs to no glyph is a refusal of
  ours. For a failure, a glyph's pixels are
  those above a noise floor of 10 % coverage (so a faint 5 pt colon is still seen); for a defect
  (R8), only those at 40 % or more (so a defect is never found in faint pixels); a coverage within ε
  of either threshold counts against the section (R7). A glyph is "too faint to judge", a refusal of
  ours, when its own raster on R4's calibration page, fixed per face, size and ratio, has no pixel
  above the noise floor, or when it has ink but no pixel above the floor in its own judged or
  isolated drawing (the raster depends on the sub-pixel phase and the colour). The "lines only" drawing sets `color: transparent` with an explicit
  `text-decoration-color` (with a transparent text fill Chrome paints no underline).
- **Markers.** A list marker's box from `DOM.getBoxModel` on its `::marker` pseudo-node, bounded as
  text.
- **Lines.** Borders, rules, decoration lines and the edges of backgrounds, at their positions
  snapped to device pixels. A glyph's **own frame** is the borders of the cell or caption it stands
  in, the edges of its ancestors' backgrounds, and its own run's decorations; every other line is a
  **foreign** line.
- **Side by side on one line.** Two runs of text stand side by side on one line when their
  baselines are within 0.5 em of the larger computed size of the two.
- **Which parameters bind.** The two glyph-pixel thresholds, "side by side on one line" and the
  band `line-through-letter` uses are binding for R8's defects (changing one amends both this note
  and the withheld note, each chosen so a defect is found less often, never more); every other
  parameter of this section is provisional (below).

**What is binding now, and what 3c-C settles.** Ten reviews of this section found that geometric
rules argued on paper either refuse the label's sound safety sections or miss a real misreading:
real authority labels hold many small contacts at some widths and ratios that change nothing a
reader reads (4.2's raised 9 against letters on the line above; the "V" of 4.8's "Appendix V",
kerned against the full stop after it, reaching past its grey background), and a few that do. So
the gate sorts what it draws into three classes (the owner's decision of 2026-09-25), and only the
classes, the principles and the seeded cases bind now; the exact thresholds, candidate rules and
costs are **provisional**, settled by 3c-C's own design note from the judge's measurements of the
pinned labels, and reviewed there before any record is attested.

**Three classes (binding).** Every glyph and every line of the authority's drawing, at every width
and ratio of R2 (at 0.8 and 0.9 as the zoomed-out rule below states), is:

1. **clear**: proven, on pixels and exact geometry, to keep its stated clearance (at least one blank
   device pixel) from every other glyph, every line and every picture's box, inside its frame (its
   cell's or caption's content box, or the section div's), uncovered, on the page and at its model's
   position; or clear by rule (P8); or
2. **a failure**: a clear misreading or a drawing the gate cannot judge, which refuses the section
   (and can be withheld only under the withheld note); or
3. **a contact**: neither proven clear nor a failure, which the gate never passes silently: it is
   recorded, captured, and must be acknowledged, legible, by the person who requests the import
   (R5), or the import refuses.

**Zoomed out (binding).** At ratios 0.8 and 0.9 one kind of contact is neither shown nor
recorded: an unshifted descender (T4's term: under no `position` or `vertical-align` shift, deleted
by T or not, on the glyph or any inline ancestor, and not in `sup` or `sub`) of one of P8(a)'s
letters (with no mark below) against its own
cell's bottom border or its own run's underline on its own line, shared pixels included, where the
tail stays visible: against an underline, at least one row of the tail's pixels at 40 % coverage
or more lies below the drawn line (a row in a cut does not count, since a tail that fills its cut
leaves the line looking unbroken); against a border, at least one free row by P8(a)'s count.
Otherwise (a fused tail, which can read "y" as "v", "g" as "q", "j" as "i") it stays a contact. The kind
covers only that one contact: the glyph's reach past its frame, where P6 finds it, and its other
contacts stay judged. (A glyph at its own background's edge that meets P8(b) is clear at every ratio;
one that does not stays a contact.) This is ADR 0003's third stated exception (the owner's decision of
2026-09-26: at those sizes the label's descenders touch their own lines in hundreds of places that
change no letter, and acknowledging them would make acknowledgement a rubber stamp). Every failure
and refusal of ours is judged there, and every other contact (a comma, semicolon, cedilla, mark
below, digit or symbol touching a line; one glyph against another, the raised 9 among them) stays
a contact, shown and acknowledged.

A pixel value within ε of a threshold (R7) falls in the more severe class of the decision it belongs
to: a failure where the threshold separates a failure, a contact where it separates a contact from
clear. Geometry from layout (advances, boxes) is compared exactly, in Chrome's layout units, with no
ε.

**Principles (binding).**

- **P1. Bounds nominate, pixels and exact geometry decide.** A rounded-out ink bound only finds
  candidates. Every verdict is decided on glyph pixels (attributed as R4 states; a pixel is a
  glyph's at 10 % coverage or more) or on exact horizontal advances, never on a widened bound, but
  for `off-page` (R8), where only the bound can speak for what is not drawn. Glyph pixels come from
  drawings made once per width and ratio where candidates exist, layout asserted unchanged, cropped
  locally.
- **P2. Thresholds in device pixels, measured both ways.** Where a seed's class depends on the face,
  weight, size, ratio or sub-pixel phase, the seed binds its class at the configurations 3c-C
  measures and states; wherever the seed's stated geometry recurs it keeps the same class or a more
  severe one, and elsewhere it does not bind. Every clearance is stated in device pixels (or in em of
  the glyph's own size where the reading depends on the size), at least one blank device pixel,
  set from the pinned labels' drawings at every ratio, and
  required to put every synthetic seeded case in its stated class at T's extremes (5 pt, every ratio)
  and every seed quoting the label in its class at the label's own sizes; never loosened by hand.
- **P3. Failures: clear misreadings.** Each of these is a failure: two cells' text standing side by
  side on one line closer than a stated gap (a fraction of a word space, in em, on exact advances;
  R8's `cells-run-together`, at no gap, is the defect within it); a line through the body of a glyph,
  with the glyph's pixels on both of its sides (a colon shifted across its cell's bottom border,
  "10:1" drawn "10.1"); a part of a glyph's ink hidden in a line (an 8-connected set of at least
  two of the glyph's pixels, or a whole 8-connected component of them of any size, every one of them
  also a pixel of the line, a line's pixel being one at 10 % coverage or more of the line's colour, as a colon's lower dot inside
  a thick border, "10·1", or a raised 7's bar inside its cell's top border); a
  glyph's pixels overlapping a picture's; a glyph covered (its coverage mask changes when a
  background, a border or a picture over it alone is made transparent; glyph over glyph is P6's), or
  under 4.5:1 against what is drawn behind it; a character wholly off the page, any advance left of
  the page, or any pixel of a shifted glyph above the section's top edge or the page's by a device pixel or
  more (ink, not the character box; an unshifted glyph's ink above the section's top, an accented
  capital on a first line at `line-height: 1em`, is a contact, not a failure); a line under text that makes
  a sign another (P5); a folded or unfolded glyph off T4's position; and every refusal of ours (R8). An unshifted
  descending part of one of P8(a)'s letters (with no mark below) against its own run's underline or
  its own cell's bottom border is judged by P4's rule, not by the line-through and hidden-part items
  above: a part lying wholly in the band (drawn or cut) or wholly in the border is a failure, and
  sharing is a contact. Only the part below the lowest baseline R4 allows is so judged; the
  letter's ink above it hidden in, or crossed by, the border (the bowl of a "g" at a small
  `line-height`) stays P3's failure.
- **P4. What a font draws is not a contact.** Two characters adjacent in logical order on one line
  fragment (one line box of one block, never across cells), in whatever text nodes, neither shifted
  relative to the other (a raised "14" beside the "C" of "¹⁴C" is checked), are not checked against
  each other; an unshifted glyph and its own run's underline on its own line are judged on the
  glyph's pixels inside the underline's band (the rows the drawn line occupies, its skip-ink cuts
  counted as band) and on those 8-adjacent to the band from below; and, for a character whose pinned
  glyphs descend below the baseline by more than a stated overshoot (set by 3c-C at or above 0.03 em and below the smallest descent of the named descending classes,
  with every other glyph that descends past it listed and classed by its measured descent, including
  "ø", "Ø", "Φ", "/" and "\" (0.036 to 0.051 em in some pinned faces) and "§", "†", "‡", "$", "¶",
  "¢", "¼", "¾" and "@" (0.059 to 0.144 em, deeper than a comma in some faces), measured; from R4's
  ink bound: a comma, a semicolon, a cedilla, a mark below, a descender, a bracket), also on its ink below the highest
  baseline R4 allows. A character resting on the baseline (its glyphs descending no more than the
  overshoot) is not judged on ink above the band that only touches it, so a letter resting on its
  underline is not a contact (P5 still judges that underline as an underline, and a shifted glyph
  keeps P6's rule). For the judged ink, the cuts count as line except for P8(a)'s letters, whose
  tails the cut lets through: a descending part (an 8-connected component of the glyph's pixels below the
  lowest baseline R4 allows) lying wholly in the band (drawn or cut), or sharing at least two
  8-connected pixels with the drawn line (a count within the anti-aliasing variance of two counting
  as two), is P3's failure (a comma merged into its
  underline, "1,5" read as "1.5"; an "ạ" whose dot sits in a cut), except that for P8(a)'s letters,
  whose tail the cut usually lets through, so that sharing leaves it visible below the line (4.6's
  underlined "Pregnancy" at 0.8), sharing is a contact and only a part wholly in the band is a
  failure (the tail hidden, "y" read as "v"); any other touch is a contact, cleared only by
  P8(a). A shifted glyph's part lying wholly in its own run's underline band, drawn or
  cut, is the same failure (a lowered hyphen in an underlined "AUC₀₋₂₄"). A low-line character under a
  decoration (U+005F, U+02CD, U+2017, U+0332 and the closed list 3c-C states) is a refusal of ours.
  The underline of another line of the same run is a foreign line; and the judge asserts that each line's visual order is its logical order; a
  combining mark that draws a line through or under its base (U+0332 among them, a closed list 3c-C
  states) is a refusal of ours; a run's own decoration is the one drawn by its decorating box, and
  the collinear decorations of adjacent runs are one line; T5's waived `+` is exempt, under its own
  run's underline, from every check that judges a line under text.
- **P5. A line under text is judged as an underline** by the allowlist over the stretch of text it
  spans, a code point counting as underlined where its pixels come within a stated distance, in its
  own em, above the line (`<` over its cell's bottom border reads "≤"): what the allowlist refuses is
  a failure.
- **P6. Contacts.** Two glyphs P4 does not exempt that touch (a pixel of one 8-adjacent to, or shared
  with, a pixel of the other) or stand within a stated clearance; a glyph and a line (a border, a
  rule, a decoration, a background's edge, a run's own underline for a shifted glyph, the gaps
  skip-ink cuts counted as line), or a glyph and a picture's box, that touch or stand within a
  stated clearance; and a glyph whose ink reaches past its frame without leaving the page, are
  contacts, unless P3 makes them failures or P8 clear (save the zoomed-out kind, R4, at 0.8 and
  0.9). A contact's kind is one of a closed list 3c-C
  enumerates with each kind's location type (glyph–glyph, glyph–line, glyph–picture, glyph–frame);
  its identity is its section's path, its kind and the characters (and line or picture) involved, in
  R3's index space. It is captured at every distinct mask (the participants' pixels relative to one
  another), and identical masks are shown once across identities, so a person sees every distinct
  drawing they acknowledge and no drawing twice, each shown with every identity, and its text, that it
  stands for; the acknowledgement stays per identity. It is neither a failure nor a defect.
- **P7. Clear needs proof.** Only a glyph or line the gate proves clear under P1 and P2, or clear by
  rule under P8, passes without acknowledgement; everything else is a contact or a failure (save the
  zoomed-out kind at 0.8 and 0.9, which is not recorded).
- **P8. Clear by rule: two drawings that cannot change a letter.** Measured on the label (4.2's and
  4.8's tables; the reporting box), these contacts are clear without acknowledgement, and nothing
  else is; each clears only the one contact it names, never the glyph's other contacts, failures or
  reach:
  (a) a descender of one of a closed list of letters (lowercase Latin g, j, p, q, y; lowercase Greek
  β, γ, ζ, η, μ, ξ, ρ, φ, χ, ψ; with no mark below, never a capital, a digit, a punctuation mark or a
  symbol, since a comma's tail on a line can read as a full stop and a cedilla's as nothing) touching,
  or standing within the glyph–line clearance of, its own cell's bottom border or its own run's
  underline on its own line (or passing through the gap skip-ink cuts in that underline), with no
  shared pixel,
  where the descender keeps at least two rows of pixels at 40 % coverage or more that are not
  8-adjacent to the border's or underline's drawn pixels (a row in a cut of its own underline, below
  the drawn line's top, counts as free: the cut is what lets the tail through), its rows counted from the lowest baseline R4's one-pixel uncertainty
  allows (so a tail small enough to fuse into the line, "y" read as "v" at 7 pt, stays a contact); and
  (b) a glyph meeting, crossing or standing within the clearance of the edge of a background of its
  own frame (its own element's or an ancestor's, as R4 defines the own frame; a boundary between two
  fills whose painted box is at least 3 device pixels in both dimensions; a thinner one is a stroke,
  judged as a line), where the glyph's coverage mask is the same with that background made
  transparent (it is not covered) and the glyph keeps 4.5:1 against both fills.

  The order of the classes is: a refusal of ours, then a failure (P3, always judged before P8), then
  clear by rule (P8), then a contact, then clear. A background's edge that meets (b) is not a line for
  P3's other items nor for R8's `line-through-letter`, but an edge lying below a glyph is always
  judged as an underline by P5 (a sign on a shaded bar reads as underlined); an edge has no pixels of its own, and a glyph
  touches it when one of its pixels is adjacent to the boundary between the fills. 3c-C seeds
  a case of each rule that becomes a contact, or a failure, when its condition fails (a covered
  descender is a failure).

These replace the ninth draft's P8 and P9, whose exact exceptions each review found too wide or too
narrow for the label. On the tablets label the twelfth review counted, at sampled widths, 295 contact identities in the
carried sections before P8 (178 descenders on their own borders in 4.8 and 9 in 4.2; 39 at the
reporting box's grey edges; 65 pairs of 4.2's raised 9 against the line above; two brackets on their
own borders in 4.8; one in 5.2) and about 70 after it, 65 of them the raised 9's, in some 100 to 250
distinct drawings; the seventeenth review counted about 49 own-underline contacts (descender tails
touching their underline at ratio 1) under the rule then, which the free cut row reduces; the
nineteenth found more underlined "y"s as plain contacts at 1.1 (4.4 and 4.8), and, at 0.8 and 0.9,
hundreds of descender contacts and two underlined "y"s sharing pixels with their line, which the
zoomed-out rule and P4's letters leave unjudged; the twenty-first counted, at 0.8 and 0.9, what
remains still in the tens (in 4.2, some twenty descender-over-ascender pairs beside the raised 9;
in 4.8, brackets on their own borders), identities ratio 1 does not have, so 3c-C counts 0.8 and
0.9 separately; every integer width adds more, and 3c-C recounts
before the cap is set. 3c-C measures the counts at every width and sets
a cap, one gate constant in the record's `tolerances` (so changing it is a new gate version), on the
number of contact identities and of distinct drawings across a record's carried sections; the lookup
refuses above it (`renderer-contacts-exceeded`, a failure, never a refusal of ours), so
acknowledgement never becomes a rubber stamp. The withheld sections' contacts are not counted. It is ADR 0003's second stated exception, amended (owner decisions of 2026-09-25): a
contact the gate cannot prove harmless passes only by a person's acknowledgement.

**Seeded cases (binding).** Each is a case of the judge's tests in both modes and at every ratio
(at 0.8 and 0.9, a seed of the kind the zoomed-out rule leaves unjudged binds only as a failure,
save the seeds that must not be failures and must not be recorded, which bind as stated); where a case's class depends on the face, weight, size, ratio or sub-pixel phase, it binds at the
configurations 3c-C measures and states (P2).

Must be failures: a descender covered by a later line's inline background; an underscore under its
own underline ("a_b", a refusal of ours at any size); a comma sharing two 8-connected pixels with its
own drawn underline ("1,5" read as "1.5"), including one that fails only by sharing (sans at 11 pt,
ratio 1.5, measured), and a mark below lying wholly in a cut of its own underline, each at the
configurations 3c-C finds them (bold serif at 7 pt, sans at 9 pt, ratio 1, measured); a lowered
hyphen lying wholly in a cut of its own run's underline ("0-24" read as "024"); a 5 pt serif "y" at
ratio 1 whose tail lies wholly in its own underline's band; a "<" one blank device row above a shaded bar that is not its own frame's background (P5,
"≤"); a colon shifted across its cell's bottom border ("10:1" drawn "10.1"); a colon's lower dot
inside a thick bottom border ("10·1"); a raised 7 folded by 2.8 pt, its bar inside its cell's top
border; two cells' numbers 0.19 px apart (5.1's table 9 at 504 px); a glyph's pixels on a picture's;
a border through a letter; a line under a `<`; two cells' text running together; text wholly off the
page; raised text above the section's top edge; a marker off the page; a picture over a glyph; a
later box in the text's own colour over a glyph; a missing glyph (U+2070); an unpinned family
(Verdana); a glyph too faint to judge. Must be a contact: a 9 pt italic serif "f" at ratio 1 in "afa",
its tail overhanging its advance and touching its cell's bottom border (lost under `::highlight()`
isolation, measured).

Must never be clear (a contact or a failure): a comma, semicolon, cedilla or mark below touching its
own underline, at every size, face and ratio (measured over 3 840 cases per ratio in Liberation
Serif and Sans, regular and bold, and again in their italics and in Carlito); the colon of
"10:1" at 5 pt against its border.

Must be contacts: a "y" sharing pixels with its own underline, its tail showing below the line, at
a ratio from 1 up where 3c-C finds one; a 5 pt serif "y" and "γ" at 0.8, underlined, with no row of its
tail at 40 % or more below the drawn line; an 11 pt sans "g" and a 10 pt serif "μ" at 0.8, underlined, each tail's pixels at 40 % or more
ending inside its own cut, with fainter ones below (5.1's underlined "adjuvant" "j" at 0.8 and 320
px is one, measured); two glyphs 0.2 px apart across lines; a combining mark stacked into the line above;
a raised digit against the underline of the line above; a raised 7 folded by 2.4 pt abutting its own
cell's top border ("x 10⁷/l"); a descender touching a picture on the next line; a comma's tail
touching its own cell's bottom border; a "ç" whose cedilla touches its own cell's bottom border; a
7 pt "y" and "μ" touching their own cells' bottom borders at ratio 1; the "y" of 4.2's underlined
"Posology" at ratio 1 where its hook touches a one-pixel piece of underline between two cuts; the
reporting box's final "." and a descender of the line above touching the grey fragment's edge; an
accent on a run's second line touching the underline of its first at `line-height: 1em`; 5.2's raised
"14" against the "C" of "¹⁴C"; `<u>AUC<sub>0-24</sub></u>` where the lowered hyphen abuts its
underline or sits in a skip-ink gap; 4.2's "10⁹/l" against the line above.

Must be clear: a `>` 0.36 em above its cell's bottom border in the pinned face (4.2; T's note measured
0.39 em in the original) as an underline question (P5); a descender over an ascender at
`line-height: normal` (4.4's "g" over "b"); `<span>T</span><span>he`; two adjacent `u` elements
(4.2's "Posology for Ph+ ALL in children"); the waived `+` of 4.2's headings; an underlined "o" at
ratio 1.25, resting on its underline (P4).

Must not be failures, and not recorded (the zoomed-out kind): 4.6's underlined "y"s at 0.8
("Fertility" at 360 px, "Pregnancy" at 1 024, 1 152 and 1 280 px; one pixel of tail at 0.45 to 0.46
coverage below the line) and at 0.9, and 4.8's underlined "Laboratory" "y" at 0.9 (414 and 900 px),
measured; R4's exact baseline must be established for each of them (a kerned "y," as in
"Fertility," among them), so none of their ink above the baseline is taken as hidden in the line;
and a 10 pt serif "y" at 0.8, underlined, its tail showing below the drawn line, whose estimate is
a device pixel off, its exact baseline established.

Must be clear (by P8 or plainly): 4.2's and 4.8's letter descenders touching their own cells'
bottom borders; the "g"s of 4.2's underlined "Posology" at every ratio, and its "y" and the "p"s of
4.8's underlined link "Appendix V" at ratio 1.25 and above, passing through the cuts of their own
underline; the grey text's own glyphs in 4.8's reporting box meeting their grey background's edges,
"Appendix V" past it included.

**Provisional checks** (the second draft's list, kept as 3c-C's starting point, each to be restated
under the three classes and P1–P7 with measured thresholds): overlap; every drawn line as ink (0.5 CSS px); a line under
text as an underline (0.3 em); the gap between cells (0.25 em); the frame (1/32 CSS px
horizontally); reach; visibility and contrast (4.5:1); folds (0.1 em and 0.2 em).

### R5. The `rendering` stage: what the lookup requires

Every import request whose authority is not `synthetic`, or which withholds a section, names the
evidence its requester reviewed, `renderEvidence: { recordSha256, environment, capturesSha256 }`,
and acknowledges each contact of every carried section by identity, `acknowledgedContacts: [{ path,
kind, location }]`, in the record's order (sections in pre-order, then R8's order), without repeats;
a withheld section's contacts are neither acknowledged nor checked, and acknowledging one refuses; a
withheld section's confirmations are the withheld note's. Acknowledging a contact is a judgement of
content, so, as for withholding, PR 5 lifts the dry run for a request that acknowledges any only once
its requester is an attested identity (every Imatinib Teva import acknowledges some, so PR 5 needs that
identity before any real label is persisted). The review the requester is shown is item 2's (`docs/design/approval.md`, its amendment of
2026-09-25): as that amendment requires, the signer verifies the attestation, the index and each
capture's hash and builds a review of every acknowledged contact (identical masks once, each with
every identity it stands for); how it is stored and linked (a Chat card is proposed) is item 2's
review's; that the requester opened it is a claim, not proven, a
residual stated below. When several attestations of a record verify (key versions
not revoked), the image build takes the one of the highest key version, and its captures are those a
request names. The image build places, beside each record it keeps,
the verified attestation's `{ environment, capturesSha256 }`
(`src/render/records/<documentSha256>.attested.json`, copied by `Dockerfile` with the store). The
stage finds the record for the document's SHA-256 (among those the image build verified, R1) and
requires:

- the record's `authority` and document `id` equal the request's; its `gateVersion` and
  `gateSha256` (R9) are this build's; and its `widths`, `ratios`, `modes` and `tolerances` equal the
  gate's constants;
- the request's `renderEvidence` equals the record's hash and its attested environment and
  captures, and that environment is this deployment's (a review made in one environment is not
  valid in another; each needs its own);
- the record's sections are exactly the document's, in pre-order, matched by `path` and `code`;
  for a carried section also by the three output hashes this import computes (T(div), T's model
  output, the scanner's text; R3), and by its pictures, which must equal the list the import
  computes for the section (R2);
- every carried section has no refusal and no failure (and so no defect: every defect is also a
  failure, R8), and its contacts are exactly those the request acknowledges; and the carried sections'
  contacts are within the cap (R4);
- the withheld sections: the withheld note's W1.

Reasons: `renderer-evidence-missing`, `renderer-evidence-mismatch` (another authority, document id,
or sections than the document's), `renderer-evidence-changed` (the record, captures or environment
not those the request names), `renderer-contact-not-acknowledged` (a contact the request does not
acknowledge; an acknowledgement of none, of a withheld section's, repeated or out of order),
`renderer-contacts-exceeded` (the carried sections' contacts above the cap, R4), `renderer-evidence-stale` (another gate, constants, output or
picture this import does not compute), `renderer-evidence-failed`, and the withheld note's
(`withheld-section-not-shown`, `withheld-evidence-changed`). A
synthetic publication with nothing withheld passes, as today; one with a withheld section needs an
attested record like any other. The importer's golden vectors use a synthetic store, so adding a
record does not change them.

### R6. The image: pinned browser, pinned fonts

`Dockerfile.renderer`, pinned as `Dockerfile.validator` is, its pins read by one reader
(`scripts/ci/renderer-pins.mjs`) used by the Dockerfile check and the lock. The renderer image build
(`cloudbuild.renderer-image.yaml`, R1) builds it when its pins change and pushes it to
`renderer-images`; it is used by digest
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
- **pixels** (the candidates' resolutions, the contacts' masks, the contrast and visibility margins
  and `line-through-letter`) within ε = 0.02, a mask's hash computed after each pixel within ε of the
  coverage floor is counted in; a value within ε of a threshold falls in the more severe class of its
  decision (R4), never a defect, so no class flips between CPUs.

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
               failures: [{ check, worst, at: [{ width, ratio, mode }] }],
               defects: [{ kind, location, measure, at: [{ width, ratio }] }],
               contacts: [{ kind, location, masks, at: [{ width, ratio }] }] }] }
```

`refusals` are ours (R3 differences, `font-unpinned`, `font-coverage`, a run in a script the gate
does not bound, a mark without a lookup, a calibration failure, an XML `parsererror`, a width
assertion, a layout that changed between a drawing and its transparent or clipped counterpart, and a
defect that is not also a failure): the gate cannot judge the section.

`failures` are P3's misreadings, each with every width and ratio it occurs at.

`defects` are geometric findings that a reader cannot read the drawing as written, which the person
who withholds a section confirms or rejects one by one against the record's captures (the withheld
note's W1). They are never taken from a content area, and use only the binding parameters above,
each chosen so a defect is found less often, never more. Each has an identity, `{ kind, location }`,
unique within its section, with a location typed by kind, in the index space of R3 (the judge's own,
defined for every section). The section's defects are sorted by kind (`cells-run-together`,
`line-through-letter`, `off-page`) then location, lexicographically, each one's `at` by ratio then
width, and its `measure` is the worst over `at`. The kinds:

- `cells-run-together` (location: the two cells and the two facing characters), at every width and
  ratio (it needs no drawing): two cells whose text stands side by side on one line (R4), whose
  facing characters (not whitespace, a no-break space or a zero-width code point) have advances that
  abut or overlap horizontally (a gap of zero or less in Chrome's layout units, 1/64 px), so no more
  space separates the cells than separates two letters of a word ("182:8" beside "177:12" read as
  one run); a split number such as "12" beside ".5" is the same geometry, which is why a person
  confirms or rejects each.
- `line-through-letter` (location: the character, and the line as its element and which of its
  edges, decorations or rules), from glyph pixels (R4) at every width where P3's candidates arise: a
  line (not a run's own decoration, nor a background's edge that meets P8(b)) with a glyph's pixels
  on both of its sides, measured from the glyph's own baseline (moved, for a shifted glyph), inside
  the band from one device pixel above the glyph's baseline to one below its x-height (or cap height
  for a capital): a line through the body of a letter, not a touch at a tail or serif; seeded
  controls that must not be one: "jelly" and an italic "jf" in an unpadded bordered cell, a ")" on a
  bottom border.
- `off-page` (location: the character), at every width and ratio: a character with ink (not
  whitespace, a no-break space or a zero-width code point) whose rounded-out ink bound lies wholly
  beyond the page's left or top edge; this one is read from a conservative bound, calibrated in every
  draw (R4), because nothing beyond the page is drawn: if even the bound that contains all the ink is
  off the page, none of the ink is drawn.

Every defect is also a failure (P3 names all three); a defect that is not is a refusal of ours. No
defect involves a picture's box. A test requires that no carried section of a pinned label has one.

`contacts` (P6) are recorded beside them, sorted by kind then location, each with its distinct pixel
masks' hashes (whose captures a person acknowledges); neither failures nor defects. Whether a contact
exists is decided on pixels, so a coverage within ε of the noise floor, or a clearance within ε of
its threshold, counts as a contact (and so needs an acknowledgement), and a contact set that differs
between the two regenerations fails the render build for that record (R7).

No overall pass and no withheld field: what is withheld is the request's. Captures are not part of
the record and not compared: the render build stores them under the record's hash before it signs,
and the attestation names their index (R1).

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

`AuthorityFetchSchema` gains `authority` and `rendering: { gateVersion, gateSha256, recordSha256,
contactsAcknowledged }`, the latter required when `authority` is not `synthetic` or something is
withheld, and absent otherwise. `RUN_MANIFEST_VERSION` moves a major (3.0.0 unless item 2's lands first), with the withheld
note's fields and the submission's 3.0.0 (`contractVersion` follows it, D13); the 2.0.0 ingestion
block is frozen with the literal `"2.0.0"` in `AnyRunManifestSchema`, as 1.0.0's was, and a 2.0.0
manifest fixture is read through it in a test. The FHIR Provenance carries `rendering.recordSha256`, the
`request` statement's hash and, per section, the number of contacts acknowledged; the attested
requester is the statement's (`docs/design/approval.md`, recorded as its `enterer`), once per
request; `get_provenance` returns them; the ledger gains a column for the count. The run's evidence keeps the record's bytes by hash. The
worker image carries the store (`Dockerfile` copies `src/render/records`); re-verifying an import
(`scripts/authority/verify-import.ts`) runs in that import's own worker image, whose store and gate
it used, not in a later one.

### R11. Cost

The ten ratios of R2 make 1 601 layouts at ratio 1 and 961 at each of the nine others (10 250 per label). The reviews measured 211 ms to read the whole tablets label's 105 k rectangles per width on an
M-series core, about 0.5 s per layout with the checks, so about 85 CPU-minutes per label per draw
(10 250 layouts; the zoomed-out ratios' failure checks cost less); a full-page capture of 5.1 at ratio 3 takes 2 to 6.5 s. So pixels are drawn only at widths and ratios where candidates exist, once per drawing (glyphs only, lines only,
a box made transparent), and cropped locally; the advance-only defects are computed from the
rectangles already read. The sixth review measured 709 to 741 own-frame candidates per layout in
4.8 and a full-page capture of 4.8 at 0.13 s (ratio 1) to 0.73 s (ratio 3): about two CPU-hours per
draw for 4.8 alone at the eighteenth draft's six ratios, more at ten, which 3c-C measures again before setting the build's timeout. The render build runs on a
high-CPU Cloud Build machine (a private pool in the project's region if the region's default pool
has none), sharded by ratio and width range within one build, which alone signs, with a timeout set
from 3c-C's measurement of the pinned labels (4.8's candidates the largest share); it runs only when
a record or the gate changes, off the deploy's critical path (R1). If a record's draw exceeds the
build's limit, the widths change by an amendment of this note, reviewed, never by sampling silently.

## Delivery

1. **3c-B**, in two changes (the first code review of 3c-B asked that what waits be named):
   - **3c-B1**: the renderer image (`Dockerfile.renderer`, its pins and fontconfig) built and
     checked offline in CI; R2's page; the DevTools client; the output format's addendum; T's
     model output; and R3's comparison of it with Chrome's computed style and list markers, in
     both modes at every ratio, and of its text ranges with the XML-mode DOM, as the CI
     pre-check. The importer moves to 2.1.0.
   - **3c-B2**: the rest of R3 and R2's second drawing: the fonts and scripts of every section,
     T's or not (`font-unpinned`, the script bound); R6's coverage (`font-coverage`, the U+2070
     seed, the pixel tests of the substitutions); each table's grid from cell rectangles against
     T(div)'s; each character box's height against the face's ascent and descent; a
     `parsererror` as its own refusal; the text, list numbers, grids and pictures of the
     authority's drawing against T(div)'s at the named widths; R2's assertion that the div's
     content box is the width, and the refusal of a div with its own padding or border; and R2's
     picture forms the import already carries (`data`, `contained`, `unpinned` rewritten to a
     failing URL), each asserted by a test, with the per-section picture list for them.
   - **3c-C** takes R1 but the image build's verification and the deploy routes' download of
     attestations (3c-D's): the render build in its attest and propose modes, their identities
     and buckets, the captures, their index and the bucket's retention, the pull request's
     recomputation of every record's output hashes, and the redrawing of records whose bytes,
     gate hash or pins differ from `main`'s; with R4, R2's width sweep, its relayout, and XML
     mode's geometry of text, markers and cells against HTML mode's at the named widths and every
     ratio. **3c-E** takes the `export`, `not-drawn` (an empty box of its evidence's `drawnBox`)
     and `fetched` forms, the last with the first picture template, and their tests.

   The renderer image's own build (`cloudbuild.renderer-image.yaml`, the `renderer-images`
   repository) moves to 3c-C with the render build, its identities and Terraform, and the
   repository connection only the owner can make (R1).

2. **3c-C**: its measured design of R4 (reviewed first), then R4, R7, R8, R9, R11; the render build,
   its trigger and attestations; the records of the pinned labels.
3. **3c-D**: R5 and R10: the lookup, the image build's verification, the manifest, re-verification
   (the review a requester signs is item 2's, before PR 5).
4. **3c-W**: the withheld section (its note), including the lookup's withheld part.
5. **3c-E**: pictures from the authority's export (the owner's decision of 2026-09-25; its own
   note), needed by every pinned label but Imatinib Teva once 5.1 is withheld.

## Stated residuals

- The gate proves what its pinned fonts, widths and ratios draw; a reader's own fonts, a
  fractional width or ratio, or a width above 1 920 px can wrap differently.
- The authority's viewer applies a stylesheet the gate does not, and its page margin may differ
  from the gate's 16 px, which `off-page` and check 6 depend on.
- The visited link colour is judged statically (T3a).
- A letter of P8(a) whose tail lies wholly in its own underline's band (drawn or cut) is P3's
  failure though it often shows in a cut and reads correctly: nearly every underlined descender at 5
  to 7 pt at ratio 1 (6 to 8 pt at 0.8), in Liberation Serif and Sans, measured; so small
  underlined text in a safety section refuses; one whose tail shares pixels with the line is a
  contact; 3c-C measures how often, on the pinned labels.
- Zoomed out (0.8 and 0.9), a descender of P8(a)'s closed list of letters touching its own
  underline or its own cell's bottom border, its tail still visible, is not shown to anyone (ADR 0003's
  third stated exception; a fused tail stays a contact: against an underline, one with no row at
  40 % or more below the drawn line, a cut row not counting; against a border, one with no free
  row by P8(a)'s count from the exact baseline; and a tail wholly hidden is still a failure); below 0.8, nothing is judged. The
  label's unjudged "y"s rest on one pixel 0.05 above the 40 % threshold at 0.8, seeded (above).
- A shifted glyph crossed through its body by its own run's underline (a lowered "2" in an
  underlined "AUC₀₋₂₄") is P3's failure even where it stays readable; 3c-C measures how often an
  underlined heading with a subscript is refused.
- That a requester looked at every capture they acknowledge is not proven: at most the record shows
  who opened the review (approval.md's amendment), never that they looked at each drawing.
- A request is one content-reviewer's judgement (approval.md's amendment); whether production adds a
  second reviewer's countersignature is the owner's production-gate decision.
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
  margin.
- The three classes: every seeded case in its stated class; at 0.8 and 0.9 every seeded failure
  found, every contact recorded but the kind the zoomed-out rule leaves unjudged; 4.6's underlined
  "Pregnancy" "y" at 0.8 not recorded; 4.2's and 4.8's contacts found and
  captured at every distinct mask; an import that does not acknowledge every contact, or
  acknowledges one the record lacks, refuses.
- Defects: the seeded controls are not defects; no carried section of a pinned label has one; the
  tablets' 5.1 shows `cells-run-together` in table 8 (from 320 to 419 px and at 671 px at ratio 1;
  320 to 418 px at 1.25 to 3, measured; 3c-C measures 0.8, 0.9, 1.1 and 2.625).
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
7. **Seventh independent reviews** (2026-09-25). The restructure was judged acceptable to approve,
   no record being attestable before 3c-C's measured design is reviewed. Medium: P3 failed two
   must-pass cases (Chrome breaks an underline around descenders; an inline background's edge
   snaps to whole device pixels); no principle forbade a glyph touching a glyph across lines or a
   line near one, which T hands to the gate; a 5 pt colon has no pixel at 40 % coverage, so P3
   could not see it; the captures a person reviews were not bound to the record; confirmation was
   per kind, not per instance. Low: P6's "same", P4's line fragment and reordering, which
   parameters bind, the render identity's actors and repository credential, the captures' order,
   a reason for a mismatched record, the output format's keys, the delivery of R2 and of the
   renderer image build, CI's comparison, facts. Fixed in this draft: P3's two exceptions; P8; a
   10 % noise floor for failures and 40 % for defects, "too faint" a refusal of ours, thresholds
   required to fail every must-fail case at T's extremes; captures before signing, named by the
   attestation; defects located; the rest as found.
8. **Eighth independent reviews** (2026-09-25). Medium: P8 with the seed "two glyphs 0.2 px apart
   across lines" refused 4.2 Posology, whose raised 9 in "10⁹/l" touches the "p" of the line above at
   most widths (a true finding; the owner chose a narrow stated exception, P9); P8's line clearance
   could not pass 4.8's must-pass cases and fail the raised digit against an underline; P1 did not
   say which glyph owns a pixel where two lines' boxes overlap, and neighbours' anti-aliasing made
   "too faint" unreachable; R1's identity story did not match Cloud Build (whoever manages a trigger
   holds `actAs` and can sign anything); a defect's location had no defined identity. Low: "unshifted"
   in P3; the repository's history needs the connection's token; captures could collide on a rerun;
   the propose mode's actor; R3's keys across modes; `off-page`'s wording; delivery's prerequisites.
   Fixed in this draft: P9; P8's line clearance by kind of line; unique pixel attribution with line
   isolation by `::highlight()`, and faintness from the calibration raster; the identity story stated
   as it is; defects' identities typed and sorted; captures by content hash with a verified index; the
   rest as found.
9. **Ninth independent reviews** (2026-09-25). High: P9 admitted any folded glyph against anything
   below the upper line's baseline, with one pixel count for every ratio, and a raised 0 under a "p"
   stem reads as a 6 while sharing fewer pixels than the label's 9; P8's allowance for ink abutting
   its own frame let a raised 7 fuse into its cell's top border ("/" or "1"). Medium: the 9 also meets
   letters, a bracket and the "0" of "30%" on the line above at some widths; the contact grows with
   the ratio (9 device pixels at ratio 1, 39 at 3); nothing made contacts reviewed; a lowered hyphen
   abutting, or in a skip-ink gap of, its own underline passed; a defect's identity had no index
   space in a section T refuses. The owner decided (2026-09-25) that a raised digit's contact with
   the line above is allowed only within automatic limits and only once the person who requests the
   import acknowledges it in the captures. Fixed in this draft: P9 limited to digits without marks,
   the raised digit's top third, a fraction per ratio, one contact per glyph, and acknowledged by the
   requester, who names the record and captures reviewed (`renderEvidence`, R5); P8's allowances
   narrowed to ink below the baseline against its own bottom border and to background edges, every
   line keeping a blank row from a shifted glyph, skip-ink gaps counted as line; the judge's own index
   space for every section; captures and index paths, reuse and recovery; attestations per key
   version; isolation per candidate glyph, markers and colours asserted.
10. **Tenth independent reviews** (2026-09-25). Medium: 4.2's measured contacts fell outside P9's
    limits (the 9 meets two glyphs at once at some widths, and letter bodies on the upper line's
    baseline row at ratios 1.25 and 2); the baseline row's uncertainty cut both ways; a touch with no
    shared pixel passed P9's position limits vacuously; 4.8's "Appendix V", kerned against the full
    stop after it, reaches past its grey background, failing P8; acknowledging a contact needed no
    attested identity; `renderEvidence` was universal but the record's binding still said "when
    withheld". The owner decided (2026-09-25) to show and acknowledge every contact the gate cannot
    prove harmless. Fixed in this draft: three classes (clear, failure, contact) replace P8 and P9;
    contacts identified by section, kind and characters, captured at every distinct mask and
    acknowledged by the requester, who must be an attested identity before PR 5; the record named in
    every authority import's source record; reasons, the attestation chosen among key versions, and
    the seeds restated by class.
11. **Eleventh independent reviews** (2026-09-25). Medium: pictures were outside the classes (a
    glyph against a picture beneath it was clear); a glyph part wholly inside a line (a colon's dot in
    a thick border, a raised 7's bar in its cell's top border) was only a contact; glyph over glyph
    could count as "covered", refusing 4.2; no failure for text above the section; the ε band pushed
    near-floor touches into failures, refusing 4.2 and 4.8; a 3c-C decision P6 had already made;
    about 350 acknowledgements per import on the tablets label, mostly identical descenders on their
    own borders, a rubber stamp; two notes still treated a shortfall of clearance as a refusal; two
    cells' numbers 0.19 px apart only a contact. Fixed in this draft: pictures in the classes; P3's
    failures widened (a part inside a line, a picture's pixels, above the section, cells closer than
    a stated gap); the ε band to the more severe class of its own decision; P8, clear by rule, for
    letter descenders on their own bottom border and text at a background's edge; identical masks
    shown once and a stated cap on contacts; the run manifest 3.0.0 with 2.0.0 frozen; the Provenance
    and ledger show acknowledgements; the review tool delivered with 3c-D; the rest as found.
12. **Twelfth independent reviews** (2026-09-25). Medium: P8(b) contradicted P3 (the "V" of "Appendix
    V" has pixels on both sides of its grey edge), and read as taking precedence it would pass a
    descender covered by a later line's background; P8 cleared only a touch, not a near miss within
    the clearance; P8(a)'s premise fails at small sizes (a 7 pt "y" or "μ" fuses into the line) and
    its "letter" was open (a cedilla on a line); the contact cap counted the withheld section's
    contacts and had no reason. Low: the counts (about 70 identities, not 60); ε applied to layout; P4
    exempting a shifted glyph against its neighbour; deduplicated masks shown without their contexts;
    "a connected part within a line" undefined; stale "free of overlapping". Fixed in this draft: the
    classes' order (failure before P8); P8 closed, within the clearance, with a remaining pixel row,
    uncovered, per contact; strokes defined; the cap over carried sections, with its reason; ε for
    pixels only; the rest as found.
13. **Thirteenth independent reviews** (2026-09-25). Medium: P8(a)'s single remaining row cleared the
    seeded 7 pt "y" and "μ", which must be contacts; a 5 pt colon's lower dot, one pixel wholly inside
    a border, was only a contact; nothing exempted a glyph from its own run's decoration, so every
    underlined descender was a contact and some skip-ink edges a failure; P8(b)'s carve-out left a
    background's edge a line for P3's hidden part, failing "Appendix V". Low: an accented capital's
    ink above the section a failure; the cap's home; a line pixel's threshold. Fixed in this draft:
    two remaining rows, counted from the lowest possible baseline; a whole component hidden in a line
    is a failure at any size; an unshifted glyph is not checked against its own run's decoration
    (P5 still judges it); a (b) edge is no line for any P3 item and has no pixels; unshifted ink above
    the section a contact; the cap a gate constant, checked by the lookup; R8's lists restructured.
14. **Fourteenth independent reviews** (2026-09-25). High: the exemption of an unshifted glyph from
    its own decoration cleared an underscore that vanishes into its own underline, a small comma or
    cedilla fused into it ("1,5" read as "1.5"), and an accent on a run's second line inside its
    first line's underline; P8(b)'s carve-out took any background's edge out of P5's judgement, so a
    "<" on a shaded bar ("≤") passed, closer passing where farther failed. Low: P8(a) has no margin at
    ratio 1; P2 against the label's own seeds; ADR 0003's wording; shifted glyphs crossed by their own
    underline. Fixed in this draft: the exemption only for a glyph clear of its own underline's drawn
    pixels on its own line, a touch a contact (P8(a)'s letters clear), a part hidden in it a failure,
    another line's underline foreign; P8(b) only for the glyph's own frame's backgrounds, and an edge
    below a glyph always judged by P5; the estimator stated; seeds per class and size.
15. **Fifteenth independent reviews** (2026-09-25). High: the skip-ink carve-out cleared a comma or
    cedilla lying in its own underline's row between the cuts ("1,5" read as "1.5", "ç" as "c"), and a
    glyph filling a cut, deeper passing where shallower was a contact. Medium: "a touch is a contact"
    caught ordinary letters resting on their own underline (about 680 identities, breaking the cap and
    the must-be-clear seeds); the "a_b" seed could not be a failure at 5 pt. Low: the reporting box's
    foreign contacts; P8(a) silent on the own underline; a P8(a) tail hidden in its underline; the
    shaded-bar seed's geometry. Fixed in this draft: the own underline judged only on ink below the
    baseline, the cut counted as line but for P8(a)'s letters, a component within the band or a pixel
    of it a failure, low-line characters under a decoration refusals of ours; P8(a) names the own
    underline; seeds restated.
16. **Sixteenth independent reviews** (2026-09-25). High: at the viewer's ratio Chrome draws the
    underline on the first row below the baseline, above the fifteenth draft's cutoff, so a comma or
    cedilla merging into it, or a mark in its cut, was never judged (round 15's High reopened).
    Medium: the "whole component in the band" failure never fired for a comma, or, read the other
    way, fired on ordinary text; a binding seed ("Posology" clear) was contradicted on the label (its
    "y" at ratio 1). Low: the "o" seed's class; stale review-tool references; ADR 0003's paraphrase;
    P8(a)'s row test for an underline; the estimator's error. Fixed in this draft: the own underline
    judged on every pixel in its band (cuts included) and adjacent below, and on a descending
    character's ink below the highest baseline; a failure is a descending part wholly in the band or
    sharing two connected drawn pixels, any other touch a contact; seeds restated as measured.
17. **Seventeenth independent reviews** (2026-09-25). Nothing High: a descending comma, cedilla or
    mark under its own underline never came out clear (3 840 cases per ratio), and no resting letter
    became a contact. Medium: "a descending part" was undefined, and no reading satisfied all three
    pixel-exact seeds, which depend on face, weight and phase; P8(a)'s "a cut row is not free" made
    every underlined "p" at ratio 1 a contact, contradicting the "Appendix V" seed. Low: "Posology" in
    two classes; the overshoot's value; contact and failure turning on one anti-aliased pixel. Fixed
    in this draft: "part" defined; seeds for such characters bind "never clear", and pixel-exact
    seeds bind at the configurations 3c-C measures; a cut row of the glyph's own underline counts as
    free for P8(a)'s letters; the overshoot at least 0.03 em; a pixel count within its variance
    counts against the section.
18. **Eighteenth independent review** (2026-09-26; its third run, the first two stopped at the owner's
    pauses). The round-17 fixes hold, open no false pass, and every seed is satisfiable in its class;
    the label's own-underline contacts fell to about ten, no failure in a carried section. Medium: the
    drawn ratios did not "cover zoom": a page zoom times the display's ratio is often undrawn (110 %,
    90 %, 80 %, a phone's 2.625), and an 11 pt comma clear at every drawn ratio touched its border at
    1.1. Low: P2's "elsewhere"; the overshoot's circular definition and value; small P8(a) letters
    always failing; a seed that did not isolate its rule; a lowered hyphen in a cut only a contact;
    "clear by rule" versus plain clear; faces named, ADR 0003's count. Fixed in this draft: ratios 0.8,
    0.9, 1.1 and 2.625 added, and "clear" needs a device pixel of margin beyond its clearance; P2's
    binding restated; the overshoot bounded and its in-between glyphs classed by 3c-C; the residual
    widened; a sharing-only seed; a shifted part in its own underline a failure; the rest as found.
19. **Nineteenth independent review** (2026-09-26). No High. Medium: at the added ratios 0.8 and
    0.9, two underlined "y"s in 4.8 and 4.6 shared pixels with their line, P3's failure in a safety
    section, and P8(a)'s two rows failed for most of 4.8's descenders (hundreds of contacts); the
    one-pixel margin left a band no class covered, made a descender one row from its border worse
    than one touching it, and multiplied contacts (4.4's "g" over "b" a contact at 0.8); and the
    margin was no bound (drift up to two device pixels; holes at 0.95 and 1.05). Low: glyphs deeper
    than the overshoot's list; R11's figures; ADR 0003's garbled count; 5.1's defect widths at the
    new ratios; more contacts at 1.1. Fixed in this draft: the owner decided (2026-09-26) that at
    0.8 and 0.9 the gate judges failures and refusals only (ADR 0003's third stated exception); for
    P8(a)'s letters, sharing with their own underline is a contact and only a tail wholly in the band
    a failure; the margin removed and the residual stated as measured; the rest as found.
20. **Twentieth independent review** (2026-09-26). High: the zoomed-out rule left every contact at
    0.8 and 0.9 unjudged, a comma sitting on its underline among them ("1,500" drawn "1.500"), though
    the ADRs said it dropped only touches that change no sign (latent: none on the tablets label at
    the sampled widths). Medium: P3's hidden-part item still made a "y" sharing two pixels with its
    underline a failure (4.6 and 4.8 at 0.8 and 0.9), undoing round 19's P4 split. Low: the residual
    understated; P4's reason for its letters false; no seed for the split; wording. Fixed in this
    draft: the zoomed-out rule names the two kinds its reason covers (P8(a)'s descenders on their own
    lines; P8(b)'s background edges), every other contact stays; P3 yields to P4 for P8(a)'s letters
    against their own underline; the residual restated as measured; seeds each way; the rest as
    found. Measured: no failure in any carried section at any of the ten ratios; 12 underline contact
    identities at ratio 1 and 15 at 1.1.
21. **Twenty-first independent review** (2026-09-26). High: the zoomed-out kind for P8(a)'s
    descenders had no legibility condition, so at 5 and 6 pt at 0.8 and 0.9 a fused tail ("y" read
    as "v", "μ" as "u") passed unseen where it is not wholly in the band. Medium: the second
    zoomed-out kind (a background's edge) added only drawings that fail P8(b), a contrast failure on
    the second fill among them. Low: a border's shared pixels still P3's failure; P3's closing
    sentence not limited to unshifted letters with no mark below; the ADRs' and roadmap's wording
    dropping the closed list; the count at 0.8 and 0.9 unstated. Fixed in this draft: the
    zoomed-out kind needs a free row by P8(a)'s count, and is the only kind (the background's edge
    removed); P3 yields to P4's rule for an unshifted P8(a) letter against its own underline or its
    own cell's bottom border; seeds each way; the wording and counts as found. Measured: no failure
    in any carried section at any of the ten ratios.
22. **Twenty-second independent review** (2026-09-26). High: the zoomed-out kind's one free row
    could be the cut row its own tail fills, so at 0.8 and 0.9, at 9 to 11 pt, nearly every
    underlined descender whose tail ended in its cut was left unjudged with the line looking
    unbroken ("g" read as "q", "j" as "i", "μ" as "u"; 5.1's "adjuvant" at 0.8, withheld). No Medium.
    Low: reach past the frame beside the kind; P3's border carve-out leaning on a rule P4 states for
    underlines only, and silent on a border through a letter's body above the baseline; the ADRs'
    wording ahead of the note's. Fixed in this draft: against an underline the tail must show a row
    at 40 % or more below the drawn line, a cut row not counting; the kind covers one contact only,
    reach stays judged; only the part below the lowest baseline is carved out; seeds added.
    Measured: no failure in any carried section at any of the ten ratios; the zoomed-out kind leaves
    4.6's and 4.8's underlined "y"s (their tails showing) unjudged.
23. **Twenty-third independent review** (2026-09-26). Round 22's High closed: a tail ending in its
    cut is a contact (the seeded "g" and "μ", 5.1's "adjuvant"); no "g" ever falls in the zoomed-out
    kind; the new P3 clause makes no failure in a carried section. Medium: ADR 0003's amendment still
    said "a free row", which counts a cut row. Low: the label's pass at 0.8 and 0.9 rests on the
    baseline estimator being exact there; the "g" and "μ" seeds' wording; the unjudged "y"s rest on
    one pixel 0.05 above the threshold; the residual omitted the fused tail. Fixed in this draft:
    ADR 0003 states the visible-tail rule; the label's unjudged "y"s seeded as neither failures nor
    recorded, with the estimator exact at 0.8 and 0.9; the wording as found. Measured: no failure
    in any carried section at any of the ten ratios.
24. **Twenty-fourth independent review** (2026-09-26; its second run, the first stopped at the
    owner's pause). Round 23's fixes hold, and the zoomed-out rule is stated alike in the ADRs, the
    roadmap and the note. Medium: the seeds' header cancelled the new "must not be recorded" seeds;
    the "estimator exact at 0.8 and 0.9" premise contradicted R4's one-pixel allowance, and nothing
    checked it per record (the rectangle-plus-ascent estimate was a device pixel off at 10 pt at 0.8,
    measured). Low: the 5 pt contact seed's "no free row" against the new seed; the residual's
    border condition; P6 and P7 silent on the exception; ADR 0003 broader than R4. Fixed in this
    draft: the header excepts those seeds; at 0.8 and 0.9 the baseline is taken from the drawing
    (a zero-size inline-block's rounded top, exact in all 352 measured cases) with no allowance, and
    checked per record, a difference refusing; the rest as found.
25. **Twenty-fifth independent review** (2026-09-26). Round 24's fixes to the seeds, P6, P7 and ADR
    0003 hold. Medium: the zero-size inline-block probe that round 24 added misread a baseline under
    a small shift T deletes but the authority's drawing keeps (a false pass if "unshifted" took in
    such shifts, which the note never defined); it changed layout (a lost "y," kerning pair; an
    auto-width cell rewrapped "Posolog / y") with nothing asserting otherwise; and which rules used
    the exact baseline at 0.8 and 0.9 was unclear. Low: the check's reference glyph and rounding;
    ADR 0005 broader than R4; which sections refuse. Fixed in this draft: the probe removed; at 0.8
    and 0.9 each candidate glyph's baseline is found by matching its isolated pixels to its
    calibration raster at one whole-pixel offset (no page change; no unique match refuses); that
    baseline serves P3, P4, P8(a) and R8 alike there; "unshifted" is T4's term, deleted shifts
    included; ADR 0005 cites R4.
26. **Twenty-sixth independent review** (2026-09-26). High: P1's isolation by `::highlight()` clips a
    glyph to its own advance, so overhanging ink (an italic "f"'s tail at its border) is in no
    drawing, a false clear; the new baseline match relied on it. Medium: the raster is not the same
    wherever a glyph is drawn (four horizontal sub-pixel phases and a colour dependence at 0.8 and
    0.9, measured on the Mac shell despite the flag), so the match as written would refuse every
    candidate, the label's seeded "y"s among them; the calibration page's baseline was undefined;
    the exact baseline was required of every candidate. Low: which drawing is matched. Fixed in this
    draft: isolation must composite back to the glyphs-only drawing, with no ink lost, by a method
    that does not clip, and an overhang seed; the exact baseline is a binding requirement only for
    P8(a)'s descenders against their own lines at 0.8 and 0.9, its method 3c-C's (no change to the
    judged page, references from drawn pixels, the raster's dependences accounted for, checked per
    record), and where it is not established the zoomed-out kind does not apply (judged, never a
    refusal of ours); every other glyph keeps the allowance; rounding out's horizontal reason
    corrected, 3c-B to measure the flag on the pinned image.
27. **Twenty-seventh independent review** (2026-09-26). No High, no Medium: round 26's fixes close
    its findings; the composite-back rule is satisfiable (colour-only isolation composited
    source-over reproduced the joint drawing within 1.4/255, overlaps included, at five ratios), and
    a method meeting the exact-baseline requirement exists (the flat-bottomed neighbours in the same
    run agreed in 186 and 189 of 192 lines at 0.8 and 0.9); the fallback is fail-safe, and the
    label's "y" seed rightly forces the method to succeed there. Low: the per-record check without
    content; the fallback's wording; two seeds incomplete; the composite's operator and region;
    "too faint" assuming one raster; a measurement given to 3c-B. Fixed as found.
