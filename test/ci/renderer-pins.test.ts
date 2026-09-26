import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { readRendererPins } from "../../scripts/ci/renderer-pins.mjs";

// The renderer image's pins (docs/design/authority-import-renderer.md, R6): read from
// Dockerfile.renderer alone, and every way that file can drift from the pinned shape refused.

const original = readFileSync("Dockerfile.renderer", "utf8");

function variant(edit: (text: string) => string): string {
  const directory = mkdtempSync(path.join(tmpdir(), "renderer-pins-"));
  const file = path.join(directory, "Dockerfile.renderer");
  writeFileSync(file, edit(original));
  return file;
}

describe("the renderer image's pins", () => {
  it("reads the base, the snapshot, Chrome and every font with its checksum", () => {
    const pins = readRendererPins();
    expect(pins.base).toMatch(/^node:22\.22\.0-bookworm-slim@sha256:[0-9a-f]{64}$/);
    expect(pins.debianSnapshot).toMatch(/^\d{8}T\d{6}Z$/);
    expect(pins.chromeVersion).toBe("154.0.8037.57");
    expect(pins.chrome.url).toBe(
      "https://storage.googleapis.com/chrome-for-testing-public/154.0.8037.57/linux64/chrome-headless-shell-linux64.zip",
    );
    expect(pins.liberation.url).toContain("liberation-fonts-ttf-2.1.5.tar.gz");
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
    [
      "a download without a checksum",
      (text: string) =>
        text.replace(
          "RUN set -eu; \\\n    unzip",
          'RUN curl -fsSL "https://example.org/x" -o x; \\\n    unzip',
        ),
    ],
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
      "a curl whose first argument is the URL",
      (text: string) =>
        text.replace(
          "RUN set -eu; \\\n    unzip",
          'RUN curl "https://example.org/x" -o x; \\\n    unzip',
        ),
    ],
    [
      "a download with wget",
      (text: string) =>
        text.replace("RUN set -eu; \\\n    unzip", "RUN wget https://example.org/x; \\\n    unzip"),
    ],
    [
      "an ADD of a URL",
      (text: string) => text.replace("USER node", "ADD https://example.org/x /opt/x\nUSER node"),
    ],
    [
      "a checksum failure ignored",
      (text: string) =>
        text.replace(
          'echo "${CHROME_SHA256}  chrome-headless-shell-linux64.zip" | sha256sum --check -',
          'echo "${CHROME_SHA256}  chrome-headless-shell-linux64.zip" | sha256sum --check - || true',
        ),
    ],
    [
      "a curl hidden in a command substitution on the install line",
      (text: string) =>
        text.replace(
          "ca-certificates curl unzip fontconfig",
          'ca-certificates curl unzip fontconfig $(curl -fsSL "https://example.org/x")',
        ),
    ],
    [
      "a checksum failure swallowed by a later command",
      (text: string) =>
        text.replace(
          'echo "${CHROME_SHA256}  chrome-headless-shell-linux64.zip" | sha256sum --check -',
          'echo "${CHROME_SHA256}  chrome-headless-shell-linux64.zip" | sha256sum --check - ; true',
        ),
    ],
    [
      "an apt-get download",
      (text: string) =>
        text.replace(
          "rm -rf /var/lib/apt/lists/*",
          "apt-get download libfoo; rm -rf /var/lib/apt/lists/*",
        ),
    ],
    [
      "a COPY from another image",
      (text: string) =>
        text.replace(
          "USER node",
          `COPY --from=busybox@sha256:${"0".repeat(64)} /bin/sh /bin/sh\nUSER node`,
        ),
    ],
    ["any ADD", (text: string) => text.replace("USER node", "ADD local.tar /opt/\nUSER node")],
    [
      "a checksum chain that ends in || true",
      (text: string) =>
        text.replace(
          'echo "${CALADEA_BOLDITALIC_SHA256}  Caladea-BoldItalic.ttf" | sha256sum --check -',
          'echo "${CALADEA_BOLDITALIC_SHA256}  Caladea-BoldItalic.ttf" | sha256sum --check - || true',
        ),
    ],
    [
      "a RUN heredoc",
      (text: string) =>
        text.replace("USER node", "RUN <<EOT\ncurl https://example.org/x\nEOT\nUSER node"),
    ],
    [
      "a bind mount of another image",
      (text: string) =>
        text.replace(
          "RUN set -eu; \\\n    unzip",
          "RUN --mount=type=bind,from=busybox,target=/b set -eu; \\\n    unzip",
        ),
    ],
    [
      "an npm install",
      (text: string) => text.replace("USER node", "RUN npm install -g something\nUSER node"),
    ],
    [
      "an apt source trusted without a signature",
      (text: string) =>
        text.replace('"Check-Valid-Until: no" \\\n      "" \\', '"Trusted: yes" \\\n      "" \\'),
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
