"""One structured record per turn, and the discipline that keeps narrative out of it.

``TurnAuditRecord`` is the ``AgentTurnRecord`` of the published ``agent-turn`` contract
(``contracts/generated/agent-turn.schema.json``, vendored next to the query-tools schema): the
same field names, the same closed enumerations for tool names, outcomes and flags, and the same
patterns for ``serviceVersion``, ``principal``, ``principalDigest``, ``turnId`` and
``errorClass``, so that a record this module builds serialises to an instance the contract
accepts. ``tests/test_audit.py`` validates an emitted record against the vendored schema.

What is absent is the point. No narrative. No argument values — a ``verify_quote`` argument
*is* narrative, so not even a digest of one is carried, because a digest of a quote is a way of
asking whether a document contains a sentence. No token, ever, in any form. No e-mail address:
the session's user id is carried only when it is an opaque identifier (``principal_fields``).

``emit`` is the only writer in the package, and it refuses to write a record containing a
forbidden key at any depth. That check is not decoration: it is what makes "never log clinical
text" a property of the code rather than a rule someone remembers.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sys
import unicodedata
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, Final, Literal, TextIO

from pydantic import BaseModel, ConfigDict, Field

from .contract import QueryToolName
from .postcheck import CheckedAnswer, VerificationFlag
from .render import AssistantFlag

__all__ = [
    "FORBIDDEN_KEYS",
    "NIL_TURN_ID",
    "WITHHELD_PRINCIPAL",
    "NarrativeLeakError",
    "ToolCallRecord",
    "TurnAuditRecord",
    "TurnOutcome",
    "emit",
    "minimal_record",
    "principal_fields",
    "turn_record",
]

AGENT_SERVICE: Final = "ema-flow-agent"

ToolOutcome = Literal["ok", "schema-invalid", "tool-error", "transport-error", "not-an-object"]
"""``AgentToolOutcome`` in the agent-turn contract."""

TurnOutcome = Literal[
    "answered", "tools-unavailable", "model-failed", "turn-id-missing", "internal-error"
]
"""``AgentTurnOutcome`` in the agent-turn contract."""

# The contract's patterns, repeated here so that a record the contract would refuse cannot be
# built in the first place. ``tests/test_audit.py`` checks each against the vendored schema.
TOKEN_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$"
PRINCIPAL_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"
SHA256_PATTERN: Final = r"^[0-9a-f]{64}$"
ERROR_CLASS_PATTERN: Final = r"^[A-Za-z_][A-Za-z0-9_]{0,127}$"
UUID_PATTERN: Final = (
    r"^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
    r"|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
)
MAX_TOOL_CALLS: Final = 200
"""``tools.maxItems`` in the agent-turn contract. A turn that made more calls than this has no
full record the contract accepts, so ``turn_record`` refuses to build one rather than truncate,
and the turn's end writes ``minimal_record`` instead."""

WITHHELD_PRINCIPAL: Final = "session-user-withheld"
"""``principal`` when the session's user id is not an opaque identifier — an e-mail address,
which is what Gemini Enterprise supplies (audit AG-1). The contract refuses an e-mail there, and
until 2026-09-27 that refusal failed every live turn silently."""

OPAQUE_PRINCIPAL_FORMS: Final = (r"[0-9]{1,64}", r"urn:[a-z][a-z0-9-]{0,31}:[A-Za-z0-9._:-]{1,90}")
"""The session user ids carried as they are: a numeric subject (Google's ``sub``) or a URN.
Anything else fitting the contract's character set could still be a person's name."""

MIN_DIGEST_KEY_BYTES: Final = 32
"""The shortest key ``principalDigest`` is made under: 256 bits, the HMAC-SHA256 output size."""

NIL_TURN_ID: Final = "00000000-0000-0000-0000-000000000000"
"""``turnId`` for a turn that had none: no request of it carried one, so it joins nothing."""

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
    """``AgentTurnRecord``: how the turn ended, the spans verified and flagged, the durations.

    An optional field that is ``None`` is left out of the emitted line: the contract has no
    null for any of them.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    service: Literal["ema-flow-agent"] = AGENT_SERVICE
    service_version: str = Field(pattern=TOKEN_PATTERN, serialization_alias="serviceVersion")
    at: str
    principal: str = Field(pattern=PRINCIPAL_PATTERN)
    principal_digest: str | None = Field(
        default=None, pattern=SHA256_PATTERN, serialization_alias="principalDigest"
    )
    turn_id: str = Field(pattern=UUID_PATTERN, serialization_alias="turnId")
    outcome: TurnOutcome | None = None
    error_class: str | None = Field(
        default=None, pattern=ERROR_CLASS_PATTERN, serialization_alias="errorClass"
    )
    tools: tuple[ToolCallRecord, ...] = Field(max_length=MAX_TOOL_CALLS)
    spans_verified: int = Field(ge=0, serialization_alias="spansVerified")
    spans_flagged: int = Field(ge=0, serialization_alias="spansFlagged")
    sections_dropped: int = Field(ge=0, serialization_alias="sectionsDropped")
    flags: tuple[VerificationFlag, ...]
    assistant_flags: tuple[AssistantFlag, ...] | None = Field(
        default=None, serialization_alias="assistantFlags"
    )
    duration_ms: int = Field(ge=0, serialization_alias="durationMs")


def principal_fields(user_id: str, key: bytes | None) -> tuple[str, str | None]:
    """``(principal, principalDigest)`` for a session user id. Never the address itself.

    Only a known opaque form is carried as it is: an identity provider's numeric subject, or a
    URN (``urn:reviewer:synthetic-01``). Anything else — an e-mail address, as Gemini Enterprise
    supplies, or a name that happens to fit the contract's character set — is withheld; with a
    deployment key of at least ``MIN_DIGEST_KEY_BYTES`` it is carried as an HMAC-SHA256 under that
    key of the casefolded id, so one user gives one digest however the address is capitalised,
    and nobody without the key can test an address against it. A shorter key is not used.
    Without a key nothing identifies the session user in this record; the query service's records
    of the same ``turnId`` carry the principal it verified.
    """
    if re.fullmatch(PRINCIPAL_PATTERN, user_id) and any(
        re.fullmatch(form, user_id) for form in OPAQUE_PRINCIPAL_FORMS
    ):
        return user_id, None
    if key is None or len(key) < MIN_DIGEST_KEY_BYTES:
        return WITHHELD_PRINCIPAL, None
    canonical = unicodedata.normalize("NFKC", user_id).casefold()
    digest = hmac.new(key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    return WITHHELD_PRINCIPAL, digest


def turn_record(
    *,
    service_version: str,
    principal: str,
    turn_id: str,
    answer: CheckedAnswer,
    tools: tuple[ToolCallRecord, ...],
    sections_dropped: int,
    duration_ms: int,
    principal_digest: str | None = None,
    outcome: TurnOutcome | None = "answered",
    assistant_flags: tuple[AssistantFlag, ...] | None = None,
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
    return TurnAuditRecord(
        service_version=service_version,
        at=_timestamp(at),
        principal=principal,
        principal_digest=principal_digest,
        turn_id=turn_id,
        outcome=outcome,
        tools=tools,
        spans_verified=len(answer.verified_blocks),
        spans_flagged=len(flagged),
        sections_dropped=sections_dropped,
        flags=tuple(distinct_flags),
        assistant_flags=assistant_flags,
        duration_ms=duration_ms,
    )


def minimal_record(
    *,
    service_version: str,
    principal: str,
    turn_id: str,
    outcome: TurnOutcome,
    tools: tuple[ToolCallRecord, ...] = (),
    principal_digest: str | None = None,
    error_class: str | None = None,
    duration_ms: int = 0,
    answer: CheckedAnswer | None = None,
    sections_dropped: int = 0,
    at: datetime | None = None,
) -> TurnAuditRecord:
    """A record for a turn that showed no checked answer, or whose full record failed.

    With no ``answer`` every count is zero: nothing was verified. With one — the full record of an
    answered turn could not be built or written — its counts and flags are carried, so the record
    of last resort still says what the reader was shown as verified and as flagged. The tool calls
    are kept when there are no more than the contract allows; the error class is kept when it is a
    plain class name. Whatever cannot be carried is dropped rather than refused.
    """
    flagged = answer.flagged_blocks if answer is not None else ()
    return TurnAuditRecord(
        service_version=service_version,
        at=_timestamp(at),
        principal=principal,
        principal_digest=principal_digest,
        turn_id=turn_id,
        outcome=outcome,
        error_class=(
            error_class
            if error_class is not None and re.fullmatch(ERROR_CLASS_PATTERN, error_class)
            else None
        ),
        tools=tools if len(tools) <= MAX_TOOL_CALLS else (),
        spans_verified=len(answer.verified_blocks) if answer is not None else 0,
        spans_flagged=len(flagged),
        sections_dropped=max(0, sections_dropped),
        flags=tuple(sorted({flag for block in flagged for flag in block.flags})),
        duration_ms=duration_ms,
    )


def _timestamp(at: datetime | None) -> str:
    moment = at if at is not None else datetime.now(tz=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def emit(record: TurnAuditRecord, stream: TextIO | None = None) -> None:
    """Write the record as one line of JSON, or refuse to write it at all."""
    payload = record.model_dump(by_alias=True, mode="json", exclude_none=True)
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
