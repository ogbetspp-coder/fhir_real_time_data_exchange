import { constants, generateKeyPairSync, sign, type KeyObject } from "node:crypto";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { sha256Bytes } from "../../src/authority/import.js";
import {
  MAX_RECORD_BYTES,
  drawingId,
  drawingPins,
  drawingVerdict,
  recordKey,
  type DrawingPins,
} from "../../src/certified-word/drawing.js";
import { importCertifiedWord } from "../../src/certified-word/import.js";
import {
  RUN,
  caseRequest,
  recomputed,
  recomputedCases,
  type RecomputedCase,
} from "../../src/certified-word/vectors.js";
import type {
  CanonicalSubmission,
  CertifiedWordSourceDocument,
} from "../../src/contracts/index.js";
import { loadEmaMapping, type EmaMapping } from "../../src/fhir/mapping.js";
import { canonicalJson } from "../../src/lib/hash.js";

// The gate's step 5 (docs/design/certified-word-drawing.md, section 3, "How Zone B verifies it"),
// against the first real record: what dev's drawing build signed with word-drawing-hsm version 1
// for the committed synthetic SmPC (test/fixtures/certified-word/recompute/smpc.docx), copied byte
// for byte from gs://sage-ship-509104-b8-ema-flow-dev-word-drawings/<PATH> on 2026-10-07. Records
// that are not real are signed here with a key made for the test.
//
// It is a record of the build it was drawn by (word-epi/1.3.2, word-drawing/1.2.0), so it is held
// to what that build made: the recompute's bytes it names, frozen beside it (smpc.recompute.json),
// its own request, and dev's pins with the drawing's version of then. A later build's request
// has another path, so it finds this record nowhere ("a later build", below).

const REAL_RECORD = readFileSync("test/fixtures/certified-word/drawing/smpc.record.json");
const SIGNED_FOR = readFileSync("test/fixtures/certified-word/drawing/smpc.recompute.json");
const PATH =
  "word/7e4c389fdb72bf3b804e1240e043eba02352fd3a70ebe25283b06a53fcd92809/03d058be759c88620d0748c07ea6c747c468b7ad9b0e95f23476e07db67d4000/1.json";
const real = JSON.parse(REAL_RECORD.toString("utf8")) as {
  record: Record<string, unknown> & {
    sections: unknown[];
    request: { recompute: RecomputedCase["request"] };
    drawing: { version: string };
  };
  signatureBase64: string;
};

let mapping: EmaMapping;
let submission: CanonicalSubmission;
let source: CertifiedWordSourceDocument;
let output: string;
let dev: DrawingPins;
let own: { publicKey: KeyObject; privateKey: KeyObject };

function certifiedSource(made: CanonicalSubmission): CertifiedWordSourceDocument {
  const named = made.provenance.sourceDocument;
  if (named.kind !== "certified-word") throw new Error("not a certified Word source");
  return named;
}

function smpcCase(): RecomputedCase {
  const found = recomputedCases().find(({ name }) => name === "smpc");
  if (found === undefined) throw new Error("no smpc case");
  return found;
}

beforeAll(async () => {
  mapping = await loadEmaMapping();
  const signed = { ...caseRequest(smpcCase()), recompute: real.record.request.recompute };
  ({ submission } = importCertifiedWord(SIGNED_FOR, signed, mapping, RUN));
  source = certifiedSource(submission);
  // What the recompute wrote for the committed label at that build, byte for byte.
  output = sha256Bytes(SIGNED_FOR);
  // Dev's pins as they stood when the record was signed: the image and key are the same.
  dev = { ...drawingPins("dev"), version: real.record.drawing.version };
  own = generateKeyPairSync("rsa", { modulusLength: 3072 });
});

// A record stored as the build stores one, signed by `key` as Cloud KMS signs (salt 32).
function stored(record: unknown, key = own.privateKey, saltLength = 32): Buffer {
  const signature = sign("sha256", Buffer.from(canonicalJson(record)), {
    key,
    padding: constants.RSA_PKCS1_PSS_PADDING,
    saltLength,
  });
  return Buffer.from(canonicalJson({ record, signatureBase64: signature.toString("base64") }));
}

// The bucket: each object by its name; every name read is noted.
function bucket(objects: Record<string, Uint8Array>, asked: string[] = []) {
  return (object: string): Promise<Uint8Array | undefined> => {
    asked.push(object);
    return Promise.resolve(objects[object]);
  };
}

const at = (version: number): string => PATH.replace(/1\.json$/, `${String(version)}.json`);

function verdict(
  objects: Record<string, Uint8Array>,
  pins: DrawingPins = dev,
  asked: string[] = [],
): ReturnType<typeof drawingVerdict> {
  return drawingVerdict({ pins, read: bucket(objects, asked) }, submission, source, output);
}

// Dev's pins with the test key in version 1's place.
const testPins = (): DrawingPins => ({ ...dev, keys: [[1, own.publicKey]] });

describe("the real record", () => {
  it("is at the path made from the submission's .docx and request and dev's pins", () => {
    expect(PATH).toBe(
      `word/${recordKey(source)}/${drawingId(dev.version, dev.imageDigest ?? "")}/1.json`,
    );
    expect(dev.keys.map(([version]) => version)).toEqual([1]);
  });

  it("verifies against dev's pinned key and is this submission's drawing", async () => {
    const asked: string[] = [];
    expect(await verdict({ [PATH]: REAL_RECORD }, dev, asked)).toBe("drawn");
    expect(asked).toEqual([PATH]);
    // 32 sections, in the provenance's order, each the narrative the submission carries.
    expect(real.record.sections).toEqual(
      submission.provenance.sections.map(({ sourceKey, narrativeDivSha256 }) => ({
        key: sourceKey,
        narrativeDivSha256,
      })),
    );
    expect(real.record.sections).toHaveLength(32);
  });

  it("is found by no later build: its request and drawing's version have other paths", async () => {
    // This build (word-epi/1.8.0, word-drawing/1.2.7) on the same label, with dev's pins now.
    expect(real.record.drawing.version).toBe("word-drawing/1.2.0");
    const later = importCertifiedWord(recomputed("smpc"), caseRequest(smpcCase()), mapping, RUN);
    const asked: string[] = [];
    const now = drawingVerdict(
      { pins: drawingPins("dev"), read: bucket({ [PATH]: REAL_RECORD }, asked) },
      later.submission,
      certifiedSource(later.submission),
      sha256Bytes(recomputed("smpc")),
    );
    expect(await now).toBe("missing");
    expect(asked).toHaveLength(1);
    expect(asked[0]).not.toBe(PATH);
  });
});

describe("an object that is not a record this build verifies", () => {
  it("is invalid: its signature, its bytes or its spelling changed", async () => {
    const flipped = real.signatureBase64.replace(/^(.{9})(.)/, (_, head: string, c: string) =>
      head.concat(c === "A" ? "B" : "A"),
    );
    const text = REAL_RECORD.toString("utf8");
    const section = real.record.sections[3] as { narrativeDivSha256: string };
    for (const [why, bytes] of [
      ["another signature", Buffer.from(text.replace(real.signatureBase64, flipped))],
      ["signed by another key", stored(real.record)],
      [
        "a section's hash changed",
        Buffer.from(text.replace(section.narrativeDivSha256, "0".repeat(64))),
      ],
      [
        "the commit changed",
        Buffer.from(text.replace(/"commitSha":"[0-9a-f]{40}"/, `"commitSha":"${"a".repeat(40)}"`)),
      ],
      ["spelled otherwise", Buffer.from(JSON.stringify(real, null, 1))],
      [
        "a repeated key",
        Buffer.from(text.replace('{"record":{', '{"record":{"environment":"dev",')),
      ],
      ["not JSON", Buffer.from(text.slice(0, -1))],
      ["empty", Buffer.alloc(0)],
      ["a byte-order mark", Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), REAL_RECORD])],
      ["more fields", stored({ ...real.record, extra: 1 })],
    ] as const) {
      const pins = why === "more fields" ? testPins() : dev;
      expect([why, await verdict({ [PATH]: bytes }, pins)]).toEqual([why, "invalid"]);
    }
  });

  it("is invalid: its signature in another base64 spelling, or with another salt length", async () => {
    // 384 bytes are 512 base64 characters and no padding: with "==" more, the same bytes.
    expect(real.signatureBase64).toHaveLength(512);
    const padded = REAL_RECORD.toString("utf8").replace(
      real.signatureBase64,
      `${real.signatureBase64}==`,
    );
    expect(await verdict({ [PATH]: Buffer.from(padded) })).toBe("invalid");
    // The test key's signature with Cloud KMS's salt, 32 bytes, verifies; with none, it does not.
    expect(await verdict({ [PATH]: stored(real.record) }, testPins())).toBe("drawn");
    expect(await verdict({ [PATH]: stored(real.record, own.privateKey, 0) }, testPins())).toBe(
      "invalid",
    );
  });

  it("is invalid: its keyVersion or request is not its path's", async () => {
    // The real record at version 2's path, version 2 being dev's key.
    const two = { ...dev, keys: [[2, dev.keys[0]?.[1]]] } as DrawingPins;
    expect(await verdict({ [at(2)]: REAL_RECORD }, two)).toBe("invalid");
    // Signed, but naming version 2 at version 1's path.
    expect(await verdict({ [PATH]: stored({ ...real.record, keyVersion: 2 }) }, testPins())).toBe(
      "invalid",
    );
    // Signed, but another request than the path's key: another view of the same .docx.
    const request = real.record.request as { recompute: object };
    const other = { ...request, recompute: { ...request.recompute, view: "accepted" } };
    expect(await verdict({ [PATH]: stored({ ...real.record, request: other }) }, testPins())).toBe(
      "invalid",
    );
  });

  it("is invalid over 64 KiB, before anything else is read of it", async () => {
    // A signed record of this request, too long: under the cap it would be a mismatch.
    const sections = Array.from({ length: 700 }, (_, index) => ({
      key: `smpc.${String(index)}`,
      narrativeDivSha256: "0".repeat(64),
    }));
    const long = stored({ ...real.record, sections });
    expect(long.length).toBeGreaterThan(MAX_RECORD_BYTES);
    expect(await verdict({ [PATH]: long }, testPins())).toBe("invalid");
    const short = stored({ ...real.record, sections: sections.slice(0, 500) });
    expect(short.length).toBeLessThanOrEqual(MAX_RECORD_BYTES);
    expect(await verdict({ [PATH]: short }, testPins())).toBe("mismatch");
  });
});

describe("a signed record that is not this submission's", () => {
  it("is a mismatch in its environment, .docx, output, drawing or sections", async () => {
    const record = real.record as typeof real.record & {
      document: { byteLength: number };
      recompute: object;
      drawing: object;
    };
    // The record itself, signed by the test key: drawn.
    expect(await verdict({ [PATH]: stored(record) }, testPins())).toBe("drawn");
    const reversed = [...record.sections].reverse();
    for (const [why, changed] of [
      ["environment", { ...record, environment: "validation" }],
      ["byteLength", { ...record, document: { ...record.document, byteLength: 8395 } }],
      ["document", { ...record, document: { ...record.document, sha256: "0".repeat(64) } }],
      ["output", { ...record, recompute: { outputSha256: "0".repeat(64) } }],
      ["version", { ...record, drawing: { ...record.drawing, version: "word-drawing/1.2.1" } }],
      [
        "image",
        { ...record, drawing: { ...record.drawing, imageDigest: `sha256:${"0".repeat(64)}` } },
      ],
      ["order", { ...record, sections: reversed }],
      ["one less", { ...record, sections: record.sections.slice(1) }],
    ] as const) {
      expect([why, await verdict({ [PATH]: stored(changed) }, testPins())]).toEqual([
        why,
        "mismatch",
      ]);
    }
  });
});

describe("the read", () => {
  it("finds nothing where no pinned key version's path holds a record, or nothing is pinned", async () => {
    const asked: string[] = [];
    expect(await verdict({}, dev, asked)).toBe("missing");
    expect(asked).toEqual([PATH]);
    // An environment with no image or no key pinned reads nothing.
    for (const pins of [{ ...dev, imageDigest: null }, { ...dev, keys: [] }, drawingPins("prod")]) {
      const none: string[] = [];
      expect(await verdict({ [PATH]: REAL_RECORD }, pins, none)).toBe("missing");
      expect(none).toEqual([]);
    }
  });

  it("lets the first object found decide, highest key version first, never stepping over it", async () => {
    const both: DrawingPins = { ...dev, keys: [[2, own.publicKey], ...dev.keys] };
    const asked: string[] = [];
    expect(await verdict({ [PATH]: REAL_RECORD }, both, asked)).toBe("drawn");
    expect(asked).toEqual([at(2), PATH]);
    // Something at version 2 that does not verify refuses, though version 1 holds a good record.
    expect(await verdict({ [at(2)]: Buffer.from("{}"), [PATH]: REAL_RECORD }, both)).toBe(
      "invalid",
    );
  });

  it("fails on any Storage error but not-found", async () => {
    const outage = drawingVerdict(
      { pins: dev, read: () => Promise.reject(new Error("Storage is down")) },
      submission,
      source,
      output,
    );
    await expect(outage).rejects.toThrow("Storage is down");
  });
});

describe("the pins", () => {
  let root: string;
  beforeAll(() => {
    root = mkdtempSync(path.join(tmpdir(), "word-drawing-pins-"));
  });
  afterAll(() => {
    rmSync(root, { recursive: true, force: true });
  });

  it("are the lock's version and image and the environment's keys, less those revoked", () => {
    const pem = readFileSync("src/render/word-drawing/keys/dev/1.pem");
    writeFileSync(path.join(root, "lock.json"), readFileSync("src/render/word-drawing/lock.json"));
    mkdirSync(path.join(root, "keys", "dev"), { recursive: true });
    for (const name of ["1.pem", "2.pem", "3.pem", "10.pem", "02.pem", "x.pem", "README"]) {
      writeFileSync(path.join(root, "keys", "dev", name), pem);
    }
    writeFileSync(path.join(root, "keys", "dev", "revoked.json"), "[3]");
    const pins = drawingPins("dev", root);
    expect(pins.keys.map(([version]) => version)).toEqual([10, 2, 1]);
    expect([pins.environment, pins.version, pins.imageDigest]).toEqual([
      "dev",
      "word-drawing/1.2.7",
      dev.imageDigest,
    ]);
    // Nothing pinned for validation.
    expect(drawingPins("validation", root)).toMatchObject({ imageDigest: null, keys: [] });
    // A revoked list that is not a list of versions, or a key that is not RSA 3072, refuses.
    writeFileSync(path.join(root, "keys", "dev", "revoked.json"), '["3"]');
    expect(() => drawingPins("dev", root)).toThrow();
    writeFileSync(path.join(root, "keys", "dev", "revoked.json"), "[]");
    const small = generateKeyPairSync("rsa", { modulusLength: 2048 }).publicKey;
    writeFileSync(
      path.join(root, "keys", "dev", "4.pem"),
      small.export({ type: "spki", format: "pem" }),
    );
    expect(() => drawingPins("dev", root)).toThrow("3072-bit RSA key");
  });
});
