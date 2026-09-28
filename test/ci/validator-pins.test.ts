import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  HERMETIC_PROPERTIES,
  PACKAGE_LOCK,
  networkUse,
  offlineStartVerdict,
  packageSummary,
  readPackageLock,
  readSidecarPins,
} from "../../scripts/ci/validator-pins.mjs";

// The official validator is hermetic (docs/foundations.md, C9): every package it uses is pinned
// in fhir/validator-packages.lock, installed before it runs, and it has no route to the network.
// The image and the CI gate read the same two files. These tests pin that, and prove the readers
// refuse a Dockerfile or a list that has drifted rather than half-reading it.

const dockerfile = readFileSync("Dockerfile.validator", "utf8");
const temporary: string[] = [];

function variant(text: string): string {
  const directory = mkdtempSync(path.join(tmpdir(), "validator-pins-"));
  temporary.push(directory);
  const file = path.join(directory, "Dockerfile.validator");
  writeFileSync(file, text);
  return file;
}

afterEach(() => {
  for (const directory of temporary.splice(0)) rmSync(directory, { recursive: true, force: true });
});

describe("the validator sidecar as built", () => {
  const pins = readSidecarPins("Dockerfile.validator");

  it("has no route to the network and uses the installed package cache", () => {
    for (const property of HERMETIC_PROPERTIES) expect(pins.jvmProperties).toContain(property);
    expect(pins.jvmProperties).toContain("-Duser.home=/opt/fhir/home");
  });

  it("validates under no particular jurisdiction, in a fixed locale", () => {
    // Without these the validator took both from the container: United States, on 2026-09-21.
    const flags = pins.flags.join(" ");
    expect(flags).toContain("-jurisdiction uv");
    expect(flags).toContain("-locale en-US");
  });

  it("refuses every HTTP request inside the application, not only at the proxy", () => {
    // The validator's own switch. Besides reproducibility it closes a request-forgery path:
    // submitted content can name URLs for the validator to fetch, internal ones included.
    expect(pins.flags).toContain("-no-http-access");
  });

  it("installs the pinned list, and stops the build on the first bad checksum", () => {
    expect(dockerfile).toContain(`COPY ${PACKAGE_LOCK} /opt/fhir/validator-packages.lock`);
    // POSIX `set -e` is ignored inside an AND-OR list, so a loop chained with `&&` would carry
    // on past a checksum mismatch. Every failing step in the loop exits explicitly instead.
    const loop = /while read -r id version sha256; do([\s\S]*?)done </.exec(dockerfile)?.[1] ?? "";
    expect(loop).toMatch(/curl[^\n]*\|\| exit 1/);
    expect(loop).toMatch(/sha256sum --check -[^\n]*\|\| exit 1/);
    expect(loop).toMatch(/tar -xzf[^\n]*\|\| exit 1/);
    expect(dockerfile).not.toMatch(/&&\s*\\?\s*\n?\s*while read/);
  });
});

// The FHIR store's profile import and the worker's run manifest read fhir/standards.lock.json;
// the sidecar reads its own ARGs. Two places, held equal here (audit B07, S-4): the EMA has shipped
// two different 1.0.0 editions, so an id#version alone does not say which bytes validated a run.
describe("the sidecar and the standards lock", () => {
  const pins = readSidecarPins("Dockerfile.validator");
  const lock = JSON.parse(readFileSync("fhir/standards.lock.json", "utf8")) as {
    artifacts: { name: string; package?: string; version?: string; url: string; sha256: string }[];
  };

  it("download every package the lock pins, from the same URL, with the same SHA-256", () => {
    const fromLock = lock.artifacts
      .filter((artifact) => artifact.package !== undefined)
      .map(({ url, sha256 }) => ({ url, sha256 }));
    const fromSidecar = pins.artefacts
      .filter(({ file }) => file !== "validator_cli.jar")
      .map(({ url, sha256 }) => ({ url, sha256 }));
    expect(fromSidecar).toHaveLength(fromLock.length);
    expect(new Set(fromSidecar.map((pin) => JSON.stringify(pin)))).toEqual(
      new Set(fromLock.map((pin) => JSON.stringify(pin))),
    );
  });

  it("run the validator the lock pins", () => {
    const jar = pins.artefacts.find(({ file }) => file === "validator_cli.jar");
    const locked = lock.artifacts.find(({ name }) => name === "HL7 FHIR Validator CLI");
    expect(jar).toEqual({ file: "validator_cli.jar", url: locked?.url, sha256: locked?.sha256 });
    expect(pins.version).toBe(locked?.version);
  });

  // HL7's unversioned URL serves whichever release is current; the STU1 path does not move.
  it("fetch the Global ePI package at its versioned URL", () => {
    const global = pins.artefacts.find(({ file }) => file === "global-epi-package.tgz");
    expect(global?.url).toBe("https://hl7.org/fhir/uv/emedicinal-product-info/STU1/package.tgz");
  });
});

// The runtime stage (audit B07, S-5): what was downloaded and verified, a user without root, and
// no download tool; no stage installs from a live package archive.
describe("the validator image's runtime", () => {
  const stages = dockerfile.split(/^FROM /m).slice(1);
  const runtime = stages.at(-1) ?? "";

  it("is a second stage that copies only the verified files", () => {
    expect(stages).toHaveLength(2);
    expect(stages[0]).toMatch(/ AS fetch\n/);
    expect(runtime).toMatch(
      /^COPY --from=fetch --chown=validator:validator \/opt\/fhir \/opt\/fhir$/m,
    );
    expect(runtime).not.toMatch(/\bcurl\s+-/);
  });

  it("runs as a user without root, and removes curl and wget, failing if either remains", () => {
    expect(runtime).toMatch(/^USER validator$/m);
    expect(runtime).toMatch(/useradd --system --uid 10001/);
    expect(runtime).toMatch(/apt-get purge --yes --auto-remove curl wget/);
    expect(runtime).toMatch(/if command -v "\$tool" >\/dev\/null; then [^\n]*exit 1; fi/);
    expect(runtime.indexOf("USER validator")).toBeLessThan(runtime.indexOf("ENTRYPOINT"));
  });

  it("installs nothing from a package archive", () => {
    const code = dockerfile
      .split("\n")
      .filter((line) => !/^\s*#/.test(line))
      .join("\n");
    expect(code).not.toMatch(/apt-get\s+(update|install)|apt\s+(update|install)/);
  });
});

describe("the pinned package list", () => {
  const entries = readPackageLock(PACKAGE_LOCK);

  it("covers the FHIR version the validator is told to use", () => {
    // `-version 5.0.0` makes the validator load the R5 core package before anything else.
    expect(entries.map(({ key }) => key)).toContain("hl7.fhir.r5.core#5.0.0");
  });

  it("downloads each entry from the registry by id and version", () => {
    for (const { id, version, url } of entries) {
      expect(url).toBe(`https://packages2.fhir.org/packages/${id}/${version}`);
    }
  });

  it("refuses a malformed line, a bad checksum and a duplicate", () => {
    const write = (text: string) => {
      const file = path.join(path.dirname(variant("")), "validator-packages.lock");
      writeFileSync(file, text);
      return file;
    };
    const sha = "a".repeat(64);
    expect(() => readPackageLock(write(`hl7.fhir.r5.core 5.0.0\n`))).toThrow(/expected/);
    expect(() => readPackageLock(write(`hl7.fhir.r5.core 5.0.0 ${"g".repeat(64)}\n`))).toThrow(
      /SHA-256/,
    );
    expect(() =>
      readPackageLock(write(`hl7.fhir.r5.core 5.0.0 ${sha}\nhl7.fhir.r5.core 5.0.0 ${sha}\n`)),
    ).toThrow(/twice/);
    expect(() => readPackageLock(write(`# only a comment\n`))).toThrow(/no packages/);
  });
});

describe("the readers refuse a Dockerfile that has drifted", () => {
  it("without the closed proxy", () => {
    const text = dockerfile.replace(`"-Dhttps.proxyPort=9", `, "");
    expect(() => readSidecarPins(variant(text))).toThrow(/-Dhttps\.proxyPort=9/);
  });

  it("without the package cache location", () => {
    const text = dockerfile.replace(`"-Duser.home=/opt/fhir/home", `, "");
    expect(() => readSidecarPins(variant(text))).toThrow(/user\.home/);
  });

  it("without the package list", () => {
    const text = dockerfile.replace(/^COPY fhir\/validator-packages\.lock.*$/m, "");
    expect(() => readSidecarPins(variant(text))).toThrow(/does not COPY/);
  });

  it("without the application's own network refusal", () => {
    const text = dockerfile.replace(`, "-no-http-access"]`, "]");
    expect(() => readSidecarPins(variant(text))).toThrow(/-no-http-access/);
  });

  it("without a pinned jurisdiction", () => {
    const text = dockerfile.replace(`"-jurisdiction", "uv", `, "");
    expect(() => readSidecarPins(variant(text))).toThrow(/-jurisdiction/);
  });

  it("loading one package twice and another not at all, which a count alone passed", () => {
    const text = dockerfile.replace(
      `"-ig", "/opt/fhir/ema-epi-package.tgz"`,
      `"-ig", "/opt/fhir/global-epi-package.tgz"`,
    );
    expect(text).not.toBe(dockerfile);
    expect(() => readSidecarPins(variant(text))).toThrow(/global-epi-package\.tgz more than once/);
  });

  it("loading one package twice beside all the others", () => {
    const text = dockerfile.replace(
      `"-tx", "n/a"`,
      `"-ig", "/opt/fhir/terminology-package.tgz", "-tx", "n/a"`,
    );
    expect(text).not.toBe(dockerfile);
    expect(() => readSidecarPins(variant(text))).toThrow(/more than once/);
  });
});

describe("reading how the validator used the network", () => {
  it("fails on installs and socket-level fetch errors, reports policy refusals", () => {
    const output = [
      "  Loading FHIR v5.0.0 from hl7.fhir.r5.core#5.0.0",
      "Installing hl7.fhir.r5.core#5.0.0 to the package cache",
      "Error fetching https://packages2.fhir.org/packages/hl7.terminology: Access to the internet is not allowed by local security policy",
      "Error fetching https://packages.fhir.org/hl7.terminology: Failed to connect to /127.0.0.1:9",
      "Failed to determine latest version of package hl7.terminology from server: build.fhir.org",
      "Failed to determine latest version of package hl7.terminology from server: x: Failed to connect to /127.0.0.1:9",
      "  Load hl7.terminology.r5#6.2.0 - 4288 resources (00:11.450)",
    ];
    const { installs, refused, other } = networkUse(output);
    expect(installs).toEqual([output[1]]);
    // The policy refusal and the bare latest-version lookup are refusals; anything that reached
    // a socket is not.
    expect(refused).toEqual([output[2], output[4]]);
    expect(other).toEqual([output[3], output[5]]);
  });

  it("reads the validator's own list of loaded packages, and knows when it is missing", () => {
    expect(
      packageSummary(["x", "  Package Summary: [hl7.fhir.r5.core#5.0.0, EUePI#1.0.0]", "y"]),
    ).toEqual(["hl7.fhir.r5.core#5.0.0", "EUePI#1.0.0"]);
    expect(packageSummary(["no summary here"])).toBeUndefined();
  });
});

describe("the local validator helper", () => {
  it("runs the way the sidecar runs, not the way it used to", () => {
    const helper = readFileSync("scripts/dev/validator-server.sh", "utf8");
    for (const property of HERMETIC_PROPERTIES) {
      const [key, value] = property.replace(/^-D/, "").split("=");
      expect(helper).toMatch(
        new RegExp(
          `-D${String(key).replace(/\./g, "\\.")}=${String(value).replace(/\./g, "\\.")}\\b`,
        ),
      );
    }
    expect(helper).toContain('-Duser.home="${CACHE}/home"');
    for (const flag of ["-jurisdiction uv", "-locale en-US", "-no-http-access", "-tx n/a"]) {
      expect(helper).toContain(flag);
    }
  });
});

describe("the image build", () => {
  const build = readFileSync("cloudbuild.images.yaml", "utf8");

  it("proves the validator starts with networking disabled", () => {
    expect(build).toContain("- id: validator-starts-offline");
    expect(build).toMatch(/docker run --detach --name offline --network none/);
    expect(build).toMatch(
      /- id: validator-offline-verdict\n[\s\S]*?waitFor: \["validator-starts-offline"\]\n\s+entrypoint: node\n\s+args:\n\s+- scripts\/ci\/validator-offline\.mjs\n\s+- offline\.log/,
    );
  });

  it("judges the log with the gate's classifier, not a second copy of it in grep", () => {
    // The fetch-error patterns live in networkUse alone.
    expect(build).not.toMatch(/Error fetching|Failed to determine latest version|Installing /);
  });

  it("builds the three images at once, the validator from its previous layers", () => {
    for (const id of ["build-app-image", "build-query-image", "pull-validator-cache"]) {
      expect(build).toMatch(new RegExp(`- id: ${id}\\n[^\\n]*\\n\\s+waitFor: \\["-"\\]`));
    }
    expect(build).toMatch(/waitFor: \["pull-validator-cache"\]/);
    expect(build).toMatch(/- --cache-from\n\s+- \S+\/validator:buildcache/);
    expect(build).toMatch(/^ {2}- \S+\/validator:buildcache$/m);
  });

  it("stamps every image with the commit it is built from", () => {
    const builds = build.match(/^ {6}- build$/gm) ?? [];
    const labels = build.match(
      /- --label\n\s+- org\.opencontainers\.image\.revision=\$\{_REVISION\}/g,
    );
    expect(builds).toHaveLength(3);
    expect(labels).toHaveLength(3);
    expect(readFileSync("scripts/gcp/deploy.sh", "utf8")).toContain("_REVISION=${SERVICE_VERSION}");
  });
});

describe("the offline start's verdict", () => {
  const started = [
    "  Jurisdiction: Global (Whole world)",
    "  Locale: United States/US",
    "  Package Summary: [hl7.fhir.r5.core#5.0.0]",
    "Failed to determine latest version of package hl7.terminology from server: build.fhir.org",
    "FHIR Validator HTTP Service started on port 8090",
  ];

  it("passes a validator that started offline, pinned, refusing its one optional lookup", () => {
    const verdict = offlineStartVerdict(started);
    expect(verdict.failures).toEqual([]);
    expect(verdict.ok).toBe(true);
    expect(verdict.evidence).toHaveLength(4);
  });

  it.each([
    [
      "an install",
      [...started, "Installing hl7.fhir.r5.core#5.0.0 to the package cache"],
      "not installed",
    ],
    [
      "a fetch that reached a socket",
      [...started, "Error fetching https://x: Failed to connect to /127.0.0.1:9"],
      "policy did not refuse",
    ],
    ["no start", started.slice(0, 4), "did not start"],
    ["another jurisdiction", started.slice(1), "universal jurisdiction"],
    ["another locale", [started[0] ?? "", ...started.slice(2)], "pinned locale"],
  ])("fails on %s", (_label, lines, reason) => {
    const verdict = offlineStartVerdict(lines);
    expect(verdict.ok).toBe(false);
    expect(verdict.failures.join("\n")).toContain(reason);
  });

  it("is what the build runs, exiting 1 on a refusal", () => {
    const directory = mkdtempSync(path.join(tmpdir(), "validator-offline-"));
    temporary.push(directory);
    const log = path.join(directory, "offline.log");
    const run = () =>
      spawnSync(process.execPath, ["scripts/ci/validator-offline.mjs", log], { encoding: "utf8" });
    writeFileSync(log, started.join("\n"));
    expect(run().status).toBe(0);
    writeFileSync(log, [...started, "Installing a#1.0.0 to the package cache"].join("\n"));
    const refused = run();
    expect(refused.status).toBe(1);
    expect(refused.stderr).toContain("not installed in the image");
  });
});
