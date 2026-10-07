import type { KeyObject } from "node:crypto";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { z } from "zod";

import { approvalPublicKey, verifyPss } from "../approval/statement.js";
import { parseStrictJson, decodeUtf8 } from "../authority/json.js";
import { ApprovalEnvironment } from "../contracts/approval.js";
import type { CanonicalSubmission, CertifiedWordSourceDocument } from "../contracts/index.js";
import {
  GitCommit,
  ImageDigest,
  PositiveInt,
  Sha256Hex,
  SourceKey,
  Token,
} from "../contracts/common.js";
import { RecomputeRequestSchema } from "../contracts/ingestion-provenance.js";
import { canonicalJson, sha256 } from "../lib/hash.js";

// The gate's step 5 (docs/design/certified-word-drawing.md, section 3, "How Zone B verifies it"):
// the signed drawing record of the submission's .docx and recompute request, found at its path in
// the record bucket, verified against a public key this build pins (src/render/word-drawing/), and
// held to the submission and to the worker's own recompute. No call to Cloud KMS; the read is
// injected.

// The most bytes a record may have; it is refused before anything is parsed.
export const MAX_RECORD_BYTES = 64 * 1024;

export type DrawingEnvironment = z.infer<typeof ApprovalEnvironment>;

// What this build pins for its environment: the drawing's version and image (lock.json), and each
// key version's public key (keys/<environment>/<n>.pem) not listed in revoked.json, highest first.
export type DrawingPins = {
  environment: DrawingEnvironment;
  version: string;
  imageDigest: string | null;
  keys: [number, KeyObject][];
};

// Where the records are: `read` answers an object's bytes (at most MAX_RECORD_BYTES and one more),
// undefined when there is none, and throws on any other Storage error, which fails the run.
export type DrawingSource = {
  pins: DrawingPins;
  read: (object: string) => Promise<Uint8Array | undefined>;
};

const LockSchema = z.strictObject({
  version: Token,
  imageDigests: z.strictObject({
    dev: ImageDigest.nullable(),
    validation: ImageDigest.nullable(),
    prod: ImageDigest.nullable(),
  }),
});

// The pins as `scripts/word-drawing/build.sh` reads them, from the working directory's
// src/render/word-drawing (the worker image copies it to /app, its working directory).
export function drawingPins(
  environment: DrawingEnvironment,
  root = path.resolve("src/render/word-drawing"),
): DrawingPins {
  const lock = LockSchema.parse(JSON.parse(readFileSync(path.join(root, "lock.json"), "utf8")));
  const folder = path.join(root, "keys", environment);
  const names = existsSync(folder) ? readdirSync(folder) : [];
  const revoked = names.includes("revoked.json")
    ? z
        .array(z.number().int())
        .parse(JSON.parse(readFileSync(path.join(folder, "revoked.json"), "utf8")))
    : [];
  const versions = names.flatMap((name) => {
    const version = /^([1-9][0-9]*)\.pem$/.exec(name)?.[1];
    return version === undefined || revoked.includes(Number(version)) ? [] : [Number(version)];
  });
  return {
    environment,
    version: lock.version,
    imageDigest: lock.imageDigests[environment],
    keys: versions
      .sort((a, b) => b - a)
      .map((version) => [
        version,
        approvalPublicKey(readFileSync(path.join(folder, `${String(version)}.pem`), "utf8")),
      ]),
  };
}

// WordDrawingRecord (section 3, "Its fields"), stored as { record, signatureBase64 }.
const StoredRecordSchema = z.strictObject({
  record: z.strictObject({
    recordVersion: z.literal("word-drawing-record/1.0.0"),
    environment: ApprovalEnvironment,
    commitSha: GitCommit,
    request: z.strictObject({ docxSha256: Sha256Hex, recompute: RecomputeRequestSchema }),
    document: z.strictObject({ sha256: Sha256Hex, byteLength: PositiveInt }),
    recompute: z.strictObject({ outputSha256: Sha256Hex }),
    drawing: z.strictObject({
      version: Token,
      chrome: z.string().min(1).max(256),
      imageDigest: ImageDigest,
    }),
    sections: z
      .array(z.strictObject({ key: SourceKey, narrativeDivSha256: Sha256Hex }))
      .min(1)
      .max(2_000),
    keyVersion: PositiveInt,
  }),
  signatureBase64: z.string().regex(/^[A-Za-z0-9+/]+={0,2}$/),
});

type WordDrawingRecord = z.infer<typeof StoredRecordSchema>["record"];

// The record's key: the SHA-256 of the canonical JSON of the drawing request, made from the source.
export function recordKey(source: CertifiedWordSourceDocument): string {
  return sha256({ docxSha256: source.document.sha256, recompute: source.recompute });
}

// The drawing id: the SHA-256 of the canonical JSON of { version, imageDigest } this build pins.
export function drawingId(version: string, imageDigest: string): string {
  return sha256({ version, imageDigest });
}

// The record in the bytes read at `word/<key>/<id>/<keyVersion>.json`, if they are one this build
// can verify: within the cap, the canonical JSON of exactly the stored shape (no repeated key, one
// spelling), its keyVersion and request those of its path, and its signature by that key version.
function verified(
  bytes: Uint8Array,
  key: string,
  keyVersion: number,
  publicKey: KeyObject,
): WordDrawingRecord | undefined {
  if (bytes.length > MAX_RECORD_BYTES) return undefined;
  let text: string;
  let value: unknown;
  try {
    text = decodeUtf8(bytes);
    value = parseStrictJson(text);
  } catch {
    return undefined;
  }
  if (canonicalJson(value) !== text) return undefined;
  const parsed = StoredRecordSchema.safeParse(value);
  if (!parsed.success) return undefined;
  const { record, signatureBase64 } = parsed.data;
  if (record.keyVersion !== keyVersion || sha256(record.request) !== key) return undefined;
  return verifyPss(canonicalJson(record), signatureBase64, publicKey) ? record : undefined;
}

// Step 5's verdict: `drawn` for a signed record of this very submission; `missing` where no pinned
// key version's path holds one (or nothing is pinned); `invalid` for an object that is not a
// record this build verifies; `mismatch` for a signed record whose environment, .docx, output,
// drawing or sections are not this submission's. The first object found decides: the gate never
// steps over it to a lower key version.
export type DrawingVerdict = "drawn" | "missing" | "invalid" | "mismatch";

export async function drawingVerdict(
  from: DrawingSource,
  submission: CanonicalSubmission,
  source: CertifiedWordSourceDocument,
  outputSha256: string,
): Promise<DrawingVerdict> {
  const { pins } = from;
  if (pins.imageDigest === null) return "missing";
  const key = recordKey(source);
  const id = drawingId(pins.version, pins.imageDigest);
  for (const [keyVersion, publicKey] of pins.keys) {
    const bytes = await from.read(`word/${key}/${id}/${String(keyVersion)}.json`);
    if (bytes === undefined) continue;
    const record = verified(bytes, key, keyVersion, publicKey);
    if (record === undefined) return "invalid";
    // The request is the source's already: it hashes to the key made from the source.
    const sections = submission.provenance.sections.map(({ sourceKey, narrativeDivSha256 }) => ({
      key: sourceKey,
      narrativeDivSha256,
    }));
    const same =
      record.environment === pins.environment &&
      record.document.sha256 === source.document.sha256 &&
      record.document.byteLength === source.document.byteLength &&
      record.recompute.outputSha256 === outputSha256 &&
      record.drawing.version === pins.version &&
      record.drawing.imageDigest === pins.imageDigest &&
      sha256(record.sections) === sha256(sections);
    return same ? "drawn" : "mismatch";
  }
  return "missing";
}
