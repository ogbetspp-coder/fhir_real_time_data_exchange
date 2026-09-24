# ADR 0004: Service boundaries and shared code

- Status: Accepted for prototype
- Date: 2026-09-20

## Context

The hub is growing from one deterministic worker into several components: Zone A structuring
(ADR 0002), an approval service, a read-only query service, adapters. In a regulated setting a
service boundary is also a validation boundary — each component has its own intended use, risk
assessment, and validation scope — so where the boundaries fall decides how much of the system
a change re-opens. At the same time, one person is building all of it, and every artificial
boundary costs coordination and can _reduce_ traceability by splitting evidence that belongs
together.

## Decision

Services are discrete in **identity and state**, never in code.

1. **One deployable per trust boundary.** A component with a different trust level, threat
   model, or caller population from the worker is its own Cloud Run service, with its own
   service account holding least-privilege IAM, its own audit identity, its own configuration,
   and its own image whose digest is recorded in the evidence it emits. The query service does
   not run as the worker: a read-only surface must not hold write permission to the FHIR store.
   Zone A does not run as Zone B. The approval record store is not written by the publisher.
2. **Pure libraries are shared, and versioned.** The contracts (`src/contracts/`), the fidelity
   check (`src/fidelity/`), canonical JSON and hashing (`src/lib/hash.ts`), and the logger are
   shared by import. They are pure — no I/O, no environment reads, no service-specific
   knowledge — and the evidence records which version ran. Duplicating them to appear separate
   would weaken traceability, not strengthen it.
3. **Nothing implicit is shared.** No module reads another service's configuration; no
   service reaches another's database, bucket, or credential. A helper that must know about two
   services is a sign the boundary is in the wrong place.
4. **One piece of evidence is one process.** The deterministic run — transform, validate,
   persist, sign, ledger — stays a single process with a single run id and a single signed
   manifest. Splitting it into services would add hops and fragment the strongest evidence the
   system produces.
5. **Boundaries are proven, not asserted.** Every service has negative tests for what it must
   not be able to do — an identity that cannot write, a tenant that cannot read another's
   product — and carries, in `docs/validation/README.md`, a traceability row per prohibition
   naming its test. Tests assert the Terraform-declared role set; the effective IAM policy can
   be widened outside Terraform, so each deployment's evidence includes an effective-policy
   export per service account. That export is implemented. After a successful apply,
   `scripts/gcp/deploy.sh` reads the effective policy held by the worker and query service
   accounts, at project level and on the Healthcare dataset, prints it into the deploy log,
   and copies it to
   `gs://<evidence bucket>/deploy-evidence/<YYYY>/<MM>/<DD>/<UTC stamp>-<environment>-<commit>/`.
   Every step is warning-only and never fails the deploy, so a missing permission leaves a
   `::warning::` naming it rather than a silent gap. (It had not run against a project when this
   was written; it has run on every deploy of `dev` since, and the exports are in the bucket.)
   Role grants that cannot be Terraform-managed — today the
   deployer's bootstrap roles, `roles/documentai.editor` on the deployer, and the audit log
   sink's writer identity — are listed in `docs/architecture.md` with the reason, and
   re-confirmed at each deployment.
6. **One repository, until a second team exists.** Discrete deployables share one repository,
   one CI, and one release cadence. A repository split is a coordination decision, not a
   compliance one, and is deferred (ADR 0002, roadmap).

## Consequences

- Each new service adds: a service account, its IAM, a Cloud Run service, an image build, an
  entry in the deploy pipeline, a section in `docs/architecture.md` naming its intended use and
  its permissions, its own intended-use paragraph and traceability rows in
  `docs/validation/README.md`, and the negative tests decision 5 requires. That is the cost of
  a validation boundary and is paid deliberately.
- Shared libraries change under change control: a change to a shared pure library is a change
  to every service that imports it. The procedure in `docs/validation/README.md` — impact
  assessment across importing services, then approval by a role other than the author —
  applies to `src/contracts/`, `src/fidelity/`, `src/lib/hash.ts`, and `src/lib/logger.ts`.
- The evidence model gains a `service` identity where it did not have one: the query service's
  audit records name the service and its image digest, as the worker's manifest already does.
- What is _not_ done: no service mesh, no shared helper packages published to a registry, no
  per-service repositories, no splitting of the deterministic run.

## Amendment (2026-09-24, ADR 0005: the authority importer)

`src/authority/` joins decision 2's shared code (`docs/design/authority-import-contract.md`,
D10), because it decides what text enters the record: the producer
(`scripts/authority/import.ts`) and Zone B's gate run the same importer, and the gate accepts an
import only as it recomputes it. The importer (`importPublication`) is pure: no clock, locale,
`Intl` or network. Beside it are the gate's fetcher (`fetch.ts`, the network and the clock,
injected into the gate) and the vector generator (`vectors.ts`, which reads the pinned labels);
neither is on the importer's path.

Its change control is its own, beside the procedure in `docs/validation/README.md`:

- **Golden vectors.** `test/fixtures/authority/vectors.json` records what the importer makes of
  the synthetic publication and where it refuses each pinned EMA label; `npm run contracts:check`
  regenerates it (`npm run authority:vectors`) and fails on drift.
- **The lock.** `src/authority/importer.lock.json` maps each `IMPORTER_VERSION` to the SHA-256 of
  every file under `src/authority/` (code and data) and of the vectors.
  `test/authority/lock.test.ts` fails when either changes while the recorded entry does not, and
  when any entry ever released differs from its released form: CI reads every lock in the
  first-parent history of the change's base (`scripts/ci/lock-base.sh`: main for a pull request,
  the commit before the push for a push to main, from full history), so neither a second push
  nor a manual run can pass a changed released entry, and a change of behaviour or data after
  release must change the version. A rewritten history of main is a stated residual. `npm run authority:lock` writes the entry for a version not yet released.
- The version is the importer's reviewed label. Its complete identity is the worker image digest
  the run manifest records, which includes `src/fidelity/`, the hash library, the mapping and
  the dependencies; the lock covers those only where the vectors exercise them.
