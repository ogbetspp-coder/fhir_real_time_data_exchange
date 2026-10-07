import { generateKeyPairSync } from "node:crypto";

import { beforeAll, describe, expect, it } from "vitest";

import {
  decideApproval,
  normaliseSubject,
  parseApproverMap,
  type ApprovalRequest,
} from "../../src/approval/approve.js";
import {
  approvalLinkBytes,
  approvalLinkId,
  approvalLinkProvenance,
} from "../../src/approval/link.js";
import { buildReview, recordFacts, renderReview, reviewSha256 } from "../../src/approval/review.js";
import {
  approvalPublicKey,
  checkHeadEntry,
  checkStatement,
  chooseHead,
  headObjectName,
  headPrefix,
  publishedSections,
  readHead,
  signedStatementBytes,
  statementSha256,
  verifySignedStatement,
  verifyWithKeys,
  type VerifiedStatement,
} from "../../src/approval/statement.js";
import { ReviewRecordSchema } from "../../src/contracts/index.js";
import { loadEmaMapping, type EmaMapping } from "../../src/fhir/mapping.js";
import { isComposition } from "../../src/fhir/types.js";
import { canonicalJson, sha256 } from "../../src/lib/hash.js";
import {
  EMAIL,
  IMAGE_DIGEST,
  KEY_VERSION,
  MemoryHeads,
  OTHER_KEY_VERSION,
  SUBJECT,
  approvableRecord,
  approvalKeys,
  approvers,
  otherKeys,
  signStatement,
  statementFor,
  trustedKeys,
  type ApprovableRecord,
} from "../support/approval.js";

// Build step 2 of docs/design/approval.md: the statement, review and verifier libraries, with the
// negative tests the design lists (another key, other content, another document, a replayed old
// head, a stale review hash, an unmapped subject, a wrong environment), each refused by its own
// closed code.

let mapping: EmaMapping;
let first: ApprovableRecord;
let second: ApprovableRecord;
let other: ApprovableRecord;

beforeAll(async () => {
  mapping = await loadEmaMapping();
  first = approvableRecord(mapping, { product: "synthetic-paracetamol", version: 1 });
  second = approvableRecord(mapping, { product: "synthetic-paracetamol", version: 2 });
  other = approvableRecord(mapping, { product: "synthetic-placebolol", version: 1 });
});

const trusted = (keyVersion: string) =>
  keyVersion === KEY_VERSION ? approvalKeys().publicKey : undefined;

function verified(bytes: string): VerifiedStatement {
  const result = verifySignedStatement(bytes, trusted);
  if (typeof result === "string") throw new Error(result);
  return result;
}

describe("a signed statement", () => {
  it("verifies against the environment's key, byte for byte as stored", () => {
    const signed = signStatement(statementFor(first.facts));
    const bytes = signedStatementBytes(signed);
    const result = verified(bytes);
    expect(result.statementSha256).toBe(sha256(signed.statement));
    expect(result.bytes).toBe(bytes);
    expect(
      checkStatement(result.statement, { environment: "dev", ...first.facts }),
    ).toBeUndefined();
  });

  it("is refused when another key signed it, or names a key version the reader does not trust", () => {
    const byOtherKey = signStatement(statementFor(first.facts), otherKeys().privateKey);
    expect(verifySignedStatement(signedStatementBytes(byOtherKey), trusted)).toBe("bad-signature");

    const untrusted = signStatement(
      statementFor(first.facts, {
        signer: { imageDigest: IMAGE_DIGEST, keyVersion: OTHER_KEY_VERSION },
      }),
    );
    expect(verifySignedStatement(signedStatementBytes(untrusted), trusted)).toBe("untrusted-key");
  });

  it("is refused for other content: a changed hash breaks the signature, and a signed one is not this record's", () => {
    const signed = signStatement(statementFor(first.facts));
    const tampered = {
      ...signed,
      statement: { ...signed.statement, approvedContentSha256: other.facts.approvedContentSha256 },
    };
    expect(verifySignedStatement(canonicalJson(tampered), trusted)).toBe("bad-signature");

    const theirs = verified(signedStatementBytes(signStatement(statementFor(other.facts))));
    expect(
      checkStatement(theirs.statement, {
        environment: "dev",
        approvedContentSha256: first.facts.approvedContentSha256,
      }),
    ).toBe("other-content");
    expect(
      checkStatement(theirs.statement, {
        environment: "dev",
        submissionId: first.facts.submissionId,
      }),
    ).toBe("other-submission");
  });

  it("is refused for another document", () => {
    const theirs = verified(signedStatementBytes(signStatement(statementFor(other.facts))));
    expect(
      checkStatement(theirs.statement, { environment: "dev", document: first.facts.document }),
    ).toBe("other-document");
  });

  it("is refused in another environment", () => {
    const signed = verified(
      signedStatementBytes(signStatement(statementFor(first.facts, { environment: "validation" }))),
    );
    expect(checkStatement(signed.statement, { environment: "dev" })).toBe("wrong-environment");
    expect(checkStatement(signed.statement, { environment: "prod" })).toBe("wrong-environment");
  });

  it("is refused when its sections or mapping are not the record's", () => {
    const signed = verified(signedStatementBytes(signStatement(statementFor(first.facts))));
    const changed = first.facts.sections.map((section, index) =>
      index === 0 ? { ...section, narrativeDivSha256: "0".repeat(64) } : section,
    );
    expect(checkStatement(signed.statement, { environment: "dev", sections: changed })).toBe(
      "section-mismatch",
    );
    expect(
      checkStatement(signed.statement, {
        environment: "dev",
        sections: first.facts.sections.slice(1),
      }),
    ).toBe("section-mismatch");
    expect(
      checkStatement(signed.statement, { environment: "dev", mappingVersion: "cap-smpc-en#0.0.1" }),
    ).toBe("other-mapping");
  });

  it("is refused when its bytes are not canonical, or not a statement at all", () => {
    const signed = signStatement(statementFor(first.facts));
    expect(verifySignedStatement(JSON.stringify(signed, null, 2), trusted)).toBe("not-canonical");
    expect(verifySignedStatement("{", trusted)).toBe("malformed");
    expect(verifySignedStatement(canonicalJson({ ...signed, extra: true }), trusted)).toBe(
      "malformed",
    );
    expect(
      verifySignedStatement(
        canonicalJson({ ...signed, statement: { ...signed.statement, kind: "withdraw" } }),
        trusted,
      ),
    ).toBe("malformed");
  });

  it("accepts only a 3072-bit RSA public key", () => {
    const pem = approvalKeys().publicKey.export({ type: "spki", format: "pem" }).toString();
    expect(approvalPublicKey(pem).asymmetricKeyDetails?.modulusLength).toBe(3_072);
    expect(() => approvalPublicKey(otherSmallKey())).toThrow(/3072-bit RSA/);
  });
});

// A 2048-bit key: the manifest key's size, not the approval key's.
function otherSmallKey(): string {
  return generateKeyPairSync("rsa", { modulusLength: 2_048 })
    .publicKey.export({ type: "spki", format: "pem" })
    .toString();
}

describe("a document's head", () => {
  it("is its highest entry, verified and held to the name it was read from", async () => {
    const heads = new MemoryHeads();
    const one = signStatement(statementFor(first.facts));
    heads.append(one);
    const two = signStatement(
      statementFor(second.facts, {
        document: first.facts.document,
        sequence: 2,
        previousStatementSha256: statementSha256(one.statement),
      }),
    );
    heads.append(two);

    const head = await readHead(heads, trustedKeys, first.facts.document);
    if (typeof head === "string") throw new Error(head);
    expect(head.statement.sequence).toBe(2);
    expect(head.bytes).toBe(signedStatementBytes(two));
    expect(await readHead(heads, trustedKeys, other.facts.document)).toBe("no-head");
  });

  // A replayed old head: the first statement is valid, but it is not the head once a second exists.
  it("is not an older statement replayed", async () => {
    const heads = new MemoryHeads();
    const one = signStatement(statementFor(first.facts));
    heads.append(one);
    heads.append(
      signStatement(
        statementFor(first.facts, {
          sequence: 2,
          previousStatementSha256: statementSha256(one.statement),
        }),
      ),
    );
    const head = await readHead(heads, trustedKeys, first.facts.document);
    if (typeof head === "string") throw new Error(head);
    expect(head.bytes).not.toBe(signedStatementBytes(one));
    expect(head.statement.sequence).toBe(2);
  });

  it("fails closed on a malformed entry rather than stepping over it", async () => {
    const document = first.facts.document;
    expect(
      chooseHead(document, [`${headPrefix(document)}000000000001`, `${headPrefix(document)}2`]),
    ).toBe("malformed-head");
    expect(chooseHead(document, [`${headPrefix(document)}000000000000`])).toBe("malformed-head");
    expect(chooseHead(document, [`${headPrefix(other.facts.document)}000000000001`])).toBe(
      "malformed-head",
    );
    expect(chooseHead(document, [])).toBe("no-head");

    const heads = new MemoryHeads();
    const signed = signStatement(statementFor(first.facts));
    // Stored under sequence 2 while it says 1.
    heads.objects.set(headObjectName(document, 2), signedStatementBytes(signed));
    expect(await readHead(heads, trustedKeys, document)).toBe("malformed-head");

    const garbage = new MemoryHeads();
    garbage.objects.set(headObjectName(document, 1), "not json");
    expect(await readHead(garbage, trustedKeys, document)).toBe("malformed-head");

    const forged = new MemoryHeads();
    forged.append(signStatement(statementFor(first.facts), otherKeys().privateKey));
    expect(await readHead(forged, trustedKeys, document)).toBe("bad-signature");

    // Another document's statement under this document's prefix.
    const misplaced = signStatement(statementFor(other.facts));
    expect(
      checkHeadEntry(
        { name: headObjectName(document, 1), sequence: 1 },
        document,
        await verifyWithKeys(signedStatementBytes(misplaced), trustedKeys),
      ),
    ).toBe("malformed-head");
  });

  it("names its entries by a twelve-digit sequence, and refuses one outside it", () => {
    expect(headObjectName(first.facts.document, 7)).toBe(
      `docs/${sha256(first.facts.document)}/000000000007`,
    );
    expect(() => headObjectName(first.facts.document, 0)).toThrow(RangeError);
    expect(() => headObjectName(first.facts.document, 1e12)).toThrow(RangeError);
  });
});

describe("the sections a statement names", () => {
  it("are every published narrative, in the mapping's order, hashed as the store will hold them", () => {
    const composition = first.transformed.documentBundle.entry[0]?.resource;
    if (composition === undefined || !isComposition(composition)) throw new Error("no Composition");
    const sections = publishedSections(composition.section, mapping);
    expect(sections).toEqual(first.facts.sections);
    // The transform copies each narrative byte for byte, so the hashes are the submission's.
    const submitted = new Map(
      first.gate.submission.provenance.sections.map((s) => [s.sourceKey, s.narrativeDivSha256]),
    );
    for (const { sourceKey, narrativeDivSha256 } of first.facts.sections) {
      expect(narrativeDivSha256).toBe(submitted.get(sourceKey));
    }
  });

  it("cannot describe a record with a narrative section the mapping does not name", () => {
    const composition = structuredClone(first.transformed.documentBundle.entry[0]?.resource);
    if (composition === undefined || !isComposition(composition)) throw new Error("no Composition");
    composition.section.push({
      id: "unlisted",
      title: "Unlisted",
      code: { coding: [] },
      text: { status: "additional", div: '<div xmlns="http://www.w3.org/1999/xhtml">x</div>' },
    });
    expect(publishedSections(composition.section, mapping)).toBeUndefined();
  });
});

describe("the review", () => {
  it("shows a first approval's every section as added, verbatim, against no baseline", () => {
    const review = buildReview("dev", first.gate, first.facts, undefined);
    expect(ReviewRecordSchema.parse(review)).toEqual(review);
    expect(review.baseline).toBeNull();
    expect(review.sections.map(({ change }) => change)).toEqual(review.sections.map(() => "added"));
    const html = renderReview(review);
    for (const section of review.sections) expect(html).toContain(section.div);
    expect(html).toContain("Content-Security-Policy");
    expect(renderReview(review)).toBe(html);
  });

  it("marks what changed since the head, and shows the full text of every section", () => {
    const head = verified(signedStatementBytes(signStatement(statementFor(first.facts))));
    const review = buildReview("dev", second.gate, second.facts, head);
    expect(review.baseline).toEqual({
      sequence: 1,
      statementSha256: head.statementSha256,
      submissionId: first.facts.submissionId,
    });
    const before = new Map(first.facts.sections.map((s) => [s.sourceKey, s.narrativeDivSha256]));
    for (const section of review.sections) {
      const expected =
        before.get(section.sourceKey) === undefined
          ? "added"
          : before.get(section.sourceKey) === section.narrativeDivSha256
            ? "unchanged"
            : "changed";
      expect(section.change).toBe(expected);
    }
    expect(review.sections.some(({ change }) => change === "changed")).toBe(true);
    expect(review.sections.some(({ change }) => change === "unchanged")).toBe(true);
  });

  it("lists a section the head approved that the record no longer carries", () => {
    const extra = { sourceKey: "smpc.removed.example", narrativeDivSha256: "c".repeat(64) };
    const head = verified(
      signedStatementBytes(
        signStatement(statementFor(first.facts, { sections: [...first.facts.sections, extra] })),
      ),
    );
    expect(buildReview("dev", first.gate, first.facts, head).removed).toEqual([extra]);
  });

  // A stale review: built against an older head, it does not hash as the review rebuilt now.
  it("hashes differently when the head it was built against has moved", () => {
    const one = verified(signedStatementBytes(signStatement(statementFor(first.facts))));
    const two = verified(
      signedStatementBytes(
        signStatement(
          statementFor(first.facts, { sequence: 2, previousStatementSha256: one.statementSha256 }),
        ),
      ),
    );
    const against = (head: VerifiedStatement) =>
      reviewSha256(renderReview(buildReview("dev", second.gate, second.facts, head)));
    expect(against(one)).not.toBe(against(two));
  });

  it("escapes every field that is not published narrative", () => {
    const review = buildReview("dev", first.gate, first.facts, undefined);
    const section = review.sections[0];
    if (section === undefined) throw new Error("no section");
    const html = renderReview({ ...review, sections: [{ ...section, title: "<b>x</b>" }] });
    expect(html).toContain("&lt;b&gt;x&lt;/b&gt;");
    expect(html).not.toContain("<b>x</b>");
  });
});

describe("what the review refuses to build", () => {
  it("a record with no Composition, an authority import's, or one whose check did not pass", () => {
    const empty = {
      ...first.transformed,
      documentBundle: { ...first.transformed.documentBundle, entry: [] },
    };
    expect(() => recordFacts(first.gate, empty, mapping)).toThrow(/no Composition/);
    const imported = structuredClone(first.gate);
    (imported.submission.provenance as { sourceDocument: { kind: string } }).sourceDocument.kind =
      "authority-publication";
    expect(() => buildReview("dev", imported, first.facts, undefined)).toThrow(/drawn record/);
    const failed = structuredClone(first.gate);
    (failed.report as { status: string }).status = "failed";
    expect(() => buildReview("dev", failed, first.facts, undefined)).toThrow(/passed fidelity/);
  });
});

describe("the approver map", () => {
  it("normalises IAP's subject form to the ID token's", () => {
    expect(normaliseSubject(`accounts.google.com:${SUBJECT}`)).toBe(SUBJECT);
    const map = parseApproverMap(
      JSON.stringify({ [`accounts.google.com:${SUBJECT}`]: { role: "qa-reviewer", email: EMAIL } }),
    );
    expect(map.entries.get(SUBJECT)?.role).toBe("qa-reviewer");
    expect(map.sha256).toBe(sha256({ [SUBJECT]: { role: "qa-reviewer", email: EMAIL } }));
  });

  it("refuses one person under two keys, an e-mail as a key, a capitalised address or an unknown field", () => {
    const entry = { role: "content-reviewer", email: EMAIL };
    expect(() =>
      parseApproverMap(
        JSON.stringify({ [SUBJECT]: entry, [`accounts.google.com:${SUBJECT}`]: entry }),
      ),
    ).toThrow(/twice/);
    expect(() => parseApproverMap(JSON.stringify({ [EMAIL]: entry }))).toThrow();
    expect(() =>
      parseApproverMap(JSON.stringify({ [SUBJECT]: { ...entry, email: "Approver@khs.dev" } })),
    ).toThrow();
    expect(() =>
      parseApproverMap(JSON.stringify({ [SUBJECT]: { ...entry, organisation: "x" } })),
    ).toThrow();
    expect(() =>
      parseApproverMap(JSON.stringify({ [SUBJECT]: { ...entry, role: "admin" } })),
    ).toThrow();
  });
});

describe("the signer's decision", () => {
  function request(overrides: Partial<ApprovalRequest> = {}): ApprovalRequest {
    const review = buildReview("dev", first.gate, first.facts, undefined);
    const hash = reviewSha256(renderReview(review));
    return {
      environment: "dev",
      kind: "approve",
      reviewSha256: hash,
      approver: { sub: SUBJECT, email: EMAIL, name: "Synthetic Approver" },
      approvers: approvers(),
      review,
      rebuiltReviewSha256: hash,
      head: undefined,
      signedAt: "2026-10-06T12:00:00.000Z",
      signer: { imageDigest: IMAGE_DIGEST, keyVersion: KEY_VERSION },
      ...overrides,
    };
  }

  it("makes the first statement of a document sequence 1, with no previous head", () => {
    const statement = decideApproval(request());
    if (typeof statement === "string") throw new Error(statement);
    expect(statement).toMatchObject({
      sequence: 1,
      previousStatementSha256: null,
      document: first.facts.document,
      sections: first.facts.sections,
      approvedContentSha256: first.facts.approvedContentSha256,
      approver: { sub: SUBJECT, role: "content-reviewer", approverMapSha256: approvers().sha256 },
      manifestation: { name: "Synthetic Approver", email: EMAIL },
      environment: "dev",
    });
    expect(checkStatement(statement, { environment: "dev", ...first.facts })).toBeUndefined();
  });

  it("extends the head it was reviewed against", () => {
    const head = verified(signedStatementBytes(signStatement(statementFor(first.facts))));
    const review = buildReview("dev", second.gate, second.facts, head);
    const hash = reviewSha256(renderReview(review));
    const statement = decideApproval(
      request({ review, reviewSha256: hash, rebuiltReviewSha256: hash, head }),
    );
    if (typeof statement === "string") throw new Error(statement);
    expect(statement.sequence).toBe(2);
    expect(statement.previousStatementSha256).toBe(head.statementSha256);
  });

  it("refuses a stale review hash", () => {
    expect(decideApproval(request({ reviewSha256: "d".repeat(64) }))).toBe("stale-review");
  });

  it("refuses an unmapped subject, and a mapped one whose verified address is not the map's", () => {
    expect(decideApproval(request({ approver: { sub: "1", email: EMAIL, name: "Someone" } }))).toBe(
      "unmapped-approver",
    );
    expect(
      decideApproval(
        request({ approver: { sub: SUBJECT, email: "someone.else@khs.dev", name: "Someone" } }),
      ),
    ).toBe("approver-email-mismatch");
  });

  it("refuses a kind phase 1 does not build", () => {
    expect(decideApproval(request({ kind: "withdraw" }))).toBe("kind-not-built");
    expect(decideApproval(request({ kind: "request" }))).toBe("kind-not-built");
  });
});

describe("the versioned link", () => {
  it("carries the signed statement's bytes for its own version only", () => {
    const signed = signStatement(statementFor(first.facts));
    const link = approvalLinkProvenance("bundle-1", "v2", signed);
    expect(link.id).toBe(approvalLinkId("bundle-1", "v2"));
    expect(approvalLinkBytes(link, "bundle-1", "v2")).toBe(signedStatementBytes(signed));
    expect(approvalLinkBytes(link, "bundle-1", "v1")).toBeUndefined();
    expect(
      approvalLinkBytes({ ...link, target: [{ reference: "Bundle/bundle-1" }] }, "bundle-1", "v2"),
    ).toBeUndefined();
    expect(approvalLinkBytes({ ...link, signature: [] }, "bundle-1", "v2")).toBeUndefined();
  });
});
