import { readFileSync } from "node:fs";
import path from "node:path";

import { instructions, SHA256_HEX } from "./validator-pins.mjs";

// Reading the renderer image's pins from Dockerfile.renderer, the one file that defines them
// (docs/design/authority-import-renderer.md, R6 and R9): the base image, the Debian snapshot,
// chrome-headless-shell and every font, each download with its SHA-256. The gate's lock and its
// tests read them here, so they cannot disagree with the image. Every reader throws on a shape it
// does not recognise rather than half-reading it.

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
  const lines = instructions(readFileSync(dockerfile, "utf8"));
  const name = path.basename(dockerfile);

  const froms = lines.filter((line) => /^\s*FROM\s/i.test(line));
  if (froms.length !== 1)
    throw new Error(`${name}: expected exactly one FROM, found ${froms.length}`);
  const base = /^\s*FROM\s+(\S+@sha256:[0-9a-f]{64})\s*$/i.exec(froms[0])?.[1];
  if (base === undefined) throw new Error(`${name}: FROM is not an image pinned by digest`);

  const args = new Map();
  for (const line of lines) {
    const match = /^\s*ARG\s+([A-Z0-9_]+)=(\S+)\s*$/.exec(line);
    if (match !== null) {
      if (args.has(match[1])) throw new Error(`${name}: ARG ${match[1]} is declared twice`);
      args.set(match[1], match[2]);
    }
  }
  const required = (key, pattern) => {
    const value = args.get(key);
    if (value === undefined || !pattern.test(value)) {
      throw new Error(`${name}: ARG ${key} is missing or malformed`);
    }
    return value;
  };
  const debianSnapshot = required("DEBIAN_SNAPSHOT", /^\d{8}T\d{6}Z$/);
  // Each suite's Release date, which the build holds apt's Release to (a downgrade fails it):
  // pinned, and read by a RUN line.
  const debianReleases = Object.fromEntries(
    [
      ["bookworm", "DEBIAN_BOOKWORM_DATE"],
      ["bookworm-updates", "DEBIAN_BOOKWORM_UPDATES_DATE"],
      ["bookworm-security", "DEBIAN_BOOKWORM_SECURITY_DATE"],
    ].map(([suite, key]) => {
      const date = required(key, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
      if (!lines.some((line) => /^\s*RUN\b/.test(line) && line.includes(`"${suite} \${${key}}"`))) {
        throw new Error(`${name}: ARG ${key} is not checked against ${suite}'s Release`);
      }
      return [suite, date];
    }),
  );
  const chromeVersion = required("CHROME_VERSION", /^\d+\.\d+\.\d+\.\d+$/);
  const googleFontsCommit = required("GOOGLE_FONTS_COMMIT", /^[0-9a-f]{40}$/);

  const substitute = (text) =>
    text.replace(/\$\{([A-Z0-9_]+)\}/g, (_, key) => {
      const value = args.get(key);
      if (value === undefined) throw new Error(`${name}: \${${key}} is not declared by an ARG`);
      return value;
    });

  const artefacts = [];
  const download =
    /curl\s+(?:-fsSL|-fL)\s+"([^"]+)"\s+-o\s+(\S+)\s+&&\s+echo\s+"\$\{([A-Z0-9_]+)\}\s+(\S+)"\s+\|\s+sha256sum\s+--check/g;
  for (const line of lines) {
    // Common ways a download could reach the image unchecked are refused here: an ADD, another
    // image's files, fetchers other than a checksummed curl, heredocs this reader cannot see into,
    // and a checksum whose failure could be swallowed. It is a denylist, not a proof: the control
    // is the review of Dockerfile.renderer itself (CODEOWNERS is recommended, the renderer note's
    // stated residuals), which this
    // reader supports by catching the usual mistakes.
    if (/^\s*ADD\s/i.test(line)) throw new Error(`${name}: ADD; copy local files with COPY`);
    if (/^\s*COPY\s.*--from/i.test(line)) throw new Error(`${name}: COPY --from another image`);
    if (!/^\s*RUN\b/.test(line)) continue;
    if (/<<-?\s*['"]?\w+/.test(line) || /--mount\b/.test(line)) {
      throw new Error(`${name}: a RUN heredoc or --mount, which this reader cannot check`);
    }
    if (
      /\bnpm\s+(?:i|install|ci|exec)\b|\bnpx\s|\bperl\s+-|\/dev\/tcp\b|\bhttps?\.get\b|\bnode\s+(?:-[ep]\b|--eval|--print|--input-type)|\|\s*node\b|\bgit\s|\bruby\s+-e\b/.test(
        line,
      )
    ) {
      throw new Error(
        `${name}: a RUN line can download with something other than a checksummed curl`,
      );
    }
    if (
      /\bTrusted:\s*yes\b|\[trusted=yes\]|allow-unauthenticated|AllowInsecureRepositories/i.test(
        line,
      )
    ) {
      throw new Error(`${name}: an apt source or install that skips signature checks`);
    }
    if (/sha256sum/.test(line) && /\|\|/.test(line)) {
      throw new Error(`${name}: a RUN line that checks a checksum may not use ||`);
    }
    if (
      /\bwget\b|\bgit\s+clone\b|\bfetch\s*\(|\bnode\s+-e\b|\bpython3?\s+-c\b|\bapt-get\s+download\b/.test(
        line,
      )
    ) {
      throw new Error(`${name}: a RUN line downloads with something other than a checksummed curl`);
    }
    // A checksum's failure must stop the build: each check (`sha256sum --check -`) is followed by
    // `&&` or ends the instruction.
    const unchecked = [...line.matchAll(/sha256sum\s+--check(?:\s+-(?=\s|$))?\s*(\S*)/g)].some(
      ([, next]) => next !== "&&" && next !== "",
    );
    if (unchecked) {
      throw new Error(
        `${name}: a checksum failure could be ignored (sha256sum --check not followed by &&)`,
      );
    }
    // Every curl, however its arguments start and wherever it stands (a command substitution
    // included), is one of the checksummed pairs; only plain package names after apt-get install
    // or purge are not invocations.
    const invocations = line
      .replace(
        /apt-get\s+(?:install|purge)(?:\s+(?:--?[a-z-]+|[a-z0-9][a-z0-9.+:-]*(?=\s|;|$)))*/g,
        "apt-get",
      )
      .match(/\bcurl\b/g);
    const curls = invocations === null ? 0 : invocations.length;
    const pairs = [...line.matchAll(download)];
    if (curls !== pairs.length) {
      throw new Error(`${name}: a RUN line downloads with curl without a SHA-256 check`);
    }
    for (const [, url, file, checksumArg, checkedFile] of pairs) {
      if (file !== checkedFile) {
        throw new Error(`${name}: ${file} is downloaded but ${checkedFile} is checksummed`);
      }
      const sha256 = args.get(checksumArg);
      if (sha256 === undefined || !SHA256_HEX.test(sha256)) {
        throw new Error(`${name}: ARG ${checksumArg} is missing or is not a SHA-256 hex digest`);
      }
      const resolved = substitute(url);
      if (!resolved.startsWith("https://"))
        throw new Error(`${name}: ${file} is not fetched over HTTPS`);
      if (artefacts.some((artefact) => artefact.file === file)) {
        throw new Error(`${name}: ${file} is downloaded twice`);
      }
      artefacts.push({ file, url: resolved, sha256 });
    }
  }

  const chrome = artefacts.find(({ file }) => file === "chrome-headless-shell-linux64.zip");
  if (chrome === undefined)
    throw new Error(`${name}: no checksummed chrome-headless-shell download`);
  if (!chrome.url.includes(`/chrome-for-testing-public/${chromeVersion}/linux64/`)) {
    throw new Error(`${name}: chrome-headless-shell is not downloaded at CHROME_VERSION`);
  }
  const liberation = artefacts.find(({ file }) => file === "liberation-fonts-ttf.tar.gz");
  if (liberation === undefined)
    throw new Error(`${name}: no checksummed Liberation fonts download`);
  const fonts = FONT_FILES.map((file) => {
    const artefact = artefacts.find((candidate) => candidate.file === file);
    if (artefact === undefined) throw new Error(`${name}: no checksummed ${file} download`);
    if (!artefact.url.includes(`/google/fonts/${googleFontsCommit}/`)) {
      throw new Error(`${name}: ${file} is not fetched at GOOGLE_FONTS_COMMIT`);
    }
    return artefact;
  });
  if (artefacts.length !== 2 + FONT_FILES.length) {
    throw new Error(
      `${name}: downloads ${artefacts.length} files; the pins name ${2 + FONT_FILES.length}`,
    );
  }
  if (
    !lines.some((line) =>
      new RegExp(`^\\s*COPY\\s+${FONTCONFIG.replace(/\./g, "\\.")}\\s`).test(line),
    )
  ) {
    throw new Error(
      `${name}: does not COPY ${FONTCONFIG}, so the image and the gate could use different fontconfigs`,
    );
  }

  return {
    base,
    debianSnapshot,
    debianReleases,
    chromeVersion,
    googleFontsCommit,
    chrome,
    liberation,
    fonts,
    artefacts,
  };
}
