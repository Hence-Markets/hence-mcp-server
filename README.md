# Hence MCP server

The door AI agents use to read [Hence](https://app.hence.markets). Connect it to Claude,
ChatGPT, Grok or a coding agent and ask "what does Hence have on AI capex?" or "where can I
trade OpenAI exposure?" and get real theses and markets back, with links into the app.

Hence turns a belief about the world into a researched basket of holdings across perpetuals,
spot tokens and outcome markets, and tracks it. This server is a small
[Model Context Protocol](https://modelcontextprotocol.io) server (Streamable HTTP) in front of
the Hence app.

**It reads, and it can leave a draft.** It holds no key to anyone's money and no database. Its
public tools read what the Hence app already shows a signed-out visitor. A person can also
connect their Hence account; then it can read their own theses and positions, and put a
thesis the agent wrote into their library as a private draft for them to review. It cannot
create an account, publish a thesis, place a trade or move money; to act on anything, a
person opens the returned link in Hence.

> This replaces the earlier TypeScript server (v0.1), which wrapped a previous Hence API that
> no longer exists. Its tool names (`hence_start_market_workflow` and the rest) are gone.

## Tools

| Tool | What it reads |
|---|---|
| `search_theses` | Public theses, by words, basket (perps or spot) and order; paged with a cursor |
| `get_thesis` | One thesis: the claim, holdings with weights and reasons, other baskets, sources, checkpoints, and a price history |
| `get_featured_theses` | Today's featured selection |
| `search_markets` | What trades on Hence and where: perpetuals with live price, funding and open interest, and Robinhood Chain spot tokens |
| `get_thesis_guide` | How to write a Hence thesis yourself: the method and the `hence-thesis/1` format |

With sign-in switched on there are six more, each touching only the connected person's own account:

| Tool | What it reads | Needs |
|---|---|---|
| `get_my_account` | The connected person's Hence username | Their Hence account connected, permission `account:read` |
| `list_my_theses` | Their theses: written, saved, followed, held on paper or archived, private ones included | `theses:read` |
| `get_my_thesis` | One of them in full, with versions and a price history | `theses:read` |
| `get_my_portfolio` | Open perpetual positions read live, realized trading results, and the last account value Hence recorded. It says what it does not include (cash, spot balances, outcome positions) | `portfolio:read` |
| `check_thesis` | Hence's verdict on a thesis the agent wrote: every problem at once, warnings, and the thesis as Hence would store it. Saves nothing | `theses:write` |
| `save_thesis_draft` | Puts that thesis in the person's Hence library as a private draft. The only tool that writes | `theses:write` |

Few tools on purpose. A caller pays for every tool description on every turn; the whole list
is about a thousand tokens.

## Writing the thesis yourself

An agent does not have to ask Hence to research an idea. It can do the thinking itself, with
its own model and sources, and hand Hence the result:

1. `get_thesis_guide` gives the method and the format (`skills/hence/write-a-thesis.md` is the
   same text).
2. `search_markets` finds each holding; its `market_id` goes into the thesis unchanged.
3. `check_thesis` returns every problem at once. Holdings are matched to Hence's own listings,
   and their names and venues come from Hence, not from the agent.
4. The agent shows the person the thesis. If they agree, `save_thesis_draft` puts it in their
   Hence library as a private draft, labelled as written by that agent.
5. The person opens the draft in Hence, changes what they want and saves it. That save is
   what makes it a thesis. Nothing an agent sends becomes one on its own.

This costs Hence no research run, and the person gets a basket shaped to what they asked for.

## The `hence` command

The same tools from a terminal, for scripts and for coding agents without an MCP client. It is
a thin client of this server (`hence_cli/`, standard library only), so a tool the server gains
is available at once through `hence tools` and `hence call`.

```bash
pipx install git+https://github.com/Hence-Markets/hence-mcp-server

hence theses search AI capex        # public theses about something
hence theses featured               # today's featured theses
hence thesis <id or link>           # one thesis in full
hence markets nvda                  # what can be traded on Hence, and where
hence login                         # connect this terminal to your Hence account
hence whoami
hence my theses                     # your own theses, private ones included
hence my thesis <id or link>        # one of them in full
hence portfolio                     # your open positions and trading results
hence guide                         # how to write a Hence thesis yourself
hence draft new > thesis.json       # a blank thesis to fill in
hence draft check thesis.json       # what is wrong with it; nothing is saved
hence draft save thesis.json        # save it as a private draft (asks first; --yes to skip)
hence tools                         # everything the server offers right now
hence call <tool> --arg key=value   # any tool by name
```

`--json` on any of them prints the exact structured answer. `--server` or `HENCE_MCP_URL`
points it at another server (https only, or this machine).

What it promises:

- **A failure is never data.** The reason goes to stderr, the exit code is non-zero and stdout
  is empty. Exit `1`: Hence could not do it. Exit `3`: run `hence login`.
- **A figure that was not read prints as `n/a`**, never as zero, and every note the server
  sent (a source that could not be read, "results are hypothetical") is printed under the result.
- **The sign-in stays put.** `hence login` opens Hence in the browser; you press Allow there.
  The credential is kept in `~/.config/hence/credentials.json`, readable only by you, and is
  sent only to the server it was issued for, only when that server asks for it. It is never
  printed. `hence logout` tells Hence to close the connection.

## For coding agents: the skill

`skills/hence/SKILL.md` teaches an agent when to use which tool and how to report what it
reads (unread figures are unavailable, returns are hypothetical, give the link, no advice).

In Claude Code this repository is a plugin that brings the MCP server and the skill together:

```
/plugin marketplace add Hence-Markets/hence-mcp-server
/plugin install hence@hence-markets
```

To add only the MCP server:

```bash
claude mcp add --transport http hence https://mcp.hence.markets/mcp
```

For any other agent, copy the `skills/hence` folder into its skills directory and add the
server address above to its MCP settings. Signing in to a Hence account happens in the
browser the first time a tool needs it (in Claude Code, through `/mcp`).

## Connecting a Hence account

Signing in happens on Hence, not here. This server is an OAuth 2.1 resource server; the Hence
app is the authorization server.

1. A client calls a signed-in tool with no credential and gets `401` with a
   `WWW-Authenticate` header pointing at `/.well-known/oauth-protected-resource/mcp`.
2. That document names the Hence app. The client sends the person there; they sign in as
   usual, see what the agent is asking for, and press Allow.
3. The client comes back with a bearer token. This server asks the Hence app, once, who the
   token speaks for. The app answers only for a token issued for this server's own address,
   and only while the person has not disconnected the agent in their Hence settings.
4. Reads of the person's data are made under that connection and signed with this server's
   own secret. **The person's token is never forwarded to a data route.**

What each failure means, because they are not the same thing to a person:

| The app says | This server answers | The person |
|---|---|---|
| The token is unknown, expired, revoked or for another server | `401`, `error="invalid_token"` | Connects again |
| The permission was not given | `403`, `error="insufficient_scope"` | Is asked for it |
| Nothing (timeout, 5xx) | A tool error: "try again shortly" | Keeps their connection |

An unanswered check is never turned into "sign in again": that would throw away a good
connection. A disconnect takes effect on the next call, not when the token expires.

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
| `HENCE_MCP_ACCOUNTS` | `0` | Strictly `1` turns sign-in on. It also needs the secret below; with either missing, the signed-in tool is not listed and no sign-in is advertised |
| `HENCE_MCP_GATEWAY_SECRET` | unset | The secret this server signs its calls to the Hence app with, at least 32 characters. The app holds the same value |
| `HENCE_MCP_RESOURCE` | `https://mcp.hence.markets/mcp` | This server's own address. A credential is issued for exactly this address |
| `HENCE_AUTH_ISSUER` | `https://app.hence.markets` | Who signs people in |

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
the tool list and on the source itself: two modules may talk to the network, one can only
send a `GET`, and the other can reach only the paths it lists. Adding a path is a decision
made on purpose. CI also builds the image
and runs `scripts/test_container.sh` against it.

## Layout

```
gateway/config.py     settings
gateway/upstream.py   public reads of the Hence app, with a short cache
gateway/appauth.py    the signed-in half: who a token speaks for, and signed reads of that account
gateway/shape.py      pure mappers from the app's payloads to tool results
gateway/tools.py      the four tools
gateway/protocol.py   MCP, both eras, no sockets
gateway/limits.py     the flood guard
gateway/app.py        the HTTP server
data/                 display names for a few venue contracts
hence_cli/            the `hence` command: a client of this server (not in the image)
skills/hence/         the skill for coding agents
.claude-plugin/       this repository as a Claude Code plugin and its own marketplace
.mcp.json             the server address, for the plugin
```

## What comes next

- **More to read once connected**: your theses and your portfolio.
- **Create a thesis** from a belief.
- **Propose a trade.** The agent prepares a basket order; you review and sign it in Hence.
  This server will never sign anything or hold a key.
