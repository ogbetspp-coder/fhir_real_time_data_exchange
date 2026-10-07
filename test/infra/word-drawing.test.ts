import { spawnSync } from "node:child_process";
import { generateKeyPairSync } from "node:crypto";
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { HARDENING } from "../../scripts/render/run.mjs";
import { readInfra, serviceAccountRoles, terraformBlocks } from "../support/terraform.js";

// The Word drawing's infrastructure (docs/design/certified-word-drawing.md, PR 2): the drawing
// identity's grants, proven exhaustive as the signer's are; its key and its record bucket; and the
// trigger, whose source, configuration and request path are literals here, so that only main's
// code, run as written, can sign. Then the build's own script, run against stand-in commands: the
// record looked for, and a record signed, checked and written create-if-absent.

const terraform = readInfra();
const blocks = terraformBlocks(terraform);
const block = (type: string, name: string) =>
  blocks.find((candidate) => candidate.type === type && candidate.name === name)?.body ?? "";
const build = readFileSync("scripts/word-drawing/build.sh", "utf8");

describe("the drawing identity", () => {
  const roles = serviceAccountRoles(terraform, "word_drawing");

  it("holds exactly these roles", () => {
    expect(roles).toEqual(
      expect.arrayContaining([
        { type: "google_kms_crypto_key_iam_member", role: "roles/cloudkms.signer" },
        { type: "google_storage_bucket_iam_member", role: "roles/storage.objectCreator" },
        { type: "google_storage_bucket_iam_member", role: "roles/storage.objectViewer" },
        {
          type: "google_artifact_registry_repository_iam_member",
          role: "roles/artifactregistry.reader",
        },
        { type: "google_project_iam_member", role: "roles/logging.logWriter" },
      ]),
    );
    // Create once (records), read twice (records; uploads, conditioned), sign, pull, log.
    expect(roles).toHaveLength(6);
    expect(
      roles.some(({ role }) => /objectAdmin|admin|signerVerifier|healthcare|bigquery/i.test(role)),
    ).toBe(false);
  });

  it("signs with the drawing key alone, and nothing else may sign with it", () => {
    expect(block("google_kms_crypto_key_iam_member", "word_drawing_signer")).toMatch(
      /crypto_key_id\s*=\s*google_kms_crypto_key\.word_drawing_hsm\.id\s*\n\s*role\s*=\s*"roles\/cloudkms\.signer"/,
    );
    const onKey = blocks.filter(
      ({ type, body }) =>
        type === "google_kms_crypto_key_iam_member" &&
        body.includes("google_kms_crypto_key.word_drawing_hsm.id"),
    );
    expect(onKey.map(({ name }) => name)).toEqual(["word_drawing_signer"]);
  });

  it("reads the submissions bucket under uploads/sha256/ only", () => {
    const grant = block("google_storage_bucket_iam_member", "word_drawing_upload_reader");
    expect(grant).toMatch(/bucket\s*=\s*google_storage_bucket\.submissions\.name/);
    expect(grant).toMatch(
      /expression\s*=\s*"resource\.name\.startsWith\(\\"\$\{local\.submission_uploads\}\\"\)"/,
    );
    expect(terraform).toMatch(
      /submission_uploads = "projects\/_\/buckets\/\$\{google_storage_bucket\.submissions\.name\}\/objects\/uploads\/sha256\/"/,
    );
  });

  it("writes and reads the record bucket, which nobody else writes, and the worker reads", () => {
    const grants = blocks.filter(
      ({ type, body }) =>
        type === "google_storage_bucket_iam_member" &&
        body.includes("google_storage_bucket.word_drawings.name"),
    );
    expect(
      grants.map(({ body }) => [
        /role\s*=\s*"([^"]+)"/.exec(body)?.[1],
        /member\s*=\s*"serviceAccount:\$\{google_service_account\.(\w+)\.email\}"/.exec(body)?.[1],
      ]),
    ).toEqual([
      ["roles/storage.objectCreator", "word_drawing"],
      ["roles/storage.objectViewer", "word_drawing"],
      ["roles/storage.objectViewer", "worker"],
    ]);
  });
});

describe("the drawing key", () => {
  it("is an HSM RSA-PSS 3072 SHA-256 signing key in the evidence ring, never destroyed", () => {
    const key = block("google_kms_crypto_key", "word_drawing_hsm");
    expect(key).toMatch(/name\s*=\s*"word-drawing-hsm"/);
    expect(key).toMatch(/key_ring\s*=\s*google_kms_key_ring\.evidence\.id/);
    expect(key).toMatch(/purpose\s*=\s*"ASYMMETRIC_SIGN"/);
    expect(key).toMatch(/algorithm\s*=\s*"RSA_SIGN_PSS_3072_SHA256"/);
    expect(key).toMatch(/protection_level\s*=\s*"HSM"/);
    expect(key).toMatch(/destroy_scheduled_duration\s*=\s*local\.key_destroy_wait/);
    expect(key).toMatch(/prevent_destroy = true/);
  });
});

describe("the record bucket", () => {
  it("is on the evidence key, keeps no versions, and is never destroyed", () => {
    const bucket = block("google_storage_bucket", "word_drawings");
    expect(bucket).toMatch(/default_kms_key_name = google_kms_crypto_key\.evidence_encryption\.id/);
    expect(bucket).toMatch(/versioning \{\s*enabled = false\s*\}/);
    expect(bucket).toMatch(/force_destroy\s*=\s*false/);
    expect(bucket).toMatch(/prevent_destroy = true/);
    expect(terraform).toMatch(
      /word_drawing_records = "\$\{var\.project_id\}-\$\{local\.name_prefix\}-word-drawings"/,
    );
  });
});

describe("the drawing trigger", () => {
  const trigger = block("google_cloudbuild_trigger", "word_drawing");

  it("runs as the drawing identity, on a message on the drawing topic", () => {
    expect(trigger).toMatch(/service_account\s*=\s*google_service_account\.word_drawing\.id/);
    expect(trigger).toMatch(
      /pubsub_config \{\s*topic = google_pubsub_topic\.word_drawing_requests\.id\s*\}/,
    );
    expect(trigger).toMatch(/location\s*=\s*var\.region/);
  });

  it("builds main of the public repository, with the steps written here, and nothing else", () => {
    expect(trigger).toContain(
      '"git remote add origin https://github.com/ogbetspp-coder/fhir_real_time_data_exchange.git",\n',
    );
    expect(trigger).toContain('"git fetch -q --depth=1 origin refs/heads/main",\n');
    expect(trigger).toContain('"git checkout -q --detach FETCH_HEAD",\n');
    expect(trigger).toContain('"exec bash scripts/word-drawing/build.sh exists",\n');
    // No other source, configuration file or repository connection.
    expect(trigger).not.toMatch(
      /source_to_build|git_file_source|repository_event_config|filename|\bsource \{/,
    );
    expect(trigger).toMatch(/dynamic "step" \{\s*for_each = local\.word_drawing_steps/);
    expect(trigger).toMatch(
      /args\s*=\s*concat\(\["scripts\/word-drawing\/build\.sh"\], step\.value\.args\)/,
    );
  });

  it("takes the request from the message body only, refuses a malformed one, and passes it through env", () => {
    expect(trigger).toMatch(
      /substitutions = \{\s*_REQUEST = "\$\(body\.message\.data\.request\)"\s*\}/,
    );
    expect(trigger).toContain(
      'filter = "size(_REQUEST) > 0 && size(_REQUEST) <= 4000 && _REQUEST.matches(\\"^[A-Za-z0-9_-]+$\\")"',
    );
    // The one use of the request: the first step's environment.
    expect(terraform.match(/_REQUEST/g)).toHaveLength(5);
    expect(trigger).toMatch(/env = \["REQUEST=\$\$\{_REQUEST\}"\]/);
  });

  it("expires a backlog, logs to Cloud Logging, and runs every step in an image pinned by digest", () => {
    expect(trigger).toMatch(/queue_ttl = "300s"/);
    expect(trigger).toMatch(/timeout\s*=\s*"600s"/);
    expect(trigger).toMatch(/logging = "CLOUD_LOGGING_ONLY"/);
    for (const image of ["cloud_sdk_image", "docker_image"]) {
      expect(terraform).toMatch(new RegExp(`${image} += "gcr\\.io/[^"@]+@sha256:[0-9a-f]{64}"`));
    }
  });

  it("names, in order, every step the build script runs", () => {
    const steps = [
      ...terraform.matchAll(/\{ id = "([\w-]+)", image = local\.\w+, args = \[([^\]]*)\]/g),
    ];
    expect(steps.map(([, id, args]) => [id, args])).toEqual([
      ["pull", '"pull"'],
      ["parse", '"parse"'],
      ["fetch", '"fetch"'],
      ["draw-1", '"draw", "1"'],
      ["draw-2", '"draw", "2"'],
      ["record", '"record"'],
      ["sign", '"sign"'],
    ]);
    expect(build).toMatch(
      /^ {2}exists \| pull \| parse \| fetch \| record \| sign\) "\$step" ;;$/m,
    );
  });
});

describe("the drawing build's containers", () => {
  it("are hardened as the renderer's, with the memory measured on Cloud Build", () => {
    const hardening = /^HARDENING=\(([^)]*)\)$/m.exec(build)?.[1]?.split(/\s+/).filter(Boolean);
    expect(hardening).toEqual([
      ...HARDENING.slice(0, -1).flatMap((flag) => flag.split(" ")),
      "--memory=2g",
    ]);
    expect(HARDENING.at(-1)).toBe("--memory=6g");
  });
});

// ---- The build script against stand-in gcloud, curl and git. ----

const dirs: string[] = [];
afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

const DIGEST = `sha256:${"c".repeat(64)}`;
const COMMIT = "b".repeat(40);
const REQUEST = Buffer.from('{"docxSha256":"aa","recompute":{}}');

// A checkout with the lock, the pinned key and, under run/, what the earlier steps wrote; and
// stand-ins for gcloud (a token), git (the commit, and main's first-parent line) and curl (Cloud
// Storage and Cloud KMS, as STUB_* say, signing with the key's private half).
function checkout(): { dir: string; bin: string } {
  const dir = mkdtempSync(path.join(tmpdir(), "word-drawing-build-"));
  dirs.push(dir);
  const bin = path.join(dir, "bin");
  mkdirSync(bin);
  mkdirSync(path.join(dir, "src/render/word-drawing/keys/dev"), { recursive: true });
  mkdirSync(path.join(dir, "run"));
  writeFileSync(
    path.join(dir, "src/render/word-drawing/lock.json"),
    JSON.stringify({
      version: "word-drawing/1.2.0",
      imageDigests: { dev: DIGEST, validation: null, prod: null },
    }),
  );
  const { privateKey, publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
  writeFileSync(path.join(dir, "private.pem"), privateKey.export({ type: "pkcs8", format: "pem" }));
  writeFileSync(
    path.join(dir, "src/render/word-drawing/keys/dev/1.pem"),
    publicKey.export({ type: "spki", format: "pem" }),
  );
  const stub = (name: string, body: string) => {
    writeFileSync(path.join(bin, name), `#!/usr/bin/env bash\n${body}\n`);
    chmodSync(path.join(bin, name), 0o755);
  };
  stub("gcloud", "echo token");
  stub(
    "git",
    `case "$1" in rev-parse) echo ${COMMIT} ;; rev-list) printf '%s\\n' \${STUB_MAIN:-${COMMIT}} ;; esac`,
  );
  stub(
    "curl",
    `out=""; data=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in
    --output) out="$2"; shift ;;
    --data) data="$2"; shift ;;
    --data-binary) data="$2"; shift ;;
    --header|--write-out|--request|--max-filesize) shift ;;
    -*) ;;
    *) url="$1" ;;
  esac
  shift
done
echo "$url" >>"${dir}/calls"
case "$url" in
  *asymmetricSign)
    printf '%s' "$data" | sed 's/.*"sha256":"\\([^"]*\\)".*/\\1/' | base64 -d >"${dir}/digest"
    sig="$(openssl pkeyutl -sign -inkey "${dir}/\${STUB_SIGNER:-private.pem}" -pkeyopt digest:sha256 -pkeyopt rsa_padding_mode:pss -pkeyopt rsa_pss_saltlen:32 -in "${dir}/digest" | base64 | tr -d '\\n')"
    printf '{"signature":"%s"}' "$sig" >"$out"; printf 200 ;;
  *uploadType=media*)
    cp "\${data#@}" "${dir}/written"; printf '%s' "\${STUB_WRITE:-200}" ;;
  *alt=media)
    cp "${dir}/existing" "$out"; printf 200 ;;
  *)
    printf '{}' >"$out"; printf '%s' "\${STUB_FOUND:-404}" ;;
esac`,
  );
  return { dir, bin };
}

function run(
  dir: string,
  bin: string,
  step: string,
  env: Record<string, string> = {},
): { status: number | null; out: string } {
  const result = spawnSync("bash", [path.resolve("scripts/word-drawing/build.sh"), step], {
    cwd: dir,
    encoding: "utf8",
    env: {
      PATH: `${bin}:${process.env.PATH ?? ""}`,
      ENVIRONMENT: "dev",
      RECORDS: "records",
      SUBMISSIONS: "submissions",
      SIGNING_KEY: "projects/p/locations/l/keyRings/r/cryptoKeys/word-drawing-hsm",
      IMAGES: "europe-west4-docker.pkg.dev/p/ema-flow-images",
      ...env,
    },
  });
  return { status: result.status, out: `${result.stdout}${result.stderr}` };
}

const base64url = (bytes: Buffer) => bytes.toString("base64url");

describe("the build's first step", () => {
  it("decodes the request, keys the record by its bytes' hash and looks for it", () => {
    const { dir, bin } = checkout();
    const result = run(dir, bin, "exists", { REQUEST: base64url(REQUEST) });
    expect(result).toMatchObject({ status: 0 });
    expect(readFileSync(path.join(dir, "run/request.json"))).toEqual(REQUEST);
    const object = readFileSync(path.join(dir, "run/object"), "utf8");
    const key = spawnSync("sha256sum", { input: REQUEST, encoding: "utf8" }).stdout.slice(0, 64);
    const drawing = spawnSync("sha256sum", {
      input: `{"imageDigest":"${DIGEST}","version":"word-drawing/1.2.0"}`,
      encoding: "utf8",
    }).stdout.slice(0, 64);
    expect(object).toBe(`word/${key}/${drawing}/1.json`);
    expect(readFileSync(path.join(dir, "calls"), "utf8")).toBe(
      `https://storage.googleapis.com/storage/v1/b/records/o/${object.replaceAll("/", "%2F")}\n`,
    );
    expect(readFileSync(path.join(dir, "run/image"), "utf8")).toBe(
      `europe-west4-docker.pkg.dev/p/ema-flow-images/word-drawing@${DIGEST}`,
    );
    expect(result.out).toContain("to draw: ");
  });

  it("ends every later step at once when the record exists", () => {
    const { dir, bin } = checkout();
    expect(
      run(dir, bin, "exists", { REQUEST: base64url(REQUEST), STUB_FOUND: "200" }),
    ).toMatchObject({
      status: 0,
    });
    for (const step of ["pull", "parse", "fetch", "record", "sign"]) {
      expect(run(dir, bin, step)).toEqual({ status: 0, out: "" });
    }
  });

  it.each([
    ["any answer but found or not found", { REQUEST: base64url(REQUEST), STUB_FOUND: "403" }],
    ["padded base64url", { REQUEST: `${base64url(Buffer.from("ab"))}==` }],
    ["a second spelling of the same bytes", { REQUEST: "YR" }],
    ["base64, not base64url", { REQUEST: Buffer.from([0xfb, 0xff]).toString("base64") }],
    ["no request", { REQUEST: "" }],
    ["a request over 4,000 characters", { REQUEST: "A".repeat(4004) }],
  ])("fails on %s", (_, env) => {
    const { dir, bin } = checkout();
    expect(run(dir, bin, "exists", env).status).toBe(1);
  });

  it("fails while no image or no key version is pinned, and skips a revoked one", () => {
    const { dir, bin } = checkout();
    writeFileSync(path.join(dir, "src/render/word-drawing/keys/dev/revoked.json"), "[1]");
    expect(run(dir, bin, "exists", { REQUEST: base64url(REQUEST) }).out).toContain(
      "no key version is pinned for dev",
    );
    expect(
      run(dir, bin, "exists", { REQUEST: base64url(REQUEST), ENVIRONMENT: "prod" }).out,
    ).toContain("no drawing image is pinned for prod");
  });
});

describe("the build's signing step", () => {
  const RECORD = '{"keyVersion":1,"recordVersion":"word-drawing-record/1.0.0"}';

  function signing(): { dir: string; bin: string } {
    const { dir, bin } = checkout();
    for (const [name, text] of Object.entries({
      commit: COMMIT,
      "key-version": "1",
      object: "word/k/d/1.json",
      "record.json": RECORD,
    })) {
      writeFileSync(path.join(dir, "run", name), text);
    }
    return { dir, bin };
  }

  it("signs the record's bytes, checks the signature, and writes {record, signatureBase64} create-if-absent", () => {
    const { dir, bin } = signing();
    const result = run(dir, bin, "sign");
    expect(result).toMatchObject({ status: 0 });
    const written = readFileSync(path.join(dir, "written"), "utf8");
    // The record's bytes as signed, then the signature in base64: canonical JSON by construction.
    const prefix = `{"record":${RECORD},"signatureBase64":"`;
    expect(written.slice(0, prefix.length)).toBe(prefix);
    expect(written.slice(prefix.length)).toMatch(/^[A-Za-z0-9+/]+={0,2}"\}$/);
    expect(readFileSync(path.join(dir, "calls"), "utf8")).toContain(
      "https://storage.googleapis.com/upload/storage/v1/b/records/o?uploadType=media&ifGenerationMatch=0&name=word%2Fk%2Fd%2F1.json",
    );
  });

  it("accepts a record already stored only when it is this record", () => {
    const { dir, bin } = signing();
    writeFileSync(path.join(dir, "existing"), `{"record":${RECORD},"signatureBase64":"other"}`);
    expect(run(dir, bin, "sign", { STUB_WRITE: "412" })).toEqual({
      status: 0,
      out: "recorded already, the same record: word/k/d/1.json\n",
    });
    writeFileSync(
      path.join(dir, "existing"),
      `{"record":${RECORD.replace('"keyVersion":1', '"keyVersion":2')},"signatureBase64":"x"}`,
    );
    expect(run(dir, bin, "sign", { STUB_WRITE: "412" }).out).toContain("not deterministic");
  });

  it("writes nothing signed by another key, for a commit off main, or on a refused write", () => {
    const other = signing();
    const { privateKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
    writeFileSync(
      path.join(other.dir, "other.pem"),
      privateKey.export({ type: "pkcs8", format: "pem" }),
    );
    expect(run(other.dir, other.bin, "sign", { STUB_SIGNER: "other.pem" }).out).toContain(
      "does not verify against the pinned key 1",
    );
    const off = signing();
    expect(run(off.dir, off.bin, "sign", { STUB_MAIN: "d".repeat(40) }).out).toContain(
      "is not a first-parent commit of main",
    );
    const refused = signing();
    expect(run(refused.dir, refused.bin, "sign", { STUB_WRITE: "500" }).out).toContain(
      "writing the record answered HTTP 500",
    );
    for (const { dir } of [other, off]) {
      expect(() => readFileSync(path.join(dir, "written"))).toThrow();
    }
  });
});
