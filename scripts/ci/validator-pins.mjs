import { readFileSync } from "node:fs";
import path from "node:path";

// Reading the official validator's pins from the files that define them, so that the CI gate
// (scripts/ci/official-validate.mjs) and the deployed sidecar (Dockerfile.validator) cannot
// disagree: one Dockerfile and one package list, read by both. Every reader here throws on a
// shape it does not recognise rather than half-reading it.

export const PACKAGE_LOCK = "fhir/validator-packages.lock";
const REGISTRY = "https://packages2.fhir.org/packages";

// Comment lines dropped and continuation lines joined, as the builder reads them.
export function instructions(text) {
  const joined = [];
  let open = null;
  for (const line of text.split(/\r?\n/)) {
    if (/^\s*#/.test(line)) continue;
    const continued = /\\\s*$/.test(line);
    const body = continued ? line.replace(/\\\s*$/, "") : line;
    if (open === null) open = body;
    else open += ` ${body.trim()}`;
    if (!continued) {
      joined.push(open);
      open = null;
    }
  }
  if (open !== null) joined.push(open);
  return joined;
}

export const SHA256_HEX = /^[0-9a-f]{64}$/;

// The pins: ARG name=value lines, the `curl ... "<url>" -o <file> && echo "${SHA} <file>" |
// sha256sum --check` pairs of the RUN instruction with ${ARG} substituted, and the sidecar's
// validator flags from the CMD line. Each is required; a Dockerfile that has drifted from this
// shape fails here rather than being silently half-read.
export function readSidecarPins(dockerfile) {
  const lines = instructions(readFileSync(dockerfile, "utf8"));
  const name = path.basename(dockerfile);

  const args = new Map();
  for (const line of lines) {
    const match = /^\s*ARG\s+([A-Z0-9_]+)=(\S+)\s*$/.exec(line);
    if (match !== null) args.set(match[1], match[2]);
  }
  if (args.size === 0) throw new Error(`${name}: no ARG name=value line could be parsed`);

  const substitute = (text) =>
    text.replace(/\$\{([A-Z0-9_]+)\}/g, (_, key) => {
      const value = args.get(key);
      if (value === undefined) throw new Error(`${name}: \${${key}} is not declared by an ARG`);
      return value;
    });

  const artefacts = [];
  const download =
    /curl\s+(?:-\S+\s+)*"([^"]+)"\s+-o\s+(\S+)\s+&&\s+echo\s+"\$\{([A-Z0-9_]+)\}\s+(\S+)"\s+\|\s+sha256sum\s+--check/g;
  for (const line of lines) {
    if (!/^\s*RUN\b/.test(line)) continue;
    for (const match of line.matchAll(download)) {
      const [, url, file, checksumArg, checkedFile] = match;
      if (file !== checkedFile) {
        throw new Error(`${name}: ${file} is downloaded but ${checkedFile} is checksummed`);
      }
      const sha256 = args.get(checksumArg);
      if (sha256 === undefined || !SHA256_HEX.test(sha256)) {
        throw new Error(`${name}: ARG ${checksumArg} is missing or is not a SHA-256 hex digest`);
      }
      artefacts.push({ file, url: substitute(url), sha256 });
    }
  }
  const jar = artefacts.find(({ file }) => file === "validator_cli.jar");
  if (jar === undefined) throw new Error(`${name}: no checksummed validator_cli.jar download`);

  const cmd = lines.find((line) => /^\s*CMD\s+\[/.test(line));
  if (cmd === undefined) throw new Error(`${name}: no CMD [...] line`);
  let tokens;
  try {
    tokens = JSON.parse(cmd.replace(/^\s*CMD\s+/, ""));
  } catch {
    throw new Error(`${name}: the CMD line is not a JSON array`);
  }
  // The server-mode arguments (subcommand, port, -allowNetworkAccess) are the sidecar's; the
  // validation arguments are shared with CLI mode and are what is reproduced here.
  const flags = [];
  const packages = [];
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (
      token === "-version" ||
      token === "-tx" ||
      token === "-jurisdiction" ||
      token === "-locale"
    ) {
      const value = tokens[index + 1];
      if (typeof value !== "string") throw new Error(`${name}: CMD ${token} has no value`);
      flags.push(token, value);
      index += 1;
    } else if (token === "-no-http-access") {
      flags.push(token);
    } else if (token === "-ig") {
      const value = tokens[index + 1];
      if (typeof value !== "string") throw new Error(`${name}: CMD -ig has no value`);
      const file = path.posix.basename(value);
      if (!artefacts.some((artefact) => artefact.file === file)) {
        throw new Error(`${name}: CMD loads ${value}, which the RUN line does not download`);
      }
      packages.push(file);
      index += 1;
    }
  }
  for (const required of ["-version", "-tx", "-jurisdiction", "-locale", "-no-http-access"]) {
    if (!flags.includes(required)) throw new Error(`${name}: CMD does not carry ${required}`);
  }
  if (packages.length === 0) throw new Error(`${name}: CMD loads no -ig package`);
  // The same packages, each once: compared as sets, with a repeat on either side refused. A count
  // alone passed a CMD that loaded one package twice and another not at all (audit B08, D-7).
  const expectedPackages = artefacts
    .filter(({ file }) => file !== "validator_cli.jar")
    .map(({ file }) => file);
  for (const [list, where] of [
    [packages, "CMD loads"],
    [expectedPackages, "the RUN line downloads"],
  ]) {
    const repeated = list.filter((file, index) => list.indexOf(file) !== index);
    if (repeated.length > 0) {
      throw new Error(`${name}: ${where} ${[...new Set(repeated)].join(", ")} more than once`);
    }
  }
  const unloaded = expectedPackages.filter((file) => !packages.includes(file));
  if (unloaded.length > 0 || packages.length !== expectedPackages.length) {
    throw new Error(
      `${name}: CMD loads ${packages.length} packages but the RUN line downloads ${expectedPackages.length}; not loaded: ${unloaded.join(", ") || "none"}`,
    );
  }

  // The JVM system properties of the sidecar's ENTRYPOINT. The package cache location and the
  // closed proxy are what make it hermetic, so their absence is an error, not a default.
  const entrypoint = lines.find((line) => /^\s*ENTRYPOINT\s+\[/.test(line));
  if (entrypoint === undefined) throw new Error(`${name}: no ENTRYPOINT [...] line`);
  let entryTokens;
  try {
    entryTokens = JSON.parse(entrypoint.replace(/^\s*ENTRYPOINT\s+/, ""));
  } catch {
    throw new Error(`${name}: the ENTRYPOINT line is not a JSON array`);
  }
  const jvmProperties = entryTokens.filter(
    (token) => typeof token === "string" && token.startsWith("-D"),
  );
  for (const required of HERMETIC_PROPERTIES) {
    if (!jvmProperties.includes(required)) {
      throw new Error(
        `${name}: ENTRYPOINT does not set ${required}, so the validator can reach the network`,
      );
    }
  }
  if (!jvmProperties.some((token) => token.startsWith("-Duser.home="))) {
    throw new Error(
      `${name}: ENTRYPOINT does not set -Duser.home, so the installed package cache is not used`,
    );
  }
  if (
    !lines.some((line) =>
      new RegExp(`^\\s*COPY\\s+${PACKAGE_LOCK.replace(/\./g, "\\.")}\\s`).test(line),
    )
  ) {
    throw new Error(
      `${name}: does not COPY ${PACKAGE_LOCK}, so the image and the gate could use different packages`,
    );
  }

  return {
    version: args.get("VALIDATOR_VERSION") ?? "unknown",
    artefacts,
    flags,
    packages,
    jvmProperties,
  };
}

// The properties that give the validator no route out. Any outbound HTTP(S) request goes to a
// closed local port and fails, so an unlisted dependency surfaces as an error, never a download.
export const HERMETIC_PROPERTIES = [
  "-Djava.net.useSystemProxies=false",
  "-Dhttp.proxyHost=127.0.0.1",
  "-Dhttp.proxyPort=9",
  "-Dhttps.proxyHost=127.0.0.1",
  "-Dhttps.proxyPort=9",
];

const PACKAGE_ID = /^[a-z][a-z0-9-]*(\.[a-z0-9-]+)+$/;
const VERSION = /^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$/;

// fhir/validator-packages.lock: `<id> <version> <sha256>` per line, `#` comments and blank
// lines ignored. Each entry becomes the registry URL it is downloaded from.
export function readPackageLock(file) {
  const entries = [];
  const seen = new Set();
  const lines = readFileSync(file, "utf8").split(/\r?\n/);
  lines.forEach((raw, index) => {
    const line = raw.trim();
    if (line === "" || line.startsWith("#")) return;
    const fields = line.split(/\s+/);
    const where = `${path.basename(file)}:${index + 1}`;
    if (fields.length !== 3) throw new Error(`${where}: expected <id> <version> <sha256>`);
    const [id, version, sha256] = fields;
    if (!PACKAGE_ID.test(id)) throw new Error(`${where}: ${id} is not a package id`);
    if (!VERSION.test(version)) throw new Error(`${where}: ${version} is not a version`);
    if (!SHA256_HEX.test(sha256)) throw new Error(`${where}: not a SHA-256 hex digest`);
    const key = `${id}#${version}`;
    if (seen.has(key)) throw new Error(`${where}: ${key} is listed twice`);
    seen.add(key);
    entries.push({ id, version, sha256, key, url: `${REGISTRY}/${id}/${version}` });
  });
  if (entries.length === 0) throw new Error(`${path.basename(file)} lists no packages`);
  return entries;
}

// How the validator's output shows it relating to the network, sorted three ways:
//
//   installs — "Installing <id>#<version> to the package cache": a package it needed was not
//              already installed. With the cache seeded from the list, any install means the
//              list is incomplete, and it fails the gate.
//   refused  — a request the validator's own `-no-http-access` policy refused before any socket
//              was opened. Two forms: "Error fetching <url>: Access to the internet is not
//              allowed by local security policy", and a bare "Failed to determine latest version
//              of package <id> from server: <server>" with no connection error after it. The
//              second is the engine asking for the newest hl7.terminology — no package declares
//              that dependency; every declared version is exact — and falling back to the newest
//              in the cache, which is the pinned one. Refusing it is what keeps the result
//              reproducible: online, the engine would take whatever the registry had published.
//              The gate's Package Summary check proves the fallback was pinned. Reported, not
//              failed.
//   other    — any other fetch error: an attempt that got as far as a socket. With the policy
//              on there should be none, so any fails the gate.
export function networkUse(lines) {
  const installs = [];
  const refused = [];
  const other = [];
  for (const line of lines) {
    if (/Installing \S+ to the package cache/.test(line)) installs.push(line);
    else if (
      /Error fetching|Failed to determine latest version of package|Failed to connect/.test(line)
    ) {
      const policy = /not allowed by local security policy/.test(line);
      const bareLatestLookup =
        /Failed to determine latest version of package \S+ from server: \S+\s*$/.test(line);
      if (policy || bareLatestLookup) refused.push(line);
      else other.push(line);
    }
  }
  return { installs, refused, other };
}

// The validator's own list of what it loaded: "Package Summary: [id#version, ...]". Undefined
// when the line is absent, which the gate treats as no verdict rather than as an empty set.
export function packageSummary(lines) {
  const line = lines.find((candidate) => /Package Summary:\s*\[/.test(candidate));
  if (line === undefined) return undefined;
  const inner = /Package Summary:\s*\[(.*)\]/.exec(line)?.[1] ?? "";
  return inner
    .split(",")
    .map((entry) => entry.trim())
    .filter((entry) => entry !== "");
}

// The verdict on the validator image started with no network at all (cloudbuild.images.yaml,
// step validator-offline-verdict), from its log. It sorts each line with networkUse above, so
// the image build and the CI gate cannot disagree about what counts as reaching for the network
// (until audit B08 the build repeated this classification in grep). Fails on any install, on any
// fetch that got as far as a socket, on a validator that never started, and on a jurisdiction or
// locale other than the pinned ones. `evidence` is the log lines worth keeping, cut to 200
// characters: what it loaded and how it was configured.
export function offlineStartVerdict(lines) {
  const { installs, other } = networkUse(lines);
  const failures = [];
  if (installs.length > 0) {
    failures.push("The validator needed a package that is not installed in the image.");
  }
  if (other.length > 0) {
    failures.push("The validator attempted a network request that its policy did not refuse.");
  }
  if (!lines.some((line) => line.includes("FHIR Validator HTTP Service started"))) {
    failures.push("The validator did not start with networking disabled.");
  }
  if (!lines.some((line) => line.includes("Jurisdiction: Global (Whole world)"))) {
    failures.push("The validator is not validating under universal jurisdiction.");
  }
  if (!lines.some((line) => line.includes("Locale: United States/US"))) {
    failures.push("The validator is not running in the pinned locale.");
  }
  const evidence = lines
    .filter((line) => /Jurisdiction:|Locale:|Package Summary|HTTP Service started/.test(line))
    .map((line) => line.slice(0, 200));
  return { ok: failures.length === 0, failures, installs, other, evidence };
}
