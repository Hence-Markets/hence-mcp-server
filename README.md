# Hence MCP Server

Public MCP gateway for safe Hence market workflows.

The server is intentionally a thin adapter over the Hence API. Cortex, scenarios,
strategy construction, paper trading, TradeXYZ, prediction-market context, and
durable records remain in the Hence backend.

## Safety Defaults

- No live orders.
- No private keys accepted.
- Paper/watch-only workflows only.
- Durable thesis records require explicit tool calls and user approval.
- Optional bearer auth for hosted deployments.

## Tools

- `hence_start_market_workflow`
- `hence_continue_market_workflow`
- `hence_approve_thesis`
- `hence_get_workflow_status`
- `hence_search_events`
- `hence_get_market_context`
- `hence_start_thesis_watch`

## Run Locally

```bash
pnpm install
pnpm build
node dist/index.js --base-url http://127.0.0.1:3001/api
```

That starts stdio mode for local clients. For hosted HTTP mode:

```bash
HENCE_API_BASE_URL=http://127.0.0.1:3001/api PORT=3333 pnpm start
```

Health check:

```bash
curl http://127.0.0.1:3333/health
```

## Hosted HTTP MCP

Deploy the container with:

```bash
HENCE_MCP_TRANSPORT=http
HENCE_API_BASE_URL=https://api.hence.ai/api
PORT=3333
```

Optional auth:

```bash
HENCE_MCP_AUTH_TOKEN=<bearer-token>
```

If `HENCE_MCP_AUTH_TOKEN` is set, clients must send:

```text
Authorization: Bearer <bearer-token>
```

## Client Setup

### Claude Code, Hosted HTTP

```bash
claude mcp add --transport http hence https://mcp.hence.ai/mcp
```

### Codex, Hosted HTTP

```bash
codex mcp add hence --url https://mcp.hence.ai/mcp
```

### Hermes, Hosted HTTP

Add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  hence:
    url: "https://mcp.hence.ai/mcp"
    timeout: 180
    connect_timeout: 30
```

Then run `/reload-mcp`.

### Local Stdio

Claude Code:

```bash
claude mcp add --transport stdio --scope user hence -- node /path/to/hence-mcp-server/dist/index.js --base-url http://127.0.0.1:3001/api --ref luma-seoul
```

Codex:

```bash
codex mcp add hence -- node /path/to/hence-mcp-server/dist/index.js --base-url http://127.0.0.1:3001/api --ref luma-seoul
```

Hermes:

```yaml
mcp_servers:
  hence:
    command: "node"
    args:
      - "/path/to/hence-mcp-server/dist/index.js"
      - "--base-url"
      - "http://127.0.0.1:3001/api"
      - "--ref"
      - "luma-seoul"
    timeout: 180
    connect_timeout: 30
```

## Smoke Prompt

```text
Use the Hence MCP tools to create a paper-only 6m medium-risk SpaceX / Starlink IPO strategy. Include TradeXYZ SPCX market data if available, suggest public-market and prediction-market proxies, reject unverified tokenized routes, and do not place live trades. Tell me which Hence MCP tools you used and what legs were selected.
```

Expected behavior: the agent should use Hence MCP tools, keep live trading
blocked, treat SpaceX secondary exposure as manual, and use TradeXYZ `xyz:SPCX`
as data-only market context when available.

