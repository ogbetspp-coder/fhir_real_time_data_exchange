import { describe, expect, it } from "vitest";
import { z } from "zod";

import { resolveBase } from "../../scripts/authority/lock-hashes.js";
import {
  currentStructures,
  identify,
  publishedHistory,
  readVersionsLock,
  recordExists,
  releasedLocks,
  respellingIssues,
  unpublishedChange,
  updateLock,
  type Structure,
  type VersionsLock,
} from "../../scripts/contracts/versions-lock.js";
import { CONTRACTS } from "../../src/contracts/index.js";
import { publishedSchema, structureSha256 } from "../../src/contracts/json-schema.js";

// A contract version names one schema (ADR 0002, "Versioning"; audit C-6). Until 2026-09-28
// nothing held it to that: `ingestion-provenance` took a required `kind` and a union under the
// `$id` of 1.0.0, and only the index's content hash moved. contracts/versions.lock.json records
// each version's structure hash; a changed schema under a recorded version fails here.

describe("the contract version lock", () => {
  it("records every published contract at its version with the structure it publishes", () => {
    const lock = readVersionsLock();
    for (const { name, version, sha256 } of currentStructures()) {
      const entries = lock[name]?.[version];
      expect(entries, `run npm run contracts:lock for ${name}@${version}`).toBeDefined();
      expect(
        entries?.at(-1)?.sha256,
        `${name}@${version} publishes a schema its version was not locked to: a changed schema is a new version`,
      ).toBe(sha256);
    }
  });

  it("names a change record that exists for every entry", () => {
    for (const versions of Object.values(readVersionsLock())) {
      for (const entries of Object.values(versions)) {
        for (const { record } of entries)
          expect([record, recordExists(record)]).toEqual([record, true]);
      }
    }
  });

  it("hashes the structure, not the name and version the $id restates", () => {
    const [contract] = CONTRACTS;
    if (contract === undefined) throw new Error("no contract");
    const document = publishedSchema(contract);
    expect(
      structureSha256({ ...document, $id: "https://khs.dev/contracts/x/9.9.9/schema.json" }),
    ).toBe(structureSha256(document));
    expect(structureSha256({ ...document, description: "changed" })).not.toBe(
      structureSha256(document),
    );
  });

  // The failure the lock exists for: the same version, a different schema.
  it("sees a changed schema under an unchanged version", () => {
    const [contract] = CONTRACTS;
    if (contract === undefined) throw new Error("no contract");
    const changed = { ...contract, schema: z.strictObject({ extra: z.string() }) };
    const lock = readVersionsLock();
    expect(lock[contract.name]?.[contract.version]?.at(-1)?.sha256).not.toBe(
      structureSha256(publishedSchema(changed)),
    );
  });

  it("keeps every entry main has released", () => {
    // Every lock in the first-parent history of LOCK_BASE (scripts/ci/lock-base.sh; locally
    // origin/main), read as the importer lock's test reads its own.
    const base = process.env.LOCK_BASE ?? "origin/main";
    const commit = resolveBase(base);
    if (process.env.CI === "true") expect(commit, "CI must name the lock's base").toBeDefined();
    if (commit === undefined) return;
    const lock = readVersionsLock();
    for (const { commit: released, lock: old } of releasedLocks(commit)) {
      for (const [name, versions] of Object.entries(old)) {
        for (const [version, entries] of Object.entries(versions)) {
          expect(
            lock[name]?.[version]?.slice(0, entries.length),
            `${name}@${version} was released (${released}); its entries only grow`,
          ).toEqual(entries);
        }
      }
    }
  });

  // Review of #148, part A L2: a retired version number can never be published again with a
  // schema it was not published with, because every structure main ever published is locked.
  it("records every structure main has published, under its version", () => {
    const base = process.env.LOCK_BASE ?? "origin/main";
    const commit = resolveBase(base);
    if (process.env.CI === "true") expect(commit, "CI must name the lock's base").toBeDefined();
    if (commit === undefined) return;
    const lock = readVersionsLock();
    const history = publishedHistory(commit);
    expect(history.length).toBeGreaterThan(20);
    for (const { name, version, sha256 } of history) {
      expect(
        [name, version, lock[name]?.[version]?.some((entry) => entry.sha256 === sha256)],
        `${name}@${version}: main published ${sha256}; run npm run contracts:lock`,
      ).toEqual([name, version, true]);
    }
  });

  // Review of #148, round 2, M-1: whatever the lock says, a version main published publishes its
  // last published schema, or a re-spelling of it. An entry appended to the lock by hand is no
  // licence.
  it("publishes every version main published with its schema, or a re-spelling of it", () => {
    const commit = resolveBase(process.env.LOCK_BASE ?? "origin/main");
    if (process.env.CI === "true") expect(commit, "CI must name the lock's base").toBeDefined();
    if (commit === undefined) return;
    const history = publishedHistory(commit);
    for (const current of currentStructures()) {
      expect([current.name, unpublishedChange(current, history)]).toEqual([
        current.name,
        undefined,
      ]);
    }

    // The reviewer's reproduction: fidelity-report 1.0.0's `issues` items widened from 256 to
    // 257 characters, regenerated, and its hash appended to the lock by hand.
    const report = currentStructures().find(({ name }) => name === "fidelity-report");
    if (report === undefined) throw new Error("no fidelity-report");
    const widened = structuredClone(report.document) as {
      $defs: { FidelityReport: { properties: { issues: { items: { maxLength: number } } } } };
    };
    expect(widened.$defs.FidelityReport.properties.issues.items.maxLength).toBe(256);
    widened.$defs.FidelityReport.properties.issues.items.maxLength = 257;
    const handEdited = {
      ...report,
      document: widened,
      sha256: structureSha256(widened),
    };
    expect(unpublishedChange(handEdited, history)).toMatch(
      /fidelity-report@1\.0\.0 is not a re-spelling of the schema main published: .*maxLength changed/,
    );
    const lock = readVersionsLock();
    const appended: VersionsLock = {
      ...lock,
      "fidelity-report": {
        ...lock["fidelity-report"],
        "1.0.0": [
          ...(lock["fidelity-report"]?.["1.0.0"] ?? []),
          { sha256: handEdited.sha256, record: RECORD },
        ],
      },
    };
    expect(() =>
      updateLock({
        lock: appended,
        history,
        current: [handEdited],
        released: [],
        record: RECORD,
        respelling: true,
      }),
    ).toThrow(/is not a re-spelling/);
  });

  // The rule, on what main actually published: #145's `\d` to `[0-9]` is a re-spelling of every
  // schema it touched; the changes ingestion-provenance 1.0.0 took before it are not.
  it("reads #145 as a re-spelling, and ingestion-provenance 1.0.0's earlier changes as none", () => {
    const commit = resolveBase(process.env.LOCK_BASE ?? "origin/main");
    if (commit === undefined) return;
    const history = publishedHistory(commit);
    const under = (name: string, version: string): Structure[] =>
      history.filter((structure) => structure.name === name && structure.version === version);
    for (const [name, version] of [
      ["canonical-submission", "2.0.0"],
      ["fidelity-report", "1.0.0"],
      ["query-tools", "4.0.0"],
      ["agent-turn", "1.1.0"],
      ["run-manifest", "4.0.0"],
    ] as const) {
      const [before, after] = under(name, version).slice(-2);
      expect([name, respellingIssues(before?.document, after?.document)]).toEqual([name, []]);
    }
    const provenance = under("ingestion-provenance", "1.0.0");
    expect(provenance).toHaveLength(5);
    for (let index = 1; index < 4; index += 1) {
      expect(
        respellingIssues(provenance[index - 1]?.document, provenance[index]?.document),
      ).not.toEqual([]);
    }
    expect(respellingIssues(provenance[3]?.document, provenance[4]?.document)).toEqual([]);
  });
});

const RECORD = "docs/validation/changes/2026-09-28-contract-versions-lock-and-parity.md";

// updateLock, over synthetic schemas: what `npm run contracts:lock` may and may not record.
describe("updating the version lock", () => {
  const document = (pattern: string, extra: Record<string, unknown> = {}): unknown => ({
    $schema: "https://json-schema.org/draft/2020-12/schema",
    $id: "https://khs.dev/contracts/thing/1.0.0/schema.json",
    $defs: { Id: { type: "string", pattern, maxLength: 64, description: "An id.", ...extra } },
  });
  const structure = (version: string, published: unknown): Structure => ({
    name: "thing",
    version,
    sha256: structureSha256(published as Record<string, unknown>),
    document: published,
  });
  const before = structure("1.0.0", document(String.raw`^\d+$`));
  const lock: VersionsLock = { thing: { "1.0.0": [{ sha256: before.sha256, record: RECORD }] } };
  const update = (current: Structure, respelling = false, released = [lock]) =>
    updateLock({
      lock,
      history: [before],
      current: [current],
      released,
      record: RECORD,
      respelling,
    });

  it("appends a re-spelling, only when asked to, and never over what main released", () => {
    const respelt = structure("1.0.0", document("^[0-9]+$"));
    expect(() => update(respelt)).toThrow(/a re-spelling is appended with --respelling/);
    const { lock: updated } = update(respelt, true);
    expect(updated.thing?.["1.0.0"]?.map(({ sha256 }) => sha256)).toEqual([
      before.sha256,
      respelt.sha256,
    ]);
  });

  // The escape hatch the review of #148 found unchecked (part A L1): a structural change passed off
  // as a re-spelling.
  it.each([
    ["a bound", document("^[0-9]+$", { maxLength: 65 })],
    ["a description", document("^[0-9]+$", { description: "Another id." })],
    ["a keyword added", document("^[0-9]+$", { minLength: 1 })],
    ["a type", document("^[0-9]+$", { type: "integer" })],
  ])("refuses %s changed under --respelling", (_, disguised) => {
    expect(() => update(structure("1.0.0", disguised), true)).toThrow(/is not a re-spelling/);
  });

  // Review of #148, round 2, L-1: a change of language made through `pattern` alone, and a
  // `pattern` that is data rather than a keyword. Only the table's rewrites are re-spellings.
  it.each([
    ["a class widened", document("^[0-9a]+$"), "$.$defs.Id.pattern is not a re-spelling"],
    ["anything", document(".*"), "$.$defs.Id.pattern is not a re-spelling"],
    ["nothing", document(String.raw`[^\s\S]`), "$.$defs.Id.pattern is not a re-spelling"],
    [
      "a const whose pattern member changed",
      document(String.raw`^\d+$`, { const: { pattern: "b" } }),
      "$.$defs.Id has other keywords",
    ],
  ])("refuses %s as a re-spelling", (_, changed, issue) => {
    expect(respellingIssues(before.document, changed)).toEqual([issue]);
    expect(() => update(structure("1.0.0", changed), true)).toThrow(/is not a re-spelling/);
  });

  it("compares data keywords whole, a pattern member of a const or default included", () => {
    const withData = (pattern: string, member: string): unknown =>
      document(String.raw`^\d+$`, {
        const: { pattern: member },
        default: { pattern: member },
        examples: [pattern],
      });
    expect(respellingIssues(withData("1", "a"), withData("1", "a"))).toEqual([]);
    expect(respellingIssues(withData("1", "a"), withData("1", "b"))).toEqual([
      "$.$defs.Id.const changed",
      "$.$defs.Id.default changed",
    ]);
    expect(respellingIssues(withData("1", "a"), withData("2", "a"))).toEqual([
      "$.$defs.Id.examples changed",
    ]);
  });

  it("walks every kind of subschema, and respells only by the table", () => {
    const tree = (pattern: string, extra: Record<string, unknown> = {}): unknown => ({
      properties: { a: { pattern } },
      items: { pattern },
      allOf: [{ pattern }],
      ...extra,
    });
    expect(respellingIssues(tree(String.raw`^[\d.]$`), tree("^[0-9.]$"))).toEqual([]);
    expect(respellingIssues(tree(String.raw`^\w$`), tree(String.raw`^\w$`))).toEqual([
      "$.allOf[0].pattern is not a re-spelling",
      "$.items.pattern is not a re-spelling",
      "$.properties.a.pattern is not a re-spelling",
    ]);
    expect(respellingIssues(tree("a"), tree("a", { allOf: [] }))).toEqual([
      "$.allOf has another length",
    ]);
    expect(respellingIssues(tree("a"), tree("a", { properties: { b: { pattern: "a" } } }))).toEqual(
      ["$.properties has other members"],
    );
  });

  // Review of #148, round 2, M-1: a structure already appended to the lock (by hand, or by an
  // earlier run) is checked again, not taken as licensed by being there.
  it("checks a structure the lock already names, not only one it would append", () => {
    const widened = structure("1.0.0", document(String.raw`^\d+$`, { maxLength: 65 }));
    const handAppended: VersionsLock = {
      thing: {
        "1.0.0": [...(lock.thing?.["1.0.0"] ?? []), { sha256: widened.sha256, record: RECORD }],
      },
    };
    expect(() =>
      updateLock({
        lock: handAppended,
        history: [before],
        current: [widened],
        released: [lock],
        record: RECORD,
        respelling: true,
      }),
    ).toThrow(
      /thing@1\.0\.0 is not a re-spelling of the schema main published: \$\.\$defs\.Id\.maxLength changed/,
    );
  });

  it("relocks a version main never published, and records what main published but the lock lacks", () => {
    const next = structure("2.0.0", document("^[a-z]+$"));
    const draft: VersionsLock = {
      thing: { "2.0.0": [{ sha256: "0".repeat(64), record: RECORD }] },
    };
    const { lock: updated, changed } = updateLock({
      lock: draft,
      history: [before],
      current: [next],
      released: [],
      record: RECORD,
      respelling: false,
    });
    expect(updated.thing).toEqual({
      "1.0.0": [{ sha256: before.sha256, record: RECORD }],
      "2.0.0": [{ sha256: next.sha256, record: RECORD }],
    });
    expect(changed).toEqual([
      expect.stringContaining("thing@1.0.0 published structure"),
      "thing@2.0.0 locked",
    ]);
  });

  it("drops what only this branch locked when the schema returns to the one main published", () => {
    const branch: VersionsLock = {
      thing: {
        "1.0.0": [...(lock.thing?.["1.0.0"] ?? []), { sha256: "2".repeat(64), record: RECORD }],
      },
    };
    const { lock: updated } = updateLock({
      lock: branch,
      history: [before],
      current: [before],
      released: [lock],
      record: RECORD,
      respelling: false,
    });
    expect(updated).toEqual(lock);
  });

  it("refuses to drop or change an entry main released", () => {
    const tampered: VersionsLock = {
      thing: { "1.0.0": [{ sha256: "1".repeat(64), record: RECORD }] },
    };
    expect(() => update(before, false, [tampered])).toThrow(/an entry main released was changed/);
  });

  it("reads a version from the $id, and refuses a document without one", () => {
    expect(identify(document("^a$"))).toEqual({ name: "thing", version: "1.0.0" });
    expect(() => identify({ $id: "https://example.org/schema.json" })).toThrow(/names no contract/);
    expect(() => identify(null)).toThrow(/names no contract/);
  });

  it("compares a schema that is not an object, and a pattern that is not a string, whole", () => {
    expect(respellingIssues(true, true)).toEqual([]);
    expect(respellingIssues(true, false)).toEqual(["$ changed"]);
    expect(respellingIssues([{ pattern: "a" }], [{ pattern: "b" }])).toEqual(["$ changed"]);
    expect(respellingIssues({ a: [1] }, { a: [2] })).toEqual(["$.a changed"]);
    expect(respellingIssues({ pattern: 1 }, { pattern: 2 })).toEqual(["$.pattern changed"]);
  });
});
