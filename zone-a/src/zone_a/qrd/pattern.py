"""The QRD bracketing convention as a small grammar.

Every QRD template states it the same way (QRD 10.4 annotated template, Annex I preamble):

    {text}: Information to be filled in
    <text>: Text to be selected or deleted as appropriate.

and uses ``[text]`` for guidance that is not part of the product information. ``parse`` turns a
template string into tokens and ``render`` turns them back; ``render(parse(s)) == s`` for every
string the pinned templates contain, which is what makes the parse checkable.

Two rules are not written down by the EMA and are read from its own usage:

- A ``<`` followed by whitespace (space, tab or no-break space) is a less-than sign, not an
  opening bracket. Appendix II writes "<Common (\u2265 1/100 to < 1/10)>": the outer pair is the
  convention, the inner "< 1/10" is arithmetic.
- A ``>`` with no open ``<`` is a greater-than sign.

A string that leaves a ``<``, ``{`` or ``[`` open is not parsed: ``parse`` raises
``UnbalancedTemplateError``, and the caller decides whether the next paragraph continues it.
"""

from __future__ import annotations

from typing import Literal, TypedDict

WHITESPACE = {" ", "\t", "\u00a0"}


class Token(TypedDict):
    kind: Literal["text", "optional", "fill", "guidance"]
    # "text", "fill" and "guidance" carry a string; "optional" carries tokens.
    value: str | list[Token]


class UnbalancedTemplateError(ValueError):
    """The string opens a bracket it does not close (or closes one it did not open)."""

    def __init__(self, depth: int) -> None:
        super().__init__(f"unbalanced template: {depth} bracket(s) left open")
        self.depth = depth


_CLOSE = {"{": "}", "[": "]"}


def _scan_group(text: str, start: int, opener: str) -> int:
    """Index just past the closer matching ``text[start]``, nesting on the same opener."""
    closer = _CLOSE[opener]
    depth = 0
    for index in range(start, len(text)):
        if text[index] == opener:
            depth += 1
        elif text[index] == closer:
            depth -= 1
            if depth == 0:
                return index + 1
    raise UnbalancedTemplateError(depth)


def _opens_optional(text: str, index: int) -> bool:
    return text[index] == "<" and index + 1 < len(text) and text[index + 1] not in WHITESPACE


def _tokens(text: str, index: int, nested: bool) -> tuple[list[Token], int]:
    tokens: list[Token] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            tokens.append({"kind": "text", "value": "".join(buffer)})
            buffer.clear()

    while index < len(text):
        character = text[index]
        if _opens_optional(text, index):
            flush()
            inner, index = _tokens(text, index + 1, nested=True)
            tokens.append({"kind": "optional", "value": inner})
            continue
        if character == ">" and nested:
            flush()
            return tokens, index + 1
        if character in _CLOSE:
            flush()
            end = _scan_group(text, index, character)
            kind: Literal["fill", "guidance"] = "fill" if character == "{" else "guidance"
            tokens.append({"kind": kind, "value": text[index + 1 : end - 1]})
            index = end
            continue
        if character in ("}", "]"):
            raise UnbalancedTemplateError(-1)
        buffer.append(character)
        index += 1
    if nested:
        raise UnbalancedTemplateError(1)
    flush()
    return tokens, index


def parse(text: str) -> list[Token]:
    """Tokens of a template string, or ``UnbalancedTemplateError``."""
    tokens, _ = _tokens(text, 0, nested=False)
    return tokens


def render(tokens: list[Token]) -> str:
    """The template string the tokens were parsed from."""
    out: list[str] = []
    for token in tokens:
        value = token["value"]
        if token["kind"] == "optional":
            assert isinstance(value, list)
            out.append("<" + render(value) + ">")
        elif token["kind"] == "fill":
            out.append("{" + str(value) + "}")
        elif token["kind"] == "guidance":
            out.append("[" + str(value) + "]")
        else:
            out.append(str(value))
    return "".join(out)


def is_balanced(text: str) -> bool:
    try:
        parse(text)
    except UnbalancedTemplateError:
        return False
    return True
