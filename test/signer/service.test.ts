import { constants, sign as rsaSign } from "node:crypto";
import { readFileSync } from "node:fs";

import { beforeAll, describe, expect, it } from "vitest";

import {
  headObjectName,
  readHead,
  signedStatementBytes,
  verifyWithKeys,
  type HeadSource,
} from "../../src/approval/statement.js";
import { loadEmaMapping, type EmaMapping } from "../../src/fhir/mapping.js";
import type { DocumentSubmissionInput, SubmissionRef } from "../../src/contracts/index.js";
import type { VerifiedEvent, IdentityRefusal } from "../../src/signer/identity.js";
import { SubmissionReadError } from "../../src/gcp/submission-reader.js";
import {
  createSignerApp,
  handleAddOnEvent,
  MAX_EVENT_BYTES,
  type SignerDeps,
} from "../../src/signer/service.js";
import { sha256, sha256Utf8 } from "../../src/lib/hash.js";
import {
  EMAIL,
  IMAGE_DIGEST,
  KEY_VERSION,
  MemoryHeads,
  SUBJECT,
  approvableRecord,
  approvalKeys,
  approvers,
  otherKeys,
  trustedKeys,
  type ApprovableRecord,
} from "../support/approval.js";

// Build step 3 of docs/design/approval.md: the signer, with an in-memory heads bucket and evidence
// bucket and a local key standing in for Cloud KMS. Evidence asked for: a signed statement and its
// review file in the evidence bucket; a racing second approval refused.

const BUCKET = "submissions";
const ENDPOINT = "https://ema-flow-dev-signer-123456789012.europe-west4.run.app";

let mapping: EmaMapping;
let first: ApprovableRecord;
let second: ApprovableRecord;

beforeAll(async () => {
  mapping = await loadEmaMapping();
  first = approvableRecord(mapping, { product: "synthetic-paracetamol", version: 1 });
  second = approvableRecord(mapping, { product: "synthetic-paracetamol", version: 2 });
});

function ref(record: ApprovableRecord): SubmissionRef {
  return {
    uri: `gs://${BUCKET}/${record.facts.submissionId}.json`,
    sha256: sha256(record.parts.submission),
  };
}

type Harness = { deps: SignerDeps; heads: MemoryHeads; evidence: MemoryHeads; tokens: string[] };

function harness(overrides: Partial<SignerDeps> = {}): Harness {
  const heads = new MemoryHeads();
  const evidence = new MemoryHeads();
  const tokens: string[] = [];
  const submissions = new Map<string, DocumentSubmissionInput>(
    [first, second].map((record) => [
      ref(record).uri,
      {
        submission: record.parts.submission,
        fidelityReport: record.parts.fidelityReport,
        sourceText: record.parts.sourceText,
      },
    ]),
  );
  let clicks = 0;
  const deps: SignerDeps = {
    environment: "dev",
    mapping,
    allowSyntheticSources: true,
    approvers: approvers(),
    identity: {
      verify: (): Promise<VerifiedEvent | IdentityRefusal> => {
        clicks += 1;
        const token = `user-token-${String(clicks)}`;
        tokens.push(token);
        return Promise.resolve({
          approver: { sub: SUBJECT, email: EMAIL, name: "Synthetic Approver" },
          userTokenSha256: sha256Utf8(token),
        });
      },
    },
    submissions: {
      read: (submissionRef) => {
        const parts = submissions.get(submissionRef.uri);
        if (parts === undefined) throw new Error("no such submission");
        return Promise.resolve(parts);
      },
    },
    heads,
    evidence,
    evidenceBucket: "evidence",
    keys: trustedKeys,
    sign: (message) =>
      Promise.resolve(
        rsaSign("sha256", message, {
          key: approvalKeys().privateKey,
          padding: constants.RSA_PKCS1_PSS_PADDING,
          saltLength: 32,
        }),
      ),
    signer: { imageDigest: IMAGE_DIGEST, keyVersion: KEY_VERSION },
    endpointUrl: ENDPOINT,
    now: () => new Date("2026-10-06T12:00:00.000Z"),
    ...overrides,
  };
  return { deps, heads, evidence, tokens };
}

function message(text: string): unknown {
  return {
    authorizationEventObject: { userIdToken: "token" },
    chat: { messagePayload: { message: { text } } },
  };
}

function click(parameters: Record<string, string>): unknown {
  return {
    authorizationEventObject: { userIdToken: "token" },
    commonEventObject: { parameters },
    chat: { buttonClickedPayload: { message: {} } },
  };
}

type Card = {
  hostAppDataAction: {
    chatDataAction: {
      createMessageAction: {
        message: {
          text: string;
          cardsV2?: {
            card: {
              sections: {
                widgets: {
                  buttonList?: {
                    buttons: {
                      onClick: {
                        openLink?: { url: string };
                        action?: { function: string; parameters: { key: string; value: string }[] };
                      };
                    }[];
                  };
                }[];
              }[];
            };
          }[];
        };
      };
    };
  };
};

function text(body: unknown): string {
  return (body as Card).hostAppDataAction.chatDataAction.createMessageAction.message.text;
}

// The Approve button's parameters, as the card sent them.
function approveParameters(body: unknown): Record<string, string> {
  const buttons =
    (body as Card).hostAppDataAction.chatDataAction.createMessageAction.message.cardsV2?.[0]?.card
      .sections[0]?.widgets[1]?.buttonList?.buttons ?? [];
  const action = buttons[1]?.onClick.action;
  expect(action?.function).toBe(ENDPOINT);
  return Object.fromEntries((action?.parameters ?? []).map(({ key, value }) => [key, value]));
}

async function review(h: Harness, record: ApprovableRecord) {
  const { uri, sha256: hash } = ref(record);
  const answer = await handleAddOnEvent(h.deps, "Bearer system", message(`review ${uri} ${hash}`));
  expect(answer.status).toBe(200);
  return answer.body;
}

describe("a review", () => {
  it("is stored once under its hash, and the card links to it and names it in the Approve button", async () => {
    const h = harness();
    const body = await review(h, first);
    const parameters = approveParameters(body);
    const stored = h.evidence.objects.get(`reviews/${parameters.reviewSha256 ?? ""}`);
    expect(stored).toBeDefined();
    expect(sha256Utf8(stored ?? "")).toBe(parameters.reviewSha256);
    expect(h.evidence.types.get(`reviews/${parameters.reviewSha256 ?? ""}`)).toBe(
      "text/html; charset=utf-8",
    );
    for (const section of first.facts.sections) {
      expect(stored).toContain(section.narrativeDivSha256);
    }
    expect(parameters).toMatchObject({
      action: "approve",
      kind: "approve",
      submissionUri: ref(first).uri,
      submissionSha256: ref(first).sha256,
    });
    const link = (body as Card).hostAppDataAction.chatDataAction.createMessageAction.message
      .cardsV2?.[0]?.card.sections[0]?.widgets[1]?.buttonList?.buttons[0]?.onClick.openLink?.url;
    expect(link).toBe(
      `https://storage.cloud.google.com/evidence/reviews/${parameters.reviewSha256 ?? ""}`,
    );
    // Nothing is signed by a review.
    expect([...h.heads.objects.keys()]).toEqual([]);
    // Asking again finds the same bytes stored, and answers the same card.
    expect(await review(h, first)).toEqual(body);
  });

  it("is refused when a stored review under its hash is not those bytes", async () => {
    const h = harness();
    const parameters = approveParameters(await review(h, first));
    h.evidence.objects.set(`reviews/${parameters.reviewSha256 ?? ""}`, "other bytes");
    expect(text(await review(h, first))).toBe("Not signed: review-object-differs.");
  });
});

describe("an approval", () => {
  it("signs the statement, appends the head and copies it to approvals/", async () => {
    const h = harness();
    const parameters = approveParameters(await review(h, first));
    const answer = await handleAddOnEvent(h.deps, "Bearer system", click(parameters));
    expect(answer.status).toBe(200);
    expect(text(answer.body)).toMatch(/^Signed: approval 1 of document /);

    const head = await readHead(h.heads, trustedKeys, first.facts.document);
    if (typeof head === "string") throw new Error(head);
    expect(head.statement).toMatchObject({
      sequence: 1,
      previousStatementSha256: null,
      environment: "dev",
      submissionId: first.facts.submissionId,
      approvedContentSha256: first.facts.approvedContentSha256,
      sections: first.facts.sections,
      reviewSha256: parameters.reviewSha256,
      approver: { sub: SUBJECT, role: "content-reviewer" },
      manifestation: { name: "Synthetic Approver", email: EMAIL },
      signer: { imageDigest: IMAGE_DIGEST, keyVersion: KEY_VERSION },
    });
    expect(h.evidence.objects.get(`approvals/${head.statementSha256}`)).toBe(head.bytes);
    expect(h.heads.objects.has(`tokens/${sha256Utf8(h.tokens[1] ?? "")}`)).toBe(true);
    // The review the statement names is in the evidence bucket, hashing to it.
    expect(sha256Utf8(h.evidence.objects.get(`reviews/${head.statement.reviewSha256}`) ?? "")).toBe(
      head.statement.reviewSha256,
    );
  });

  it("extends the head with the next version's approval, showing what changed", async () => {
    const h = harness();
    await handleAddOnEvent(
      h.deps,
      "Bearer system",
      click(approveParameters(await review(h, first))),
    );
    const parameters = approveParameters(await review(h, second));
    const answer = await handleAddOnEvent(h.deps, "Bearer system", click(parameters));
    expect(text(answer.body)).toMatch(/^Signed: approval 2 /);
    const head = await readHead(h.heads, trustedKeys, second.facts.document);
    if (typeof head === "string") throw new Error(head);
    expect(head.statement.sequence).toBe(2);
    expect(head.statement.previousStatementSha256).not.toBeNull();
    expect(head.statement.submissionId).toBe(second.facts.submissionId);
  });

  // A click on a review built before the head moved: its hash is no longer the rebuilt review's.
  it("refuses a stale review, and consumes no token", async () => {
    const h = harness();
    const stale = approveParameters(await review(h, second));
    await handleAddOnEvent(
      h.deps,
      "Bearer system",
      click(approveParameters(await review(h, first))),
    );
    const tokensBefore = [...h.heads.objects.keys()].filter((name) => name.startsWith("tokens/"));
    const answer = await handleAddOnEvent(h.deps, "Bearer system", click(stale));
    expect(text(answer.body)).toBe("Not signed: stale-review.");
    expect([...h.heads.objects.keys()].filter((name) => name.startsWith("tokens/"))).toEqual(
      tokensBefore,
    );
  });

  // Two approvals that read the same head: the second's create-if-absent finds the first's entry.
  it("refuses a racing second approval of the same sequence", async () => {
    const h = harness();
    const parameters = approveParameters(await review(h, first));
    // A heads view frozen before the first approval is written, as a racing request read it.
    const snapshot: HeadSource = {
      list: () => Promise.resolve([]),
      read: () => Promise.resolve(undefined),
    };
    const racing: SignerDeps = {
      ...h.deps,
      heads: { ...snapshot, create: (name, bytes, type) => h.heads.create(name, bytes, type) },
    };
    await handleAddOnEvent(h.deps, "Bearer system", click(parameters));
    const answer = await handleAddOnEvent(racing, "Bearer system", click(parameters));
    expect(text(answer.body)).toBe("Not signed: lost-race.");
    const heads = [...h.heads.objects.keys()].filter((name) => name.startsWith("docs/"));
    expect(heads).toEqual([headObjectName(first.facts.document, 1)]);
  });

  it("refuses a token used once already", async () => {
    const h = harness();
    const reused: SignerDeps = {
      ...h.deps,
      identity: {
        verify: () =>
          Promise.resolve({
            approver: { sub: SUBJECT, email: EMAIL, name: "Synthetic Approver" },
            userTokenSha256: sha256Utf8("the same token"),
          }),
      },
    };
    await handleAddOnEvent(
      reused,
      "Bearer system",
      click(approveParameters(await review(h, first))),
    );
    const answer = await handleAddOnEvent(
      reused,
      "Bearer system",
      click(approveParameters(await review(h, second))),
    );
    expect(text(answer.body)).toBe("Not signed: token-reused.");
  });

  it("writes no head when its own signature does not verify", async () => {
    const h = harness({
      sign: (message) =>
        Promise.resolve(
          rsaSign("sha256", message, {
            key: otherKeys().privateKey,
            padding: constants.RSA_PKCS1_PSS_PADDING,
            saltLength: 32,
          }),
        ),
    });
    const answer = await handleAddOnEvent(
      h.deps,
      "Bearer system",
      click(approveParameters(await review(h, first))),
    );
    expect(text(answer.body)).toBe("Not signed: signature-unverified.");
    expect([...h.heads.objects.keys()].some((name) => name.startsWith("docs/"))).toBe(false);
  });

  it("refuses an unreadable head rather than starting a new chain", async () => {
    const h = harness();
    h.heads.objects.set(headObjectName(first.facts.document, 1), "not a statement");
    expect(text(await review(h, first))).toBe("Not signed: head-unreadable.");
  });

  it("refuses an authority import's request, which phase 1 does not build", async () => {
    const h = harness({
      submissions: {
        read: () =>
          Promise.resolve({
            submission: JSON.parse(
              readFileSync("test/fixtures/contracts/canonical-submission-type1.json", "utf8"),
            ) as unknown,
            fidelityReport: {},
            sourceText: {},
          }),
      },
    });
    expect(text(await review(h, first))).toBe("Not signed: authority-import-not-built.");
  });
});

describe("who may ask", () => {
  it("answers 401 to a caller the identity check refuses, and signs nothing", async () => {
    const h = harness({ identity: { verify: () => Promise.resolve("system-token-rejected") } });
    const answer = await handleAddOnEvent(h.deps, undefined, message("review x y"));
    expect(answer).toEqual({ status: 401, body: { error: "unauthenticated" } });
  });

  it("refuses a verified person the approver map does not name", async () => {
    const h = harness({
      identity: {
        verify: () =>
          Promise.resolve({
            approver: { sub: "1", email: "someone@khs.dev", name: "Someone" },
            userTokenSha256: "0".repeat(64),
          }),
      },
    });
    const answer = await handleAddOnEvent(
      h.deps,
      "Bearer system",
      message(`review ${ref(first).uri} ${ref(first).sha256}`),
    );
    expect(text(answer.body)).toBe("Not signed: not-an-approver.");
    expect(h.evidence.objects.size).toBe(0);
  });

  it("answers a message that is not a review command with how to ask", async () => {
    const h = harness();
    const answer = await handleAddOnEvent(h.deps, "Bearer system", message("hello"));
    expect(text(answer.body)).toMatch(/^Send: review /);
  });
});

describe("the HTTP endpoint", () => {
  it("refuses a body that is not JSON or is larger than an event", async () => {
    const app = createSignerApp(harness().deps);
    const post = (body: string) => app.request("/", { method: "POST", body });
    expect((await post("{")).status).toBe(400);
    expect((await post(JSON.stringify({ x: "y".repeat(MAX_EVENT_BYTES) }))).status).toBe(400);
    expect(await (await app.request("/healthz")).json()).toEqual({
      status: "ok",
      service: "ema-flow-signer",
    });
  });

  it("answers a signed approval over HTTP, and the head it wrote verifies", async () => {
    const h = harness();
    const app = createSignerApp(h.deps);
    const parameters = approveParameters(await review(h, first));
    const response = await app.request("/", {
      method: "POST",
      headers: { authorization: "Bearer system" },
      body: JSON.stringify(click(parameters)),
    });
    expect(response.status).toBe(200);
    const bytes = h.heads.objects.get(headObjectName(first.facts.document, 1)) ?? "";
    const verified = await verifyWithKeys(bytes, trustedKeys);
    if (typeof verified === "string") throw new Error(verified);
    expect(signedStatementBytes(verified.signed)).toBe(bytes);
  });
});

describe("what the signer cannot read", () => {
  it("refuses an event that is not one, and a click that names no submission", async () => {
    const h = harness();
    expect((await handleAddOnEvent(h.deps, "Bearer system", [])).status).toBe(400);
    const answer = await handleAddOnEvent(h.deps, "Bearer system", click({ action: "approve" }));
    expect(text(answer.body)).toBe("Not signed: unrecognised-event.");
  });

  it("refuses a submission it cannot read, or that the gate refuses", async () => {
    const unreadable = harness({
      submissions: {
        read: () => Promise.reject(new SubmissionReadError("object-not-found", "submission")),
      },
    });
    expect(text(await review(unreadable, first))).toBe("Not signed: submission-unreadable.");

    const tampered = structuredClone(first.parts.submission);
    tampered.bundleSha256 = "0".repeat(64);
    const refusedByGate = harness({
      submissions: {
        read: () =>
          Promise.resolve({
            submission: tampered,
            fidelityReport: first.parts.fidelityReport,
            sourceText: first.parts.sourceText,
          }),
      },
    });
    expect(text(await review(refusedByGate, first))).toBe("Not signed: submission-refused.");
  });

  it("answers 500 and says nothing more when a store fails", async () => {
    const h = harness();
    const app = createSignerApp({
      ...h.deps,
      heads: {
        list: () => Promise.reject(new Error("Cloud Storage is down")),
        read: (name) => h.heads.read(name),
        create: (name, bytes, type) => h.heads.create(name, bytes, type),
      },
    });
    const response = await app.request("/", {
      method: "POST",
      headers: { authorization: "Bearer system" },
      body: JSON.stringify(message(`review ${ref(first).uri} ${ref(first).sha256}`)),
    });
    expect([response.status, await response.json()]).toEqual([500, { error: "unavailable" }]);
  });

  it("keeps an approval whose copy under approvals/ could not be written", async () => {
    const h = harness();
    const parameters = approveParameters(await review(h, first));
    const failing: SignerDeps = {
      ...h.deps,
      evidence: {
        list: (prefix) => h.evidence.list(prefix),
        read: (name) => h.evidence.read(name),
        create: (name, bytes, type) =>
          name.startsWith("approvals/")
            ? Promise.reject(new Error("Cloud Storage refused a write"))
            : h.evidence.create(name, bytes, type),
      },
    };
    const answer = await handleAddOnEvent(failing, "Bearer system", click(parameters));
    expect(text(answer.body)).toMatch(/^Signed: approval 1 /);
    expect(h.heads.objects.has(headObjectName(first.facts.document, 1))).toBe(true);
  });
});
