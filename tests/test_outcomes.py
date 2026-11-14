"""Outcome (event) markets, and buying one spot token or one side of an outcome market.

What these pin:

  * an outcome market is listed one row per SIDE: each side is its own contract and its own
    market_id, and nothing here suggests it can be shorted;
  * a market that has settled is not listed as tradeable, and a list that could not be read
    is an error or an "incomplete", never "no such market";
  * a price that is not one (no quote, the venue's 0.5 placeholder, 1.0) is null, never a
    number an agent would repeat;
  * a purchase is passed on only as a purchase: one that arrives with a short side, a
    leverage or a limit is unread, and "sent" is never passed on as "filled".
"""
import json
import os
import unittest
from unittest import mock

from gateway import shape, tools
from gateway.upstream import NotFound, Unavailable

from .support import StubUpstream, fixture, standard_routes

BASE = 'https://app.hence.markets'
PID = 'pr_' + 'a' * 22
HISTORY = {'ok': True, 'points': [{'t': 1793000000 + i * 86400, 'p': 0.5 + i * 0.01} for i in range(12)]}


def routes(**over):
    r = standard_routes()
    r['/api/hip4/history'] = HISTORY
    r.update(over)
    return r


def proposal(**over):
    p = {'proposal_id': PID, 'status': 'waiting', 'kind': 'order', 'venue': 'spot', 'market_id': 'spot:4663:0x' + 'd' * 40, 'market': 'NVIDIA', 'side': 'long',
         'size_usd': 50.0, 'leverage': None, 'order_type': 'market', 'limit_price': None, 'note': None, 'created_at': '2026-11-14T11:00:00+00:00',
         'expires_at': '2026-11-15T11:00:00+00:00', 'url': BASE + '/#/proposal/' + PID, 'what_now': 'Waiting for the person.'}
    p.update(over)
    return p


class Search(unittest.TestCase):
    def setUp(self):
        tools._NAMES = {}

    def tearDown(self):
        tools._NAMES = None

    def test_an_outcome_market_is_one_row_per_side(self):
        up = StubUpstream(routes())
        out = tools.search_markets({'kind': 'outcome', 'limit': 25}, up)
        self.assertEqual([c for c in up.calls], [('/api/hip4/events', {})])                # only what can still be traded is asked for
        rows = {m['market_id']: m for m in out['markets']}
        self.assertEqual(sorted(rows), ['hip4:1212:0', 'hip4:1212:1', 'hip4:300:0', 'hip4:300:1', 'hip4:301:0', 'hip4:301:1'])
        yes = rows['hip4:1212:0']
        self.assertEqual((yes['kind'], yes['name'], yes['side'], yes['price'], yes['closes_at'], yes['other_side'], yes['symbol'], yes['answer']),
                         ('outcome', 'BTC above $100,000 on Dec 31 · Yes', 'Yes', 0.62, '2026-12-31T16:00:00+00:00', 'hip4:1212:1', 'BTC', None))
        self.assertEqual(yes['url'], BASE + '/#/terminal/o/o1212')
        self.assertEqual(rows['hip4:1212:1']['price'], 0.38)
        # an answer to a question is named with its question
        a = rows['hip4:300:1']
        self.assertEqual((a['name'], a['question'], a['answer'], a['side'], a['url']), ('Who wins the final? · Team A · No', 'Who wins the final?', 'Team A', 'No', BASE + '/#/terminal/o/q7'))
        # not prices: no quote, the venue's placeholder for an unquoted book, and a side at 1.0 nobody can buy
        self.assertEqual([rows[i]['price'] for i in ('hip4:300:1', 'hip4:301:0', 'hip4:301:1')], [None, None, None])
        for m in out['markets']:
            self.assertIn('pays 1 US dollar if this side is the result', m['pays'])
            self.assertNotIn('volume_24h_usd', m)                                          # none is known here: absent, not zero
        self.assertIn('never shorted', out['coverage'])

    def test_a_settled_market_is_not_listed(self):
        out = tools.search_markets({'kind': 'outcome', 'query': 'eth'}, StubUpstream(routes()))
        self.assertEqual((out['markets'], out['matches']), ([], 0))
        self.assertEqual(shape.outcome_markets(fixture('hip4_events')['events'][2], BASE), [])
        unsaid = {k: v for k, v in fixture('hip4_events')['events'][0].items() if k != 'settled'}
        self.assertEqual(shape.outcome_markets(unsaid, BASE), [])                           # a list that did not say "not settled" is not "open"

    def test_a_search_finds_them_by_their_words_beside_the_perps(self):
        out = tools.search_markets({'query': 'btc', 'limit': 25}, StubUpstream(routes()))
        kinds = {m['kind'] for m in out['markets']}
        self.assertIn('outcome', kinds)
        self.assertIn('perp', kinds)
        self.assertNotIn('incomplete', out)
        out = tools.search_markets({'query': 'final', 'kind': 'outcome'}, StubUpstream(routes()))
        self.assertEqual({m['question'] for m in out['markets']}, {'Who wins the final?'})

    def test_with_no_query_only_perps_are_read_and_the_answer_says_so(self):
        up = StubUpstream(routes())
        out = tools.search_markets({}, up)
        self.assertEqual([c[0] for c in up.calls], ['/api/screener/hl'])
        self.assertIn('kind "spot" or "outcome"', out['not_listed_here'])
        self.assertNotIn('not_listed_here', tools.search_markets({'query': 'btc'}, StubUpstream(routes())))

    def test_a_list_that_cannot_be_read_is_never_no_such_market(self):
        down = routes(**{'/api/hip4/events': Unavailable('Hence answered 503', 503)})
        with self.assertRaises(tools.ToolError) as ctx:
            tools.search_markets({'kind': 'outcome'}, StubUpstream(down))
        self.assertIn('No markets were read', str(ctx.exception))
        out = tools.search_markets({'query': 'btc'}, StubUpstream(down))
        self.assertIn('outcome could not be read', out['incomplete'])
        for wrong in ({'ok': True}, {'ok': False, 'events': []}, {'events': []}, [], 'x'):
            with self.assertRaises(tools.ToolError, msg=repr(wrong)):
                tools.search_markets({'kind': 'outcome'}, StubUpstream(routes(**{'/api/hip4/events': wrong})))

    def test_a_row_that_is_not_well_formed_is_skipped_not_guessed_at(self):
        ev = fixture('hip4_events')['events'][0]
        for broken in ({'outcome': '1212'}, {'outcome': True}, {'outcome': -1}, {'sides': ['Yes']}, {'sides': ['Yes', '']}, {'sides': None}):
            self.assertEqual(shape.outcome_markets(dict(ev, outcomes=[dict(ev['outcomes'][0], **broken)]), BASE), [], broken)
        for event in (dict(ev, id='x1'), dict(ev, id=None), dict(ev, title=''), None, 'x'):
            self.assertEqual(shape.outcome_markets(event, BASE), [])


class Asset(unittest.TestCase):
    def test_one_side_with_its_price_line(self):
        up = StubUpstream(routes())
        out = tools.get_asset({'id': 'hip4:1212:0'}, up)
        # settled events too, so a market that has closed reads as closed; and the SIDE's own coin for the line
        self.assertEqual(up.calls, [('/api/hip4/events', {'all': '1'}), ('/api/hip4/history', {'coin': '#12120', 'tf': '1d', 'hours': '720'})])
        self.assertEqual(out['asset'], {'id': 'hip4:1212:0', 'symbol': 'BTC above $100,000 on Dec 31 · Yes', 'class': 'outcome', 'tradeable_on_hence': True,
                                        'markets': [{'market_id': 'hip4:1212:0', 'venue': 'Hyperliquid outcome market', 'url': BASE + '/#/terminal/o/o1212'}],
                                        'reference_ticker': None})
        self.assertEqual(out['quote']['venues'], [{'market_id': 'hip4:1212:0', 'price': 0.62, 'other_side_price': 0.38}])
        self.assertEqual((len(out['history']['points']), out['history']['points'][0]['close'], out['history']['change_points']), (12, 0.5, 11.0))
        self.assertRegex(out['history']['points'][0]['date'], r'^\d{4}-\d{2}-\d{2}$')
        self.assertEqual(out['profile']['other_side'], {'market_id': 'hip4:1212:1', 'side': 'No'})
        self.assertEqual((out['profile']['status'], out['profile']['closes_at']), ('open', '2026-12-31T16:00:00+00:00'))
        self.assertEqual((out['unavailable'], out['not_applicable']), ([], []))
        self.assertNotIn('note', out)

    def test_a_price_line_that_could_not_be_read_is_unavailable_and_the_market_still_answers(self):
        for failed in (Unavailable('Hence answered 503', 503), {'ok': False}, {'ok': True, 'points': []}, {'ok': True, 'points': [{'t': 1, 'p': 0.5}]},
                       {'ok': True, 'points': [{'t': 1, 'p': 0}, {'t': 2, 'p': 1.2}]}):
            out = tools.get_asset({'id': 'hip4:1212:1'}, StubUpstream(routes(**{'/api/hip4/history': failed})))
            self.assertIsNone(out['history'], repr(failed))
            self.assertEqual(out['unavailable'], ['history'])
            self.assertEqual(out['quote']['venues'][0]['price'], 0.38)

    def test_company_research_does_not_exist_for_an_outcome_market(self):
        up = StubUpstream(routes())
        out = tools.get_asset({'id': 'hip4:300:0', 'sections': ['quote', 'analysts', 'insiders', 'news']}, up)
        self.assertEqual([c[0] for c in up.calls], ['/api/hip4/events'])                    # the price line was not asked for: not read
        self.assertEqual((out['not_applicable'], out['unavailable']), (['analysts', 'insiders', 'news'], []))
        self.assertEqual([out[k] for k in ('analysts', 'insiders', 'news')], [None, None, None])
        self.assertNotIn('history', out)
        self.assertEqual(out['asset']['symbol'], 'Who wins the final? · Team A · Yes')

    def test_a_market_that_has_settled_reads_as_closed_not_as_unknown(self):
        out = tools.get_asset({'id': 'hip4:900:0', 'sections': ['quote', 'profile']}, StubUpstream(routes()))
        self.assertIs(out['asset']['tradeable_on_hence'], False)
        self.assertEqual(out['asset']['markets'], [])
        self.assertIsNone(out['quote'])
        self.assertEqual(out['not_applicable'], ['quote'])
        self.assertEqual(out['profile']['status'], 'closed or settled')
        self.assertIn('can no longer be bought', out['note'])

    def test_what_is_not_listed_or_not_read_is_said(self):
        with self.assertRaises(tools.ToolError) as ctx:
            tools.get_asset({'id': 'hip4:555:0'}, StubUpstream(routes()))
        self.assertIn('no outcome market', str(ctx.exception))
        with self.assertRaises(tools.ToolError) as ctx:
            tools.get_asset({'id': 'hip4:1212:0'}, StubUpstream(routes(**{'/api/hip4/events': Unavailable('Hence answered 503', 503)})))
        self.assertIn('was not read', str(ctx.exception))
        for bad in ('hip4:1212:2', 'hip4:01212:0', 'hip4:1212', '#12120'):
            with self.assertRaises(tools.ToolError, msg=bad):
                tools.get_asset({'id': bad}, StubUpstream({}))


class Purchases(unittest.TestCase):
    def test_a_purchase_is_passed_on_with_its_venue(self):
        spot = shape.trade_proposal(proposal())
        self.assertEqual((spot['kind'], spot['venue'], spot['side'], spot['size_usd'], spot['leverage'], spot['placed']), ('order', 'spot', 'long', 50.0, None, False))
        out = shape.trade_proposal(proposal(venue='outcome', market_id='hip4:1212:0', market='BTC above $100,000 on Dec 31 · Yes', market_closes_at='2026-12-31T16:00:00+00:00'))
        self.assertEqual((out['venue'], out['market_closes_at']), ('outcome', '2026-12-31T16:00:00+00:00'))
        # an app from before purchases names no venue: a perpetual
        self.assertEqual(shape.trade_proposal(proposal(venue=None, market_id='perp:BTC', leverage=3))['venue'], 'perp')
        self.assertNotIn('market_closes_at', spot)

    def test_a_purchase_with_anything_a_purchase_does_not_have_is_unread(self):
        for venue in ('spot', 'outcome'):
            for over in ({'side': 'short'}, {'leverage': 1}, {'leverage': 3}, {'order_type': 'limit'}, {'limit_price': 1.0}):
                self.assertIsNone(shape.trade_proposal(proposal(venue=venue, **over)), (venue, over))
        for venue in ('withdrawal', '', 7, 'solana'):
            self.assertIsNone(shape.trade_proposal(proposal(venue=venue)), venue)

    def test_sent_is_never_passed_on_as_filled(self):
        sent = shape.trade_proposal(proposal(status='placed', order={'state': 'pending', 'size_usd': 50.0, 'confirmed_by_venue': False, 'placed_at': 'x'}))
        self.assertEqual((sent['order']['state'], sent['order']['confirmed_by_venue']), ('pending', False))
        self.assertNotIn('placed', sent)
        self.assertIsNone(shape.trade_proposal(proposal(status='placed', order={'state': 'sent', 'confirmed_by_venue': False})))

    def test_what_the_agent_is_told_to_do_next(self):
        answer = {'ok': True, 'proposed': True, 'already_proposed': False, 'problems': [], 'warnings': ['An outcome contract pays 1 US dollar if this side is the result and nothing if it is not: the whole amount can be lost.'],
                  'proposal': proposal(venue='outcome', market_id='hip4:1212:0'), 'hence_fee': {'max_percent': None, 'note': 'Any Hence fee is shown in the ticket.'}}
        out = shape.trade_propose(answer)
        self.assertIn('PREPARED, not made', out['next'])
        self.assertIn('state "pending"', out['next'])
        self.assertIn('Do not say it was bought', out['next'])
        self.assertEqual(len(out['warnings']), 1)
        perp = shape.trade_propose(dict(answer, proposal=proposal(venue='perp', market_id='perp:BTC', leverage=3)))
        self.assertIn('PREPARED, not placed', perp['next'])

    def test_the_tool_says_what_can_be_proposed_and_how_a_purchase_differs(self):
        props = tools.PROPOSE_TRADE['properties']
        for needle in ('spot:4663:0x', 'hip4:1212:0', 'Polymarket cannot be proposed'):
            self.assertIn(needle, props['market_id']['description'])
        self.assertIn('only "long"', props['side']['description'])
        self.assertIn('the US dollars to spend', props['size_usd']['description'])
        for only in ('leverage', 'order_type', 'limit_price'):
            self.assertTrue(props[only]['description'].startswith('Perpetuals only.'), only)
        self.assertEqual(tools.SEARCH_MARKETS['properties']['kind']['enum'], ['all', 'perp', 'spot', 'outcome'])



class Shown(unittest.TestCase):
    """The card, the CLI and the skill say the same thing the tools do."""

    def setUp(self):
        with open(os.path.join(os.path.dirname(__file__), '..', 'data', 'thesis-card.html'), encoding='utf-8') as f:
            self.card = f.read()

    def test_the_card_draws_a_purchase_as_a_purchase(self):
        for needle in ("function purchase(p) { return p.kind !== 'basket' && (p.venue === 'spot' || p.venue === 'outcome'); }",
                       "'Amount to spend'", "if (p.order.state === 'pending') return p.venue === 'spot' ? 'You sent this purchase. Hence is confirming it on the chain.'",
                       "Nothing is bought until you review it and sign it there."):
            self.assertIn(needle, self.card)
        # a probability is cents and a percentage, or n/a: never a dollar price and never 0
        self.assertIn("function chance(v) { return isNum(v) && v > 0 && v < 1 ? Math.round(v * 100) + '¢' : 'n/a'; }", self.card)

    def test_the_card_draws_an_outcome_side_with_what_it_pays_and_its_other_side(self):
        for needle in ("if (m.kind === 'outcome') {", "It can only be bought: to take the other side, buy ", "oquote.appendChild(el('span', 'na', implied(m.price)));", "'no quote right now'",
                       "var prof = value.profile, outcome = a['class'] === 'outcome';", "hist.change_points"):
            self.assertIn(needle, self.card)

    def test_the_command_line_takes_buy_and_lists_outcome_markets(self):
        from hence_cli import render
        out = render.markets({'markets': shape.outcome_markets(fixture('hip4_events')['events'][0], BASE)})
        self.assertIn('hip4:1212:0', out)
        self.assertIn('0.62 (62%)', out)
        self.assertIn('BTC above $100,000 on Dec 31 · Yes', out)
        self.assertEqual(render._chance(None), render.NA)
        self.assertEqual(render._chance(0), render.NA)                                    # no quote is n/a, never 0
        shown = render.trade({'proposal': shape.trade_proposal(proposal(venue='outcome', market_id='hip4:1212:0', market='BTC above $100,000 on Dec 31 · Yes',
                                                                         market_closes_at='2026-12-31T16:00:00+00:00'))})
        self.assertIn('Buy BTC above $100,000 on Dec 31 · Yes', shown)
        self.assertIn('market closes: 2026-12-31T16:00:00+00:00', shown)
        self.assertNotIn('Long', shown)
        self.assertNotIn('market order', shown)
        sent = render.trade({'proposal': shape.trade_proposal(proposal(status='placed', order={'state': 'pending', 'size_usd': 50.0, 'confirmed_by_venue': False}))})
        self.assertIn('order: sent, $50.00, not confirmed yet', sent)
        bought = render.trade({'proposal': shape.trade_proposal(proposal(status='placed', order={'state': 'filled', 'size_usd': 50.0, 'confirmed_by_venue': True}))})
        self.assertIn('order: filled, $50.00, confirmed on the chain', bought)
        with open(os.path.join(os.path.dirname(__file__), '..', 'hence_cli', 'main.py'), encoding='utf-8') as f:
            main = f.read()
        self.assertIn("propose.add_argument('side', choices=['long', 'short', 'buy']", main)
        self.assertIn("'side': 'long' if purchase else ns.side", main)
        self.assertIn("markets.add_argument('--kind', choices=['all', 'perp', 'spot', 'outcome'])", main)

    def test_the_skill_says_a_purchase_is_never_a_short_and_pending_is_not_bought(self):
        with open(os.path.join(os.path.dirname(__file__), '..', 'skills', 'hence', 'SKILL.md'), encoding='utf-8') as f:
            skill = f.read()
        for needle in ('bought, never shorted', 'pays 1 dollar if its side is the result and nothing if not', '"pending" is not bought', 'one row per side'):
            self.assertIn(needle, skill)


if __name__ == '__main__':
    unittest.main()
