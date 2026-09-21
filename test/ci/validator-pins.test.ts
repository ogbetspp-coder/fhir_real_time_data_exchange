import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  HERMETIC_PROPERTIES,
  PACKAGE_LOCK,
  downloadAttempts,
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

  it("without a pinned jurisdiction", () => {
    const text = dockerfile.replace(`"-jurisdiction", "uv", `, "");
    expect(() => readSidecarPins(variant(text))).toThrow(/-jurisdiction/);
  });
});

describe("recognising a download attempt in validator output", () => {
  it("catches installing, fetching and failed fetches, and nothing else", () => {
    const output = [
      "  Loading FHIR v5.0.0 from hl7.fhir.r5.core#5.0.0",
      "Installing hl7.fhir.r5.core#5.0.0 to the package cache",
      "  Fetching:",
      "Error fetching https://packages2.fhir.org/packages/hl7.fhir.r5.core: Failed to connect",
      "  Load hl7.terminology.r5#6.2.0 - 4288 resources (00:11.450)",
      "  Package Summary: [hl7.fhir.r5.core#5.0.0]",
    ];
    expect(downloadAttempts(output)).toEqual(output.slice(1, 4));
  });
});

describe("the image build", () => {
  it("proves the validator starts with networking disabled", () => {
    const build = readFileSync("cloudbuild.images.yaml", "utf8");
    expect(build).toContain("- id: validator-starts-offline");
    expect(build).toMatch(/docker run --detach --name offline --network none/);
    expect(build).toMatch(/Installing \[\^ \]\+ to the package cache\|Fetching:\|Error fetching/);
  });
});
