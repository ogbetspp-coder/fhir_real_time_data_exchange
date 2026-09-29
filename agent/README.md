# The verifiable-answer agent

Delivered (was roadmap item 1b); its post-check seen in a live turn is roadmap item 1. A small Agent
Development Kit agent, its own deployable under ADR 0004 — own `pyproject.toml`, own lock, own
identity, own CI job — that shares nothing with the worker or with Zone A except two published
artefacts: `contracts/generated/query-tools.schema.json` (the query service's surface) and
`contracts/generated/agent-turn.schema.json` (the shape of this agent's own audit record), at the
versions `src/contracts/` names.

It does four things and refuses to do a fifth.

1. Calls the query service's four MCP tools over streamable HTTP, passing the **end user's**
   bearer token on every request, with no fallback credential, and one `X-Query-Turn-Id`
   header per turn so the service's audit lines can be joined to this agent's.
2. Composes answers in a fixed shape: verbatim blocks from tool results, each with its
   citation and the product and language named for its document version, and the
   assistant's own words in a separate labelled part.
3. Runs the post-check — every verbatim block back through `verify_quote`, held to exact
   coverage and matching hashes — and flags anything short of that on the block itself.
4. Emits one structured audit record per turn, however the turn ends, in the shape the
   `agent-turn` contract publishes: how it ended, tools called, spans verified and flagged,
   principal (never an e-mail address), durations. Never narrative, never arguments, never the
   token.

The design is `docs/design/verifiable-answers.md`. The promise it implements, stated narrowly
enough to be true: _every sentence an answer presents as label content is verbatim from the
store, carries a hash a reader can recompute, and has been mechanically re-checked against the
store after the assistant wrote it._ This package is layer 2 of that; the query service is
layer 1 and the system instruction is layer 3.

## What is proven, and where

| Claim                                                                           | Where                                                    |
| ------------------------------------------------------------------------------- | -------------------------------------------------------- |
| The real ADK toolset exposes exactly the contract's four tools                  | `tests/test_toolset_wiring.py`                           |
| The end user's token reaches the service on every request                       | `tests/test_toolset_wiring.py`                           |
| The turn id is made before any tool call and reaches the service on each one    | `tests/test_toolset_wiring.py`, `tests/test_turn.py`     |
| Without a turn id in state, only `Authorization` is sent                        | `tests/test_toolset_wiring.py`                           |
| A `find_product` result without `truncated` fails the contract                  | `tests/test_contract.py`                                 |
| A missing or blank token fails the call closed, attempting nothing              | `tests/test_toolset_wiring.py`                           |
| A tool result that fails the contract's schema is unavailable, never content    | `tests/test_contract.py`, `tests/test_turn.py`           |
| Composition is a deterministic function of the tool results                     | `tests/test_compose.py`                                  |
| An altered quotation comes back `no-match` and is flagged on the card           | `tests/test_postcheck.py`, `tests/test_turn.py`          |
| A block that was never checked is unverified, not assumed good                  | `tests/test_postcheck.py`                                |
| No answer can be rendered without passing through the post-check                | `tests/test_postcheck.py`                                |
| Both surfaces carry quote, status, product, citation per block                  | `tests/test_render.py`                                   |
| The audit record carries no narrative and no arguments at any depth             | `tests/test_audit.py`                                    |
| An emitted record validates against the vendored `agent-turn` schema            | `tests/test_audit.py`, `tests/test_finish_turn.py`       |
| Nothing under `src/` prints or logs, and nothing here quotes a fixture          | `tests/test_no_narrative_leak.py`                        |
| The only text a turn emits is the checked answer, streamed or not               | `tests/test_turn_events.py`                              |
| The model's thoughts and asides never leave the agent                           | `tests/test_turn_events.py`                              |
| Without the four query tools the model is not called, and the person is told    | `tests/test_turn_events.py`                              |
| A failed model call ends the turn with a notice, not a platform error           | `tests/test_turn_events.py`                              |
| All of the above hold for a deep copy of the agent, which is what deploys       | `tests/test_turn_events.py`                              |
| The assistant cannot write the labels reserved for checked text, disguised      | `tests/test_render.py`, `tests/test_turn_events.py`      |
| A failure at the turn's end shows a notice, never the model's draft             | `tests/test_finish_turn.py`                              |
| An e-mail user id gets its checked answer and a record without the address      | `tests/test_finish_turn.py`, `tests/test_audit.py`       |
| A record that cannot be built never hides the checked answer                    | `tests/test_finish_turn.py`                              |
| Every way a turn ends writes one record saying which                            | `tests/test_finish_turn.py`                              |
| The record lists every call the turn made, the post-check's included, timed     | `tests/test_finish_turn.py`, `tests/test_turn_events.py` |
| A match not at the chunk's own offsets, or with a hash that disagrees, fails    | `tests/test_postcheck.py`                                |
| A table or picture is not sent and says it cannot be checked yet                | `tests/test_postcheck.py`                                |
| Checksums, identifiers and repeated label text in the model's words are handled | `tests/test_render.py`                                   |
| The instruction never asks the model to write label text, ids or hashes         | `tests/test_agent.py`                                    |

No test calls a language model. `tests/test_agent.py` constructs the `LlmAgent` and never runs
it. `tests/test_turn_events.py` runs it through a real ADK `Runner` against the fake query
service with a scripted `BaseLlm` in place of Gemini, and reads every event the turn emits:
that is the level at which the draft leak of 2026-09-22 was visible, and the callbacks tested
one at a time could not show it.

## Why the model's text is held, not replaced

ADK's `after_agent_callback` runs after the agent has already yielded every model event, and
what it returns is appended as one more event. Until 2026-09-22 the model's draft therefore
reached Gemini Enterprise as a final response before the checked answer. `hold.DraftHold` stops
it where it is produced: its `after_model_callback` removes every text part from every model
response, partial or complete, and keeps the text of the last complete one for the post-check;
its `before_model_callback` refuses to call the model when the query toolset failed to load,
because ADK otherwise runs the model with no tools and it answers from memory; its
`on_model_error_callback` turns a failed model call into a notice instead of a platform error.
Only function calls are forwarded from a model response — an allowlist, so a kind of part a
model adds later is held back by default.

Agent Engine deploys `AdkApp.clone()`, a deep copy of the agent. A deep copy copies the object
behind a bound method once, through its memo, but keeps a closure pointing at the original. The
callbacks are therefore bound methods of `DraftHold` and `finish.TurnFinisher`, so the deployed
copy's callbacks and turn's end share one hold and the model's own toolset. The first version of
this fix used a closure and would have split them; the deep-copy cases in
`tests/test_turn_events.py` fail if that comes back.

The assistant's own words are still shown, under their own label and before an end line, and
are not checked. **Structurally, they cannot render as a checked block:** on the text surface
they sit inside a fenced code block (`render._fenced`) one backtick longer than any run of
backticks in them, so no Markdown or HTML they contain is rendered and no line of them closes
the fence. A fence rather than escaping, because CommonMark parses nothing inside one, it
closes only on a line of as many backticks, and on a surface that renders no Markdown it still
shows two marker lines around the words; escaping would rely on every escape being honoured,
show backslashes where one is not, and draw no boundary.

**The same fence carries every checked block** (review of PR #129, M2). Gemini Enterprise
renders the answer as Markdown, and a label's own text is not Markdown: in
`ALT <ULN and bilirubin >1.5 x ULN` the renderer took `<ULN and bilirubin >` for an HTML tag and
dropped it, `_not_` became emphasis, `&micro;` an entity, a label opening `# ` or `1. ` a heading
or a list, and a `<!--` in a label swallowed the rest of the answer. Each block (its status line,
its quotation, its citation and checksums) is now one fenced code block, the quotation wrapped at
spaces to 80 characters (a normalised section is a single line, and a code block does not wrap),
so the lines joined by single spaces are the stored text exactly. Only this module's own fixed
sentences stand between the fences, each its own paragraph. The verbatim display of a quotation
therefore depends on the fence: `tests/test_render.py` renders adversarial label text and the
reviewer's escape attempts through a CommonMark parser (markdown-it-py, a test-only dependency)
and checks that each quotation comes back out of its fence exactly and nothing leaks outside one.

As defence in depth (`render.sanitise_assistant`), the words are cut at 20,000 characters (with a
marker saying so), split on every kind of line break (`str.splitlines`: carriage return,
U+0085, U+2028 as well as line feed), and **shown as written**, less only the code points that
draw nothing and can hide or reorder what is drawn (zero-width space, word joiner, bidirectional
controls, byte order mark, control characters); the joiners U+200C and U+200D are kept, since
Persian words and emoji are spelt with them. The patterns read a folded copy — compatibility-folded
per code point, format characters dropped — mapped back to the line as written, so a checksum or
identifier found there is cut from the original where it stands and a folded line is never
shown: round 1 of the review showed the folded line, which turned "10⁹/L" into "109/L" and
"m²" into "m2" (round 2, M1). The filters: a line that
opens with a label reserved for checked text, or has the citation line's whole shape ("From
section … of document version"), is removed — compared as letters and digits only, after HTML
entities are decoded, HTML comments and tags dropped and Cyrillic and Greek look-alikes folded,
so emphasis, an entity, a comment, a tag, a backslash, a zero-width space, an emoji, a list
number or a table bar does not hide one; the unverified status is reserved only in its own
capitals, so "Not verified by me" stays. Runs of 32 or more hexadecimal digits (however Markdown
is threaded through them) are removed, and so is an identifier named by its field when its
value looks like one (`versionId 7`, not "the version ID shown"). Eight or more words in a row
shared with a block are pointed out as label text that is not checked. Each is said in the
answer and recorded as an `assistantFlags` value. The instruction asks the model never to write
any of it (`instruction.py`). Checking a quotation in the assistant's words against the store is
not done.

## The invariant, in the types

The thing worth getting right is that a quotation cannot reach a reader without having been
checked. It is not a comment; it is the shape of the code.

- `answer.QuotedBlock` is a candidate. Nothing renders one.
- `postcheck.CheckedAnswer` is the only type `render.render_text` and
  `render.sanitise_assistant` accept, and its `__init__` takes a module-private witness object.
  Constructing another
  `_PostCheckWitness` yields a different instance and raises `TypeError`, so the only way to
  obtain a `CheckedAnswer` is `post_check()` — which takes the `verify_quote` results as an
  argument and therefore cannot have skipped them.
- A block with no verification is **unverified**, not verified-by-default. So are
  `no-match`, a match in a different section from the one cited, an answer about a
  different document version, and — since 2026-09-27 (audit AG-4) — a match that does not tile
  the block: each chunk's match must sit at exactly the code-point offsets the splitter cut that
  chunk from (`contract.chunk_spans`), so the matches run from the block's first code point to
  its last with nothing between them but the spaces the cuts dropped (`coverage-gap`); and any
  hash that disagrees — a match naming another text hash than the section's, an answer whose
  `quoteSha256` is not the chunk's, a block whose text or XHTML does not hash to what its
  citation shows, or an answer under another normalisation version than `fidelity-norm/3.1.0`
  (`checksum-mismatch`). A match that does not say where, or over no section, is refused by
  the contract since query-tools 4.0.0 and by the post-check too.

`tests/test_postcheck.py` asserts all three: that a `CheckedAnswer` cannot be constructed, that
every renderer's first parameter is annotated `CheckedAnswer`, and that `post_check` and
`run_post_check` are the only public functions in the module that return one.

`post_check` is pure — `(draft, verifications) → CheckedAnswer`. `run_post_check` is the driver
that makes the calls, four at a time, keeping each answer at its chunk's index. Splitting them
is what lets the decision procedure be tested exhaustively without a server and the wiring be
tested without a model. The only hashing here is SHA-256 over the UTF-8 of strings the service
returned, compared with the hashes it returned beside them; nothing is normalised.

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
`v1beta1` `PackageSpec` references still list 3.8–3.11 and are stale. Confirmed by use: the
agent has run on Agent Engine in `europe-west4` on Python 3.14 since its first deploy on
2026-09-22, and again after the redeploys of 2026-09-23.

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
.uv-bootstrap/bin/uv run --frozen pytest --cov
```

Every line above was run as written. `--cov` measures line coverage of the package and fails
below the floor in `pyproject.toml` (`[tool.coverage.report] fail_under`, 98 as of 2026-09-22);
it is a flag rather than an `addopts` entry so that running one test file does not fail on it.

## The contracts are vendored, and gate-checked

The agent must be self-contained on Agent Engine — the repository root is not there — but a
second hand-maintained copy of a contract would be a second source of truth. So
`src/verifiable_answer_agent/contracts/query-tools.schema.json` and
`src/verifiable_answer_agent/contracts/agent-turn.schema.json` are byte copies written by
`scripts/sync_contract.py`, committed, and checked in CI (`--check` fails on drift in either),
exactly as Zone A treats its generated pydantic models. `tests/test_contract.py` asserts each
copy equals the published file and that each tool's output type is the one the query-tools
contract's own `tools.<name>.output` names. The same script writes
`src/verifiable_answer_agent/contracts/code-points.json` from the fidelity vectors' `codePoints`
table (`test/fixtures/fidelity/vectors.json`): the gap, Default_Ignorable and word-character
classes `quote_edge` reads, so the agent holds no hand copy of those lists and `--check` fails
when they move. All three vendored files are listed in the repository's `.prettierignore` for
the same reason Zone A's generated models are: formatting them would make the `--check`
comparison fail.

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

The fake's `verify_quote` decides with the service's quote-edge rule, not a substring search
(until 2026-09-22 it matched any substring, so a chunk the real service refuses passed here).
The rule is ported in `src/verifiable_answer_agent/quote_edge.py` and held to the service's own
answers: `scripts/contracts/export-quote-edge-cases.ts` runs the service's `locateQuote` over the
design's worked examples (`test/query/quote-edge-cases.ts`, which the service's acceptance test
also drives) and writes `test/fixtures/contracts/quote-edge-cases.json`; `npm run
contracts:check` regenerates it and fails on drift; `tests/test_quote_edge.py` asserts the port
and the fake, over the wire, reproduce every answer and offset, and that the port's three
character sets are the ones in `src/query/quote-edge.ts`.

Like the service, the fake searches only the section a `verify_quote` call names, and reports
the section it searched; it also records the JSON-RPC method of each request, so a test can
count `tools/list` round trips.

Two tests are not hermetic: `tests/test_deploy_requirements.py` resolves the runtime's
requirements against the package index for Google's build platform, and **skips** when the
index cannot be reached. Until 2026-09-27 its conflict test passed offline because the resolver
failed for want of a network; it now asserts uv's own "No solution found".

No test mints Cloud Run's edge token: `tests/conftest.py` stubs `tools.edge_auth_token` for
every test, because on a machine with no runtime identity each attempt waited about 3.4 s on
the metadata server and the suite took three minutes. `tests/test_tools_edge_auth.py` tests the
minting, its fallback and its caches with Google's own calls patched.

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

## query-tools 2.0.0

Two changes reach this agent.

- **`find_product` output carries `truncated`, required.** True means the list is shorter than
  the caller's entitlement holds — documents left unsearched, or matches dropped by `limit` —
  so an empty `products` with `truncated` true is not "no such product", and a full one is not
  the whole list. The agent does not compose `find_product` results
  — only `get_section` results become blocks — so this lands in the system instruction: the
  model is told to say the search was cut short and to ask for a narrower product name, and
  never to say there is no such product when `truncated` is true. That is a layer-3 instruction
  and carries layer-3 weight; `tests/test_agent.py` checks only that the instruction addresses
  the field, and `tests/test_contract.py` that a result without it is refused. A result the
  service produced under 1.0.0 fails the 2.0.0 contract and is unavailable, which is the right
  reading of a service answering a contract this agent does not hold.
- **`not-entitled` is no longer an error code a caller can see.** Outside the caller's
  entitlement the service answers `document-not-found`. The agent never branched on the code —
  an `isError` result is unavailable whatever it says — so nothing changes at this end;
  `tests/test_contract.py` pins the enum so a return of the code would be noticed.

The `QueryAuditRecord` changes in 2.0.0 (`credentialType`, `imageDigest`, `versionId`,
`turnId`, `truncated`) describe the service's own record and do not concern this agent, except
that `turnId` is the value this agent sends — see the next section.

## query-tools 2.0.1

Descriptions only; no shape changed, so the agent's validation of tool results is unchanged.
Two meanings changed. `verify_quote`'s `match` now requires both edges of the quote to hold
under the service's quote-edge rule — not inside a word, not at punctuation joined to a number
or word — so a block the model quoted starting or ending mid-token is now `no-match`, and the
post-check marks it. The post-check splits a block longer than 2,000 units into chunks, and
until 2026-09-22 it cut at the last space of each window, so a boundary could fall between the
groups of a space-grouped number ("1 000" | "000 IU daily.") or just after a spaced comparator
("CrCl ≥" | "30 ml/min."): the service refused those chunks and the label's own text was
flagged `no-match`. The splitter now cuts only where the quote-edge rule holds on both sides
(next section but one). And an approval is given for a document's current version
only: `get_provenance` for an earlier version is `unavailable`, which this agent reads as it
reads any other `unavailable`.

## query-tools 3.0.0 and later

**Deploy order.** The agent must run a vendored `query-tools` 3.0.0 or later no later than the
service does: an earlier agent reads a 3.0.0 answer with a `/` in an identifier as unavailable.
Each change record (`docs/validation/README.md`, Change records) states its own order. 4.1.0 and
`agent-turn` 1.2.0 add an optional `contractVersion` to both audit records, and the published
`pattern`s are ECMA-262 expressions over ASCII digits, which the vendored models read as such.

### query-tools 4.0.0

`QuoteVerification` is a union on `result` (audit AG-4). A `match` must carry `match`, must
have searched at least one section, and its `endOffset` is at least 1; a `no-match` must not
carry `match`. Before 4.0.0 a `match` with no location validated, and a post-check that read
`result` alone — this one, until 2026-09-27 — stamped it verified. The service never produced
one, so the change is defence in depth: an answer the union refuses is unavailable here, never a
match. The schema cannot say that `startOffset` comes before `endOffset` (zod checks it at the
service); the post-check's own rule is stricter anyway, since every match must sit at its
chunk's exact offsets. A `verify_quote` answer from a service on an earlier version validates
here unchanged, because the service never produced an instance 4.0.0 refuses. 4.0.0 is built on
3.0.0, the query service's own batch of the same audit (a `/` in a product identifier; errors
without `structuredContent`), merged from `main` with the schemas regenerated: the agent is
adapted to both.

## The turn id

The service's audit record and this agent's are joined on one value. `tools.begin_turn`, the
agent's `before_agent_callback`, generates a UUID when the turn starts — before the model runs,
so before any tool call — and puts it in session state under a `temp:` key, which ADK does not
persist beyond the invocation. `bearer_header_provider` reads it and sends it as
`X-Query-Turn-Id` on every request of the turn, alongside `Authorization`; `turn.answer_turn`
takes the same value, read back with `tools.current_turn_id`, as the record's `turnId`. It is
not generated in `answer_turn`: a record whose id the tool calls never carried could not be
joined to anything.

A context with no turn id sends `Authorization` alone, which the service accepts. A context
whose turn id is present but not a UUID fails the call closed (`InvalidTurnIdError`), because
the contract types the header's value as a UUID and a value the service would refuse is not
worth sending. `tests/test_toolset_wiring.py` covers all three, and the fake service captures
the header off the wire so `tests/test_turn.py` can assert that every request of a turn carried
the same id.

## Where the contract left something open

**`VerifyQuoteInput.quote` is `maxLength: 2000`, and the two sides count differently.** JSON
Schema defines `maxLength` over Unicode code points; the service enforcing it is TypeScript,
where a string length is UTF-16 code units. For anything outside the Basic Multilingual Plane
the two disagree, and the contract does not say which governs. `split_for_verification`
measures UTF-16 units — the smaller and therefore safe reading — and never splits a surrogate
pair. Worth one clause in the contract, the way `docs/fidelity-normalization.md` closed the
same class of question for offsets.

The splitter matters because a section can be a hundred times longer than one `verify_quote`
argument. It cuts on the last U+0020 inside the window where the service's quote-edge rule
(`quote_edge.py`) holds on both sides — not between two digits, not just after a comparator or
sign set off by a space — dropping the separator rather than carrying a leading or trailing
space into either chunk, since normalisation would remove one anyway. Every chunk is a
contiguous substring of the block, which is what makes each one findable in the stored section,
and a block is verified only if **every** chunk came back `match`.

The bound: a chunk is longer than the window only when the window holds no acceptable cut at
all (a single token longer than 2,000 units, or an unbroken run of space-grouped digits), and
it then ends at the first acceptable cut after the window, or at the end of the block. A cut
inside such a run would be a certain `no-match`, reported as if the text were not the label's;
the longer chunk is not sent (the contract refuses it) and the block is flagged
`verification-unavailable`. Neither verifies the block; only the second says why truthfully.

The splitter reads each cut's sign from an index built once over the whole block, exactly as
the service reads it over the section, and measures each window once: 200,000 code points of
`( ` split in about a tenth of a second, where the bounded walk back from every space took
21.9 s (audit AG-12). It gives each chunk's offsets (`chunk_spans`), which is what the
post-check holds every match to. The chunks are then evened out: the smallest window that needs
no more chunks than the full one is used, so a block just over 2,000 units is two halves, not a
full chunk and a sliver of a few words.

**A normalisation-version bump needs this agent first.** The post-check accepts answers under
`fidelity-norm/3.1.0` only (`quote_edge.NORMALIZATION_VERSION`, held to the service's own export
by a test). When Zone B moves to a new normalisation version, port the quote-edge rule to it and
redeploy the agent **before** the service answers under it; until then every block is flagged
`checksum-mismatch` and shown unverified.

**Tables and pictures cannot be checked yet.** A section's text carries the scanner's grid
markers (U+FDD0–U+FDEF) and a picture's U+FFFC, and `verify_quote` refuses any quote holding
one (`invalid-request`). A chunk holding one is therefore not sent, and the block is flagged
`table-not-quotable` — before 2026-09-27 it was sent and came back `verification-unavailable`,
which told the reader the service had failed. Every section with a table or a picture is shown
unverified, which includes Imatinib Teva's 4.2 and 4.8, the 3a demonstration sections; cell-level
quoting is roadmap 3a PR 5.

**A repeated chunk fails closed.** The service answers the first occurrence the quote-edge rule
accepts. A chunk whose text also occurs earlier in its section matches there, at other offsets,
and the block is flagged `coverage-gap` although it is the label's text. For a chunk of up to
2,000 units in a real label this has not been seen, and evening the chunks out keeps a short last
chunk from making it likely; it is a false failure, never a false pass.

## Audit

`TurnAuditRecord` is the `AgentTurnRecord` of `contracts/generated/agent-turn.schema.json`: the
same fields, tool names restricted to the four `QueryToolName` values, flags to the
nine `VerificationFlag` values, `outcome` and `assistantFlags` to theirs, and `serviceVersion`,
`principal`, `principalDigest`, `turnId` and `errorClass` held to the contract's patterns in the
pydantic model, so a record the contract would refuse cannot be built. An optional field left
empty is omitted, never `null`. `tools` is capped at the contract's 200; a turn that made more
calls gets no full record rather than a cut one. `tests/test_audit.py` serialises a record and
validates it with `jsonschema` against the vendored schema, and asserts the patterns, the
enumerations and the cap are the schema's own values.

**The principal is never an e-mail address** (audit AG-1). Gemini Enterprise gives the agent the
user's e-mail as the session's user id; the contract's `principal` refuses one, and until
2026-09-27 that refusal was raised inside the post-check's guard, so every live turn showed "could
not be verified" and wrote nothing. Now only a known opaque form — a numeric subject or a URN —
is carried as it is; anything else, a name that fits the contract's characters included, is
withheld (`session-user-withheld`), with an HMAC-SHA256 of it, casefolded, under
`AGENT_PRINCIPAL_DIGEST_KEY` in `principalDigest` when the runtime has that key and it is at
least 32 bytes (`deploy/README.md`, step 2); a shorter key is not used. The principal the query service verified from the user's token is
on its own records of the same `turnId`.

**Every turn writes one record, and the record never costs the answer** (audit AG-1, AG-6). The
turn's end (`finish.py`) decides what is shown first and builds the record apart from it:
`outcome` says which way the turn ended — `answered`, `tools-unavailable`, `model-failed`,
`turn-id-missing` (with the nil UUID as `turnId`), `internal-error` — and `errorClass` names the
exception's class where one ended it, never its message. If the full record cannot be built or
written, a minimal one (the tools if they fit, the class of that failure, and — for an answered
turn — the checked answer's own counts and flags, otherwise zeros) is written instead, and the
checked answer is still shown. Nothing else in the package may log, so
the record is the only trace a failed turn leaves.

**Every call is recorded as it ran** (audit AG-5). `tools.ToolCallLog` is the agent's
`before_tool_callback`, `after_tool_callback` and `on_tool_error_callback`, so each of the
model's calls is timed as it runs; the post-check adds each of its `verify_quote` calls the same
way. Each `resultCount` is the service's own (the products found, one section or provenance, one
match or none). Until 2026-09-27 the deployed record listed the model's `get_section` calls
alone, read back from the turn's events with a duration of 0, and none of the post-check's.

What is identical to `QueryAuditRecord` is what is absent. No narrative. **No argument digest
either**, unlike `QueryAuditRecord`: a `verify_quote` argument _is_ narrative, and a digest of
a quote is a way of asking whether a document contains a sentence. No token in any form.

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
- **No deploy from CI.** `deploy/` is run by the owner by hand (first deployed 2026-09-22); see
  `deploy/README.md`.
- **No agent-side normalisation or fidelity checking.** Those live where the specification
  lives. The one hash the agent computes is SHA-256 over UTF-8, of strings the service returned,
  to compare with the hashes it returned beside them.
- **No A2UI.** The agent renders text only, because nothing it is served through advertises
  the A2UI extension. An A2UI renderer was built and never sent; it was removed in refactor R1
  (in history at `250d8a2`). Re-adding it means settling, for that surface, whether a `Text`
  component parses Markdown, and testing it as `render_text` is tested.
- **No selection of blocks by the model.** Every section the model fetched in the turn is shown
  (at most eight, `compose.MAX_BLOCKS`; the answer says how many more were read), labelled as
  the sections read, not as the answer; the model's own words say which of them answer the
  question.

## The residual risk this does not close

The post-check catches **alteration**, not **absence**. An assistant that quotes three sections
correctly and silently omits the fourth produces an answer in which every block verifies and
which is still misleading. That is why "the answer is the tool result" matters more than "the
assistant is careful", and why the intended-use statement in the design note calls this an
information-retrieval aid for trained staff rather than a substitute for reading the label.
Prompt injection through document content is bounded by `ContentNotice` and by the model; it is
not eliminated.
