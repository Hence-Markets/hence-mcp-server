"""Cards inside the chat (MCP Apps, the `io.modelcontextprotocol/ui` extension).

A host that supports MCP Apps (Claude, Claude Desktop, VS Code Copilot and others) can draw a
tool's answer as a small page instead of only handing the model JSON. This server offers one:
a thesis, or a list of theses, with holdings, the price history and a button that opens Hence;
a market (its price, 24 hour change, funding and open interest) or a list of markets;
and a trade the agent prepared, with a button that opens it in Hence for the person to review
and sign. The card places nothing and says "not placed" until the app itself says otherwise.
With agent trading on, it also draws an order the agent placed itself (what filled, at what
price, or plainly that it did not) and what the agent may place on its own (the person's
limits, today's use, when it ends, and its recent orders). It never calls an order filled
unless the order's own status says so.

How it fits together:
  - a tool that has a card says so in its definition: _meta.ui.resourceUri = CARD;
  - the host fetches that address with resources/read and gets one self-contained HTML page;
  - the host hands the page the tool's result, and the page draws it.

The page is data/thesis-card.html. It loads ONE kind of thing: asset logos, as images, from
Hence's own public address (the content policy below names that address under images and
nothing under anything else). No script, font or stylesheet is loaded, and the page can call
nothing: every figure it shows arrives from the host. It puts every value on the page as text
and never as markup, and shows a figure that was not read as "n/a". A chat that allows no
images still gets a whole card: each logo has a lettered tile under it. A host with no support
for cards ignores all of this: every tool still returns its full answer as text.

Until 2026-11-15 the page loaded nothing at all and looked like nothing in particular. It is
now drawn in Hence's own look, with the logos people know the assets by.
"""
import urllib.parse
import os

from . import config

CARD = 'ui://hence/thesis-card'
MIME = 'text/html;profile=mcp-app'
# the tools whose answer the card can draw: one thesis, a list of them, what can be traded
# (one market in full, or a list), a prepared trade, or an order the agent placed and what it may place
TOOLS = ('search_theses', 'get_thesis', 'get_featured_theses', 'search_markets', 'get_asset', 'list_my_theses', 'get_my_thesis', 'propose_trade',
         'propose_basket', 'get_trade_proposals', 'get_trading_status', 'place_trade')

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
             'description': 'Draws a Hence thesis, a list of theses, a market with its live figures, a list of markets, a trade prepared '
                            'for the person to review and sign in Hence, or an order the agent placed and what it may place, inside the '
                            'conversation.', 'mimeType': MIME}]


def image_origins():
    """Where the card may load an image from: Hence's public address, and only when it is a
    plain https one. A deployment whose public address is not https (a developer's laptop) gets
    none, and the card shows its lettered tiles."""
    u = urllib.parse.urlsplit(config.public_base())
    if u.scheme != 'https' or not u.hostname or u.username or u.password:
        return []
    return ['https://' + u.netloc]


def read(uri):
    """The resources/read answer for `uri`, or None when this server has no such resource.
    Raises OSError when the page cannot be read from disk."""
    if uri != CARD:
        return None
    return {'contents': [{
        'uri': CARD, 'mimeType': MIME, 'text': html(),
        # Images from Hence's own address (asset logos), and nothing else: no address the page may call, frame or
        # be based on. Everything it shows as a figure arrives from the host.
        '_meta': {'ui': {'csp': {'connectDomains': [], 'resourceDomains': image_origins(), 'frameDomains': [], 'baseUriDomains': []}, 'prefersBorder': False}},
    }]}
