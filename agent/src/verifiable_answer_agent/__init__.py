"""The verifiable-answer agent (roadmap item 1b).

Four things, and nothing else:

1. ``tools`` — the query service's four MCP tools over streamable HTTP, with the end user's
   bearer token supplied per request and no fallback credential.
2. ``compose`` — a pure function from validated tool results to an answer structure.
3. ``postcheck`` — every verbatim block back through ``verify_quote`` before anything can be
   rendered as label content.
4. ``audit`` — one structured, narrative-free record per turn.

Nothing here generates, summarises, or infers regulated narrative. The composed answer's label
content is verbatim from a tool result or it does not exist.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
