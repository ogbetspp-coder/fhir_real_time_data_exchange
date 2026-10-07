# Recorded change: the Word drawing's pins in dev, 2026-10-07

_Follows the procedure in `docs/validation/README.md`, "Change control for shared, evidenced
libraries", as the drawing's entry point did
(`2026-10-07-word-drawing-entry-point.md`, item 5, the lock)._

Dev's drawing image and dev's first record-signing public key. No version changes. ADR 0006 P4,
D3: `docs/design/certified-word-drawing.md`, build order step 4 (PR 3), "Step 3: PR 3 as built".

**What changed.**

1. **The lock** (`src/render/word-drawing/lock.json`): dev's image digest,
   `sha256:eff4827a9f5ebefe001e624ae197ecace2ca126d8827f2fe70dacf1632061c27`, the `word-drawing`
   target the images build pushed from main at `cb27bd9` (Cloud Build
   `adba9d09-1227-4361-88b4-d2c20930ad67`, from the deploy's run 37688275636; the image's
   `org.opencontainers.image.revision` is that commit). Validation and prod stay null.
2. **The key** (`src/render/word-drawing/keys/dev/1.pem`): `word-drawing-hsm` version 1's public
   key in dev (`RSA_SIGN_PSS_3072_SHA256`, HSM, enabled), the PEM Cloud KMS served, its CRC32C the
   `pemCrc32c` it reported (939517586).

**Why.** The drawing build (`scripts/word-drawing/build.sh`, `exists`) refuses every request while
no image digest and no key version are pinned for its environment. With these, a request in dev
goes on to draw and, where every section agrees, to sign a record that the gate (PR 4) will
verify against this key.

**Impact assessment (step 0).**

- Zone B (`src/`): nothing reads either file yet; the worker image carries `dist/` only. PR 4's
  gate reads the key, and puts it in the worker image.
- The drawing build in dev: it goes past `exists`, pulls this image by digest, and signs with key
  version 1. Records are keyed under dev's drawing id,
  `03d058be759c88620d0748c07ea6c747c468b7ad9b0e95f23476e07db67d4000`. No record exists yet.
- Validation and prod: unchanged; their builds still end at `exists`, refused.
- Zone A, the importers, contracts and vectors: unchanged. The lock and the keys are outside
  `src/certified-word/` and every other lock, so no version moves.
- Evidence: none produced before this change refers to a drawing record.

**Steps 1–7.** 1: no version literal moves (the drawing id moves with the image, by design). 2:
`npm run contracts:check` shows no drift. 3: no vector changes. 4:
`test/render/word-drawing.test.ts` (dev's digest a digest reference; the key RSA 3072, exponent
65537, its PEM the key's own SubjectPublicKeyInfo export with KMS's CRC32C, verifying under PSS
with SHA-256 and a 32-byte salt) and `test/infra/word-drawing.test.ts` (`exists` with main's own
pins: dev goes on, by the pinned image and key version 1; validation and prod refused). 5: no ADR
amended. 6: none. 7: no approval is affected.

**Blast radius.** Dev's drawing build signs records where it refused before. A record proves only
what the design note's section 3 says, and nothing persists on one until PR 4's gate and P5.

**Tests.** `test/render/word-drawing.test.ts`, `test/infra/word-drawing.test.ts`.

**Not verified here.** The build's steps after `exists` against the deployed resources: `pull` as
the drawing identity, `fetch` under its conditioned grant, Cloud KMS's signature and the write.
The first record, made after the merge from the committed synthetic SmPC, is their first run.

**Approval (step 8).** Not obtained: the author and the releaser are the same identity.
