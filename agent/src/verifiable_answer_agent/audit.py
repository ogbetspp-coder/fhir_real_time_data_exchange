"""One structured record per turn, and the discipline that keeps narrative out of it.

``TurnAuditRecord`` is the ``AgentTurnRecord`` of the published ``agent-turn`` contract
(``contracts/generated/agent-turn.schema.json``, vendored next to the query-tools schema): the
same field names, the same closed enumerations for tool names, outcomes and flags, and the same
patterns for ``serviceVersion``, ``principal`` and ``turnId``, so that a record this module
builds serialises to an instance the contract accepts. ``tests/test_audit.py`` validates an
emitted record against the vendored schema.

What is absent is the point. No narrative. No argument values — a ``verify_quote`` argument
*is* narrative, so not even a digest of one is carried, because a digest of a quote is a way of
asking whether a document contains a sentence. No token, ever, in any form.

``emit`` is the only writer in the package, and it refuses to write a record containing a
forbidden key at any depth. That check is not decoration: it is what makes "never log clinical
text" a property of the code rather than a rule someone remembers.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, Final, Literal, TextIO

from pydantic import BaseModel, ConfigDict, Field

from .contract import QueryToolName
from .postcheck import CheckedAnswer, VerificationFlag

__all__ = [
    "FORBIDDEN_KEYS",
    "NarrativeLeakError",
    "ToolCallRecord",
    "TurnAuditRecord",
    "emit",
    "turn_record",
]

AGENT_SERVICE: Final = "ema-flow-agent"

ToolOutcome = Literal["ok", "schema-invalid", "tool-error", "transport-error", "not-an-object"]
"""``AgentToolOutcome`` in the agent-turn contract."""

# The contract's patterns, repeated here so that a record the contract would refuse cannot be
# built in the first place. ``tests/test_audit.py`` checks each against the vendored schema.
TOKEN_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$"
PRINCIPAL_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"
UUID_PATTERN: Final = (
    r"^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
    r"|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
)
MAX_TOOL_CALLS: Final = 200
"""``tools.maxItems`` in the agent-turn contract. A turn that made more calls than this has no
record the contract accepts, so ``turn_record`` refuses to build one rather than truncate."""

FORBIDDEN_KEYS: Final[frozenset[str]] = frozenset(
    {
        # Narrative, in every name the contract or the transport gives it.
        "div",
        "text",
        "quote",
        "narrative",
        "title",
        "content",
        "structuredContent",
        "products",
        "assistant",
        "blocks",
        # Arguments, which for this tool surface can themselves be narrative.
        "args",
        "arguments",
        "argumentssha256",
        "input",
        "params",
        # Credentials.
        "authorization",
        "token",
        "bearer",
        "credential",
        "headers",
    }
)


class NarrativeLeakError(RuntimeError):
    """Raised instead of writing a record that carries a forbidden key."""


class ToolCallRecord(BaseModel):
    """``AgentToolCall``: which tool, how it went, how long — never what was asked."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: QueryToolName
    outcome: ToolOutcome
    duration_ms: int = Field(ge=0, serialization_alias="durationMs")
    result_count: int = Field(ge=0, serialization_alias="resultCount")


class TurnAuditRecord(BaseModel):
    """``AgentTurnRecord``: the spans verified and flagged, the principal, the durations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    service: Literal["ema-flow-agent"] = AGENT_SERVICE
    service_version: str = Field(pattern=TOKEN_PATTERN, serialization_alias="serviceVersion")
    at: str
    principal: str = Field(pattern=PRINCIPAL_PATTERN)
    turn_id: str = Field(pattern=UUID_PATTERN, serialization_alias="turnId")
    tools: tuple[ToolCallRecord, ...] = Field(max_length=MAX_TOOL_CALLS)
    spans_verified: int = Field(ge=0, serialization_alias="spansVerified")
    spans_flagged: int = Field(ge=0, serialization_alias="spansFlagged")
    sections_dropped: int = Field(ge=0, serialization_alias="sectionsDropped")
    flags: tuple[VerificationFlag, ...]
    duration_ms: int = Field(ge=0, serialization_alias="durationMs")


def turn_record(
    *,
    service_version: str,
    principal: str,
    turn_id: str,
    answer: CheckedAnswer,
    tools: tuple[ToolCallRecord, ...],
    sections_dropped: int,
    duration_ms: int,
    at: datetime | None = None,
) -> TurnAuditRecord:
    """Build the record from the checked answer. Counts and flag names only.

    Raises pydantic's ``ValidationError`` when the inputs cannot make a record the contract
    accepts — a ``turn_id`` that is not a UUID, a principal or version outside the contract's
    character set, more than ``MAX_TOOL_CALLS`` tool calls. There is no partial record.
    """
    flagged = answer.flagged_blocks
    distinct_flags: list[VerificationFlag] = sorted(
        {flag for block in flagged for flag in block.flags}
    )
    moment = at if at is not None else datetime.now(tz=UTC)
    return TurnAuditRecord(
        service_version=service_version,
        at=moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        principal=principal,
        turn_id=turn_id,
        tools=tools,
        spans_verified=len(answer.verified_blocks),
        spans_flagged=len(flagged),
        sections_dropped=sections_dropped,
        flags=tuple(distinct_flags),
        duration_ms=duration_ms,
    )


def emit(record: TurnAuditRecord, stream: TextIO | None = None) -> None:
    """Write the record as one line of JSON, or refuse to write it at all."""
    payload = record.model_dump(by_alias=True, mode="json")
    offenders = sorted(set(_forbidden_keys_in(payload)))
    if offenders:
        raise NarrativeLeakError(f"audit record carries forbidden key(s): {', '.join(offenders)}")
    destination = stream if stream is not None else sys.stdout
    destination.write(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")
    destination.flush()


def _forbidden_keys_in(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str) and key.lower() in FORBIDDEN_KEYS:
                yield key
            yield from _forbidden_keys_in(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _forbidden_keys_in(item)
