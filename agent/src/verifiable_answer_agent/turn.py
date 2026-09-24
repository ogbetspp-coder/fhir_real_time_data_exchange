"""One turn, end to end: compose, post-check, render, audit.

This is the whole of the agent's own logic and it is deliberately one short function. Given the
``get_section`` results the model asked for, the model's free text, and a way to call
``verify_quote``, it produces the rendered answer and the record of what happened. It contains
no model call, so it is fully testable without one.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, final

from .audit import ToolCallRecord, TurnAuditRecord, turn_record
from .compose import compose
from .contract import ToolResult
from .postcheck import CheckedAnswer, VerifyQuote, run_post_check
from .render import Surface, render

__all__ = ["TurnResult", "answer_turn"]


@final
@dataclass(frozen=True, slots=True)
class TurnResult:
    """What one turn produced: the checked answer, its rendering and the turn's audit record."""

    answer: CheckedAnswer
    rendered: list[dict[str, Any]] | str
    audit: TurnAuditRecord


async def answer_turn(
    *,
    section_results: Sequence[ToolResult],
    assistant_text: str,
    verify_quote: VerifyQuote,
    surface: Surface,
    principal: str,
    service_version: str,
    turn_id: str,
    tool_calls: Sequence[ToolCallRecord] = (),
) -> TurnResult:
    """Compose deterministically, check mechanically, render, and record.

    ``turn_id`` is the UUID ``tools.begin_turn`` put in session state when the turn started —
    the one every tool call of the turn sent as ``X-Query-Turn-Id`` — read back with
    ``tools.current_turn_id``. It is not generated here: a record whose id the tool calls never
    carried could not be joined to the service's audit lines.

    ``tool_calls`` is read when the turn ends, not when it starts, so a live list that the
    caller's ``verify_quote`` appends to during the post-check is recorded in full.
    """
    started = time.monotonic()
    composition = compose(section_results, assistant_text)
    checked = await run_post_check(composition.draft, verify_quote)
    rendered = render(checked, surface)
    record = turn_record(
        service_version=service_version,
        principal=principal,
        turn_id=turn_id,
        answer=checked,
        tools=tuple(tool_calls),
        sections_dropped=composition.sections_dropped,
        duration_ms=max(0, round((time.monotonic() - started) * 1000)),
    )
    return TurnResult(answer=checked, rendered=rendered, audit=record)
