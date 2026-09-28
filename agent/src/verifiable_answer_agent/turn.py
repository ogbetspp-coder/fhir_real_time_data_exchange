"""One turn, end to end: compose, post-check, render, audit.

This is the whole of the agent's own logic and it is deliberately short. Given the
``get_section`` results the model asked for, the model's free text, and a way to call
``verify_quote``, ``check_turn`` produces the rendered answer; ``answer_turn`` adds the record of
what happened. It contains no model call, so it is fully testable without one.

The two are separate because the turn's end (``finish``) must never let a failure to build the
record hide an answer that was checked (audit AG-1): it renders with ``check_turn`` and builds
the record on its own, falling back to a minimal record when the full one cannot be built.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, final

from .audit import ToolCallRecord, TurnAuditRecord, turn_record
from .compose import ProductFacts, compose
from .contract import ToolResult
from .postcheck import CheckedAnswer, VerifyQuote, run_post_check
from .render import AssistantFlag, Surface, render, sanitise_assistant

__all__ = ["CheckedTurn", "TurnResult", "answer_turn", "check_turn"]


@final
@dataclass(frozen=True, slots=True)
class CheckedTurn:
    """The checked answer, its rendering, and the counts its audit record needs."""

    answer: CheckedAnswer
    rendered: list[dict[str, Any]] | str
    sections_dropped: int
    assistant_flags: tuple[AssistantFlag, ...]
    duration_ms: int


@final
@dataclass(frozen=True, slots=True)
class TurnResult:
    """What one turn produced: the checked answer, its rendering and the turn's audit record."""

    answer: CheckedAnswer
    rendered: list[dict[str, Any]] | str
    audit: TurnAuditRecord


async def check_turn(
    *,
    section_results: Sequence[ToolResult],
    assistant_text: str,
    verify_quote: VerifyQuote,
    surface: Surface,
    products: ProductFacts | None = None,
) -> CheckedTurn:
    """Compose deterministically, check mechanically, render. No record."""
    started = time.monotonic()
    composition = compose(section_results, assistant_text, products)
    checked = await run_post_check(composition.draft, verify_quote)
    rendered = render(checked, surface)
    return CheckedTurn(
        answer=checked,
        rendered=rendered,
        sections_dropped=composition.sections_dropped,
        assistant_flags=sanitise_assistant(checked).flags,
        duration_ms=max(0, round((time.monotonic() - started) * 1000)),
    )


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
    products: ProductFacts | None = None,
) -> TurnResult:
    """Compose deterministically, check mechanically, render, and record.

    ``turn_id`` is the UUID ``tools.begin_turn`` put in session state when the turn started —
    the one every tool call of the turn sent as ``X-Query-Turn-Id`` — read back with
    ``tools.current_turn_id``. It is not generated here: a record whose id the tool calls never
    carried could not be joined to the service's audit lines.

    ``tool_calls`` is read when the turn ends, not when it starts, so a live list that the
    caller's ``verify_quote`` appends to during the post-check is recorded in full.
    """
    checked = await check_turn(
        section_results=section_results,
        assistant_text=assistant_text,
        verify_quote=verify_quote,
        surface=surface,
        products=products,
    )
    record = turn_record(
        service_version=service_version,
        principal=principal,
        turn_id=turn_id,
        answer=checked.answer,
        tools=tuple(tool_calls),
        sections_dropped=checked.sections_dropped,
        assistant_flags=checked.assistant_flags,
        duration_ms=checked.duration_ms,
    )
    return TurnResult(answer=checked.answer, rendered=checked.rendered, audit=record)
