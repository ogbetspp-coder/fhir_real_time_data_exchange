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
   product — and the Terraform that grants its roles is the only place they are granted.
6. **One repository, until a second team exists.** Discrete deployables share one repository,
   one CI, and one release cadence. A repository split is a coordination decision, not a
   compliance one, and is deferred (ADR 0002, roadmap).

## Consequences

- Each new service adds: a service account, its IAM, a Cloud Run service, an image build, an
  entry in the deploy pipeline, and a section in `docs/architecture.md` naming its intended use
  and its permissions. That is the cost of a validation boundary and is paid deliberately.
- Shared libraries change under change control: a change to a shared pure library is a change
  to every service that imports it, and the release notes say so.
- The evidence model gains a `service` identity where it did not have one: the query service's
  audit records name the service and its image digest, as the worker's manifest already does.
- What is _not_ done: no service mesh, no shared helper packages published to a registry, no
  per-service repositories, no splitting of the deterministic run.
