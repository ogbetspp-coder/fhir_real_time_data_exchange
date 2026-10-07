import { constants, createPublicKey, verify } from "node:crypto";
import { existsSync, readFileSync, statSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { readRendererPins } from "../../scripts/ci/renderer-pins.mjs";
import { crc32c } from "../../src/lib/crc32c.js";
import { HARDENING } from "../../scripts/render/run.mjs";
import { dockerArgs, IMAGE, MOUNTS } from "../../scripts/render/word-drawing.mjs";

// The Word drawing's image and its run (docs/design/certified-word-drawing.md, PR 1): the
// renderer's image with Python added, run only hardened as the renderer's is, with only what the
// drawing reads mounted, read-only; and the lock that pins each environment's image, with the
// public keys that verify each environment's records (PR 3).

const dockerfile = readFileSync("Dockerfile.renderer", "utf8");
const stages = [...dockerfile.matchAll(/^FROM (\S+) AS (\S+)$/gm)].map(([, image, name]) => ({
  image,
  name,
}));
const scripts = (
  JSON.parse(readFileSync("package.json", "utf8")) as { scripts: Record<string, string> }
).scripts;
const word = dockerfile.slice(dockerfile.indexOf("FROM renderer AS word-drawing"));

describe("the word-drawing image", () => {
  it("is the renderer unchanged, then Python, as its last target", () => {
    expect(stages.map(({ name }) => name)).toEqual(["renderer", "uv", "python", "word-drawing"]);
    expect(stages[2]?.image).toBe("renderer");
    expect(stages[3]?.image).toBe("renderer");
    // The renderer's own image is its target, not the file's last stage.
    expect(scripts["renderer:image"]).toContain("--target renderer ");
    expect(scripts["word-drawing:image"]).toBe(
      `docker build --file Dockerfile.renderer --target word-drawing --tag ${IMAGE} .`,
    );
  });

  it("installs the worker's Python with the worker's uv, and only the interpreter", () => {
    const worker = readFileSync("Dockerfile", "utf8");
    // The same uv image, by digest, so the same archive checksums install the same Python.
    const uv = /^FROM (ghcr\.io\/astral-sh\/uv:\S+@sha256:[0-9a-f]{64}) AS uv$/m;
    expect(uv.exec(dockerfile)?.[1]).toBeDefined();
    expect(uv.exec(dockerfile)?.[1]).toBe(uv.exec(worker)?.[1]);
    const python = /^RUN uv python install (\S+)/m;
    expect(python.exec(dockerfile)?.[1]).toBe(python.exec(worker)?.[1]);
    expect(word).toContain("COPY --from=python /opt/python /opt/python");
    // zone_a and label_docx are mounted from the checkout, never installed in the image.
    expect(dockerfile).not.toMatch(/uv sync|pip install|COPY (?:zone-a|label-docx-reader)/);
    expect(word).toMatch(
      /^ENV LABEL_CHROME=\/opt\/renderer\/chrome\.sh \\\n {4}PYTHONPATH=\/work\/zone-a\/src:\/work\/label-docx-reader\/src \\\n {4}ZONE_A_ROOT=\/work \\$/m,
    );
  });

  it("runs the pinned Chrome through its launcher, and asserts both versions in the image", () => {
    const launcher = "src/render/image/word-drawing-chrome.sh";
    expect(word).toContain(`COPY ${launcher} /opt/renderer/chrome.sh`);
    // Executable as committed: the legacy builder has no COPY --chmod.
    expect(statSync(launcher).mode & 0o111).toBe(0o111);
    const exec = readFileSync(launcher, "utf8")
      .split("\n")
      .filter((line) => line.startsWith("exec "));
    expect(exec).toHaveLength(1);
    expect(exec[0]).toMatch(
      /^exec \/opt\/renderer\/chrome-headless-shell-linux64\/chrome-headless-shell (?:--no-sandbox )?"\$@"$/,
    );
    const assertion = /^RUN \["\/opt\/python\/python", "-c", "([^\n]*)"\]$/m.exec(word)?.[1] ?? "";
    expect(assertion).toContain("('3.14.7', '16.0.0')");
    expect(assertion).toContain(`endswith(' ${readRendererPins().chromeVersion}')`);
  });
});

describe("the word-drawing run", () => {
  it("is hardened as the renderer's, and mounts only what the drawing reads, read-only", () => {
    const args = dockerArgs("/repo", ["-m", "zone_a.drawing", "/work/x.docx"]);
    expect(args.slice(0, 3 + HARDENING.length)).toEqual([
      "run",
      "--rm",
      "--interactive",
      ...HARDENING,
    ]);
    for (const flag of ["--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges"]) {
      expect(args).toContain(flag);
    }
    expect(args).toContain("none");
    expect(args).not.toContain("--env");
    const volumes = args.flatMap((arg, index) => (arg === "--volume" ? [args[index + 1]] : []));
    expect(volumes).toEqual(MOUNTS.map((mounted) => `/repo/${mounted}:/work/${mounted}:ro`));
    for (const mounted of MOUNTS) {
      expect([mounted, existsSync(mounted)]).toEqual([mounted, true]);
      expect(mounted).not.toMatch(/^\.?$|\.\./u);
    }
    expect(args.slice(-5)).toEqual([
      IMAGE,
      "/opt/python/python",
      "-m",
      "zone_a.drawing",
      "/work/x.docx",
    ]);
  });

  it("is what CI's Word drawing job runs, on the check script", () => {
    expect(scripts["word-drawing:check"]).toBe(
      "node scripts/render/word-drawing.mjs /work/zone-a/scripts/word_drawing_check.py",
    );
    expect(existsSync("zone-a/scripts/word_drawing_check.py")).toBe(true);
    const workflow = readFileSync(".github/workflows/ci.yml", "utf8");
    const job = workflow.slice(workflow.indexOf("\n  word-drawing:\n"));
    expect(job).toMatch(
      /run: npm run word-drawing:image\n[\s\S]*run: npm run word-drawing:check\n/,
    );
  });
});

describe("the word-drawing lock", () => {
  // The drawing id is the SHA-256 of { version, imageDigest } as this lock pins them per
  // environment (section 3, "Where it is stored"); the version is the drawing code's own.
  it("names zone_a.drawing's version and each environment's image digest, or none yet", () => {
    const lock = JSON.parse(readFileSync("src/render/word-drawing/lock.json", "utf8")) as {
      version: string;
      imageDigests: Record<string, string | null>;
    };
    const drawing = readFileSync("zone-a/src/zone_a/drawing.py", "utf8");
    const version = /^DRAWING_VERSION: Final = "(word-drawing\/\d+\.\d+\.\d+)"$/m.exec(
      drawing,
    )?.[1];
    expect(Object.keys(lock)).toEqual(["version", "imageDigests"]);
    expect(lock.version).toBe(version);
    expect(Object.keys(lock.imageDigests)).toEqual(["dev", "validation", "prod"]);
    for (const digest of Object.values(lock.imageDigests)) {
      expect(digest === null || /^sha256:[0-9a-f]{64}$/.test(digest)).toBe(true);
    }
    // PR 3: dev's image, by digest, never by a tag that can move. The build pulls
    // `<repository>/word-drawing@<digest>` and the drawing id hashes it.
    expect(lock.imageDigests.dev).toMatch(/^sha256:[0-9a-f]{64}$/);
  });
});

describe("the drawing's public keys", () => {
  // PR 3: word-drawing-hsm's version 1 in dev (section 3, "Who signs it, and with which key"), the
  // PEM exactly as Cloud KMS gave it on 2026-10-07, whose CRC32C it reported as pemCrc32c.
  it("pin dev's version 1: RSA 3072, for RSA-PSS with SHA-256 and a 32-byte salt", () => {
    const pem = readFileSync("src/render/word-drawing/keys/dev/1.pem");
    expect(crc32c(pem)).toBe(939517586);
    const key = createPublicKey(pem);
    expect(key.type).toBe("public");
    expect(key.asymmetricKeyType).toBe("rsa");
    expect(key.asymmetricKeyDetails).toEqual({ modulusLength: 3072, publicExponent: 65537n });
    // One spelling: the PEM is the key's own SubjectPublicKeyInfo export, and nothing else.
    expect(key.export({ type: "spki", format: "pem" })).toBe(pem.toString("utf8"));
    // It verifies under PSS with SHA-256 and a 32-byte salt, as the build and the worker check it:
    // a signature that is not one is refused, not an error.
    const pss = { key, padding: constants.RSA_PKCS1_PSS_PADDING, saltLength: 32 };
    expect(verify("sha256", Buffer.from("record"), pss, Buffer.alloc(384, 1))).toBe(false);
  });
});
