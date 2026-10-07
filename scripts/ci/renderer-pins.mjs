import { readFileSync } from "node:fs";
import path from "node:path";

import { args, checksummedDownloads, instructions } from "./dockerfile.mjs";

// Reading the renderer image's pins from Dockerfile.renderer, the one file that defines them
// (docs/design/authority-import-renderer.md, R6 and R9): the base image, the Debian snapshot,
// chrome-headless-shell and every font, each download with its SHA-256. The gate's lock and its
// tests read them here, so they cannot disagree with the image. It throws on a shape it does not
// recognise and on the plausible mistakes below. It catches honest mistakes; a disguised edit is
// for review (owner decision O3).

export const RENDERER_DOCKERFILE = "Dockerfile.renderer";
export const FONTCONFIG = "src/render/image/fonts.conf";

const FONT_FILES = [
  "Carlito-Regular.ttf",
  "Carlito-Bold.ttf",
  "Carlito-Italic.ttf",
  "Carlito-BoldItalic.ttf",
  "Caladea-Regular.ttf",
  "Caladea-Bold.ttf",
  "Caladea-Italic.ttf",
  "Caladea-BoldItalic.ttf",
];

export function readRendererPins(dockerfile = RENDERER_DOCKERFILE) {
  const lines = instructions(readFileSync(dockerfile, "utf8")).map(({ text }) => text);
  const name = path.basename(dockerfile);
  const fail = (reason) => {
    throw new Error(`${name}: ${reason}`);
  };

  // The renderer is the first stage, from one base pinned by digest. The Word drawing's stages
  // follow it (docs/design/certified-word-drawing.md): each from that stage or an image pinned by
  // digest, never a base of its own the renderer could be built from.
  const froms = lines.filter((line) => /^\s*FROM\s/i.test(line));
  const base = /^\s*FROM\s+(\S+@sha256:[0-9a-f]{64})\s+AS\s+renderer\s*$/i.exec(
    froms[0] ?? "",
  )?.[1];
  if (base === undefined) fail("expected the first FROM pinned by digest, AS renderer");
  for (const from of froms.slice(1)) {
    if (!/^\s*FROM\s+(?:renderer|\S+@sha256:[0-9a-f]{64})\s+AS\s+\S+\s*$/i.test(from)) {
      fail(`a later stage from neither the renderer nor an image pinned by digest: ${from}`);
    }
  }

  const declared = args(lines, name);
  const required = (key, pattern) => {
    const value = declared.get(key);
    if (value === undefined || !pattern.test(value)) fail(`ARG ${key} is missing or malformed`);
    return value;
  };
  const debianSnapshot = required("DEBIAN_SNAPSHOT", /^\d{8}T\d{6}Z$/);
  const chromeVersion = required("CHROME_VERSION", /^\d+\.\d+\.\d+\.\d+$/);
  const googleFontsCommit = required("GOOGLE_FONTS_COMMIT", /^[0-9a-f]{40}$/);

  // Debian packages come from the dated snapshot only: no apt install outside the RUN that writes
  // the snapshot's sources.
  const runs = lines.filter((line) => /^\s*RUN\b/.test(line));
  const snapshot = runs.filter((line) =>
    line.includes("snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}"),
  );
  if (snapshot.length !== 1) fail("expected one RUN that installs from the Debian snapshot");
  if (
    runs.some((line) => line !== snapshot[0] && /\bapt(?:-get)?\s+(?:-\S+\s+)*install\b/.test(line))
  ) {
    fail("an apt install outside the snapshot's RUN");
  }

  // Every download is a checksummed curl over HTTPS: no bare curl, no wget, no ADD.
  const artefacts = checksummedDownloads(lines, declared, name);
  if (
    (runs.join("\n").match(/\bcurl\s+(?:[-"'$]|https?:\/\/)/g) ?? []).length !== artefacts.length
  ) {
    fail("a curl download without a SHA-256 check");
  }
  if (lines.some((line) => /\bwget\b|^\s*ADD\s/i.test(line))) {
    fail("a download by wget or ADD; use a checksummed curl");
  }
  for (const { file, url } of artefacts) {
    if (!url.startsWith("https://")) fail(`${file} is not fetched over HTTPS`);
  }

  const chrome = artefacts.find(({ file }) => file === "chrome-headless-shell-linux64.zip");
  if (!chrome?.url.includes(`/chrome-for-testing-public/${chromeVersion}/linux64/`)) {
    fail("chrome-headless-shell is not downloaded at CHROME_VERSION");
  }
  if (!artefacts.some(({ file }) => file === "liberation-fonts-ttf.tar.gz")) {
    fail("no checksummed Liberation fonts download");
  }
  const fonts = FONT_FILES.map((file) => {
    const artefact = artefacts.find((candidate) => candidate.file === file);
    if (!artefact?.url.includes(`/google/fonts/${googleFontsCommit}/`)) {
      fail(`${file} is not downloaded at GOOGLE_FONTS_COMMIT`);
    }
    return artefact;
  });
  if (artefacts.length !== 2 + FONT_FILES.length) {
    fail(`downloads ${artefacts.length} files; the pins name ${2 + FONT_FILES.length}`);
  }
  if (!lines.some((line) => line.startsWith(`COPY ${FONTCONFIG} `))) {
    fail(`does not COPY ${FONTCONFIG}, the fontconfig the gate reads`);
  }

  return { base, debianSnapshot, chromeVersion, fonts, artefacts };
}
