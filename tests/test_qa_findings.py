"""Findings from a QA pass against a local run, each pinned where it was fixed.

What these pin:
  * an outcome side's price comes from its own order book, and only when the book is quoted
    closely: the middle of an empty book (bid 0.00126, ask 0.99611) is not a probability, and a
    book that could not be read is said, never replaced by the event list's figure;
  * in a search, identical listed mids near one half across different markets are null;
  * a search for "bitcoin" finds BTC; a core perpetual's common name ranks with its ticker;
  * an account with no wallet is told so, never that its account "could not be read";
  * a signed-in failure names what was not read or done for that tool;
  * a section that was not asked for is absent, and one that is empty is null and named;
  * the CLI sends a 0 it was given; the list that failed in a spot search is named; a spot id
    refused for its letter case says so.
"""
import argparse
import json
import os
import unittest
from unittest import mock

from gateway import appauth, protocol, shape, tools
from gateway.upstream import Unavailable
from hence_cli import main, render

from .support import StubUpstream, standard_routes
from .test_account import GRANT, ON, TOKEN, modern
from .test_asset import ROUTE, answer
from .test_my_account import PORTFOLIO

BASE = 'https://app.hence.markets'
HERE = os.path.dirname(os.path.abspath(__file__))


def strikes(mid=0.498685, n=6, settled=False):
    """A question of BTC strikes whose books nobody quotes: every one listed at the same mid."""
    return {'id': 'q30', 'kind': 'question', 'title': 'BTC price at Nov 16', 'expiry': '2026-11-16T08:00:00+00:00', 'settled': settled,
            'underlying': 'BTC', 'outcomes': [{'outcome': 30120 + i, 'label': 'Above $%d,000' % (70 + i), 'sides': ['Yes', 'No'],
                                               'yes': mid, 'no': round(1 - mid, 6)} for i in range(n)]}


def book(bid, ask, coin='#301260'):
    return {'ok': True, 'coin': coin, 'bid': bid, 'ask': ask}


def routes(events, quotes):
    r = standard_routes()
    r['/api/hip4/events'] = {'ok': True, 'events': events}
    r['/api/hip4/history'] = {'ok': True, 'points': []}

    def quote(query):
        answer = quotes[query['coin']]
        if isinstance(answer, BaseException):
            raise answer
        return answer
    r['/api/hip4/quote'] = quote
    return r


class OutcomeQuote(unittest.TestCase):
    """A. get_asset for one side of an outcome market."""

    def asset(self, quotes, sections=('quote',)):
        return tools.get_asset({'id': 'hip4:30126:0', 'sections': list(sections)}, StubUpstream(routes([strikes(n=7)], quotes)))

    def test_the_rule_for_a_usable_quote_is_one_pure_function(self):
        self.assertEqual(shape.QUOTE_MAX_SPREAD, 0.10)
        self.assertEqual(shape.quoted_mid(0.61, 0.63), 0.62)
        self.assertEqual(shape.quoted_mid(0.40, 0.50), 0.45)                     # exactly the widest spread allowed
        for bid, ask in ((0.00126, 0.99611), (0.40, 0.51), (None, 0.3), (0.3, None), (0.5, 0.5), (0.6, 0.5), (0, 0.05), (0.95, 1), (True, 0.5)):
            self.assertIsNone(shape.quoted_mid(bid, ask), (bid, ask))

    def test_the_middle_of_an_empty_book_is_not_a_price(self):
        out = self.asset({'#301260': book(0.00126, 0.99611), '#301261': book(0.00389, 0.99874, '#301261')})
        venue = out['quote']['venues'][0]
        self.assertEqual((venue['price'], venue['bid'], venue['ask'], venue['other_side_price']), (None, 0.00126, 0.99611, None))
        self.assertIn('Nobody is quoting this market closely (best bid 0.00126, best ask 0.99611)', out['quote']['note'])
        self.assertIn('no reliable price', out['quote']['note'])
        self.assertEqual(out['unavailable'], [])                                  # the book WAS read: this is what it says
        self.assertNotIn('0.498685', json.dumps(out))                             # the list's figure is never the price

    def test_a_quoted_book_gives_its_middle_and_carries_bid_and_ask(self):
        out = self.asset({'#301260': book(0.30, 0.34), '#301261': book(0.66, 0.70, '#301261')})
        self.assertEqual(out['quote']['venues'][0], {'market_id': 'hip4:30126:0', 'price': 0.32, 'bid': 0.30, 'ask': 0.34, 'other_side_price': 0.68})
        self.assertNotIn('note', out['quote'])
        self.assertIn('at most 0.1 apart', out['quote']['price_note'])

    def test_a_book_that_could_not_be_read_is_said_and_never_replaced_by_the_list(self):
        for failed in (Unavailable('Hence answered 502', 502), {'ok': False}, book(0, 0.5), book(0.2, 1.4), book('0.2', 0.3), book(0.2, 0.3, '#999')):
            out = self.asset({'#301260': failed, '#301261': book(0.66, 0.70, '#301261')})
            self.assertIsNone(out['quote'], repr(failed))
            self.assertEqual(out['unavailable'], ['quote'])
        # the other side's book failing: its price is not given, and the answer says why
        out = self.asset({'#301260': book(0.30, 0.34), '#301261': Unavailable('Hence answered 502', 502)})
        self.assertEqual((out['quote']['venues'][0]['price'], out['quote']['venues'][0]['other_side_price']), (0.32, None))
        self.assertIn('other side\'s order book could not be read', out['quote']['note'])

    def test_a_book_is_read_briefly_cached_by_the_sides_own_coin(self):
        up = StubUpstream(routes([strikes(n=7)], {'#301260': book(0.3, 0.34), '#301261': book(0.66, 0.7, '#301261')}))
        calls = []
        real = up.get_json
        up.get_json = lambda path, query=None, ttl=0: (calls.append((path, query, ttl)), real(path, query, ttl))[1]
        tools.get_asset({'id': 'hip4:30126:0', 'sections': ['quote']}, up)
        self.assertEqual([c for c in calls if c[0] == '/api/hip4/quote'], [('/api/hip4/quote', {'coin': '#301260'}, 5), ('/api/hip4/quote', {'coin': '#301261'}, 5)])

    def test_a_closed_market_reads_no_book(self):
        up = StubUpstream(routes([strikes(n=7, settled=True)], {}))
        out = tools.get_asset({'id': 'hip4:30126:0', 'sections': ['quote']}, up)
        self.assertEqual((out['quote'], out['not_applicable']), (None, ['quote']))
        self.assertNotIn('/api/hip4/quote', [c[0] for c in up.calls])

    def test_the_card_and_the_command_line_show_the_book(self):
        out = self.asset({'#301260': book(0.00126, 0.99611), '#301261': book(0.00389, 0.99874, '#301261')})
        shown = render.asset(out)
        self.assertIn('best bid', shown)
        self.assertIn('Nobody is quoting this market closely', shown)
        with open(os.path.join(HERE, '..', 'data', 'thesis-card.html'), encoding='utf-8') as f:
            card = f.read()
        self.assertIn("'Best bid ' + chance(live.bid) + ' · best ask ' + chance(live.ask)", card)
        self.assertIn("if (q && q.note) card.appendChild(el('div', 'warnline', q.note));", card)
        # only a price read from the book itself (the asset view passes `checked`) is called what the market prices the side at;
        # the figure from the market list is called the middle of the book, as listed
        self.assertIn("oq.appendChild(el('span', 'na', implied(live.price, true)));", card)
        self.assertIn("oquote.appendChild(el('span', 'na', implied(m.price)));", card)
        self.assertIn("return checked ? 'a contract · the market prices this side at about ' + Math.round(v * 100) + '%' : "
                      "'a contract · middle of the order book, as listed';", card)
        self.assertEqual(card.count('implied('), 3)                                # the rule and its two uses: no third place a price is explained


class OutcomeSearch(unittest.TestCase):
    """A. search_markets rows: the listing's mid cannot be checked per row."""

    def setUp(self):
        tools._NAMES = {}

    def tearDown(self):
        tools._NAMES = None

    def test_identical_mids_near_one_half_across_markets_are_null(self):
        real = {'id': 'o5', 'title': 'BTC above $60,000 at Nov 16', 'settled': False, 'underlying': 'BTC',
                'outcomes': [{'outcome': 5, 'label': 'BTC above $60,000 at Nov 16', 'sides': ['Yes', 'No'], 'yes': 0.93, 'no': 0.07}]}
        out = tools.search_markets({'query': 'btc', 'kind': 'outcome', 'limit': 25}, StubUpstream(routes([strikes(), real], {})))
        prices = {m['market_id']: m['price'] for m in out['markets']}
        self.assertEqual({k: v for k, v in prices.items() if k.startswith('hip4:3012')}, {k: None for k in prices if k.startswith('hip4:3012')})
        self.assertEqual((prices['hip4:5:0'], prices['hip4:5:1']), (0.93, 0.07))
        # one market alone near one half is a price; two different markets sharing it are not
        self.assertEqual(shape.unquoted_mids([strikes(mid=0.55, n=1)]), frozenset())
        self.assertEqual(shape.unquoted_mids([strikes(mid=0.55, n=2)]), frozenset({0.55, 0.45}))
        self.assertEqual(shape.unquoted_mids([strikes(mid=0.3, n=5)]), frozenset())     # outside the band: left as listed

    def test_the_note_no_longer_says_read_it_as_the_probability(self):
        self.assertNotIn('probability', shape.OUTCOME_PRICE_NOTE)
        for said in ('middle of this side\'s order book', 'null means it could not be told apart from an empty book', 'get_asset reads the book'):
            self.assertIn(said, shape.OUTCOME_PRICE_NOTE)


class CoreNames(unittest.TestCase):
    """C. a search by a common name finds the core perpetual."""

    def setUp(self):
        tools._NAMES = {}

    def tearDown(self):
        tools._NAMES = None

    def test_bitcoin_finds_btc_first(self):
        hl = {'ts': 1, 'rows': {'BTC': {'px': 67200, 'vol': 1e9}, 'xyz:IBIT': {'px': 38, 'vol': 5e7}, 'BVIV': {'px': 50, 'vol': 9e9},
                                'xyz:BITX': {'px': 40, 'vol': 1e6}}}
        names = {'xyz:IBIT': {'name': 'iShares Bitcoin Trust'}, 'BVIV': {'name': 'Bitcoin Volatility Index'}, 'xyz:BITX': {'name': 'Bitcoin 2x ETF'}}
        tools._NAMES = names
        out = tools.search_markets({'query': 'bitcoin', 'kind': 'perp'}, StubUpstream(dict(standard_routes(), **{'/api/screener/hl': hl})))
        self.assertEqual(out['markets'][0]['market_id'], 'perp:BTC')
        self.assertEqual(out['markets'][0]['name'], 'Bitcoin')

    def test_the_name_is_only_for_a_core_perpetual_the_app_did_not_name(self):
        self.assertEqual(shape.perp_market('ETH', {}, {}, BASE)['name'], 'Ethereum')
        self.assertIsNone(shape.perp_market('xyz:BTC', {}, {}, BASE)['name'])          # a builder venue's BTC is not named for it
        self.assertEqual(shape.perp_market('SOL', {}, {'SOL': {'name': 'Solana (app)'}}, BASE)['name'], 'Solana (app)')
        self.assertIsNone(shape.core_name('XRP'))                                     # its ticker is its name: nothing to add
        # a name match on a core perpetual ranks with a ticker-prefix match, above a ticker-substring one
        self.assertGreaterEqual(shape.match_score('bitco', 'BTC', 'Bitcoin', shape.CORE_NAME_FLOOR), shape.match_score('bitco', 'XBITCO', None))
        self.assertGreater(shape.match_score('bitco', 'BTC', 'Bitcoin', shape.CORE_NAME_FLOOR), shape.match_score('bitco', 'WBITCO', None))


class NoWallet(unittest.TestCase):
    """D. an account with no wallet is a fact, not a failed read."""

    def test_it_is_passed_on_and_said(self):
        empty = dict(PORTFOLIO, no_wallet=True, positions={'positions': [], 'complete': True, 'unread': [], 'wallets_owned': 0, 'wallets_read': 0},
                     outcomes={'positions': None, 'complete': False}, unavailable=[])
        got = shape.portfolio(empty)
        self.assertIs(got['no_wallet'], True)
        self.assertTrue(got['note'].startswith('No wallet is linked to this Hence account, so there are no positions or balances to read. This is not a failed read.'))
        shown = render.portfolio(got)
        self.assertIn('No wallet is linked to this Hence account', shown)
        self.assertNotIn('(they could not be read)', shown)
        self.assertIn('Outcome positions: none (no wallet linked)', shown)
        for flag in (None, 'true', 1, False):
            self.assertIs(shape.portfolio(dict(PORTFOLIO, no_wallet=flag))['no_wallet'], False, flag)      # strictly true, or false
        self.assertNotIn('No wallet', shape.portfolio(PORTFOLIO)['note'])
        self.assertIn('`no_wallet` is true', next(t for t in tools.ACCOUNT_TOOLS if t['name'] == 'get_my_portfolio')['description'])


class Wording(unittest.TestCase):
    """E. a signed-in failure names what was not read or done; I. the list that failed is named."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ, dict(ON, HENCE_MCP_TRADES='1', HENCE_MCP_RESEARCH='1'))
        patcher.start()
        self.addCleanup(patcher.stop)

    def refusal(self, name, arguments):
        class App:
            def connection(self, token):
                return {'grant': GRANT, 'scopes': ['account:read', 'theses:read', 'theses:write', 'theses:research', 'portfolio:read', 'trades:propose'],
                        'client_name': 'x'}

            def get(self, grant, path, query=None):
                raise appauth.Unavailable('Hence answered 503')

            post = lambda self, grant, path, payload: self.get(grant, path)

            def forget(self, token):
                pass
        status, out, _ = protocol.handle_full(*modern('tools/call', {'name': name, 'arguments': arguments}, {'authorization': 'Bearer ' + TOKEN}, name),
                                              StubUpstream({}), auth=App())
        self.assertIs(out['result']['isError'], True, name)
        return out['result']['content'][0]['text']

    def test_each_tool_says_what_it_did_not_read_or_do(self):
        thesis = {'format': 'hence-thesis/1'}
        for name, args, said in (('list_my_theses', {}, 'Your theses were not read.'),
                                 ('get_my_thesis', {'thesis_id': '11111111-1111-4111-8111-111111111111'}, 'The thesis was not read.'),
                                 ('save_thesis_draft', {'thesis': thesis}, 'Hence did not confirm the draft was saved.'),
                                 ('get_thesis_research', {}, 'The research was not read.'),
                                 ('propose_basket', {'thesis_id': '11111111-1111-4111-8111-111111111111', 'amount_usd': 100}, 'Hence did not confirm a proposal.'),
                                 ('get_my_portfolio', {}, 'Your portfolio was not read.'),
                                 ('get_my_account', {}, 'Your account was not read.')):
            text = self.refusal(name, args)
            self.assertIn('Hence answered 503. ' + said, text, name)
            self.assertIn('try again shortly', text)
            if name != 'get_my_account':
                self.assertNotIn('account', text.lower(), name)

    def test_a_sign_in_check_that_did_not_answer_blames_nothing_it_did_not_read(self):
        class Down:
            def connection(self, token):
                raise appauth.Unavailable('Hence could not verify the sign-in (answered 503)')
        status, out, _ = protocol.handle_full(*modern('tools/call', {'name': 'list_my_theses', 'arguments': {}}, {'authorization': 'Bearer ' + TOKEN},
                                                      'list_my_theses'), StubUpstream({}), auth=Down())
        text = out['result']['content'][0]['text']
        self.assertIn('Nothing was read or done; try again shortly.', text)
        self.assertNotIn('Your account', text)

    def test_a_spot_search_names_the_list_that_failed(self):
        down = dict(standard_routes(), **{'/api/screener/spot': Unavailable('Hence answered 503', 503)})
        out = tools.search_markets({'kind': 'spot', 'query': 'tsla'}, StubUpstream(down))
        self.assertIn('Robinhood Chain spot could not be read', out['incomplete'])

    def test_a_spot_id_refused_for_its_case_says_so(self):
        refused = {'ok': False, 'proposed': False, 'problems': [{'field': 'market_id', 'problem': 'Not a market Hence lists.'}]}

        class App:
            def post(self, grant, path, payload):
                return refused
        account = protocol._Account(App(), GRANT)
        upper = tools.propose_trade({'market_id': 'spot:4663:0x' + 'AB' * 20, 'side': 'long', 'size_usd': 50}, None, account)
        self.assertIn('lower-case hex', upper['hint'])
        mint = tools.propose_trade({'market_id': 'spot:solana:xsoe' + 'a' * 36, 'side': 'long', 'size_usd': 50}, None, account)
        self.assertIn('case-sensitive', mint['hint'])
        lower = tools.propose_trade({'market_id': 'spot:4663:0x' + 'ab' * 20, 'side': 'long', 'size_usd': 50}, None, account)
        self.assertNotIn('hint', lower)                                              # nothing about case when case is not the question


class Sections(unittest.TestCase):
    """G. not asked = absent; empty = null and named; F. the price line names its market."""

    def test_a_section_not_asked_for_is_absent_and_an_empty_one_is_null(self):
        got = tools.get_asset({'id': 'perp:xyz:NVDA', 'sections': ['profile']}, StubUpstream({ROUTE: answer(sections_asked=['quote', 'history', 'profile'])}))
        self.assertNotIn('history', got)
        self.assertNotIn('quote', got)
        got = tools.get_asset({'id': 'NVDA'}, StubUpstream({ROUTE: answer(history={})}))
        self.assertIsNone(got['history'])
        self.assertIn('history', got['unavailable'])
        self.assertNotIn('{}', json.dumps({k: v for k, v in got.items() if k in shape.ASSET_SECTIONS}))

    def test_the_price_line_names_the_market_it_was_read_from(self):
        hist = dict(answer()['history'], market_id='perp:xyz:NVDA')
        got = tools.get_asset({'id': 'NVDA'}, StubUpstream({ROUTE: answer(history=hist)}))
        self.assertEqual(got['history']['market_id'], 'perp:xyz:NVDA')
        got = tools.get_asset({'id': 'NVDA'}, StubUpstream({ROUTE: answer(history=dict(hist, market_id=7))}))
        self.assertIsNone(got['history']['market_id'])
        got = tools.get_asset({'id': 'NVDA'}, StubUpstream({ROUTE: answer(history=dict(hist, market_id='x' * 500))}))
        self.assertEqual(len(got['history']['market_id']), 80)
        self.assertIn('`history.market_id`', shape.ASSET_HOW_TO_READ)


class CommandLine(unittest.TestCase):
    """H. a 0 typed is a 0 sent, so the tool's refusal is what the person sees."""

    def arguments(self, *argv):
        return main._arguments(main._parser().parse_args(list(argv)))

    def test_zero_is_sent(self):
        self.assertEqual(self.arguments('markets', '--limit', '0'), ('search_markets', {'limit': 0}))
        self.assertEqual(self.arguments('portfolio', '--days', '0'), ('get_my_portfolio', {'history_days': 0}))
        self.assertEqual(self.arguments('theses', 'search', '--limit', '0')[1], {'limit': 0})
        self.assertEqual(self.arguments('markets'), ('search_markets', {}))                   # left out is still not sent
        with self.assertRaises(tools.ToolError):
            tools.search_markets({'limit': 0}, StubUpstream(standard_routes()))


class Documented(unittest.TestCase):
    """B. the proposal limits are written where an agent and a person read them."""

    def test_the_limits_are_said(self):
        with open(os.path.join(HERE, '..', 'README.md'), encoding='utf-8') as f:
            readme = f.read()
        self.assertIn('30 new proposals per account per day, and 10 waiting at once', readme)
        desc = next(t for t in tools.TRADE_TOOLS if t['name'] == 'propose_trade')['description']
        self.assertIn('30 new proposals per account per day and 10 waiting at once', desc)
        self.assertIn('does not count', desc)


if __name__ == '__main__':
    unittest.main()
