# Validation lifecycle starter pack

These documents support a future risk-based validation effort. They are templates, not
executed validation or evidence that the system is in a validated state.

## Intended use

EMA Flow deterministically converts approved, authored ePI product and document inputs into
an EMA-profiled FHIR representation, rejects nonconforming outputs, stores successful outputs,
and exposes their near-real-time analytical representation.

It is not intended to:

- author or approve regulated clinical narrative;
- replace regulatory, medical, quality, or legal review;
- submit directly to EMA;
- provide patient-specific data or clinical decisions; or
- provide a human electronic-signature workflow.

## Required lifecycle records

Before regulated use, the owning organization should approve:

1. GxP impact and data-integrity risk assessment;
2. user requirements and acceptance criteria;
3. functional/configuration specification and system inventory;
4. supplier and Google Cloud service assessment;
5. requirements-to-test traceability;
6. installation/configuration qualification;
7. operational and negative-path qualification;
8. performance qualification using representative approved data;
9. security, access, backup/restore, disaster-recovery, and audit-trail tests;
10. deviations, CAPA, release, change-control, and periodic-review procedures;
11. retention, legal hold, data export, and decommissioning procedures; and
12. role training and segregation-of-duties evidence.

## Initial traceability matrix

| Requirement                           | Risk                       | Design control                                 | Automated evidence                      |
| ------------------------------------- | -------------------------- | ---------------------------------------------- | --------------------------------------- |
| UR-01 Preserve authored narrative     | Incorrect labeling         | Byte-preservation assertion                    | `transform.test.ts`, mapping decisions  |
| UR-02 Reject missing QRD sections     | Incomplete SmPC            | Fail-closed mapping                            | Negative fixture test, OperationOutcome |
| UR-03 Require all selected profiles   | Invalid EMA output         | Explicit profile-by-profile validation         | HL7 and Healthcare outcomes             |
| UR-04 Prevent unvalidated writes      | Uncontrolled record        | Write gate after outcomes                      | Pipeline tests and transaction receipt  |
| UR-05 Prove source-to-target identity | Untraceable transformation | SHA-256 hashes and lineage                     | Signed run manifest                     |
| UR-06 Preserve audit history          | Data-integrity loss        | FHIR versioning, retained logs/artifacts       | Audit exports and restore test          |
| UR-07 Observe analytical availability | Stale downstream data      | Native stream and Workflow query               | Recorded stream lag                     |
| UR-08 Control software changes        | Unapproved executable      | Provenance, Binary Authorization, Cloud Deploy | Build and rollout records               |

## Release criteria

A release candidate is not production eligible until all automated gates pass, the generated
evidence is reviewed, deviations are resolved or accepted, an independent approver authorizes
deployment, and the organization’s quality process records the release decision.

Cloud Deploy approval controls software promotion only. Product-content approval and
electronic signature require a separately validated business workflow with signer identity,
signature meaning, re-authentication where required, and permanent signature-to-record
linkage.
