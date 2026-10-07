# Recorded change: the certified Word gate recomputes, 2026-10-07

The certified Word importer 1.1.0 (`certified-word-import/1.1.0`), `recompute/1.1.0`, the worker
image. No contract version changes.

**What changed.** ADR 0006 prerequisite P4, D2 and D4 (`docs/design/certified-word-import.md`,
"D4, as built" and "The gate").

1. **The upload path (D4).** The producer stores the .docx in the existing CMEK submissions bucket
   at `uploads/sha256/<its SHA-256>.docx`. The gate (`src/certified-word/recompute.ts`,
   `readUpload`) reads only that object of the configured bucket, whose name holds the source's own
   `sha256`; refuses a `byteLength` over 32 MiB before reading; reads `byteLength + 1` bytes at
   most, undecompressed, under the worker's identity (the submission reader's ranged read, now
   exported); and requires the length and SHA-256 to be the source's. No IAM change: the worker
   already reads the whole bucket, and a prefix condition would refuse the submissions themselves.
2. **The recompute in the worker (D2).** The worker image (only the worker's target) gains
   Python 3.14.7 (python-build-standalone, installed by uv 0.12.17 pinned by digest) and the
   zone-a and label-docx packages, installed non-editable from `zone-a/uv.lock`
   (`uv sync --locked --no-dev --no-editable`), and `qrd/registry`. Verified here, each with uv
   0.12.17 on macOS (the same code as the Linux build's): served the Python archive with one byte
   changed (through `--mirror`), `uv python install 3.14.7` refused it, naming the SHA-256 it
   expected, which the uv binary carries; the genuine archive installed. A copy of `zone-a/uv.lock`
   with one wheel's hash changed made `uv sync --locked` refuse that wheel. The package READMEs are
   not copied: empty ones stand in (they are only the long description); it sets `RECOMPUTE_PYTHON` and `ZONE_A_ROOT`, and its build asserts
   Unicode 16.0.0 (ADR 0003) and that the recompute reads its files there. `zone_a.recompute` reads
   the registry and mapping files under `ZONE_A_ROOT` where it is set, since an installed package is
   in no checkout (`recompute/1.0.0` → `1.1.0`, the same output for the same input). The gate runs
   `python -I -X utf8 -m zone_a.recompute LABEL.docx` with the source's request on standard input:
   an environment of `ZONE_A_ROOT` alone, the root as working directory, standard error discarded,
   standard output capped at 32 MiB, killed at 60 s.
3. **The gate** (`src/certified-word/gate.ts`): after the importer-version check, read the upload,
   run the recompute (its refusal refuses the run, `certified-word-recompute-refused`; any other
   failure refuses with a closed reason), make the importer's request again from the submission,
   run the importer on the recompute's bytes and require the identical submission, page text and
   report by SHA-256. A run that is not a dry run is then refused, `certified-word-drawing-missing`,
   until D3; a dry run goes on to the ordinary gate as before. A worker without the bucket or the
   Python recomputes nothing: a dry run checks what the submission holds, as before, and any other
   run is refused, `certified-word-not-recomputed`. `SubmissionRefusal` gains the two new codes the
   HTTP answer carries (`reason`).
4. **The importer's version** (`src/certified-word/import.ts`, 1.0.0 → 1.1.0): what it makes is
   unchanged; its lock covers its directory, which holds the gate, so the gate's change is a new
   version (AGENTS.md), and so a new extractor token.
5. **Fixtures**: `zone-a/scripts/certified_word_fixtures.py` also commits each synthetic label
   (`<name>.docx`), which the gate's tests read as the uploads; every committed result moves with
   the recompute's version, and the importer's golden vectors with both versions.
6. **CI and the image build**: the Zone A job runs `test/certified-word/recompute.test.ts` with its
   own Python (`RECOMPUTE_PYTHON`, 3.14.7 as the image's), and `scripts/check-all.sh` with it; the
   Images job and Cloud Build run the gate's runner inside the worker image on the committed labels
   and require each result byte for byte.
7. **The deploy trigger** (`.github/workflows/deploy.yml`): the worker image now reads
   `zone-a/src`, `label-docx-reader/src`, their `pyproject.toml`, `zone-a/uv.lock` and
   `qrd/registry`, so a merge touching only them deploys; every other file of those three folders
   is still skipped (`test/ci/deploy-trigger.test.ts`). Without this, a recompute change merged alone would leave
   the deployed worker on the previous build, which refuses every submission the new one makes.

**Why.** ADR 0006 decision 1's second leg: Zone B makes the sections again from the uploaded bytes
itself, rather than trusting Zone A's (ADR 0002). The owner decided D2 (a) and D4's narrow path on
2026-10-06.

**Impact assessment (step 0).**

- Zone B (`src/`): the certified Word gate, the pipeline's wiring to it, the configuration
  (`RECOMPUTE_PYTHON`, `ZONE_A_ROOT`, optional). Drawn sources and authority imports: unchanged.
- The worker image: larger (a Python runtime and two packages); its Node, ICU and Unicode are
  unchanged and still asserted. The query service's and the signer's images: unchanged.
- Zone A: `zone_a.recompute`'s version; its results are byte for byte the same but for the version
  string. Every other component's version: unchanged.
- Contracts: no version moves; `RecomputeVersions` names the recompute's version as before.
- Evidence: a certified Word submission made at importer 1.0.0 or `recompute/1.0.0` is refused by
  this gate (another importer version, or a request naming versions this build does not have). None
  is persisted, so no evidence is affected; the run manifest fixture `6.0.0-certified-word.json`
  stays the evidence of its version, never regenerated.
- Infrastructure: no Terraform change; no IAM change (above). More merges deploy (step 7).

**Steps 1–7.** 1: the versions above. 2: `uv run --frozen python scripts/certified_word_fixtures.py`
and `scripts/lock_versions.py` in `zone-a/`; `npm run certified-word:vectors` and
`npm run certified-word:lock`; `npm run contracts:check` (no other drift). 3: every changed vector
reviewed: the committed results differ only in `recompute/1.1.0`; each vector's result hash moves
with them, the submission hashes with the importer's version and the token; every refusal vector's
outcome is unchanged. 4: `test/certified-word/recompute.test.ts`: the upload's URI rules (other
bucket, prefix, hash, case, segments, URL), hash and length (a byte flipped, a byte more or less,
missing, over the cap); every subprocess ending (no Python, timeout, oversized output, another
status, a signal, status 1 without a refusal, a refusal and its code kept only as a token, a broken
pipe); the gate's refusals of a narrative and page, a page text, a report, an assignment and an
upload not the label's, of a request naming other versions and a result naming them, of malformed
output, and of every run but a dry one; `zone-a/tests/test_recompute.py` for `ZONE_A_ROOT`. 5: ADR
0002's invariant 11 and ADR 0006's progress amended. 6: UR-14. 7: an approval of a submission made
at importer 1.0.0 does not carry forward (none exists).

**Blast radius.** A source the previous gate accepted and this one refuses: a certified Word
submission made at importer 1.0.0; in a worker that can recompute, a dry run whose upload is not at
its content address or whose sections are not the upload's. None it accepts that the previous
refused.

**Tests.** `test/certified-word/` (with the real Python in CI's Zone A job), `test/ci/images.test.ts`,
`test/ci/check-all.test.ts`, `zone-a/tests/test_recompute.py`,
`zone-a/tests/test_certified_word_fixtures.py`; the Images job's run of the recompute in the worker
image.

**The independent review of #196**, each fixed with a test where code is involved:

1. **High: `.gcloudignore` left out `label-docx-reader/`**, which the worker image copies and
   `zone-a/uv.lock` names by path, so Cloud Build would have failed at `COPY` on every deploy
   while CI's Images job, building from the full checkout, passed
   (`gcloud meta list-files-for-upload .` listed no file of it). Now only its corpus, tests, scripts and
   documents are left out (the same command lists its `pyproject.toml` and `src/`), and
   `test/ci/gcloudignore.test.ts` reads `.gcloudignore` with gitignore's semantics (the `ignore`
   library, now a declared dev dependency at the version eslint already installed; gcloud's
   `#!include:` expanded) and requires every path the built Dockerfiles copy, every project a
   copied `uv.lock` names by path, and every path a Cloud Build step reads to survive it. It fails
   on the old file.
2. The image Cloud Build builds is now proven: a `worker-recompute-smoke` step runs
   `scripts/ci/worker-recompute-smoke.sh` on it before anything is pushed, the script CI's Images
   job runs, which there also runs once on a changed result and must fail.
3. Fewer needless deploys: of `zone-a/`, `label-docx-reader/` and `qrd/`, a merge deploys for
   exactly what the Dockerfile copies (`test/ci/deploy-trigger.test.ts` holds both lists to it);
   the label reader's own `uv.lock`, both `versions.lock.json` and the projects' dot files and
   licence no longer deploy.
4. A dry run's answer and completion log carry `certifiedWordCheck`, `recomputed` or
   `submission-only`.
5. Every workflow's Python is pinned to 3.14.7, the image's (CI had moved to 3.14.8), and
   `test/ci/images.test.ts` holds them equal.
6. `RECOMPUTE_PYTHON` and `ZONE_A_ROOT` must be absolute paths; the ordinary gate's refusal and
   `GateOptions.certifiedWordDryRun` no longer say Zone B does not recompute ("until its drawing is
   recorded"); the uv claims above say what was verified.
7. The design note records what an oversized label does (the instance is killed for memory; the
   run fails closed), for measurement on the first deploy.

**Not verified here.** Docker is not available on the machine this was written on: the worker
image's build and its recompute smoke run in CI's Images job and, on deploy, in Cloud Build (where
it has not run yet). The recompute's memory on the largest labels, inside the worker's 1 GiB with
four requests at once, is not measured. That Cloud Build pushes `images` only after every step has
passed is its documented behaviour, not tested here. Dropping the
environment keeps credentials' variables from the subprocess, but on Cloud Run any process in the
container can reach the metadata server; the recompute opens no socket (it reads the label and the
committed files only), and nothing but that code stops one.

**Approval (step 8).** Not obtained: the author and the releaser are the same identity (see
"Release criteria").
