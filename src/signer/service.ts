import { randomUUID } from "node:crypto";

import { Hono } from "hono";
import { z } from "zod";

import {
  decideApproval,
  normaliseSubject,
  type ApproverMap,
  type SigningRefusal,
} from "../approval/approve.js";
import { buildReview, recordFacts, renderReview, reviewSha256 } from "../approval/review.js";
import {
  headObjectName,
  readHead,
  signedStatementBytes,
  statementSha256,
  verifyWithKeys,
  type KeySource,
  type VerifiedStatement,
} from "../approval/statement.js";
import {
  AUTHORITY_IMPORT_PREFIX,
  SubmissionRefSchema,
  SubmissionRejectedError,
  verifyDocumentSubmission,
  type ApprovalEnvironment,
  type ReviewRecord,
  type SubmissionRef,
} from "../contracts/index.js";
import type { EmaMapping } from "../fhir/mapping.js";
import {
  hasValidationErrors,
  validateCanonicalPreflight,
  validateEmaPreflight,
} from "../fhir/preflight.js";
import { TransformationError, transformType2ToEma } from "../fhir/transform.js";
import type { StoredObjects } from "../gcp/approval-store.js";
import { SubmissionReadError, type SubmissionReader } from "../gcp/submission-reader.js";
import { canonicalJson, sha256Utf8 } from "../lib/hash.js";
import { log } from "../lib/logger.js";
import type { ApproverIdentityVerifier } from "./identity.js";

// The approval signer (docs/design/approval.md, D3, D6 and D8; phase 1, build step 3): the HTTP
// endpoint of the Google Chat app built as a Workspace add-on. It answers two events.
//
// - A message `review <gs:// submission URI> <its SHA-256>`: the signer reads the submission, runs
//   the gate's checks and the crosswalk, reads the document's head, builds the review (D6), stores
//   it once under `reviews/<its SHA-256>` in the evidence bucket, and answers a card with a link to
//   it and an Approve button whose parameters name the submission and the review's hash.
// - The Approve click: the signer verifies the person again, rebuilds the review from the
//   submission and the current head, and signs only if it hashes to what the click names (a
//   review built against an older head does not: the loser of a race re-reviews). It consumes the
//   person's token, signs with the HSM key, verifies its own signature, and appends the head
//   create-if-absent (two approvals racing for one sequence cannot both be written), then copies
//   the statement to `approvals/`.
//
// The signer is the add-on's endpoint itself, not a receiver in front of it: the click's
// parameters are not signed by Google's tokens, so nothing may stand between them and the signer
// (the design's amendment, open question on the receiver). Every refusal is a closed code, in the
// reply and in the log; no narrative, token, e-mail or name is ever logged. Publishing is started
// by hand in phase 1: the signer calls nothing.

export const SIGNER_SERVICE_NAME = "ema-flow-signer";

export type SignerDeps = {
  environment: ApprovalEnvironment;
  mapping: EmaMapping;
  allowSyntheticSources: boolean;
  approvers: ApproverMap;
  identity: ApproverIdentityVerifier;
  submissions: SubmissionReader;
  // The heads bucket (heads, and consumed tokens under `tokens/`) and the evidence bucket
  // (`reviews/`, `approvals/`).
  heads: StoredObjects;
  evidence: StoredObjects;
  evidenceBucket: string;
  keys: KeySource;
  // Signs a message (a statement's canonical JSON) with the approval key version: RSA-PSS over its
  // SHA-256 (Cloud KMS in production, which is given the digest).
  sign: (message: Buffer) => Promise<Buffer>;
  signer: { imageDigest: string; keyVersion: string };
  // The URL the add-on calls, which a card's button calls back.
  endpointUrl: string;
  now?: () => Date;
};

export type Refusal =
  | SigningRefusal
  | "not-an-approver"
  | "unrecognised-event"
  | "submission-unreadable"
  | "submission-refused"
  | "authority-import-not-built"
  | "crosswalk-refused"
  | "preflight-refused"
  | "head-unreadable"
  | "review-object-differs"
  | "token-reused"
  | "signature-unverified"
  | "lost-race";

// The parts of a Workspace add-on event the signer reads; anything else is ignored. A Chat event
// carries either a message (`chat.messagePayload`) or a click (`chat.buttonClickedPayload`, with
// the button's parameters in `commonEventObject.parameters`).
const AddOnEventSchema = z.object({
  authorizationEventObject: z.object({ userIdToken: z.string().optional() }).optional(),
  commonEventObject: z
    .object({ parameters: z.record(z.string(), z.string()).optional() })
    .optional(),
  chat: z
    .object({
      messagePayload: z
        .object({
          message: z
            .object({ text: z.string().optional(), argumentText: z.string().optional() })
            .optional(),
        })
        .optional(),
      buttonClickedPayload: z.unknown().optional(),
    })
    .optional(),
});

const REVIEW_COMMAND = /^review\s+(gs:\/\/\S+)\s+([0-9a-f]{64})$/;

// A reply in Chat: one new message (Google, "Build a Google Chat app as a Google Workspace add-on",
// HTTP quickstart: hostAppDataAction.chatDataAction.createMessageAction).
function reply(message: Record<string, unknown>): Record<string, unknown> {
  return { hostAppDataAction: { chatDataAction: { createMessageAction: { message } } } };
}

function refused(code: Refusal): Record<string, unknown> {
  return reply({ text: `Not signed: ${code}.` });
}

type Prepared = {
  review: ReviewRecord;
  html: string;
  sha256: string;
  head: VerifiedStatement | undefined;
};

function logOutcome(event: string, fields: Record<string, string | undefined>): void {
  log(event === "signed" ? "info" : "warning", "Approval signer event", {
    service: SIGNER_SERVICE_NAME,
    stage: "signer",
    event,
    ...fields,
  });
}

// The review of a submission against its document's current head, built, stored once and hashed.
async function prepare(deps: SignerDeps, ref: SubmissionRef): Promise<Prepared | Refusal> {
  let parts;
  try {
    parts = await deps.submissions.read(ref, randomUUID());
  } catch (error) {
    if (error instanceof SubmissionReadError) return "submission-unreadable";
    throw error;
  }
  // An authority import's request is reviewed differently (the design's amendment of 2026-09-25)
  // and is not built: its submission is refused before its gate, which would fetch the authority.
  const source = (parts.submission as { provenance?: { sourceDocument?: { kind?: unknown } } })
    .provenance?.sourceDocument?.kind;
  if (source === "authority-publication") return "authority-import-not-built";
  let gate;
  try {
    gate = verifyDocumentSubmission(parts, deps.mapping.sourceCodeSystem, {
      allowSyntheticSources: deps.allowSyntheticSources,
    });
  } catch (error) {
    if (error instanceof SubmissionRejectedError) return "submission-refused";
    throw error;
  }
  if (gate.submission.bundle.identifier.value.startsWith(AUTHORITY_IMPORT_PREFIX)) {
    return "authority-import-not-built";
  }
  if (hasValidationErrors(validateCanonicalPreflight(gate.bundle, gate.submission.graphType))) {
    return "preflight-refused";
  }
  let transformed;
  try {
    transformed = transformType2ToEma(gate.bundle, deps.mapping);
  } catch (error) {
    if (error instanceof TransformationError) return "crosswalk-refused";
    throw error;
  }
  if (
    hasValidationErrors(
      validateEmaPreflight(transformed.list, transformed.documentBundle, deps.mapping),
    )
  ) {
    return "preflight-refused";
  }
  const facts = recordFacts(gate, transformed, deps.mapping);
  const head = await readHead(deps.heads, deps.keys, facts.document);
  if (typeof head === "string" && head !== "no-head") return "head-unreadable";
  const current = typeof head === "string" ? undefined : head;
  const review = buildReview(deps.environment, gate, facts, current);
  const html = renderReview(review);
  const hash = reviewSha256(html);
  // Stored once: an existing review must be these bytes, or what the approver opens is not what
  // they would sign.
  const name = `reviews/${hash}`;
  if ((await deps.evidence.create(name, html, "text/html; charset=utf-8")) === "exists") {
    const stored = await deps.evidence.read(name);
    if (stored === undefined || sha256Utf8(stored) !== hash) return "review-object-differs";
  }
  return { review, html, sha256: hash, head: current };
}

function reviewCard(deps: SignerDeps, ref: SubmissionRef, prepared: Prepared): unknown {
  const { review } = prepared;
  const count = (change: string): number =>
    review.sections.filter((section) => section.change === change).length;
  const summary = [
    `Document ${review.document.identifier.value} (${review.document.language}), submission ${review.submissionId}.`,
    review.baseline === null
      ? "No current approval: every section is new."
      : `Against approval ${String(review.baseline.sequence)}: ${String(count("changed"))} changed, ${String(count("added"))} added, ${String(count("unchanged"))} unchanged, ${String(review.removed.length)} removed.`,
    `Review SHA-256 ${prepared.sha256}.`,
  ].join(" ");
  const link = `https://storage.cloud.google.com/${deps.evidenceBucket}/reviews/${prepared.sha256}`;
  return reply({
    text: summary,
    cardsV2: [
      {
        cardId: "approval-review",
        card: {
          header: { title: "Approval review", subtitle: review.mappingVersion },
          sections: [
            {
              widgets: [
                { textParagraph: { text: summary } },
                {
                  buttonList: {
                    buttons: [
                      { text: "Open the review", onClick: { openLink: { url: link } } },
                      {
                        text: "Approve",
                        onClick: {
                          action: {
                            function: deps.endpointUrl,
                            parameters: [
                              { key: "action", value: "approve" },
                              { key: "kind", value: "approve" },
                              { key: "submissionUri", value: ref.uri },
                              { key: "submissionSha256", value: ref.sha256 },
                              { key: "reviewSha256", value: prepared.sha256 },
                            ],
                          },
                        },
                      },
                    ],
                  },
                },
              ],
            },
          ],
        },
      },
    ],
  });
}

async function approve(
  deps: SignerDeps,
  parameters: Record<string, string>,
  verified: { approver: { sub: string; email: string; name: string }; userTokenSha256: string },
): Promise<{ body: unknown; event: string; statementSha256?: string }> {
  const ref = SubmissionRefSchema.safeParse({
    uri: parameters.submissionUri,
    sha256: parameters.submissionSha256,
  });
  const clicked = z
    .object({ kind: z.string().max(32), reviewSha256: z.string().regex(/^[0-9a-f]{64}$/) })
    .safeParse(parameters);
  if (!ref.success || !clicked.success) {
    return { body: refused("unrecognised-event"), event: "unrecognised-event" };
  }
  const prepared = await prepare(deps, ref.data);
  if (typeof prepared === "string") return { body: refused(prepared), event: prepared };

  const statement = decideApproval({
    environment: deps.environment,
    kind: clicked.data.kind,
    reviewSha256: clicked.data.reviewSha256,
    approver: verified.approver,
    approvers: deps.approvers,
    review: prepared.review,
    rebuiltReviewSha256: prepared.sha256,
    head: prepared.head,
    signedAt: (deps.now ?? (() => new Date()))().toISOString(),
    signer: deps.signer,
  });
  if (typeof statement === "string") return { body: refused(statement), event: statement };

  // One token, one signature: a captured token cannot sign a second time.
  const consumed = await deps.heads.create(
    `tokens/${verified.userTokenSha256}`,
    "",
    "text/plain; charset=utf-8",
  );
  if (consumed === "exists") return { body: refused("token-reused"), event: "token-reused" };

  const hash = statementSha256(statement);
  const signature = await deps.sign(Buffer.from(canonicalJson(statement), "utf8"));
  const bytes = signedStatementBytes({ statement, signatureBase64: signature.toString("base64") });
  // The signer checks its own signature against the key every reader will use before anything is
  // written: a signature no reader would accept is never a head.
  if (typeof (await verifyWithKeys(bytes, deps.keys)) === "string") {
    return { body: refused("signature-unverified"), event: "signature-unverified" };
  }
  const head = headObjectName(statement.document, statement.sequence);
  if ((await deps.heads.create(head, bytes, "application/json")) === "exists") {
    return { body: refused("lost-race"), event: "lost-race" };
  }
  // The head is the commit point. The copy under `approvals/` is evidence; a failure to write it
  // is logged, and the approval stands.
  try {
    await deps.evidence.create(`approvals/${hash}`, bytes, "application/json");
  } catch (error) {
    log("error", "Approval statement copy not written", {
      service: SIGNER_SERVICE_NAME,
      stage: "signer",
      statementSha256: hash,
      errorType: error instanceof Error ? error.name : typeof error,
    });
  }
  return {
    body: reply({
      text: `Signed: approval ${String(statement.sequence)} of document ${statement.document.identifier.value}, statement ${hash}. Publish it by running the pipeline on the same submission.`,
    }),
    event: "signed",
    statementSha256: hash,
  };
}

export async function handleAddOnEvent(
  deps: SignerDeps,
  authorization: string | undefined,
  payload: unknown,
): Promise<{ status: 200 | 400 | 401; body: unknown }> {
  const event = AddOnEventSchema.safeParse(payload);
  if (!event.success) return { status: 400, body: { error: "invalid-request" } };
  const bearer = /^Bearer ([\x21-\x7e]+)$/.exec(authorization ?? "")?.[1];
  const verified = await deps.identity.verify(
    bearer,
    event.data.authorizationEventObject?.userIdToken,
  );
  if (typeof verified === "string") {
    logOutcome("unauthenticated", { reason: verified });
    return { status: 401, body: { error: "unauthenticated" } };
  }
  const approverSub = normaliseSubject(verified.approver.sub);
  if (!deps.approvers.entries.has(approverSub)) {
    logOutcome("not-an-approver", { approverSub });
    return { status: 200, body: refused("not-an-approver") };
  }

  const parameters = event.data.commonEventObject?.parameters ?? {};
  if (event.data.chat?.buttonClickedPayload !== undefined && parameters.action === "approve") {
    const outcome = await approve(deps, parameters, verified);
    logOutcome(outcome.event, { approverSub, statementSha256: outcome.statementSha256 });
    return { status: 200, body: outcome.body };
  }

  const message = event.data.chat?.messagePayload?.message;
  const command = REVIEW_COMMAND.exec((message?.argumentText ?? message?.text ?? "").trim());
  const ref = SubmissionRefSchema.safeParse({ uri: command?.[1], sha256: command?.[2] });
  if (command === null || !ref.success) {
    return {
      status: 200,
      body: reply({ text: "Send: review <gs:// URI of the submission> <its SHA-256>." }),
    };
  }
  const prepared = await prepare(deps, ref.data);
  if (typeof prepared === "string") {
    logOutcome(prepared, { approverSub });
    return { status: 200, body: refused(prepared) };
  }
  logOutcome("reviewed", { approverSub });
  return { status: 200, body: reviewCard(deps, ref.data, prepared) };
}

// The add-on event is small; anything larger is not one.
export const MAX_EVENT_BYTES = 64 * 1_024;

export function createSignerApp(deps: SignerDeps): Hono {
  const app = new Hono();
  app.get("/healthz", (context) => context.json({ status: "ok", service: SIGNER_SERVICE_NAME }));
  app.post("/", async (context) => {
    const text = await context.req.text();
    if (Buffer.byteLength(text, "utf8") > MAX_EVENT_BYTES) {
      return context.json({ error: "invalid-request" }, 400);
    }
    let payload: unknown;
    try {
      payload = JSON.parse(text);
    } catch {
      return context.json({ error: "invalid-request" }, 400);
    }
    try {
      const { status, body } = await handleAddOnEvent(
        deps,
        context.req.header("authorization"),
        payload,
      );
      return context.json(body, status);
    } catch (error) {
      // Cloud Storage, KMS or the store failed: nothing is reported but the failure's type.
      log("error", "Approval signer failed", {
        service: SIGNER_SERVICE_NAME,
        stage: "signer",
        errorType: error instanceof Error ? error.name : typeof error,
      });
      return context.json({ error: "unavailable" }, 500);
    }
  });
  return app;
}
