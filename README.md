# Hence MCP Server

Give coding agents a clean, safe way to ask Hence for market context, thesis
construction, paper strategies, and scenario-linked evidence.

This repo contains the MCP gateway for [Hence](https://hence.markets). It is
designed for agent flows like Codex, Claude Code, Hermes, Cursor, and other MCP
clients that should be able to say: "I have a market view. Help me express it
with the best available assets, evidence, and paper/watch-only strategy."

## What This Does

The MCP server exposes a small set of tools that wrap the existing Hence API:

- Start a market thesis workflow from a natural-language goal.
- Continue the workflow after clarification.
- Fetch a concise workflow summary for presentation-safe output.
- Search Cortex events and evidence.
- Fetch market context for an asset, theme, or scenario.
- Approve a candidate thesis into durable Hence strategy records.
- Start a follow-up watch when the user explicitly opts in.

The important design choice: this server is a gateway, not a second backend.
Cortex, scenarios, strategy construction, paper trading, TradeXYZ, prediction
market context, and durable records all remain in the Hence API.

## Safety Model

This v0 server is intentionally safe by default:

- No live orders.
- No private keys.
- No wallet custody.
- No silent durable record creation.
- Paper/watch-only workflows only.
- TradeXYZ and other `data_only` venues are market context, not live routing.
- Approval tools should be called only after explicit user approval.

If a route is manual, unsupported, or data-only, agents should say that clearly
instead of pretending it is executable.

## Tools

| Tool | Purpose |
| --- | --- |
| `hence_start_market_workflow` | Start a safe paper/watch-only thesis workflow. |
| `hence_continue_market_workflow` | Continue after the user answers a clarification question. |
| `hence_get_workflow_summary` | Get a concise presenter-friendly candidate/legs/evidence/blockers summary. |
| `hence_approve_thesis` | Approve/edit a candidate thesis and create durable records. Always pass `selected_candidate_id` when multiple candidates exist. |
| `hence_save_strategy` | Deprecated alias of `hence_approve_thesis` kept for compatibility; prefer approval directly. |
| `hence_get_workflow_status` | Read workflow status and created object IDs. |
| `hence_search_events` | Search Cortex event intelligence. |
| `hence_get_market_context` | Fetch candidate theses, evidence, and market context. |
| `hence_start_thesis_watch` | Start a follow-up watch after explicit consent. |

Tools that create durable records accept optional identity metadata:
`anonymous_user_id`, `account_id_hash`, `wallet_address`, `client_type`,
`session_id`, and `conversation_id`. Agents should pass a stable non-secret
anonymous or session id when available; wallet/Privy ownership can be added by
authenticated clients later without changing the workflow shape.

## Hosted MCP Endpoint

The intended public endpoint is:

```text
https://mcp.hence.markets/mcp
```

The hosted MCP server should point at:

```text
https://hence-api.hence.markets/api
```

For local backend development, use the stdio setup below and point `--base-url`
at the API server you are running.

## Quickstart For Agents

### Codex, Hosted HTTP

```bash
codex mcp add hence --url https://mcp.hence.markets/mcp
```

**What to do next (this is required):**

1. **Start a fresh Codex session**. The current thread will usually not hydrate newly added MCP tools.
2. In the new session, run:

```text
/mcp
```

3. Confirm you can see the `hence` server/tools.
4. Then try a first prompt like:

```text
Use Hence MCP to turn this belief into a paper strategy. Show me:
- the thesis in one sentence
- the asset basket with ticker + full asset name
- the exact percentage allocation for each asset
- one short reason per asset
- how I can save it anonymously or under a handle

Belief: humanoid robots walking about US cities by 2028, not just one demo but thousands.
```

**If you do not start a fresh session, Codex may show the MCP as installed but still fail to expose callable Hence tools in the current thread.**

### Claude Code, Hosted HTTP

```bash
claude mcp add --transport http hence https://mcp.hence.markets/mcp
```

Then in Claude Code:

```text
/mcp
```

### Hermes, Hosted HTTP

Add this to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  hence:
    url: "https://mcp.hence.markets/mcp"
    timeout: 180
    connect_timeout: 30
```

Then run:

```text
/reload-mcp
```

## Presentation-Safe Local Setup

Use this when the hosted endpoint is not live yet.

```bash
git clone https://github.com/Hence-Markets/hence-mcp-server.git
cd hence-mcp-server
pnpm install
pnpm build
```

Then add the local stdio server to your agent.

### Codex Local Stdio

```bash
codex mcp add hence -- node "$PWD/dist/index.js" --base-url http://127.0.0.1:3001/api --ref demo
```

Then:

1. start a **fresh Codex session**
2. run `/mcp`
3. confirm the `hence` server is visible before asking for a thesis

If Codex says the server is installed but Hence tools are not callable, you are almost certainly still in the old session.

### Claude Code Local Stdio

```bash
claude mcp add --transport stdio --scope user hence -- node "$PWD/dist/index.js" --base-url http://127.0.0.1:3001/api --ref demo
```

### Hermes Local Stdio

Add this to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  hence:
    command: "node"
    args:
      - "/absolute/path/to/hence-mcp-server/dist/index.js"
      - "--base-url"
      - "http://127.0.0.1:3001/api"
      - "--ref"
      - "demo"
    timeout: 180
    connect_timeout: 30
```

Then run `/reload-mcp`.

## Run The Server Yourself

Stdio mode:

```bash
pnpm install
pnpm build
node dist/index.js --base-url http://127.0.0.1:3001/api
```

HTTP mode:

```bash
HENCE_API_BASE_URL=http://127.0.0.1:3001/api PORT=3333 pnpm start
```

Health check:

```bash
curl http://127.0.0.1:3333/health
```

## Deployment

Deploy the container or Node service with:

```bash
HENCE_MCP_TRANSPORT=http
HENCE_API_BASE_URL=https://hence-api.hence.markets/api
PORT=3333
```

Optional bearer auth:

```bash
HENCE_MCP_AUTH_TOKEN=<bearer-token>
```

When `HENCE_MCP_AUTH_TOKEN` is set, clients must send:

```text
Authorization: Bearer <bearer-token>
```

## Smoke Prompt

Use this after the MCP server is connected:

```text
Use the Hence MCP tools to create a paper-only 6m medium-risk SpaceX / Starlink IPO strategy. Include TradeXYZ SPCX market data if available, suggest public-market and prediction-market proxies, reject unverified tokenized routes, and do not place live trades. Tell me which Hence MCP tools you used and what legs were selected.
```

To test durable strategy creation, ask the agent to save only after review:

```text
Use Hence to create a paper-only 6m medium-risk SpaceX / Starlink IPO strategy. Show me the candidate, then if I approve, use Hence to save the strategy with a stable anonymous_user_id for this session. Do not place live trades.
```

Expected behavior:

- The agent uses Hence MCP tools.
- Live trading remains blocked.
- SpaceX secondary exposure is manual.
- TradeXYZ `SPCX` appears as `xyz:SPCX` data-only when market data is available.
- Public proxies and hedges are sized/qualified as basis-risky expressions.
- Unsupported or unverified routes are rejected instead of quietly included.

## Development Checks

```bash
pnpm typecheck
pnpm build
pnpm test
```

`pnpm test` runs protocol smoke tests for both stdio and HTTP transports.
