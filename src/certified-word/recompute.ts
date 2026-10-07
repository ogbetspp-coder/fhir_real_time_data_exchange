import { spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type { AppConfig } from "../config.js";
import type { CertifiedWordSourceDocument, RecomputeRequest } from "../contracts/index.js";
import { readAuthorityJson } from "../authority/json.js";
import { sha256Bytes } from "../authority/import.js";
import { storageFetcher, type GcsObjectFetcher } from "../gcp/submission-reader.js";
import { drawingPins, MAX_RECORD_BYTES, type DrawingSource } from "./drawing.js";
import { RecomputeRefusalSchema } from "./shape.js";

// What the certified Word gate reads and runs (docs/design/certified-word-import.md, D4 and D2):
// the uploaded .docx, from the one place the producer may put it, and `python -m zone_a.recompute`
// on its bytes, in a subprocess; and where it reads the drawing record (D3, ./drawing.ts).

// D4: the producer stores the .docx once, content-addressed, in the submissions bucket:
// `gs://<SUBMISSION_BUCKET>/uploads/sha256/<its SHA-256>.docx`. Nothing else is read.
const UPLOAD_OBJECT = /^uploads\/sha256\/([0-9a-f]{64})\.docx$/;
// The largest .docx the gate reads. The EMA's published Word labels are well under it.
export const MAX_UPLOAD_BYTES = 32 * 1024 * 1024;

// D2: the recompute's limits. Measured on an M2 laptop, 2026-10-07: the slowest EMA Word SmPC that
// builds (2,280 paragraphs, 90,422 runs, two tracked changes, read by its accepted view) took 31.6 s
// and peaked at 796 MB resident; the largest label at hand (9,795 paragraphs) took 4.1 s. A Cloud
// Run vCPU is slower than a laptop core, so the limit leaves room: a label slower still is
// refused (`timeout`), never read in part. The worker has 2 GiB and runs one recompute at a time
// (`exclusive`), so two such reads never share its memory.
export const RECOMPUTE_TIMEOUT_MS = 300_000;
export const MAX_RECOMPUTE_OUTPUT_BYTES = 32 * 1024 * 1024;

// Why the upload was not read, as a closed code; never a byte of it.
export type UploadRefusal =
  "uri-not-allowed" | "too-large" | "not-found" | "length-differs" | "hash-differs";

export class UploadRefusedError extends Error {
  public constructor(public readonly reason: UploadRefusal) {
    super(`Upload refused: ${reason}`);
    this.name = "UploadRefusedError";
  }
}

// Why the recompute gave no answer, as a closed code: it could not start, ran out of time, wrote
// too much, ended otherwise than with its result (0) or its refusal (1), or wrote no refusal with 1.
export type RecomputeFailure =
  "unavailable" | "timeout" | "output-too-large" | "exit-status" | "not-a-refusal";

export class RecomputeFailedError extends Error {
  public constructor(public readonly reason: RecomputeFailure) {
    super(`Recompute failed: ${reason}`);
    this.name = "RecomputeFailedError";
  }
}

// The recompute's answer: the bytes it wrote with status 0, for the importer to read, or the code
// of its refusal (status 1). A code is the recompute's own, not the document's text.
export type RecomputeOutcome = { made: Uint8Array } | { refused: string };

export type CertifiedWordSources = {
  upload: (document: CertifiedWordSourceDocument["document"]) => Promise<Uint8Array>;
  recompute: (docx: Uint8Array, request: RecomputeRequest) => Promise<RecomputeOutcome>;
  // Absent, the gate finds no record (`certified-word-drawing-missing`).
  drawing?: DrawingSource;
};

// The object a source's storage URI names, if it is the upload of these bytes in this bucket.
export function uploadObject(
  storageUri: string,
  bucket: string,
  sha256: string,
): string | undefined {
  const prefix = `gs://${bucket}/`;
  if (!storageUri.startsWith(prefix)) return undefined;
  const object = storageUri.slice(prefix.length);
  return UPLOAD_OBJECT.exec(object)?.[1] === sha256 ? object : undefined;
}

// The uploaded .docx, read under the worker's own identity: only its content-addressed object in
// the submissions bucket, at most its declared length and one byte more, and only the bytes the
// source pins, by length and SHA-256.
export async function readUpload(
  document: CertifiedWordSourceDocument["document"],
  bucket: string,
  fetchObject: GcsObjectFetcher,
): Promise<Uint8Array> {
  const object = uploadObject(document.storageUri, bucket, document.sha256);
  if (object === undefined) throw new UploadRefusedError("uri-not-allowed");
  if (document.byteLength > MAX_UPLOAD_BYTES) throw new UploadRefusedError("too-large");
  const bytes = await fetchObject(bucket, object, document.byteLength);
  if (bytes === undefined) throw new UploadRefusedError("not-found");
  if (bytes.length !== document.byteLength) throw new UploadRefusedError("length-differs");
  if (sha256Bytes(bytes) !== document.sha256) throw new UploadRefusedError("hash-differs");
  return bytes;
}

// A recompute code as the issue may carry it: the recompute's own token, or nothing of what it wrote.
const CODE = /^[a-z][a-z0-9-]{0,63}$/;

type Exited = { status: number | null; stdout: Buffer };

// One run of the recompute: the request on its standard input, its standard output collected up to
// the cap, its standard error discarded (a traceback may quote the label), killed at the timeout.
function run(
  python: string,
  args: string[],
  input: string,
  options: { cwd: string; env: NodeJS.ProcessEnv; timeoutMs: number; maxOutputBytes: number },
): Promise<Exited> {
  return new Promise((resolve, reject) => {
    const child = spawn(python, args, {
      cwd: options.cwd,
      env: options.env,
      stdio: ["pipe", "pipe", "ignore"],
    });
    const chunks: Buffer[] = [];
    let length = 0;
    let failure: RecomputeFailure | undefined;
    let settled = false;
    const settle = (outcome: () => void): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      outcome();
    };
    const stop = (reason: RecomputeFailure): void => {
      failure ??= reason;
      child.kill("SIGKILL");
    };
    const timer = setTimeout(() => stop("timeout"), options.timeoutMs);
    child.stdout.on("data", (chunk: Buffer) => {
      length += chunk.length;
      if (length > options.maxOutputBytes) stop("output-too-large");
      else chunks.push(chunk);
    });
    // A child that ends before it reads its input closes the pipe; its status says what happened.
    child.stdin.on("error", () => undefined);
    child.on("error", () => settle(() => reject(new RecomputeFailedError("unavailable"))));
    child.on("close", (status) =>
      settle(() =>
        failure === undefined
          ? resolve({ status, stdout: Buffer.concat(chunks) })
          : reject(new RecomputeFailedError(failure)),
      ),
    );
    child.stdin.end(input);
  });
}

// `python -I -X utf8 -m zone_a.recompute LABEL.docx < REQUEST.json` (D2): Python's isolated mode
// (no PYTHON* variable, no user site, no script or working directory on the path), UTF-8 whatever
// the locale, in the root as working directory, with an environment of ZONE_A_ROOT alone, so no
// variable that names a credential, a proxy or a path reaches it. The label is written to a
// directory of its own, removed after. `timeoutMs` and `maxOutputBytes` are for tests.
export function pythonRecompute(options: {
  python: string;
  root: string;
  timeoutMs?: number;
  maxOutputBytes?: number;
}): CertifiedWordSources["recompute"] {
  return (docx, request) =>
    exclusive(async () => {
      const folder = await mkdtemp(join(tmpdir(), "certified-word-"));
      try {
        const label = join(folder, "label.docx");
        await writeFile(label, docx, { mode: 0o600 });
        const { status, stdout } = await run(
          options.python,
          ["-I", "-X", "utf8", "-m", "zone_a.recompute", label],
          JSON.stringify(request),
          {
            cwd: options.root,
            env: { ZONE_A_ROOT: options.root },
            timeoutMs: options.timeoutMs ?? RECOMPUTE_TIMEOUT_MS,
            maxOutputBytes: options.maxOutputBytes ?? MAX_RECOMPUTE_OUTPUT_BYTES,
          },
        );
        if (status === 0) return { made: stdout };
        if (status !== 1) throw new RecomputeFailedError("exit-status");
        let refusal: unknown;
        try {
          refusal = readAuthorityJson(stdout);
        } catch {
          throw new RecomputeFailedError("not-a-refusal");
        }
        const parsed = RecomputeRefusalSchema.safeParse(refusal);
        if (!parsed.success) throw new RecomputeFailedError("not-a-refusal");
        const { code } = parsed.data.refusal;
        return { refused: CODE.test(code) ? code : "unnamed" };
      } finally {
        await rm(folder, { recursive: true, force: true });
      }
    });
}

// One recompute at a time in this process, in the order asked: a label's read can take most of a
// gigabyte (above), and the worker takes four requests at once. A failed recompute does not stop
// the next.
let running: Promise<unknown> = Promise.resolve();
export function exclusive<T>(task: () => Promise<T>): Promise<T> {
  const next = running.then(task, task);
  running = next.catch(() => undefined);
  return next;
}

// The worker's own: the submissions bucket under its identity, and the Python its image installs
// (Dockerfile, the worker target, sets RECOMPUTE_PYTHON and ZONE_A_ROOT). Without either, none:
// the gate then cannot recompute, and refuses a certified Word submission that is not a dry run.
// With the record bucket and the environment (infra/run.tf sets both), the drawing records, read
// under the worker's identity, and the image and keys this build pins for that environment.
// `fetchObject` is for tests.
export function certifiedWordSources(
  config: Pick<
    AppConfig,
    | "SUBMISSION_BUCKET"
    | "GOOGLE_CLOUD_PROJECT"
    | "RECOMPUTE_PYTHON"
    | "ZONE_A_ROOT"
    | "WORD_DRAWING_BUCKET"
    | "WORD_DRAWING_ENVIRONMENT"
  >,
  fetcher?: GcsObjectFetcher,
): CertifiedWordSources | undefined {
  const { SUBMISSION_BUCKET: bucket, GOOGLE_CLOUD_PROJECT: project } = config;
  const { RECOMPUTE_PYTHON: python, ZONE_A_ROOT: root } = config;
  const { WORD_DRAWING_BUCKET: records, WORD_DRAWING_ENVIRONMENT: environment } = config;
  if (bucket === undefined || project === undefined || python === undefined || root === undefined) {
    return undefined;
  }
  const fetchObject = fetcher ?? storageFetcher(project);
  return {
    upload: (document) => readUpload(document, bucket, fetchObject),
    recompute: pythonRecompute({ python, root }),
    ...(records === undefined || environment === undefined
      ? {}
      : {
          drawing: {
            pins: drawingPins(environment),
            read: (object) => fetchObject(records, object, MAX_RECORD_BYTES),
          },
        }),
  };
}
