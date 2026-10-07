import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { readRendererPins } from "../../scripts/ci/renderer-pins.mjs";

// The renderer image's pins (docs/design/authority-import-renderer.md, R6): read from
// Dockerfile.renderer alone, and the plausible mistakes in editing it refused. A disguised edit is
// for review, not for this reader (owner decision O3).

const original = readFileSync("Dockerfile.renderer", "utf8");

function variant(edit: (text: string) => string): string {
  const directory = mkdtempSync(path.join(tmpdir(), "renderer-pins-"));
  const file = path.join(directory, "Dockerfile.renderer");
  writeFileSync(file, edit(original));
  return file;
}

// A line added to the RUN that unpacks the downloads.
const inUnpackRun = (line: string) => (text: string) =>
  text.replace("RUN set -eu; \\\n    unzip", `RUN set -eu; \\\n    ${line}; \\\n    unzip`);

describe("the renderer image's pins", () => {
  it("reads the base, the snapshot, Chrome and every font with its checksum", () => {
    const pins = readRendererPins();
    expect(pins.base).toMatch(/^node:22\.22\.0-bookworm-slim@sha256:[0-9a-f]{64}$/);
    expect(pins.debianSnapshot).toMatch(/^\d{8}T\d{6}Z$/);
    expect(pins.chromeVersion).toBe("154.0.8037.57");
    expect(pins.fonts.map(({ file }) => file)).toEqual([
      "Carlito-Regular.ttf",
      "Carlito-Bold.ttf",
      "Carlito-Italic.ttf",
      "Carlito-BoldItalic.ttf",
      "Caladea-Regular.ttf",
      "Caladea-Bold.ttf",
      "Caladea-Italic.ttf",
      "Caladea-BoldItalic.ttf",
    ]);
    for (const artefact of pins.artefacts) expect(artefact.sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(pins.artefacts).toHaveLength(10);
  });

  it("shares the worker's node digest", () => {
    const worker = /^FROM (node:\S+@sha256:[0-9a-f]{64}) AS build$/m.exec(
      readFileSync("Dockerfile", "utf8"),
    )?.[1];
    expect(readRendererPins().base).toBe(worker);
  });

  it.each([
    ["an unpinned base", (text: string) => text.replace(/@sha256:[0-9a-f]{64}/, "")],
    ["a second FROM", (text: string) => `${text}\nFROM scratch\n`],
    ["a later stage from an unpinned image", (text: string) => `${text}\nFROM python:3 AS x\n`],
    ["an unnamed renderer stage", (text: string) => text.replace(/ AS renderer$/m, "")],
    ["an apt install outside the snapshot's RUN", inUnpackRun("apt-get install --yes x")],
    ["an apt install with an option first", inUnpackRun("apt-get -y install x")],
    ["an apt install with a quiet option first", inUnpackRun("apt-get -qq install x")],
    ["a download without a checksum", inUnpackRun('curl -fsSL "https://example.org/x" -o x')],
    ["a curl whose first argument is the URL", inUnpackRun('curl "https://example.org/x" -o x')],
    ["a curl piped to tar", inUnpackRun("curl https://example.org/x.tgz | tar xz")],
    ["a curl piped to a shell", inUnpackRun("curl https://example.org/install.sh | sh")],
    ["a curl of an ARG", inUnpackRun("curl ${CHROME_VERSION} -o x")],
    ["a download with wget", inUnpackRun("wget https://example.org/x")],
    ["an ADD", (text: string) => text.replace("USER node", "ADD https://e/x /opt/x\nUSER node")],
    [
      "a checksum of another file",
      (text: string) =>
        text.replace(
          'echo "${CHROME_SHA256}  chrome-headless-shell-linux64.zip"',
          'echo "${CHROME_SHA256}  other.zip"',
        ),
    ],
    [
      "a malformed checksum",
      (text: string) => text.replace(/ARG CHROME_SHA256=[0-9a-f]{64}/, "ARG CHROME_SHA256=abc"),
    ],
    [
      "Chrome at another version",
      (text: string) =>
        text.replace(
          "chrome-for-testing-public/${CHROME_VERSION}/",
          "chrome-for-testing-public/153.0.0.0/",
        ),
    ],
    [
      "a font at another commit",
      (text: string) =>
        text.replace(
          "google/fonts/${GOOGLE_FONTS_COMMIT}/ofl/carlito/Carlito-Bold.ttf",
          "google/fonts/main/ofl/carlito/Carlito-Bold.ttf",
        ),
    ],
    [
      "a font left out",
      (text: string) =>
        text.replace(
          / {4}&& curl -fsSL \\\n {6}"https:\/\/raw\.githubusercontent\.com\/google\/fonts\/\$\{GOOGLE_FONTS_COMMIT\}\/ofl\/caladea\/Caladea-BoldItalic\.ttf" \\\n {6}-o Caladea-BoldItalic\.ttf \\\n {4}&& echo "\$\{CALADEA_BOLDITALIC_SHA256\} {2}Caladea-BoldItalic\.ttf" \| sha256sum --check -\n/,
          "\n",
        ),
    ],
    [
      "an undeclared argument",
      (text: string) => text.replace("${LIBERATION_VERSION}.tar.gz", "${NOT_DECLARED}.tar.gz"),
    ],
    [
      "an argument declared twice",
      (text: string) =>
        text.replace("ARG CHROME_VERSION=", "ARG CHROME_VERSION=1.2.3.4\nARG CHROME_VERSION="),
    ],
    [
      "a plain-HTTP download",
      (text: string) =>
        text.replace("https://storage.googleapis.com", "http://storage.googleapis.com"),
    ],
    [
      "no fontconfig copied",
      (text: string) => text.replace("COPY src/render/image/fonts.conf", "COPY other.conf"),
    ],
    [
      "a malformed snapshot",
      (text: string) => text.replace(/ARG DEBIAN_SNAPSHOT=\S+/, "ARG DEBIAN_SNAPSHOT=latest"),
    ],
  ])("refuses %s", (_, edit) => {
    const file = variant(edit);
    expect(readFileSync(file, "utf8")).not.toBe(original);
    expect(() => readRendererPins(file)).toThrow(/Dockerfile\.renderer/);
  });
});
