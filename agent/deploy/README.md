# Deploying the verifiable-answer agent

Nothing in this directory has been executed. It is written from Google's documentation, with
every claim cited, so that the person who does run it can check each step against the source
before touching a project. Documentation dates are the dates the pages were read, 2026-09-18 to
2026-09-20.

Five steps. Only step 2 is a script; the rest are console and Terraform work, which is the
correct division — an OAuth client and an IAM grant are decisions, not build artefacts.

| #   | Step                                                          | Where                           |
| --- | ------------------------------------------------------------- | ------------------------------- |
| 1   | An internal OAuth 2.0 client for the MCP connector            | Cloud console                   |
| 2   | Deploy the agent to Vertex AI Agent Engine                    | `deploy_agent_engine.py`        |
| 3   | Register the agent in the Gemini Enterprise Agent Gallery     | Console or Discovery Engine API |
| 4   | Grant the Gemini Enterprise service agent `roles/run.invoker` | Terraform (`query_invokers`)    |
| 5   | (Optional) Expose the same agent as a Google Chat app         | Apps Script quickstart          |

## Prerequisites, stated before anything is spent

- **Gemini Enterprise is a licensed product.** Confirm the organisation or the demonstration
  tenant has it before scheduling any of this. The query service and roadmap item 1c do not
  depend on it.
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
- **The client id becomes the audience the query service accepts on an access token.** That is
  the one change to roadmap item 1 the design note names: the service must accept both a
  Google-signed OIDC ID token (verified by signature and audience, as built) and a Google
  OAuth 2.0 access token (verified through the token-info endpoint, audience equal to this
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
AGENT_SERVICE_VERSION=agent/0.1.0 \
.uv-bootstrap/bin/uv run --frozen python deploy/deploy_agent_engine.py --dry-run
```

Drop `--dry-run` to deploy; add `--update projects/.../reasoningEngines/...` to update in
place. The script refuses to run if any variable is unset: there is no default project, no
default region, and no guessed bucket.

The script needs the Vertex AI SDK, which is **not** in `uv.lock` — it pulls the whole Google
Cloud client stack, none of which the agent runs. Supply it for the one command:

```bash
.uv-bootstrap/bin/uv run --frozen \
  --with 'google-cloud-agentplatform[agent-engines,adk]==2.1.3' \
  python deploy/deploy_agent_engine.py
```

Two details worth knowing before you read the script:

- **The API moved.** `client.agent_engines.create(agent=..., config=...)` via
  `vertexai.Client(project=..., location=...)` is current; the module-level
  `vertexai.agent_engines.create(...)` is the legacy form, and `agent_engine=` is deprecated in
  favour of `agent=`. `config.staging_bucket` is now required and replaces
  `vertexai.init(staging_bucket=...)`
  ([AgentEngines reference](https://docs.cloud.google.com/python/docs/reference/vertexai/latest/vertexai._genai.agent_engines.AgentEngines)).
- **The package moved too.** The 2.0 release (2026-08-28) split the agent surface out of
  `google-cloud-aiplatform` into `google-cloud-agentplatform`. Do not `pip install vertexai`:
  that PyPI name is a stale 1.71.1 shim; the `vertexai` namespace ships inside
  aiplatform/agentplatform.
- **Requirements come from `uv.lock`.** `requirements_from_lock()` walks the dependency graph
  from this package's own `dependencies`, so the deployed runtime gets the 61 packages the CI
  gate ran against and none of the dev-only ones.

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

- No Terraform for Agent Engine. It would need a project, a region, and a bucket that do not
  exist yet; the `query_invokers` grant in step 4 is the only infrastructure change this agent
  requires, and it is a variable in a module that already exists.
- No CI deploy step. The agent's CI job lints, type-checks and tests; it does not deploy.
- No A2A agent card. `render.A2UI_EXTENSION_URI` names the extension a card would advertise,
  but publishing one belongs with whatever serves the agent over A2A.
