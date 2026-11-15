"""Cards inside the chat (gateway/cards.py, data/thesis-card.html): a thesis drawn in the
conversation by hosts that support MCP Apps.

What these pin:
  * the page is self-contained and harmless: it loads nothing, calls nothing, and puts every
    value on the page as text, never as markup (thesis text is written by Hence users);
  * it never shows an unread figure as zero, and never an empty card for a failed answer;
  * a host without card support loses nothing: every tool still answers in text, and the
    card is extra metadata it can ignore.
"""
import os
import re
import unittest
from unittest import mock

from gateway import cards, protocol, shape, tools

from .support import T1, StubUpstream, standard_routes
from .test_account import ON
from .test_protocol import legacy, modern

HERE = os.path.dirname(os.path.abspath(__file__))


def page():
    with open(os.path.join(HERE, '..', 'data', 'thesis-card.html'), encoding='utf-8') as f:
        return f.read()


class Served(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {'HENCE_MCP_ACTIVE': '1'})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.up = StubUpstream(standard_routes())

    def run_(self, request):
        return protocol.handle(*request, self.up)

    def test_the_card_is_listed_and_read_in_both_eras(self):
        for make in (modern, legacy):
            status, out = self.run_(make('resources/list'))
            self.assertEqual((status, [r['uri'] for r in out['result']['resources']]), (200, ['ui://hence/thesis-card']), make.__name__)
            self.assertEqual(out['result']['resources'][0]['mimeType'], 'text/html;profile=mcp-app')
            status, out = self.run_(make('resources/read', {'uri': 'ui://hence/thesis-card'}))
            content = out['result']['contents'][0]
            self.assertEqual((status, content['uri'], content['mimeType'], content['text']), (200, cards.CARD, cards.MIME, page()), make.__name__)

    def test_the_page_may_load_images_from_hence_and_reach_nothing_else(self):
        # Until 2026-11-15 this policy was empty. The card now draws asset logos, so it may load an image from Hence's own
        # public address. Still nowhere it may call, frame or be based on.
        _, out = self.run_(modern('resources/read', {'uri': cards.CARD}))
        csp = out['result']['contents'][0]['_meta']['ui']['csp']
        self.assertEqual(csp, {'connectDomains': [], 'resourceDomains': ['https://app.hence.markets'], 'frameDomains': [], 'baseUriDomains': []})
        self.assertNotIn('permissions', out['result']['contents'][0]['_meta']['ui'])          # no camera, microphone, location, clipboard
        # only a plain https public address is ever named: anything else gets no image address at all
        for base, allowed in (('https://app.hence.markets', ['https://app.hence.markets']), ('https://staging.hence.markets:8443/app', ['https://staging.hence.markets:8443']),
                              ('http://127.0.0.1:4317', []), ('https://user:pw@app.hence.markets', []), ('ftp://app.hence.markets', []), ('nonsense', [])):
            with mock.patch.dict(os.environ, {'HENCE_PUBLIC_BASE': base}):
                self.assertEqual(cards.image_origins(), allowed, base)

    def test_an_unknown_resource_is_refused_and_nothing_else_is_readable(self):
        for uri in ('ui://hence/other', 'file:///etc/passwd', 'https://app.hence.markets/api/me', '../data/market-display.json', '', None, 7):
            status, out = self.run_(modern('resources/read', {'uri': uri}))
            self.assertEqual((status, out['error']['code']), (200, protocol.INVALID_PARAMS), repr(uri))
        self.assertEqual(self.run_(modern('resources/read', {}))[1]['error']['code'], protocol.INVALID_PARAMS)

    def test_the_server_says_it_has_resources(self):
        _, out = self.run_(modern('server/discover'))
        self.assertEqual(out['result']['capabilities']['resources'], {'listChanged': False, 'subscribe': False})
        _, out = self.run_(legacy('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'x', 'version': '1'}}))
        self.assertIn('resources', out['result']['capabilities'])

    def test_thesis_tools_point_at_the_card_and_the_rest_do_not(self):
        with mock.patch.dict(os.environ, dict(ON, HENCE_MCP_RESEARCH='1', HENCE_MCP_TRADES='1', HENCE_MCP_TRADING='1')):
            _, out = self.run_(modern('tools/list'))
        listed = {t['name']: t.get('_meta') for t in out['result']['tools']}
        with_card = sorted(name for name, meta in listed.items() if meta)
        self.assertEqual(with_card, sorted(cards.TOOLS))
        # theses, and a trade PREPARED for the person: its card says "not placed" and opens Hence (tests/test_trades.py);
        # and (2026-11-16) an order the agent PLACED and what it may place: the card says what filled only when the
        # order's own status is a fill (tests/test_agent_trading.py)
        self.assertEqual(with_card, ['get_asset', 'get_featured_theses', 'get_my_thesis', 'get_thesis', 'get_trade_proposals', 'get_trading_status',
                                     'list_my_theses', 'place_trade', 'propose_basket', 'propose_trade', 'search_markets', 'search_theses'])
        for name in with_card:
            self.assertEqual(listed[name], {'ui': {'resourceUri': 'ui://hence/thesis-card'}})
        # nothing else that writes, and nothing that reads the person's money, draws itself into a chat
        for name in ('save_thesis_draft', 'start_thesis_research', 'get_my_portfolio', 'get_my_account', 'check_thesis'):
            self.assertIsNone(listed[name], name)

    def test_a_host_without_cards_still_gets_the_whole_answer_in_text(self):
        _, out = self.run_(modern('tools/call', {'name': 'get_thesis', 'arguments': {'thesis_id': T1}}, name='get_thesis'))
        result = out['result']
        self.assertTrue(result['content'][0]['text'].startswith('{'))
        self.assertEqual(result['structuredContent']['thesis_id'], T1)


class ThePage(unittest.TestCase):
    def test_it_loads_asset_logos_from_hence_and_nothing_else_and_calls_nothing(self):
        html = page()
        # THE ONE THING IT LOADS (2026-11-15): an asset's logo, as an image, made in one place, from Hence's own address
        # (`home`: taken from the answer's own links, https only), tried in turn and then given up on. The tile under it stays.
        self.assertEqual(html.count("document.createElement('img')"), 1)
        self.assertEqual(len(re.findall(r'\bimg\.src\s*=', html)), 2)
        self.assertEqual(html.count('img.src = home + ICON + from['), 2)                # never an address from the answer itself
        self.assertIn("var home = null, ICON = '/api/icon?v=4&src=';", html)
        self.assertIn("function origin(url) { var safe = hence(url), m = safe ? /^https:\/\/[^\/?#\s]+/.exec(safe) : null; return m ? m[0] : null; }", html)
        self.assertIn('home = homeOf(value);', html)
        self.assertIn("var from = home ? sources(id, sym) : [];", html)                  # no Hence address in the answer: no image is asked for
        self.assertIn("img.referrerPolicy = 'no-referrer'", html)
        self.assertIn("else if (img.parentNode) img.parentNode.removeChild(img);", html)  # a logo that does not load leaves the lettered tile
        self.assertIn("var ok = /^[A-Za-z0-9._-]{1,24}$/", html)                          # only a plain ticker is ever put in an address
        self.assertEqual(html.count('encodeURIComponent('), 6)
        self.assertNotRegex(html, r'<(script|link|img|iframe|video|audio|source|object|embed)\b[^>]*\b(src|href)\s*=')
        self.assertNotRegex(html, r'@import|url\(|@font-face')
        self.assertNotRegex(html, r'\bfetch\(|XMLHttpRequest|WebSocket|EventSource|sendBeacon|importScripts|new Worker')
        self.assertNotRegex(html, r'localStorage|sessionStorage|document\.cookie|indexedDB')
        self.assertEqual(len(re.findall(r'<script', html)), 1)                     # one inline script, nothing loaded

    def test_every_value_goes_on_the_page_as_text(self):
        html = page()
        self.assertNotRegex(html, r'innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function|setAttribute\(\s*[\'"]on')
        self.assertIn('node.textContent = String(text)', html)
        # a link is opened only when it is a plain https address
        self.assertIn("typeof url === 'string' && /^https:\\/\\/[^\\s\"'<>]+$/.test(url) ? url : null", html)
        self.assertIn("rel = 'noopener noreferrer'", html)

    def test_an_unread_figure_is_n_a_and_a_failed_answer_is_said(self):
        html = page()
        self.assertIn("isNum(v) ? (v > 0 ? '+' : '') + v.toFixed(2) + '%' : 'n/a'", html)
        self.assertIn("(why || 'The price history could not be read') + '. No return is shown.'", html)
        self.assertIn('Hence could not answer. Nothing is shown.', html)
        self.assertIn('Incomplete: ', html)
        self.assertIn('Before costs. Not a live track record.', html)
        self.assertNotRegex(html, r"\|\|\s*0\b")                                    # no "or zero" anywhere a figure is read

    def test_it_speaks_the_view_side_of_the_protocol(self):
        html = page()
        for said in ("request('ui/initialize'", "protocolVersion: '2026-01-26'", "notify('ui/notifications/initialized')", "'ui/notifications/tool-result'",
                     "'ui/notifications/host-context-changed'", "'ui/notifications/size-changed'", "request('ui/open-link'", "'ui/resource-teardown'"):
            self.assertIn(said, html, said)
        self.assertIn('if (event.source !== window.parent) return;', html)         # only the host is listened to
        # the page asks the host for nothing but to open a link: no tool calls, no messages sent as the person
        self.assertNotRegex(html, r"request\('(tools/call|ui/message|ui/update-model-context|resources/read)'")

    def test_a_market_is_drawn_from_the_fields_the_search_returns(self):
        html = page()
        up = StubUpstream(standard_routes())
        found = tools.search_markets({'query': ''}, up)
        perp = next(m for m in found['markets'] if m['kind'] == 'perp')
        for field in ('symbol', 'name', 'venue', 'kind', 'asset_class', 'price', 'change_24h_pct', 'funding_apr_pct', 'open_interest_usd', 'volume_24h_usd', 'url'):
            self.assertIn(field, perp, field)
            self.assertIn('m.' + field, html, field)
        spot = shape.spot_market({'address': '0x' + 'A' * 40, 'chainId': 4663, 'sym': 'NVDA', 'name': 'NVIDIA', 'cls': 'equity'}, 'https://app.hence.markets')
        for field in ('price_note', 'contract'):
            self.assertIn(field, spot, field)
            self.assertIn('m.' + field, html, field)
        for field in ('markets', 'matches', 'prices_as_of_unix', 'coverage'):
            self.assertIn(field, found, field)
            self.assertIn('value.' + field, html, field)
        self.assertIn('value.incomplete', html)                                    # a list that was only half read says so

    def test_a_market_figure_that_was_not_read_is_n_a_and_the_card_places_nothing(self):
        html = page()
        self.assertIn("if (!isNum(v)) return 'n/a';", html)                        # open interest and volume
        self.assertIn("minimumFractionDigits: v >= 1 ? 2 : 0, maximumFractionDigits: v >= 100 ? 2 : v >= 1 ? 4 : 8 }) : 'n/a'", html)   # a price
        # a spot token's price is not passed on by this server: the card says where to see it, never a number
        self.assertTrue("var priced = m.kind === 'perp' || isNum(m.price);" in html)
        self.assertTrue("priced ? px(m.price) : 'in Hence'" in html)
        self.assertTrue("} else if (isNum(m.price)) {" in html)                      # a spot token is drawn with figures only when it has a price
        for field in ('liquidity_usd', 'market_cap_usd'):
            self.assertTrue('m.' + field in html, field)
        self.assertIn("'No price is shown here. Open the link for the live price.'", html)
        # not found in this search is not "Hence does not list it"
        self.assertIn("'No market matched this search.'", html)
        self.assertIn('if (value.coverage) card.appendChild', html)
        self.assertIn("'Nothing here places a trade: you trade in Hence.'", html)
        # a row is a link only when its address is a plain https one; the page still asks the host for nothing but to open it
        self.assertIn("if (!linkTo(row, m.url)) row = el('div', 'mrow');", html)
        self.assertEqual(len(re.findall(r"request\('ui/open-link'", html)), 1)

    def test_the_fields_it_draws_are_fields_the_tools_return(self):
        html = page()
        up = StubUpstream(standard_routes())
        one = tools.get_thesis({'thesis_id': T1}, up)
        many = tools.search_theses({'query': ''}, up)
        for field in ('title', 'author', 'topic', 'summary', 'holdings', 'performance', 'url', 'horizon_days', 'cash_weight_pct', 'invalidation', 'checkpoints'):
            self.assertIn(field, one, field)
            self.assertIn('t.' + field, html, field)
        for field in ('symbol', 'side', 'weight_pct'):
            self.assertIn(field, one['holdings'][0])
            self.assertIn('h.' + field, html)
        for field in ('return_pct', 'benchmark_return_pct', 'max_drawdown_pct', 'complete', 'period_days'):
            self.assertIn('p.' + field, html)
        self.assertIn('theses', many)
        self.assertIn('value.theses', html)


if __name__ == '__main__':
    unittest.main()


class TheLook(unittest.TestCase):
    """2026-11-15: the card is drawn in Hence's own look, and what moves on it changes nothing."""

    def test_it_wears_hences_mark_and_its_one_gradient(self):
        html = page()
        # the tri-dot mark, built node by node, in the colour of the text beside it (white on black, black on white): never tinted
        self.assertIn("[[9, 3], [3, 13], [15, 13]].forEach(function (at) {", html)
        self.assertIn("dot.setAttribute('fill', 'currentColor');", html)
        self.assertIn(".mark { width: 16px; height: 16px; flex: none; color: var(--text); }", html)
        # the iridescent gradient is the landing page's own four stops, as a text fill and nowhere else
        for stop in ('--g1: light-dark(#5b62d6, #9aa0f0);', '--g2: light-dark(#8a6fd0, #c9b6e8);', '--g3: light-dark(#c2743a, #f4c39a);', '--g4: light-dark(#b0865c, #f6e7d6);'):
            self.assertIn(stop, html)
        self.assertEqual(html.count('linear-gradient(95deg, var(--g1), var(--g2) 38%, var(--g3) 78%, var(--g4))'), 1)
        self.assertIn('-webkit-background-clip: text; background-clip: text;', html)
        # Hence's surfaces and money colours, and its white pill for the way in (black on a light chat)
        for token in ('--bg: light-dark(#ffffff, #0e0f12);', '--up: light-dark(#17794a, #5fcf91);', '--down: light-dark(#bb3a2d, #f08d83);',
                      '--cta-bg: light-dark(#0a0a0b, #f4f4f5);'):
            self.assertIn(token, html)
        self.assertNotRegex(html, r'#f0a868|#f6cba2')                                 # the old all-peach heading gradient is not Hence's highlight

    def test_what_moves_on_the_card_changes_nothing(self):
        html = page()
        # nothing on it can be typed into or switched: a number changed in a chat would change nothing in Hence
        self.assertNotRegex(html, r"el\('(input|select|textarea)'|createElement\('(input|select|textarea|form)'\)|contenteditable|<input|<select|<textarea|<form")
        # a price line is read by pointing at it or with the arrow keys; the readout is text, like everything else
        for said in ("wrap.addEventListener('pointermove', function (ev) {", "if (ev.key === 'ArrowLeft' || ev.key === 'ArrowRight') {", "box.appendChild(el('i', '', dates[at]));",
                     "read(at).forEach(function (r, k) { box.appendChild(el(k ? 'span' : 'b', '', r)); });"):
            self.assertIn(said, html, said)
        # an explanation opens where the person points and is made of text nodes inside this page
        for said in ("node.addEventListener('mouseenter', showTip); node.addEventListener('focus', showTip);", "if (tip.parentNode !== root) root.appendChild(tip);",
                     "said.forEach(function (x) { tip.appendChild(el('span', '', x)); });"):
            self.assertIn(said, html, said)
        # the buttons it has only change what THIS page shows (more of a summary, a shorter window of the same line)
        self.assertEqual(len(re.findall(r"el\('button'", html)), 2)
        self.assertEqual(html.count("b.type = 'button';") + html.count("more.type = 'button';"), 2)
        # time still running is said from the clock here and the time Hence gave; a time that could not be read says nothing
        self.assertIn("if (!isNum(at)) return null;", html)
        self.assertIn("if (s <= 0) return null;", html)

    def test_a_thesiss_chart_is_drawn_only_from_the_points_hence_sent(self):
        html = page()
        self.assertIn("if ('performance' in t) performance(card, t, t.card && typeof t.card === 'object' ? t.card.curve : null);", html)
        self.assertIn("curve.dates.length === curve.basket.length && curve.basket.length > 1 && curve.basket.every(isNum)", html)
        self.assertIn("var bench = Array.isArray(curve.benchmark) && curve.benchmark.length === curve.basket.length && curve.benchmark.every(isNum) ? curve.benchmark : null;", html)
        # no points: the three figures still show, and no line is made up
        up = StubUpstream(standard_routes())
        got = tools.get_thesis({'thesis_id': T1}, up)
        self.assertEqual(sorted(got['card']['curve']), ['basket', 'benchmark', 'dates'])
        self.assertEqual(shape.CARD_ONLY, 'card')

    def test_a_spot_tokens_ticker_reaches_the_card_only_when_it_is_one(self):
        base = {'proposal_id': 'pr_' + 'a' * 22, 'status': 'waiting', 'market_id': 'spot:4663:0x' + 'a' * 40, 'market': 'NVIDIA', 'side': 'long', 'size_usd': 50,
                'leverage': None, 'order_type': 'market', 'limit_price': None, 'url': 'https://app.hence.markets/#/proposal/pr_x', 'what_now': 'Waiting.',
                'kind': 'order', 'venue': 'spot'}
        self.assertEqual(shape.trade_proposal(dict(base, symbol='NVDA'))['symbol'], 'NVDA')
        for bad in ('', 'NV DA', '<b>', 'x' * 30, 7, None, ['NVDA']):
            self.assertNotIn('symbol', shape.trade_proposal(dict(base, symbol=bad)), repr(bad))
        perp = dict(base, market_id='perp:xyz:NVDA', venue='perp', leverage=3, symbol='NVDA')
        self.assertNotIn('symbol', shape.trade_proposal(perp))                           # a perpetual is named by its market id
        self.assertIn("p.venue === 'spot' ? (p.symbol || p.market) : p.market_id", page())
