# The verifiable-answer agent

Roadmap item 1b. A small Agent Development Kit agent, its own deployable under ADR 0004 — own
`pyproject.toml`, own lock, own identity, own CI job — that shares nothing with the worker or
with Zone A except one published artefact: `contracts/generated/query-tools.schema.json`.

It does four things and refuses to do a fifth.

1. Calls the query service's four MCP tools over streamable HTTP, passing the **end user's**
   bearer token on every request, with no fallback credential.
2. Composes answers in a fixed shape: verbatim blocks from tool results, each with its
   citation, and the assistant's own words in a separate labelled part.
3. Runs the post-check — every verbatim block back through `verify_quote` — and flags any
   `no-match` on the block itself.
4. Emits one structured audit record per turn: tools called, spans verified and flagged,
   principal, durations. Never narrative, never arguments, never the token.

The design is `docs/design/verifiable-answers.md`. The promise it implements, stated narrowly
enough to be true: _every sentence an answer presents as label content is verbatim from the
store, carries a hash a reader can recompute, and has been mechanically re-checked against the
store after the assistant wrote it._ This package is layer 2 of that; the query service is
layer 1 and the system instruction is layer 3.

## What is proven, and where

| Claim                                                                        | Where                                           |
| ---------------------------------------------------------------------------- | ----------------------------------------------- |
| The real ADK toolset exposes exactly the contract's four tools               | `tests/test_toolset_wiring.py`                  |
| The end user's token reaches the service on every request                    | `tests/test_toolset_wiring.py`                  |
| A missing or blank token fails the call closed, attempting nothing           | `tests/test_toolset_wiring.py`                  |
| A tool result that fails the contract's schema is unavailable, never content | `tests/test_contract.py`, `tests/test_turn.py`  |
| Composition is a deterministic function of the tool results                  | `tests/test_compose.py`                         |
| An altered quotation comes back `no-match` and is flagged on the card        | `tests/test_postcheck.py`, `tests/test_turn.py` |
| A block that was never checked is unverified, not assumed good               | `tests/test_postcheck.py`                       |
| No answer can be rendered without passing through the post-check             | `tests/test_postcheck.py`                       |
| The A2UI card carries quote, citation and status per block                   | `tests/test_render.py`                          |
| The audit record carries no narrative and no arguments at any depth          | `tests/test_audit.py`                           |
| Nothing under `src/` prints or logs, and nothing here quotes a fixture       | `tests/test_no_narrative_leak.py`               |

No test calls a language model. `tests/test_agent.py` constructs the `LlmAgent` — construction
is pure pydantic validation and resolves no model — and never runs it.

## The invariant, in the types

The thing worth getting right is that a quotation cannot reach a reader without having been
checked. It is not a comment; it is the shape of the code.

- `answer.QuotedBlock` is a candidate. Nothing renders one.
- `postcheck.CheckedAnswer` is the only type `render`, `render_a2ui` and `render_text` accept,
  and its `__init__` takes a module-private witness object. Constructing another
  `_PostCheckWitness` yields a different instance and raises `TypeError`, so the only way to
  obtain a `CheckedAnswer` is `post_check()` — which takes the `verify_quote` results as an
  argument and therefore cannot have skipped them.
- A block with no verification is **unverified**, not verified-by-default. So are
  `no-match`, a match in a different section from the one cited, and an answer about a
  different document version.

`tests/test_postcheck.py` asserts all three: that a `CheckedAnswer` cannot be constructed, that
every renderer's first parameter is annotated `CheckedAnswer`, and that `post_check` and
`run_post_check` are the only public functions in the module that return one.

`post_check` is pure — `(draft, verifications) → CheckedAnswer`. `run_post_check` is the driver
that makes the calls. Splitting them is what lets the decision procedure be tested exhaustively
without a server and the wiring be tested without a model.

## Why 3.14

`requires-python = ">=3.14,<3.15"` — the highest version `google-adk` and Vertex AI Agent
Engine both support, which happens to be the same as Zone A's, though for a different reason
(Zone A pins the Unicode Character Database; nothing here hashes anything).

- `google-adk` 2.9.2 declares `requires_python >=3.10` and classifies 3.10 through 3.14
  (PyPI, 2026-09-20).
- Agent Engine's `AgentEngineConfig.python_version` enumerates `"3.10"`, `"3.11"`, `"3.12"`,
  `"3.13"`, `"3.14"` and **defaults to `3.10`**
  ([Agent Platform types](https://pkg.go.dev/cloud.google.com/go/agentplatform/types),
  [reasoningEngines REST reference](https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/rest/v1/projects.locations.reasoningEngines)).
  Because the default is four minors behind this lock, `deploy/deploy_agent_engine.py` sets
  `python_version` explicitly rather than inheriting it.

One caveat, stated rather than hidden: the version list above comes from the generated API
reference rather than from the prose "set up" page, because `docs.cloud.google.com` renders its
body client-side and could not be read as text. Both sources are proto-derived. Older
`v1beta1` `PackageSpec` references still list 3.8–3.11 and are stale. Confirm in the console
before the first real deploy.

The whole stack installs and runs on 3.14 here: 75 packages resolved, the ADK toolset
negotiating MCP protocol version 2025-11-25 against a real server.

## Set-up

`uv` lives outside the environment it manages, for the reason Zone A's README records: `uv sync
--frozen` prunes anything not in `uv.lock`, and `uv` is a tool rather than a dependency, so
installing it into `.venv` destroys it on the first sync.

```bash
cd agent
python3.14 -m venv .uv-bootstrap
.uv-bootstrap/bin/pip install "uv==0.12.17"
.uv-bootstrap/bin/uv sync --frozen
```

On macOS 26 with Homebrew's `python@3.14`, prefix the two `venv`/`pip` lines with
`DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib` (after `brew install expat`) — the same
`ensurepip`/`pyexpat` defect Zone A's README describes. Nothing after the bootstrap needs it.

Then the five commands CI runs:

```bash
.uv-bootstrap/bin/uv run --frozen ruff check .
.uv-bootstrap/bin/uv run --frozen ruff format --check .
.uv-bootstrap/bin/uv run --frozen mypy --strict
.uv-bootstrap/bin/uv run --frozen python scripts/sync_contract.py --check
.uv-bootstrap/bin/uv run --frozen pytest
```

Every line above was run as written.

## The contract is vendored, and gate-checked

The agent must be self-contained on Agent Engine — the repository root is not there — but a
second hand-maintained copy of the contract would be a second source of truth. So
`src/verifiable_answer_agent/contracts/query-tools.schema.json` is a byte copy written by
`scripts/sync_contract.py`, committed, and checked in CI, exactly as Zone A treats its
generated pydantic models. `tests/test_contract.py` asserts the copy equals the published file
and that each tool's output type is the one the contract's own `tools.<name>.output` names.

Every tool result is validated against it before anything reads it. A result that does not
validate is **unavailable**: dropped from composition, counted in the audit record, never
rendered, never quoted. The `ValidationError` is discarded rather than logged, because for
`get_section` the offending instance _is_ clinical narrative.

## Tests are hermetic

`tests/fake_query_service.py` stands up the four tools on `mcp`'s `FastMCP` over streamable
HTTP, on an ephemeral loopback port, and the real ADK `McpToolset` connects to it. What is
faked is the store; the protocol is real.

The canned outputs are built from `test/fixtures/contracts/canonical-submission.json` — the
repository's own synthetic submission, exported and gate-checked on the Node side — walking its
`Composition` for the 32 sections and its `provenance.sections` for the per-section hashes.
Each one is validated against the contract before the server will serve it, so a fixture that
drifted out of contract would fail here rather than let the agent be tested against a shape the
real service cannot produce.

Two faults can be injected: `corrupt_section` makes one section stop matching anything, which
is what "the store moved on since composition" looks like; `break_schema_for` returns a section
whose hash is not a SHA-256, which is what a malformed result looks like.

`tests/fake_query_service.py` contains a 15-line XHTML-to-text step, and it is worth being
explicit that this is **not** a third implementation of `docs/fidelity-normalization.md`. The
fixture's narrative is one `<p>` of ASCII per section, so stripping tags and collapsing spaces
reproduces the normalised text exactly; the real normalisation stays in Zone A and Zone B where
it belongs.

## Where the documentation and the library disagreed

Four places, recorded because each cost time and a second implementer would hit them.

1. **`MCPToolset` is deprecated; `McpToolset` is current.** Both are exported from
   `google.adk.tools.mcp_tool`, and `MCPToolset` is a subclass that warns. More surprising:
   ADK 2.9.2 _itself_ instantiates the deprecated `MCPTool` internally, so a clean run of this
   suite emits ADK's own deprecation warning 16 times. Nothing to fix at this end.
2. **`header_provider` takes a `ReadonlyContext`, not a `CallbackContext`.** The community
   code in [adk-python discussion #2482](https://github.com/google/adk-python/discussions/2482)
   uses `CallbackContext` / `callback_context.session.state`, which does not match the current
   source. The signature is
   `Callable[[ReadonlyContext], dict[str, str] | Awaitable[dict[str, str]]]`, on `McpToolset`
   and on `McpTool`, not on the connection params. It is called for **every** HTTP request in
   the session — initialise, list-tools and each call — which the header-capture assertion in
   `tests/test_toolset_wiring.py` observes directly.
3. **`McpTool.run_async` does not return the tool's output.** It returns the serialised
   `CallToolResult` — `{"content": [...], "structuredContent": {...}, "isError": false}`. The
   contract describes the `structuredContent`, so `tools.read_tool_result` unwraps before it
   validates. A tool result with `isError` is unavailable, not content.
4. **A2UI's basic catalog declares a different id from the one the specification's example
   uses.** The catalog document served from the v0.9.1 path declares
   `catalogId: https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json` (v0_9), while
   the v0.9.1 specification's `createSurface` example writes `v0_9_1`. `render.A2UI_CATALOG_ID`
   uses the catalog document's own value, because that is what a renderer matches against.
   A2UI v1.0 is a candidate and renames `theme` to `surfaceProperties`; re-check on 1.0.

## Where the contract left something open

**`VerifyQuoteInput.quote` is `maxLength: 2000`, and the two sides count differently.** JSON
Schema defines `maxLength` over Unicode code points; the service enforcing it is TypeScript,
where a string length is UTF-16 code units. For anything outside the Basic Multilingual Plane
the two disagree, and the contract does not say which governs. `split_for_verification`
measures UTF-16 units — the smaller and therefore safe reading — and never splits a surrogate
pair. Worth one clause in the contract, the way `docs/fidelity-normalization.md` closed the
same class of question for offsets.

The splitter matters because a section can be a hundred times longer than one `verify_quote`
argument. It cuts on a U+0020 inside the window, dropping the separator rather than carrying a
leading or trailing space into either chunk — normalisation would remove one anyway — and at
the hard limit when a single token is longer than the window. Every chunk is a contiguous
substring of the block, which is what makes each one findable in the stored section, and a
block is verified only if **every** chunk came back `match`.

## Audit

`TurnAuditRecord` mirrors `QueryAuditRecord` in spirit rather than in fields — the query
service audits one tool call, this audits one turn — and what is identical is what is absent.
No narrative. **No argument digest either**, unlike `QueryAuditRecord`: a `verify_quote`
argument _is_ narrative, and a digest of a quote is a way of asking whether a document contains
a sentence. No token in any form.

`audit.emit` is the only writer in the package and refuses to write a record carrying a
forbidden key at any depth, raising `NarrativeLeakError` instead. `tests/test_audit.py` proves
the refusal fires and that no key in a real record is one. `tests/test_no_narrative_leak.py`
AST-scans every module under `src/` and `tests/` for `print`, `breakpoint`, `sys.stdout`,
`sys.stderr` and `logging`, allowing exactly one occurrence in the whole package: `audit.py`'s
default destination.

## What is not built

- **No delegated trust.** The agent never asserts a user under its own identity. The design
  note settles this: the end user's token arrives natively, and the fallback is not needed.
- **No write tool, and no tool outside the four.** `tool_filter` closes the surface at this end
  as well as at the service's.
- **No custom frontend.** The surfaces are Gemini Enterprise and Google Chat, both Google's.
- **No deploy from CI.** `deploy/` is documented and scripted, not executed; see
  `deploy/README.md`.
- **No agent-side normalisation, hashing, or fidelity checking.** Those live where the
  specification lives.

## The residual risk this does not close

The post-check catches **alteration**, not **absence**. An assistant that quotes three sections
correctly and silently omits the fourth produces an answer in which every block verifies and
which is still misleading. That is why "the answer is the tool result" matters more than "the
assistant is careful", and why the intended-use statement in the design note calls this an
information-retrieval aid for trained staff rather than a substitute for reading the label.
Prompt injection through document content is bounded by `ContentNotice` and by the model; it is
not eliminated.
