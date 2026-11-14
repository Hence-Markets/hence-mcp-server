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

    def test_the_page_is_given_no_network_at_all(self):
        _, out = self.run_(modern('resources/read', {'uri': cards.CARD}))
        csp = out['result']['contents'][0]['_meta']['ui']['csp']
        self.assertEqual(csp, {'connectDomains': [], 'resourceDomains': [], 'frameDomains': [], 'baseUriDomains': []})
        self.assertNotIn('permissions', out['result']['contents'][0]['_meta']['ui'])          # no camera, microphone, location, clipboard

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
        with mock.patch.dict(os.environ, dict(ON, HENCE_MCP_RESEARCH='1', HENCE_MCP_TRADES='1')):
            _, out = self.run_(modern('tools/list'))
        listed = {t['name']: t.get('_meta') for t in out['result']['tools']}
        with_card = sorted(name for name, meta in listed.items() if meta)
        self.assertEqual(with_card, sorted(cards.TOOLS))
        # theses, and a trade PREPARED for the person: its card says "not placed" and opens Hence (tests/test_trades.py)
        self.assertEqual(with_card, ['get_featured_theses', 'get_my_thesis', 'get_thesis', 'get_trade_proposals', 'list_my_theses', 'propose_basket',
                                     'propose_trade', 'search_markets', 'search_theses'])
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
    def test_it_loads_and_calls_nothing(self):
        html = page()
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
        self.assertIn("m.kind === 'perp' ? px(m.price) : 'in Hence'", html)
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
