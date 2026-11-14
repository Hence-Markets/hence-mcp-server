"""Cards inside the chat (MCP Apps, the `io.modelcontextprotocol/ui` extension).

A host that supports MCP Apps (Claude, Claude Desktop, VS Code Copilot and others) can draw a
tool's answer as a small page instead of only handing the model JSON. This server offers one:
a thesis, or a list of theses, with holdings, the price history and a button that opens Hence;
a market (its price, 24 hour change, funding and open interest) or a list of markets;
and a trade the agent prepared, with a button that opens it in Hence for the person to review
and sign. The card places nothing and says "not placed" until the app itself says otherwise.

How it fits together:
  - a tool that has a card says so in its definition: _meta.ui.resourceUri = CARD;
  - the host fetches that address with resources/read and gets one self-contained HTML page;
  - the host hands the page the tool's result, and the page draws it.

The page is data/thesis-card.html. It loads nothing from anywhere (its content policy below is
empty, which means "no network at all"), puts every value on the page as text and never as
markup, and shows a figure that was not read as "n/a". A host with no support for cards
ignores all of this: every tool still returns its full answer as text.
"""
import os

from . import config

CARD = 'ui://hence/thesis-card'
MIME = 'text/html;profile=mcp-app'
# the tools whose answer the card can draw: one thesis, a list of them, what can be traded
# (one market in full, or a list), or a prepared trade
TOOLS = ('search_theses', 'get_thesis', 'get_featured_theses', 'search_markets', 'list_my_theses', 'get_my_thesis', 'propose_trade',
         'get_trade_proposals')

_HTML = None


def html():
    global _HTML
    if _HTML is None:
        with open(os.path.join(config.data_dir(), 'thesis-card.html'), encoding='utf-8') as f:
            _HTML = f.read()
    return _HTML


def tool_meta(name):
    """What a tool's definition carries when it has a card, else None."""
    return {'ui': {'resourceUri': CARD}} if name in TOOLS else None


def listing():
    return [{'uri': CARD, 'name': 'hence-thesis-card', 'title': 'Hence thesis card',
             'description': 'Draws a Hence thesis, a list of theses, a market with its live figures, a list of markets, or a trade prepared '
                            'for the person to review and sign in Hence, inside the conversation.', 'mimeType': MIME}]


def read(uri):
    """The resources/read answer for `uri`, or None when this server has no such resource.
    Raises OSError when the page cannot be read from disk."""
    if uri != CARD:
        return None
    return {'contents': [{
        'uri': CARD, 'mimeType': MIME, 'text': html(),
        # empty lists: the page may load and call nothing. Everything it shows arrives from the host.
        '_meta': {'ui': {'csp': {'connectDomains': [], 'resourceDomains': [], 'frameDomains': [], 'baseUriDomains': []}, 'prefersBorder': False}},
    }]}
