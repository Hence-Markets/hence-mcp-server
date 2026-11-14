"""Research on one asset: get_asset, `hence asset`, and the card's price line.

What these pin:
  * the tool reads one address of the app and passes on what it was given by allowlist: a
    section that is null must be accounted for by the app's own `unavailable` or
    `not_applicable` list, or the whole answer is refused as unreadable;
  * the request is built from validated parts only (an id and section names), in one order, so
    the same question is always the same address;
  * the card draws the price line from the closes it was given and nothing else.
"""
import io
import json
import os
import unittest

from gateway import cards, shape, tools
from gateway.upstream import NotFound, Unavailable
from hence_cli import main, render

from .support import StubUpstream

ROUTE = '/api/research/asset'
HERE = os.path.dirname(os.path.abspath(__file__))
BASE = 'https://app.hence.markets'
POINTS = [{'date': '2026-08-%02d' % (i + 1), 'close': 100.0 + i} for i in range(30)]


def answer(**over):
    a = {
        'asset': {'id': 'perp:xyz:NVDA', 'symbol': 'NVDA', 'class': 'equity', 'tradeable_on_hence': True,
                  'markets': [{'market_id': 'perp:xyz:NVDA', 'venue': 'trade.xyz perp (equity/commodity/index/FX)'}], 'reference_ticker': 'NVDA',
                  'internal_note': 'must not leave'},
        'sections_asked': ['quote', 'history', 'profile'],
        'quote': {'venues': [{'market_id': 'perp:xyz:NVDA', 'price': 140.5, 'change_24h_pct': 2.0, 'funding_apr_pct': 3.1, 'open_interest_usd': 4.0e7,
                              'volume_24h_usd': 9.0e7, 'source': 'live venue price'}],
                  'reference': {'price': 140.4, 'change_pct': 1.9, 'year_high': 153.1, 'year_low': 75.6, 'source': 'reference quote, may be delayed'}},
        'history': {'source': 'venue daily closes', 'from': '2026-08-01', 'to': '2026-08-30', 'points': POINTS, 'change_pct': 29.0, 'high': 129.0, 'low': 100.0},
        'profile': {'name': 'NVIDIA Corporation', 'sector': 'Technology', 'industry': 'Semiconductors', 'exchange': 'NASDAQ'},
        'unavailable': [], 'not_applicable': [], 'debug': {'vendor_calls': 2},
    }
    a.update(over)
    return a


class Tool(unittest.TestCase):
    def test_one_read_in_one_form(self):
        up = StubUpstream({ROUTE: answer()})
        got = tools.get_asset({'id': 'perp:xyz:NVDA'}, up)
        self.assertEqual(up.calls, [(ROUTE, {'id': 'perp:xyz:NVDA', 'sections': ''})])
        self.assertEqual(got['asset'], {'id': 'perp:xyz:NVDA', 'symbol': 'NVDA', 'class': 'equity', 'tradeable_on_hence': True, 'reference_ticker': 'NVDA',
                                        'markets': [{'market_id': 'perp:xyz:NVDA', 'venue': 'trade.xyz perp (equity/commodity/index/FX)',
                                                     'url': BASE + '/m/xyz/xyz%3ANVDA'}]})
        self.assertEqual(got['quote']['venues'][0]['price'], 140.5)
        self.assertEqual(len(got['history']['points']), 30)
        for hidden in ('debug', 'sections_asked'):
            self.assertNotIn(hidden, got)
        self.assertNotIn('must not leave', json.dumps(got))
        for said in ('could not be read', 'does not exist for this kind of asset', 'written by third parties', 'not advice'):
            self.assertIn(said, got['how_to_read'], said)
        # the sections go in the answer's own order whatever order they were asked in
        up = StubUpstream({ROUTE: answer(sections_asked=['quote', 'news'], news={'headlines': []})})
        tools.get_asset({'id': 'NVDA', 'sections': ['news', 'quote']}, up)
        self.assertEqual(up.calls, [(ROUTE, {'id': 'NVDA', 'sections': 'quote,news'})])

    def test_a_bad_request_reads_nothing(self):
        up = StubUpstream({ROUTE: answer()})
        for args in ({'id': 'spot:4663:0xabc'}, {'id': 'NVDA&from=2000-01-01'}, {'id': '../me'}, {'id': ''}, {'id': 'NVDA', 'sections': ['everything']},
                     {'id': 'NVDA', 'sections': 'quote'}, {'id': 'NVDA', 'limit': 500}, {'id': 'NVDA', 'from': '2000-01-01'}):
            with self.assertRaises(tools.ToolError, msg=repr(args)):
                tools.get_asset(args, up)
        self.assertEqual(up.calls, [])
        self.assertIs(tools.GET_ASSET['additionalProperties'], False)
        self.assertEqual(sorted(tools.GET_ASSET['properties']), ['id', 'sections'])           # no date, limit or page a caller could vary

    def test_a_null_section_must_be_accounted_for(self):
        ok = answer(sections_asked=['quote', 'insiders', 'analysis'], insiders=None, analysis=None, unavailable=['analysis'], not_applicable=['insiders'])
        got = tools.get_asset({'id': 'perp:BTC', 'sections': ['quote', 'insiders', 'analysis']}, StubUpstream({ROUTE: ok}))
        self.assertEqual((got['insiders'], got['analysis'], got['unavailable'], got['not_applicable']), (None, None, ['analysis'], ['insiders']))
        # null, and neither list says why: the answer cannot be trusted
        for broken in (answer(sections_asked=['quote', 'insiders'], insiders=None), answer(profile='n/a'), answer(asset={'symbol': ''}), answer(asset=None),
                       {k: v for k, v in answer().items() if k != 'unavailable'}, answer(unavailable='none'), 'text', None):
            with self.assertRaises(tools.ToolError, msg=repr(broken)[:80]) as caught:
                tools.get_asset({'id': 'NVDA'}, StubUpstream({ROUTE: broken}))
            self.assertIn('could not be read', str(caught.exception))
        # a section the app sent but that was not asked for is not passed on
        extra = tools.get_asset({'id': 'NVDA'}, StubUpstream({ROUTE: answer(insiders={'transactions': []})}))
        self.assertNotIn('insiders', extra)

    def test_nothing_found_and_not_reachable_are_said_differently(self):
        with self.assertRaises(tools.ToolError) as caught:
            tools.get_asset({'id': 'ZZZZ'}, StubUpstream({ROUTE: NotFound(ROUTE)}))
        self.assertIn('no market and no research data for "ZZZZ"', str(caught.exception))
        with self.assertRaises(tools.ToolError) as caught:
            tools.get_asset({'id': 'NVDA'}, StubUpstream({ROUTE: Unavailable('Hence answered 503', 503)}))
        self.assertIn('was not read; try again shortly', str(caught.exception))

    def test_research_only_is_said(self):
        research = answer(asset={'id': 'TSLA', 'symbol': 'TSLA', 'class': 'equity', 'tradeable_on_hence': False, 'markets': [], 'reference_ticker': 'TSLA'})
        got = tools.get_asset({'id': 'TSLA'}, StubUpstream({ROUTE: research}))
        self.assertEqual((got['asset']['markets'], got['asset']['tradeable_on_hence']), ([], False))
        self.assertIn('not traded on Hence', got['note'])
        unknown = answer(asset={'id': 'NVDA', 'symbol': 'NVDA', 'class': 'equity', 'tradeable_on_hence': None, 'markets': []}, unavailable=['venues'])
        got = tools.get_asset({'id': 'NVDA'}, StubUpstream({ROUTE: unknown}))
        self.assertEqual((got['asset']['tradeable_on_hence'], got['unavailable']), (None, ['venues']))    # not known is not "no"
        self.assertNotIn('note', got)

    def test_it_is_a_public_read_with_a_card(self):
        tool = next(t for t in tools.TOOLS if t['name'] == 'get_asset')
        self.assertNotIn('scope', tool)
        self.assertIs(tool['annotations']['readOnlyHint'], True)
        self.assertIn('get_asset', cards.TOOLS)
        for said in ('analyst ratings', 'insider trades', 'research only', 'may be delayed', 'was not read: say so'):
            self.assertIn(said, tool['description'], said)


class Card(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(HERE, '..', 'data', 'thesis-card.html'), encoding='utf-8') as f:
            self.html = f.read()

    def has(self, needle):
        self.assertTrue(needle in self.html, needle)

    def test_the_line_is_drawn_from_the_closes_and_nothing_else(self):
        self.has("if (closes.length < 2) return null;")                                  # one point is not a line
        self.has("return p && isNum(p.close) ? p.close : null;")                         # a bad point is dropped, never read as 0
        self.has("var y = span > 0 ? h - 4 - (c - min) / span * (h - 8) : h / 2;")       # a flat series is a flat line, not a divide by zero
        self.has("document.createElementNS(ns, 'polyline')")
        self.assertNotRegex(self.html, r'innerHTML|insertAdjacentHTML|<svg')              # built node by node, no markup from data
        self.has("else if (value.asset && typeof value.asset === 'object') show(asset(value));")

    def test_the_card_reads_fields_the_tool_returns(self):
        got = tools.get_asset({'id': 'perp:xyz:NVDA'}, StubUpstream({ROUTE: answer()}))
        for field in ('symbol', 'markets', 'tradeable_on_hence'):
            self.assertIn(field, got['asset'])
            self.has('a.' + field)
        for field in ('quote', 'history', 'profile', 'unavailable'):
            self.assertIn(field, got)
            self.has('value.' + field)
        for needle in ('hist.points', 'hist.change_pct', 'live.price', 'ref.price', 'an.price_target.consensus', 'earn.next_date', 'news.headlines',
                       "'A reference quote, which may be delayed.'", "'Could not be read: '", 'Hence does not list a market for this asset: research only. ',
                       "'Headlines are written by their publishers, not by Hence.'"):
            self.has(needle)


class Command(unittest.TestCase):
    def cli(self, value, *argv):
        def sender(url, method, headers, body, timeout):
            params = json.loads(body)['params']
            self.sent = {'name': params['name'], 'arguments': params['arguments']}
            return 200, json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': {'content': [{'type': 'text', 'text': json.dumps(value)}],
                                                                           'structuredContent': value, 'isError': False}}).encode(), {}
        out, err = io.StringIO(), io.StringIO()
        code = main.run(list(argv), out=out, err=err, sender=sender)
        return code, out.getvalue(), err.getvalue()

    def test_the_command_asks_and_prints_what_was_and_was_not_read(self):
        full = answer(sections_asked=list(shape.ASSET_SECTIONS), unavailable=['analysis'], not_applicable=[],
                      analysts={'price_target': {'consensus': 170.0, 'low': 100.0, 'high': 220.0}, 'ratings': {'consensus': 'Buy'},
                                'recent_changes': [{'date': '2026-11-10', 'firm': 'Example Securities', 'from': 'Hold', 'to': 'Buy'}]},
                      insiders={'transactions': [{'traded': '2026-11-03', 'who': 'DOE JANE', 'direction': 'disposed', 'shares': 1000, 'value_usd': 141000.0}]},
                      earnings={'next_date': '2026-11-19', 'reported': [{'date': '2026-08-20', 'eps': 0.81, 'eps_estimate': 0.78}]},
                      news={'headlines': [{'title': 'Chipmaker reports record quarter', 'publisher': 'Example Wire', 'published': '2026-11-13 21:05:00'}]},
                      fundamentals={'pe': 55.2, 'gross_margin': 0.75, 'net_margin': 0.52, 'ev_to_sales': 28.0}, analysis=None)
        value = shape.asset_research(full, BASE)
        code, out, err = self.cli(value, 'asset', 'perp:xyz:NVDA', '--all')
        self.assertEqual(code, 0, err)
        self.assertEqual(self.sent, {'name': 'get_asset', 'arguments': {'id': 'perp:xyz:NVDA', 'sections': list(shape.ASSET_SECTIONS)}})
        for said in ('NVDA  (equity)', 'NVIDIA Corporation · Technology · Semiconductors · NASDAQ', 'perp:xyz:NVDA', '2026-08-01 to 2026-08-30: +29.00%',
                     'analysts: Buy; target 170.00', 'Example Securities: Hold -> Buy', 'earnings: next 2026-11-19', 'DOE JANE', '$141,000.00',
                     'P/E 55.20 · gross margin 75.00%', 'Chipmaker reports record quarter', 'could not be read: analysis', 'trade in Hence: ' + BASE + '/m/xyz/xyz%3ANVDA'):
            self.assertIn(said, out, said)
        code, out, err = self.cli(shape.asset_research(answer(), BASE), 'asset', 'NVDA', '--sections', 'quote,news')
        self.assertEqual(self.sent['arguments'], {'id': 'NVDA', 'sections': ['quote', 'news']})
        code, out, err = self.cli(shape.asset_research(answer(), BASE), 'asset', 'NVDA')
        self.assertEqual(self.sent['arguments'], {'id': 'NVDA'})                         # nothing asked: the server's own default three
        self.assertIn('get_asset', render.VIEWS)


if __name__ == '__main__':
    unittest.main()
