import { z } from "zod";

// Shared primitives for every Zone A / Zone B contract. Each named primitive carries a
// `.meta({ id })` so it becomes a reusable `$defs` entry in the generated JSON Schema and a
// stable model name for code generated from it (ADR 0002).

export const Sha256Hex = z
  .string()
  .regex(/^[0-9a-f]{64}$/)
  .meta({ id: "Sha256Hex", description: "Lower-case hexadecimal SHA-256 digest." });

export const Uuid = z.uuid().meta({ id: "Uuid" });

export const IsoDateTime = z.iso
  .datetime({ offset: true })
  .meta({ id: "IsoDateTime", description: "RFC 3339 timestamp with Z or a numeric offset." });

export const HttpUrl = z.url().max(256).meta({ id: "HttpUrl" });

// Identifier-class strings (tool names, versions, ids, codes). Token-limited so that no
// provenance field can carry prose into a manifest, ledger row, or Provenance resource.
export const Token = z
  .string()
  .regex(/^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$/)
  .meta({ id: "Token", description: "Identifier: letters, digits, and . _ : / @ + - only." });

// JSON path of a target field inside the Bundle, e.g. Composition.section[0].section[1].code.
export const TargetPath = z
  .string()
  .regex(/^[A-Za-z][A-Za-z0-9]*(?:\[\d+\])?(?:\.[A-Za-z][A-Za-z0-9]*(?:\[\d+\])?)*$/)
  .max(256)
  .meta({ id: "TargetPath" });

export const StorageUri = z
  .string()
  .regex(/^gs:\/\/[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]\/\S+$/)
  .meta({ id: "StorageUri", description: "Cloud Storage object URI (gs://bucket/object)." });

export const SourceKey = z
  .string()
  .regex(/^[a-z0-9]+(?:\.[a-z0-9]+)*$/)
  .meta({
    id: "SourceKey",
    description:
      "Canonical SmPC section identifier from the mapping manifest, e.g. smpc.4.2.posology.",
  });

export const NonEmptyString = z.string().min(1).max(1024);

export const Count = z.number().int().nonnegative();

export const PositiveInt = z.number().int().positive();

export const NormalizationVersion = z
  .string()
  .regex(/^fidelity-norm\/\d+\.\d+\.\d+$/)
  .meta({
    id: "NormalizationVersion",
    description: "Version of docs/fidelity-normalization.md the hashes were computed under.",
  });
