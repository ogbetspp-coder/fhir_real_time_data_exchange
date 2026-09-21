import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

// Parsing just enough Terraform to answer one question: which roles does a given service
// account hold anywhere in infra/. Shared by every least-privilege test so that each boundary is
// checked by the same reader, and a weakness in it is a weakness found once.

export type TerraformBinding = { type: string; role: string };

// Reads the IAM bindings Terraform declares for the query service account: `*_iam_member`,
// `*_iam_binding`, and `*_iam_policy` resources alike, so a grant made through a binding or a
// policy is counted and not overlooked. It is deliberately tolerant about naming — the
// Terraform is another builder's file — and strict about what it asserts: the set of roles,
// and where each one is bound.
export function serviceAccountRoles(terraform: string, account: string): TerraformBinding[] {
  // Only a binding that names the account as a *member* is a role the account holds. A binding
  // whose `service_account_id` is the account is the opposite direction — someone else holding
  // a role over it, as the token-creator grant does — and must not be counted as a permission
  // this identity has.
  const member = new RegExp(
    `\\bmembers?\\s*=[^\\n]*google_service_account\\.${account}\\.email|\\bmembers?\\s*=\\s*\\[[^\\]]*google_service_account\\.${account}\\.email`,
  );

  return terraformBlocks(terraform).flatMap(({ type, body }) => {
    if (!/_iam_(member|binding|policy)$/.test(type)) return [];
    if (!member.test(body)) return [];
    const role = /\brole\s*=\s*"([^"]+)"/.exec(body)?.[1];
    return role === undefined ? [] : [{ type, role }];
  });
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
