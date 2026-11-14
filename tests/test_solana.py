"""Stocks and tokens on Solana: listed in search from Hence's own catalog, and bought through
propose_trade like any spot token.

What these pin:

  * one row per STOCK, named by a token Hence offers that is not halted: the Hence ticket buys
    whichever of a stock's tokens quotes best, so the stock is what an agent names;
  * a mint is case-sensitive and is passed on exactly as read;
  * a Hence that does not serve Solana at all is not a failed read; a Solana read that failed
    on a Hence that does serve it is said, never "no such market";
  * no price: none is passed on, and null is never a number.
"""
import os
import unittest

from gateway import shape, tools
from gateway.upstream import Unavailable

from .support import StubUpstream, fixture, standard_routes

BASE = 'https://app.hence.markets'
TSLAX, TSLA_ONDO = 'XsDoVfqeBukxuZHWhdvWHBhgEHjGNst4MLodqsJHzoB', 'ndo7YJ3mE8vRkL2pQwXzTn4cBh5dGs6fKa9uVtMiN1x'
SPY_ONDO = 'k18WJUULWheRkSpSquYGdNNmtuE2Vbw1hpuUi92ondo'
DOWN = Unavailable('Hence answered 503', 503)


def routes(**over):
    r = standard_routes()
    r.update(over)
    return r


class Search(unittest.TestCase):
    def setUp(self):
        tools._NAMES = {}

    def tearDown(self):
        tools._NAMES = None

    def solana(self, out):
        return {m['symbol']: m for m in out['markets'] if m.get('venue') == 'Solana'}

    def test_a_stock_is_one_row_named_by_a_token_hence_offers(self):
        out = tools.search_markets({'kind': 'spot', 'limit': 25}, StubUpstream(routes()))
        rows = self.solana(out)
        self.assertEqual(sorted(rows), ['SPY', 'TSLA'])                              # halted everywhere, and not offered: not listed
        tsla = rows['TSLA']
        self.assertEqual((tsla['market_id'], tsla['kind'], tsla['name'], tsla['asset_class'], tsla['contract'], tsla['issuers']),
                         ('spot:solana:' + TSLAX, 'spot', 'Tesla, Inc.', 'CS', TSLAX, ['Ondo', 'xStocks']))
        self.assertEqual(tsla['url'], BASE + '/#/terminal/s/solana/' + TSLAX)
        self.assertIsNone(tsla['price'])
        self.assertIn('whichever quotes best', tsla['price_note'])
        for absent in ('change_24h_pct', 'volume_24h_usd', 'liquidity_usd'):
            self.assertNotIn(absent, tsla)                                           # none is read: absent, not zero
        # its first token is halted: the row is named by the one that can be bought
        self.assertEqual((rows['SPY']['market_id'], rows['SPY']['issuers']), ('spot:solana:' + SPY_ONDO, ['Ondo']))
        self.assertIn('one row per stock', out['coverage'])

    def test_a_mint_is_passed_on_exactly_as_read(self):
        asset = fixture('solana_assets')['assets'][0]
        self.assertEqual(shape.solana_market(asset, BASE)['contract'], TSLAX)
        self.assertNotEqual(TSLAX, TSLAX.lower())
        for bad in ('0x' + 'a' * 40, TSLAX[:20], TSLAX + '0', TSLAX.replace('X', 'O'), None, 7):
            only = dict(asset, variants=[dict(asset['variants'][0], mint=bad)])
            self.assertIsNone(shape.solana_market(only, BASE), repr(bad))
        for broken in (None, 'x', {}, dict(asset, underlying=''), dict(asset, variants=None), dict(asset, variants=['x'])):
            self.assertIsNone(shape.solana_market(broken, BASE))

    def test_a_search_finds_a_stock_on_both_spot_chains_and_as_a_perpetual(self):
        out = tools.search_markets({'query': 'tsla', 'limit': 25}, StubUpstream(routes()))
        self.assertIn('spot:solana:' + TSLAX, [m['market_id'] for m in out['markets']])
        self.assertNotIn('incomplete', out)
        up = StubUpstream(routes())
        tools.search_markets({}, up)                                                 # no query: only the perps are read
        self.assertEqual([c[0] for c in up.calls], ['/api/screener/hl'])

    def test_a_hence_that_does_not_serve_solana_is_not_a_failed_read(self):
        off = routes(**{'/api/solana/assets': DOWN, '/api/config': {'solanaSpot': False, 'solanaResearch': False}})
        out = tools.search_markets({'query': 'nvda'}, StubUpstream(off))
        self.assertNotIn('incomplete', out)
        self.assertTrue(out['markets'])
        self.assertEqual(self.solana(out), {})

    def test_a_solana_read_that_failed_is_said(self):
        for config in ({'solanaSpot': True, 'solanaResearch': False}, {'solanaSpot': False, 'solanaResearch': True}, DOWN, {}, 'x'):
            out = tools.search_markets({'query': 'nvda'}, StubUpstream(routes(**{'/api/solana/assets': DOWN, '/api/config': config})))
            self.assertIn('solana', out['incomplete'], repr(config))                   # a config that could not be read is not "off"
        for wrong in ({}, {'assets': 'x'}, [], 'x'):
            out = tools.search_markets({'query': 'nvda'}, StubUpstream(routes(**{'/api/solana/assets': wrong})))
            self.assertIn('solana', out['incomplete'], repr(wrong))

    def test_every_list_that_was_read_failing_is_an_error(self):
        every = {'/api/screener/spot': DOWN, '/api/solana/assets': DOWN, '/api/config': {'solanaSpot': True, 'solanaResearch': True}}
        with self.assertRaises(tools.ToolError) as ctx:
            tools.search_markets({'kind': 'spot'}, StubUpstream(every))
        self.assertIn('No markets were read', str(ctx.exception))
        # Robinhood Chain down, Solana up: a partial answer that says so, with the Solana rows
        out = tools.search_markets({'kind': 'spot', 'query': 'tsla'}, StubUpstream(routes(**{'/api/screener/spot': DOWN})))
        self.assertIn('spot could not be read', out['incomplete'])
        self.assertEqual(sorted(self.solana(out)), ['TSLA'])


class Proposing(unittest.TestCase):
    def test_the_tool_takes_a_solana_market_and_passes_the_purchase_on(self):
        self.assertIn('spot:solana:<mint>', tools.PROPOSE_TRADE['properties']['market_id']['description'])
        self.assertIn('case-sensitive', tools.PROPOSE_TRADE['properties']['market_id']['description'])
        pid = 'pr_' + 'a' * 22
        p = shape.trade_proposal({'proposal_id': pid, 'status': 'waiting', 'kind': 'order', 'venue': 'spot', 'market_id': 'spot:solana:' + TSLAX, 'market': 'Tesla, Inc.',
                                  'side': 'long', 'size_usd': 50.0, 'leverage': None, 'order_type': 'market', 'limit_price': None, 'note': None,
                                  'created_at': '2026-11-14T11:00:00+00:00', 'expires_at': '2026-11-15T11:00:00+00:00', 'url': BASE + '/#/proposal/' + pid,
                                  'what_now': 'Waiting for the person.'})
        self.assertEqual((p['venue'], p['market_id'], p['market']), ('spot', 'spot:solana:' + TSLAX, 'Tesla, Inc.'))

    def test_the_card_and_the_skill_say_where_and_how(self):
        here = os.path.dirname(__file__)
        with open(os.path.join(here, '..', 'data', 'thesis-card.html'), encoding='utf-8') as f:
            card = f.read()
        self.assertIn("String(p.market_id || '').indexOf('spot:solana:') === 0 ? 'Solana' : 'Robinhood Chain'", card)
        with open(os.path.join(here, '..', 'skills', 'hence', 'SKILL.md'), encoding='utf-8') as f:
            skill = f.read()
        for needle in ('whichever of its tokens quotes best', 'a Solana mint is case-sensitive'):
            self.assertIn(needle, skill)


if __name__ == '__main__':
    unittest.main()
