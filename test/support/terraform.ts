import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

// Parsing just enough Terraform to answer one question: which roles does a given service
// account hold anywhere in infra/. Shared by every least-privilege test so that each boundary is
// checked by the same reader, and a weakness in it is a weakness found once.

export type TerraformBinding = { type: string; role: string };

// Reads the IAM bindings Terraform declares for one service account, and refuses to answer when
// it cannot be sure. A least-privilege test built on a reader that silently skips what it does
// not understand proves nothing: a role written as `role = each.value`, a member reached through
// a local, a grant assembled in a `data "google_iam_policy"`, or a member spelled as a literal
// e-mail would each be dropped, and "exactly these roles" would pass while being false. So every
// reference to the account anywhere in infra/ must be one of four readable forms, or this
// throws and the test fails:
//
//   1. a `member`/`members` line of an `*_iam_member|binding|policy` resource — a role the
//      account holds, whose `role` must be a quoted literal;
//   2. a `service_account_id` line of such a resource — someone else holding a role over the
//      account (the token-creator grant), which is not a permission of the account;
//   3. a line of a non-IAM resource, such as a Cloud Run service's runtime `service_account`;
//   4. an `output`, which grants nothing.
//
// And no IAM resource may name the account by a literal e-mail instead of by reference.
export function serviceAccountRoles(terraform: string, account: string): TerraformBinding[] {
  const blocks = topLevelBlocks(terraform);
  const reference = new RegExp(`google_service_account\\.${account}\\.\\w+`, "g");
  const bindings: TerraformBinding[] = [];

  for (const match of terraform.matchAll(reference)) {
    const at = match.index;
    const block = blocks.find(({ start, end }) => at >= start && at < end);
    const where = `google_service_account.${account} referenced at offset ${String(at)}`;
    if (block === undefined) throw new Error(`${where} outside any block`);
    if (block.kind === "output") continue;
    if (block.kind !== "resource") {
      throw new Error(`${where} inside a ${block.kind} block, which this reader cannot follow`);
    }
    if (block.type === `google_service_account` && block.name === account) continue;

    const line = lineAt(terraform, at);
    const isIam = /_iam_(member|binding|policy)$/.test(block.type);
    if (!isIam) continue; // form 3: a runtime identity, not a grant

    if (/^\s*service_account_id\s*=/.test(line)) continue; // form 2
    if (!/^\s*members?\s*=/.test(line)) {
      throw new Error(`${where}: ${block.type}.${block.name} uses it on an unrecognised line`);
    }
    const role = /^\s*role\s*=\s*"([^"]+)"\s*$/m.exec(block.body)?.[1];
    if (role === undefined) {
      throw new Error(`${block.type}.${block.name} grants to ${account} with a non-literal role`);
    }
    bindings.push({ type: block.type, role });
  }

  // A literal e-mail for the account in any IAM member line bypasses the reference scan above.
  const accountId = new RegExp(
    `resource\\s+"google_service_account"\\s+"${account}"\\s*\\{[^}]*?account_id\\s*=\\s*"([^"$]+)`,
    "s",
  ).exec(terraform)?.[1];
  if (accountId !== undefined) {
    for (const block of blocks) {
      if (block.kind !== "resource" || !block.type.includes("_iam_")) continue;
      if (new RegExp(`members?\\s*=[^\\n]*serviceAccount:${accountId}`).test(block.body)) {
        throw new Error(`${block.type}.${block.name} names ${account} by literal e-mail`);
      }
    }
  }
  // A policy document assembled in a data source is invisible to the scan; refuse it outright.
  if (/\bdata\s+"google_iam_policy"/.test(terraform)) {
    throw new Error('infra/ uses data "google_iam_policy", which this reader cannot follow');
  }
  return bindings;
}

type TopLevelBlock = {
  kind: string;
  type: string;
  name: string;
  body: string;
  start: number;
  end: number;
};

// Every top-level block, with its extent: resources, data sources, locals, outputs, variables,
// modules. Only resources are returned by terraformBlocks; the rest exist so that a reference
// found inside them is recognised as being there rather than nowhere.
function topLevelBlocks(terraform: string): TopLevelBlock[] {
  const blocks: TopLevelBlock[] = [];
  const header = /^(resource|data|locals|output|variable|module|provider|terraform)\b([^{\n]*)\{/gm;
  for (const match of terraform.matchAll(header)) {
    const start = match.index;
    let depth = 1;
    let index = match.index + match[0].length;
    while (index < terraform.length && depth > 0) {
      const character = terraform[index];
      if (character === "{") depth += 1;
      if (character === "}") depth -= 1;
      index += 1;
    }
    const labels = [...(match[2] ?? "").matchAll(/"([^"]+)"/g)].map((label) => label[1] ?? "");
    blocks.push({
      kind: match[1] ?? "",
      type: match[1] === "resource" || match[1] === "data" ? (labels[0] ?? "") : "",
      name: match[1] === "resource" || match[1] === "data" ? (labels[1] ?? "") : (labels[0] ?? ""),
      body: terraform.slice(match.index + match[0].length, index - 1),
      start,
      end: index,
    });
  }
  return blocks;
}

function lineAt(text: string, at: number): string {
  const from = text.lastIndexOf("\n", at) + 1;
  const to = text.indexOf("\n", at);
  return text.slice(from, to === -1 ? undefined : to);
}

export function terraformBlocks(terraform: string): { type: string; name: string; body: string }[] {
  const blocks: { type: string; name: string; body: string }[] = [];
  const header = /resource\s+"([^"]+)"\s+"([^"]+)"\s*\{/g;
  for (const match of terraform.matchAll(header)) {
    const start = match.index + match[0].length;
    let depth = 1;
    let index = start;
    while (index < terraform.length && depth > 0) {
      const character = terraform[index];
      if (character === "{") depth += 1;
      if (character === "}") depth -= 1;
      index += 1;
    }
    blocks.push({
      type: match[1] ?? "",
      name: match[2] ?? "",
      body: terraform.slice(start, index - 1),
    });
  }
  return blocks;
}

export function readInfra(): string {
  const infra = path.resolve("infra");
  const files = readdirSync(infra)
    .filter((name) => name.endsWith(".tf"))
    .sort()
    .map((name) => readFileSync(path.join(infra, name), "utf8"));
  if (files.length === 0) throw new Error("no Terraform files under infra/");
  return files.join("\n");
}
