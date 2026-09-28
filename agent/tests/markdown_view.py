"""What a reader sees of an answer once a CommonMark renderer has drawn it.

Gemini Enterprise renders the agent's text as Markdown, so the tests read an answer the same way:
through markdown-it-py (the Python port of markdown-it, CommonMark with raw HTML allowed and the
GitHub strikethrough and table extensions on), never by searching the source text. A block's
quotation is what a reader sees only if it comes back out of its fence exactly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, final

from markdown_it import MarkdownIt

_MARKDOWN: Final = MarkdownIt("commonmark", {"html": True}).enable(["strikethrough", "table"])


@final
@dataclass(frozen=True, slots=True)
class ShownBlock:
    """One checked block as drawn: its status line, its quotation, its reading lines."""

    status: str
    text: str
    reading: tuple[str, ...]


def code_blocks(answer: str) -> list[str]:
    """The literal content of every code block the renderer draws, in order."""
    return [
        token.content for token in _MARKDOWN.parse(answer) if token.type in {"fence", "code_block"}
    ]


def outside_code(answer: str) -> str:
    """The HTML the renderer draws outside every code block: what Markdown did act on."""
    return re.sub(r"<pre>.*?</pre>", "", _MARKDOWN.render(answer), flags=re.DOTALL)


def shown_blocks(answer: str) -> list[ShownBlock]:
    """Every checked block as drawn: each code block but the last, which is the assistant's."""
    blocks: list[ShownBlock] = []
    for content in code_blocks(answer)[:-1]:
        lines = content.removesuffix("\n").split("\n")
        status, blank = lines[0], lines[1]
        assert blank == "", "a block's status is followed by a blank line"
        end = lines.index("", 2)
        blocks.append(
            ShownBlock(status=status, text=" ".join(lines[2:end]), reading=tuple(lines[end + 1 :]))
        )
    return blocks


def shown_assistant(answer: str) -> str:
    """The assistant's words as drawn: the last code block."""
    return code_blocks(answer)[-1].removesuffix("\n")
