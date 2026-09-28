import { z } from "zod";

import {
  ContractVersion,
  Count,
  IsoDateTime,
  PrincipalId,
  Sha256Hex,
  Token,
  Uuid,
} from "./common.js";
import { QueryToolName } from "./query-tools.js";

// The record the verifiable-answer agent (docs/design/verifiable-answers.md, `agent/`) writes
// once per turn. It is published as a contract for the same reason the query service's audit
// record is: it is the evidence that a turn was checked, and a reader who holds both records
// can join them on `turnId` and reconstruct which tool calls a turn made and what the
// post-check concluded — without either record carrying a word of what was asked or answered.
//
// What is absent is the point. No narrative, no quote, no argument value, not even a digest of a
// quote (a digest of a quote is a way of asking whether a document contains a sentence). Counts,
// closed flag names, identities, and durations only.
//
// 1.1.0 (minor: optional fields, and enum members nothing in Zone B branches on — ADR 0002,
// "Versioning"): `outcome` and `errorClass`, so a turn that ended without an answer still leaves
// a record saying why; `principalDigest`, for the session user id `principal` may not carry
// (Gemini Enterprise's is an e-mail address); `assistantFlags`, what was removed from or noticed
// in the assistant's own words; and three verification flags.
//
// 1.2.0 (minor: two optional fields; audit C-9): `contractVersion`, the version of this contract
// the record was written under, and `queryToolsVersion`, the version of the query-tools contract
// the agent checked the tools' answers against. A `match` meant a different thing under
// query-tools 2.0.0 and 2.0.1, and no record said which rule decided it.

export const AGENT_TURN_VERSION = "1.2.0";

// How one tool call went, as the agent saw it: the transport failed, the tool answered with the
// contract's error shape, the result was not an object, the result did not validate against the
// contract, or it validated (`ok`).
export const AgentToolOutcome = z
  .enum(["ok", "schema-invalid", "tool-error", "transport-error", "not-an-object"])
  .meta({ id: "AgentToolOutcome" });

export const AgentToolCallSchema = z
  .strictObject({
    tool: QueryToolName,
    outcome: AgentToolOutcome,
    durationMs: Count,
    resultCount: Count,
  })
  .meta({
    id: "AgentToolCall",
    description:
      "One tool call from a turn: which tool, how it went, how long. Never what was asked.",
  });

// Why a quoted span was not shown as verified. `no-match`: verify_quote answered no-match;
// `verification-missing`: the span was never checked; `verification-unavailable`: verify_quote
// failed; `section-mismatch` / `document-mismatch`: the match was found, but not where the
// citation said; `empty-block`: a quotation block carried no text. Since 1.1.0:
// `coverage-gap`: the matches do not cover the block exactly, chunk by chunk, from its first
// code point to its last (a match with no location, or at other offsets than the chunk's own);
// `checksum-mismatch`: a hash or the normalisation version disagrees with the section cited,
// the chunk sent, or the version the agent was built against; `table-not-quotable`: part of the
// block is a table or a picture, which verify_quote refuses to compare (its grid markers are the
// scanner's, not a reader's), so that part was not sent.
export const VerificationFlag = z
  .enum([
    "no-match",
    "verification-missing",
    "verification-unavailable",
    "section-mismatch",
    "document-mismatch",
    "empty-block",
    "coverage-gap",
    "checksum-mismatch",
    "table-not-quotable",
  ])
  .meta({ id: "VerificationFlag" });

// How the turn ended. `answered`: a checked answer was shown; `tools-unavailable`: the query
// tools never loaded, so the model was not called; `model-failed`: the model call failed;
// `turn-id-missing`: the turn had no usable turn id, so nothing could be joined or checked;
// `internal-error`: the agent failed while checking, and the reader was told the answer could
// not be verified. Only `answered` shows label content.
export const AgentTurnOutcome = z
  .enum(["answered", "tools-unavailable", "model-failed", "turn-id-missing", "internal-error"])
  .meta({ id: "AgentTurnOutcome" });

// What the agent removed from, or noticed in, the assistant's own words before showing them: a
// line opening with a label reserved for checked text; a 64-hex checksum; a document identifier
// (`bundleId`, `versionId` and the like) with its value; label text of eight or more words
// repeated outside a checked block.
export const AssistantFlag = z
  .enum(["reserved-label-removed", "checksum-removed", "identifier-removed", "label-text-repeated"])
  .meta({ id: "AssistantFlag" });

// An exception's class name, never its message: a message can carry what was asked.
export const ErrorClassName = z
  .string()
  .regex(/^[A-Za-z_][A-Za-z0-9_]{0,127}$/)
  .meta({ id: "ErrorClassName" });

export const AgentTurnRecordSchema = z
  .strictObject({
    service: z.literal("ema-flow-agent"),
    serviceVersion: Token,
    // The agent-turn version the record was written under, and the query-tools version whose
    // answers the turn validated and whose `match` rule its verification stamps rest on: the
    // agent's vendored copies of the two contracts. Optional in the contract, which gained them
    // at 1.2.0; the agent always writes both.
    contractVersion: ContractVersion.optional(),
    queryToolsVersion: ContractVersion.optional(),
    at: IsoDateTime,
    // The session's user id when it is an opaque identifier; otherwise (an e-mail address, which
    // is what Gemini Enterprise supplies) the fixed value `session-user-withheld`. Either way it
    // is the platform's assertion, not a verified identity: the query service's records of the
    // same `turnId` carry the principal it verified from the user's own token.
    principal: PrincipalId,
    // HMAC-SHA256 of a withheld session user id under the deployment's key, when one is
    // configured: one user gives one digest, and nobody without the key can test an address
    // against it.
    principalDigest: Sha256Hex.optional(),
    // Sent to the query service as the `X-Query-Turn-Id` header on every call of the turn, so
    // the service's audit records carry the same value. The nil UUID when the turn had none.
    turnId: Uuid,
    outcome: AgentTurnOutcome.optional(),
    // The class of the exception that ended the turn early, or that stopped its full record
    // being built: enough to tell a bug from an outage, and never the exception's message.
    errorClass: ErrorClassName.optional(),
    tools: z.array(AgentToolCallSchema).max(200),
    spansVerified: Count,
    spansFlagged: Count,
    // Sections the model asked for that were dropped before composing (a result that failed
    // the contract, for instance) — the answer was built from fewer sources than were fetched.
    sectionsDropped: Count,
    // The distinct flags raised across the turn, sorted; empty exactly when spansFlagged is 0.
    flags: z.array(VerificationFlag).max(VerificationFlag.options.length),
    // The distinct assistant flags, sorted.
    assistantFlags: z.array(AssistantFlag).max(AssistantFlag.options.length).optional(),
    durationMs: Count,
  })
  .meta({
    id: "AgentTurnRecord",
    description:
      "One structured line per assistant turn: the principal, how the turn ended, the tool calls made, how many quoted spans verified or were flagged, and which flags. Never narrative, never a quote, never an argument.",
  });

export type AgentToolCall = z.infer<typeof AgentToolCallSchema>;
export type AgentTurnRecord = z.infer<typeof AgentTurnRecordSchema>;
export type VerificationFlag = z.infer<typeof VerificationFlag>;
export type AgentTurnOutcome = z.infer<typeof AgentTurnOutcome>;
export type AssistantFlag = z.infer<typeof AssistantFlag>;
