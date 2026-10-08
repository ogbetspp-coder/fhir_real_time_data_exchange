import { constants, generateKeyPairSync, sign } from "node:crypto";
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { loadConfig } from "../../src/config.js";
import { sha256Bytes } from "../../src/authority/import.js";
import {
  drawingId,
  drawingPins,
  recordKey,
  type DrawingPins,
  type DrawingSource,
} from "../../src/certified-word/drawing.js";
import {
  CERTIFIED_WORD_DOCUMENT_UNBOUND,
  CERTIFIED_WORD_DRAWING_INVALID,
  CERTIFIED_WORD_DRAWING_MISMATCH,
  CERTIFIED_WORD_DRAWING_MISSING,
  verifyCertifiedWordImport,
} from "../../src/certified-word/gate.js";
import { importCertifiedWord } from "../../src/certified-word/import.js";
import {
  exclusive,
  MAX_UPLOAD_BYTES,
  RecomputeFailedError,
  UploadRefusedError,
  certifiedWordSources,
  pythonRecompute,
  readUpload,
  uploadObject,
  type CertifiedWordSources,
  type RecomputeOutcome,
} from "../../src/certified-word/recompute.js";
import {
  RecomputeRefusalSchema,
  type CertifiedWordRequest,
} from "../../src/certified-word/shape.js";
import {
  RECOMPUTED,
  RUN,
  caseRequest,
  recomputed,
  recomputedCases,
} from "../../src/certified-word/vectors.js";
import {
  SubmissionRejectedError,
  approvedContent,
  type CanonicalSubmission,
  type DocumentSubmissionInput,
} from "../../src/contracts/index.js";
import { loadEmaMapping, loadEmaMappings, type EmaMapping } from "../../src/fhir/mapping.js";
import type { FhirComposition } from "../../src/fhir/types.js";
import type { GcsObjectFetcher } from "../../src/gcp/submission-reader.js";
import { canonicalJson, sha256 } from "../../src/lib/hash.js";
import { runPipeline } from "../../src/pipeline.js";

// The certified Word gate, recomputing (docs/design/certified-word-import.md, "The gate"): the
// upload read under the worker's identity (D4), `python -m zone_a.recompute` in a subprocess (D2),
// and the importer run again on its result, the submission held to what it makes.
//
// Where RECOMPUTE_PYTHON names a Python with zone-a installed (CI's Zone A job), the gate runs the
// real recompute on the committed synthetic labels; elsewhere (CI's Check job, which has no
// Python) it runs a stand-in that answers, for a committed label and its committed request, what
// the recompute wrote for it, committed byte for byte and held to this build's Python by
// zone-a/tests/test_certified_word_fixtures.py, and refuses anything else.

const PYTHON = process.env.RECOMPUTE_PYTHON;
const BUCKET = "synthetic-submissions";
const OPTIONS = { allowSyntheticSources: true, dryRun: true };

let mapping: EmaMapping;
beforeAll(async () => {
  mapping = await loadEmaMapping();
});

const label = (name: string): Buffer => readFileSync(`${RECOMPUTED}/${name}.docx`);
const uri = (bytes: Uint8Array): string =>
  `gs://${BUCKET}/uploads/sha256/${sha256Bytes(bytes)}.docx`;

// The bucket: every committed label at its content address, served as Cloud Storage serves a
// ranged read of at most `maxBytes + 1` bytes; a label's name in `objects` puts other bytes there.
function bucket(objects = new Map<string, Uint8Array>()): GcsObjectFetcher {
  for (const { name } of recomputedCases()) {
    const bytes = label(name);
    objects.set(`uploads/sha256/${sha256Bytes(bytes)}.docx`, objects.get(name) ?? bytes);
  }
  return (at, object, maxBytes) => {
    const bytes = at === BUCKET ? objects.get(object) : undefined;
    return Promise.resolve(
      bytes === undefined ? undefined : Buffer.from(bytes.subarray(0, maxBytes + 1)),
    );
  };
}

// What the recompute answers for a committed label and its committed request.
const committed: CertifiedWordSources["recompute"] = (docx, request) => {
  const found = recomputedCases().find(
    ({ name, request: asked }) =>
      sha256Bytes(label(name)) === sha256Bytes(docx) && sha256(asked) === sha256(request),
  );
  if (found === undefined) return Promise.resolve({ refused: "not-committed" });
  const bytes = recomputed(found.name);
  const refusal = RecomputeRefusalSchema.safeParse(JSON.parse(new TextDecoder().decode(bytes)));
  return Promise.resolve(
    refusal.success ? { refused: refusal.data.refusal.code } : { made: bytes },
  );
};

const recompute =
  PYTHON === undefined ? committed : pythonRecompute({ python: PYTHON, root: process.cwd() });

function sources(change: Partial<CertifiedWordSources> = {}): CertifiedWordSources {
  const fetchObject = bucket();
  return {
    upload: (document) => readUpload(document, BUCKET, fetchObject),
    recompute,
    ...change,
  };
}

// A committed label's submission as the producer makes it: the upload at its content address.
function uploaded(
  name = "smpc",
  change: (request: CertifiedWordRequest) => CertifiedWordRequest = (request) => request,
  result: Uint8Array = recomputed(name),
): DocumentSubmissionInput & { submission: CanonicalSubmission } {
  const found = recomputedCases().find((candidate) => candidate.name === name);
  if (found === undefined) throw new Error(`no case ${name}`);
  const request = {
    ...caseRequest(found),
    upload: { filename: `${name}.docx`, storageUri: uri(label(name)) },
  };
  return importCertifiedWord(result, change(request), mapping, RUN);
}

function gate(
  input: DocumentSubmissionInput,
  options = OPTIONS,
  from: CertifiedWordSources = sources(),
): ReturnType<typeof verifyCertifiedWordImport> {
  return verifyCertifiedWordImport(input, mapping, options, from);
}

async function rejection(promise: Promise<unknown>): Promise<SubmissionRejectedError> {
  const error: unknown = await promise.then(
    () => undefined,
    (cause: unknown) => cause,
  );
  if (error instanceof SubmissionRejectedError) return error;
  throw new Error("not refused", { cause: error });
}

// The submission changed and its approved content sealed again, so only the recompute refuses it.
function resealed(submission: CanonicalSubmission): CanonicalSubmission {
  return {
    ...submission,
    approval: {
      ...submission.approval,
      approvedContentSha256: sha256(approvedContent(submission)),
    },
  };
}

describe("the upload (D4)", () => {
  const sha = "a".repeat(64);

  it("is read only from its content address in the submissions bucket", () => {
    const at = `uploads/sha256/${sha}.docx`;
    expect(uploadObject(`gs://${BUCKET}/${at}`, BUCKET, sha)).toBe(at);
    for (const other of [
      `gs://other-bucket/${at}`,
      `gs://${BUCKET}x/${at}`,
      `gs://${BUCKET}/uploads/sha256/${"b".repeat(64)}.docx`,
      `gs://${BUCKET}/uploads/sha256/${sha.toUpperCase()}.docx`,
      `gs://${BUCKET}/uploads/sha256/${sha}.docx.json`,
      `gs://${BUCKET}/uploads/sha256/${sha}`,
      `gs://${BUCKET}/uploads/${sha}.docx`,
      `gs://${BUCKET}/x/uploads/sha256/${sha}.docx`,
      `gs://${BUCKET}/uploads/sha256/../sha256/${sha}.docx`,
      `gs://${BUCKET}/demo/${sha}.submission.json`,
      `https://storage.googleapis.com/${BUCKET}/${at}`,
    ]) {
      expect([other, uploadObject(other, BUCKET, sha)]).toEqual([other, undefined]);
    }
  });

  it("reads at most its length and one byte more, and only the pinned bytes", async () => {
    const bytes = label("smpc");
    const document = {
      sha256: sha256Bytes(bytes),
      byteLength: bytes.length,
      filename: "smpc.docx",
      storageUri: uri(bytes),
    };
    const asked: [string, string, number][] = [];
    const fetchObject: GcsObjectFetcher = (at, object, maxBytes) => {
      asked.push([at, object, maxBytes]);
      return bucket()(at, object, maxBytes);
    };
    expect(sha256Bytes(await readUpload(document, BUCKET, fetchObject))).toBe(document.sha256);
    expect(asked).toEqual([[BUCKET, `uploads/sha256/${document.sha256}.docx`, bytes.length]]);

    const refused = async (
      change: Partial<typeof document>,
      fetcher: GcsObjectFetcher = bucket(),
    ): Promise<string> => {
      const error: unknown = await readUpload({ ...document, ...change }, BUCKET, fetcher).then(
        () => undefined,
        (cause: unknown) => cause,
      );
      if (!(error instanceof UploadRefusedError)) throw new Error("not refused");
      return error.reason;
    };
    expect(await refused({ storageUri: `gs://other/uploads/sha256/${document.sha256}.docx` })).toBe(
      "uri-not-allowed",
    );
    // A byte flipped, the object one byte longer or shorter, or not there.
    const flipped = Buffer.from(bytes);
    flipped[100] = (flipped[100] ?? 0) ^ 1;
    const serving =
      (object: Uint8Array): GcsObjectFetcher =>
      (_at, _object, maxBytes) =>
        Promise.resolve(Buffer.from(object.subarray(0, maxBytes + 1)));
    expect(await refused({}, serving(flipped))).toBe("hash-differs");
    expect(await refused({}, serving(Buffer.concat([bytes, Buffer.from([0])])))).toBe(
      "length-differs",
    );
    expect(await refused({}, serving(bytes.subarray(1)))).toBe("length-differs");
    expect(await refused({}, () => Promise.resolve(undefined))).toBe("not-found");
    // Larger than the cap: refused before anything is read.
    const huge = await refused({ byteLength: MAX_UPLOAD_BYTES + 1 }, () => {
      throw new Error("read");
    });
    expect(huge).toBe("too-large");
  });

  it("is the worker's own only with a bucket, a project and a Python", async () => {
    const config = {
      SUBMISSION_BUCKET: BUCKET,
      GOOGLE_CLOUD_PROJECT: "p",
      RECOMPUTE_PYTHON: "/opt/zone-a/bin/python",
      ZONE_A_ROOT: "/app",
    };
    const own = certifiedWordSources(config);
    if (own === undefined) throw new Error("no sources");
    // Its upload is held to the bucket it is given before anything is read.
    await expect(
      own.upload({
        sha256: "a".repeat(64),
        byteLength: 1,
        filename: "x.docx",
        storageUri: `gs://other/uploads/sha256/${"a".repeat(64)}.docx`,
      }),
    ).rejects.toThrow(UploadRefusedError);
    for (const key of Object.keys(config) as (keyof typeof config)[]) {
      expect([key, certifiedWordSources({ ...config, [key]: undefined })]).toEqual([
        key,
        undefined,
      ]);
    }
    // The drawing records (D3), only with the record bucket and the environment, by its pins.
    expect(own.drawing).toBeUndefined();
    const drawing = { WORD_DRAWING_BUCKET: "records", WORD_DRAWING_ENVIRONMENT: "dev" } as const;
    const asked: [string, string, number][] = [];
    const records = certifiedWordSources({ ...config, ...drawing }, (at, object, maxBytes) => {
      asked.push([at, object, maxBytes]);
      return Promise.resolve(undefined);
    })?.drawing;
    expect([
      records?.pins.environment,
      records?.pins.imageDigest,
      records?.pins.keys.map(([version]) => version),
    ]).toEqual(["dev", drawingPins("dev").imageDigest, [1]]);
    // A record is read from the record bucket, at most 64 KiB and one byte more.
    expect(await records?.read("word/x.json")).toBeUndefined();
    expect(asked).toEqual([["records", "word/x.json", 64 * 1024]]);
    for (const key of Object.keys(drawing) as (keyof typeof drawing)[]) {
      const without = certifiedWordSources({ ...config, ...drawing, [key]: undefined });
      expect([key, without?.drawing]).toEqual([key, undefined]);
    }
    expect(() => loadConfig({ WORD_DRAWING_ENVIRONMENT: "staging" })).toThrow();
    // Each an absolute path: the subprocess has no PATH, and its working directory is the root.
    for (const relative of [
      { RECOMPUTE_PYTHON: "python3" },
      { RECOMPUTE_PYTHON: "zone-a/.venv/bin/python" },
      { ZONE_A_ROOT: "." },
      { ZONE_A_ROOT: "" },
    ]) {
      expect(() => loadConfig(relative), JSON.stringify(relative)).toThrow(
        "must be an absolute path",
      );
    }
    const loaded = loadConfig({ RECOMPUTE_PYTHON: "/opt/zone-a/bin/python", ZONE_A_ROOT: "/app" });
    expect([loaded.RECOMPUTE_PYTHON, loaded.ZONE_A_ROOT]).toEqual([
      "/opt/zone-a/bin/python",
      "/app",
    ]);
  });
});

describe("the recompute's subprocess (D2)", () => {
  let folder: string;
  // A stand-in for Python: a Node script under its own shebang, given the very arguments, input,
  // environment and working directory the gate gives Python.
  const fake = (name: string, body: string): string => {
    const file = join(folder, name);
    writeFileSync(file, `#!${process.execPath}\n${body}\n`, { mode: 0o755 });
    return file;
  };
  const request = recomputedCases()[0]?.request;
  const answer = async (
    python: string,
    limits: { timeoutMs?: number; maxOutputBytes?: number } = {},
    asked = request,
  ): Promise<RecomputeOutcome | string> => {
    if (asked === undefined) throw new Error("no cases");
    const run = pythonRecompute({ python, root: folder, ...limits });
    return run(label("smpc"), asked).catch((error: unknown) => {
      if (error instanceof RecomputeFailedError) return error.reason;
      throw error;
    });
  };

  beforeAll(() => {
    // Its real path (macOS's temporary directory is under a symbolic link), as a process's cwd is.
    folder = realpathSync(mkdtempSync(join(tmpdir(), "fake-python-")));
  });
  afterAll(() => {
    rmSync(folder, { recursive: true, force: true });
  });

  it("runs isolated, with the request on its input, the label in a file it removes after", async () => {
    const python = fake(
      "observes",
      `const { readFileSync } = require("node:fs");
       const { createHash } = require("node:crypto");
       const args = process.argv.slice(2);
       const label = args[args.length - 1];
       process.stdout.write(JSON.stringify({
         args: args.slice(0, -1),
         env: process.env,
         cwd: process.cwd(),
         label,
         labelSha256: createHash("sha256").update(readFileSync(label)).digest("hex"),
         input: readFileSync(0, "utf8"),
       }));`,
    );
    const outcome = await answer(python);
    if (typeof outcome === "string" || !("made" in outcome)) throw new Error("no result");
    const seen = JSON.parse(new TextDecoder().decode(outcome.made)) as Record<string, unknown>;
    expect(seen.args).toEqual(["-I", "-X", "utf8", "-m", "zone_a.recompute"]);
    // Nothing of the worker's environment: no credential, no project, no proxy, no PATH.
    // (macOS's CoreFoundation sets __CF_USER_TEXT_ENCODING in every process it starts.)
    const env = Object.keys(seen.env as object).filter((key) => key !== "__CF_USER_TEXT_ENCODING");
    expect(env).toEqual(["ZONE_A_ROOT"]);
    expect((seen.env as { ZONE_A_ROOT?: string }).ZONE_A_ROOT).toBe(folder);
    expect(seen.cwd).toBe(folder);
    expect(seen.labelSha256).toBe(sha256Bytes(label("smpc")));
    expect(JSON.parse(seen.input as string)).toEqual(request);
    expect(existsSync(seen.label as string)).toBe(false);
  });

  it("runs one recompute at a time, in the order asked, a failure not stopping the next", async () => {
    // Each run writes when it started and ended; overlapping runs would interleave.
    const log = join(folder, "order.log");
    writeFileSync(log, "");
    const python = fake(
      "takes-turns",
      `const { appendFileSync } = require("node:fs");
       appendFileSync(${JSON.stringify(log)}, "start\\n");
       setTimeout(() => { appendFileSync(${JSON.stringify(log)}, "end\\n"); process.exit(3); }, 150);`,
    );
    const outcomes = await Promise.all([answer(python), answer(python), answer(python)]);
    expect(outcomes).toEqual(["exit-status", "exit-status", "exit-status"]);
    expect(readFileSync(log, "utf8").trim().split("\n")).toEqual([
      "start",
      "end",
      "start",
      "end",
      "start",
      "end",
    ]);
    const order: number[] = [];
    await Promise.all([
      exclusive(async () => {
        await new Promise((done) => setTimeout(done, 30));
        order.push(1);
      }),
      exclusive(() => Promise.reject(new Error("no"))).catch(() => order.push(2)),
      exclusive(() => {
        order.push(3);
        return Promise.resolve();
      }),
    ]);
    expect(order).toEqual([1, 2, 3]);
  });

  it("answers a refusal by its code, and only a token of it", async () => {
    const refusal = (code: string): string =>
      `process.stdout.write(${JSON.stringify(`${JSON.stringify({ refusal: { code, detail: "SECRET" } })}\n`)}); process.exit(1);`;
    expect(await answer(fake("refuses", refusal("versions")))).toEqual({ refused: "versions" });
    expect(await answer(fake("refuses-oddly", refusal("Not A Code: SECRET")))).toEqual({
      refused: "unnamed",
    });
  });

  it("refuses every other ending with a closed reason", async () => {
    expect(await answer(join(folder, "missing")), "no Python there").toBe("unavailable");
    expect(await answer(fake("sleeps", "setInterval(() => {}, 1000);"), { timeoutMs: 200 })).toBe(
      "timeout",
    );
    expect(
      await answer(fake("floods", 'process.stdout.write("x".repeat(5000));'), {
        maxOutputBytes: 4096,
      }),
    ).toBe("output-too-large");
    expect(await answer(fake("exits", "process.exit(3);")), "status 3").toBe("exit-status");
    // Gone before it read an input larger than a pipe holds: the broken pipe is no crash here.
    if (request === undefined) throw new Error("no cases");
    const assignments = Object.fromEntries(
      Array.from({ length: 20_000 }, (_, at) => [`smpc.${String(at)}`, at]),
    );
    expect(
      await answer(fake("exits-unread", "process.exit(3);"), {}, { ...request, assignments }),
    ).toBe("exit-status");
    expect(await answer(fake("killed", 'process.kill(process.pid, "SIGKILL");'))).toBe(
      "exit-status",
    );
    // Status 1 without a refusal: a traceback, a partial line, a refusal with more in it.
    expect(
      await answer(fake("crashes", 'process.stdout.write("Traceback"); process.exit(1);')),
    ).toBe("not-a-refusal");
    expect(
      await answer(
        fake(
          "refuses-loosely",
          'process.stdout.write(\'{"refusal":{"code":"x","detail":"","more":1}}\'); process.exit(1);',
        ),
      ),
    ).toBe("not-a-refusal");
    // Status 0 is the result whatever it holds; the importer reads it (below: malformed output).
    expect(await answer(fake("writes", 'process.stdout.write("not JSON");'))).toEqual({
      made: Buffer.from("not JSON"),
    });
  });
});

describe(`the gate, recomputing (${PYTHON === undefined ? "the committed results" : "Python"})`, () => {
  it.each(["smpc", "smpc-tracked", "smpc-assigned"])(
    "passes a dry run of %s, made again from its upload",
    async (name) => {
      const input = uploaded(name);
      const passed = await gate(input);
      expect(passed.gate.submission).toEqual(input.submission);
      // The answer says the dry run made the sections again and compared them.
      expect(passed.check).toBe("recomputed");
    },
  );

  it("passes one whose approval names its record, which the request is made again with", async () => {
    const input = uploaded("smpc", (request) => ({
      ...request,
      approval: { ...request.approval, recordRef: "urn:record:synthetic-01" },
    }));
    expect((await gate(input)).gate.submission).toEqual(input.submission);
  });

  it("refuses one that is not a dry run, after the recompute, for want of a drawing", async () => {
    const error = await rejection(gate(uploaded(), { ...OPTIONS, dryRun: false }));
    expect(error.issues).toEqual([CERTIFIED_WORD_DRAWING_MISSING]);
    expect(error.reason).toBe("certified-word-drawing-missing");
    // The order: a recompute that fails refuses first.
    const failing = sources({
      recompute: () => Promise.reject(new RecomputeFailedError("timeout")),
    });
    expect(
      (await rejection(gate(uploaded(), { ...OPTIONS, dryRun: false }, failing))).issues,
    ).toEqual(["The recompute gave no answer: timeout"]);
  });

  it("refuses with the recompute's refusal, by its closed code", async () => {
    // A section of the label the recompute refuses (two tabs in 4.2), with the label's own request.
    const found = recomputedCases().find(({ name }) => name === "smpc-refused");
    if (found === undefined) throw new Error("no refused case");
    const input = uploaded("smpc");
    const source = input.submission.provenance.sourceDocument;
    if (source.kind !== "certified-word") throw new Error("not a certified Word source");
    const bytes = label("smpc-refused");
    const other = resealed({
      ...input.submission,
      provenance: {
        ...input.submission.provenance,
        sourceDocument: {
          ...source,
          document: {
            ...source.document,
            sha256: sha256Bytes(bytes),
            byteLength: bytes.length,
            storageUri: uri(bytes),
          },
        },
      },
    });
    const error = await rejection(gate({ ...input, submission: other }));
    expect(error.reason).toBe("certified-word-recompute-refused");
    expect(error.issues).toEqual(["The recompute refuses the uploaded document: section"]);
  });

  it("refuses a request naming versions this build does not have", async () => {
    const versions = sources({ recompute: () => Promise.resolve({ refused: "versions" }) });
    const error = await rejection(gate(uploaded(), OPTIONS, versions));
    expect([error.reason, error.issues]).toEqual([
      "certified-word-recompute-refused",
      ["The recompute refuses the uploaded document: versions"],
    ]);
    // A result naming other versions than the request: the importer refuses it.
    const result = JSON.parse(new TextDecoder().decode(recomputed("smpc"))) as {
      versions: { builder: string };
    };
    result.versions.builder = "word-epi/0.0.0";
    const other = sources({
      recompute: () => Promise.resolve({ made: new TextEncoder().encode(JSON.stringify(result)) }),
    });
    expect((await rejection(gate(uploaded(), OPTIONS, other))).issues).toEqual([
      "The importer refuses the recompute's result at binding: other-versions",
    ]);
  });

  it("refuses malformed or oversized output, and every other failure, with a closed reason", async () => {
    const writes = (made: string): CertifiedWordSources =>
      sources({ recompute: () => Promise.resolve({ made: new TextEncoder().encode(made) }) });
    expect((await rejection(gate(uploaded(), OPTIONS, writes("not JSON")))).issues).toEqual([
      "The importer refuses the recompute's result at bytes: invalid-json",
    ]);
    expect((await rejection(gate(uploaded(), OPTIONS, writes('{"sections":[]}')))).issues).toEqual([
      "The importer refuses the recompute's result at shape: result-shape",
    ]);
    for (const reason of [
      "unavailable",
      "output-too-large",
      "exit-status",
      "not-a-refusal",
    ] as const) {
      const failing = sources({
        recompute: () => Promise.reject(new RecomputeFailedError(reason)),
      });
      expect((await rejection(gate(uploaded(), OPTIONS, failing))).issues).toEqual([
        `The recompute gave no answer: ${reason}`,
      ]);
    }
    // Neither an infrastructure failure nor a bug is dressed up as a refusal.
    const outage = sources({ upload: () => Promise.reject(new Error("Storage is down")) });
    await expect(gate(uploaded(), OPTIONS, outage)).rejects.toThrow("Storage is down");
    const bug = sources({ recompute: () => Promise.reject(new TypeError("a bug")) });
    await expect(gate(uploaded(), OPTIONS, bug)).rejects.toThrow(TypeError);
  });

  it("refuses a submission that does not name its report's location", async () => {
    const input = uploaded();
    const fidelity = { ...input.submission.provenance.fidelity, reportUri: undefined };
    const unnamed = resealed({
      ...input.submission,
      provenance: { ...input.submission.provenance, fidelity },
    });
    expect((await rejection(gate({ ...input, submission: unnamed }))).issues).toEqual([
      "A certified Word import names its report's location",
    ]);
  });

  it("refuses an upload that is not the one the source pins", async () => {
    const input = uploaded();
    const flipped = Buffer.from(label("smpc"));
    flipped[100] = (flipped[100] ?? 0) ^ 1;
    const fetchObject = bucket(new Map([["smpc", flipped]]));
    const tampered = sources({ upload: (document) => readUpload(document, BUCKET, fetchObject) });
    expect((await rejection(gate(input, OPTIONS, tampered))).issues).toEqual([
      "The uploaded document is refused: hash-differs",
    ]);
    // The submission the golden vectors pin names no content address: refused before any read.
    const found = recomputedCases()[0];
    if (found === undefined) throw new Error("no cases");
    const vector = importCertifiedWord(recomputed(found.name), caseRequest(found), mapping, RUN);
    expect((await rejection(gate(vector))).issues).toEqual([
      "The uploaded document is refused: uri-not-allowed",
    ]);
  });

  it("refuses a narrative, page or report that is not the label's", async () => {
    // Sections made from a result that is not the upload's: a narrative and its page changed
    // together, so the submission holds together and only the recompute shows it is not the label's.
    const text = new TextDecoder().decode(recomputed("smpc"));
    const changed = text.replace(
      '<p>Synthetic text, not for clinical use.</p></div>","page":"\\nSynthetic text, not for clinical use.\\n"',
      '<p>Synthetic text, not for clinical use, changed.</p></div>","page":"\\nSynthetic text, not for clinical use, changed.\\n"',
    );
    expect(changed).not.toBe(text);
    const forged = uploaded("smpc", undefined, new TextEncoder().encode(changed));
    // The ordinary gate, which cannot recompute, finds nothing wrong with it.
    expect(
      (await verifyCertifiedWordImport(forged, mapping, OPTIONS, undefined)).gate.submission,
    ).toEqual(forged.submission);
    expect((await rejection(gate(forged))).issues).toEqual([
      "The submission is not what the importer makes of the recomputed sections",
    ]);
    // The page text or the report sent with the very submission, changed.
    const input = uploaded();
    const pages = input.sourceText as { pages: { text: string }[] };
    const sourceText = {
      ...pages,
      pages: pages.pages.map((page, at) => (at === 1 ? { ...page, text: `${page.text} ` } : page)),
    };
    expect((await rejection(gate({ ...input, sourceText }))).issues).toEqual([
      "The page text is not what the importer makes of the recomputed sections",
    ]);
    const fidelityReport = { ...(input.fidelityReport as object), generatedBy: "other" };
    expect((await rejection(gate({ ...input, fidelityReport }))).issues).toEqual([
      "The fidelity report is not the recomputed one",
    ]);
  });

  it("refuses a product in the record that is not the one the label's sections were made for", async () => {
    const input = uploaded();
    const withProduct = (
      change: (resource: Record<string, unknown>) => void,
    ): CanonicalSubmission => {
      const bundle = structuredClone(input.submission.bundle);
      const product = bundle.entry.find(
        ({ resource }) => resource.resourceType === "MedicinalProductDefinition",
      )?.resource;
      if (product === undefined) throw new Error("no product");
      change(product);
      return resealed({ ...input.submission, bundle, bundleSha256: sha256(bundle) });
    };
    // Another name, which section 1 does not begin with; no canonical id at all.
    const renamed = withProduct((product) => (product.name = [{ productName: "Synthetic" }]));
    expect((await rejection(gate({ ...input, submission: renamed }))).issues).toEqual([
      "The importer refuses the recompute's result at product: name-not-in-section-1",
    ]);
    const unnamed = withProduct((product) => (product.identifier = [null]));
    expect((await rejection(gate({ ...input, submission: unnamed }))).issues).toEqual([
      "The importer refuses the recompute's result at request: request-shape",
    ]);
  });

  it("refuses assignments that are not the ones the label's sections were made with", async () => {
    const input = uploaded("smpc-assigned");
    const source = input.submission.provenance.sourceDocument;
    if (source.kind !== "certified-word") throw new Error("not a certified Word source");
    // The heading a person assigned left out, or another paragraph named.
    for (const assignments of [
      {},
      { "smpc.4.1": (source.recompute.assignments["smpc.4.1"] ?? 0) + 1 },
    ]) {
      const other = resealed({
        ...input.submission,
        provenance: {
          ...input.submission.provenance,
          sourceDocument: { ...source, recompute: { ...source.recompute, assignments } },
        },
      });
      // Python refuses the label under them, or makes other sections than these; either refuses.
      const error = await rejection(gate({ ...input, submission: other }));
      expect([assignments, error.issues.length]).toEqual([assignments, 1]);
    }
  });

  it("runs through the worker's pipeline dry, and is refused there otherwise", async () => {
    const config = loadConfig({
      ALLOW_SYNTHETIC_SOURCES: "true",
      NODE_ENV: "test",
      DRY_RUN: "true",
    });
    const run = (dryRun: boolean) =>
      runPipeline(
        {
          runId: "00000000-0000-4000-8000-00000000c0d1",
          sourceKind: "document",
          sourceResource: "document:certified-word",
          ...uploaded(),
        },
        mapping,
        { ...config, DRY_RUN: dryRun },
        { certifiedWord: sources() },
      );
    const dry = await run(true);
    expect([dry.status, dry.certifiedWordCheck]).toEqual(["validated", "recomputed"]);
    expect((await rejection(run(false))).reason).toBe("certified-word-drawing-missing");
  });

  // A package leaflet (docs/design/pl-structure.md, "Zone B"): the worker, given every mapping,
  // takes the leaflet's by the record's document type, and the gate makes it again from its upload.
  it("carries a package leaflet through the worker's pipeline dry, by the leaflet's mapping", async () => {
    const { smpc, pl: leaflet } = await loadEmaMappings();
    const found = recomputedCases().find(({ name }) => name === "pl");
    if (found === undefined) throw new Error("no leaflet");
    const input = importCertifiedWord(
      recomputed("pl"),
      { ...caseRequest(found), upload: { filename: "pl.docx", storageUri: uri(label("pl")) } },
      leaflet,
      RUN,
    );
    const config = loadConfig({
      ALLOW_SYNTHETIC_SOURCES: "true",
      NODE_ENV: "test",
      DRY_RUN: "true",
    });
    const result = await runPipeline(
      {
        runId: "00000000-0000-4000-8000-00000000c0d2",
        sourceKind: "document",
        sourceResource: "document:certified-word-leaflet",
        ...input,
      },
      [smpc, leaflet],
      config,
      { certifiedWord: sources() },
    );
    expect([result.status, result.certifiedWordCheck]).toEqual(["validated", "recomputed"]);
    const composition = result.emaBundle.entry[0]?.resource as FhirComposition;
    expect(composition.type.coding?.[0]?.code).toBe("100000155538");
    expect(composition.meta?.profile).toEqual(leaflet.profiles.composition);
    expect(composition.section[0]?.section?.[0]?.title).toBe(
      "1. What Synthetic Exampline is and what it is used for",
    );
    expect(result.evidence.manifest.standards.mappingVersion).toBe(leaflet.mappingVersion);
    expect(result.evidence.manifest.validation.profiles).toContain(
      "http://ema.europa.eu/fhir/StructureDefinition/EUQRD-CAP-template-new-Package-Leaflet-en",
    );
  });
});

// Step 5 (D3; docs/design/certified-word-drawing.md, section 3). The real record dev's drawing
// build signed for the committed synthetic SmPC (test/fixtures/certified-word/drawing/, which
// test/certified-word/drawing.test.ts verifies field by field against dev's key, and every way it
// is refused) is of the build before word-epi/1.4.0: this build's request has another path, and
// the gate finds it nowhere. Until dev draws the label again, the record bucket holds the record
// dev's build makes for this build: the real record's fields with this build's request, output and
// drawing version, signed by a key made for the test in version 1's place.
describe(`the gate's step 5, the drawing record (${PYTHON === undefined ? "the committed results" : "Python"})`, () => {
  const REAL_PATH =
    "word/7e4c389fdb72bf3b804e1240e043eba02352fd3a70ebe25283b06a53fcd92809/03d058be759c88620d0748c07ea6c747c468b7ad9b0e95f23476e07db67d4000/1.json";
  const REAL = readFileSync("test/fixtures/certified-word/drawing/smpc.record.json");
  const own = generateKeyPairSync("rsa", { modulusLength: 3072 });
  const pins: DrawingPins = { ...drawingPins("dev"), keys: [[1, own.publicKey]] };
  let PATH: string;
  let RECORD: Buffer;

  beforeAll(() => {
    const named = uploaded().submission.provenance.sourceDocument;
    if (named.kind !== "certified-word") throw new Error("not a certified Word source");
    const { record } = JSON.parse(REAL.toString("utf8")) as {
      record: { drawing: object } & Record<string, unknown>;
    };
    const made = {
      ...record,
      request: { docxSha256: named.document.sha256, recompute: named.recompute },
      recompute: { outputSha256: sha256Bytes(recomputed("smpc")) },
      drawing: { ...record.drawing, version: pins.version },
    };
    const signature = sign("sha256", Buffer.from(canonicalJson(made)), {
      key: own.privateKey,
      padding: constants.RSA_PKCS1_PSS_PADDING,
      saltLength: 32,
    });
    RECORD = Buffer.from(
      canonicalJson({ record: made, signatureBase64: signature.toString("base64") }),
    );
    PATH = `word/${recordKey(named)}/${drawingId(pins.version, pins.imageDigest ?? "")}/1.json`;
  });

  function records(
    objects?: Record<string, Uint8Array>,
    read: DrawingSource["read"] = (object) =>
      Promise.resolve((objects ?? { [PATH]: RECORD })[object]),
  ): CertifiedWordSources {
    return sources({ drawing: { pins, read } });
  }

  it("passes a dry run of the synthetic SmPC as drawn, by a record dev's build signs", async () => {
    const input = uploaded();
    const passed = await gate(input, OPTIONS, records());
    expect(passed.gate.submission).toEqual(input.submission);
    expect(passed.check).toBe("drawn");
  });

  it("finds the real record of the build before nowhere, with dev's own pins", async () => {
    const before = sources({
      drawing: {
        pins: drawingPins("dev"),
        read: (object) => Promise.resolve({ [REAL_PATH]: REAL }[object]),
      },
    });
    expect((await gate(uploaded(), OPTIONS, before)).check).toBe("recomputed");
  });

  it("refuses a run that is not dry: unbound once the record verifies, missing without one", async () => {
    const notDry = { ...OPTIONS, dryRun: false };
    const unbound = await rejection(gate(uploaded(), notDry, records()));
    expect([unbound.reason, unbound.issues]).toEqual([
      "certified-word-document-unbound",
      [CERTIFIED_WORD_DOCUMENT_UNBOUND],
    ]);
    const missing = await rejection(gate(uploaded(), notDry, records({})));
    expect([missing.reason, missing.issues]).toEqual([
      "certified-word-drawing-missing",
      [CERTIFIED_WORD_DRAWING_MISSING],
    ]);
    // Dry, with no record, the run answers as before the drawing was built.
    expect((await gate(uploaded(), OPTIONS, records({}))).check).toBe("recomputed");
  });

  it("refuses an invalid or mismatched record, dry or not, by its closed code", async () => {
    for (const dryRun of [true, false]) {
      const invalid = await rejection(
        gate(uploaded(), { ...OPTIONS, dryRun }, records({ [PATH]: Buffer.from("{}") })),
      );
      expect([invalid.reason, invalid.issues]).toEqual([
        "certified-word-drawing-invalid",
        [CERTIFIED_WORD_DRAWING_INVALID],
      ]);
      // The recompute writes the same value in other bytes, which the importer makes the same
      // submission of: the record names the bytes, so it is not this run's.
      const spaced = sources({
        ...records(),
        recompute: (docx, request) =>
          recompute(docx, request).then((outcome) =>
            "made" in outcome
              ? { made: Buffer.concat([Buffer.from(" "), Buffer.from(outcome.made)]) }
              : outcome,
          ),
      });
      const mismatch = await rejection(gate(uploaded(), { ...OPTIONS, dryRun }, spaced));
      expect([mismatch.reason, mismatch.issues]).toEqual([
        "certified-word-drawing-mismatch",
        [CERTIFIED_WORD_DRAWING_MISMATCH],
      ]);
    }
  });

  it("fails the run on a Storage error reading the record, never taking it for missing", async () => {
    const outage = records({}, () => Promise.reject(new Error("Storage is down")));
    for (const dryRun of [true, false]) {
      await expect(gate(uploaded(), { ...OPTIONS, dryRun }, outage)).rejects.toThrow(
        "Storage is down",
      );
    }
  });

  it("runs through the worker's pipeline dry as drawn, and is refused there otherwise", async () => {
    const config = loadConfig({
      ALLOW_SYNTHETIC_SOURCES: "true",
      NODE_ENV: "test",
      DRY_RUN: "true",
    });
    const run = (dryRun: boolean) =>
      runPipeline(
        {
          runId: "00000000-0000-4000-8000-00000000c0d3",
          sourceKind: "document",
          sourceResource: "document:certified-word",
          ...uploaded(),
        },
        mapping,
        { ...config, DRY_RUN: dryRun },
        { certifiedWord: records() },
      );
    const dry = await run(true);
    expect([dry.status, dry.certifiedWordCheck]).toEqual(["validated", "drawn"]);
    expect((await rejection(run(false))).reason).toBe("certified-word-document-unbound");
  });
});

describe.runIf(PYTHON !== undefined)("the real recompute, through the gate's runner", () => {
  it.each(recomputedCases().map(({ name }) => name))(
    "makes again, byte for byte, what was committed for %s",
    async (name) => {
      const found = recomputedCases().find((candidate) => candidate.name === name);
      if (found === undefined || PYTHON === undefined) throw new Error("no case");
      expect(await recompute(label(name), found.request)).toEqual(
        await committed(label(name), found.request),
      );
    },
  );

  it("refuses a request naming versions this build does not have", async () => {
    const found = recomputedCases()[0];
    if (found === undefined) throw new Error("no cases");
    const request = {
      ...found.request,
      versions: { ...found.request.versions, builder: "word-epi/0.0.0" },
    };
    expect(await recompute(label(found.name), request)).toEqual({ refused: "versions" });
  });
});
