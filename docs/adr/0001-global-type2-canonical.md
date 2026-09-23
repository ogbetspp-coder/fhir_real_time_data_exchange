# ADR 0001: Global ePI Type 2 as interchange baseline

- Status: Accepted for prototype
- Date: 2026-09-19

## Context

EMA EU ePI is the immediate target, but it is jurisdiction-specific and version 1.0.0 remains
a preview. Pharmaceutical companies may also serve regulators with different document,
terminology, and lifecycle requirements.

HL7 Global ePI is designed as a common baseline. Its published 1.0.0 package is trial-use and
must not be confused with a stable enterprise master-data model.

## Decision

Use the published `hl7.fhir.uv.emedicinal-product-info#1.0.0` Bundle profile and a complete
Type 2 resource graph as the narrow interchange baseline. Keep authored document narrative as
an explicit source input. Apply EMA StructureDefinitions and QRD terminology as an output
projection and validation contract.

The package, validator, profile artifacts, and non-normative examples are checksum pinned.
Profile upgrades require change control, mapping review, regression validation, and evidence
regeneration.

## Consequences

- Multi-market transformations have a consistent product graph.
- EMA-specific extensions and section codes do not leak into the global product model.
- A mapping/evidence layer must be maintained.
- Trial-use changes remain a material architecture risk.
- An EMA-only deployment can remove the projection and author directly to EMA profiles.

## Amendment (2026-09-23, ADR 0005)

An authority's published ePI may enter the record as a Global ePI Type 1 graph: the
Composition, the product name, the marketing authorisation holder and the authorisation, all
from the authority's structured index. Packs, ingredients and substances are declared not
supplied, never inferred from narrative. Every other submission stays a complete Type 2 graph.
