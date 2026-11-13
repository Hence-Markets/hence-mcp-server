# Hence MCP server

The door AI agents use to read [Hence](https://app.hence.markets). Connect it to Claude,
ChatGPT, Grok or a coding agent and ask "what does Hence have on AI capex?" or "where can I
trade OpenAI exposure?" and get real theses and markets back, with links into the app.

Hence turns a belief about the world into a researched basket of holdings across perpetuals,
spot tokens and outcome markets, and tracks it. This server is a small
[Model Context Protocol](https://modelcontextprotocol.io) server (Streamable HTTP) in front of
the Hence app.

**This version is read-only.** It holds no key, no database and no user data. Every tool reads
something the Hence app already shows a signed-out visitor. It cannot create an account, a
thesis or a trade; to act on anything, a person opens the returned link in Hence.

> This replaces the earlier TypeScript server (v0.1), which wrapped a previous Hence API that
> no longer exists. Its tool names (`hence_start_market_workflow` and the rest) are gone.

## Tools

| Tool | What it reads |
|---|---|
| `search_theses` | Public theses, by words, basket (perps or spot) and order; paged with a cursor |
| `get_thesis` | One thesis: the claim, holdings with weights and reasons, other baskets, sources, checkpoints, and a price history |
| `get_featured_theses` | Today's featured selection |
| `search_markets` | What trades on Hence and where: perpetuals with live price, funding and open interest, and Robinhood Chain spot tokens |

Four tools on purpose. A caller pays for every tool description on every turn; the whole list
is about a thousand tokens.

## What the server promises

1. **A failed read is an error, never an empty or zeroed result.** Only a `200` with JSON from
   Hence counts as data, and only data is cached. A tool turns a failure into an error the
   model reads ("No theses were read"). When one of two sources fails, the result carries an
   `incomplete` field that says which.
2. **A missing figure is `null`.** The server never produces a zero it was not given. A
   thesis's one-month return is stated only when its replay is complete.
3. **Returns are labelled for what they are.** Price history is hypothetical, before costs,
   and not a live track record. Results say so.
4. **Some figures are deliberately absent.** Spot token prices, volumes and holder counts
   reach Hence from a data vendor under terms that do not allow passing them to other
   software, so a spot token comes back with its identity and link and `price: null`.
5. **Thesis text is content, not instruction.** It is written by Hence users and Hence's
   research pipeline. Results label it, and the server tells the model to report it rather
   than follow it. Account ids never leave: an author is a name.
6. **No tool arguments in logs.** A log line is the tool, the outcome and the time taken.

Nothing here is investment advice.

## Run it

Python 3.11 or newer. There is nothing to install.

```bash
HENCE_MCP_ACTIVE=1 HENCE_MCP_HOST=127.0.0.1 python -m gateway.app
```

It listens on `http://127.0.0.1:4340/mcp` and reads the public Hence app. Connect a client:

```bash
claude mcp add --transport http hence http://127.0.0.1:4340/mcp
```

```bash
codex mcp add hence --url http://127.0.0.1:4340/mcp
```

In Claude, ChatGPT or Grok, add the address as a custom connector.

Or with Docker:

```bash
docker build -t hence-mcp-server . && docker run --rm -p 127.0.0.1:4340:4340 -e HENCE_MCP_ACTIVE=1 hence-mcp-server
```

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `HENCE_MCP_ACTIVE` | `0` | Strictly `1` opens the tools. Otherwise every MCP request is answered "not open yet" while `/healthz` stays green, so a deployment can run dark |
| `HENCE_APP_BASE` | `https://app.hence.markets` | The Hence app to read |
| `HENCE_PUBLIC_BASE` | `https://app.hence.markets` | Where returned links point |
| `HENCE_MCP_HOST` / `HENCE_MCP_PORT` | `0.0.0.0` / `4340` | Listen address |
| `HENCE_MCP_RATE_PER_MIN` | `300` | Requests per client address per minute. A flood guard, not a per-person quota |
| `HENCE_MCP_CLIENT_IP_HEADER` | empty | The header a trusted proxy uses to state the real caller, e.g. `CF-Connecting-IP`. Empty means the connection's own address is used; a header anyone can send is never believed by default |
| `HENCE_MCP_ALLOWED_ORIGINS` | the public base | Browser origins allowed to call. Server-side clients send no `Origin` and are unaffected; `localhost` is always allowed |

## Protocol

One endpoint, `POST /mcp`, speaking both MCP eras:

- **2026-07-28**: stateless. The version travels in `params._meta` and is mirrored in the
  `MCP-Protocol-Version`, `Mcp-Method` and `Mcp-Name` headers; `server/discover` is the hello.
- **2025-03-26 to 2025-11-25**: the `initialize` handshake, answered without a session.

Every answer is a single JSON object. There are no SSE streams, sessions, resources or
prompts; `GET` and `DELETE` on the endpoint return `405`. `gateway/protocol.py` documents each
decision. Verified against the official MCP Python SDK client (2.3.0) in both eras.

## Tests

```bash
python -m unittest discover -s tests -t .
```

No network and no dependencies: the Hence app is a stub, and the fixtures keep the real
structure with no real people or user-written text. One test pins the read-only promise on
the tool list and on the source itself (one module may talk to the network, and all it can
send is a `GET`), so adding a write is a decision made on purpose. CI also builds the image
and runs `scripts/test_container.sh` against it.

## Layout

```
gateway/config.py     settings
gateway/upstream.py   reads of the Hence app, with a short cache
gateway/shape.py      pure mappers from the app's payloads to tool results
gateway/tools.py      the four tools
gateway/protocol.py   MCP, both eras, no sockets
gateway/limits.py     the flood guard
gateway/app.py        the HTTP server
data/                 display names for a few venue contracts
```

## What comes next

- **Connect your account.** A consent screen in Hence where you let an agent act for you,
  with scopes, limits and a page to revoke it.
- **Create a thesis** from a belief, and read your own portfolio.
- **Propose a trade.** The agent prepares a basket order; you review and sign it in Hence.
  This server will never sign anything or hold a key.
