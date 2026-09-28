import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";

import { z } from "zod";

import { transformDocument } from "../authority/t/document.js";
import { modelDocument, modelSection, type Model } from "../authority/t/model.js";
import { TRefusal, transformSection } from "../authority/t/transform.js";
import { xhtmlToText, XhtmlError } from "../fidelity/xhtml.js";
import type { Resource } from "./page.js";
import type { Contained } from "./pictures.js";

// What the renderer gate draws (docs/design/authority-import-renderer.md, R2 and R3): every
// accepted T case, every synthetic model case, and every section of every pinned label, each
// named once, so every check of the renderer image judges the same sections, and none is left
// out silently. An accepted case T or the scanner now refuses, a model case either refuses, a
// label section T and the scanner accept without a model, an empty lock, a label with no
// sections, and a label whose bytes are not the lock's are each `broken`: the checks fail on any.

// R2's device pixel ratios, and its named widths (the EMA viewer's 813 px first).
export const RATIOS: readonly number[] = [0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.625, 3];
export const WIDTHS: readonly number[] = [813, 360, 1240];

// A case's div: its root (the plain XHTML div unless it names another), its content, and the
// marking every synthetic section carries.
export const ROOT = '<div xmlns="http://www.w3.org/1999/xhtml">';
export const MARKED = "<p>not for clinical use</p></div>";

// The pinned labels' lock: each source's file, byte count and SHA-256 (its other fields are the
// source's provenance, read by Zone A's checks).
const LOCK = z.object({
  sources: z.array(
    z.looseObject({
      file: z.string().regex(/^[a-z0-9-]+\.json$/u),
      sha256: z.string().regex(/^[0-9a-f]{64}$/u),
      bytes: z.number().int().positive(),
    }),
  ),
});

export type PinnedLabel = { file: string; document: Buffer };

// The labels a lock pins, each read and held to the lock's byte count and SHA-256.
export function pinnedLabels(directory: string): PinnedLabel[] {
  const lock = LOCK.parse(
    JSON.parse(readFileSync(path.join(directory, "sources.lock.json"), "utf8")),
  );
  return lock.sources.map(({ file, sha256, bytes }) => {
    const document = readFileSync(path.join(directory, "sources", file));
    const hash = createHash("sha256").update(document).digest("hex");
    if (document.length !== bytes || hash !== sha256) {
      throw new Error(
        `${file}: ${document.length} bytes, SHA-256 ${hash}; the lock pins ${bytes} bytes, ${sha256}`,
      );
    }
    return { file, document };
  });
}

type Json = Record<string, unknown>;
const object = (value: unknown): Json =>
  typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Json) : {};
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);

// A document's Composition (its first entry's resource): each section's div in pre-order, with
// its path, and the Binaries it contains, by id (R2's `contained` pictures).
export function sectionDivs(document: unknown): {
  sections: { path: string; div: string | undefined }[];
  contained: Contained;
} {
  const composition = object(object(list(object(document).entry)[0]).resource);
  const sections: { path: string; div: string | undefined }[] = [];
  const walk = (nodes: unknown[], base: string): void => {
    nodes.forEach((node, position) => {
      const at = `${base}[${position}]`;
      const div = object(object(node).text).div;
      sections.push({ path: at, div: typeof div === "string" ? div : undefined });
      walk(list(object(node).section), `${at}.section`);
    });
  };
  walk(list(composition.section), "Composition.section");
  const contained = new Map<string, Resource>();
  for (const entry of list(composition.contained).map(object)) {
    if (entry.resourceType !== "Binary" || typeof entry.id !== "string") continue;
    contained.set(entry.id, {
      body: Buffer.from(typeof entry.data === "string" ? entry.data : "", "base64"),
      contentType:
        typeof entry.contentType === "string" ? entry.contentType : "application/octet-stream",
    });
  }
  return { sections, contained };
}

// A section every check draws. A carried one (T and the scanner accept it: a case, or a label's
// section the import would carry) has its T(div) and T's model; one T or the scanner refuses only
// has its page and fonts reported, since it is withheld or refuses the import anyway.
export type Section =
  | {
      name: string;
      div: string;
      contained: Contained;
      carried: true;
      output: string;
      model: Model;
    }
  | { name: string; div: string; contained: Contained; carried: false; refused: string };

export type CaseInput = {
  name: string;
  inner: string;
  root?: string;
  evidence?: readonly string[];
};

// Whether the scanner accepts a T(div); only its own refusal counts as no.
export function scannerAccepts(output: string): boolean {
  try {
    xhtmlToText(output);
    return true;
  } catch (error) {
    if (error instanceof XhtmlError) return false;
    throw error;
  }
}

export function enumerateSections(input: {
  // The T cases T accepts (their `expected` a div).
  tCases: readonly CaseInput[];
  modelCases: readonly CaseInput[];
  labels: readonly PinnedLabel[];
}): { sections: Section[]; broken: string[] } {
  const sections: Section[] = [];
  const broken: string[] = [];
  const carryCase = (name: string, testCase: CaseInput, kind: string): void => {
    const div = `${testCase.root ?? ROOT}${testCase.inner}${MARKED}`;
    const evidence = testCase.evidence === undefined ? undefined : new Set(testCase.evidence);
    try {
      const output = transformSection(div, evidence).div;
      if (!scannerAccepts(output)) {
        broken.push(`${name}: ${kind}, refused by the scanner`);
        return;
      }
      const model = JSON.parse(modelSection(div, evidence)) as Model;
      sections.push({ name, div, contained: new Map(), carried: true, output, model });
    } catch (error) {
      if (!(error instanceof TRefusal)) throw error;
      broken.push(`${name}: ${kind}, refused by T (${error.reason})`);
    }
  };
  for (const testCase of input.tCases) {
    carryCase(`t-case ${testCase.name}`, testCase, "accepted in t-cases.ts");
  }
  for (const modelCase of input.modelCases) {
    carryCase(`model-case ${modelCase.name}`, modelCase, "a model case");
  }
  if (input.labels.length === 0) broken.push("the lock pins no label");
  for (const { file, document } of input.labels) {
    const { sections: placed, contained } = sectionDivs(JSON.parse(document.toString("utf8")));
    if (placed.length === 0) broken.push(`${file}: no sections`);
    const divs = placed.map(({ div }) => div);
    const outcomes = transformDocument(divs);
    const models = modelDocument(divs);
    placed.forEach(({ path: at, div }, index) => {
      if (div === undefined) return;
      const name = `${file} ${at}`;
      const outcome = outcomes[index];
      if (outcome === undefined || !("div" in outcome)) {
        const refused = outcome === undefined ? "no outcome" : outcome.refused;
        sections.push({ name, div, contained, carried: false, refused: `T: ${refused}` });
        return;
      }
      if (!scannerAccepts(outcome.div)) {
        sections.push({ name, div, contained, carried: false, refused: "the scanner" });
        return;
      }
      const model = models[index];
      if (model === undefined) {
        broken.push(`${name}: accepted by T and the scanner, but has no model`);
        return;
      }
      sections.push({
        name,
        div,
        contained,
        carried: true,
        output: outcome.div,
        model: JSON.parse(model) as Model,
      });
    });
  }
  return { sections, broken };
}

// A check's own count of the carried sections it judged against the enumeration's: a check that
// skips one, or judges one twice, fails.
export function carriedMismatch(
  check: string,
  judged: readonly string[],
  sections: readonly Section[],
): string | undefined {
  const expected = sections.filter(({ carried }) => carried).map(({ name }) => name);
  const seen = new Set(judged);
  const missing = expected.filter((name) => !seen.has(name));
  if (judged.length === expected.length && seen.size === judged.length && missing.length === 0) {
    return undefined;
  }
  return (
    `${check}: judged ${judged.length} carried sections (${seen.size} distinct), not ${expected.length}` +
    (missing.length > 0 ? `; missing ${missing.slice(0, 5).join(", ")}` : "")
  );
}
