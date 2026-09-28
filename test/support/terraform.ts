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
//
// Every structural question (where a block starts and ends, which line a reference is on, which
// line assigns the role) is asked of the lexed text, in which comments, string literals and
// heredoc bodies are blanked: a brace, a reference or a `role =` inside any of them is not code.
export function serviceAccountRoles(terraform: string, account: string): TerraformBinding[] {
  const lexed = lex(terraform);
  const blocks = topLevelBlocks(terraform, lexed);
  const reference = new RegExp(`google_service_account\\.${account}\\.\\w+`, "g");
  const bindings: TerraformBinding[] = [];

  for (const match of lexed.code.matchAll(reference)) {
    const at = match.index;
    const block = blocks.find(({ start, end }) => at >= start && at < end);
    const where = `google_service_account.${account} referenced at offset ${String(at)}`;
    if (block === undefined) throw new Error(`${where} outside any block`);
    if (block.kind === "output") continue;
    if (block.kind !== "resource") {
      throw new Error(`${where} inside a ${block.kind} block, which this reader cannot follow`);
    }
    if (block.type === `google_service_account` && block.name === account) continue;

    const line = lineAt(lexed.code, at);
    const isIam = /_iam_(member|binding|policy)$/.test(block.type);
    if (!isIam) continue; // form 3: a runtime identity, not a grant

    if (/^\s*service_account_id\s*=/.test(line)) continue; // form 2
    if (!/^\s*members?\s*=/.test(line)) {
      throw new Error(`${where}: ${block.type}.${block.name} uses it on an unrecognised line`);
    }
    bindings.push({ type: block.type, role: literalRole(block, account) });
  }

  // A literal e-mail for the account in any IAM member line bypasses the reference scan above.
  const declaration = blocks.find(
    ({ kind, type, name }) =>
      kind === "resource" && type === "google_service_account" && name === account,
  );
  const accountId =
    declaration === undefined
      ? undefined
      : /^\s*account_id\s*=\s*"([^"$]+)/m.exec(declaration.body)?.[1];
  if (accountId !== undefined) {
    for (const block of blocks) {
      if (block.kind !== "resource" || !block.type.includes("_iam_")) continue;
      if (new RegExp(`members?\\s*=[^\\n]*serviceAccount:${accountId}`).test(block.body)) {
        throw new Error(`${block.type}.${block.name} names ${account} by literal e-mail`);
      }
    }
  }
  // A policy document assembled in a data source is invisible to the scan; refuse it outright.
  if (blocks.some(({ kind, type }) => kind === "data" && type === "google_iam_policy")) {
    throw new Error('infra/ uses data "google_iam_policy", which this reader cannot follow');
  }
  return bindings;
}

// The one `role` a grant assigns, as a quoted literal. The assignment is found in the lexed body,
// so a `role =` line in a comment or a heredoc is not mistaken for it, and two assignments (one
// in a nested block) are refused rather than guessed between.
function literalRole(block: TopLevelBlock, account: string): string {
  const assignments = [...block.code.matchAll(/^[ \t]*role\s*=/gm)];
  if (assignments.length !== 1) {
    throw new Error(
      `${block.type}.${block.name} grants to ${account} with ${String(assignments.length)} role assignments`,
    );
  }
  const line = lineAt(block.body, assignments[0]?.index ?? 0);
  const role = /^\s*role\s*=\s*"([^"$%\\]+)"\s*$/.exec(line)?.[1];
  if (role === undefined) {
    throw new Error(`${block.type}.${block.name} grants to ${account} with a non-literal role`);
  }
  return role;
}

type TopLevelBlock = {
  kind: string;
  type: string;
  name: string;
  // The block's text with comments blanked; string literals are kept, so a test can match them.
  body: string;
  // The same text lexed: comments, string literals and heredoc bodies blanked.
  code: string;
  start: number;
  end: number;
};

const BLOCK_KINDS = new Set([
  "resource",
  "data",
  "locals",
  "output",
  "variable",
  "module",
  "provider",
  "terraform",
  "moved",
  "import",
  "removed",
  "check",
]);

// Every top-level block, with its extent: resources, data sources, locals, outputs, variables,
// modules. Only resources are returned by terraformBlocks; the rest exist so that a reference
// found inside them is recognised as being there rather than nowhere. Anything at the top level
// that is not a block header, and any brace that does not balance, is refused.
function topLevelBlocks(terraform: string, lexed = lex(terraform)): TopLevelBlock[] {
  const { code, plain } = lexed;
  const blocks: TopLevelBlock[] = [];
  const header = /([A-Za-z_][\w-]*)((?:\s*"[^"\n]*")*)\s*\{/y;
  let index = 0;
  while (index < code.length) {
    if (/\s/.test(code[index] ?? "")) {
      index += 1;
      continue;
    }
    header.lastIndex = index;
    const match = header.exec(code);
    const kind = match?.[1] ?? "";
    if (match === null || !BLOCK_KINDS.has(kind)) {
      throw new Error(
        `unexpected top-level text at offset ${String(index)}: ${lineAt(plain, index)}`,
      );
    }
    const open = index + match[0].length;
    const close = closingBrace(code, open);
    const labels = [...plain.slice(index, open).matchAll(/"([^"]*)"/g)].map(
      (label) => label[1] ?? "",
    );
    const typed = kind === "resource" || kind === "data";
    blocks.push({
      kind,
      type: typed ? (labels[0] ?? "") : "",
      name: typed ? (labels[1] ?? "") : (labels[0] ?? ""),
      body: plain.slice(open, close),
      code: code.slice(open, close),
      start: index,
      end: close + 1,
    });
    index = close + 1;
  }
  return blocks;
}

// The offset of the `}` that closes a block whose body starts at `from`, counting only code
// braces (an interpolation's braces are code, and always balanced).
function closingBrace(code: string, from: number): number {
  let depth = 1;
  for (let index = from; index < code.length; index += 1) {
    const character = code[index];
    if (character === "{") depth += 1;
    if (character === "}") depth -= 1;
    if (depth === 0) return index;
  }
  throw new Error(`unbalanced braces: a block opened before offset ${String(from)} never closes`);
}

function lineAt(text: string, at: number): string {
  const from = text.lastIndexOf("\n", at) + 1;
  const to = text.indexOf("\n", at);
  return text.slice(from, to === -1 ? undefined : to);
}

type Lexed = {
  // Comments blanked; everything else as written.
  plain: string;
  // Comments, string literal text and heredoc bodies blanked too. Quote marks, heredoc markers
  // and the code inside `${ }` and `%{ }` remain.
  code: string;
};

// A lexer for the parts of HCL that decide what is code: `#`, `//` and `/* */` comments, quoted
// strings with their escapes, heredocs (`<<EOT`, `<<-EOT`), and the template interpolations and
// directives inside both, which are code again and may nest strings of their own. Blanking keeps
// every offset and every newline, so a position in one text is the same position in the others.
// Anything left open at the end is refused.
export function lex(terraform: string): Lexed {
  // UTF-16 units, the same units every offset and slice here counts in.
  const plain = terraform.split("");
  const code = terraform.split("");
  const blank = (from: number, to: number, alsoPlain: boolean) => {
    for (let index = from; index < to; index += 1) {
      if (terraform[index] === "\n") continue;
      code[index] = " ";
      if (alsoPlain) plain[index] = " ";
    }
  };
  const at = (index: number, text: string) => terraform.startsWith(text, index);

  // Code from `from`; with `closes`, until the `}` that ends an interpolation, returning the
  // offset after it.
  const scanCode = (from: number, closes: boolean): number => {
    let depth = 0;
    let index = from;
    while (index < terraform.length) {
      if (at(index, "#") || at(index, "//")) {
        const end = terraform.indexOf("\n", index);
        const stop = end === -1 ? terraform.length : end;
        blank(index, stop, true);
        index = stop;
        continue;
      }
      if (at(index, "/*")) {
        const end = terraform.indexOf("*/", index + 2);
        if (end === -1) throw new Error(`unterminated /* comment at offset ${String(index)}`);
        blank(index, end + 2, true);
        index = end + 2;
        continue;
      }
      if (at(index, '"')) {
        index = scanQuoted(index + 1);
        continue;
      }
      const heredoc = /<<(-?)([A-Za-z_][\w-]*)\r?\n/y;
      heredoc.lastIndex = index;
      const opener = heredoc.exec(terraform);
      if (opener !== null) {
        index = scanHeredoc(index + opener[0].length, opener[2] ?? "");
        continue;
      }
      const character = terraform[index];
      if (character === "{") depth += 1;
      if (character === "}") {
        if (closes && depth === 0) return index + 1;
        depth -= 1;
        if (depth < 0) throw new Error(`unbalanced braces: an extra } at offset ${String(index)}`);
      }
      index += 1;
    }
    if (closes)
      throw new Error(`unterminated template interpolation before offset ${String(from)}`);
    return index;
  };

  // A template's text at `index`: an escaped `$${`/`%%{` is text, a `${`/`%{` opens code.
  // Returns the offset after what it consumed, or undefined when `index` is plain text.
  const scanTemplatePart = (index: number): number | undefined => {
    if (at(index, "$${") || at(index, "%%{")) {
      blank(index, index + 3, false);
      return index + 3;
    }
    if (at(index, "${") || at(index, "%{")) return scanCode(index + 2, true);
    return undefined;
  };

  const scanQuoted = (from: number): number => {
    let index = from;
    while (index < terraform.length) {
      const character = terraform[index];
      if (character === '"') return index + 1;
      if (character === "\n") throw new Error(`unterminated string at offset ${String(from - 1)}`);
      if (character === "\\") {
        blank(index, index + 2, false);
        index += 2;
        continue;
      }
      const after = scanTemplatePart(index);
      if (after !== undefined) {
        index = after;
        continue;
      }
      blank(index, index + 1, false);
      index += 1;
    }
    throw new Error(`unterminated string at offset ${String(from - 1)}`);
  };

  // Lines up to the one that is only the marker (indented or not); the marker itself is code.
  const scanHeredoc = (from: number, marker: string): number => {
    let index = from;
    let lineStart = true;
    while (index < terraform.length) {
      if (lineStart) {
        const end = terraform.indexOf("\n", index);
        const line = terraform.slice(index, end === -1 ? undefined : end);
        if (line.trim() === marker) return index + line.length;
      }
      const character = terraform[index];
      if (character === "\n") {
        lineStart = true;
        index += 1;
        continue;
      }
      lineStart = false;
      const after = scanTemplatePart(index);
      if (after !== undefined) {
        index = after;
        continue;
      }
      blank(index, index + 1, false);
      index += 1;
    }
    throw new Error(`unterminated heredoc ${marker} starting at offset ${String(from)}`);
  };

  scanCode(0, false);
  return { plain: plain.join(""), code: code.join("") };
}

export function terraformBlocks(terraform: string): { type: string; name: string; body: string }[] {
  return topLevelBlocks(terraform)
    .filter(({ kind }) => kind === "resource")
    .map(({ type, name, body }) => ({ type, name, body }));
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
