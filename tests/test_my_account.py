"""The connected person's own theses and portfolio (list_my_theses, get_my_thesis,
get_my_portfolio), read through the app's signed surface.

What these pin:
  * the app decides whose data it is: no tool sends an account or a wallet, and this server
    can ask for nothing outside its listed paths;
  * what the app could not read stays unread: a null is passed on as null and named, a short
    page says it is short, and an answer that cannot be read is an error, never an empty list;
  * Hence's own "no" (private, not ready, no history) reaches the agent as Hence's sentence.
"""
import json
import os
import unittest
from unittest import mock

from gateway import appauth, protocol, shape, tools

from .support import T1, StubUpstream, standard_routes
from .test_account import GRANT, ON, SECRET, TOKEN, modern

BASE = '/api/agents/v1'
ALL = ['account:read', 'theses:read', 'portfolio:read']
ROW = {'id': T1, 'title': 'Overbuilt', 'summary': 'Capex outruns demand.', 'topic': 'AI & compute', 'source': 'ai', 'visibility': 'private',
       'assessment': 'active', 'current_version': 2, 'view_version': 2, 'version_at': '2026-11-02T00:00:00', 'author_name': 'Alice',
       'author_handle': 'alice', 'is_owner': True, 'research_pending': False, 'horizon_days': 180, 'evidence_count': 3,
       'relationship': {'saved': True, 'following': False, 'archived': False, 'pinned_version': None},
       'legs': [{'instrument_id': 'perp:xyz:NVDA', 'symbol': 'xyz:NVDA', 'name': 'NVIDIA', 'kind': 'perp', 'venue': 'Hyperliquid',
                 'side': 'short', 'weight': 60, 'role': 'core', 'reason': 'supplier'}]}
DETAIL = dict(ROW, created_at='2026-11-01T00:00:00', body={
    'title': 'Overbuilt', 'summary': 'Capex outruns demand.', 'mechanism': 'm', 'why_now': 'w', 'counter_case': 'c', 'invalidation': 'i',
    'horizon_days': 180, 'cash_weight': 40, 'legs': ROW['legs'], 'paper_legs': [], 'policy': {'mode': 'scheduled', 'check_hours': 24, 'trigger': 'review'},
    'evidence': [{'title': 'Filing', 'url': 'https://sec.gov/x', 'publisher': 'SEC', 'published_at': '2026-10-01'}],
    'milestones': [{'by': '2026-11-30', 'observable': 'NVDA results', 'direction': 'supports'}], 'venue_baskets': {}},
    versions=[{'version': 1, 'change_reason': 'first', 'created_at': '2026-11-01'}, {'version': 2, 'change_reason': 'reweighted', 'created_at': '2026-11-02'}])
PERF = {'thesis_id': T1, 'from': '2026-08-15', 'to': '2026-11-12', 'return_pct': -6.86, 'benchmark': 'BTC', 'benchmark_return_pct': 13.3,
        'max_drawdown_pct': -17.9, 'complete': True, 'excluded': [], 'methodology': 'Fixed weights.',
        'latest': {'at': '2026-11-13T13:00:00', 'return_pct': -7.5, 'live': True, 'delayed': 2}}
POSITION = {'wallet': '0x1111…1111', 'venue': 'trade.xyz', 'coin': 'xyz:NVDA', 'side': 'short', 'size': 3.0, 'entry_price': 100.0,
            'value_usd': 330.0, 'unrealized_pnl_usd': -30.0, 'margin_used_usd': 50.0, 'leverage': 5.0, 'margin_mode': 'cross', 'liquidation_price': None}
PORTFOLIO = {'positions': {'positions': [POSITION], 'complete': False, 'unread': [{'wallet': '0x2222…2222', 'venue': 'Entropy'}],
                           'wallets_owned': 2, 'wallets_read': 2, 'wallets_not_read': 0, 'truncated': False},
             'history': {'has_history': True, 'venues_covered': ['hyperliquid'], 'freshness': {'partial': False},
                         'windows': {'30': {'realized': 12.5, 'trades': 4}}, 'by_market': [], 'markets_not_shown': 0},
             'history_days': 30, 'last_recorded_value': {'usd': 4200.0, 'recorded_at_ms': 1000}, 'unavailable': [],
             'not_included': ['cash and account value now', 'spot token balances'], 'as_of_ms': 5}


class App:
    """Stands in for AppAuth. Answers by path; records what was asked."""

    def __init__(self, answers=None, scopes=ALL):
        self.answers = dict(answers or {})
        self.scopes, self.read = scopes, []

    def connection(self, token):
        return {'grant': GRANT, 'scopes': self.scopes, 'client_name': 'Example Agent'}

    def get(self, grant, path, query=None):
        self.read.append((grant, path, dict(query or {})))
        answer = self.answers[path]
        if isinstance(answer, BaseException):
            raise answer
        return json.loads(json.dumps(answer))

    def forget(self, token):
        pass


class Case(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, ON)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.up = StubUpstream(standard_routes())

    def call(self, name, arguments=None, app=None, signed_in=True):
        headers = {'authorization': 'Bearer ' + TOKEN} if signed_in else None
        status, out, extra = protocol.handle_full(*modern('tools/call', {'name': name, 'arguments': arguments or {}}, headers, name), self.up, auth=app)
        self.status, self.headers = status, extra or {}
        return out.get('result') if isinstance(out, dict) else None

    def value(self, *a, **k):
        result = self.call(*a, **k)
        self.assertEqual((self.status, result['isError']), (200, False), result)
        return result['structuredContent']

    def refusal(self, *a, **k):
        result = self.call(*a, **k)
        self.assertEqual(self.status, 200)
        self.assertIs(result['isError'], True)
        self.assertNotIn('structuredContent', result)             # an error is never also data
        return result['content'][0]['text']


class Permissions(Case):
    def test_each_tool_asks_for_its_own_permission(self):
        app = App(scopes=['account:read'])
        for name, scope, args in (('list_my_theses', 'theses:read', {}), ('get_my_thesis', 'theses:read', {'thesis_id': T1}),
                                  ('get_my_portfolio', 'portfolio:read', {})):
            self.call(name, args, app)
            self.assertEqual(self.status, 403, name)
            self.assertIn('scope="%s"' % scope, self.headers['WWW-Authenticate'])
            self.call(name, args, app, signed_in=False)
            self.assertEqual(self.status, 401, name)
        self.assertEqual(app.read, [])                                # nothing was read on the way to a refusal

    def test_the_sign_in_metadata_lists_every_permission_a_tool_needs(self):
        self.assertEqual(protocol.resource_metadata()['scopes_supported'], ['account:read', 'portfolio:read', 'theses:read', 'theses:write'])

    def test_no_tool_can_name_an_account_or_a_wallet(self):
        for t in tools.ACCOUNT_TOOLS:
            for arg in t['inputSchema']['properties']:
                self.assertNotRegex(arg, r'(?i)wallet|address|user|account|did|owner|author', '%s.%s' % (t['name'], arg))
            self.assertIs(t['inputSchema']['additionalProperties'], False)
        app = App({BASE + '/portfolio': PORTFOLIO})
        self.assertIn('Unknown argument', self.refusal('get_my_portfolio', {'wallet': '0x' + '1' * 40}, app))
        self.assertEqual(app.read, [])


class MyTheses(Case):
    def page(self, **over):
        return dict({'theses': [ROW], 'total': 1, 'snapshot': 'snap1', 'next_offset': None, 'unread': 0, 'as_of': 'now'}, **over)

    def test_the_list_is_read_under_the_connection_and_says_whose_and_who_sees_it(self):
        app = App({BASE + '/theses': self.page()})
        got = self.value('list_my_theses', {}, app)
        self.assertEqual(app.read, [(GRANT, BASE + '/theses', {'filter': 'created', 'limit': 10})])
        card = got['theses'][0]
        self.assertEqual((card['thesis_id'], card['title'], card['yours'], card['visibility'], card['status'], card['saved']),
                         (T1, 'Overbuilt', True, 'private', 'active', True))
        self.assertEqual(card['url'], 'https://app.hence.markets/t/' + T1)
        self.assertEqual((got['which'], got['total'], got['next_cursor']), ('created', 1, None))
        public = self.value('list_my_theses', {}, App({BASE + '/theses': self.page(theses=[dict(ROW, visibility='local')])}))
        self.assertEqual(public['theses'][0]['visibility'], 'public')          # the service's word for it is 'local'

    def test_the_next_page_continues_the_same_list_and_no_other(self):
        app = App({BASE + '/theses': self.page(total=30, next_offset=5)})
        first = self.value('list_my_theses', {'which': 'saved', 'limit': 5}, app)
        self.value('list_my_theses', {'which': 'saved', 'limit': 5, 'cursor': first['next_cursor']}, app)
        self.assertEqual(app.read[-1][2], {'filter': 'saved', 'limit': 5, 'snapshot': 'snap1', 'offset': 5})
        self.assertIn('different list', self.refusal('list_my_theses', {'which': 'created', 'cursor': first['next_cursor']}, app))
        self.assertIn('not valid', self.refusal('list_my_theses', {'cursor': 'garbage'}, app))

    def test_a_short_page_says_it_is_short(self):
        got = self.value('list_my_theses', {}, App({BASE + '/theses': self.page(total=5, unread=2)}))
        self.assertEqual(got['unread_count'], 2)
        self.assertIn('not complete', got['unread_note'])
        self.assertNotIn('unread_count', self.value('list_my_theses', {}, App({BASE + '/theses': self.page()})))

    def test_an_answer_that_cannot_be_read_is_an_error_not_an_empty_list(self):
        for broken in ({'theses': 'none'}, {}, [], 'text'):
            self.assertIn('could not be read', self.refusal('list_my_theses', {}, App({BASE + '/theses': broken})))
        self.assertIn('try again shortly', self.refusal('list_my_theses', {}, App({BASE + '/theses': appauth.Unavailable('Hence answered 503')})))
        self.assertIn('Try again in 9 seconds', self.refusal('list_my_theses', {}, App({BASE + '/theses': appauth.Busy('9')})))


class MyThesis(Case):
    def test_one_thesis_with_its_price_history(self):
        app = App({BASE + '/theses/' + T1: DETAIL, BASE + '/theses/%s/performance' % T1: PERF})
        got = self.value('get_my_thesis', {'thesis_id': T1.upper(), 'period_days': 30}, app)
        self.assertEqual([r[1:] for r in app.read], [(BASE + '/theses/' + T1, {}), (BASE + '/theses/%s/performance' % T1, {'days': 30})])
        self.assertEqual((got['title'], got['invalidation'], got['yours'], got['visibility'], got['cash_weight_pct']), ('Overbuilt', 'i', True, 'private', 40))
        self.assertEqual(got['holdings'][0]['symbol'], 'xyz:NVDA')
        self.assertEqual([v['version'] for v in got['versions']], [1, 2])
        self.assertEqual((got['performance']['return_pct'], got['performance']['latest']['delayed_holdings']), (-6.86, 2))
        self.assertNotIn('performance_unavailable', got)

    def test_no_price_history_is_said_not_left_out(self):
        for failure, said in ((appauth.Refused(422, 'no_history', 'No price history yet.'), 'No price history yet.'),
                              (appauth.Unavailable('Hence answered 503'), 'try again shortly'), (appauth.Busy('5'), 'try again shortly')):
            app = App({BASE + '/theses/' + T1: DETAIL, BASE + '/theses/%s/performance' % T1: failure})
            got = self.value('get_my_thesis', {'thesis_id': T1}, app)
            self.assertIsNone(got['performance'])
            self.assertIn(said, got['performance_unavailable'])
            self.assertIn('Do not state a return', got['performance_unavailable'])
        skipped = self.value('get_my_thesis', {'thesis_id': T1, 'include_performance': False}, App({BASE + '/theses/' + T1: DETAIL}))
        self.assertNotIn('performance', skipped)

    def test_hences_own_no_reaches_the_agent_in_hences_words(self):
        app = App({BASE + '/theses/' + T1: appauth.Refused(404, 'not_found', 'This thesis is private or is no longer available.')})
        self.assertEqual(self.refusal('get_my_thesis', {'thesis_id': T1}, app), 'This thesis is private or is no longer available.')

    def test_a_bad_id_never_becomes_a_path(self):
        app = App()
        for bad in ('nope', T1 + '/../../whoami', '', '../portfolio'):
            self.assertIn('not a thesis id', self.refusal('get_my_thesis', {'thesis_id': bad}, app))
        self.assertEqual(app.read, [])


class MyPortfolio(Case):
    def test_the_answer_is_passed_on_with_what_it_is_not(self):
        app = App({BASE + '/portfolio': PORTFOLIO})
        got = self.value('get_my_portfolio', {'history_days': 90}, app)
        self.assertEqual(app.read, [(GRANT, BASE + '/portfolio', {'days': 90})])
        self.assertEqual(got['open_positions'], [POSITION])
        self.assertEqual((got['positions']['complete'], got['positions']['could_not_read']), (False, [{'wallet': '0x2222…2222', 'venue': 'Entropy'}]))
        self.assertEqual((got['trading_results']['windows']['30']['realized'], got['last_recorded_value']), (12.5, {'usd': 4200.0, 'recorded_at_ms': 1000}))
        self.assertEqual(got['not_included'], PORTFOLIO['not_included'])
        self.assertIn('not the whole portfolio', got['note'])

    def test_unknown_stays_unknown(self):
        dark = dict(PORTFOLIO, positions=dict(PORTFOLIO['positions'], positions=None), history=None, last_recorded_value=None,
                    unavailable=['positions', 'history'])
        got = self.value('get_my_portfolio', {}, App({BASE + '/portfolio': dark}))
        self.assertIsNone(got['open_positions'])                     # not [] : nobody knows what is held
        self.assertIsNone(got['trading_results'])
        self.assertIsNone(got['last_recorded_value'])
        self.assertEqual(got['unavailable'], ['positions', 'history'])
        # nothing in the answer is a sum this server made up
        self.assertFalse([k for k in got if 'total' in k or 'equity' in k or 'net_worth' in k])

    def test_an_answer_that_is_not_a_portfolio_is_an_error(self):
        for broken in ({}, {'positions': {}}, dict(PORTFOLIO, positions='x'), dict(PORTFOLIO, unavailable=None),
                       dict(PORTFOLIO, not_included=None), dict(PORTFOLIO, positions=dict(PORTFOLIO['positions'], positions='many')), []):
            self.assertIn('could not be read', self.refusal('get_my_portfolio', {}, App({BASE + '/portfolio': broken})), repr(broken)[:60])
        self.assertIsNone(shape.portfolio(None))


class SignedReads(unittest.TestCase):
    """appauth.AppAuth.get against a recorded transport."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ, ON)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self, status, body, headers=None):
        self.sent = []

        def send(url, method, hdrs, data, timeout):
            self.sent.append((url, method, hdrs, data))
            return status, json.dumps(body).encode() if body is not None else b'', headers or {}
        return appauth.AppAuth(base='http://app.test', send=send)

    def test_the_query_is_part_of_what_is_signed(self):
        auth = self.make(200, {'theses': []})
        auth.get(GRANT, BASE + '/theses', {'filter': 'created', 'limit': 10})
        url, method, headers, body = self.sent[0]
        self.assertEqual((url, method, body), ('http://app.test/api/agents/v1/theses?filter=created&limit=10', 'GET', None))
        expected = appauth.sign(SECRET, 'GET', '/api/agents/v1/theses?filter=created&limit=10', GRANT, b'',
                                headers['X-Hence-Agent-Time'], headers['X-Hence-Agent-Nonce'])
        self.assertEqual(headers['X-Hence-Agent-Signature'], expected['X-Hence-Agent-Signature'])
        self.assertNotEqual(expected['X-Hence-Agent-Signature'], appauth.sign(
            SECRET, 'GET', '/api/agents/v1/theses', GRANT, b'', headers['X-Hence-Agent-Time'], headers['X-Hence-Agent-Nonce'])['X-Hence-Agent-Signature'])
        self.assertNotIn('Authorization', headers)                   # the person's token is never forwarded

    def test_the_same_signature_the_hence_app_computes_for_a_query(self):
        # pinned in the app's tests/test_agent_reads.py too
        a = appauth.sign('k' * 40, 'GET', '/api/agents/v1/theses?filter=created&limit=10', 'ag_' + 'a' * 20, b'', stamp=1794560000,
                         nonce='00112233445566778899aabbccddeeff')
        self.assertEqual(a['X-Hence-Agent-Signature'], '2c7dbfd22249b6c4032cf41ce9e4cfd9d80e29209f5f64bf5ef924b4aa047138')

    def test_a_path_outside_the_list_is_never_requested(self):
        auth = self.make(200, {})
        for path in ('/api/me', '/api/exchange', BASE + '/introspect', BASE + '/theses/not-an-id', BASE + '/portfolio?wallet=0x1'):
            with self.assertRaises(ValueError):
                auth.get(GRANT, path)
        self.assertEqual(self.sent, [])

    def test_hences_no_is_a_refusal_and_a_silent_failure_is_not(self):
        for status, code in ((404, 'not_found'), (409, 'not_ready'), (422, 'no_history'), (400, 'invalid_request'), (403, 'access_denied')):
            with self.assertRaises(appauth.Refused) as caught:
                self.make(status, {'error': code, 'error_description': 'Hence says no.'}).get(GRANT, BASE + '/theses/' + T1)
            self.assertEqual((caught.exception.status, caught.exception.code, str(caught.exception)), (status, code, 'Hence says no.'))
        # no sentence, or not ours: that is not an answer, it is an outage
        for status, body in ((404, None), (404, {'error': 'not_found'}), (500, {'error': 'x', 'error_description': 'boom'}), (502, None)):
            with self.assertRaises(appauth.Unavailable):
                self.make(status, body).get(GRANT, BASE + '/theses/' + T1)
        with self.assertRaises(appauth.MissingScope):
            self.make(403, {'error': 'insufficient_scope', 'scope': 'theses:read'}).get(GRANT, BASE + '/theses')
        with self.assertRaises(appauth.NotConnected):
            self.make(401, {'error': 'invalid_token'}).get(GRANT, BASE + '/theses')


if __name__ == '__main__':
    unittest.main()
