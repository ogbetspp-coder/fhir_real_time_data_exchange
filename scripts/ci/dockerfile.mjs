// One Dockerfile reader for the pin readers (validator-pins.mjs, renderer-pins.mjs), the image
// check (check-dockerfiles.mjs) and the legacy-builder guard (test/ci/images.test.ts). Plain
// JavaScript on Node's built-ins, so the CI gate runs it with node alone. It catches honest
// mistakes; a disguised edit is for review (owner decision O3).

export const SHA256_HEX = /^[0-9a-f]{64}$/;

// The instructions, each with its first physical line, joined as moby's parser joins them: a
// trailing backslash (and any spaces or tabs after it) removed and the next line appended with no
// space added; comment and blank lines dropped, inside a continuation too.
export function instructions(text) {
  const joined = [];
  let open = null;
  text.split(/\r?\n/).forEach((line, index) => {
    if (/^\s*(?:#|$)/.test(line)) return;
    const continued = /\\[ \t]*$/.test(line);
    const body = continued ? line.replace(/\\[ \t]*$/, "") : line;
    if (open === null) open = { line: index + 1, text: body };
    else open.text += body;
    if (!continued) {
      joined.push(open);
      open = null;
    }
  });
  if (open !== null) joined.push(open);
  return joined;
}

// Every `ARG NAME=value`, by name. A name declared twice is refused.
export function args(lines, name) {
  const found = new Map();
  for (const line of lines) {
    const match = /^\s*ARG\s+([A-Z0-9_]+)=(\S+)\s*$/.exec(line);
    if (match === null) continue;
    if (found.has(match[1])) throw new Error(`${name}: ARG ${match[1]} is declared twice`);
    found.set(match[1], match[2]);
  }
  return found;
}

// `text` with each ${ARG} replaced by its value. An undeclared one is refused.
function substitute(text, declared, name) {
  return text.replace(/\$\{([A-Z0-9_]+)\}/g, (_, key) => {
    const value = declared.get(key);
    if (value === undefined) throw new Error(`${name}: \${${key}} is not declared by an ARG`);
    return value;
  });
}

// Each `curl ... "<url>" -o <file> && echo "${SHA} <file>" | sha256sum --check` pair on a RUN line,
// as { file, url, sha256 }, the URL's ${ARG}s substituted. The checksum must name the file
// downloaded and its ARG must be a SHA-256.
const DOWNLOAD =
  /curl\s+(?:-\S+\s+)*"([^"]+)"\s+-o\s+(\S+)\s+&&\s+echo\s+"\$\{([A-Z0-9_]+)\}\s+(\S+)"\s+\|\s+sha256sum\s+--check/g;
export function checksummedDownloads(lines, declared, name) {
  const artefacts = [];
  for (const line of lines.filter((candidate) => /^\s*RUN\b/.test(candidate))) {
    for (const [, url, file, checksumArg, checkedFile] of line.matchAll(DOWNLOAD)) {
      if (file !== checkedFile) {
        throw new Error(`${name}: ${file} is downloaded but ${checkedFile} is checksummed`);
      }
      const sha256 = declared.get(checksumArg);
      if (sha256 === undefined || !SHA256_HEX.test(sha256)) {
        throw new Error(`${name}: ARG ${checksumArg} is missing or is not a SHA-256 hex digest`);
      }
      artefacts.push({ file, url: substitute(url, declared, name), sha256 });
    }
  }
  return artefacts;
}

// Each file a RUN line checks with `echo "${SHA} <file>" | sha256sum --check` that a `COPY <source>
// <destination>` put there from the build context, as { file, path, sha256 }: the repository's own
// package, which is built and committed, not downloaded. Its ARG must be a SHA-256.
const CHECK = /echo\s+"\$\{([A-Z0-9_]+)\}\s+(\S+)"\s+\|\s+sha256sum\s+--check/g;
export function checksummedCopies(lines, declared, name) {
  const copied = new Map();
  for (const line of lines) {
    const copy = /^\s*COPY\s+([^-\s]\S*)\s+(\S+)\s*$/.exec(line);
    if (copy !== null) copied.set(copy[2].split("/").at(-1), copy[1]);
  }
  const artefacts = [];
  for (const line of lines.filter((candidate) => /^\s*RUN\b/.test(candidate))) {
    for (const [, checksumArg, file] of line.matchAll(CHECK)) {
      const source = copied.get(file);
      if (source === undefined) continue;
      const sha256 = declared.get(checksumArg);
      if (sha256 === undefined || !SHA256_HEX.test(sha256)) {
        throw new Error(`${name}: ARG ${checksumArg} is missing or is not a SHA-256 hex digest`);
      }
      artefacts.push({ file, path: source, sha256 });
    }
  }
  return artefacts;
}
