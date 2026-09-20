import { z } from "zod";

import { Count, IsoDateTime, PrincipalId, Token, Uuid } from "./common.js";
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

export const AGENT_TURN_VERSION = "1.0.0";

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
// citation said; `empty-block`: a quotation block carried no text.
export const VerificationFlag = z
  .enum([
    "no-match",
    "verification-missing",
    "verification-unavailable",
    "section-mismatch",
    "document-mismatch",
    "empty-block",
  ])
  .meta({ id: "VerificationFlag" });

export const AgentTurnRecordSchema = z
  .strictObject({
    service: z.literal("ema-flow-agent"),
    serviceVersion: Token,
    at: IsoDateTime,
    principal: PrincipalId,
    // Sent to the query service as the `X-Query-Turn-Id` header on every call of the turn, so
    // the service's audit records carry the same value.
    turnId: Uuid,
    tools: z.array(AgentToolCallSchema).max(200),
    spansVerified: Count,
    spansFlagged: Count,
    // Sections the model asked for that were dropped before composing (a result that failed
    // the contract, for instance) — the answer was built from fewer sources than were fetched.
    sectionsDropped: Count,
    // The distinct flags raised across the turn, sorted; empty exactly when spansFlagged is 0.
    flags: z.array(VerificationFlag).max(VerificationFlag.options.length),
    durationMs: Count,
  })
  .meta({
    id: "AgentTurnRecord",
    description:
      "One structured line per assistant turn: the principal, the tool calls made, how many quoted spans verified or were flagged, and which flags. Never narrative, never a quote, never an argument.",
  });

export type AgentToolCall = z.infer<typeof AgentToolCallSchema>;
export type AgentTurnRecord = z.infer<typeof AgentTurnRecordSchema>;
export type VerificationFlag = z.infer<typeof VerificationFlag>;
