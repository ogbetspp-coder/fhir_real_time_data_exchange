#!/usr/bin/env node
// Pin freshness (audit B07, S-2): every pin that Dependabot does not move, read from the file
// that defines it, compared with what upstream publishes now. Dependabot moves the Actions, the
// npm and uv lockfiles, the Terraform providers and every Dockerfile FROM digest; nothing moved
// these, so a pin could rot for months without anyone being told (docs/foundations.md, C3).
//
//   node scripts/ci/pin-freshness.mjs [--report <file>]
//
// Exits 0 when every pin is current, 1 when any is behind or could not be checked (a lookup that
// fails is reported, never counted as current). It changes nothing: moving a pin is a reviewed
// pull request, with its checksum re-measured. `.github/workflows/pin-freshness.yml` runs it
// weekly and opens an issue on a non-zero exit. Node built-ins only; GITHUB_TOKEN, when set, is
// sent to api.github.com alone, to lift its anonymous rate limit.
//
// Not compared: the Google Fonts commit and the Debian snapshot's suites, which are content
// pinned by checksum and move only with the renderer gate's own review (the snapshot's age is
// reported), and every pin Dependabot already moves.

import { readFileSync, writeFileSync } from "node:fs";

import { readRendererPins } from "./renderer-pins.mjs";
import { readSidecarPins } from "./validator-pins.mjs";

// One match of `pattern` in `file`, or a thrown error naming both: a reader never guesses.
function capture(file, pattern) {
  const text = readFileSync(file, "utf8");
  const found = [
    ...text.matchAll(new RegExp(pattern.source, `${pattern.flags.replace("g", "")}g`)),
  ];
  const values = [...new Set(found.map((match) => match[1]))];
  if (values.length !== 1) {
    throw new Error(`${file}: expected one value for ${pattern}, found ${values.length}`);
  }
  return values[0];
}

const DAY_MS = 24 * 60 * 60 * 1000;
// How old the renderer's Debian snapshot may be before it is reported: its security suite is
// frozen at that date.
export const SNAPSHOT_MAX_AGE_DAYS = 45;

// Every pin: where it is defined, how to read it, and how to ask upstream. `compare` says whether
// the pinned value is current given upstream's answer; the default is equality.
export function pins() {
  const renderer = () => readRendererPins();
  return [
    {
      name: "HL7 FHIR validator (validator_cli.jar)",
      where: "Dockerfile.validator",
      read: () => readSidecarPins("Dockerfile.validator").version,
      upstream: { github: "hapifhir/org.hl7.fhir.core" },
    },
    {
      name: "chrome-headless-shell",
      where: "Dockerfile.renderer",
      read: () => renderer().chromeVersion,
      upstream: { chrome: "Stable" },
    },
    {
      name: "Liberation fonts",
      where: "Dockerfile.renderer",
      read: () => capture("Dockerfile.renderer", /^ARG LIBERATION_VERSION=(\S+)$/m),
      upstream: { github: "liberationfonts/liberation-fonts" },
    },
    {
      name: "Debian snapshot (renderer)",
      where: "Dockerfile.renderer",
      read: () => renderer().debianSnapshot,
      upstream: { age: SNAPSHOT_MAX_AGE_DAYS },
    },
    {
      name: "OSV-Scanner",
      where: "scripts/ci/vuln-scan.sh",
      read: () => capture("scripts/ci/vuln-scan.sh", /^VERSION="(v[^"]+)"$/m),
      upstream: { github: "google/osv-scanner" },
    },
    {
      name: "gitleaks",
      where: "scripts/ci/secret-scan.sh",
      read: () => capture("scripts/ci/secret-scan.sh", /^VERSION="([^"]+)"$/m),
      upstream: { github: "gitleaks/gitleaks", strip: "v" },
    },
    {
      name: "uv",
      where: ".github/workflows/ci.yml",
      read: () => capture(".github/workflows/ci.yml", /^\s+version: "(\d+\.\d+\.\d+)"$/m),
      upstream: { github: "astral-sh/uv" },
    },
    {
      name: "Terraform",
      where: ".github/workflows/deploy.yml",
      read: () => capture(".github/workflows/deploy.yml", /terraform_version: (\S+)/),
      upstream: { terraform: true },
    },
    {
      name: "Google Cloud SDK (setup-gcloud version)",
      where: ".github/workflows/deploy.yml",
      read: () => capture(".github/workflows/deploy.yml", /^\s+version: "(\d+\.\d+\.\d+)"$/m),
      upstream: { cloudSdk: true },
    },
    {
      name: "Cloud Build docker builder",
      where: "cloudbuild.images.yaml",
      read: () =>
        capture("cloudbuild.images.yaml", /gcr\.io\/cloud-builders\/docker@(sha256:[0-9a-f]{64})/),
      upstream: { gcrTag: { repository: "cloud-builders/docker", tag: "29" } },
    },
    ...[
      ["google-cloud-agentplatform", /"google-cloud-agentplatform\[[^\]]*\]==([^"]+)"/],
      ["google-cloud-aiplatform", /"google-cloud-aiplatform\[[^\]]*\]==([^"]+)"/],
      ["cloudpickle", /"cloudpickle==([^"]+)"/],
    ].map(([pkg, pattern]) => ({
      name: `${pkg} (Agent Engine deploy)`,
      where: "agent/deploy/deploy_agent_engine.py",
      read: () => capture("agent/deploy/deploy_agent_engine.py", pattern),
      upstream: { pypi: pkg },
    })),
  ];
}

async function json(url, headers = {}) {
  const response = await fetch(url, { headers, signal: globalThis.AbortSignal.timeout(30_000) });
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.json();
}

// What upstream publishes now for one pin: a version, a digest, or (for `age`) the current time.
export async function latest(upstream, env = process.env) {
  if (upstream.github !== undefined) {
    const headers = { Accept: "application/vnd.github+json" };
    if (env.GITHUB_TOKEN) headers.Authorization = `Bearer ${env.GITHUB_TOKEN}`;
    const release = await json(
      `https://api.github.com/repos/${upstream.github}/releases/latest`,
      headers,
    );
    const tag = String(release.tag_name);
    return upstream.strip !== undefined && tag.startsWith(upstream.strip)
      ? tag.slice(upstream.strip.length)
      : tag;
  }
  if (upstream.chrome !== undefined) {
    const known = await json(
      "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions.json",
    );
    return known.channels[upstream.chrome].version;
  }
  if (upstream.terraform) {
    return (await json("https://checkpoint-api.hashicorp.com/v1/check/terraform")).current_version;
  }
  if (upstream.cloudSdk) {
    return (await json("https://dl.google.com/dl/cloudsdk/channels/rapid/components-2.json"))
      .version;
  }
  if (upstream.pypi !== undefined) {
    return (await json(`https://pypi.org/pypi/${upstream.pypi}/json`)).info.version;
  }
  if (upstream.gcrTag !== undefined) {
    const { repository, tag } = upstream.gcrTag;
    const token = (await json(`https://gcr.io/v2/token?scope=repository:${repository}:pull`)).token;
    const response = await fetch(`https://gcr.io/v2/${repository}/manifests/${tag}`, {
      method: "HEAD",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept:
          "application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.docker.distribution.manifest.v2+json",
      },
      signal: globalThis.AbortSignal.timeout(30_000),
    });
    const digest = response.headers.get("docker-content-digest");
    if (!response.ok || digest === null) {
      throw new Error(`gcr.io/${repository}:${tag}: no digest (HTTP ${response.status})`);
    }
    return digest;
  }
  if (upstream.age !== undefined) return new Date().toISOString();
  throw new Error("a pin with no upstream");
}

// `current`, `behind` (with what upstream has) or `unchecked` (with why), for one pin.
export function verdict(pin, pinned, upstreamValue) {
  if (pin.upstream.age !== undefined) {
    const stamp = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/.exec(pinned);
    if (stamp === null) return { status: "unchecked", detail: `${pinned} is not a snapshot time` };
    const [, year, month, day, hour, minute, second] = stamp;
    const taken = Date.UTC(+year, +month - 1, +day, +hour, +minute, +second);
    const days = Math.floor((Date.parse(upstreamValue) - taken) / DAY_MS);
    return days > pin.upstream.age
      ? { status: "behind", detail: `${days} days old (limit ${pin.upstream.age})` }
      : { status: "current", detail: `${days} days old` };
  }
  return pinned === upstreamValue
    ? { status: "current", detail: upstreamValue }
    : { status: "behind", detail: upstreamValue };
}

export async function check(list = pins(), lookup = latest) {
  const rows = [];
  for (const pin of list) {
    let pinned;
    try {
      pinned = pin.read();
    } catch (error) {
      rows.push({ pin, pinned: "?", status: "unchecked", detail: `unreadable: ${error.message}` });
      continue;
    }
    try {
      rows.push({ pin, pinned, ...verdict(pin, pinned, await lookup(pin.upstream)) });
    } catch (error) {
      rows.push({ pin, pinned, status: "unchecked", detail: `lookup failed: ${error.message}` });
    }
  }
  return rows;
}

export function report(rows) {
  const behind = rows.filter(({ status }) => status !== "current").length;
  const lines = [
    `#### Pin freshness: ${behind === 0 ? "every pin is current" : `${behind} of ${rows.length} pins behind or unchecked`}`,
    "",
    "| Pin | Defined in | Pinned | Upstream now | Status |",
    "|---|---|---|---|---|",
    ...rows.map(
      ({ pin, pinned, status, detail }) =>
        `| ${pin.name} | \`${pin.where}\` | ${pinned} | ${detail} | ${status} |`,
    ),
    "",
  ];
  return { text: lines.join("\n"), behind };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const at = process.argv.indexOf("--report");
  const { text, behind } = report(await check());
  console.log(text);
  if (at !== -1 && process.argv[at + 1] !== undefined) writeFileSync(process.argv[at + 1], text);
  process.exit(behind === 0 ? 0 : 1);
}
