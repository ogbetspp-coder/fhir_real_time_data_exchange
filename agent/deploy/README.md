# Deploying the verifiable-answer agent

Written first from Google's documentation, with every claim cited, and corrected by the first
real deploy on 2026-09-22 (below). The agent is live as
`projects/398017980210/locations/europe-west4/reasoningEngines/6226059359072288768`, Python
3.14. Its post-check has not yet run in a live Gemini turn. Documentation dates are the dates
the pages were read, 2026-09-18 to 2026-09-20.

**Which build is live.** The build deployed before 2026-09-27 (from `caa5d9a`) predates that
day's audit fixes: it fails every turn whose user id is an e-mail address, which is every Gemini
Enterprise turn, and it records none of the post-check's calls. Redeploy from `main` before the
live turn. From that redeploy on, the deployed build is named by the `serviceVersion` of its own
audit records, `agent/<version>+<commit>`, which the deploy script derives and refuses to derive
from a tree that differs from its commit; record the commit in `docs/roadmap.md` beside `main`'s
when you deploy.

Six steps. Steps 1 and 4 were done for the MCP connector on 2026-09-21 and are reused; the
OAuth client is a console decision, the invoker grant a Terraform variable.

| #   | Step                                                                   | Where                        | State      |
| --- | ---------------------------------------------------------------------- | ---------------------------- | ---------- |
| 1   | An internal OAuth 2.0 client for the MCP connector                     | Cloud console                | done       |
| 2   | Deploy the agent to Vertex AI Agent Engine                             | `deploy_agent_engine.py`     | 2026-09-22 |
| 2b  | An authorization so Gemini Enterprise hands the agent the user's token | `authorization.sh` (owner)   | 2026-09-22 |
| 3   | Register the agent in the Gemini Enterprise Agent Gallery              | `register.sh`                | 2026-09-22 |
| 4   | Grant the Gemini Enterprise service agent `roles/run.invoker`          | Terraform (`query_invokers`) | done       |
| 5   | (Optional) Expose the same agent as a Google Chat app                  | Apps Script quickstart       | not now    |

**Step 2b, the token.** Gemini Enterprise runs an OAuth consent flow for each authorization in a
registered agent's `authorizationConfig.toolAuthorizations`, and hands the access token to the
agent in session state as `temp:<authorization id>`. The agent reads one key,
`temp:query_service_bearer_token`, so the authorization id is `query_service_bearer_token`;
`tests/test_deploy_authorization.py` holds the two together. The authorization reuses the
connector's OAuth client, so the query service sees the same principal, entitlement and audit
identity whether Gemini calls it directly or through the agent. The client secret is typed at a
prompt and stored encrypted by the API; it is never an argument, a file or a repository value.

## Prerequisites, stated before anything is spent

- **Gemini Enterprise is a licensed product.** Confirm the organisation or the demonstration
  tenant has it before scheduling any of this (for `dev`, confirmed 2026-09-21: a trial until
  2026-10-20). The query service and the demonstration set (delivered, was roadmap item 1c) do
  not depend on it.
- **Residency.** Gemini Enterprise offers the `eu` multi-region and `europe-west2` (London,
  which is not the EU); some features fall back to global. The Agent Runtime location must
  match the Gemini Enterprise app location — a global app takes any region, a US app takes
  `us-*`, an EU app takes `europe-*`
  ([register an ADK agent](https://docs.cloud.google.com/gemini/enterprise/docs/register-and-manage-an-adk-agent)).
  Confirm against the client's residency bar before promising EU-only.
- **Python.** `pyproject.toml` pins `>=3.14,<3.15`. Agent Engine's
  `AgentEngineConfig.python_version` enumerates `3.10`–`3.14` and **defaults to 3.10**, so
  `deploy_agent_engine.py` sets it explicitly
  ([Agent Platform types reference](https://pkg.go.dev/cloud.google.com/go/agentplatform/types),
  [reasoningEngines REST reference](https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/rest/v1/projects.locations.reasoningEngines)).

## Step 1 — the internal OAuth 2.0 client

Gemini Enterprise calls a custom MCP server as the end user, which means it needs an OAuth
client of its own with your identity provider (Google, Okta, or Entra). Create it in the Cloud
console with an **internal** consent screen; Terraform does not manage it, deliberately — the
consent screen is an organisational decision.

- Redirect URI: `https://vertexaisearch.cloud.google.com/oauth-redirect`
- Grant the scopes the query service expects, and capture the client id and client secret for
  the connector configuration.
- **The client id becomes the audience the query service accepts on an access token.** That is the
  one change to the query service (delivered, was roadmap item 1) the design note names: the service
  must accept both a Google-signed OIDC ID token (verified by signature and audience, as built) and
  a Google OAuth 2.0 access token (verified through the token-info endpoint, audience equal to this
  client id, `sub` as the principal), with `credentialType` recorded in the audit record.

Source: [Set up a custom MCP server](https://docs.cloud.google.com/gemini/enterprise/docs/connectors/custom-mcp-server/set-up-custom-mcp-server).

## Step 2 — deploy to Agent Engine

```bash
cd agent
AGENT_ENGINE_PROJECT=<project-id> \
AGENT_ENGINE_LOCATION=<region> \
AGENT_ENGINE_STAGING_BUCKET=gs://<bucket> \
QUERY_SERVICE_MCP_URL=https://<query-service-host>/mcp \
AGENT_MODEL=<pinned-gemini-model-version> \
.uv-bootstrap/bin/uv run --frozen python deploy/deploy_agent_engine.py --dry-run
```

Drop `--dry-run` to deploy; add `--update projects/.../reasoningEngines/...` to update in
place. The script refuses to run if any variable is unset: there is no default project, no
default region, and no guessed bucket. It also refuses when anything under `agent/` differs from
the commit (modified, staged or untracked), when the commit is not on `origin/main` (fetch
first), and when `AGENT_SERVICE_VERSION` is set: the version is derived,
`agent/<package version>+<commit>`, so every audit record names the code that wrote it. The
package uploaded is taken from the commit itself (`git archive`), so no ignored or untracked file
in the working tree — a `__pycache__`, a stray `.pyc` — can reach the runtime. Until 2026-09-27 it was typed by hand, and the engine ran a build that predated the fixes it
was assumed to carry.

Optional, each passed through only when set:

- `MCP_TIMEOUT_SECONDS` — the agent's MCP timeout, above 0 and at most 600 (default 30).
- `AGENT_PRINCIPAL_DIGEST_SECRET` — the name of a Secret Manager secret in the project holding a
  random key of at least 32 bytes (a shorter one is not used; `openssl rand -base64 48`). The engine then gets `AGENT_PRINCIPAL_DIGEST_KEY` as a secret reference (never a
  value; the key passes through neither this script nor the engine's configuration), and each
  audit record carries `principalDigest`, an HMAC-SHA256 of the Gemini Enterprise user's e-mail
  address under it. Without it the agent's records identify no user at all (`principal` is
  `session-user-withheld`); the query service's records of the same `turnId` still carry the
  principal it verified. The runtime's identity needs `roles/secretmanager.secretAccessor` on
  the secret. The key is read at run time, so it is never in the pickled agent.
- `AGENT_ENGINE_SERVICE_ACCOUNT` — the service account the engine runs as. See "The edge
  identity" below.

The configuration the agent is built with (`QUERY_SERVICE_MCP_URL`, `AGENT_MODEL`,
`AGENT_SERVICE_VERSION`, `MCP_TIMEOUT_SECONDS`) is pickled with it, and passed as environment
variables too, so the deployed runtime and the pickled agent agree.

The script needs the Vertex AI SDK, which is **not** in `uv.lock` — it pulls the whole Google
Cloud client stack, none of which the agent runs. Supply it for the one command:

```bash
.uv-bootstrap/bin/uv run --frozen \
  --with 'google-cloud-agentplatform[agent-engines,adk]==2.1.3' \
  python deploy/deploy_agent_engine.py
```

What the first real run (2026-09-22) corrected in this runbook, which had been written from
documentation before the SDK's 2.0 split:

- **The module is `agentplatform`, not `vertexai`,** and Agent Engine is `client.runtimes`
  (`agentplatform.Client(project=..., location=...).runtimes.create(agent=..., config=...)`).
  The `vertexai` namespace and `client.agent_engines` do not exist in
  `google-cloud-agentplatform` 2.1.3.
- **The agent is wrapped in `agentplatform.frameworks.AdkApp`,** which is also how Gemini
  Enterprise's calls arrive: its `streaming_agent_run_with_events` copies each authorization's
  end-user access token into session state as `temp:<authorization id>` and never persists it.
  That is read from the SDK's source, and it is the key the agent reads. So the SDK is a runtime
  requirement too, added after the `uv.lock` pins.
- **`extra_packages` is relative and the upload runs from `src/`.** The SDK archives each path as
  given, so an absolute path would nest the code under the deploying machine's home directory.
- **Requirements come from `uv.lock`.** `requirements_from_lock()` asks uv itself
  (`uv export --frozen --no-dev`) for every runtime package at its locked version with its
  platform marker, so the deployed runtime gets the packages the CI gate ran against, plus the
  SDK, and none of the dev-only ones. (A hand reading of the lock that dropped the markers failed
  the first deploy on `pywin32`.)
- **The query service URL must be the one it validates against.** The service accepts ID tokens
  whose audience equals `QUERY_AUDIENCE`:
  `https://ema-flow-dev-query-<project number>.<region>.run.app`. Cloud Run also answers on its
  other form, `https://ema-flow-dev-query-<hash>-<code>.a.run.app`, and a token minted for that
  one is refused with `401 {"error":"unauthenticated"}`. Use the audience form in
  `QUERY_SERVICE_MCP_URL`.
- **Live since 2026-09-22:**
  `projects/398017980210/locations/europe-west4/reasoningEngines/6226059359072288768`.
- **The model is `gemini-2.5-flash` in `europe-west4`,** so inference stays in the EU. On
  2026-09-22 no Gemini 3 model was served in that region.

## Step 3 — register in the Agent Gallery

Console: the Gemini Enterprise web app → **Agents** → **Add agent** → **Custom agent via Agent
Runtime**. Three fields: display name, description (the routing model reads it, so it should
say what the agent will and will not do), and the resource path
`projects/PROJECT_ID/locations/LOCATION/reasoningEngines/RESOURCE_ID` that step 2 printed.

API equivalent:

```
POST https://<ENDPOINT_LOCATION>-discoveryengine.googleapis.com/v1alpha/projects/<PROJECT_ID>/locations/global/collections/default_collection/engines/<APP_ID>/assistants/default_assistant/agents
{ "displayName": "...", "description": "...", "adkAgentDefinition": { ... } }
```

Prerequisites: the Gemini Enterprise Admin role, the Discovery Engine API enabled, an existing
Gemini Enterprise app, and the agent already deployed (step 2).

`register.sh` does this from the repository. It takes the app from `GEMINI_APP_ID` and has no
default, since an agent registered in the wrong app is offered to the wrong people; and
`authorization.sh` takes the OAuth client from `GEMINI_OAUTH_CLIENT_ID`, likewise. The `dev`
values, which both scripts once carried as silent defaults:

```bash
GEMINI_APP_ID=gemini-enterprise-17899354_1789935441481
GEMINI_OAUTH_CLIENT_ID=398017980210-mgn6flks5a9nmlbkgkhh1pple9tv2075.apps.googleusercontent.com
```

`register.sh`, `authorization.sh` and `grant-invoker.sh` pass the access token to `curl` on its
standard input (`--config -`), never on the command line where any process can read it, and
stop on an HTTP error with the answer shown (`--fail-with-body`) rather than parse an error body
as a result.

The registration's description says only what the agent does (2026-09-27): it shows the label
sections it read, verbatim and re-checked; its own remarks are labelled and not checked; it reads
only documents the user is entitled to, and any other is answered as not found. It used to say
the agent "machine-checks every quotation" and "refuses questions about products the user is not
entitled to", neither of which is what it does.

Sources: [Register and manage an ADK agent](https://docs.cloud.google.com/gemini/enterprise/docs/register-and-manage-an-adk-agent),
[Agent Gallery](https://docs.cloud.google.com/gemini/enterprise/docs/agent-gallery).

## Step 4 — let Gemini Enterprise reach the query service

When Gemini Enterprise calls a custom MCP server on Cloud Run it sends two headers:

- `X-Serverless-Authorization` — a Google-signed ID token authenticating the Gemini Enterprise
  service agent. Cloud Run's IAM check consumes it and **strips the signature afterwards** so
  the token cannot be replayed. It is sent **only for the default `*.run.app` URL**, not for a
  custom domain — which is a real constraint on how the query service is published.
- `Authorization` — the end user's OAuth 2.0 token, preserved intact. No collision, because
  Cloud Run consumed the other one.

That is exactly the two-layer model the query service was designed for: IAM at the edge,
end-user identity in the service. Grant the service agent the invoker role through the existing
Terraform variable rather than by hand:

```hcl
# infra/<env>.tfvars
query_invokers = [
  "serviceAccount:service-<PROJECT_NUMBER>@gcp-sa-discoveryengine.iam.gserviceaccount.com",
]
```

No new mechanism, no new module: `query_invokers` already exists for this purpose.

Source: [Set up a custom MCP server](https://docs.cloud.google.com/gemini/enterprise/docs/connectors/custom-mcp-server/set-up-custom-mcp-server).

## Supplying the client secret without pasting it

A paste into a hidden prompt carried the surrounding instructions with it on 2026-09-22: 217 then
319 characters went to Google as the secret, and the trailing lines ran as shell commands, which
echoed a live secret into a terminal. It was rotated. `authorization.sh` now checks the shape
before sending, and takes the secret four ways, of which the first needs no typing:

    bash agent/deploy/authorization.sh --clipboard          # copy it in the console, then run
    bash agent/deploy/authorization.sh client_secret_*.json # Google's download
    bash agent/deploy/authorization.sh --secret-file PATH   # a file holding it alone
    bash agent/deploy/authorization.sh                      # a hidden prompt

## An update needs `updateMask`, and must be read back

`PATCH` on an authorization without `updateMask` answers **200 and changes nothing**. Four runs
on 2026-09-22 reported success while the resource kept its first, mangled secret, and Gemini
Enterprise logged `Authorization failed: The provided client secret is invalid`. The script now
sends `updateMask=serverSideOauth2,displayName` and reads the resource back, checking the stored
`redirect_uri`: the response echoes what was sent, so only a read-back proves anything. The
secret itself can never be read back, which is why the redirect_uri is the field that is checked.

## The OAuth client needs both redirect URIs

The consent window opened, accepted the sign-in and never closed (2026-09-22). The authorization's
`authorizationUri` had no `redirect_uri`, so the result had nowhere to return to. Google's
template for an agent authorization fixes it at
`https://vertexaisearch.cloud.google.com/static/oauth/oauth.html`, which is **not** the
connector's `…/oauth-redirect`. The OAuth client must list both, and `authorization.sh` now
builds the documented URI.

## What the first Gemini Enterprise turns showed (2026-09-22)

Two questions asked in the app were answered **without any tool call ever reaching the query
service**, the second one inventing a `versionId` and quoting hashes it had seen in an earlier
session. The audit trail is unambiguous: `StreamAssist` was called on
`assistants/default_assistant` with no agent named, and the query service logged nothing at all.

Two causes, both fixed in `register.sh`:

- The registration carried no `sharingConfig`, so the agent was not offered in the app.
- It carried no `adkAgentDefinition.toolSettings.toolDescription`, which is what the assistant's
  router reads when deciding whether to route a question to an agent. Google's own Deep Research
  agent sets both.

The lesson for the demonstration, which is the product's whole argument: **an answer that looks
right is not evidence.** The transcript was fluent, correctly formatted, and cited a document
that exists; only the version id and the audit record showed that nothing had been read. Show
the version, the hash, and `verify_quote` — never the prose alone.

## Step 4b — let Gemini Enterprise run the agent

The first turn that reached the agent failed with `PERMISSION_DENIED: Reasoning Engine Execution
Service stream failed`. Gemini Enterprise calls a registered agent as its own service agent, and
`roles/discoveryengine.serviceAgent` does not include `aiplatform.reasoningEngines.query`.

`grant-invoker.sh` creates a custom role of exactly two permissions, query and get, and binds it
**on the one reasoning engine**, not on the project. The alternative, `roles/aiplatform.user`,
carries 451 permissions including create and delete on every Vertex AI resource.

```bash
AGENT_RESOURCE=projects/<number>/locations/<region>/reasoningEngines/<id> \
  bash agent/deploy/grant-invoker.sh
```

The regional endpoint is read from `AGENT_RESOURCE` itself (it once came from a separate
`GCP_REGION` defaulting to `europe-west4`), and an error answer from the IAM API now stops the
script with the answer shown, rather than being parsed as the policy and posted back.

## The edge identity

The agent's calls reach the query service through Cloud Run's edge, which does not accept the
user's OAuth token, so the agent also sends an ID token minted from the runtime's own
credentials (`X-Serverless-Authorization`; `tools.edge_auth_token`). Deployed without
`AGENT_ENGINE_SERVICE_ACCOUNT`, that runtime identity is the project's shared Reasoning Engine
service agent (`service-<number>@gcp-sa-aiplatform-re.iam.gserviceaccount.com`), and whatever
it has been granted on the query service is granted to every Agent Engine deployment in the
project, not to this agent alone. The service still verifies and entitles only the user's own
token, so the edge admits a caller, never a user; but the edge is meant to admit this agent.

The fix, an owner step: create a service account for the agent alone, grant it
`roles/run.invoker` on the query service (through `query_invokers`, as step 4 does for Gemini
Enterprise) and whatever Agent Engine needs to run it (logging, the staging bucket, and the
digest secret if one is used), remove the shared service agent from `query_invokers` if it is
there, and deploy with `AGENT_ENGINE_SERVICE_ACCOUNT` set to it. The exact runtime roles have not
been established in this project; confirm them against Agent Engine's documentation for a
custom service account before relying on this.

## What the agent keeps, and where

The agent's own audit record carries no narrative (`agent/README.md`, "Audit"). Agent Engine
does keep the conversation: with no `session_service_builder`, `AdkApp` uses Agent Engine
Sessions (`VertexAiSessionService`) whenever the runtime names its engine, which it does on
Agent Engine. Each session holds the user's question, every tool result of every turn —
including each `get_section` answer, the full section narrative — and the rendered answer, in
the engine's region, for the session's lifetime (not set here, and not yet confirmed), readable
with Agent Engine session permissions on the project. `temp:` state, and so the user's token, is
not persisted. `docs/design/verifiable-answers.md` ("Intended use") states this as the
retention it is; switching to an in-memory session service, which keeps nothing but loses a
conversation's history across runtime instances, is an owner decision.

## Step 5 — the Google Chat app (optional)

The same Agent Engine agent can front a Google Chat app, through Google's own quickstart: set
up the environment, deploy the ADK agent (step 2 already did), deploy an Apps Script Chat app
that references the reasoning-engine resource name in its script properties, configure it, and
test. No second copy of the agent, and no custom frontend.

Source: [Build a Google Chat app with an ADK AI agent](https://developers.google.com/workspace/add-ons/chat/quickstart-adk-agent).

## What the agent does with the user's token

ADK's `McpToolset` takes a `header_provider` callback invoked with a `ReadonlyContext` for
every MCP request; `tools.bearer_header_provider` reads the token from session state under
`temp:query_service_bearer_token` and returns an `Authorization` header. **If the token is
absent it raises and the tool call fails.** There is no Application Default Credentials
fallback and no delegated-trust path in which the agent asserts a user under its own identity —
the design note says that fallback is not needed, and it is not built.

Whatever hosts the agent must put the end user's token into that state key before the first
tool call. On Agent Engine behind Gemini Enterprise the token arrives on the request; the
alternative is Agent Engine's brokered three-legged OAuth (Agent Identity), in which the agent
never holds the raw credential. Either satisfies the design.

Session pooling is worth knowing about: ADK pools MCP sessions keyed by header identity, so a
provider that mints a fresh token per call fragments the pool. A per-user token for the life of
an invocation is the intended shape.

Sources: [ADK MCP tools](https://adk.dev/tools-custom/mcp-tools/),
[`mcp_toolset.py`](https://github.com/google/adk-python/blob/main/src/google/adk/tools/mcp_tool/mcp_toolset.py),
[Agent Identity](https://docs.cloud.google.com/agent-builder/agent-engine/agent-identity).

## What is not here

- No Terraform for Agent Engine. The engine is created and updated by `deploy_agent_engine.py`,
  and its invoker role (step 4b) by `grant-invoker.sh`, both run by hand; the `query_invokers`
  grant in step 4 is the only change this agent needed in the existing Terraform.
- No CI deploy step. The agent's CI job lints, type-checks and tests; it does not deploy.
- No A2A agent card. `render.A2UI_EXTENSION_URI` names the extension a card would advertise,
  but publishing one belongs with whatever serves the agent over A2A.
